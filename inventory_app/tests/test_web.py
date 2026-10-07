import pytest
from flask import g
from sqlalchemy import event

from app import db
from app.models import Category, Item, ItemNote, StockMovement, User, Variant
from app.services import stock


def test_login_required(client):
    for path in ["/", "/blank", "/printed", "/receive", "/convert", "/move", "/history", "/admin/users"]:
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path


def test_login_logout_and_open_redirect_blocked(client):
    r = client.post("/login?next=https://evil.example/", data={"username": "admin", "password": "password123"})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    r = client.post("/logout")
    assert r.status_code == 302
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 200 and "違います" in r.get_data(as_text=True)


def test_disabled_user_cannot_login(client, app):
    with app.app_context():
        User.query.filter_by(username="staff").one().is_active_flag = False
        db.session.commit()
    r = client.post("/login", data={"username": "staff", "password": "password123"})
    assert r.status_code == 200


def test_staff_cannot_access_admin(client, login):
    login("staff")
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/items").status_code == 403


def test_pages_render_and_full_flow(client, login, app):
    login("admin")
    with app.app_context():
        item_id = Item.query.filter_by(brand="Printstar").one().id
    for path in ["/", "/blank", "/printed", "/receive", "/convert", "/move", "/history",
                 "/admin/items", "/admin/categories", "/admin/users"]:
        assert client.get(path).status_code == 200, path

    r = client.post("/receive", data={"item_id": item_id, "color": "黒", "size": "M", "qty": "20"})
    assert r.status_code == 302
    r = client.post("/receive", data={"item_id": item_id, "color": "黒", "size": "L", "qty": "5"})
    html = client.get("/blank").get_data(as_text=True)
    assert "Printstar" in html and ">20<" in html and ">25<" in html  # 行合計 25

    with app.app_context():
        vid = Variant.query.filter_by(size="M").one().id
    r = client.post("/convert", data={"variant_id": vid, "design": "", "design_new": "ロゴA",
                                      "used": "10", "output": "9"})
    assert r.status_code == 302
    assert "ロゴA" in client.get("/printed").get_data(as_text=True)

    # 在庫超過はエラー表示で、何も変わらない
    r = client.post("/convert", data={"variant_id": vid, "design_new": "ロゴA", "used": "999", "output": "1"},
                    follow_redirects=True)
    assert "在庫が足りません" in r.get_data(as_text=True)
    with app.app_context():
        assert Variant.query.filter_by(size="M").one().quantity == 10
        assert StockMovement.query.count() == 4  # receive2 + use + output


def test_receive_rejects_garbage_qty(client, login, app):
    login("admin")
    with app.app_context():
        item_id = Item.query.filter_by(brand="Printstar").one().id
    for bad in ["", "abc", "-3", "0"]:
        r = client.post("/receive", data={"item_id": item_id, "color": "黒", "size": "M", "qty": bad},
                        follow_redirects=True)
        assert "1以上の整数" in r.get_data(as_text=True), bad
    with app.app_context():
        assert StockMovement.query.count() == 0


# ---- 管理画面: 数値でない入力で500にならない ----------------------------------

def test_admin_invalid_numbers_do_not_crash(client, login):
    login("admin")
    r = client.post("/admin/categories/1", data={"name": "Tシャツ", "sort_order": "abc"})
    assert r.status_code == 200 and "整数" in r.get_data(as_text=True)
    r = client.post("/admin/items", data={"category_id": "abc", "brand": "X"})
    assert r.status_code == 200 and "カテゴリーを選んでください" in r.get_data(as_text=True)


def test_admin_invalid_sort_order_changes_nothing_and_blank_means_zero(client, login):
    login("admin")
    before = db.session.get(Category, 1)
    name = before.name
    client.post("/admin/categories/1", data={"name": "改名", "sort_order": "abc"})
    db.session.remove()
    assert db.session.get(Category, 1).name == name  # 不正入力では何も保存されない
    r = client.post("/admin/categories/1", data={"name": name, "sort_order": ""})
    assert r.status_code == 302
    db.session.remove()
    assert db.session.get(Category, 1).sort_order == 0


# ---- N+1の防止: 表示行が増えてもSQL発行数が増えない -----------------------------

def _seed_printed(n, start):
    tee = Category.query.filter_by(name="Tシャツ").one()
    admin = User.query.filter_by(username="admin").one()
    for i in range(start, start + n):
        item = Item(category_id=tee.id, brand=f"Brand{i}", item_no=f"No{i}", name="T")
        db.session.add(item)
        db.session.flush()
        v = stock.get_or_create_variant(item, "黒", "M")
        stock.receive(admin, v, 10)
        stock.convert_to_printed(admin, v, f"Design{i}", 5, 5)
        db.session.add(ItemNote(item_id=item.id, target="黒", body=f"備考{i}", user_id=admin.id))
    db.session.commit()


def _count_queries(client, path):
    db.session.remove()  # identity mapを空にして、遅延ロードを正しく数える
    # テストはapp contextを共有するので、Flask-Loginがgに保持した旧セッションのユーザーを捨てる
    g.pop("_login_user", None)
    n = []

    def cap(*args):
        n.append(1)
    event.listen(db.engine, "before_cursor_execute", cap)
    try:
        assert client.get(path).status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", cap)
    return len(n)


@pytest.mark.parametrize("path", ["/", "/blank?category=1", "/printed", "/convert", "/move", "/history", "/notes"])
def test_query_count_does_not_grow_with_rows(client, login, path):
    login("admin")
    _seed_printed(3, 0)
    small = _count_queries(client, path)
    _seed_printed(12, 3)
    large = _count_queries(client, path)
    assert large == small, f"{path}: {small} -> {large}"
