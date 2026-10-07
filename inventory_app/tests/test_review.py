"""レビュー指摘・備考機能の回帰テスト(sqlite3 版の tests/test_sqlalchemy_db_and_review.py と同じ観点)。"""
import re
import sqlite3
from pathlib import Path

import pytest
from flask import g
from flask_migrate import stamp, upgrade
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.inventory import routes as inventory_routes
from app.models import Category, Item, ItemNote, PrintedProduct, StockMovement, User, Variant
from app.services import stock
from config import TestConfig

HERE = Path(__file__).parent
MIGRATIONS_DIR = str(HERE.parent / "migrations")
SQLITE3_VERSION_DDL = (HERE / "sqlite3_version_schema.sql").read_text(encoding="utf-8")
REV1 = "afd0c4cd0c7c"


def _get(client, path):
    """fixture が app context を握ったままだと、Flask-Login がリクエスト間で『ログイン中のユーザー』を
    使い回す(テスト特有)。毎回捨ててから叩く。"""
    g.pop("_login_user", None)
    return client.get(path)


def _post(client, path, data):
    g.pop("_login_user", None)
    return client.post(path, data=data)


def _login(app, username="admin", password="password123"):
    g.pop("_login_user", None)
    c = app.test_client()
    r = c.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302 and r.headers["Location"].endswith("/"), "ログインできていない"
    g.pop("_login_user", None)
    return c


def _admin():
    return User.query.filter_by(username="admin").one()


def _tee(brand="Printstar"):
    return Item.query.filter_by(brand=brand).one()


# ---- 旧『メモ』欄 → 備考(Alembic リビジョン1 → 2) -----------------------------------

def _file_app(tmp_path, name="f.db", **extra):
    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / name}"

    for k, v in extra.items():
        setattr(Cfg, k, v)
    return create_app(Cfg), tmp_path / name


def test_upgrade_moves_old_item_note_into_item_notes(tmp_path):
    app, path = _file_app(tmp_path)
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR, revision=REV1)  # 備考機能より前のDB
        raw = sqlite3.connect(path)
        raw.executescript("""
        INSERT INTO category VALUES (1,'Tシャツ',0,1,1,0,1);
        INSERT INTO item VALUES (1,1,'Printstar','085-CVT','Tシャツ','旧メモ: 色違いを探す',1),
                                (2,1,'United Athle','5001','Tシャツ','',1);
        """)
        raw.execute('INSERT INTO "user" VALUES (1, \'neko\', ?, \'admin\', 1, \'2026-01-01 00:00:00\')',
                    (generate_password_hash("x"),))
        raw.commit()
        raw.close()
        upgrade(directory=MIGRATIONS_DIR)
        (n,) = ItemNote.query.all()
        assert n.item_id == 1 and "色違いを探す" in n.body and n.username == "neko" and n.is_resolved is False


def test_upgrade_without_users_does_not_fail(tmp_path):
    app, path = _file_app(tmp_path)
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR, revision=REV1)
        raw = sqlite3.connect(path)
        raw.executescript("INSERT INTO category VALUES (1,'T',0,1,1,0,1); "
                          "INSERT INTO item VALUES (1,1,'B','1','x','メモ',1);")
        raw.commit()
        raw.close()
        upgrade(directory=MIGRATIONS_DIR)  # NOT NULL の user_id を埋められないので、移行をスキップ
        assert ItemNote.query.count() == 0


# ---- sqlite3 版が作ったDBを、SQLAlchemy 版(この版)でも使える ----------------------------------

