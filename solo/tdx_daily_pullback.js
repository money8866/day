const fs = require('fs');
const path = require('path');

const TDX = 'c:/new_tdx';
const HQ = path.join(TDX, 'T0002', 'hq_cache');
const VIP = path.join(TDX, 'vipdoc');
const MKTS = ['sz', 'sh', 'bj'];
const VD_LOOKBACK = 30;
const SHRINK = 0.5;

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

function r1Triggers(bars, i, n) {
  const V = bars[i].vol, C = bars[i].close, L = bars[i].low;
  const out = [];
  for (let j = i + 1; j <= Math.min(i + 20, n - 1); j++) {
    const b = bars[j];
    if (b.close > b.open && b.vol <= V * SHRINK && b.close < C && b.close > L) out.push(j);
  }
  return out;
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
  const cnt = { notstock: 0, cap0: 0, nofile: 0, short: 0, susp: 0, novd: 0, stale: 0, ex: 0, brk: 0 };
  const tiers = { green: [], yellow: [], blue: [] };
  let todayDate = 0;
  try {
    const ib = readDay(path.join(VIP, 'sh', 'lday', 'sh999999.day'));
    if (ib.length) todayDate = ib[ib.length - 1].date;
  } catch (e) {}

  for (const s of stocks) {
    if (!isStock(s.mkt, s.code)) { cnt.notstock++; continue; }
    if (s.ltag <= 0) { cnt.cap0++; continue; }
    const dir = MKTS[s.mkt];
    let bars;
    try { bars = readDay(path.join(VIP, dir, 'lday', dir + s.code + '.day')); } catch (e) { cnt.nofile++; continue; }
    const n = bars.length;
    if (n < 280) { cnt.short++; continue; }
    if (todayDate && bars[n - 1].date !== todayDate) { cnt.susp++; continue; }

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
      if (i - lastPick <= 10) continue;
      picks.push(i); lastPick = i;
    }
    if (!picks.length) { cnt.novd++; continue; }
    const vd = picks[picks.length - 1];
    const age = n - 1 - vd;
    if (age > 20) { cnt.stale++; continue; }

    let ex = false;
    for (let j = Math.max(1, vd - 59); j <= vd; j++) {
      if (bars[j].open < bars[j - 1].close * 0.85) { ex = true; break; }
    }
    if (!ex) {
      for (let j = vd + 1; j < n; j++) {
        if (bars[j].open < bars[j - 1].close * 0.85 && bars[j].vol > bars[j - 1].vol * 0.2) { ex = true; break; }
      }
    }
    if (ex) { cnt.ex++; continue; }

    const vdV = bars[vd].vol, vdC = bars[vd].close, vdL = bars[vd].low;
    let broke = false;
    for (let j = vd + 1; j < n; j++) {
      if (bars[j].close < vdL) { broke = true; break; }
    }
    if (broke) { cnt.brk++; continue; }

    const last = bars[n - 1];
    const trig = r1Triggers(bars, vd, n);
    const t = trig.length ? trig[trig.length - 1] : -1;
    const item = {
      code: s.code,
      name: names.get(dir + s.code) || '',
      vdDate: bars[vd].date,
      age,
      hsl: vdV / cap,
      vdC, vdL,
      px: last.close,
      depth: last.close / vdC - 1,
      vr: last.vol / vdV,
      status: '',
      note: '',
    };
    if (t === n - 1) {
      item.status = '★今日确认';
      if (isLimitUpClose(bars, t, s.code)) item.note = '▲今日涨停难买';
      tiers.green.push(item);
    } else if (t >= n - 6) {
      item.status = `✓${n - 1 - t}日前确认`;
      item.note = (isLimitUpClose(bars, t, s.code) ? '▲确认日涨停@' : '确认@') + String(bars[t].date % 10000).padStart(4, '0');
      tiers.yellow.push(item);
    } else if (last.close >= vdL && last.close <= vdC) {
      item.status = '○观察区';
      item.note = t >= 0 ? '曾确认@' + String(bars[t].date % 10000).padStart(4, '0') : '待缩量收阳';
      tiers.blue.push(item);
    }
  }

  const sortVr = a => a.sort((x, y) => x.vr - y.vr);
  sortVr(tiers.green); sortVr(tiers.yellow); sortVr(tiers.blue);

  let envLine = '指数数据缺失';
  if (idx && idx.size) {
    let lastIdxDate = 0;
    for (const d of idx.keys()) if (d > lastIdxDate) lastIdxDate = d;
    const e = idx.get(lastIdxDate);
    if (e && e.ma) envLine = `上证 ${e.close.toFixed(2)} / MA20 ${e.ma.toFixed(2)} → ${e.close > e.ma ? '强势区' : '弱势区'}`;
  }

  console.log(`[每日天量回调选股] 数据截至 ${todayDate}`);
  console.log(`[环境] ${envLine}`);
  console.log('[口径] 天量=换手率创本地历史新高且距今≤20个交易日 | 观察区=现价∈[天量日低,天量日收] | 确认(R1)=缩量≤天量50%+收阳+收于区内 | 失效=收盘破天量日低 | 已剔除除权失真与停牌');
  console.log(`[扫描] 候选=${stocks.length - cnt.notstock - cnt.cap0} 无天量=${cnt.novd} 天量过旧(>20日)=${cnt.stale} 除权失真=${cnt.ex} 中途破位=${cnt.brk} 停牌=${cnt.susp} 数据不足=${cnt.short} 缺文件=${cnt.nofile}`);

  const printTier = (title, arr) => {
    console.log(`\n${title}：${arr.length}只（按量比升序，缩量越充分越靠前）`);
    if (!arr.length) { console.log('  （无）'); return; }
    console.log(pad('代码', 8) + pad('名称', 11) + pad('天量日', 10) + pad('距(日)', 7) + pad('天量换手', 9) + pad('天量收', 8) + pad('天量低', 8) + pad('现价', 8) + pad('深度', 8) + pad('量比', 7) + pad('状态', 14) + '备注');
    for (const v of arr) {
      console.log(pad(v.code, 8) + pad(v.name, 11) + pad(v.vdDate, 10) + pad(v.age, 7) + pad(f1(v.hsl), 9) + pad(f2(v.vdC), 8) + pad(f2(v.vdL), 8) + pad(f2(v.px), 8) + pad(f1(v.depth), 8) + pad(f2(v.vr), 7) + pad(v.status, 14) + v.note);
    }
  };
  printTier('★ 今日确认（R1今日触发·可执行）', tiers.green);
  printTier('✓ 近5日已确认（持有跟踪）', tiers.yellow);
  printTier('○ 回调观察区（待确认）', tiers.blue);

  console.log('\n[纪律] ①观察区票等触发再动手：缩量≤天量50%+收阳+收在天量日低点上方；②失效铁律：收盘跌破天量日低点无条件离场；③R1确认后120日回测：最高涨幅中位19.8%/P90 72.3%、≥20%概率49.6%、全程未盈利仅1.7%、达峰中位33日→分批止盈、给足耐心；④确认日涨停的次日勿追高开；⑤量比越小回调越充分，但确认必须靠阳线，防缩量阴跌。');
}

try { main(); } catch (e) { console.error('FATAL', e); process.exit(1); }
