from functools import wraps

from flask import Blueprint, abort
from flask_login import current_user, login_required

bp = Blueprint("admin", __name__)


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


from . import routes  # noqa: E402,F401
