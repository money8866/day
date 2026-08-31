# -*- coding: utf-8 -*-
"""解析 MCP K线输出并输出关键时间线"""
import re, sys, datetime

def load_klines(path):
    raw = open(path, encoding='utf-8').read().replace('\\"', '"')
    pat = re.compile(r'\{"date_ms":(\d+),"volume":([\d.eE+-]+),"turnover":([\d.eE+-]+),"open_price":([\d.eE+-]+),"high_price":([\d.eE+-]+),"low_price":([\d.eE+-]+),"close_price":([\d.eE+-]+)\}')
    rows = []
    for m in pat.finditer(raw):
        rows.append({'date': int(m.group(1)), 'vol': float(m.group(2)),
                     'open': float(m.group(4)), 'high': float(m.group(5)),
                     'low': float(m.group(6)), 'close': float(m.group(7))})
    rows.sort(key=lambda r: r['date'])
    return rows

def fmt(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d')

def main():
    path, label = sys.argv[1], sys.argv[2]
    rows = load_klines(path)
    print(f"===== {label} =====")
    print(f"范围 {fmt(rows[0]['date'])} ~ {fmt(rows[-1]['date'])}, 共{len(rows)}根")
    # 52周高低点及日期
    w52 = rows[-252:]
    h = max(w52, key=lambda r: r['high']); l = min(w52, key=lambda r: r['low'])
    print(f"52周最高价 {h['high']:.2f} @ {fmt(h['date'])}")
    print(f"52周最低价 {l['low']:.2f} @ {fmt(l['date'])}")
    print(f"最新收盘 {rows[-1]['close']:.2f} @ {fmt(rows[-1]['date'])}")
    # 2025年内高低
    for year in ('2025', '2026'):
        seg = [r for r in rows if fmt(r['date'])[:4] == year]
        if not seg:
            continue
        c0 = seg[0]['open']
        c1 = seg[-1]['close']
        print(f"{year}年: 开{c0:.2f} 末{c1:.2f} 年内{ (c1/c0-1)*100:+.1f}%  "
              f"高{max(r['high'] for r in seg):.2f}@{fmt(max(seg, key=lambda r: r['high'])['date'])} "
              f"低{min(r['low'] for r in seg):.2f}@{fmt(min(seg, key=lambda r: r['low'])['date'])}")
    # 关键时间点
    last = rows[-1]['close']
    for ds in ('2026-01-02', '2026-03-02', '2026-05-30', '2026-06-30', '2026-07-31'):
        near = [r for r in rows if fmt(r['date']) >= ds]
        if near:
            r = near[0]
            print(f"  {ds} 最近交易日 {fmt(r['date'])} 收盘 {r['close']:.2f}")
    # 近20日逐日收盘（看回调节奏）
    print("近15日: " + " ".join(f"{fmt(r['date'])[5:]}={r['close']:.1f}" for r in rows[-15:]))
    # 从低点涨幅
    lw = min(w52, key=lambda r: r['low'])
    print(f"从52周低点 {lw['low']:.2f} 至今: {(last/lw['low']-1)*100:+.1f}%")

if __name__ == '__main__':
    main()
