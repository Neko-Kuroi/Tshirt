import pytest

from app import create_app, db
from app.models import Category, Item, User


@pytest.fixture
def app():
    app = create_app("config.TestConfig")
    with app.app_context():
        db.create_all()
        db.session.add_all([
            Category(name="Tシャツ", sort_order=0, uses_color=True, uses_size=True, is_printable=True),
            Category(name="バッジ", sort_order=1, uses_color=False, uses_variant_name=True, is_printable=False),
        ])
        db.session.flush()
        tee = Category.query.filter_by(name="Tシャツ").one()
        badge = Category.query.filter_by(name="バッジ").one()
        db.session.add_all([
            Item(category_id=tee.id, brand="Printstar", item_no="085-CVT", name="Tシャツ"),
            Item(category_id=tee.id, brand="United Athle", item_no="5001", name="Tシャツ"),
            Item(category_id=badge.id, brand="", item_no="44mm", name="缶バッジ"),
        ])
        admin = User(username="admin", role="admin"); admin.set_password("password123")
        staff = User(username="staff", role="staff"); staff.set_password("password123")
        db.session.add_all([admin, staff])
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    def _login(username="admin"):
        r = client.post("/login", data={"username": username, "password": "password123"})
        assert r.status_code == 302
        return client
    return _login
