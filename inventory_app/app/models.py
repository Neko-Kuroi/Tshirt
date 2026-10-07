import hmac
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager
from .utils import to_int


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="staff")  # admin / staff
    is_active_flag = db.Column("is_active", db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def is_active(self):  # Flask-Login: 無効ユーザーはログイン不可
        return self.is_active_flag

    @property
    def is_admin(self):
        return self.role == "admin"

    def session_token(self) -> str:
        """パスワードを変えると変わる値(ハッシュそのものは Cookie に出さず、HMAC にする)。"""
        key = current_app.config["SECRET_KEY"]
        key = key.encode() if isinstance(key, str) else key
        return hmac.new(key, self.password_hash.encode(), "sha256").hexdigest()[:20]

    def get_id(self):  # Flask-Login がセッション/remember Cookie に保存する値
        return f"{self.id}:{self.session_token()}"


@login_manager.user_loader
def load_session_user(session_id):
    """'ユーザーID:トークン' が一致しなければ(パスワード変更後など)None。"""
    uid, _, token = (session_id or "").partition(":")
    uid = to_int(uid)  # 範囲外・数字以外は None(改ざんされたCookieでも落ちない)
    user = db.session.get(User, uid) if uid is not None else None
    if user and hmac.compare_digest(token, user.session_token()):
        return user
    return None


class Category(db.Model):
    __tablename__ = "category"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    uses_color = db.Column(db.Boolean, nullable=False, default=True)
    uses_size = db.Column(db.Boolean, nullable=False, default=False)
    uses_variant_name = db.Column(db.Boolean, nullable=False, default=False)
    is_printable = db.Column(db.Boolean, nullable=False, default=True)

    items = db.relationship("Item", back_populates="category")


class Item(db.Model):
    """ブランド・品番単位のマスタ。(画面側は、sqlite3 版の『行』と同じ名前の属性で読む)"""
    __tablename__ = "item"
    __table_args__ = (UniqueConstraint("category_id", "brand", "item_no"),)
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    brand = db.Column(db.String(64), nullable=False, default="")
    item_no = db.Column(db.String(64), nullable=False, default="")
    name = db.Column(db.String(128), nullable=False, default="")
    # 旧『メモ』欄。画面では使わない(備考 = item_note に引き継ぎ済み)。列は互換のため残す
    note = db.Column(db.Text, nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    category = db.relationship("Category", back_populates="items")
    variants = db.relationship("Variant", back_populates="item")

    @property
    def item_name(self):
        return self.name

    @property
    def label(self):
        head = " ".join(p for p in (self.brand, self.item_no) if p)
        return f"{head} {self.name}".strip() or f"Item#{self.id}"

    @property
    def item_label(self):
        return self.label

    @property
    def category_name(self):
        return self.category.name

    uses_color = property(lambda self: self.category.uses_color)
    uses_size = property(lambda self: self.category.uses_size)
    uses_variant_name = property(lambda self: self.category.uses_variant_name)
    is_printable = property(lambda self: self.category.is_printable)


class Variant(db.Model):
    """在庫の最小単位(品番 × 色 × サイズ × 種類名)。未使用の列は NULL ではなく空文字。"""
    __tablename__ = "variant"
    __table_args__ = (
        UniqueConstraint("item_id", "color", "size", "variant_name"),
        CheckConstraint("quantity >= 0", name="qty_nonneg"),
    )
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    color = db.Column(db.String(64), nullable=False, default="")
    size = db.Column(db.String(32), nullable=False, default="")
    variant_name = db.Column(db.String(64), nullable=False, default="")
    quantity = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    item = db.relationship("Item", back_populates="variants")

    @property
    def spec(self):
        """色 / サイズ / 種類名 の表示用文字列。"""
        return " / ".join(p for p in (self.variant_name, self.color, self.size) if p) or "-"

    @property
    def item_label(self):
        return self.item.label

    @property
    def label(self):
        return f"{self.item.label} | {self.spec}"

    category_name = property(lambda self: self.item.category.name)
    is_printable = property(lambda self: self.item.category.is_printable)
    item_active = property(lambda self: self.item.is_active)


class Design(db.Model):
    __tablename__ = "design"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    note = db.Column(db.Text, nullable=False, default="")


class PrintedProduct(db.Model):
    __tablename__ = "printed_product"
    __table_args__ = (
        UniqueConstraint("design_id", "variant_id"),
        CheckConstraint("quantity >= 0", name="qty_nonneg"),
    )
    id = db.Column(db.Integer, primary_key=True)
    design_id = db.Column(db.Integer, db.ForeignKey("design.id"), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("variant.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    design = db.relationship("Design")
    variant = db.relationship("Variant")

    @property
    def label(self):
        return f"{self.design.name} | {self.variant.label}"

    design_name = property(lambda self: self.design.name)
    item_label = property(lambda self: self.variant.item.label)
    spec = property(lambda self: self.variant.spec)
    category_name = property(lambda self: self.variant.item.category.name)


KINDS = {
    "receive": "入荷",
    "print_use": "プリント使用",
    "print_output": "プリント完成",
    "ship": "出荷",
    "adjust": "棚卸し調整",
}


class StockMovement(db.Model):
    """在庫の増減はすべてここに残す。variant か printed_product のどちらか一方だけが入る。"""
    __tablename__ = "stock_movement"
    __table_args__ = (
        CheckConstraint(
            "(variant_id IS NULL) <> (printed_product_id IS NULL)", name="one_target"),
        CheckConstraint("delta <> 0", name="delta_nonzero"),
    )
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(36), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("variant.id"))
    printed_product_id = db.Column(db.Integer, db.ForeignKey("printed_product.id"))
    delta = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text, nullable=False, default="")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    variant = db.relationship("Variant")
    printed_product = db.relationship("PrintedProduct")
    user = db.relationship("User")

    @property
    def kind_label(self):
        return KINDS.get(self.kind, self.kind)

    @property
    def target_label(self):
        return (self.variant or self.printed_product).label

    username = property(lambda self: self.user.username)


class ItemNote(db.Model):
    """品番ごと(または色・種類ごと)の備考。書いた人と日時が残る。"""
    __tablename__ = "item_note"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False, index=True)
    target = db.Column(db.String(64), nullable=False, default="")  # 対象の色・種類。空なら品番全体
    body = db.Column(db.Text, nullable=False)
    is_resolved = db.Column(db.Boolean, nullable=False, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    item = db.relationship("Item")
    user = db.relationship("User")

    item_label = property(lambda self: self.item.label)
    username = property(lambda self: self.user.username)
    category_id = property(lambda self: self.item.category_id)
    category_name = property(lambda self: self.item.category.name)
    brand = property(lambda self: self.item.brand)
    item_no = property(lambda self: self.item.item_no)
