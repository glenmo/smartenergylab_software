"""
tools.smartenergylab.software — public, unauthenticated static tools.

Routed by the Host header exactly like the proxied inverter subdomains,
but with two deliberate differences:

  - the content is served locally out of static/, not forwarded over
    WireGuard to another host, so there is no upstream to be down;
  - there is NO auth gate. These pages are public by design (the PV
    string calculator is a free tool handed out to installers), so this
    branch must run *before* anything that would redirect to login.

That last point is why this can't just be a portal_bp route: the apex
`/` view requires a login, and a blueprint route would inherit it.
Like proxy.forward(), serve_tool() is reached through app.py's
route_by_host hook, not through a registered URL rule.

Named toolsite.py rather than tools.py so it can't be confused with (or
shadowed by) the tools/ source directory, which holds the build inputs
for these pages rather than anything importable.
"""

import logging

from flask import Response, current_app, send_from_directory

log = logging.getLogger("portal.toolsite")

# Default subdomain, overridable with TOOLS_SUBDOMAIN in config. Kept as a
# bare hostname label to match the UPSTREAMS keys.
DEFAULT_TOOLS_SUBDOMAIN = "tools"

# URL path (no leading slash) → filename under static/. The empty key is
# the bare subdomain, so https://tools.smartenergylab.software/ lands
# straight on the calculator; the named path is an alias, which keeps the
# door open for a tools index at "" later without breaking shared links.
TOOLS = {
    "": "string-calculator.html",
    "string-calculator": "string-calculator.html",
}


def _tools_subdomain() -> str:
    return current_app.config.get("TOOLS_SUBDOMAIN", DEFAULT_TOOLS_SUBDOMAIN)


def is_tools_host(host: str) -> bool:
    """True when this request arrived on the public tools subdomain."""
    if not host:
        return False
    sub = host.split(":")[0].split(".")[0]
    return sub == _tools_subdomain()


def serve_tool(path: str):
    """Serve a static tool page, or 404. No login required."""
    # Tolerate a trailing slash so /string-calculator/ works too.
    filename = TOOLS.get(path.rstrip("/"))
    if filename is None:
        return Response("Not found", status=404, mimetype="text/plain")
    return send_from_directory(current_app.static_folder, filename)
