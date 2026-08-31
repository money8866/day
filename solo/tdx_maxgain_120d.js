const fs = require('fs');
const path = require('path');

const TDX = 'c:/new_tdx';
const HQ = path.join(TDX, 'T0002', 'hq_cache');
const VIP = path.join(TDX, 'vipdoc');
const MKTS = ['sz', 'sh', 'bj'];
const W = 120;

function isStock(mkt, code) {
  if (mkt === 0) return /^00|^30/.test(code);
  if (mkt === 1) return /^60|^68/.test(code);
  if (mkt === 2) return /^43|^8[2-8]|^92/.test(code);
  return false;
}

function readDay(p) {
  const buf = fs.readFileSync(p);
  const n = Math.floor(buf.length / 32);
  const bars = new Array(n);
  for (let i = 0; i < n; i++) {
    const o = i * 32;
    bars[i] = {
      date: buf.readUInt32LE(o),
      open: buf.readUInt32LE(o + 4) / 100,
      high: buf.readUInt32LE(o + 8) / 100,
      low: buf.readUInt32LE(o + 12) / 100,
      close: buf.readUInt32LE(o + 16) / 100,
      vol: buf.readUInt32LE(o + 24),
    };
  }
  return bars;
}

function readBase() {
  const buf = fs.readFileSync(path.join(HQ, 'base.dbf'));
  const nRec = buf.readUInt32LE(4);
  const hSize = buf.readUInt16LE(8);
  const rSize = buf.readUInt16LE(10);
  const fields = [];
  for (let off = 32; buf[off] !== 0x0d && buf[off] !== undefined; off += 32) {
    const name = buf.slice(off, off + 11).toString('latin1').split('\0')[0].trim();
    fields.push({ name, len: buf[off + 16] });
  }
  let acc = 1;
  for (const f of fields) { f.off = acc; acc += f.len; }
  const get = (rec, fname) => {
    const f = fields.find(x => x.name === fname);
    return buf.slice(hSize + rec * rSize + f.off, hSize + rec * rSize + f.off + f.len).toString('latin1').trim();
  };
  const out = [];
  for (let i = 0; i < nRec; i++) {
    const code = get(i, 'GPDM');
    if (!/^\d{6}$/.test(code)) continue;
    out.push({ mkt: +get(i, 'SC'), code, ltag: parseFloat(get(i, 'LTAG')) || 0 });
  }
  return out;
}

let gbkDec = null;
try { gbkDec = new TextDecoder('gbk'); } catch (e) {}
const decName = b => (gbkDec ? gbkDec.decode(b) : '').replace(/\0/g, '').trim();

function buildNames() {
  const map = new Map();
  for (const [fn, mkt] of [['shs.tnf', 'sh'], ['szs.tnf', 'sz'], ['bjs.tnf', 'bj']]) {
    const buf = fs.readFileSync(path.join(HQ, fn));
    const count = Math.floor((buf.length - 50) / 360);
    for (let i = 0; i < count; i++) {
      const p = 50 + i * 360;
      const code = buf.slice(p, p + 6).toString('latin1');
      if (!/^\d{6}$/.test(code)) continue;
      const name = decName(buf.slice(p + 31, p + 31 + 16));
      if (name) map.set(mkt + code, name);
    }
  }
  return map;
}

function limitOf(code) {
  if (/^30|^68/.test(code)) return 0.20;
  if (/^43|^8[2-8]|^92/.test(code)) return 0.30;
  return 0.10;
}
function isLimitUpClose(bars, idx, code) {
  if (idx < 1) return false;
  return bars[idx].close / bars[idx - 1].close - 1 >= limitOf(code) - 0.003;
}
function isLimitUpOpen(bars, idx, code) {
  if (idx < 1) return false;
  return bars[idx].open / bars[idx - 1].close - 1 >= limitOf(code) - 0.003;
}

function loadIndexMA20() {
  try {
    const bars = readDay(path.join(VIP, 'sh', 'lday', 'sh999999.day'));
    const map = new Map();
    let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += bars[i].close;
      if (i >= 20) sum -= bars[i - 20].close;
      const ma = i >= 19 ? sum / 20 : null;
      map.set(bars[i].date, { close: bars[i].close, ma });
    }
    return map;
  } catch (e) { return null; }
}

