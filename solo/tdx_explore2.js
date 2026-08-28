const fs = require('fs');

// tnf files: find code/name offsets and record stride
for (const f of ['shs.tnf', 'szs.tnf', 'bjs.tnf']) {
  const b = fs.readFileSync('c:/new_tdx/T0002/hq_cache/' + f);
  console.log('====', f, 'size', b.length);
  console.log('head hex:', b.slice(0, 64).toString('hex'));
  const findCode = code => {
    const pat = Buffer.from(code);
    const pos = [];
    let idx = b.indexOf(pat);
    while (idx >= 0 && pos.length < 8) { pos.push(idx); idx = b.indexOf(pat, idx + 1); }
    return pos;
  };
  const probes = f === 'shs.tnf' ? ['600516', '600519', '600520', '600522']
    : f === 'szs.tnf' ? ['000001', '000002', '000003', '300750']
    : ['920000', '920001', '920002'];
  const positions = {};
  for (const c of probes) positions[c] = findCode(c);
  console.log('code positions:', JSON.stringify(positions));
  const diffs = [];
  const ks = Object.keys(positions).filter(k => positions[k].length);
  for (let i = 1; i < ks.length; i++) {
    if (positions[ks[i]][0] && positions[ks[i - 1]][0]) diffs.push(positions[ks[i]][0] - positions[ks[i - 1]][0]);
  }
  console.log('first-pos diffs:', diffs.join(','));
  if (f === 'shs.tnf') {
    const nm = Buffer.from([0xB9, 0xF9, 0xD6, 0xDD, 0xD6, 0xA9, 0xCC, 0xA9]);
    const npos = [];
    let j = b.indexOf(nm);
    while (j >= 0 && npos.length < 5) { npos.push(j); j = b.indexOf(nm, j + 1); }
    console.log('GBK 贵州茅台 at:', npos, ' rel-to-code600519:', positions['600519'].map(p => npos.map(n => n - p)));
  }
}

// root base.dbf quick dump
const r = fs.readFileSync('c:/new_tdx/base.dbf');
console.log('==== root base.dbf size', r.length, 'recs', r.readUInt32LE(4), 'hsize', r.readUInt16LE(8), 'rsize', r.readUInt16LE(10));
const fields = [];
let off = 32;
while (r[off] !== 0x0d && off < r.readUInt16LE(8)) {
  fields.push({
    name: r.slice(off, off + 11).toString('ascii').replace(/\0.*$/, ''),
    type: String.fromCharCode(r[off + 11]),
    len: r[off + 16],
  });
  off += 32;
}
console.log('root base.dbf fields:', JSON.stringify(fields));
