from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager


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


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


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
    """ブランド・品番単位のマスタ。"""
    __tablename__ = "item"
    __table_args__ = (UniqueConstraint("category_id", "brand", "item_no"),)
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    brand = db.Column(db.String(64), nullable=False, default="")
    item_no = db.Column(db.String(64), nullable=False, default="")
    name = db.Column(db.String(128), nullable=False, default="")
    note = db.Column(db.Text, nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    category = db.relationship("Category", back_populates="items")
    variants = db.relationship("Variant", back_populates="item")

    @property
    def label(self):
        head = " ".join(p for p in (self.brand, self.item_no) if p)
        return f"{head} {self.name}".strip() or f"Item#{self.id}"


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
    def label(self):
        return f"{self.item.label} | {self.spec}"


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
