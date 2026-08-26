# PV String Calculator — handoff

A free, public PV string sizing calculator built for SolarPlus (Aug 2026), modelled on
wirewrite.co.nz/calculator/solar-string. Single self-contained HTML file — no backend,
no database, no build framework. Works on phone, tablet and PC, light and dark mode.
The only external request is Google Fonts (Archivo + Public Sans); everything else,
including all calculation logic, is inlined.

This document covers what the app does, where it lives in this repo, how to deploy it
to burgan, how the maths works, and how to change it safely.

---

## What it calculates

Given a PV module's datasheet values, the site temperature extremes, and the inverter's
DC limits, it reports the allowable modules-per-string range and live pass/fail checks
for any proposed string length, per the AS/NZS 5033:2021 methodology:

| Result | How |
|---|---|
| Cold V<sub>oc</sub> per module | `Voc × (1 + βVoc/100 × (Tmin − 25))`, or `Voc × Table 4.1 factor` when the coefficient isn't available (crystalline Si only) |
| Hot V<sub>mp</sub> per module | `Vmp × (1 + γ/100 × (Tcell,max − 25))` — γP<sub>max</sub>, falling back to βV<sub>oc</sub> if γ is blank |
| Cold V<sub>mp</sub> per module | same formula at T<sub>min</sub> |
| Max modules (absolute) | `floor(inverter max DC input / cold Voc)` |
| Max modules (MPPT tracking) | `floor(MPPT max / cold Vmp)` |
| Min modules | `ceil(MPPT min / hot Vmp)` |
| Design I<sub>sc</sub> per string | `max(1.25 × K_I × Isc, K_I × Isc corrected to Tcell,max via αIsc)` — K_I = 1 unless the module is bifacial |
| Current checks | strings × design I<sub>sc</sub> ≤ inverter I<sub>sc</sub> rating; strings × K_I × I<sub>mp</sub> ≤ max input current |
| Proposed-string checks | cold string V<sub>oc</sub> ≤ max DC input; hot string V<sub>mp</sub> ≥ MPPT min (and ≥ start-up voltage); cold string V<sub>mp</sub> ≤ MPPT max |

The Table 4.1 factor bands (25 °C+ → 1.00 down to −40 °C → 1.25) are embedded in
`engine.js` (`TABLE_41`) and shown to the user in a collapsible reference table.
**Before promoting this tool publicly, verify those factors line-by-line against a
current printed copy of AS/NZS 5033:2021** — the page already carries a
"guidance only / verify with a licensed designer" disclaimer in the footer.

Non-integer minimum temperatures fall into the next colder band (e.g. −5.5 °C → the
−10 to −6 °C band, factor 1.14), which is the conservative reading.

### Bifacial modules

Per AS/NZS 5033:2021 **Clause 3.3.3.1** and **Appendix J (normative)**, bifaciality
raises the maximum string current:

    I_STRING_MAX = 1.25 × K_I × I_SC_MOD

K_I is the ratio of the maximum bifacial short-circuit current (allowing for all site
factors) to the monofacial front-face I<sub>sc</sub> at STC. Appendix J gives three ways
to determine it, and the UI offers exactly those three:

| Method | K_I | Basis |
|---|---|---|
| Close-parallel roof mounting | `1` | Appendix J(c) — modules close and parallel to a tiled roof see very limited rear-face irradiance |
| Datasheet BNPI I<sub>sc</sub> | `Isc_BNPI / Isc` | Appendix J(b) — where no simulation data exists. BNPI = bifacial nameplate irradiance, 1000 W/m² front + 135 W/m² rear (Clause 1.3.3) |
| Simulation | entered directly | Appendix J(a) — accounts for albedo, location, orientation, shading, row spacing, bifacial factor and mismatch |

Where both faces can see irradiance above 400 W/m² (fence-type/vertical installs, open
racks in snowy areas), the standard recommends determining K_I by simulation. The UI
says so under the control.

Two things to hold onto when changing this:

- **K_I scales current only.** The standard does not correct voltage for bifaciality,
  so no V<sub>oc</sub>/V<sub>mp</sub> calculation touches `ki`. Don't "fix" that.
- **K_I is defined for I<sub>sc</sub>.** Applying it to I<sub>mp</sub> for the inverter
  max-input-current check is a deliberate conservative extension, not a clause
  requirement — it is flagged as such in a comment in `engine.js`.

Monofacial is the default and K_I = 1 is a strict no-op: `test.js` asserts the roof
method and `bifacial: false` produce identical `iscDesign`, `nMax` and `nMin`, and that
the check detail strings are byte-identical.

Engine inputs: `bifacial` (bool), `kiMethod` (`'roof' | 'bnpi' | 'sim'`), `iscBnpi`,
`kiSim`. Missing `iscBnpi`/`kiSim` is treated as incomplete input (the calculation
short-circuits, exactly like a temperature below −40 °C on the Table 4.1 method).
Warnings fire for `Isc_BNPI < Isc`, a simulated K_I below 1, and any K_I above 1.35.

## Files

