import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"


def _load_secret_key() -> str:
    """環境変数 → instance/secret_key の順。無ければ生成して保存する。"""
    env = os.environ.get("INVENTORY_SECRET_KEY")
    if env:
        return env
    INSTANCE_DIR.mkdir(exist_ok=True)
    path = INSTANCE_DIR / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path.read_text().strip()


class Config:
    SECRET_KEY = _load_secret_key()
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "INVENTORY_DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'inventory.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # tailscale serve などで HTTPS 化したら INVENTORY_COOKIE_SECURE=1
    SESSION_COOKIE_SECURE = os.environ.get("INVENTORY_COOKIE_SECURE") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14  # 14日

    HISTORY_PER_PAGE = 50
    # DBが未作成/古いままのとき、500ではなく案内ページ(503)を出す。テストでは切る
    CHECK_SCHEMA = True
    # プロキシ(tailscale serve等)の背後で動かす場合 INVENTORY_BEHIND_PROXY=1
    BEHIND_PROXY = os.environ.get("INVENTORY_BEHIND_PROXY") == "1"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    CHECK_SCHEMA = False
    SECRET_KEY = "test"
