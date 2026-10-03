"""在庫操作ロジック。画面側は quantity を直接触らず、必ずここの関数を使う。

各関数は flush までで commit はしない(呼び出し側が1リクエスト=1トランザクションで commit する)。
失敗時は StockError を投げるので、呼び出し側で rollback すること。
"""
import uuid

from sqlalchemy import update

from .. import db
from ..models import Design, Item, PrintedProduct, StockMovement, Variant


class StockError(Exception):
    """ユーザーに見せてよいエラー。"""


def _norm(s) -> str:
    return (s or "").strip()


def _apply(model, row_id: int, delta: int):
    """在庫を原子的に増減する。マイナスになる場合は更新せず StockError。"""
    res = db.session.execute(
        update(model)
        .where(model.id == row_id, model.quantity + delta >= 0)
        .values(quantity=model.quantity + delta)
    )
    if res.rowcount != 1:
        raise StockError("在庫が足りません。")


def _log(batch_id, kind, user, delta, note, variant=None, product=None):
    db.session.add(StockMovement(
        batch_id=batch_id, kind=kind, user_id=user.id, delta=delta, note=_norm(note),
        variant_id=variant.id if variant else None,
        printed_product_id=product.id if product else None,
    ))


def _positive(n, label="数量") -> int:
    if not isinstance(n, int) or n <= 0:
        raise StockError(f"{label}は1以上の整数で入力してください。")
    return n


def get_or_create_variant(item: Item, color="", size="", variant_name="") -> Variant:
    """カテゴリーの設定に合わない列は空文字に落として登録する。"""
    cat = item.category
    color = _norm(color) if cat.uses_color else ""
    size = _norm(size) if cat.uses_size else ""
    variant_name = _norm(variant_name) if cat.uses_variant_name else ""
    if cat.uses_color and not color:
        raise StockError("色を入力してください。")
    if cat.uses_size and not size:
        raise StockError("サイズを入力してください。")
    if cat.uses_variant_name and not variant_name:
        raise StockError("種類名を入力してください。")
    v = Variant.query.filter_by(
        item_id=item.id, color=color, size=size, variant_name=variant_name).first()
    if not v:
        v = Variant(item_id=item.id, color=color, size=size, variant_name=variant_name, quantity=0)
        db.session.add(v)
        db.session.flush()
    return v


def get_or_create_design(name: str) -> Design:
    name = _norm(name)
    if not name:
        raise StockError("デザイン名を入力してください。")
    d = Design.query.filter_by(name=name).first()
    if not d:
        d = Design(name=name)
        db.session.add(d)
        db.session.flush()
    return d


def get_or_create_product(design: Design, variant: Variant) -> PrintedProduct:
    p = PrintedProduct.query.filter_by(design_id=design.id, variant_id=variant.id).first()
    if not p:
        p = PrintedProduct(design_id=design.id, variant_id=variant.id, quantity=0)
        db.session.add(p)
        db.session.flush()
    return p


# ---- 操作 -------------------------------------------------------------

def receive(user, variant: Variant, qty: int, note="") -> str:
    qty = _positive(qty)
    batch = str(uuid.uuid4())
    _apply(Variant, variant.id, qty)
    _log(batch, "receive", user, qty, note, variant=variant)
    db.session.flush()
    return batch


def ship(user, target, qty: int, note="") -> str:
    """target は Variant か PrintedProduct。"""
    qty = _positive(qty)
    batch = str(uuid.uuid4())
    model = type(target)
    _apply(model, target.id, -qty)
    if isinstance(target, Variant):
        _log(batch, "ship", user, -qty, note, variant=target)
    else:
        _log(batch, "ship", user, -qty, note, product=target)
    db.session.flush()
    return batch


def adjust(user, target, delta: int, note: str) -> str:
    """棚卸し調整。増減どちらも可。理由(note)は必須。"""
    if not isinstance(delta, int) or delta == 0:
        raise StockError("増減数は0以外の整数で入力してください。")
    if not _norm(note):
        raise StockError("棚卸し調整には理由を入力してください。")
    batch = str(uuid.uuid4())
    _apply(type(target), target.id, delta)
    if isinstance(target, Variant):
        _log(batch, "adjust", user, delta, note, variant=target)
    else:
        _log(batch, "adjust", user, delta, note, product=target)
    db.session.flush()
    return batch


def convert_to_printed(user, variant: Variant, design_name: str,
                       used: int, output: int, note="") -> str:
    """無地在庫 → プリント済み製品。used 枚消費して output 枚完成(ミスプリントで差が出てよい)。"""
    used = _positive(used, "使用数")
    if not isinstance(output, int) or output < 0:
        raise StockError("完成数は0以上の整数で入力してください。")
    if output > used:
        raise StockError("完成数が使用数を超えています。")
    if not variant.item.category.is_printable:
        raise StockError("このカテゴリーはプリント対象ではありません。")

    design = get_or_create_design(design_name)
    batch = str(uuid.uuid4())

    _apply(Variant, variant.id, -used)
    _log(batch, "print_use", user, -used, note, variant=variant)
    if output > 0:
        product = get_or_create_product(design, variant)
        _apply(PrintedProduct, product.id, output)
        _log(batch, "print_output", user, output, note, product=product)
    db.session.flush()
    return batch