```
tools/pv-string-calculator/
├── handoff.md      this file
├── engine.js       calculation engine — the single source of truth for the maths
├── ui.html         page template (markup + CSS + UI wiring); contains an
│                   /*__ENGINE__*/ placeholder where engine.js is inlined
├── test.js         engine unit tests — run with `node test.js` (57 checks:
│                   Table 4.1 band edges, a hand-calculated worked example,
│                   table-vs-coefficient methods, deliberate fail cases, and the
│                   three Appendix J bifacial K_I paths)
└── build.py        inlines engine.js into ui.html, wraps it in a full HTML
                    document, writes ../../static/string-calculator.html

static/
└── string-calculator.html   the BUILT artifact that actually gets served —
                             never edit this file by hand
```

## Where it's served

The public URL is its own subdomain:

    https://tools.smartenergylab.software/

with `https://tools.smartenergylab.software/string-calculator` as an alias, so a
tools index can take over `/` later without breaking links already handed out.

No login required, and that is enforced structurally rather than by omission.
`toolsite.py` handles the `tools.` host from `app.py`'s `route_by_host` hook, and
that branch runs **before** the proxy branch, so a tools page can never reach
`auth_gate()`. It serves files from `static/` through an explicit allow-list
(`TOOLS`), so unmapped paths 404 rather than exposing the rest of `static/`.

The file also remains reachable at `https://smartenergylab.software/static/string-calculator.html`
via Flask's built-in static route — harmless, but `tools.` is the URL to publish.

Two things this needs that a plain static file wouldn't:

- **an Apache vhost** for `tools.smartenergylab.software` (in `apache/smartenergylab.conf`,
  §4) — same `ProxyPreserveHost` → `127.0.0.1:8000` pattern as the other three;
- **a gunicorn restart** after `toolsite.py` or `app.py` changes. Editing only the
  built HTML still needs no restart — gunicorn reads it from disk per request.

DNS needs no work — `*.smartenergylab.software` is a wildcard A record pointing at
burgan. **TLS is not a wildcard**, though: the Let's Encrypt cert is a SAN cert naming
each host explicitly, so a new subdomain resolves immediately while serving the wrong
certificate until certbot is re-run with an extra `-d`. See README §7.

**Do not add `tools` to `UPSTREAMS`.** Those entries are reverse-proxy targets and are
gated by `auth_gate()`; putting `tools` there would both break the page (there's no
upstream to reach) and put a deliberately-public tool behind a login.

### Optional: link it from the portal page

Add a card or footer link in `templates/portal.html` pointing at the URL above if you
want logged-in users to find it from the system menu.

## Deploying to burgan

It rides along with the normal deploy flow from the README (§2) — rsync the repo to
`/tmp/burgan-portal/` and sync into `/opt/burgan-portal/`. The new files land in
`static/` and `tools/` automatically.

Quick one-off deploy of just the built page (from your machine, repo root) — use this
when you've only rebuilt the HTML and the routing is already live:

```bash
scp static/string-calculator.html you@burgan.arachnoid.net.au:/tmp/
ssh you@burgan.arachnoid.net.au \
  'sudo install -o burgan-portal -g burgan-portal -m 644 \
     /tmp/string-calculator.html /opt/burgan-portal/static/'
```

Check: open https://tools.smartenergylab.software/ — the defaults (a 550 W module on a
600 V / MPPT 90–560 V inverter at −5 °C) should immediately show **"3 – 11 modules"**
with all six checks MET.

## Making changes

1. Edit `engine.js` (maths) and/or `ui.html` (layout, copy, defaults, colours).
2. Rebuild: `python3 build.py` (from `tools/pv-string-calculator/`).
3. Re-test: `node test.js` — must end `57 passed, 0 failed` (add tests when you add
   maths; every formula change should get a hand-calculated expectation).
4. Deploy as above.

Never edit `static/string-calculator.html` directly — the next build overwrites it.

Common tweaks, all in `ui.html`:

- **Default values** — the `value="…"` attributes on the inputs (module, temps,
  inverter). Blank the optional ones if you'd rather start empty.
- **Branding** — the wordmark in `<header class="top">` and the colour tokens at the
  top of the `<style>` block (`--accent`, `--accent-deep` etc.; light theme in
  `:root`, dark theme in the two `data-theme`/media blocks — change all three
  consistently).
- **Disclaimer text** — `<footer class="fine">`.

## Design decisions worth knowing

- **Engine is framework-free ES5-ish JS** exporting via `module.exports` when present,
  so the identical file runs in the browser (inlined) and under Node for testing.
- **γP<sub>max</sub> fallback**: hot/cold V<sub>mp</sub> uses the P<sub>max</sub>
  coefficient when given, otherwise βV<sub>oc</sub> — the common conservative
  convention when γ isn't published.
- **1.25 × I<sub>sc</sub> floor**: design short-circuit current is the greater of the
  AS/NZS 5033 1.25 multiplier and temperature-corrected I<sub>sc</sub>.
- **MPPT-limited warning**: if the MPPT upper voltage (not the absolute DC limit) is
  what caps the string, the tool says so — the string is safe above that count but can
  drift out of the tracking window in cold weather.
- **No storage, no network calls**: results are computed client-side on every
  keystroke; nothing is logged or sent anywhere.

## Provenance

Built with Claude (Cowork) for Glen Morris, Aug 2026. Engine verified against
hand-calculated examples (see `test.js`). A hosted preview also exists as a private
Claude artifact; the repo copy here is the canonical one.
