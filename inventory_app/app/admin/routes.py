from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager

from .. import db
from ..models import Category, Item, User, Variant
from ..utils import is_unique_violation, to_int
from . import admin_required, bp


def _flag(name):
    return request.form.get(name) == "on"


def _not_found(message):
    return render_template("error.html", code=404, message=message), 404


def _commit_or_flash(duplicate_message):
    """commit して True を返す。UNIQUE違反なら理由を表示して False。それ以外の制約違反は握りつぶさない。"""
    try:
        db.session.commit()
        return True
    except IntegrityError as e:
        db.session.rollback()
        if not is_unique_violation(e):
            raise
        flash(duplicate_message, "danger")
        return False


# ---- カテゴリー ------------------------------------------------------

@bp.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("カテゴリー名を入力してください。", "danger")
        else:
            db.session.add(Category(
                name=name, sort_order=Category.query.count(), uses_color=_flag("uses_color"),
                uses_size=_flag("uses_size"), uses_variant_name=_flag("uses_variant_name"),
                is_printable=_flag("is_printable")))
            if _commit_or_flash("同名のカテゴリーが既にあります。"):
                flash(f"カテゴリーを追加しました: {name}", "success")
                return redirect(url_for("admin.categories"))
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    return render_template("admin/categories.html", cats=cats)


# 入力欄の設定 → その設定で使う variant の列
_FLAG_COLUMN = {"uses_color": (Variant.color, "色"), "uses_size": (Variant.size, "サイズ"),
                "uses_variant_name": (Variant.variant_name, "種類名")}


def _flags_in_use(cat):
    """OFFにしようとしているのに、既に値が入っている設定の名前を返す。"""
    blocked = []
    for flag, (column, label) in _FLAG_COLUMN.items():
        if getattr(cat, flag) and not _flag(flag):
            used = (db.session.query(Variant.id).join(Variant.item)
                    .filter(Item.category_id == cat.id, column != "").first())
            if used:
                blocked.append(label)
    return blocked


@bp.route("/categories/<int:cid>", methods=["GET", "POST"])
@admin_required
def category_edit(cid):
    cat = db.get_or_404(Category, cid)
    if request.method == "POST":
        raw_order = request.form.get("sort_order", "").strip()
        sort_order = to_int(raw_order) if raw_order else 0  # 空欄は0
        blocked = _flags_in_use(cat)
        if sort_order is None:
            flash("表示順は整数で入力してください。", "danger")
        elif blocked:
            flash("「" + "」「".join(blocked) + "」は、既に在庫登録があるためOFFにできません。"
                  "(OFFにすると、同じ行・列の在庫が1つに見えてしまいます)", "danger")
        else:
            cat.name = request.form.get("name", "").strip() or cat.name
            cat.sort_order = sort_order
            # 在庫が既にあるカテゴリーでも、入力欄の出し分け設定は変更できる(既存データは消えない)
            cat.uses_color = _flag("uses_color")
            cat.uses_size = _flag("uses_size")
            cat.uses_variant_name = _flag("uses_variant_name")
            cat.is_printable = _flag("is_printable")
            if _commit_or_flash("同名のカテゴリーが既にあります。"):
                flash("カテゴリーを更新しました。", "success")
                return redirect(url_for("admin.categories"))
    return render_template("admin/category_edit.html", cat=cat)


# ---- 品番(Item) ------------------------------------------------------

def _item_fields():
    return (request.form.get("brand", "").strip(), request.form.get("item_no", "").strip(),
            request.form.get("name", "").strip())


@bp.route("/items", methods=["GET", "POST"])
@admin_required
def items():
    if request.method == "POST":
        cat = db.session.get(Category, to_int(request.form.get("category_id"), 0))
        brand, item_no, name = _item_fields()
        if not cat:
            flash("カテゴリーを選んでください。", "danger")
        elif not (brand or item_no):
            flash("ブランドか品番のどちらかは入力してください。", "danger")
        else:
            db.session.add(Item(category_id=cat.id, brand=brand, item_no=item_no, name=name))
            if _commit_or_flash("同じカテゴリーに同じブランド・品番が既にあります。"):
                flash("品番を追加しました。", "success")
                return redirect(url_for("admin.items", category=cat.id))
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    cat_id = to_int(request.args.get("category"))
    query = Item.query.join(Item.category).options(contains_eager(Item.category))
    if cat_id:
        query = query.filter(Item.category_id == cat_id)
    all_items = query.order_by(Category.sort_order, Item.brand, Item.item_no).all()
    return render_template("admin/items.html", cats=cats, items=all_items, cat_id=cat_id)


@bp.route("/items/<int:iid>", methods=["GET", "POST"])
@admin_required
def item_edit(iid):
    item = db.get_or_404(Item, iid)
    if request.method == "POST":
        brand, item_no, name = _item_fields()
        if not (brand or item_no):
            flash("ブランドか品番のどちらかは入力してください。", "danger")
        else:
            item.brand, item.item_no, item.name = brand, item_no, name
            item.is_active = _flag("is_active")
            if _commit_or_flash("同じカテゴリーに同じブランド・品番が既にあります。"):
                flash("品番を更新しました。", "success")
                return redirect(url_for("admin.items", category=item.category_id))
    return render_template("admin/item_edit.html", item=item)


# ---- ユーザー --------------------------------------------------------

@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < 8:
            flash("ユーザー名と、8文字以上のパスワードを入力してください。", "danger")
        else:
            u = User(username=username, role="admin" if request.form.get("role") == "admin" else "staff")
            u.set_password(password)
            db.session.add(u)
            if _commit_or_flash("そのユーザー名は既に使われています。"):
                flash(f"ユーザーを追加しました: {username}", "success")
                return redirect(url_for("admin.users"))
    return render_template("admin/users.html", users=User.query.order_by(User.id).all())


@bp.post("/users/<int:uid>/toggle")
@admin_required
def user_toggle(uid):
    u = db.get_or_404(User, uid)
    if u.id == current_user.id:
        flash("自分自身は無効化できません。", "danger")
    else:
        u.is_active_flag = not u.is_active_flag
        db.session.commit()
        flash(f"{u.username} を{'有効' if u.is_active_flag else '無効'}にしました。", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:uid>/password")
@admin_required
def user_password(uid):
    u = db.get_or_404(User, uid)
    pw = request.form.get("password", "")
    if len(pw) < 8:
        flash("パスワードは8文字以上にしてください。", "danger")
    else:
        u.set_password(pw)
        db.session.commit()
        if u.id == current_user.id:
            login_user(u)  # 自分自身を再設定した場合は入り直し
        flash(f"{u.username} のパスワードを再設定しました。(その人は再ログインが必要になります)", "success")
    return redirect(url_for("admin.users"))
