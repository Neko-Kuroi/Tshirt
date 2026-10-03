from collections import OrderedDict

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from .. import db
from ..models import (KINDS, Category, Design, Item, PrintedProduct, StockMovement,
                      Variant)
from ..services import stock
from ..services.stock import StockError
from . import bp

SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "2XL", "XXL", "3XL", "XXXL", "4XL", "5XL",
              "FREE", "F"]


def size_key(s):
    u = s.upper()
    if u in SIZE_ORDER:
        return (0, SIZE_ORDER.index(u), s)
    if s.isdigit():
        return (1, int(s), s)
    return (2, 0, s)


def to_int(raw, default=None):
    try:
        return int((raw or "").strip())
    except ValueError:
        return default


@bp.before_request
@login_required
def _require_login():
    pass


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
    recent = (StockMovement.query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
              .limit(8).all())
    return render_template("inventory/dashboard.html", cats=cats, blank=blank,
                           printed=printed, recent=recent)


# ---- 無地在庫(色×サイズのマトリクス) ---------------------------------

@bp.route("/blank")
def blank_list():
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    cat_id = to_int(request.args.get("category"))
    cat = next((c for c in cats if c.id == cat_id), cats[0] if cats else None)
    q = request.args.get("q", "").strip()
    items = []
    if cat:
        query = Item.query.filter_by(category_id=cat.id, is_active=True)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Item.brand.like(like), Item.item_no.like(like),
                                        Item.name.like(like)))
        for item in query.order_by(Item.brand, Item.item_no).all():
            items.append(build_matrix(item, cat))
    return render_template("inventory/blank_list.html", cats=cats, cat=cat, items=items, q=q)


def build_matrix(item, cat):
    """行=色/種類名、列=サイズ(サイズ無しカテゴリーは1列)。"""
    sizes = sorted({v.size for v in item.variants}, key=size_key) if cat.uses_size else [""]
    rows = OrderedDict()
    for v in sorted(item.variants, key=lambda v: (v.variant_name, v.color)):
        key = (v.variant_name, v.color)
        row = rows.setdefault(key, {
            "label": " / ".join(p for p in key if p) or "-", "cells": {}, "total": 0})
        row["cells"][v.size if cat.uses_size else ""] = v
        row["total"] += v.quantity
    return {"item": item, "sizes": sizes, "rows": list(rows.values()),
            "total": sum(r["total"] for r in rows.values())}


# ---- プリント済み在庫 ------------------------------------------------

@bp.route("/printed")
def printed_list():
    q = request.args.get("q", "").strip()
    query = (PrintedProduct.query.join(Design).join(Variant).join(Item))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Design.name.like(like), Item.brand.like(like),
                                    Item.item_no.like(like), Item.name.like(like),
                                    Variant.color.like(like)))
    products = query.order_by(Design.name, Item.brand, Item.item_no).all()
    groups = OrderedDict()
    for p in products:
        groups.setdefault(p.design, []).append(p)
    return render_template("inventory/printed_list.html", groups=groups, q=q)


# ---- 入荷 ------------------------------------------------------------

def _items_for_select():
    return (Item.query.join(Category).filter(Item.is_active.is_(True))
            .order_by(Category.sort_order, Item.brand, Item.item_no).all())


@bp.route("/receive", methods=["GET", "POST"])
def receive():
    items = _items_for_select()
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
    # 入力補完用に既存の色・サイズを渡す
    colors = sorted({c for (c,) in db.session.query(Variant.color).filter(Variant.color != "")})
    return render_template("inventory/receive.html", items=items, selected=selected,
                           colors=colors, form=request.form)


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
            db.session.commit()
            flash(f"プリント変換しました: {design_name} / {v.label} "
                  f"(使用 {used} → 完成 {output})", "success")
            return redirect(url_for("inventory.convert"))
        except StockError as e:
            db.session.rollback()
            flash(str(e), "danger")
    variants = (Variant.query.join(Item).join(Category)
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
            db.session.commit()
            flash(f"{KINDS[kind]}を登録しました: {target.label} ({qty:+d})" if kind == "adjust"
                  else f"{KINDS[kind]}を登録しました: {target.label} (-{qty})", "success")
            return redirect(url_for("inventory.move"))
        except StockError as e:
            db.session.rollback()
            flash(str(e), "danger")
    variants = (Variant.query.join(Item).join(Category)
                .order_by(Category.sort_order, Item.brand, Item.item_no, Variant.color, Variant.size)
                .all())
    products = (PrintedProduct.query.join(Design).join(Variant).join(Item)
                .order_by(Design.name, Item.brand, Item.item_no).all())
    return render_template("inventory/move.html", variants=variants, products=products,
                           form=request.form)


# ---- 履歴 ------------------------------------------------------------

@bp.route("/history")
def history():
    page = max(to_int(request.args.get("page"), 1), 1)
    kind = request.args.get("kind", "")
    query = StockMovement.query
    if kind in KINDS:
        query = query.filter_by(kind=kind)
    pagination = query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).paginate(
        page=page, per_page=current_app.config["HISTORY_PER_PAGE"], error_out=False)
    return render_template("inventory/history.html", pagination=pagination, kind=kind, kinds=KINDS)
