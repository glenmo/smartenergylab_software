'use strict';
var E = require('./engine.js');
var pass = 0, fail = 0;
function eq(name, got, want, tol) {
  tol = tol == null ? 1e-9 : tol;
  var ok = Math.abs(got - want) <= tol;
  ok ? pass++ : fail++;
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + '  got=' + got + ' want=' + want);
}

/* Table 4.1 band lookups */
eq('factor @ -5',    E.table41Factor(-5).f,   1.12);
eq('factor @ -5.5',  E.table41Factor(-5.5).f, 1.14); // falls into -10..-6, conservative
eq('factor @ 0',     E.table41Factor(0).f,    1.10);
eq('factor @ 22',    E.table41Factor(22).f,   1.02);
eq('factor @ 25',    E.table41Factor(25).f,   1.00);
eq('factor @ -40',   E.table41Factor(-40).f,  1.25);
console.log('below -40 returns null: ' + (E.table41Factor(-41) === null ? 'PASS' : 'FAIL'));
E.table41Factor(-41) === null ? pass++ : fail++;

/* Worked example — 550 W module, coefficient method
   Voc 49.5 V, Vmp 41.5 V, Isc 13.9 A, Imp 13.25 A
   βVoc -0.25 %/°C, αIsc +0.045 %/°C, γPmp -0.30 %/°C
   Tmin -5 °C, Tcell,max 70 °C
   Inverter: 600 V max DC, MPPT 90–560 V, start 100 V, 16 A input, 24 A Isc */
var inp = {
  voc: 49.5, vmp: 41.5, isc: 13.9, imp: 13.25, pmax: 550,
  betaVoc: -0.25, alphaIsc: 0.045, gammaPmp: -0.30,
  method: 'coeff', tMin: -5, tCellMax: 70,
  vInvMax: 600, vMpptMin: 90, vMpptMax: 560, vStart: 100,
  iMpptMax: 16, iscMpptMax: 24, nStrings: 1, nProposed: 10
};
var r = E.calcString(inp);
/* hand calc: vocCold = 49.5*(1 + (-0.0025)*(-30)) = 49.5*1.075 = 53.2125 */
eq('vocCold', r.vocCold, 53.2125, 1e-6);
/* vmpHot = 41.5*(1 + (-0.003)*45) = 41.5*0.865 = 35.8975 */
eq('vmpHot', r.vmpHot, 35.8975, 1e-6);
/* vmpCold = 41.5*(1 + (-0.003)*(-30)) = 41.5*1.09 = 45.235 */
eq('vmpCold', r.vmpCold, 45.235, 1e-6);
/* iscHot = 13.9*(1+0.00045*45) = 13.9*1.02025 = 14.181475 ; 1.25*13.9 = 17.375 governs */
eq('iscHot', r.iscHot, 14.181475, 1e-6);
eq('iscDesign', r.iscDesign, 17.375, 1e-6);
/* nMaxVoc = floor(600/53.2125) = floor(11.276) = 11 */
eq('nMaxVoc', r.nMaxVoc, 11);
/* nMaxMppt = floor(560/45.235) = floor(12.379) = 12 -> nMax = 11 */
eq('nMaxMppt', r.nMaxMppt, 12);
eq('nMax', r.nMax, 11);
/* nMin = ceil(90/35.8975) = ceil(2.507) = 3 */
eq('nMin', r.nMin, 3);
/* proposed 10: vOcCold = 532.125 <= 600 PASS; vMpHot = 358.975 >= 90 PASS;
   vMpCold = 452.35 <= 560 PASS; start 358.975 >= 100 PASS;
   Isc: 17.375 <= 24 PASS; Imp: 13.25 <= 16 PASS => overall pass */
eq('proposed vOcCold', r.proposed.vOcCold, 532.125, 1e-6);
console.log('proposed 10 overall: ' + (r.proposed.pass ? 'PASS' : 'FAIL'));
r.proposed.pass ? pass++ : fail++;