@pytest.fixture
def sqlite3_made_app(tmp_path):
    app, path = _file_app(tmp_path, "from_sqlite3.db")
    raw = sqlite3.connect(path)
    raw.executescript(SQLITE3_VERSION_DDL)  # sqlite3 版が作るスキーマ(DEFAULT付き)
    raw.executescript("""
    INSERT INTO category (name, sort_order, uses_color, uses_size, uses_variant_name, is_printable)
      VALUES ('Tシャツ',0,1,1,0,1), ('バッジ',1,0,0,1,0);
    INSERT INTO item (category_id, brand, item_no, name) VALUES (1,'Printstar','085-CVT','Tシャツ'), (2,'','44mm','缶バッジ');
    INSERT INTO variant (item_id, color, size, quantity) VALUES (1,'黒','M',10);
    INSERT INTO design (name) VALUES ('ロゴA');
    INSERT INTO printed_product (design_id, variant_id, quantity) VALUES (1,1,9);
    INSERT INTO item_note (item_id, target, body, user_id) VALUES (1,'黒','sqlite3版で書いた備考',1);
    """)
    raw.execute('INSERT INTO "user" (username, password_hash, role) VALUES (?,?,?)',
                ("neko", generate_password_hash("pass12345"), "admin"))
    raw.execute("INSERT INTO stock_movement (batch_id, kind, variant_id, delta, note, user_id, created_at) "
                "VALUES ('b1','receive',1,10,'sqlite3版で入荷',1,'2026-10-04 00:20:10')")  # マイクロ秒なしの形式
    raw.commit()
    raw.close()
    with app.app_context():
        stamp(directory=MIGRATIONS_DIR, revision="head")  # 『ここまで適用済み』と記録するだけ(READMEの手順)
        upgrade(directory=MIGRATIONS_DIR)                 # 以後の upgrade は何もしない
        yield app


def test_db_made_by_sqlite3_version_works_in_this_version(sqlite3_made_app):
    c = _login(sqlite3_made_app, "neko", "pass12345")
    for path in ["/", "/blank", "/printed", "/receive?item_id=1", "/convert", "/move", "/history", "/notes",
                 "/admin/items", "/admin/categories", "/admin/users"]:
        assert _get(c, path).status_code == 200, path
    assert "sqlite3版で書いた備考" in _get(c, "/notes").get_data(as_text=True)
    assert "10/04 09:20" in _get(c, "/history").get_data(as_text=True)  # UTC 00:20 → JST 09:20

    assert _post(c, "/receive", {"item_id": 1, "color": "白", "size": "L", "qty": "7"}).status_code == 302
    assert _post(c, "/convert", {"variant_id": 1, "design_new": "新デザイン", "used": "4", "output": "4"}).status_code == 302
    assert _post(c, "/move", {"kind": "ship", "target": "v:1", "qty": "1"}).status_code == 302
    assert _post(c, "/move", {"kind": "adjust", "target": "p:1", "qty": "-1", "note": "数え直し"}).status_code == 302
    assert _post(c, "/admin/users", {"username": "staff1", "password": "longenough1"}).status_code == 302
    assert _post(c, "/admin/items", {"category_id": 1, "brand": "United Athle", "item_no": "5001"}).status_code == 302
    assert _post(c, "/notes", {"item_id": 1, "target": "黒", "body": "この版で書いた備考"}).status_code == 302
    db.session.expire_all()
    assert db.session.get(Variant, 1).quantity == 10 - 4 - 1
    assert Variant.query.filter_by(color="白", size="L").one().quantity == 7
    assert ItemNote.query.count() == 2


# ---- 1: ブランドか品番のどちらか必須 ----------------------------------------------

def test_item_requires_brand_or_item_no(client, login):
    login("admin")
    before = Item.query.count()
    for data in [{"name": "名前だけ"}, {"brand": " ", "item_no": " ", "name": "空白だけ"}]:
        r = client.post("/admin/items", data={"category_id": 1, **data})
        assert r.status_code == 200 and "どちらかは入力" in r.get_data(as_text=True)
    assert Item.query.count() == before
    assert client.post("/admin/items", data={"category_id": 2, "item_no": "55mm"}).status_code == 302
    assert client.post("/admin/items", data={"category_id": 2, "brand": "ブランドのみ"}).status_code == 302
    r = client.post("/admin/items", data={"category_id": 2, "item_no": "55mm"})
    assert "既にあります" in r.get_data(as_text=True)


