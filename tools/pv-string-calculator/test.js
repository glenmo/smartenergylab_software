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

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
