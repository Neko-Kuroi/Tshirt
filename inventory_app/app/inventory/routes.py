from collections import OrderedDict

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from .. import db
from ..models import (KINDS, Category, Design, Item, ItemNote, PrintedProduct,
                      StockMovement, Variant)
from ..services import stock
from ..services.stock import StockError
from ..utils import safe_next, size_key, to_int
from . import bp


def _movement_loads():
    """履歴の表示(target_label / user)に使う関連をまとめて読み、行ごとの追加クエリを防ぐ。"""
    return (
        joinedload(StockMovement.user),
        joinedload(StockMovement.variant).joinedload(Variant.item),
        joinedload(StockMovement.printed_product).options(
            joinedload(PrintedProduct.design),
            joinedload(PrintedProduct.variant).joinedload(Variant.item)),
    )


@bp.before_request
@login_required
def _require_login():
    pass


# ---- 備考の取得(画面ごとに、行数が増えてもSQL発行数が増えないようにまとめて読む) --------

def _notes(include_resolved=False, item_id=None, category_id=None, limit=None):
    query = (ItemNote.query.join(ItemNote.item)
             .options(contains_eager(ItemNote.item).joinedload(Item.category),
                      joinedload(ItemNote.user)))
    if not include_resolved:
        query = query.filter(ItemNote.is_resolved.is_(False))
    if item_id:
        query = query.filter(ItemNote.item_id == item_id)
    if category_id:
        query = query.filter(Item.category_id == category_id)
    query = query.order_by(ItemNote.is_resolved, ItemNote.created_at.desc(), ItemNote.id.desc())
    return (query.limit(limit) if limit else query).all()


def _open_note_count():
    return ItemNote.query.filter(ItemNote.is_resolved.is_(False)).count()


# ---- ダッシュボード --------------------------------------------------

@bp.route("/")
def dashboard():
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    blank = dict(db.session.query(Item.category_id, func.coalesce(func.sum(Variant.quantity), 0))
                 .join(Variant, Variant.item_id == Item.id).group_by(Item.category_id).all())
    printed = dict(db.session.query(Item.category_id, func.coalesce(func.sum(PrintedProduct.quantity), 0))
                   .join(Variant, Variant.item_id == Item.id)
                   .join(PrintedProduct, PrintedProduct.variant_id == Variant.id)
                   .group_by(Item.category_id).all())
    recent = (StockMovement.query.options(*_movement_loads())
              .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
              .limit(8).all())
    return render_template("inventory/dashboard.html", cats=cats, blank=blank, printed=printed,
                           recent=recent, notes=_notes(limit=5), note_count=_open_note_count())


# ---- 無地在庫(色×サイズのマトリクス) ---------------------------------

@bp.route("/blank")
def blank_list():
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    cat_id = to_int(request.args.get("category"))
    cat = next((c for c in cats if c.id == cat_id), cats[0] if cats else None)
    q = request.args.get("q", "").strip()
    items = []
    if cat:
        query = (Item.query.filter_by(category_id=cat.id, is_active=True)
                 .options(selectinload(Item.variants)))
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Item.brand.like(like), Item.item_no.like(like),
                                        Item.name.like(like)))
        notes_by_item = {}
        for n in _notes(category_id=cat.id):
            notes_by_item.setdefault(n.item_id, []).append(n)
        for item in query.order_by(Item.brand, Item.item_no).all():
            items.append(build_matrix(item, cat, notes_by_item.get(item.id, [])))
    return render_template("inventory/blank_list.html", cats=cats, cat=cat, items=items, q=q)


def note_targets(variants):
    """備考の対象に選べる値(その品番にある色・種類名)。"""
    return sorted({t for v in variants for t in (v.color, v.variant_name) if t})


