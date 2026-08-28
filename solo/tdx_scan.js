const fs = require('fs');
const path = require('path');

const TDX = 'c:/new_tdx';
const HQ = path.join(TDX, 'T0002', 'hq_cache');
const VIP = path.join(TDX, 'vipdoc');
const TODAY = 20260828;
const CAL_DAYS = 30;
const MA_N = 250;
const MKTS = ['sz', 'sh', 'bj'];

let gbkDec = null;
try { gbkDec = new TextDecoder('gbk'); } catch (e) {}
const decName = b => (gbkDec ? gbkDec.decode(b) : '').replace(/\0/g, '').trim();

function ymd(d) { return d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate(); }
const winStart = ymd(new Date(Date.UTC(2026, 7, 28) - CAL_DAYS * 86400000));

function findCodePositions(buf, code) {
  const needle = Buffer.from(code, 'latin1');
  const out = [];
  let i = 0;
  while ((i = buf.indexOf(needle, i)) !== -1) { out.push(i); i += 1; }
  return out;
}

function detectNameOffset() {
  const probes = [
    ['shs.tnf', '600519', Buffer.from([0xB9, 0xF3, 0xD6, 0xDD, 0xC3, 0xA9, 0xCC, 0xA8])],
    ['szs.tnf', '000001', Buffer.from([0xC6, 0xBD, 0xB0, 0xB2, 0xD2, 0xF8, 0xD0, 0xD0])],
  ];
  const hits = [];
  for (const [fn, code, nameBytes] of probes) {
    const buf = fs.readFileSync(path.join(HQ, fn));
    for (const p of findCodePositions(buf, code)) {
      if ((p - 50) % 360 !== 0) continue;
      const rel = buf.slice(p, p + 360).indexOf(nameBytes);
      if (rel >= 0) hits.push({ fn, rel });
    }
  }
  if (hits.length) {
    const rel = hits[0].rel;
    if (hits.every(h => h.rel === rel)) return { off: rel, how: 'probe' };
  }
  const buf = fs.readFileSync(path.join(HQ, 'shs.tnf'));
  for (const p of findCodePositions(buf, '600519')) {
    if ((p - 50) % 360 !== 0) continue;
    for (let r = 12; r < 120; r++) {
      const b = buf[p + r], b2 = buf[p + r + 1];
      if (b >= 0xB0 && b <= 0xF7 && b2 >= 0x40 && b2 <= 0xFE && b2 !== 0x7F) return { off: r, how: 'fallback' };
    }
  }
  return { off: -1, how: 'none' };
}

function buildNames(nameOff) {
  const map = new Map();
  if (nameOff < 0) return map;
  for (const [fn, mkt] of [['shs.tnf', 'sh'], ['szs.tnf', 'sz'], ['bjs.tnf', 'bj']]) {
    const buf = fs.readFileSync(path.join(HQ, fn));
    const count = Math.floor((buf.length - 50) / 360);
    for (let i = 0; i < count; i++) {
      const p = 50 + i * 360;
      const code = buf.slice(p, p + 6).toString('latin1');
      if (!/^\d{6}$/.test(code)) continue;
      const name = decName(buf.slice(p + nameOff, p + nameOff + 16));
      if (name) map.set(mkt + code, name);
    }
  }
  return map;
}

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
    out.push({
      mkt: +get(i, 'SC'),
      code,
      ltag: parseFloat(get(i, 'LTAG')) || 0,
      hy: get(i, 'HY'),
    });
  }
  return out;
}

function pad(s, n) {
  s = String(s);
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) > 255 ? 2 : 1;
  return s + ' '.repeat(Math.max(0, n - w));
}
const f2 = x => x.toFixed(2);

