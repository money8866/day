const fs = require('fs');
const path = require('path');
const HQ = path.join('c:/new_tdx', 'T0002', 'hq_cache');

let gbk = null;
try { gbk = new TextDecoder('gbk'); } catch (e) { console.log('TextDecoder gbk NOT available'); }
console.log('gbk available:', !!gbk);
const dec = b => { try { return gbk.decode(b); } catch (e) { return '<err>'; } };

function allPos(buf, s) {
  const needle = Buffer.from(s, 'latin1');
  const out = [];
  let i = 0;
  while ((i = buf.indexOf(needle, i)) !== -1) { out.push(i); i += 1; }
  return out;
}

function hexdump(buf, p, len) {
  for (let r = 0; r < len; r += 16) {
    const row = [];
    for (let j = 0; j < 16 && r + j < len; j++) row.push(buf[p + r + j].toString(16).padStart(2, '0'));
    console.log((p + r).toString().padStart(9), row.join(' '), '|', dec(buf.slice(p + r, Math.min(p + r + 16, p + len))).replace(/[\0\-\x00-\x1f]/g, '.'));
  }
}

function probe(fn, code) {
  console.log('\n=====', fn, 'code', code);
  const buf = fs.readFileSync(path.join(HQ, fn));
  const ps = allPos(buf, code);
  console.log('all positions:', ps.length, ps.slice(0, 12));
  for (const p of ps.slice(0, 4)) {
    console.log(`-- pos ${p} aligned=${((p - 50) % 360) === 0}`);
    hexdump(buf, p, 80);
    for (const r of [20, 24, 25, 26, 28, 30, 31, 32, 34, 40]) {
      const t = dec(buf.slice(p + r, p + r + 16)).replace(/\0/g, '').trim();
      if (/[\u4e00-\u9fa5]/.test(t)) console.log(`   off+${r}: "${t}"`);
    }
  }
}

probe('shs.tnf', '600519');
probe('szs.tnf', '000001');
probe('szs.tnf', '000802');
probe('bjs.tnf', '920000');