def test_item_edit_cannot_blank_out_identity(client, login):
    login("admin")
    r = client.post("/admin/items/1", data={"brand": "", "item_no": "", "name": "x", "is_active": "on"})
    assert r.status_code == 200 and "どちらかは入力" in r.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Item, 1).brand == "Printstar"
    assert client.post("/admin/items/1", data={"brand": "Printstar", "item_no": "085-CVT",
                                              "name": "改名", "is_active": "on"}).status_code == 302
    db.session.expire_all()
    assert db.session.get(Item, 1).item_name == "改名"


def test_item_label_fallback_uses_item_id(app):
    item = Item(category_id=1, brand="", item_no="", name="")  # 旧データなどで、全項目が空の品番
    db.session.add(item)
    db.session.flush()
    v = stock.get_or_create_variant(item, "黒", "M")
    stock.receive(_admin(), v, 5)
    stock.convert_to_printed(_admin(), v, "ロゴA", 5, 5)
    db.session.commit()
    p = PrintedProduct.query.one()
    assert p.id != item.id and f"Item#{item.id}" in p.label
    assert f"Item#{item.id}" in StockMovement.query.order_by(StockMovement.id.desc()).first().target_label


# ---- 2: 使用中のカテゴリー設定はOFFにできない ---------------------------------------

def _tee_form(**flags):
    base = {"name": "Tシャツ", "sort_order": "0", "uses_color": "on", "uses_size": "on", "is_printable": "on"}
    base.update(flags)
    return {k: v for k, v in base.items() if v is not None}


def test_category_flag_in_use_cannot_be_turned_off(client, login):
    login("admin")
    stock.receive(_admin(), stock.get_or_create_variant(_tee(), "黒", "M"), 5)
    db.session.commit()
    r = client.post("/admin/categories/1", data=_tee_form(uses_size=None))
    text = r.get_data(as_text=True)
    assert r.status_code == 200 and "サイズ" in text and "OFFにできません" in text
    db.session.expire_all()
    assert db.session.get(Category, 1).uses_size is True
    assert client.post("/admin/categories/1", data=_tee_form(uses_variant_name="on")).status_code == 302
    db.session.expire_all()
    assert db.session.get(Category, 1).uses_variant_name is True
    assert client.post("/admin/categories/1", data=_tee_form()).status_code == 302
    db.session.expire_all()
    assert db.session.get(Category, 1).uses_variant_name is False


def test_category_flag_without_stock_can_be_turned_off(client, login):
    login("admin")
    assert client.post("/admin/categories/2", data={"name": "バッジ", "sort_order": "1",
                                                   "uses_color": "on"}).status_code == 302
    db.session.expire_all()
    assert db.session.get(Category, 2).uses_variant_name is False


# ---- 4: パスワード変更で既存セッションが切れる --------------------------------------

def test_admin_password_reset_logs_out_that_user(app):
    admin, staff = _login(app, "admin"), _login(app, "staff")
    assert _get(staff, "/").status_code == 200
    sid = User.query.filter_by(username="staff").one().id
    assert _post(admin, f"/admin/users/{sid}/password", {"password": "brandnew-pass"}).status_code == 302
    r = _get(staff, "/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert _get(_login(app, "staff", "brandnew-pass"), "/").status_code == 200
    assert _get(admin, "/").status_code == 200


def test_changing_own_password_keeps_own_session_only(app):
    me, other = _login(app, "staff"), _login(app, "staff")
    r = _post(me, "/account/password", {"old": "password123", "new": "another-pass1", "confirm": "another-pass1"})
    assert r.status_code == 302
    assert _get(me, "/").status_code == 200
    assert _get(other, "/").status_code == 302


# ---- 5: エラーページ ---------------------------------------------------------------

def test_japanese_error_pages(client, login, monkeypatch):
    login("staff")
    r = client.get("/admin/users")
    assert r.status_code == 403 and "権限" in r.get_data(as_text=True)

    def locked(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))
    monkeypatch.setattr(inventory_routes, "_open_note_count", locked)
    r = client.get("/")
    assert r.status_code == 503 and "混み合って" in r.get_data(as_text=True)