def build_matrix(item, cat, notes=()):
    """行=色/種類名、列=サイズ(サイズ無しカテゴリーは1列)。備考のある行には印を付ける。"""
    noted = {n.target for n in notes if n.target}
    sizes = sorted({v.size for v in item.variants}, key=size_key) if cat.uses_size else [""]
    rows = OrderedDict()
    for v in sorted(item.variants, key=lambda v: (v.variant_name, v.color)):
        key = (v.variant_name, v.color)
        row = rows.setdefault(key, {
            "label": " / ".join(p for p in key if p) or "-", "cells": {}, "total": 0,
            "has_note": bool(noted & {k for k in key if k})})
        row["cells"][v.size if cat.uses_size else ""] = v
        row["total"] += v.quantity
    return {"item": item, "sizes": sizes, "rows": list(rows.values()), "notes": list(notes),
            "targets": note_targets(item.variants), "total": sum(r["total"] for r in rows.values())}


# ---- プリント済み在庫 ------------------------------------------------

@bp.route("/printed")
def printed_list():
    q = request.args.get("q", "").strip()
    query = (PrintedProduct.query
             .join(PrintedProduct.design).join(PrintedProduct.variant)
             .join(Variant.item).join(Item.category)
             .options(contains_eager(PrintedProduct.design),
                      contains_eager(PrintedProduct.variant)
                      .contains_eager(Variant.item).contains_eager(Item.category)))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Design.name.like(like), Item.brand.like(like),
                                    Item.item_no.like(like), Item.name.like(like),
                                    Variant.color.like(like)))
    groups = OrderedDict()
    for p in query.order_by(Design.name, Item.brand, Item.item_no).all():
        groups.setdefault(p.design.name, []).append(p)
    return render_template("inventory/printed_list.html", groups=groups, q=q)


# ---- 入荷 ------------------------------------------------------------

def _items_for_select():
    return (Item.query.join(Item.category).options(contains_eager(Item.category))
            .filter(Item.is_active.is_(True))
            .order_by(Category.sort_order, Item.brand, Item.item_no).all())


@bp.route("/receive", methods=["GET", "POST"])
def receive():
    selected = to_int(request.values.get("item_id"))
    if request.method == "POST":
        item = db.session.get(Item, selected) if selected else None
        try:
            if not item:
                raise StockError("品番を選んでください。")
            v = stock.get_or_create_variant(
                item, request.form.get("color"), request.form.get("size"),
                request.form.get("variant_name"))
            qty = to_int(request.form.get("qty"))
            stock.receive(current_user, v, qty, request.form.get("note"))
            db.session.commit()
            flash(f"入荷を登録しました: {v.label} +{qty}", "success")
            return redirect(url_for("inventory.receive", item_id=item.id))
        except StockError as e:
            db.session.rollback()
            flash(str(e), "danger")
    colors = sorted({c for (c,) in db.session.query(Variant.color).filter(Variant.color != "")})
    sel_item = db.session.get(Item, selected) if selected else None
    return render_template(
        "inventory/receive.html", items=_items_for_select(), selected=selected, colors=colors,
        form=request.form, item_notes=_notes(item_id=selected) if sel_item else [],
        targets=note_targets(sel_item.variants) if sel_item else [])


# ---- プリント変換 ----------------------------------------------------

@bp.route("/convert", methods=["GET", "POST"])
def convert():
    if request.method == "POST":
        try:
            v = db.session.get(Variant, to_int(request.form.get("variant_id"), 0))
            if not v:
                raise StockError("無地在庫を選んでください。")
            used = to_int(request.form.get("used"))
            output = to_int(request.form.get("output"))
            design_name = request.form.get("design_new", "").strip() or request.form.get("design", "")
            stock.convert_to_printed(current_user, v, design_name, used, output,
                                     request.form.get("note"))
            label = v.label
            db.session.commit()
            flash(f"プリント変換しました: {design_name} / {label} "
                  f"(使用 {used} → 完成 {output})", "success")
            return redirect(url_for("inventory.convert"))
        except StockError as e:
            db.session.rollback()
            flash(str(e), "danger")
    variants = (Variant.query.join(Variant.item).join(Item.category)
                .options(contains_eager(Variant.item).contains_eager(Item.category))
                .filter(Category.is_printable.is_(True), Variant.quantity > 0, Item.is_active.is_(True))
                .order_by(Category.sort_order, Item.brand, Item.item_no, Variant.color, Variant.size)
                .all())
    designs = Design.query.order_by(Design.name).all()
    return render_template("inventory/convert.html", variants=variants, designs=designs,
                           form=request.form)


# ---- 出荷・棚卸し調整 ------------------------------------------------