/* Table method at -5 °C: vocCold = 49.5*1.12 = 55.44, nMaxVoc = floor(600/55.44)=10 */
var r2 = E.calcString(Object.assign({}, inp, { method: 'table' }));
eq('table vocCold', r2.vocCold, 55.44, 1e-6);
eq('table nMaxVoc', r2.nMaxVoc, 10);

/* Failing proposed: 12 modules on 600 V -> 12*53.2125 = 638.55 > 600 */
var r3 = E.calcString(Object.assign({}, inp, { nProposed: 12 }));
console.log('proposed 12 fails Voc: ' + (!r3.proposed.checks.find(c=>c.id==='pvoc').pass ? 'PASS' : 'FAIL'));
!r3.proposed.checks.find(c=>c.id==='pvoc').pass ? pass++ : fail++;

/* Two strings: Isc design 2*17.375 = 34.75 > 24 -> fail */
var r4 = E.calcString(Object.assign({}, inp, { nStrings: 2 }));
var iscChk = r4.checks.find(c=>c.id==='isc');
console.log('2 strings Isc fail: ' + (!iscChk.pass ? 'PASS' : 'FAIL'));
!iscChk.pass ? pass++ : fail++;
var impChk = r4.checks.find(c=>c.id==='imp');
/* 2*13.25 = 26.5 > 16 -> fail */
console.log('2 strings Imp fail: ' + (!impChk.pass ? 'PASS' : 'FAIL'));
!impChk.pass ? pass++ : fail++;

/* ===================================================================
   Bifacial modules — AS/NZS 5033:2021 Clause 3.3.3.1 + Appendix J
   I_STRING_MAX = 1.25 × K_I × I_SC_MOD.  Currents only: every voltage
   result below must be identical to the monofacial case.
   =================================================================== */
function ok(name, cond) {
  cond ? pass++ : fail++;
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + name);
}
function hasWarn(r, re) { return r.warnings.some(function (w) { return re.test(w); }); }

var mono = E.calcString(inp);   /* monofacial baseline, same inp as above */

/* --- Appendix J(b): K_I from the datasheet Isc at BNPI --- */
var bnpi = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'bnpi', iscBnpi: 15.8
}));
/* ki = 15.8/13.9 = 1.136690647...; design Isc = 1.25 × ki × 13.9 = 1.25 × 15.8 = 19.75 exactly */
eq('bnpi ki', bnpi.ki, 15.8 / 13.9, 1e-12);
eq('bnpi ki value', bnpi.ki, 1.136690647482014, 1e-9);
eq('bnpi iscDesign', bnpi.iscDesign, 19.75, 1e-6);
/* iscHot itself stays the monofacial front-face figure */
eq('bnpi iscHot unscaled', bnpi.iscHot, 14.181475, 1e-6);
/* Imp scaled by ki (conservative extension): 13.25 × 1.136690647 = 15.06115... */
eq('bnpi iStringOp', bnpi.iStringOp, 13.25 * (15.8 / 13.9), 1e-9);

/* --- voltages must be untouched by bifaciality --- */
eq('bnpi vocCold unchanged', bnpi.vocCold, mono.vocCold, 0);
eq('bnpi vmpHot unchanged',  bnpi.vmpHot,  mono.vmpHot,  0);
eq('bnpi vmpCold unchanged', bnpi.vmpCold, mono.vmpCold, 0);
eq('bnpi nMax unchanged',    bnpi.nMax,    mono.nMax,    0);
eq('bnpi nMin unchanged',    bnpi.nMin,    mono.nMin,    0);

/* --- Appendix J(a): K_I straight from simulation --- */
var sim = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'sim', kiSim: 1.15
}));
eq('sim ki', sim.ki, 1.15, 1e-12);
/* 1.25 × 1.15 × 13.9 = 19.98125 */
eq('sim iscDesign', sim.iscDesign, 19.98125, 1e-6);

