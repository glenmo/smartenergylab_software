/* SolarPlus PV String Calculator engine — AS/NZS 5033:2021 methodology */
'use strict';

/* AS/NZS 5033:2021 Table 4.1 — Voc correction factors for crystalline and
   multi-crystalline silicon modules when the Voc temperature coefficient is
   not available, by lowest expected operating temperature. */
var TABLE_41 = [
  { lo: 20,  hi: 24,  f: 1.02 },
  { lo: 15,  hi: 19,  f: 1.04 },
  { lo: 10,  hi: 14,  f: 1.06 },
  { lo: 5,   hi: 9,   f: 1.08 },
  { lo: 0,   hi: 4,   f: 1.10 },
  { lo: -5,  hi: -1,  f: 1.12 },
  { lo: -10, hi: -6,  f: 1.14 },
  { lo: -15, hi: -11, f: 1.16 },
  { lo: -20, hi: -16, f: 1.18 },
  { lo: -25, hi: -21, f: 1.20 },
  { lo: -30, hi: -26, f: 1.21 },
  { lo: -35, hi: -31, f: 1.23 },
  { lo: -40, hi: -36, f: 1.25 }
];

function table41Factor(tMin) {
  if (tMin >= 25) return { f: 1.00, band: '25 °C and above' };
  for (var i = 0; i < TABLE_41.length; i++) {
    var b = TABLE_41[i];
    if (tMin >= b.lo) return { f: b.f, band: b.lo + ' to ' + b.hi + ' °C' };
  }
  return null; /* below -40 °C: outside Table 4.1 — coefficient method required */
}

