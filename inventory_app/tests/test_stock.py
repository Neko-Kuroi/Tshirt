import pytest

from app import db
from app.models import Design, Item, PrintedProduct, StockMovement, User, Variant
from app.services import stock
from app.services.stock import StockError


def _user():
    return User.query.filter_by(username="admin").one()


def _tee(brand="Printstar"):
    return Item.query.filter_by(brand=brand).one()


def test_receive_creates_variant_and_movement(app):
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(_user(), v, 30, "納品書A")
    db.session.commit()
    assert Variant.query.one().quantity == 30
    m = StockMovement.query.one()
    assert (m.kind, m.delta, m.variant_id) == ("receive", 30, v.id)


def test_same_spec_reuses_variant_and_brands_are_separate(app):
    a1 = stock.get_or_create_variant(_tee("Printstar"), "黒", "M")
    a2 = stock.get_or_create_variant(_tee("Printstar"), " 黒 ", "M")
    b = stock.get_or_create_variant(_tee("United Athle"), "黒", "M")
    assert a1.id == a2.id
    assert a1.id != b.id


def test_unused_columns_are_blanked_and_required_ones_enforced(app):
    badge = Item.query.filter_by(item_no="44mm").one()
    v = stock.get_or_create_variant(badge, color="赤", size="M", variant_name="デザインA")
    assert (v.color, v.size, v.variant_name) == ("", "", "デザインA")
    with pytest.raises(StockError):
        stock.get_or_create_variant(badge, variant_name="")
    with pytest.raises(StockError):
        stock.get_or_create_variant(_tee(), "黒", "")


def test_cannot_go_negative(app):
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(_user(), v, 5)
    db.session.commit()
    with pytest.raises(StockError):
        stock.ship(_user(), v, 6)
    db.session.rollback()
    # 失敗した出荷は在庫にも履歴にも残らない
    assert Variant.query.one().quantity == 5
    assert StockMovement.query.count() == 1


def test_convert_is_atomic_and_logs_both_sides(app):
    u = _user()
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(u, v, 20)
    db.session.commit()

    batch = stock.convert_to_printed(u, v, "ロゴA", used=10, output=9, note="1枚ミスプリント")
    db.session.commit()

    db.session.expire_all()
    assert Variant.query.one().quantity == 10
    p = PrintedProduct.query.one()
    assert p.quantity == 9 and p.design.name == "ロゴA"
    moves = StockMovement.query.filter_by(batch_id=batch).order_by(StockMovement.id).all()
    assert [(m.kind, m.delta) for m in moves] == [("print_use", -10), ("print_output", 9)]


def test_convert_failure_leaves_nothing_behind(app):
    u = _user()
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(u, v, 3)
    db.session.commit()
    with pytest.raises(StockError):
        stock.convert_to_printed(u, v, "ロゴA", used=5, output=5)
    db.session.rollback()
    assert Variant.query.one().quantity == 3
    assert PrintedProduct.query.count() == 0
    assert StockMovement.query.count() == 1


def test_convert_validations(app):
    u = _user()
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(u, v, 10)
    with pytest.raises(StockError):
        stock.convert_to_printed(u, v, "A", used=5, output=6)  # 完成数 > 使用数
    with pytest.raises(StockError):
        stock.convert_to_printed(u, v, "", used=5, output=5)  # デザイン名なし
    badge = Item.query.filter_by(item_no="44mm").one()
    bv = stock.get_or_create_variant(badge, variant_name="デザインA")
    stock.receive(u, bv, 10)
    with pytest.raises(StockError):  # プリント対象外カテゴリー
        stock.convert_to_printed(u, bv, "A", used=1, output=1)


def test_adjust_requires_reason_and_nonzero(app):
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(_user(), v, 10)
    with pytest.raises(StockError):
        stock.adjust(_user(), v, -1, "")
    with pytest.raises(StockError):
        stock.adjust(_user(), v, 0, "理由")
    stock.adjust(_user(), v, -2, "棚卸し")
    db.session.commit()
    assert Variant.query.one().quantity == 8


def test_ship_printed_product(app):
    u = _user()
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    stock.receive(u, v, 10)
    stock.convert_to_printed(u, v, "ロゴA", 10, 10)
    p = PrintedProduct.query.one()
    stock.ship(u, p, 4)
    db.session.commit()
    assert PrintedProduct.query.one().quantity == 6


# ---- 同時リクエストで同じ行を作ろうとした場合(UNIQUE衝突しても落ちない) ----------

@pytest.fixture
def lose_the_race(monkeypatch):
    """INSERTの直前に「別リクエストが先に同じ行を作った」状況を再現する。"""
    real = stock._insert_ignore

    def racing(model, **values):
        real(model, **values)  # 他リクエストが先に作成
        real(model, **values)  # 自分のINSERTは衝突するが、何も起きず例外にならない
    monkeypatch.setattr(stock, "_insert_ignore", racing)


def test_get_or_create_variant_survives_race(app, lose_the_race):
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    db.session.commit()
    assert Variant.query.count() == 1
    assert v.quantity == 0 and v.updated_at is not None


def test_get_or_create_design_survives_race(app, lose_the_race):
    d = stock.get_or_create_design(" ロゴA ")
    db.session.commit()
    assert Design.query.count() == 1 and d.name == "ロゴA"


def test_get_or_create_product_survives_race(app, monkeypatch):
    v = stock.get_or_create_variant(_tee(), "黒", "M")
    d = stock.get_or_create_design("ロゴA")
    real = stock._insert_ignore

    def racing(model, **values):
        real(model, **values)
        real(model, **values)
    monkeypatch.setattr(stock, "_insert_ignore", racing)
    p = stock.get_or_create_product(d, v)
    db.session.commit()
    assert PrintedProduct.query.count() == 1 and p.quantity == 0
