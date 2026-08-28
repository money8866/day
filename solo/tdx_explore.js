const fs = require('fs');

// ---------- 1. base.dbf ----------
function dumpDbf(file, samples) {
  const buf = fs.readFileSync(file);
  const nRec = buf.readUInt32LE(4);
  const hSize = buf.readUInt16LE(8);
  const rSize = buf.readUInt16LE(10);
  console.log('DBF', file, 'ver', buf[0], 'recs', nRec, 'hsize', hSize, 'rsize', rSize, 'expected', hSize + nRec * rSize, 'actual', buf.length);
  const fields = [];
  let off = 32;
  while (buf[off] !== 0x0d) {
    const name = buf.slice(off, off + 11).toString('ascii').replace(/\0.*$/, '');
    const type = String.fromCharCode(buf[off + 11]);
    const len = buf[off + 16];
    const dec = buf[off + 17];
    fields.push({ name, type, len, dec });
    off += 32;
  }
  console.log('fields:', JSON.stringify(fields));
  const recs = [];
  for (let i = 0; i < nRec; i++) {
    const ro = hSize + i * rSize;
    const rec = {};
    let fo = ro + 1;
    for (const f of fields) {
      rec[f.name] = buf.slice(fo, fo + f.len).toString('latin1').trim();
      fo += f.len;
    }
    recs.push(rec);
  }
  console.log('first rec:', JSON.stringify(recs[0]));
  for (const code of samples) {
    const hit = recs.find(r => Object.values(r).some(v => typeof v === 'string' && v.includes(code)));
    console.log('sample', code, JSON.stringify(hit || null));
  }
}
dumpDbf('c:/new_tdx/T0002/hq_cache/base.dbf', ['600519', '000001', '300750', '920000']);

// ---------- 2. .day ----------
function dumpDay(file) {
  const buf = fs.readFileSync(file);
  const n = buf.length / 32;
  console.log('DAY', file, 'bytes', buf.length, 'records', n);
  const get = i => {
    const o = i * 32;
    return {
      date: buf.readUInt32LE(o),
      open: buf.readUInt32LE(o + 4) / 100,
      high: buf.readUInt32LE(o + 8) / 100,
      low: buf.readUInt32LE(o + 12) / 100,
      close: buf.readUInt32LE(o + 16) / 100,
      amount: buf.readFloatLE(o + 20),
      vol: buf.readUInt32LE(o + 24),
      extra: buf.readUInt32LE(o + 28),
    };
  };
  console.log('first', JSON.stringify(get(0)));
  for (let i = Math.max(0, n - 3); i < n; i++) {
    const d = get(i);
    const vwap = d.vol ? d.amount / d.vol : 0;
    console.log(JSON.stringify(d), 'vwap=', vwap.toFixed(2), 'inRange=', vwap >= d.low * 0.99 && vwap <= d.high * 1.01);
  }
}
dumpDay('c:/new_tdx/vipdoc/sh/lday/sh600519.day');

// ---------- 3. gbbq ----------
const g = fs.readFileSync('c:/new_tdx/T0002/hq_cache/gbbq');
console.log('gbbq size', g.length, 'per37', g.length / 37);
console.log('gbbq head hex:', g.slice(0, 74).toString('hex'));
const found = [];
for (let o = 0; o + 37 <= g.length; o += 37) {
  const code = g.slice(o + 1, o + 8).toString('ascii').replace(/\0/g, '');
  if (code === '600519') {
    found.push({
      o, mkt: g[o], code,
      date: g.readUInt32LE(o + 8), cat: g[o + 12],
      f1: g.readFloatLE(o + 13), f2: g.readFloatLE(o + 17), f3: g.readFloatLE(o + 21),
      f4: g.readFloatLE(o + 25), f5: g.readFloatLE(o + 29), f6: g.readFloatLE(o + 33),
    });
  }
}
console.log('600519 gbbq count', found.length);
if (found.length) {
  console.log('head:', JSON.stringify(found.slice(0, 3)));
  console.log('tail:', JSON.stringify(found.slice(-3)));
}
