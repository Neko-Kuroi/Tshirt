import click
from flask import Flask, redirect, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine

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

    @app.errorhandler(404)
    def not_found(_):
        from flask import render_template
        return render_template("error.html", code=404, message="ページが見つかりません。"), 404

    @app.after_request
    def no_cache(resp):
        # 在庫は常に最新を見せたいので、HTMLはキャッシュさせない
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.template_filter("jst")
    def jst(dt):
        """DBはUTC保存。表示は日本時間(JST)。"""
        from datetime import timedelta, timezone
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")

    register_cli(app)
    return app


def register_cli(app):
    from .models import Category, User

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

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--admin", is_flag=True, help="管理者として作成")
    @click.password_option()
    def create_user(username, admin, password):
        """ユーザーを作成する。"""
        if User.query.filter_by(username=username).first():
            raise click.ClickException("そのユーザー名は既に使われています。")
        u = User(username=username, role="admin" if admin else "staff")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"作成しました: {username} ({u.role})")
