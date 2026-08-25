"""
Smart Energy Lab portal — Flask entry point.

One Flask app serves three subdomains via Apache mod_proxy:

    smartenergylab.software        portal + login + forgot/reset
    fox.smartenergylab.software    reverse-proxied to fox-monitor on desky
    solis.smartenergylab.software  reverse-proxied to microgrid on rubberduck

Routing is host-aware:
  - Apex domain → portal_bp / auth_bp views.
  - Inverter subdomains → forward() in proxy.py via a before_request hook,
    after the auth gate. The proxy doesn't need its own URL routes
    because the before_request shortcut runs first for every request.
"""

import logging
import os

from flask import Blueprint, Flask, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import bp as auth_bp
from extensions import csrf, db, limiter, login_manager
from proxy import auth_gate, forward, is_proxied_host
from toolsite import is_tools_host, serve_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("portal")


# ---------------------------------------------------------------------------
# Portal blueprint — apex domain only.
# ---------------------------------------------------------------------------
portal_bp = Blueprint("portal", __name__)


@portal_bp.route("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    systems = [
        {"key": "fox",   "name": "Fox ESS H3-15.0-SMART", "accent": "#f97316",
         "blurb": "Hybrid inverter · 24-hour efficiency log + CSV export"},
        {"key": "solis", "name": "Solis S6-EH3P 50 kW",   "accent": "#eab308",
         "blurb": "Hybrid inverter · live dashboard via microgrid"},
    ]
    for s in systems:
        s["url"] = f"https://{s['key']}.smartenergylab.software/"
    return render_template("portal.html", systems=systems)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Config — explicit path via PORTAL_CONFIG (the systemd unit sets
    # this), otherwise local config.py for dev.
    cfg_path = os.environ.get("PORTAL_CONFIG")
    if cfg_path:
        app.config.from_pyfile(cfg_path)
    else:
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
        app.config.from_pyfile(local)

    # Trust the X-Forwarded-* headers Apache adds. x_for/x_proto/x_host=1
    # = exactly one trusted proxy (Apache on the same host).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)

    # ----- host-aware dispatch ------------------------------------------
    @app.before_request
    def route_by_host():
        # Public tools subdomain — served locally from static/, no auth.
        # Checked before the proxy branch so a tools page can never be
        # sent through auth_gate().
        if is_tools_host(request.host):
            return serve_tool(request.path.lstrip("/"))
        # Proxied subdomain? enforce login, then forward to upstream.
        if is_proxied_host(request.host):
            gate = auth_gate()
            if gate is not None:
                return gate
            path = request.path.lstrip("/")
            return forward(path)
        # Apex domain — fall through to the normal view dispatcher.
        return None

    # Initialise schema on first start (idempotent).
    with app.app_context():
        db.create_all()

    log.info("Portal: app created. Upstreams = %s", app.config.get("UPSTREAMS"))
    return app


# Module-level app so `gunicorn app:app` works.
app = create_app()


if __name__ == "__main__":
    # Local dev only. In prod, gunicorn serves the module-level `app`.
    app.run(host="127.0.0.1", port=8000, debug=True)