def test_csrf_is_enforced_when_enabled(tmp_path):
    app, _ = _file_app(tmp_path, "csrf.db", WTF_CSRF_ENABLED=True)
    with app.app_context():
        db.create_all()
        u = User(username="u", role="staff")
        u.set_password("password123")
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    r = c.post("/login", data={"username": "u", "password": "password123"})
    assert r.status_code == 400 and "有効期限" in r.get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
    r = c.post("/login", data={"csrf_token": token, "username": "u", "password": "password123"})
    assert r.status_code == 302


def test_open_redirect_is_blocked(client):
    for evil in ["https://evil.example/", "//evil.example.com", "/\\evil.example.com", "javascript:alert(1)"]:
        r = client.post(f"/login?next={evil}", data={"username": "admin", "password": "password123"})
        assert r.status_code == 302 and r.headers["Location"] == "/", evil
        client.post("/logout")


# ---- 6: 停止中の品番 ---------------------------------------------------------------

def test_inactive_item_cannot_receive_or_convert_but_can_ship(client, login):
    login("admin")
    client.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "10"})
    vid = Variant.query.one().id
    db.session.get(Item, 1).is_active = False
    db.session.commit()
    r = client.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "1"}, follow_redirects=True)
    assert "使用停止中" in r.get_data(as_text=True)
    r = client.post("/convert", data={"variant_id": vid, "design_new": "A", "used": "1", "output": "1"},
                    follow_redirects=True)
    assert "使用停止中" in r.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Variant, vid).quantity == 10
    assert client.post("/move", data={"kind": "ship", "target": f"v:{vid}", "qty": "10"}).status_code == 302
    db.session.expire_all()
    assert db.session.get(Variant, vid).quantity == 0


# ---- 備考 --------------------------------------------------------------------------

def _open_notes():
    db.session.expire_all()
    return ItemNote.query.filter_by(is_resolved=False).order_by(ItemNote.id.desc()).all()


def test_notes_flow(client, login):
    login("staff")
    msg = "これで入荷は最後。この色は別ブランドの近い色を、仕入れ先を変えて探す。"
    client.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "3"})
    r = client.post("/notes", data={"item_id": 1, "target": "黒", "body": msg, "back": "/blank?category=1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/blank?category=1")

    blank = client.get("/blank?category=1").get_data(as_text=True)
    assert msg in blank and "備考" in blank
    assert re.search(r"黒\s*<span class=\"badge text-bg-warning\">備考</span>", blank)
    assert msg in client.get("/receive?item_id=1").get_data(as_text=True)
    dash = client.get("/").get_data(as_text=True)
    assert msg in dash and "未対応の備考" in dash
    assert msg in client.get("/notes").get_data(as_text=True)

    nid = _open_notes()[0].id
    client.post(f"/notes/{nid}/resolve", data={})
    assert msg not in client.get("/notes").get_data(as_text=True)
    assert msg in client.get("/notes?all=1").get_data(as_text=True)
    assert _open_notes() == []
    client.post(f"/notes/{nid}/resolve", data={})
    assert len(_open_notes()) == 1


def test_notes_validation_permissions_and_escaping(client, login):
    login("staff")
    for data in [{"item_id": 1, "body": "   "}, {"item_id": 999, "body": "x"}, {"item_id": 1, "body": "x" * 1001},
                 {"item_id": 1, "target": "t" * 65, "body": "x"}]:
        client.post("/notes", data=data)
    assert _open_notes() == []

    client.post("/notes", data={"item_id": 1, "body": "<script>alert(1)</script>", "back": "https://evil.example"})
    r = client.post("/notes", data={"item_id": 1, "body": "確認", "back": "https://evil.example"})
    assert "evil.example" not in r.headers["Location"]
    page = client.get("/notes").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page

    nid = _open_notes()[0].id
    assert client.post(f"/notes/{nid}/delete", data={}).status_code == 403
    client.post("/logout")
    login("admin")
    assert client.post(f"/notes/{nid}/delete", data={}).status_code == 302
    db.session.expire_all()
    assert db.session.get(ItemNote, nid) is None
    assert client.post("/notes/99999/resolve", data={}).status_code == 404
