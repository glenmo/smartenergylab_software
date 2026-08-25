"""
Configuration template for smartenergylab_software portal.

Deployment: copy to /etc/burgan-portal/config.py on burgan and fill in
the real values. The systemd unit reads config via PORTAL_CONFIG=
pointing at this file.

NEVER commit a real config.py — the .gitignore excludes it.
"""

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
# Long random string — generate with:  python -c 'import secrets; print(secrets.token_hex(32))'
SECRET_KEY = "REPLACE_WITH_64_CHAR_HEX"

# Domain the session cookie is scoped to — must start with a dot so all
# subdomains (fox./solis./portal) share the session.
SESSION_COOKIE_DOMAIN = ".smartenergylab.software"
SESSION_COOKIE_SECURE = True        # set False only for local HTTP testing
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14   # 14 days

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URI = "sqlite:////var/lib/burgan-portal/portal.sqlite"

# ---------------------------------------------------------------------------
# Subdomain → upstream map
# Apache forwards every request to Flask with the original Host: header
# intact. Flask reads request.host and looks up the upstream URL here.
#
# Upstream IPs are WireGuard tunnel addresses on the wg0 interface.
# The deployed subnet is 10.13.13.0/24 (matches the long-standing
# burgan setup; pick a different /24 here only if you're starting
# fresh and have no other wg peers to coexist with):
#   10.13.13.1 = burgan       (server)
#   10.13.13.7 = desky        (fox-monitor)
#   10.13.13.8 = rubberduck   (microgrid_remote_monitor / solis)
#
# Keys are bare hostnames; the public DNS pattern is
# <key>.smartenergylab.software.
# ---------------------------------------------------------------------------
# How to read this:
#   - desky's fox_remote_monitoring sits behind Apache: burgan hits :80
#     and Apache reverse-proxies to Flask on 127.0.0.1:5000. Apache's
#     vhost needs to recognise the WireGuard IP as a ServerAlias (set
#     via EXTRA_SERVER_ALIAS on the fox installer — see README step 4).
#   - rubberduck's microgrid_remote_monitor binds Flask straight to the
#     LAN interface — no Apache reverse proxy. Burgan hits :5000
#     directly. Verify Flask is bound to 0.0.0.0 (or to the wg0 IP) on
#     rubberduck, otherwise the tunnel won't reach it.
UPSTREAMS = {
    "fox":   "http://10.13.13.7",        # → Apache on desky → fox-monitor:5000
    "solis": "http://10.13.13.8:5000",   # → Flask directly on rubberduck
}

# ---------------------------------------------------------------------------
# Public tools subdomain
# ---------------------------------------------------------------------------
# <TOOLS_SUBDOMAIN>.smartenergylab.software serves the free static tools
# (currently the PV string calculator) straight out of static/, with NO
# login — see toolsite.py. Deliberately NOT an entry in UPSTREAMS: those
# are gated by auth_gate(), and these pages are public by design.
#
# The default is "tools", so this key only needs setting if you want a
# different label. Whatever you pick needs a matching Apache vhost.
TOOLS_SUBDOMAIN = "tools"

# ---------------------------------------------------------------------------
# SMTP — Gmail with an app password
# Create at: https://myaccount.google.com/apppasswords
# ---------------------------------------------------------------------------
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587                       # STARTTLS
SMTP_USER = "you@example.com"         # the Gmail address that owns the app password
SMTP_PASSWORD = "REPLACE_APP_PW"      # 16-char Google app password (NOT your normal pw)
MAIL_FROM = "Smart Energy Lab <you@example.com>"

# Base URL used in password-reset emails (must be HTTPS in prod)
PORTAL_BASE_URL = "https://smartenergylab.software"

# ---------------------------------------------------------------------------
# Rate limits (Flask-Limiter syntax)
# ---------------------------------------------------------------------------
RATE_LIMIT_LOGIN  = "5 per 15 minutes"
RATE_LIMIT_FORGOT = "3 per hour"

# How long a password-reset token is valid for
RESET_TOKEN_TTL_SECONDS = 60 * 60     # 1 hour

# ---------------------------------------------------------------------------
# Suspicious-login alerts
# ---------------------------------------------------------------------------
# Email to notify when a burst of failed logins is detected. Set to
# None to disable alerting (failures still get persisted in login_events).
LOGIN_ALERT_TO = "you@example.com"

# Burst trigger: N or more failures for the same target (email OR ip)
# within the past WINDOW seconds.
LOGIN_ALERT_THRESHOLD = 5
LOGIN_ALERT_WINDOW    = 60 * 15        # 15 minutes

# Cooldown so a sustained attack doesn't generate one email per
# attempt. Once an alert fires for a given email/IP, no further alert
# for the same target until COOLDOWN seconds have elapsed.
LOGIN_ALERT_COOLDOWN  = 60 * 60        # 1 hour
