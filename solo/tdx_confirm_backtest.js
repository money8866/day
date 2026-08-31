const fs = require('fs');
const path = require('path');

const TDX = 'c:/new_tdx';
const HQ = path.join(TDX, 'T0002', 'hq_cache');
const VIP = path.join(TDX, 'vipdoc');
const MKTS = ['sz', 'sh', 'bj'];
const KS = [1, 3, 5, 10, 20];
const LOOKFWD = 20;

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
    out.push({ mkt: +get(i, 'SC'), code, ltag: parseFloat(get(i, 'LTAG')) || 0, hy: get(i, 'HY') });
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
  const H = bars[i].high, L = bars[i].low, C = bars[i].close, V = bars[i].vol;
  if (rule === 'R1') {
    for (let j = i + 1; j <= Math.min(i + 20, n - 1); j++) {
      const b = bars[j];
      if (b.close > b.open && b.vol <= V * 0.5 && b.close < C && b.close > L) return j;
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

const GRPS = ['all', 'strong', 'weak', 'ex'];
const RULES = ['R1', 'R2', 'R3', 'R4', 'B_close', 'B_next'];
const stat = {};
for (const r of RULES) { stat[r] = {}; for (const g of GRPS) { stat[r][g] = {}; for (const k of KS) stat[r][g][k] = { n: 0, win: 0, sum: 0, rets: [], winSum: 0, loseSum: 0 }; } }
function addStat(rule, grp, k, ret) {
  const b = stat[rule][grp][k];
  b.n++; b.sum += ret; b.rets.push(ret);
  if (ret > 0) { b.win++; b.winSum += ret; } else b.loseSum += ret;
}

function pad(s, n) {
  s = String(s);
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) > 255 ? 2 : 1;
  return s + ' '.repeat(Math.max(0, n - w));
}
const f1 = x => (x * 100).toFixed(1) + '%';
const f2 = x => x.toFixed(2);

function main() {
  const names = buildNames();
  const idx = loadIndexMA20();
  const stocks = readBase();
  const TODAY = 20260828, WIN = 20260729;
  const cnt = { notstock: 0, cap0: 0, nofile: 0, short: 0, ex: 0, dup: 0, noidx: 0, lu: 0 };
  let samples = 0;
  const live = [];
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
      let envGrp = null;
      if (!ex) {
        const id = idx ? idx.get(bars[i].date) : undefined;
        if (id && id.ma) envGrp = id.close > id.ma ? 'strong' : 'weak'; else cnt.noidx++;
      }
      const groups = ex ? ['ex'] : (envGrp ? ['all', envGrp] : ['all']);
      const feed = (rule, t, entry) => {
        for (const k of KS) {
          if (t + k > n - 1) continue;
          const ret = bars[t + k].close / entry - 1;
          for (const g of groups) addStat(rule, g, k, ret);
        }
      };
      if (!isLimitUpClose(bars, i, s.code)) feed('B_close', i, bars[i].close); else cnt.lu++;
      if (!isLimitUpOpen(bars, i + 1, s.code)) feed('B_next', i + 1, bars[i + 1].open);
      for (const rule of ['R1', 'R2', 'R3', 'R4']) {
        const t = findTrigger(rule, bars, i, n);
        if (t < 0) continue;
        if (isLimitUpClose(bars, t, s.code)) { cnt.lu++; continue; }
        feed(rule, t, bars[t].close);
      }
      if (!ex) samples++;
      if (!ex && bars[n - 1].date === TODAY && bars[i].date >= WIN && n >= 250) {
        const last = bars[n - 1];
        let ma = 0;
        for (let k = n - 250; k < n; k++) ma += bars[k].close;
        ma /= 250;
        if (last.close < ma && last.close > last.open) {
          const st = {};
          for (const rule of ['R1', 'R2', 'R3', 'R4']) {
            const t = findTrigger(rule, bars, i, n);
            st[rule] = t >= 0 ? { date: bars[t].date, entry: bars[t].close } : null;
          }
          live.push({ code: s.code, name: names.get(dir + s.code) || '', i, st, bars, ma });
        }
      }
    }
  }

  console.log(`[样本] 干净天量日样本=${samples}（换手率严格创本地历史新高、连续新高10根内去重、天量日前60根无除权缺口）`);
  console.log(`[剔除] 除权失真样本=${cnt.ex} 连续新高去重=${cnt.dup} 买入日涨停不可成交=${cnt.lu} 指数对齐缺失=${cnt.noidx}`);
  console.log('\n表1 全部干净样本（格=样本数|胜率|平均收益%）:');
  console.log(pad('规则', 9) + KS.map(k => pad(k + '日', 17)).join(''));
  for (const r of RULES) {
    const cells = [pad(r, 9)];
    for (const k of KS) {
      const b = stat[r].all[k];
      cells.push(pad(b.n ? `${b.n}|${f1(b.win / b.n)}|${(b.sum / b.n * 100).toFixed(1)}` : '-', 17));
    }
    console.log(cells.join(''));
  }
  console.log('\n表2 明细（10日/20日 格=胜率|均值%|中位%|盈亏比）:');
  for (const g of ['all', 'strong', 'weak']) {
    console.log(`-- ${g === 'all' ? '全样本' : g === 'strong' ? '天量日时上证在MA20上方' : '天量日时上证在MA20下方'} --`);
    console.log(pad('规则', 9) + pad('10日', 32) + pad('20日', 32));
    for (const r of RULES) {
      const cells = [pad(r, 9)];
      for (const k of [10, 20]) {
        const b = stat[r][g][k];
        if (!b.n) { cells.push(pad('-', 32)); continue; }
        const sorted = b.rets.slice().sort((a, b2) => a - b2);
        const med = sorted[Math.floor(sorted.length / 2)];
        const wn = b.win, ln = b.n - b.win;
        const pl = wn > 0 && ln > 0 ? (b.winSum / wn) / Math.abs(b.loseSum / ln) : Infinity;
        cells.push(pad(`${f1(b.win / b.n)}|${(b.sum / b.n * 100).toFixed(1)}|${(med * 100).toFixed(1)}|${isFinite(pl) ? pl.toFixed(2) : 'INF'}`, 32));
      }
      console.log(cells.join(''));
    }
  }
  console.log('\n表3 除权失真样本 vs 干净样本（10日胜率对比，验证失真危害）:');
  for (const r of RULES) {
    const a = stat[r].all[10], e = stat[r].ex[10];
    console.log(pad(r, 9) + pad(`干净:${a.n ? f1(a.win / a.n) : '-'}`, 14) + pad(`除权:${e.n ? f1(e.win / e.n) : '-'}`, 14));
  }

  const byCode = new Map();
  for (const v of live) { const p = byCode.get(v.code); if (!p || v.i > p.i) byCode.set(v.code, v); }
  const lv = [...byCode.values()];
  console.log(`\n表4 当前窗口天量票(${lv.length}只)的确认买点状态（✓=已触发@日期(入场价) / 待=观察中）:`);
  console.log(pad('代码', 8) + pad('名称', 11) + pad('天量日', 10) + pad('天量收', 8) + pad('天量高', 8) + pad('天量低', 8) + pad('现价', 8) + pad('R1', 20) + pad('R2', 20) + pad('R3', 18) + pad('R4', 18));
  for (const v of lv) {
    const H = v.bars[v.i].high, L = v.bars[v.i].low, C = v.bars[v.i].close;
    const last = v.bars[v.bars.length - 1];
    const st = r => {
      const x = v.st[r];
      return x ? `✓${x.date}(${f2(x.entry)})` : '待';
    };
    console.log(pad(v.code, 8) + pad(v.name, 11) + pad(v.bars[v.i].date, 10) + pad(f2(C), 8) + pad(f2(H), 8) + pad(f2(L), 8) + pad(f2(last.close), 8) + pad(st('R1'), 20) + pad(st('R2'), 20) + pad(st('R3'), 18) + pad(st('R4'), 18));
  }
}

try { main(); } catch (e) { console.error('FATAL', e); process.exit(1); }
