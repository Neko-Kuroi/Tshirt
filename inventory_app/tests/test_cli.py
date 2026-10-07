import os
from pathlib import Path

from flask_migrate import upgrade

from app import create_app, db
from app.models import User
from config import TestConfig

MIGRATIONS_DIR = str(Path(__file__).parent.parent / "migrations")


def _runner(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def _file_app(tmp_path, name):
    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / name}"

    return create_app(Cfg), tmp_path / name


def test_list_users_shows_name_role_and_status_but_not_the_hash(app):
    staff = User.query.filter_by(username="staff").one()
    staff.is_active_flag = False
    pw_hash = staff.password_hash
    db.session.commit()
    r = _runner(app, "list-users")
    assert r.exit_code == 0
    assert r.output.strip().splitlines() == ["admin\t管理者\t有効", "staff\tスタッフ\t無効"]
    assert pw_hash not in r.output


def test_list_users_with_no_users(tmp_path):
    app, _ = _file_app(tmp_path, "empty.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR)
    r = _runner(app, "list-users")
    assert r.exit_code == 0 and "まだ登録されていません" in r.output


def test_list_users_on_missing_db_does_not_create_it(tmp_path):
    app, path = _file_app(tmp_path, "missing.db")
    r = _runner(app, "list-users")
    assert r.exit_code != 0 and "flask db upgrade" in r.output
    assert not os.path.exists(path)


def test_list_users_on_uninitialized_db(tmp_path):
    app, path = _file_app(tmp_path, "blank.db")
    open(path, "w").close()                       # 空ファイル(テーブルなし)
    r = _runner(app, "list-users")
    assert r.exit_code != 0 and "flask db upgrade" in r.output