def _resolve_target(raw):
    kind, _, rid = (raw or "").partition(":")
    rid = to_int(rid, 0)
    if kind == "v":
        return db.session.get(Variant, rid)
    if kind == "p":
        return db.session.get(PrintedProduct, rid)
    return None


@bp.route("/move", methods=["GET", "POST"])
def move():
    if request.method == "POST":
        try:
            target = _resolve_target(request.form.get("target"))
            if not target:
                raise StockError("対象の在庫を選んでください。")
            kind = request.form.get("kind")
            qty = to_int(request.form.get("qty"))
            note = request.form.get("note")
            if kind == "ship":
                stock.ship(current_user, target, qty, note)
            elif kind == "adjust":
                stock.adjust(current_user, target, qty, note)
            else:
                raise StockError("操作の種類を選んでください。")
            label = target.label
            db.session.commit()
            shown = f"{qty:+d}" if kind == "adjust" else f"-{qty}"
            flash(f"{KINDS[kind]}を登録しました: {label} ({shown})", "success")
            return redirect(url_for("inventory.move"))
        except StockError as e:
            db.session.rollback()
            flash(str(e), "danger")
    variants = (Variant.query.join(Variant.item).join(Item.category)
                .options(contains_eager(Variant.item).contains_eager(Item.category))
                .order_by(Category.sort_order, Item.brand, Item.item_no, Variant.color, Variant.size)
                .all())
    products = (PrintedProduct.query
                .join(PrintedProduct.design).join(PrintedProduct.variant)
                .join(Variant.item).join(Item.category)
                .options(contains_eager(PrintedProduct.design),
                         contains_eager(PrintedProduct.variant)
                         .contains_eager(Variant.item).contains_eager(Item.category))
                .order_by(Design.name, Item.brand, Item.item_no).all())
    return render_template("inventory/move.html", variants=variants, products=products,
                           form=request.form)


# ---- 履歴 ------------------------------------------------------------

@bp.route("/history")
def history():
    page = min(max(to_int(request.args.get("page"), 1), 1), 100_000)  # OFFSET の計算が溢れないように
    kind = request.args.get("kind", "")
    query = StockMovement.query.options(*_movement_loads())
    if kind in KINDS:
        query = query.filter_by(kind=kind)
    pagination = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).paginate(
        page=page, per_page=current_app.config["HISTORY_PER_PAGE"], error_out=False)
    return render_template("inventory/history.html", pagination=pagination, kind=kind, kinds=KINDS)


# ---- 備考 ------------------------------------------------------------

@bp.route("/notes")
def notes():
    show_all = request.args.get("all") == "1"
    return render_template("inventory/notes.html", notes=_notes(include_resolved=show_all),
                           show_all=show_all)


@bp.post("/notes")
def note_add():
    back = safe_next(request.form.get("back")) or url_for("inventory.notes")
    item_id = to_int(request.form.get("item_id"), 0)
    body = request.form.get("body", "").strip()
    target = request.form.get("target", "").strip()
    if not db.session.get(Item, item_id):
        flash("品番が見つかりません。", "danger")
    elif not body:
        flash("備考の内容を入力してください。", "danger")
    elif len(body) > 1000 or len(target) > 64:
        flash("備考は1000文字まで、対象は64文字までです。", "danger")
    else:
        db.session.add(ItemNote(item_id=item_id, target=target, body=body, user_id=current_user.id))
        db.session.commit()
        flash("備考を追加しました。", "success")
    return redirect(back)


@bp.post("/notes/<int:note_id>/resolve")
def note_resolve(note_id):
    note = db.get_or_404(ItemNote, note_id)
    note.is_resolved = not note.is_resolved  # 対応済み ⇔ 未対応 を切り替える
    db.session.commit()
    return redirect(safe_next(request.form.get("back")) or url_for("inventory.notes"))


@bp.post("/notes/<int:note_id>/delete")
def note_delete(note_id):
    if not current_user.is_admin:
        abort(403)
    db.session.delete(db.get_or_404(ItemNote, note_id))
    db.session.commit()
    flash("備考を削除しました。", "success")
    return redirect(safe_next(request.form.get("back")) or url_for("inventory.notes"))
