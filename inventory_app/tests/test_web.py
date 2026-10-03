from app import db
from app.models import Item, StockMovement, User, Variant


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
