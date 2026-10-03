from flask import Blueprint

bp = Blueprint("inventory", __name__)

from . import routes  # noqa: E402,F401