function findTrigger(rule, bars, i, n) {
  let thr = 0.5;
  if (rule === 'R1_40') thr = 0.4;
  else if (rule === 'R1_60') thr = 0.6;
  const H = bars[i].high, L = bars[i].low, C = bars[i].close, V = bars[i].vol;
  if (rule === 'R1' || rule === 'R1_40' || rule === 'R1_60') {
    for (let j = i + 1; j <= Math.min(i + 20, n - 1); j++) {
      const b = bars[j];
      if (b.close > b.open && b.vol <= V * thr && b.close < C && b.close > L) return j;
    }
    return -1;
  }
  if (rule === 'R2') {
    for (let j = i + 1; j <= Math.min(i + 20, n - 1); j++) {
      const b = bars[j];
      if (b.close > H && b.vol <= V) return j;
    }
    return -1;
  }
  if (rule === 'R3') {
    let broke = false;
    for (let j = i + 1; j <= Math.min(i + 20, n - 1); j++) {
      const ma5 = (bars[j].close + bars[j - 1].close + bars[j - 2].close + bars[j - 3].close + bars[j - 4].close) / 5;
      if (!broke) { if (bars[j].close < ma5) broke = true; continue; }
      const ma5p = (bars[j - 1].close + bars[j - 2].close + bars[j - 3].close + bars[j - 4].close + bars[j - 5].close) / 5;
      if (bars[j].close > ma5 && ma5 > ma5p) return j;
    }
    return -1;
  }
  if (rule === 'R4') {
    let quiet = 0;
    for (let j = i + 1; j <= Math.min(i + 20, n - 1); j++) {
      const b = bars[j];
      if (b.vol <= V * 0.6) { quiet++; continue; }
      if (quiet >= 3) {
        let s = 0;
        for (let k = j - 5; k < j; k++) s += bars[k].vol;
        if (b.vol >= (s / 5) * 1.8 && b.close > b.open && b.close > bars[j - 1].close) return j;
      }
      quiet = 0;
    }
    return -1;
  }
  return -1;
}

const GRPS = ['all', 'strong', 'weak', 'ex', 'exwin'];
const MAIN = ['R1', 'R2', 'R3', 'R4', 'B_close', 'B_next'];
const rec = {};
for (const r of [...MAIN, 'R1_40', 'R1_60']) { rec[r] = {}; for (const g of GRPS) rec[r][g] = { list: [], cen: 0 }; }

function pad(s, n) {
  s = String(s);
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) > 255 ? 2 : 1;
  return s + ' '.repeat(Math.max(0, n - w));
}
const f1 = x => (x * 100).toFixed(1) + '%';

function pct(sorted, p) {
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}
function summ(list) {
  const n = list.length;
  if (!n) return null;
  const hs = list.map(r => r.h).sort((a, b) => a - b);
  const mean = hs.reduce((s, x) => s + x, 0) / n;
  const a = q => list.filter(r => r.h >= q).length / n;
  const ts = list.map(r => r.t).sort((a, b) => a - b);
  return {
    n, mean,
    med: pct(hs, 0.5), p75: pct(hs, 0.75), p90: pct(hs, 0.9),
    a10: a(0.10), a20: a(0.20), a30: a(0.30), a50: a(0.50),
    neg: list.filter(r => r.h < 0).length / n,
    medt: pct(ts, 0.5),
  };
}
function summC(list) {
  const n = list.length;
  if (!n) return null;
  const cs = list.map(r => r.c).sort((a, b) => a - b);
  return {
    n, med: pct(cs, 0.5), p75: pct(cs, 0.75), p90: pct(cs, 0.9),
    a20: list.filter(r => r.c >= 0.20).length / n, a30: list.filter(r => r.c >= 0.30).length / n,
  };
}

