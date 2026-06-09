"""
Subdomain-based reverse proxy.

Apache forwards every request to this Flask app (via gunicorn on
127.0.0.1:8000) with the original Host: header intact. We read
request.host, look up the upstream in config.UPSTREAMS, and stream the
response back. Streaming matters: the Fox dashboard exposes a CSV log
export that can be tens of MB, and the live dashboards do long polls.

Login is enforced before any request reaches an upstream — see
auth_gate() registered in app.py as a before_request.
"""

import logging
from urllib.parse import urlsplit

import requests
from flask import Response, current_app, redirect, request, stream_with_context
from flask_login import current_user

log = logging.getLogger("portal.proxy")

# Hop-by-hop headers must not be forwarded between proxies (RFC 7230 §6.1).
# Content-Encoding is stripped because requests has already decoded the
# body; passing through the original encoding header would tell the
# browser to decode an already-decoded stream.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length",
})

def upstream_for_host(host: str) -> str | None:
    """Return the upstream base URL for a Host header, or None for the portal."""
    if not host:
        return None
    sub = host.split(":")[0].split(".")[0]
    return current_app.config["UPSTREAMS"].get(sub)


def is_proxied_host(host: str) -> bool:
    return upstream_for_host(host) is not None


def forward(path: str):
    """Proxy the current request to the mapped upstream."""
    upstream = upstream_for_host(request.host)
    if upstream is None:
        # No upstream for this host — caller (before_request hook) should
        # only invoke this for proxied hosts. Defensive fallback.
        return Response("Not found", status=404)

    # Build upstream URL preserving query string.
    target = f"{upstream.rstrip('/')}/{path}"
    if request.query_string:
        target += "?" + request.query_string.decode("latin-1")

    # Headers to forward — strip Host (set by requests) and hop-by-hop.
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != "host"
    }
    # Make the upstream aware it's behind a proxy.
    fwd_headers["X-Forwarded-For"]   = request.headers.get(
        "X-Forwarded-For", request.remote_addr or ""
    )
    fwd_headers["X-Forwarded-Proto"] = request.headers.get("X-Forwarded-Proto", "https")
    fwd_headers["X-Forwarded-Host"]  = request.host

    try:
        upstream_resp = requests.request(
            method=request.method,
            url=target,
            headers=fwd_headers,
            data=request.get_data() if request.method in ("POST", "PUT", "PATCH") else None,
            stream=True,
            allow_redirects=False,
            timeout=(5, 60),    # 5 s connect, 60 s read
        )
    except requests.exceptions.RequestException as e:
        log.warning(f"proxy: upstream {target!r} failed: {e}")
        return Response(
            "Upstream unavailable. Please try again in a moment.",
            status=502, mimetype="text/plain",
        )

    # Strip hop-by-hop and any Server / X-Powered-By that would leak the
    # upstream stack to the public internet.
    out_headers = []
    for k, v in upstream_resp.headers.items():
        kl = k.lower()
        if kl in HOP_BY_HOP:
            continue
        if kl in ("server", "x-powered-by"):
            continue
        # If the upstream issued a redirect to its own host, rewrite the
        # Location to the public subdomain so the user's browser stays
        # on smartenergylab.software instead of being sent to 10.99.0.x.
        if kl == "location":
            parsed = urlsplit(v)
            if parsed.netloc and parsed.netloc == urlsplit(upstream).netloc:
                v = parsed._replace(scheme="https", netloc=request.host).geturl()
        out_headers.append((k, v))

    return Response(
        stream_with_context(upstream_resp.iter_content(chunk_size=8192)),
        status=upstream_resp.status_code,
        headers=out_headers,
    )


# ---------------------------------------------------------------------------
# Auth gate — runs as a before_request on the proxy blueprint.
# Allows unauthenticated traffic through ONLY for the portal subdomain
# (so the login page works); every other host requires login.
# ---------------------------------------------------------------------------
def auth_gate():
    """Reject unauthenticated requests on any proxied subdomain."""
    if not is_proxied_host(request.host):
        return None
    if current_user.is_authenticated:
        return None
    # Send the browser to the portal's login page, with a `next` that
    # points back to the original deep link.
    target = (
        current_app.config["PORTAL_BASE_URL"]
        + "/login?next="
        + request.url
    )
    return redirect(target)
