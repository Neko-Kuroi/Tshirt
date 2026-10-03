from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .. import db
from ..models import User

bp = Blueprint("auth", __name__)


def _safe_next(target):
    """同一サイト内の相対パスだけ許可(オープンリダイレクト対策)。"""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inventory.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(request.form.get("password", "")):
            login_user(user, remember=request.form.get("remember") == "on")
            return redirect(_safe_next(request.args.get("next")) or url_for("inventory.dashboard"))
        flash("ユーザー名またはパスワードが違います。", "danger")
    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old = request.form.get("old", "")
        new = request.form.get("new", "")
        if not current_user.check_password(old):
            flash("現在のパスワードが違います。", "danger")
        elif len(new) < 8:
            flash("新しいパスワードは8文字以上にしてください。", "danger")
        elif new != request.form.get("confirm", ""):
            flash("確認用パスワードが一致しません。", "danger")
        else:
            current_user.set_password(new)
            db.session.commit()
            flash("パスワードを変更しました。", "success")
            return redirect(url_for("inventory.dashboard"))
    return render_template("auth/password.html")
