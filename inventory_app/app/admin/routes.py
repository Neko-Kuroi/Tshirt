from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from .. import db
from ..models import Category, Item, User
from . import admin_required, bp


def _flag(name):
    return request.form.get(name) == "on"


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
            try:
                db.session.commit()
                flash(f"カテゴリーを追加しました: {name}", "success")
                return redirect(url_for("admin.categories"))
            except IntegrityError:
                db.session.rollback()
                flash("同名のカテゴリーが既にあります。", "danger")
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    return render_template("admin/categories.html", cats=cats)


@bp.route("/categories/<int:cid>", methods=["GET", "POST"])
@admin_required
def category_edit(cid):
    cat = db.get_or_404(Category, cid)
    if request.method == "POST":
        cat.name = request.form.get("name", "").strip() or cat.name
        cat.sort_order = int(request.form.get("sort_order") or 0)
        # 在庫が既にあるカテゴリーでも、入力欄の出し分け設定は変更できる(既存データは消えない)
        cat.uses_color = _flag("uses_color")
        cat.uses_size = _flag("uses_size")
        cat.uses_variant_name = _flag("uses_variant_name")
        cat.is_printable = _flag("is_printable")
        try:
            db.session.commit()
            flash("カテゴリーを更新しました。", "success")
            return redirect(url_for("admin.categories"))
        except IntegrityError:
            db.session.rollback()
            flash("同名のカテゴリーが既にあります。", "danger")
    return render_template("admin/category_edit.html", cat=cat)


# ---- 品番(Item) ------------------------------------------------------

@bp.route("/items", methods=["GET", "POST"])
@admin_required
def items():
    if request.method == "POST":
        cat = db.session.get(Category, int(request.form.get("category_id") or 0))
        brand = request.form.get("brand", "").strip()
        item_no = request.form.get("item_no", "").strip()
        name = request.form.get("name", "").strip()
        if not cat:
            flash("カテゴリーを選んでください。", "danger")
        elif not (brand or item_no or name):
            flash("ブランド・品番・商品名のいずれかを入力してください。", "danger")
        else:
            db.session.add(Item(category_id=cat.id, brand=brand, item_no=item_no, name=name,
                                note=request.form.get("note", "").strip()))
            try:
                db.session.commit()
                flash("品番を追加しました。", "success")
                return redirect(url_for("admin.items", category=cat.id))
            except IntegrityError:
                db.session.rollback()
                flash("同じカテゴリーに同じブランド・品番が既にあります。", "danger")
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    cat_id = request.args.get("category", type=int)
    query = Item.query.join(Category)
    if cat_id:
        query = query.filter(Item.category_id == cat_id)
    all_items = query.order_by(Category.sort_order, Item.brand, Item.item_no).all()
    return render_template("admin/items.html", cats=cats, items=all_items, cat_id=cat_id)


@bp.route("/items/<int:iid>", methods=["GET", "POST"])
@admin_required
def item_edit(iid):
    item = db.get_or_404(Item, iid)
    if request.method == "POST":
        item.brand = request.form.get("brand", "").strip()
        item.item_no = request.form.get("item_no", "").strip()
        item.name = request.form.get("name", "").strip()
        item.note = request.form.get("note", "").strip()
        item.is_active = _flag("is_active")
        try:
            db.session.commit()
            flash("品番を更新しました。", "success")
            return redirect(url_for("admin.items", category=item.category_id))
        except IntegrityError:
            db.session.rollback()
            flash("同じカテゴリーに同じブランド・品番が既にあります。", "danger")
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
            try:
                db.session.commit()
                flash(f"ユーザーを追加しました: {username}", "success")
                return redirect(url_for("admin.users"))
            except IntegrityError:
                db.session.rollback()
                flash("そのユーザー名は既に使われています。", "danger")
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
        flash(f"{u.username} のパスワードを再設定しました。", "success")
    return redirect(url_for("admin.users"))
