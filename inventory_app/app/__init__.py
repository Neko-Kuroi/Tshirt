import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import Flask, Response, render_template, request
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

# 制約名を固定(マイグレーションのbatchモードで必要)
naming = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
db = SQLAlchemy(metadata=MetaData(naming_convention=naming))
migrate = Migrate(render_as_batch=True)
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "ログインしてください。"
login_manager.login_message_category = "warning"

JST = timezone(timedelta(hours=9))

_SCHEMA_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>準備中</title></head>
<body style="font-family:sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem">
<h1 style="font-size:1.3rem">データベースの更新が必要です</h1>
<p>管理者に連絡してください。</p>
<p style="color:#666;font-size:.9rem">(管理者向け: サーバーで <code>flask db upgrade</code> を実行すると、再起動なしで使えるようになります)</p>
</body></html>"""


def _sqlite_path(app):
    """SQLite のDBファイルのパス(SQLite以外・メモリDBなら None)。"""
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return None
    return url.database if os.path.isabs(url.database) else os.path.join(app.instance_path, url.database)


def _schema_is_current(app) -> bool:
    """DBの Alembic リビジョンが、migrations/ の最新(head)と同じか。"""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(app.root_path).parent / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with db.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision() == head


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    """SQLite のときだけ外部キー・WAL・待ち時間を設定する。"""
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    if isinstance(config_object, str):
        from werkzeug.utils import import_string
        config_object = import_string(config_object)
    app.config.from_object(config_object)

    from .utils import BoundedIntegerConverter
    app.url_map.converters["int"] = BoundedIntegerConverter  # blueprint登録より前に差し替える

    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401
    from .auth import bp as auth_bp
    from .inventory import bp as inventory_bp
    from .admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(404)
    def not_found(_):
        return error_page(404, "ページが見つかりません。")

    @app.errorhandler(403)
    def forbidden(_):
        return error_page(403, "この操作をする権限がありません。")

    @app.errorhandler(CSRFError)
    def csrf_failed(_):
        return error_page(400, "画面の有効期限が切れたか、不正な送信です。ページを開き直して、もう一度お試しください。")

    @app.errorhandler(OperationalError)
    def db_busy(e):
        db.session.rollback()
        app.logger.exception("database error")
        if "locked" in str(e) or "busy" in str(e):
            return error_page(503, "ただいま混み合っています。数秒おいて、もう一度お試しください。")
        return error_page(500, "データベースでエラーが起きました。管理者に連絡してください。")

    @app.errorhandler(500)
    def server_error(_):
        return error_page(500, "エラーが起きました。管理者に連絡してください。")

    @app.before_request
    def require_current_schema():
        """DBが未作成/古いままだと、画面を開いて初めて500になる。先に分かりやすく止める。"""
        if (not app.config.get("CHECK_SCHEMA", True) or app.extensions.get("schema_ok")
                or request.endpoint == "static"):
            return None
        path = _sqlite_path(app)
        # 存在しないファイルに接続すると、空のDBが勝手に作られてしまうので先に確かめる
        if not (path and not os.path.exists(path)):
            try:
                if _schema_is_current(app):
                    app.extensions["schema_ok"] = True  # 更新が済めば、それ以降は確認しない
                    return None
            except (OperationalError, ProgrammingError):
                pass  # テーブルが無い(未初期化)
        if not app.extensions.get("schema_warned"):
            app.extensions["schema_warned"] = True
            app.logger.error("DBが未作成か古いバージョンです。`flask db upgrade` を実行してください。")
        return Response(_SCHEMA_PAGE, 503, content_type="text/html; charset=utf-8",
                        headers={"Retry-After": "60"})

    @app.after_request
    def no_cache(resp):
        # 在庫は常に最新を見せたいので、HTMLはキャッシュさせない
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.template_filter("jst")
    def jst(value):
        """DBはUTC保存。表示は日本時間(JST)。"""
        if not value:
            return ""
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%m/%d %H:%M")

    register_cli(app)
    return app


def register_cli(app):
    from .models import Category, User
    from .utils import is_unique_violation

    @app.cli.command("seed")
    def seed():
        """初期カテゴリーを登録する(既にあれば何もしない)。"""
        if Category.query.first():
            click.echo("カテゴリーは登録済みです。")
            return
        defaults = [
            # name, color, size, variant_name, printable
            ("Tシャツ", True, True, False, True),
            ("キャップ", True, False, False, True),
            ("カバン", True, False, False, True),
            ("バッジ", False, False, True, False),
        ]
        for i, (name, c, s, v, p) in enumerate(defaults):
            db.session.add(Category(
                name=name, sort_order=i, uses_color=c, uses_size=s,
                uses_variant_name=v, is_printable=p))
        db.session.commit()
        click.echo("初期カテゴリーを登録しました。")

    @app.cli.command("list-users")
    def list_users():
        """登録済みのユーザー名・権限・有効/無効を表示する(パスワードは表示しない)。"""
        path = _sqlite_path(app)
        # 存在しないファイルに接続すると空のDBが勝手に作られてしまうので、先に確かめる
        if path and not os.path.exists(path):
            raise click.ClickException(f"DBがありません({path})。先に flask db upgrade を実行してください。")
        try:
            users = User.query.order_by(User.id).all()
        except (OperationalError, ProgrammingError):
            raise click.ClickException("DBが初期化されていません。先に flask db upgrade を実行してください。")
        if not users:
            click.echo("ユーザーはまだ登録されていません。(flask create-user --admin 名前 で作成できます)")
            return
        for u in users:
            role = "管理者" if u.is_admin else "スタッフ"
            click.echo(f"{u.username}\t{role}\t{'有効' if u.is_active_flag else '無効'}")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--admin", is_flag=True, help="管理者として作成")
    @click.password_option()
    def create_user(username, admin, password):
        """ユーザーを作成する。"""
        username = username.strip()  # ログイン画面・管理画面と同じ扱い。空白付きだと二度とログインできない
        if not username:
            raise click.ClickException("ユーザー名を入力してください。")
        u = User(username=username, role="admin" if admin else "staff")
        u.set_password(password)
        db.session.add(u)
        try:
            db.session.commit()
        except IntegrityError as e:
            db.session.rollback()
            if not is_unique_violation(e):
                raise
            raise click.ClickException("そのユーザー名は既に使われています。")
        click.echo(f"作成しました: {username} ({u.role})")
