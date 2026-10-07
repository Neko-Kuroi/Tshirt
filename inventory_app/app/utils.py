from urllib.parse import urlparse

from werkzeug.routing import IntegerConverter

INT64_MAX = 2**63 - 1   # SQLite の整数の上限。これを超える値を渡すと OverflowError になる
MAX_QTY = 1_000_000     # 1回の入荷・出荷・調整の上限。SQLite は整数の足し算が溢れると、
                        # エラーにせず REAL(小数)にしてしまうので、在庫列を守るためにも必要

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
    """フォーム/クエリの文字列を整数にする。変換できない/SQLiteの整数範囲外なら default(例外は出さない)。"""
    try:
        n = int((raw or "").strip())
    except (ValueError, AttributeError):
        return default
    return n if -INT64_MAX <= n <= INT64_MAX else default


class BoundedIntegerConverter(IntegerConverter):
    """URLの <int:x> を SQLite の整数範囲に制限する(範囲外はルートに一致せず 404)。"""

    def __init__(self, map, **kwargs):
        kwargs.setdefault("max", INT64_MAX)
        super().__init__(map, **kwargs)


def safe_next(target):
    """同一サイト内の相対パスだけ許可(オープンリダイレクト対策)。それ以外は None。"""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//") \
            or target.startswith("/\\"):
        return None
    return target


def is_unique_violation(exc) -> bool:
    """IntegrityError が UNIQUE 違反か(SQLite / PostgreSQL 共通)。
    他の制約違反(NOT NULL など)を『重複』と誤表示しないために使う。"""
    return "unique constraint" in str(exc).lower()
