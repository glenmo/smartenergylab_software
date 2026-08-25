# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The **Smart Energy Lab portal**: a single Flask app that acts as an authenticating
reverse-proxy gateway in front of several remote solar-inverter dashboards. One login
(`smartenergylab.software`) gates access to per-inverter subdomains
(`fox.`, `solis.`), each of which is transparently mirrored from a dashboard running on
a different physical host reachable only over a WireGuard tunnel.

The whole thing runs on one host called **burgan** (gunicorn behind Apache). The
inverter dashboards it proxies run on other hosts (**desky** runs fox-monitor,
**rubberduck** runs the Solis microgrid monitor); this repo does *not* contain their
code — only the config needed to reach them.

## The one architectural idea to hold in your head

**Everything is routed by the `Host` header, not by URL path.** Apache preserves the
original `Host` (`ProxyPreserveHost On`) and forwards *every* request for all three
vhosts to the same gunicorn on `127.0.0.1:8000`. Flask then decides what to do:

- `app.py`'s `before_request` hook (`route_by_host`) runs first for every request.
  If `is_tools_host(request.host)` (the public `tools.` subdomain), it serves a
  static page via `toolsite.serve_tool()` with **no auth** — this branch is first
  precisely so a public page can never fall into the auth gate below it.
- Else if `is_proxied_host(request.host)` (i.e. the subdomain is in `config.UPSTREAMS`),
  it enforces login via `auth_gate()` then hands off to `proxy.forward()` — the
  proxied subdomains have **no Flask URL routes of their own**.
- Otherwise (the apex domain) it falls through to the normal blueprint views
  (`portal_bp`, `auth_bp`).

So `proxy.py` and `toolsite.py` are both reached through the request hook, not through
registered routes. Adding a subdomain means adding it to `UPSTREAMS` (authenticated
mirror) or to `TOOLS` in `toolsite.py` (public static page) — not adding a route. The
two are mutually exclusive: never put a public tool host in `UPSTREAMS`, because
everything in `UPSTREAMS` is gated by `auth_gate()`.

Either way, a new subdomain also needs an Apache vhost **and a certbot reissue** — the
cert is a SAN cert naming each host explicitly, while DNS is a wildcard, so a new name
resolves long before it has a valid certificate.

Session cookies are scoped to `.smartenergylab.software` (leading dot, set in config)
so a single login is shared across the apex and every subdomain.

## Layout

- `app.py` — entry point, app factory `create_app()`, the host-routing hook, and the
  apex `/` portal view. Module-level `app` exists so `gunicorn app:app` works.
- `proxy.py` — the reverse proxy: `forward()` streams upstream responses back, rewrites
  `Location` redirects from the internal WireGuard IP to the public subdomain, strips
  hop-by-hop / stack-leaking headers, and buffers `text/html` responses to inject a
  fixed "← Portal" back-link before `</body>` (CSV/JSON stay streamed).
- `toolsite.py` — the public `tools.` subdomain: an allow-list (`TOOLS`) mapping URL
  paths to files in `static/`, served with no login. Named `toolsite.py`, not
  `tools.py`, to avoid colliding with the `tools/` source directory.
- `tools/pv-string-calculator/` — source for the public PV string calculator
  (`engine.js` + `ui.html` → `build.py` → `static/string-calculator.html`, with
  `node test.js` covering the maths). Never hand-edit the built file in `static/`.
- `auth.py` — login / logout / forgot / reset routes, plus login-event recording and
  the failed-login burst-alert logic (`_maybe_alert_on_burst`).
- `models.py` — `User`, `PasswordResetToken`, `LoginEvent` (SQLAlchemy 2.0 typed models).
- `manage.py` — the admin CLI (see below). There is **no self-serve registration**; the
  first user is created here.
- `extensions.py` — the shared extension singletons (`db`, `login_manager`, `csrf`,
  `limiter`) that everything imports to avoid import cycles.
- `mail.py` — ~30-line SMTP helper (Gmail STARTTLS) for reset + alert emails.
- `config.example.py` — the config template. The real `config.py` is git-ignored and
  lives at `/etc/burgan-portal/config.py` in prod.
- `apache/`, `systemd/`, `wireguard/` — deployment artifacts (templates, not live copies).
- `README.md` — the authoritative step-by-step deploy/operator manual. Consult it for
  anything infrastructure-related rather than duplicating its steps here.

## Deploy model

Production runs from **`/opt/burgan-portal` on burgan**, not from this working tree.
A code change has no effect until it is rsync'd across and the service restarted. The
full one-liner is in the README under "Re-deploy after a code change"; in essence:
rsync the tree to burgan, `pip install -r requirements.txt`, then
`sudo systemctl restart burgan-portal`.

- Config is loaded from the path in the `PORTAL_CONFIG` env var (the systemd unit sets
  it to `/etc/burgan-portal/config.py`); with no env var it falls back to a local
  `config.py` for dev.
- The SQLite DB is the only writable path (`ReadWritePaths=/var/lib/burgan-portal` in
  the hardened unit). `db.create_all()` runs on startup, so the schema is
  self-bootstrapping — there are no migrations.

## Commands

```bash
# Local dev (foreground on 127.0.0.1:8000, debug on). Needs a local config.py first —
# copy config.example.py, set SESSION_COOKIE_SECURE=False for plain HTTP.
python3 app.py

# Admin CLI — always with an app context + config. In prod on burgan:
cd /opt/burgan-portal
sudo -u burgan-portal env PORTAL_CONFIG=/etc/burgan-portal/config.py \
    venv/bin/python manage.py <cmd>
#   create-user <email> / set-password <email> / list-users / delete-user <email>
#   recent-logins [--limit N] [--email X]
#   failed-logins [--limit N] [--since-hours H]     # + top-N summary by email & IP

# Logs
sudo journalctl -u burgan-portal -f
sudo tail -f /var/log/apache2/smartenergylab-*.log
```

There is no test suite, linter, or build step.

## Things that will bite you

- **Upstream IPs are WireGuard tunnel addresses** (`10.13.13.0/24`: burgan=.1,
  desky=.7, rubberduck=.8). The portal can only reach a dashboard when `wg0` is up on
  both ends. `desky`'s fox dashboard sits behind its own Apache (burgan hits `:80`);
  `rubberduck`'s Solis dashboard binds Flask directly (burgan hits `:5000`). Note the
  "Adding a new monitored system" README example uses `10.99.0.x` placeholders — the
  deployed subnet is `10.13.13.0/24` per `config.example.py`.
- **`ProxyFix(x_for=1, x_proto=1, x_host=1)`** in `create_app()` trusts exactly one
  proxy (Apache on the same box). It is correct only because Apache is the sole hop; do
  not bump these numbers without another real trusted proxy in front.
- **Login-event burst alerts have no separate table.** `_maybe_alert_on_burst` records
  "an alert already fired" as a sentinel row in `login_events` with `email_attempted`
  prefixed `_ALERT:`. Every query that reads real events filters these out with
  `NOT LIKE '_ALERT:%'` — preserve that filter in any new query over `login_events`.
- **Password-reset tokens are double-guarded:** the token itself is an `itsdangerous`
  signed+timed string (stateless expiry), *and* a `password_reset_tokens` row enforces
  single use via `used_at`. Both must pass in `auth.reset`.
- **Never commit `config.py`, `*.sqlite`, or `wireguard/*.key|*.pub`** — all git-ignored;
  only the `*.example` templates are tracked.