function main() {
  const names = buildNames();
  const idx = loadIndexMA20();
  const stocks = readBase();
  const cnt = { notstock: 0, cap0: 0, nofile: 0, short: 0, ex: 0, exwin: 0, dup: 0, noidx: 0, lu: 0 };
  let samples = 0;
  for (const s of stocks) {
    if (!isStock(s.mkt, s.code)) { cnt.notstock++; continue; }
    if (s.ltag <= 0) { cnt.cap0++; continue; }
    const dir = MKTS[s.mkt];
    let bars;
    try { bars = readDay(path.join(VIP, dir, 'lday', dir + s.code + '.day')); } catch (e) { cnt.nofile++; continue; }
    const n = bars.length;
    if (n < 280) { cnt.short++; continue; }
    const cap = s.ltag * 10000;
    let pm = -1;
    const days = [];
    for (let i = 0; i < n; i++) {
      const h = bars[i].vol / cap * 100;
      const isNew = h > pm;
      if (h > pm) pm = h;
      if (isNew && i >= 60 && i <= n - 2) days.push(i);
    }
    const picks = [];
    let lastPick = -100;
    for (const i of days) {
      if (i - lastPick <= 10) { cnt.dup++; continue; }
      picks.push(i); lastPick = i;
    }
    for (const i of picks) {
      let ex = false;
      for (let j = Math.max(1, i - 59); j <= i; j++) {
        if (bars[j].open < bars[j - 1].close * 0.85) { ex = true; break; }
      }
      if (ex) cnt.ex++;
      let windowEx = false;
      for (let j = i + 1; j <= Math.min(i + W, n - 1); j++) {
        if (bars[j].open < bars[j - 1].close * 0.85 && bars[j].vol > bars[j - 1].vol * 0.2) { windowEx = true; break; }
      }
      if (!ex && windowEx) cnt.exwin++;
      let envGrp = null;
      if (!ex) {
        const id = idx ? idx.get(bars[i].date) : undefined;
        if (id && id.ma) envGrp = id.close > id.ma ? 'strong' : 'weak'; else cnt.noidx++;
      }
      const groups = ex ? ['ex'] : windowEx ? ['exwin'] : (envGrp ? ['all', envGrp] : ['all']);
      const feed = (rule, entryBar, startJ, entry) => {
        const end = entryBar + W;
        if (end > n - 1) { for (const g of groups) rec[rule][g].cen++; return; }
        let mh = -Infinity, mc = -Infinity, mt = 0;
        for (let j = startJ; j <= end; j++) {
          if (bars[j].high > mh) { mh = bars[j].high; mt = j - entryBar; }
          if (bars[j].close > mc) mc = bars[j].close;
        }
        const item = { y: Math.floor(bars[i].date / 10000), h: mh / entry - 1, c: mc / entry - 1, t: mt };
        for (const g of groups) rec[rule][g].list.push(item);
      };
      if (!isLimitUpClose(bars, i, s.code)) feed('B_close', i, i + 1, bars[i].close); else cnt.lu++;
      if (!isLimitUpOpen(bars, i + 1, s.code)) feed('B_next', i + 1, i + 1, bars[i + 1].open);
      for (const rule of ['R1', 'R2', 'R3', 'R4', 'R1_40', 'R1_60']) {
        const t = findTrigger(rule, bars, i, n);
        if (t < 0) continue;
        if (isLimitUpClose(bars, t, s.code)) { cnt.lu++; continue; }
        feed(rule, t, t + 1, bars[t].close);
      }
      if (!ex && !windowEx) samples++;
    }
  }

  console.log(`[样本] 干净天量日事件=${samples}（换手率严格创历史新高、10根内去重、天量日前60根及入场后120日内均无除权缺口）`);
  console.log(`[剔除/隔离] 天量日前除权=${cnt.ex} 窗口内除权=${cnt.exwin} 连续新高去重=${cnt.dup} 买入日涨停不可成交=${cnt.lu} 指数对齐缺失=${cnt.noidx}`);
  console.log(`[删失] 天量日后不足120个交易日(不计入分布): ` + MAIN.map(r => `${r}=${rec[r].all.cen}`).join(' '));

  console.log('\n表1 120日内最高涨幅分布（最高价口径=理想出场；入场价: B*=天量日收盘/次日开盘, R*=触发日收盘）:');
  console.log(pad('规则', 9) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('均值', 9) + pad('≥10%', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('≥50%', 9) + pad('全程未盈利', 11) + pad('中位达峰', 9));
  for (const r of MAIN) {
    const u = summ(rec[r].all.list);
    if (!u) { console.log(pad(r, 9) + pad('-', 8)); continue; }
    console.log(pad(r, 9) + pad(u.n, 8) + pad(f1(u.med), 9) + pad(f1(u.p75), 9) + pad(f1(u.p90), 9) + pad(f1(u.mean), 9) + pad(f1(u.a10), 9) + pad(f1(u.a20), 9) + pad(f1(u.a30), 9) + pad(f1(u.a50), 9) + pad(f1(u.neg), 11) + pad(u.medt + '日', 9));
  }

  console.log('\n表2 收盘价口径（最高收盘=可实现性更强的出场代理，不需精确卖在最高点）:');
  console.log(pad('规则', 9) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('≥20%', 9) + pad('≥30%', 9));
  for (const r of MAIN) {
    const u = summC(rec[r].all.list);
    if (!u) { console.log(pad(r, 9) + pad('-', 8)); continue; }
    console.log(pad(r, 9) + pad(u.n, 8) + pad(f1(u.med), 9) + pad(f1(u.p75), 9) + pad(f1(u.p90), 9) + pad(f1(u.a20), 9) + pad(f1(u.a30), 9));
  }

  console.log('\n表3 环境分组（天量日当日上证 vs MA20；格=中位|≥20%，最高价口径）:');
  for (const g of ['all', 'strong', 'weak']) {
    console.log(`-- ${g === 'all' ? '全样本' : g === 'strong' ? '上证在MA20上方' : '上证在MA20下方'} --`);
    console.log(pad('规则', 9) + pad('n', 8) + pad('中位', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('全程未盈利', 11));
    for (const r of MAIN) {
      const u = summ(rec[r][g].list);
      if (!u) { console.log(pad(r, 9) + pad('-', 8)); continue; }
      console.log(pad(r, 9) + pad(u.n, 8) + pad(f1(u.med), 9) + pad(f1(u.a20), 9) + pad(f1(u.a30), 9) + pad(f1(u.neg), 11));
    }
  }

  console.log('\n表4 窗口内除权失真影响（不复权最高价会被除权压低，验证剔除必要性；格=中位|≥20%）:');
  console.log(pad('规则', 9) + pad('干净n', 9) + pad('干净中位|≥20%', 20) + pad('窗口除权n', 10) + pad('除权中位|≥20%', 20));
  for (const r of MAIN) {
    const a = summ(rec[r].all.list), e = summ(rec[r].exwin.list);
    console.log(pad(r, 9) + pad(a ? a.n : '-', 9) + pad(a ? `${f1(a.med)}|${f1(a.a20)}` : '-', 20) + pad(e ? e.n : '-', 10) + pad(e ? `${f1(e.med)}|${f1(e.a20)}` : '-', 20));
  }

  const YEARS = [2021, 2022, 2023, 2024, 2025, 2026];
  console.log('\n表5 年度稳健性（R1/R3/B_close 按天量日年份；格=n|中位|≥20%）:');
  console.log(pad('规则', 9) + YEARS.map(y => pad(String(y), 20)).join(''));
  for (const r of ['R1', 'R3', 'B_close']) {
    const cells = [pad(r, 9)];
    for (const y of YEARS) {
      const u = summ(rec[r].all.list.filter(x => x.y === y));
      cells.push(pad(u ? `${u.n}|${f1(u.med)}|${f1(u.a20)}` : '-', 20));
    }
    console.log(cells.join(''));
  }

  console.log('\n表6 R1缩量阈值参数平台（检验参数稳健性；格=中位|P90|≥20%|≥30%）:');
  for (const [r, label] of [['R1_40', 'R1@量≤40%天量'], ['R1', 'R1@量≤50%天量'], ['R1_60', 'R1@量≤60%天量']]) {
    const u = summ(rec[r].all.list);
    console.log(pad(label, 15) + (u ? pad(u.n, 8) + pad(f1(u.med), 9) + pad(f1(u.p90), 9) + pad(f1(u.a20), 9) + pad(f1(u.a30), 9) : pad('-', 8)));
  }

  console.log('\n表7 达峰时间分布（窗口内最高价出现在入场后第几个交易日，占比）:');
  console.log(pad('规则', 9) + pad('≤5日', 10) + pad('6~20日', 10) + pad('21~60日', 10) + pad('61~120日', 10));
  for (const r of ['R1', 'R3', 'B_close']) {
    const L = rec[r].all.list;
    const n = L.length;
    if (!n) { console.log(pad(r, 9) + pad('-', 10)); continue; }
    const b = q => L.filter(x => x.t <= q).length / n;
    console.log(pad(r, 9) + pad(f1(b(5)), 10) + pad(f1(L.filter(x => x.t > 5 && x.t <= 20).length / n), 10) + pad(f1(L.filter(x => x.t > 20 && x.t <= 60).length / n), 10) + pad(f1(L.filter(x => x.t > 60).length / n), 10));
  }
}

try { main(); } catch (e) { console.error('FATAL', e); process.exit(1); }
