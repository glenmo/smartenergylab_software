# Smart Energy Lab portal

Public-facing portal at **https://smartenergylab.software/** that
fronts the residential-LAN inverter dashboards behind a single Flask
auth gateway. Each monitored system is mirrored at its own subdomain:

| URL | What it mirrors | Backend host |
|---|---|---|
| `smartenergylab.software` | Login + system menu + forgot/reset | local Flask |
| `fox.smartenergylab.software` | [fox_remote_monitoring](https://github.com/glenmo/fox_remote_monitoring) | desky.local (WireGuard 10.99.0.2) |
| `solis.smartenergylab.software` | microgrid_remote_monitor | rubberduck.local (WireGuard 10.99.0.3) |

```
                              Internet
                                 │
                                 ▼
                ┌──────────────────────────────┐
                │  burgan.arachnoid.net.au     │
                │  ┌─────────────────────────┐ │
                │  │ Apache :443             │ │
                │  │   smartenergylab.*  ─┐  │ │
                │  └────────────────────│──┘ │
                │  ┌────────────────────▼──┐ │
                │  │ Flask (gunicorn) :8000│ │
                │  │  - auth (login etc)   │ │
                │  │  - portal page        │ │
                │  │  - reverse proxy ─┐   │ │
                │  └───────────────────│───┘ │
                │  WireGuard wg0 ─────│─────│
                └──────────│──────────│─────┘
                           │          │
              10.99.0.0/24 ▼          ▼ 10.99.0.0/24
              ┌──────────────┐  ┌──────────────────┐
              │ desky        │  │ rubberduck       │
              │ 10.99.0.2    │  │ 10.99.0.3        │
              │ fox-monitor  │  │ microgrid (solis)│
              └──────────────┘  └──────────────────┘
```

## What gets deployed where

| Component | Host | Source |
|---|---|---|
| Flask portal app + gunicorn + systemd unit | burgan | this repo, rsync'd to `/opt/burgan-portal` |
| Apache vhost | burgan | `apache/smartenergylab.conf` |
| Let's Encrypt wildcard cert | burgan | `certbot` |
| WireGuard `wg0` interface | burgan, desky, rubberduck | `wireguard/*.conf.example` (one per host) |

The Fox / Solis dashboards on desky / rubberduck need a tiny config
tweak (bind to all interfaces, not just 127.0.0.1) so the WireGuard
tunnel can reach them — see [Step 4](#4-let-burgan-reach-the-inverter-dashboards-over-the-tunnel).

---

## 1. Burgan: install OS dependencies + create the service account

```bash
ssh you@burgan.arachnoid.net.au

sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
                    apache2 wireguard wireguard-tools \
                    certbot python3-certbot-apache

sudo a2enmod ssl proxy proxy_http headers rewrite
sudo useradd --system --create-home --home-dir /var/lib/burgan-portal \
             --shell /usr/sbin/nologin burgan-portal
sudo mkdir -p /etc/burgan-portal /opt/burgan-portal /var/lib/burgan-portal
sudo chown burgan-portal:burgan-portal /var/lib/burgan-portal
```

Check: `id burgan-portal` should show the account.

---

## 2. Burgan: deploy the portal code

From your **local** machine (where you cloned this repo):

```bash
rsync -avz --delete \
    --exclude '.git' --exclude '__pycache__' --exclude 'venv' \
    --exclude 'config.py' --exclude '*.sqlite' \
    ./ you@burgan.arachnoid.net.au:/tmp/burgan-portal/
```

Then on **burgan**:

```bash
sudo rsync -av --chown=burgan-portal:burgan-portal \
    /tmp/burgan-portal/ /opt/burgan-portal/
cd /opt/burgan-portal
sudo -u burgan-portal python3 -m venv venv
sudo -u burgan-portal venv/bin/pip install -r requirements.txt
```

Check: `/opt/burgan-portal/venv/bin/python -c 'import flask, flask_login, flask_wtf'` exits clean.

---

## 3. Burgan: write the real config

```bash
sudo cp /opt/burgan-portal/config.example.py /etc/burgan-portal/config.py
sudo chown root:burgan-portal /etc/burgan-portal/config.py
sudo chmod 640 /etc/burgan-portal/config.py
sudo nano /etc/burgan-portal/config.py
```

Fill in:

- **`SECRET_KEY`** — `python3 -c 'import secrets; print(secrets.token_hex(32))'`
- **`SMTP_USER`** — the Gmail address that owns the app password
- **`SMTP_PASSWORD`** — the 16-char Google app password (no spaces)
- **`MAIL_FROM`** — e.g. `Smart Energy Lab <you@example.com>`

Leave the rest as defaults unless you need to change ports / paths.

Check: `sudo -u burgan-portal /opt/burgan-portal/venv/bin/python -c '
import os; os.environ["PORTAL_CONFIG"]="/etc/burgan-portal/config.py"
from app import app; print(app.config["UPSTREAMS"])
'` prints the upstream map.

---

## 4. Let burgan reach the inverter dashboards over the tunnel

The two upstreams have different deployment shapes and need different
treatment.

### 4a. desky — Apache vhost on `:80` in front of Flask

`fox_remote_monitoring` puts Apache in front of Flask. Burgan hits
`http://10.99.0.2/` (port 80, Apache), Apache reverse-proxies to
Flask on `127.0.0.1:5000`. Apache already binds to `0.0.0.0:80`, so
it's already listening on the WireGuard interface — but vhost matching
needs the WireGuard IP in `ServerAlias`, otherwise it falls back to
"first vhost wins". Re-run the fox installer with `EXTRA_SERVER_ALIAS`
to rewrite the vhost cleanly:

```bash
ssh you@desky
cd /home/glen/fox_remote_monitoring
EXTRA_SERVER_ALIAS="10.99.0.2" bash install.sh
```

Or do the same change by hand if you'd rather not re-run the script:

```bash
sudo sed -i 's|ServerAlias \(.*\)|ServerAlias \1 10.99.0.2|' \
    /etc/apache2/sites-available/fox-monitor.conf
sudo systemctl reload apache2
```

Flask itself stays bound to `127.0.0.1` — no extra LAN exposure.

### 4b. rubberduck — Flask bound directly to the LAN, no Apache vhost

`microgrid_remote_monitor` doesn't run behind Apache. Flask is
exposed directly on port 5000, and burgan's `UPSTREAMS["solis"]`
points at `http://10.99.0.3:5000` accordingly. The only requirement
is that microgrid's Flask is bound to **all interfaces** so the
WireGuard tunnel actually reaches it.

Check what microgrid is bound to:

```bash
ssh you@rubberduck
sudo ss -tlnp | grep ':5000'
```

- `0.0.0.0:5000` → fine, it'll answer on the wg0 interface automatically.
- `127.0.0.1:5000` → won't answer over the tunnel. Edit microgrid's
  systemd unit (likely `/etc/systemd/system/microgrid-monitor.service`)
  and change `--host 127.0.0.1` to `--host 0.0.0.0`, then
  `sudo systemctl daemon-reload && sudo systemctl restart microgrid-monitor`.

### Verification (from burgan, after WireGuard is up — step 5)

```bash
curl -sI http://10.99.0.2/             # 200 OK from desky's Apache
curl -sI http://10.99.0.3:5000/        # 200 OK from microgrid Flask
```

If either curl hangs or times out, the tunnel isn't carrying packets
to that host yet — work through step 5 again.

---

## 5. WireGuard — keys + interface on all three hosts

### 5a. Get the templates onto each host

Step 2 only synced this repo to burgan. The wireguard templates need
to land on desky and rubberduck too — the cleanest way is to git-clone
the repo on each, so future updates are a `git pull` away:

```bash
ssh you@desky      'git clone https://github.com/glenmo/smartenergylab_software.git ~/smartenergylab_software'
ssh you@rubberduck 'git clone https://github.com/glenmo/smartenergylab_software.git ~/smartenergylab_software'
```

(If you'd rather not clone the repo on the LAN hosts, scp the one
template each needs from your local checkout — `desky.wg0.conf.example`
to desky, `rubberduck.wg0.conf.example` to rubberduck — and skip the
git step.)

### 5b. Install WireGuard + generate keys on each host

```bash
sudo apt install -y wireguard wireguard-tools     # all three hosts
cd ~ && umask 077
wg genkey | tee $(hostname).key | wg pubkey > $(hostname).pub
cat $(hostname).pub      # this is the public key — share it
```

You now have:

- on burgan:     `burgan.key`,     `burgan.pub`
- on desky:      `desky.key`,      `desky.pub`
- on rubberduck: `rubberduck.key`, `rubberduck.pub`

You'll need to copy each `.pub` over to burgan (by scp or paste) so
burgan's `wg0.conf` can register the peers. The `.key` files stay
where they were generated — never copy them anywhere.

### 5c. Burgan side (the server)

```bash
sudo cp /opt/burgan-portal/wireguard/burgan.wg0.conf.example /etc/wireguard/wg0.conf
sudo nano /etc/wireguard/wg0.conf
# replace REPLACE_WITH_burgan.key_CONTENTS with the body of ~/burgan.key
# replace each REPLACE_WITH_*.pub_CONTENTS with the matching .pub from
# the other hosts
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

### 5d. desky + rubberduck side (the clients)

On each LAN host:

```bash
# Pick the right template for the host you're on:
sudo cp ~/smartenergylab_software/wireguard/$(hostname).wg0.conf.example \
        /etc/wireguard/wg0.conf
sudo nano /etc/wireguard/wg0.conf
# replace REPLACE_WITH_<hostname>.key_CONTENTS with body of ~/<hostname>.key
# replace REPLACE_WITH_burgan.pub_CONTENTS with body of burgan's .pub
#   (scp it across from burgan or paste it)
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

Check (run on burgan):

```bash
sudo wg show          # both peers should show 'latest handshake' within 30 s
curl -sI http://10.99.0.2/   # should return 200 OK (Apache on desky)
curl -sI http://10.99.0.3/   # should return 200 OK (Apache on rubberduck)
```

If `curl` hangs, the tunnel isn't carrying packets yet — recheck the
public keys (a one-character typo is the usual culprit).

**Firewall on burgan** — open UDP 51820 inbound for WireGuard:

```bash
sudo ufw allow 51820/udp comment "WireGuard"
# (or your equivalent firewall rule)
```

---

## 6. Burgan: systemd unit + smoke test

```bash
sudo cp /opt/burgan-portal/systemd/burgan-portal.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now burgan-portal
sudo systemctl status burgan-portal --no-pager
```

Check: `curl -sI http://127.0.0.1:8000/login` returns `200 OK`.

---

## 7. Burgan: Apache vhost + Let's Encrypt SAN cert

A single SAN cert covering the three exact names (apex + fox + solis)
is cleaner than a wildcard: HTTP-01 issuance + renewal need only that
Apache serves `/.well-known/acme-challenge/` on port 80, with no DNS
gymnastics. The bundled vhost has the renewal-friendly :80 redirect
already wired up.

Install the vhost file — but **don't enable it yet**, because the
SSL cert it points at doesn't exist on burgan yet:

```bash
sudo cp /opt/burgan-portal/apache/smartenergylab.conf \
        /etc/apache2/sites-available/
```

Make sure something is serving port 80 in the meantime so HTTP-01
can land. Debian's default site is fine:

```bash
sudo a2ensite 000-default 2>/dev/null || true
sudo systemctl restart apache2
```

Issue the cert (one cert, three SANs, HTTP-01 via the default
webroot):

```bash
sudo certbot certonly --webroot -w /var/www/html \
    -d smartenergylab.software \
    -d fox.smartenergylab.software \
    -d solis.smartenergylab.software \
    --agree-tos --no-eff-email -m you@example.com
```

Verify the cert landed, then enable our vhost:

```bash
sudo ls /etc/letsencrypt/live/smartenergylab.software/
sudo a2ensite smartenergylab.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

Renewal is automatic: certbot installs a systemd timer that runs
`certbot renew` twice a day. The :80 vhost blocks in
`smartenergylab.conf` carve out `/.well-known/acme-challenge/` so
HTTP-01 keeps working forever without touching anything by hand.

Check:

```bash
curl -sI https://smartenergylab.software/login            # 200
curl -sI https://fox.smartenergylab.software/             # 302 → /login (proxied via burgan-portal)
curl -sI https://solis.smartenergylab.software/           # 302 → /login
```

Auto-renewal: `certbot renew` re-uses the DNS-01 method recorded in
`/etc/letsencrypt/renewal/smartenergylab.software.conf`. If you're
using a non-API DNS provider, schedule a calendar reminder for the
90-day mark.

---

## 8. Burgan: create the first user

```bash
cd /opt/burgan-portal
sudo -u burgan-portal env PORTAL_CONFIG=/etc/burgan-portal/config.py \
    venv/bin/python manage.py create-user you@example.com
# Prompts for a password (≥ 12 chars)
```

Open https://smartenergylab.software/ in a browser. You should see the
login page → sign in → portal menu with cards for **Fox ESS H3** and
**Solis S6**. Clicking a card takes you to the subdomain dashboard.
Forgot-password works against the email you just registered.

---

## CLI cheatsheet (run on burgan)

```bash
# Where venv lives + how to run manage.py
cd /opt/burgan-portal
sudo -u burgan-portal env PORTAL_CONFIG=/etc/burgan-portal/config.py \
    venv/bin/python manage.py <cmd>

# User management
manage.py create-user  <email>
manage.py set-password <email>          # admin password reset
manage.py list-users
manage.py delete-user  <email>

# Login activity
manage.py recent-logins                          # last 20 successful logins
manage.py recent-logins --limit 50 --email X     # filter to one user
manage.py failed-logins                          # last 30 failures + summary by email/IP
manage.py failed-logins --since-hours 168 --limit 200    # week-long view
```

Login activity (both successes and failures) is persisted in the
`login_events` table — see [Login audit + alerts](#login-audit--alerts)
below.

```bash
# Logs
sudo journalctl -u burgan-portal -f
sudo journalctl -u wg-quick@wg0 -f
sudo tail -f /var/log/apache2/smartenergylab-*.log
```

```bash
# Re-deploy after a code change
rsync -avz --exclude '.git' --exclude 'venv' --exclude 'config.py' \
    ./ you@burgan:/tmp/burgan-portal/
ssh you@burgan 'sudo rsync -av --chown=burgan-portal:burgan-portal \
    /tmp/burgan-portal/ /opt/burgan-portal/ && \
    sudo -u burgan-portal /opt/burgan-portal/venv/bin/pip install -r /opt/burgan-portal/requirements.txt && \
    sudo systemctl restart burgan-portal'
```

---

## Adding a new monitored system

Two edits, in this order:

1. **`/etc/burgan-portal/config.py`** on burgan — add an entry to
   `UPSTREAMS`:

   ```python
   UPSTREAMS = {
       "fox":   "http://10.99.0.2",
       "solis": "http://10.99.0.3",
       "new-system": "http://10.99.0.4",
   }
   ```

2. **`app.py`** `index()` view — add a new dict to the `systems` list
   so the portal menu has a card for it. (Could be moved to config
   later if you find yourself adding lots.)

Plus the DNS + WireGuard plumbing for the new peer.

---

## Login audit + alerts

Every login POST — success or failure — is recorded in the
`login_events` table with the typed email, source IP, user-agent,
timestamp and a `success` flag. Two CLI commands surface that data
quickly without running ad-hoc SQL:

- `manage.py recent-logins` — successful logins, newest first. Add
  `--email you@example.com` to filter to one user.
- `manage.py failed-logins` — failures from the last `--since-hours`
  (default 24) plus a top-10 summary by email and by source IP.

**Burst alerting.** When `LOGIN_ALERT_TO` is set in
`/etc/burgan-portal/config.py`, the portal emails that address as
soon as a single email *or* a single source IP racks up
`LOGIN_ALERT_THRESHOLD` failures inside `LOGIN_ALERT_WINDOW`. A
`LOGIN_ALERT_COOLDOWN` (default 1 h) prevents one sustained attack
from generating an inbox flood — at most one email per target per
cooldown window. Disable alerts by setting `LOGIN_ALERT_TO = None`;
the table still fills, you just won't get notified.

Alert sentinels are stored as ordinary `login_events` rows with
`email_attempted` prefixed `_ALERT:` — they're filtered out of CLI
listings.

## Security notes

- All cookies are `Secure`, `HttpOnly`, `SameSite=Lax`, scoped to
  `.smartenergylab.software` so a single login covers every subdomain.
- Passwords: pbkdf2-sha256, 600k iterations (werkzeug default).
- Login: rate-limited 5 / 15 min per IP (POST only).
- Forgot-password: rate-limited 3 / hour per IP; same response whether
  the email is registered or not, so the form doesn't leak account
  existence.
- Reset tokens: signed via itsdangerous (1 hour TTL) **and** persisted
  in SQLite with a `used_at` column, so a token can only be used once.
- Proxied responses have `Server` and `X-Powered-By` stripped before
  reaching the public internet.
- WireGuard puts the inverter dashboards on a private subnet that
  isn't reachable from the public internet — burgan is the only host
  that can talk to them. The fact that Flask listens on `0.0.0.0` on
  desky/rubberduck only widens *LAN* exposure, not internet exposure.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `502` from a subdomain | WireGuard not up, or upstream Flask down. `sudo wg show` + `curl http://10.13.13.7/` from burgan. |
| Login form posts but you stay on the login page | CSRF token mismatch — usually means SECRET_KEY was regenerated. Clear cookies and try again. |
| Password reset emails not arriving | Check Gmail's "less secure apps" + that you used an **app password**, not your account password. Gmail will reject non-app-password SMTP from a server. |
| Browser stuck at `https://smartenergylab.software/login?next=https://fox...` after clicking a card | You're not logged in. Sign in first. |
| `sudo wg show` shows no latest handshake on a peer | UDP ListenPort isn't reaching burgan, or keys don't match. See "Lessons from first deploy" below. |

## Lessons from first deploy (read this before re-doing on a new host)

Things that cost us time on first deployment and would have been easy
to avoid if we'd known:

### The UFW rule must match burgan's WireGuard ListenPort exactly

Burgan's `wg0.conf` had `ListenPort = 41820` but UFW had `51820/udp`
allowed. Every WireGuard handshake from desky and rubberduck was
dropped at the firewall and we spent an hour staring at "0 B
received" before noticing. Check both before debugging anything else:

```bash
sudo grep ListenPort /etc/wireguard/wg0.conf
sudo ufw status | grep -E "udp"
```

If they disagree, fix the UFW rule (don't change the WG port — the
port is in every existing peer's config):

```bash
sudo ufw allow 41820/udp comment "WireGuard"
sudo ufw delete allow 51820/udp     # remove the stale rule
```

### Recover an existing wg0.conf before overwriting it

If burgan already has a `wg0` interface up with other peers (personal
VPN, etc.), the on-disk `/etc/wireguard/wg0.conf` may be stale or
gone, but the kernel still has the real config loaded. Dump it first
before doing anything:

```bash
sudo wg showconf wg0 | sudo tee /etc/wireguard/wg0.conf.recovered
```

That dump includes the live `PrivateKey` (which is otherwise
irrecoverable) and every existing peer. Then APPEND the new
`[Peer]` blocks to that file rather than starting fresh. Live-load
without dropping the interface — so existing connections stay up:

```bash
sudo bash -c 'wg-quick strip wg0 > /tmp/wg0.stripped \
    && wg syncconf wg0 /tmp/wg0.stripped \
    && rm /tmp/wg0.stripped'
```

### Use install-client.sh on each LAN host instead of pasting templates

Some terminals' bracketed-paste / auto-indent behaviour silently
breaks multi-line heredocs and long `printf`s — long lines get split
mid-value, heredoc terminators get indented and never match. We hit
this on both desky and rubberduck and ended up with broken
`/etc/wireguard/wg0.conf` files that *looked* fine on read but wouldn't
parse.

The `wireguard/install-client.sh` script in this repo sidesteps the
problem by writing the file inside a single sudo invocation, not from
pasted heredocs:

```bash
# On each LAN host, after generating ~/<hostname>.key:
curl -sL https://raw.githubusercontent.com/glenmo/smartenergylab_software/main/wireguard/install-client.sh \
    | sudo bash
```

### Process substitution doesn't work inside `sudo`

`sudo wg syncconf wg0 <(sudo wg-quick strip wg0)` fails with `fopen:
No such file or directory`. The outer sudo can't open the
process-substitution FD created by the parent shell. Use a tempfile
instead:

```bash
sudo bash -c 'wg-quick strip wg0 > /tmp/wg0.stripped \
    && wg syncconf wg0 /tmp/wg0.stripped \
    && rm /tmp/wg0.stripped'
```

### HTTP-01, not DNS-01, for the cert

Wildcard certs via manual DNS-01 require two simultaneous TXT
records at `_acme-challenge.<domain>` — easy to fumble in a DNS UI,
and renewal needs the same step every 90 days. The SAN cert path in
README step 7 (HTTP-01 via `--webroot`) is simpler and renewal is
automatic, as long as the `:80` vhost has the `/.well-known/
acme-challenge/` carve-out (it does, in `apache/smartenergylab.conf`).