/* --- Appendix J(c): close-parallel roof ⇒ K_I = 1 ⇒ strict no-op --- */
var roof = E.calcString(Object.assign({}, inp, { bifacial: true, kiMethod: 'roof' }));
eq('roof ki is 1', roof.ki, 1, 0);
eq('roof iscDesign == monofacial', roof.iscDesign, mono.iscDesign, 0);
eq('roof iStringOp == monofacial', roof.iStringOp, mono.iStringOp, 0);
eq('roof nMax == monofacial', roof.nMax, mono.nMax, 0);
eq('roof nMin == monofacial', roof.nMin, mono.nMin, 0);
ok('roof isc detail identical to monofacial',
   roof.checks.find(c => c.id === 'isc').detail === mono.checks.find(c => c.id === 'isc').detail);

/* --- 2 parallel strings on BNPI ki: 2 × 19.75 = 39.5 A > 24 A rating ⇒ fail --- */
var bnpi2 = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'bnpi', iscBnpi: 15.8, nStrings: 2
}));
var b2isc = bnpi2.checks.find(c => c.id === 'isc');
eq('2-string bnpi isc value', b2isc.value, 39.5, 1e-6);
ok('2-string bnpi isc check fails', !b2isc.pass);
ok('bnpi isc detail shows K_I arithmetic', /1\.25 × 1\.14 × 13\.90/.test(b2isc.detail));

/* --- hot-Isc floor, with K_I applied to BOTH candidate terms ---
   (a) alpha 0.045: 1.25 × isc (17.375) beats hot isc (14.1815) ⇒ 1.25 term governs */
ok('hot floor not governing when 1.25 term is larger',
   inp.isc * 1.25 > sim.iscHot && Math.abs(sim.iscDesign - 1.25 * 1.15 * 13.9) < 1e-9);
/*  (b) alpha 0.8 @ 70 °C: iscHot = 13.9 × 1.36 = 18.904 > 1.25 × 13.9 = 17.375
       ⇒ hot term governs, and ki must scale it: 1.15 × 18.904 = 21.7396 */
var hot = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'sim', kiSim: 1.15, alphaIsc: 0.8
}));
eq('hot floor iscHot', hot.iscHot, 18.904, 1e-9);
ok('hot floor actually governs', hot.iscHot > inp.isc * 1.25);
eq('hot floor iscDesign = ki × iscHot', hot.iscDesign, 1.15 * 18.904, 1e-9);

/* --- warning paths --- */
var wLow = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'bnpi', iscBnpi: 13.0     /* below front-face STC Isc */
}));
ok('bnpi below STC Isc warns', hasWarn(wLow, /BNPI is below the front-face/));
ok('bnpi below STC Isc still computes', wLow.iscDesign > 0 && wLow.errors.length === 0);

var wSim = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'sim', kiSim: 0.9
}));
ok('kiSim below 1 warns', hasWarn(wSim, /below 1/));
eq('kiSim below 1 not clamped', wSim.ki, 0.9, 1e-12);

var wHigh = E.calcString(Object.assign({}, inp, {
  bifacial: true, kiMethod: 'sim', kiSim: 1.40
}));
ok('ki above 1.35 warns', hasWarn(wHigh, /unusually high/));

/* --- incomplete bifacial input is treated like a missing required field --- */
var eSim = E.calcString(Object.assign({}, inp, { bifacial: true, kiMethod: 'sim' }));
ok('missing kiSim errors', eSim.errors.length > 0);
var eBnpi = E.calcString(Object.assign({}, inp, { bifacial: true, kiMethod: 'bnpi' }));
ok('missing iscBnpi errors', eBnpi.errors.length > 0);

/* --- monofacial default really is untouched: ki defaults to 1, no warnings added --- */
eq('monofacial ki defaults to 1', mono.ki, 1, 0);
ok('monofacial kiMethod is null', mono.kiMethod === null);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
