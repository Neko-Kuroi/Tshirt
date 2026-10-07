"""flask create-user: ユーザー名の前後の空白の扱い(ログイン画面・管理画面と同じく strip する)。"""
from app import db
from app.models import User


def _create(app, username, password="password123"):
    return app.test_cli_runner().invoke(args=["create-user", username, "--password", password])


def _names():
    db.session.expire_all()
    return [u.username for u in User.query.order_by(User.id)]


def test_surrounding_whitespace_is_stripped_so_the_user_can_log_in(app, client):
    for raw in (" pad1", "pad2 ", "\u3000pad3\u3000"):      # 半角・全角の空白
        assert _create(app, raw).exit_code == 0
    assert _names()[-3:] == ["pad1", "pad2", "pad3"]
    for name in ("pad1", "pad2", "pad3"):
        r = client.post("/login", data={"username": name, "password": "password123"})
        assert r.status_code == 302, name
        client.post("/logout")


def test_blank_username_is_rejected(app):
    before = _names()
    for raw in ("", "   ", "\u3000"):
        r = _create(app, raw)
        assert r.exit_code != 0 and "ユーザー名を入力してください" in r.output
    assert _names() == before


def test_duplicate_is_detected_after_stripping(app):
    assert _create(app, "pad").exit_code == 0
    r = _create(app, " pad ")
    assert r.exit_code != 0 and "既に使われています" in r.output
    assert _names().count("pad") == 1
