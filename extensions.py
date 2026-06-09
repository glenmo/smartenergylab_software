"""
Flask extension singletons.

Kept in one module so auth.py, app.py and any blueprint can `from
extensions import db, login_manager, ...` without an import cycle.

Each one is initialised against the Flask app inside create_app().
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db            = SQLAlchemy()
login_manager = LoginManager()
csrf          = CSRFProtect()
limiter       = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)

login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"