/* inputs:
   voc, vmp, isc, imp        module STC values (V, V, A, A)
   pmax                      module STC power, W (optional, for string power)
   betaVoc                   Voc temp coefficient, %/°C (usually negative)
   alphaIsc                  Isc temp coefficient, %/°C (optional, usually positive)
   gammaPmp                  Pmax temp coefficient, %/°C (optional; falls back to betaVoc for hot Vmp)
   method                    'coeff' | 'table'
   tMin                      lowest expected ambient/operating temp, °C
   tCellMax                  highest expected cell temp, °C (default 70)
   vInvMax                   inverter max DC input voltage, V
   vMpptMin, vMpptMax        MPPT window, V
   vStart                    inverter start-up voltage, V (optional)
   iMpptMax                  max usable input current per MPPT, A (optional)
   iscMpptMax                max short-circuit current per MPPT, A (optional)
   nStrings                  parallel strings on the MPPT (default 1)
   nProposed                 proposed modules per string (optional)
*/
function calcString(inp) {
  var r = { warnings: [], errors: [], checks: [] };
  var tMin = inp.tMin, tCellMax = (inp.tCellMax == null ? 70 : inp.tCellMax);
  var nStrings = inp.nStrings || 1;

  /* --- cold open-circuit voltage per module --- */
  if (inp.method === 'table') {
    var t41 = table41Factor(tMin);
    if (!t41) {
      r.errors.push('Lowest temperature is below −40 °C — outside Table 4.1. Use the temperature-coefficient method.');
      return r;
    }
    r.vocFactor = t41.f;
    r.vocBand = t41.band;
    r.vocCold = inp.voc * t41.f;
  } else {
    r.vocCold = inp.voc * (1 + (inp.betaVoc / 100) * (tMin - 25));
    r.vocFactor = r.vocCold / inp.voc;
  }

  /* --- hot & cold MPP voltage per module --- */
  var gv = (inp.gammaPmp == null || isNaN(inp.gammaPmp)) ? inp.betaVoc : inp.gammaPmp;
  if (gv == null || isNaN(gv)) gv = 0;
  r.vmpCoeffUsed = gv;
  r.vmpHot  = inp.vmp * (1 + (gv / 100) * (tCellMax - 25));
  r.vmpCold = inp.vmp * (1 + (gv / 100) * (tMin - 25));

  /* --- currents --- */
  var a = (inp.alphaIsc == null || isNaN(inp.alphaIsc)) ? 0 : inp.alphaIsc;
  r.iscHot = inp.isc * (1 + (a / 100) * (tCellMax - 25));
  r.iscDesign = Math.max(inp.isc * 1.25, r.iscHot); /* AS/NZS 5033 1.25 × Isc minimum */
  r.iStringOp = inp.imp || 0;

  /* --- string length limits --- */
  r.nMaxVoc  = Math.floor(inp.vInvMax / r.vocCold);
  r.nMaxMppt = (inp.vMpptMax > 0) ? Math.floor(inp.vMpptMax / r.vmpCold) : null;
  r.nMin     = (inp.vMpptMin > 0) ? Math.ceil(inp.vMpptMin / r.vmpHot) : 1;
  r.nMax     = (r.nMaxMppt != null) ? Math.min(r.nMaxVoc, r.nMaxMppt) : r.nMaxVoc;
  r.rangeOk  = r.nMax >= r.nMin && r.nMax >= 1;

  if (r.nMaxMppt != null && r.nMaxMppt < r.nMaxVoc) {
    r.warnings.push('String length is limited by the MPPT upper voltage (' + inp.vMpptMax +
      ' V), not the inverter absolute maximum. Above ' + r.nMaxMppt +
      ' modules the string stays below the DC limit but can drift out of the tracking window in cold weather.');
  }

  /* --- parallel-string current checks --- */
  if (inp.iscMpptMax != null && !isNaN(inp.iscMpptMax) && inp.iscMpptMax > 0) {
    r.checks.push({
      id: 'isc', label: 'Short-circuit current × ' + nStrings + ' string' + (nStrings > 1 ? 's' : ''),
      value: nStrings * r.iscDesign, limit: inp.iscMpptMax, unit: 'A', cmp: '≤',
      pass: nStrings * r.iscDesign <= inp.iscMpptMax,
      detail: nStrings + ' × ' + r.iscDesign.toFixed(2) + ' A design Isc vs ' + inp.iscMpptMax + ' A MPPT rating'
    });
  }
  if (inp.iMpptMax != null && !isNaN(inp.iMpptMax) && inp.iMpptMax > 0 && r.iStringOp > 0) {
    r.checks.push({
      id: 'imp', label: 'Operating current × ' + nStrings + ' string' + (nStrings > 1 ? 's' : ''),
      value: nStrings * r.iStringOp, limit: inp.iMpptMax, unit: 'A', cmp: '≤',
      pass: nStrings * r.iStringOp <= inp.iMpptMax,
      detail: nStrings + ' × ' + r.iStringOp.toFixed(2) + ' A Imp vs ' + inp.iMpptMax + ' A max input current'
    });
  }

  /* --- proposed string --- */
  if (inp.nProposed != null && !isNaN(inp.nProposed) && inp.nProposed >= 1) {
    var n = Math.round(inp.nProposed);
    var p = { n: n, checks: [] };
    p.vOcCold  = n * r.vocCold;
    p.vMpHot   = n * r.vmpHot;
    p.vMpCold  = n * r.vmpCold;
    p.power    = inp.pmax ? n * inp.pmax * nStrings : null;
    p.checks.push({
      id: 'pvoc', label: 'Maximum string voltage (cold Voc)',
      value: p.vOcCold, limit: inp.vInvMax, unit: 'V', cmp: '≤',
      pass: p.vOcCold <= inp.vInvMax,
      detail: n + ' × ' + r.vocCold.toFixed(1) + ' V vs ' + inp.vInvMax + ' V max DC input'
    });
    if (inp.vMpptMin > 0) p.checks.push({
      id: 'pmin', label: 'Hot-weather MPP voltage above MPPT minimum',
      value: p.vMpHot, limit: inp.vMpptMin, unit: 'V', cmp: '≥',
      pass: p.vMpHot >= inp.vMpptMin,
      detail: n + ' × ' + r.vmpHot.toFixed(1) + ' V vs ' + inp.vMpptMin + ' V MPPT minimum'
    });
    if (inp.vMpptMax > 0) p.checks.push({
      id: 'pmax', label: 'Cold-weather MPP voltage inside MPPT window',
      value: p.vMpCold, limit: inp.vMpptMax, unit: 'V', cmp: '≤',
      pass: p.vMpCold <= inp.vMpptMax,
      detail: n + ' × ' + r.vmpCold.toFixed(1) + ' V vs ' + inp.vMpptMax + ' V MPPT maximum'
    });
    if (inp.vStart != null && !isNaN(inp.vStart) && inp.vStart > 0) p.checks.push({
      id: 'pstart', label: 'Start-up voltage reached in hot weather',
      value: p.vMpHot, limit: inp.vStart, unit: 'V', cmp: '≥',
      pass: p.vMpHot >= inp.vStart,
      detail: n + ' × ' + r.vmpHot.toFixed(1) + ' V vs ' + inp.vStart + ' V start-up'
    });
    p.pass = p.checks.every(function (c) { return c.pass; }) && r.checks.every(function (c) { return c.pass; });
    r.proposed = p;
  }
  return r;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { calcString: calcString, table41Factor: table41Factor, TABLE_41: TABLE_41 };
}
