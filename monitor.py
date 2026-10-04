"""
monitor.smartenergylab.software — admin view of every controlled load.

One page showing the loads the solar-surplus controllers switch at the
Lodge (LodgyBox) and on the Smart Energy Lab network (noisy, rubberduck),
next to the microgrid's batteries, demand, frequency and generator.

Routed by the Host header like toolsite.py and proxy.py, but unlike the
proxied subdomains nothing is forwarded: burgan fetches the public JSON
the dashboards on pignus (monitor.mooramoora.org.au) already serve and
merges it into one /api/state. The data is read-only here; this page
can't switch anything. It's login-gated anyway because it puts every
site's occupancy-revealing load state on one screen.

Sources (all on MONITOR_SOURCE_BASE):
  /api/soc              both battery SoCs, online flags
  /api/solis/data       Solis AC power, pack powers, microgrid frequency
  /api/sppro/data       SP Pro battery power, generator (grid_w)
  /hotwater/api/state   both AC-THOR hot water diverters (acthor-surplus)
  /comfort/api/current  Lodge Lounge + Dining Room aircons, heater plugs
  /dacha/api/current    Dacha aircon (noisy)
  /studio/api/current   Studio aircon (noisy)

The Caretaker's Flat is never pushed to pignus, so it can't appear here.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import (
    Response, current_app, jsonify, redirect, render_template, request,
    send_from_directory,
)
from flask_login import current_user

log = logging.getLogger("portal.monitor")

DEFAULT_MONITOR_SUBDOMAIN = "monitor"
DEFAULT_SOURCE_BASE = "https://monitor.mooramoora.org.au"

SOURCES = {
    "soc":     "/api/soc",
    "solis":   "/api/solis/data",
    "sppro":   "/api/sppro/data",
    "hotwater": "/hotwater/api/state",
    "comfort": "/comfort/api/current",
    "dacha":   "/dacha/api/current",
    "studio":  "/studio/api/current",
}

# Same threshold as the flow diagram and acthor-surplus: the SP Pro reports
# generator import on grid_w as NEGATIVE, so running = |grid_w| above this.
GENERATOR_THRESHOLD_W = 200

# The browser polls every 15 s and gunicorn runs 2 workers, so a short
# per-process cache keeps pignus to a handful of requests a minute however
# many tabs are open.
CACHE_SECONDS = 10

_cache = {"at": 0.0, "state": None}
_cache_lock = threading.Lock()


def _monitor_subdomain() -> str:
    return current_app.config.get("MONITOR_SUBDOMAIN", DEFAULT_MONITOR_SUBDOMAIN)


def is_monitor_host(host: str) -> bool:
    if not host:
        return False
    return host.split(":")[0].split(".")[0] == _monitor_subdomain()


def _allowed() -> bool:
    """Logged in, and on the MONITOR_ALLOWED_EMAILS list if one is set."""
    if not current_user.is_authenticated:
        return False
    allow = current_app.config.get("MONITOR_ALLOWED_EMAILS")
    if not allow:
        return True
    return current_user.email.lower() in {e.lower() for e in allow}


def serve_monitor(path: str):
    # The shared stylesheet; this host has no Flask static route of its own.
    if path.startswith("static/"):
        return send_from_directory(current_app.static_folder, path[len("static/"):])
    path = path.rstrip("/")
    if path not in ("", "api/state"):
        return Response("Not found", status=404, mimetype="text/plain")

    if not current_user.is_authenticated:
        if path == "api/state":
            return jsonify(error="not signed in"), 401
        return redirect(
            current_app.config["PORTAL_BASE_URL"] + "/login?next=" + request.url
        )
    if not _allowed():
        log.info("monitor: refused %s (not in MONITOR_ALLOWED_EMAILS)", current_user.email)
        return Response("Forbidden", status=403, mimetype="text/plain")

    if path == "api/state":
        resp = jsonify(get_state())
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return render_template(
        "monitor.html", portal_url=current_app.config["PORTAL_BASE_URL"]
    )


# ---------------------------------------------------------------------------
# Fetch + merge
# ---------------------------------------------------------------------------
def get_state() -> dict:
    with _cache_lock:
        if _cache["state"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["state"]
    base = current_app.config.get("MONITOR_SOURCE_BASE", DEFAULT_SOURCE_BASE).rstrip("/")
    state = build_state(_fetch_all(base))
    with _cache_lock:
        _cache.update(at=time.monotonic(), state=state)
    return state


def _fetch_one(url: str):
    try:
        r = requests.get(url, timeout=(4, 8))
        r.raise_for_status()
        return r.json(), None
    except (requests.exceptions.RequestException, ValueError) as e:
        log.warning("monitor: %s failed: %s", url, e)
        return None, type(e).__name__


def _fetch_all(base: str) -> dict:
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {k: pool.submit(_fetch_one, base + p) for k, p in SOURCES.items()}
        return {k: f.result() for k, f in futures.items()}


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _age_from_iso(ts):
    """Seconds since an ISO timestamp with an offset, or None."""
    try:
        t = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        return None
    return max(0, round((datetime.now(timezone.utc) - t).total_seconds()))


def build_state(raw: dict) -> dict:
    """Merge the source payloads into one shape for the page.

    Pure function of the fetched payloads (each a (json, error) pair) so it
    can be exercised without the network.
    """
    data = {k: v[0] for k, v in raw.items()}
    sources = {k: {"ok": v[0] is not None, "error": v[1]} for k, v in raw.items()}

    soc = (data["soc"] or {}).get("devices", {})
    solis = data["solis"] or {}
    sppro = data["sppro"] or {}
    solis_dev = soc.get("solis", {})
    sppro_dev = soc.get("sppro", {})
    solis_on = bool(data["solis"]) and solis_dev.get("online", True)
    sppro_on = bool(data["sppro"]) and sppro_dev.get("online", True)

    # --- batteries and the bus balance (mirrors templates/flow_diagram.html
    # in microgrid_remote_monitor, so the numbers agree with /flow) ---
    solis_batt = None
    if solis_on:
        packs = [_num(solis.get("battery_power")), _num(solis.get("battery2_power"))]
        packs = [p for p in packs if p is not None]
        solis_batt = sum(packs) if packs else None
    solis_to_bus = None
    if solis_on:
        solis_to_bus = _num(solis.get("inverter_ac_power"))
        if solis_to_bus is None:
            solis_to_bus = (_num(solis.get("pv_total_power")) or 0) - (solis_batt or 0)

    gen_w = _num(sppro.get("grid_w")) if sppro_on else None
    gen_running = None if gen_w is None else abs(gen_w) >= GENERATOR_THRESHOLD_W
    sppro_batt = _num(sppro.get("battery_w")) if sppro_on else None   # + = charging
    sppro_to_bus = None
    if sppro_batt is not None:
        gen_supply = -gen_w if (gen_w is not None and gen_w < -GENERATOR_THRESHOLD_W) else 0
        sppro_to_bus = -sppro_batt + gen_supply

    if solis_to_bus is None and sppro_to_bus is None:
        injection = None
    else:
        injection = (solis_to_bus or 0) + (sppro_to_bus or 0)
    house_solar = None
    if solis_to_bus is not None and sppro_to_bus is not None:
        house_solar = max(0.0, -injection)
    consumption = None if injection is None else max(0.0, injection + (house_solar or 0))

    batteries = [
        {
            "key": "solis", "name": "Solis S6-EH3P 50 kW",
            "online": solis_on,
            "soc": _num(solis_dev.get("soc")),
            "packs": [_num(solis.get("battery_soc")), _num(solis.get("bms2_battery_soc"))],
            "battery_w": solis_batt,
            "to_bus_w": solis_to_bus,
            "pv_w": _num(solis.get("pv_total_power")),
            "age_s": _age_from_iso(solis_dev.get("polled_at")),
        },
        {
            "key": "sppro", "name": "Selectronic SP Pro",
            "online": sppro_on,
            "soc": _num(sppro_dev.get("soc", sppro.get("battery_soc"))),
            "packs": [],
            "battery_w": sppro_batt,
            "to_bus_w": sppro_to_bus,
            "pv_w": None,
            "age_s": _age_from_iso(sppro_dev.get("polled_at")),
        },
    ]

    microgrid = {
        "frequency_hz": _num(solis.get("grid_frequency")) if solis_on else None,
        "consumption_w": consumption,
        "house_solar_w": house_solar,
        "generator_running": gen_running,
        "generator_w": None if gen_w is None else abs(gen_w),
        "solis_ok": solis_on, "sppro_ok": sppro_on,
    }

    # --- controlled loads ---
    loads = []
    hw = (data["hotwater"] or {})
    hw_snap = hw.get("snapshot") or {}
    hw_heaters = {h.get("name"): h for h in hw_snap.get("heaters", [])}
    for name, site in (("Wharenui", "sel"), ("Lodge", "lodge")):
        loads.append(_hotwater_load(name, site, hw_heaters.get(name), hw, sources["hotwater"]["ok"]))

    comfort = data["comfort"] or {}
    for key, name in (("lounge", "Lounge"), ("dining_room", "Dining Room")):
        loads.append(_aircon_load(f"lodge_{key}", f"{name} aircon", "lodge",
                                  (comfort.get("units") or {}).get(key), comfort))
    for key, name in (("office", "Office heater"), ("lodge_upstairs", "Upstairs heater")):
        loads.append(_plug_load(f"heater_{key}", name, "lodge",
                                (comfort.get("heaters") or {}).get(key), comfort))
    for src, key, name in (("dacha", "dacha", "Dacha aircon"), ("studio", "studio", "Studio aircon")):
        payload = data[src] or {}
        loads.append(_aircon_load(key, name, "sel", (payload.get("units") or {}).get(key), payload))

    surplus = comfort.get("surplus") or {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "batteries": batteries,
        "microgrid": microgrid,
        "loads": loads,
        "loads_total_w": sum(l["power_w"] or 0 for l in loads),
        "lodge_surplus": {
            "active": surplus.get("active"),
            "heating_enabled": surplus.get("heating_enabled"),
            "setpoint": surplus.get("setpoint"),
        } if surplus else None,
        "hotwater_decision": (hw_snap.get("decision") or {}).get("reason"),
    }


def _base(id_, name, site, kind):
    return {"id": id_, "name": name, "site": site, "kind": kind,
            "status": "unknown", "on": None, "power_w": None,
            "detail": [], "managed": None, "age_s": None, "stale": True}


def _hotwater_load(name, site, h, payload, ok):
    out = _base(f"hotwater_{site}", f"{name} hot water", site, "hotwater")
    if not ok or h is None:
        out["detail"].append("no data")
        return out
    out["age_s"] = payload.get("age_s")
    out["stale"] = (out["age_s"] or 0) > 120
    out["power_w"] = _num(h.get("power_w"))
    out["managed"] = bool(h.get("modbus_control"))
    if not h.get("reachable", True):
        out["status"] = "unavailable"
    elif h.get("boosting"):
        out["status"] = "boost"
    elif (out["power_w"] or 0) > 0:
        out["status"] = "heating"
    else:
        out["status"] = "off"
    out["on"] = out["status"] in ("heating", "boost")
    t, target = _num(h.get("temp_c")), _num(h.get("target_c"))
    if t is not None:
        out["detail"].append(f"{t:.1f} °C" + (f" / {target:.0f} °C" if target is not None else ""))
    if h.get("note"):
        out["detail"].append(h["note"])
    return out


def _aircon_load(id_, name, site, u, payload):
    out = _base(id_, name, site, "aircon")
    if u is None:
        out["detail"].append("no data")
        return out
    out["age_s"] = payload.get("age_s")
    out["stale"] = bool(payload.get("stale")) or (out["age_s"] or 0) > 300
    kw = _num(u.get("compressor_kw"))
    out["power_w"] = None if kw is None else kw * 1000
    out["managed"] = u.get("managed_by_surplus")
    mode = u.get("hvac_mode") or "off"
    if not u.get("available", True):
        out["status"] = "unavailable"
    elif mode == "off":
        out["status"] = "off"
    else:
        # Dacha/Studio push a compressor-frequency "running" flag; the lodge
        # units only have hvac_action, which says heating while resting.
        running = u.get("running")
        if running is None:
            running = u.get("hvac_action") in ("heating", "cooling") or (kw or 0) > 0.05
        out["status"] = (u.get("hvac_action") or mode) if running else "idle"
    out["on"] = mode != "off" and out["status"] != "unavailable"
    bits = []
    if u.get("inside_temp") is not None:
        bits.append(f"{u['inside_temp']:g} °C in")
    if mode != "off":
        target = u.get("target_temp")
        bits.append(f"{mode}" + (f" → {target:g} °C" if target is not None else ""))
    if u.get("energy_today_kwh") is not None:
        bits.append(f"{u['energy_today_kwh']:g} kWh today")
    out["detail"] = bits
    return out


def _plug_load(id_, name, site, h, payload):
    out = _base(id_, name, site, "heater")
    if h is None:
        out["detail"].append("no data")
        return out
    out["age_s"] = payload.get("age_s")
    out["stale"] = bool(payload.get("stale")) or (out["age_s"] or 0) > 300
    out["power_w"] = _num(h.get("power_w"))
    out["managed"] = h.get("managed_by_surplus")
    if not h.get("available", True):
        out["status"] = "unavailable"
    else:
        out["status"] = "on" if h.get("on") else "off"
    out["on"] = bool(h.get("on"))
    return out