function main() {
  const det = detectNameOffset();
  const names = buildNames(det.off);
  console.log(`[name-offset] off=${det.off} how=${det.how} 样本: sh600519=${names.get('sh600519')} sz000001=${names.get('sz000001')} sz300750=${names.get('sz300750')} sh688981=${names.get('sh688981')}`);
  const stocks = readBase();
  const mktCount = { 0: 0, 1: 0, 2: 0 };
  const skip = { notstock: 0, cap0: 0, nofile: 0, short: 0, stale: 0 };
  const results = [];
  for (const s of stocks) {
    if (!isStock(s.mkt, s.code)) { skip.notstock++; continue; }
    mktCount[s.mkt]++;
    if (s.ltag <= 0) { skip.cap0++; continue; }
    const dir = MKTS[s.mkt];
    const f = path.join(VIP, dir, 'lday', dir + s.code + '.day');
    let bars;
    try { bars = readDay(f); } catch (e) { skip.nofile++; continue; }
    const n = bars.length;
    if (n < MA_N) { skip.short++; continue; }
    if (bars[n - 1].date !== TODAY) { skip.stale++; continue; }
    const cap = s.ltag * 10000;
    let pm = -1;
    let lastAth = -1, athCount30 = 0;
    for (let i = 0; i < n; i++) {
      const h = bars[i].vol / cap * 100;
      if (h > pm) {
        pm = h;
        if (bars[i].date >= winStart) { lastAth = i; athCount30++; }
      }
    }
    if (lastAth < 0) continue;
    let exRightDate = 0;
    for (let i = n - 1; i >= Math.max(1, n - 60); i--) {
      if (bars[i].open < bars[i - 1].close * 0.85) { exRightDate = bars[i].date; break; }
    }
    const last = bars[n - 1];
    let ma = 0;
    for (let i = n - MA_N; i < n; i++) ma += bars[i].close;
    ma /= MA_N;
    if (!(last.close < ma)) continue;
    if (!(last.close > last.open)) continue;
    const prevClose = bars[n - 2].close;
    results.push({
      mkt: dir, code: s.code, name: names.get(dir + s.code) || '',
      lastDate: last.date, close: last.close,
      chg: (last.close / prevClose - 1) * 100,
      todayHsl: last.vol / cap * 100,
      athDate: bars[lastAth].date,
      athHsl: bars[lastAth].vol / cap * 100,
      athAgo: n - 1 - lastAth,
      athCount30, ma250: ma,
      dev: (last.close / ma - 1) * 100,
      exRightDate, bars: n,
    });
  }
  results.sort((a, b) => b.todayHsl - a.todayHsl);
  console.log(`\n[市场覆盖] sz=${mktCount[0]} sh=${mktCount[1]} bj=${mktCount[2]}`);
  console.log(`[剔除] 非A股代码=${skip.notstock} 流通股本为0=${skip.cap0} 无.day文件=${skip.nofile} K线不足250根=${skip.short} 当日无K线(停牌/未更新)=${skip.stale}`);
  console.log(`[窗口] 今日=${TODAY} 创新高窗口=${winStart}~${TODAY}(${CAL_DAYS}自然日) MA=${MA_N}日 历史新高=严格超过此前全部本地K线(${results.length ? Math.min(...results.map(r => r.bars)) : '??'}~根内)`);
  console.log(`\n命中 ${results.length} 只：`);
  console.log(pad('市场', 5) + pad('代码', 8) + pad('名称', 11) + pad('收盘', 9) + pad('涨跌%', 8) + pad('当日换手%', 10) + pad('ATH日', 10) + pad('ATH换手%', 10) + pad('ATH距今', 9) + pad('30日新高数', 11) + pad('MA250', 10) + pad('偏离MA%', 10) + pad('疑似除权日', 11) + 'K线数');
  for (const r of results) {
    console.log(pad(r.mkt, 5) + pad(r.code, 8) + pad(r.name, 11) + pad(f2(r.close), 9) + pad(f2(r.chg), 8) + pad(f2(r.todayHsl), 10) + pad(r.athDate, 10) + pad(f2(r.athHsl), 10) + pad(r.athAgo + '日', 9) + pad(r.athCount30, 11) + pad(f2(r.ma250), 10) + pad(f2(r.dev), 10) + pad(r.exRightDate || '-', 11) + r.bars);
  }
}

try { main(); } catch (e) { console.error('FATAL', e); process.exit(1); }