# -*- coding: utf-8 -*-
"""实盘验证：今早(10-22)推演 vs 今日实际走势。数据源与 _trade_plan.py 一致（腾讯 m5/日K）。"""
import sys
sys.path.insert(0, "D:/mystock/lib")
import astock_data as A

DAY = "2026-10-22"
PLAN = {  # 今早推演的关键级别（来自 trade-plan-20261022）
    "sh": dict(name="上证指数", tx="sh000001", dl=3703.12, death=3674.82,
               a_lo=3696, a_hi=3719, b_lo=3667, b_hi=3703, b_lim=3667.7,
               t1=3719.0, mid5=3713.7),
    "cy": dict(name="创业板指", tx="sz399006", dl=2682.71, death=2682.71,
               a_lo=2682, a_hi=2737, b_lo=2647, b_hi=2682, b_lim=2647.8,
               t1=2738.0, mid5=2697.4),
}

for key, p in PLAN.items():
    m5 = A.tencent_kline(p["tx"], period="m5", count=320)
    m5 = m5[m5["datetime"].astype(str).str.startswith(DAY)].reset_index(drop=True)
    dk = A.tencent_kline(p["tx"], period="day", count=5)
    dk = dk[dk["date"].astype(str) == DAY]
    if not len(m5) or not len(dk):
        print(f"[{p['name']}] 数据不足 m5={len(m5)} day={len(dk)}")
        continue
    c = m5["close"].astype(float)
    lo = m5["low"].astype(float)
    hi = m5["high"].astype(float)
    tms = m5["datetime"].astype(str).str[-5:]
    close, high, low = float(dk.iloc[0]["close"]), float(dk.iloc[0]["high"]), float(dk.iloc[0]["low"])
    prev_close = float(dk.iloc[0]["open"])  # placeholder, recompute below
    # 09:45 窗口（±5 分钟）= 09:40-09:50
    win = (tms >= "09:40") & (tms <= "09:50")
    w_lo = float(lo[win].min()) if win.any() else None
    w_lo_t = tms[lo[win].idxmin()] if win.any() else "-"
    # 10:00 不再破：10:00 及之后 10 分钟内的最低 > 裁决线
    after = (tms >= "10:00") & (tms <= "10:10")
    a_lo = float(lo[after].min()) if after.any() else None
    print(f"===== {p['name']}（{DAY} 实际）=====")
    print(f"  开 {float(dk.iloc[0]['open']):.2f} | 高 {high:.2f} | 低 {low:.2f} | 收 {close:.2f}")
    print(f"  09:45 窗口最低 {w_lo:.2f}（{w_lo_t}） vs 裁决线 {p['dl']:.2f} → "
          f"{'已破' if w_lo < p['dl'] else '未破'}")
    if a_lo is not None:
        print(f"  10:00-10:10 最低 {a_lo:.2f} → 见底信号条件「10:00 不再破」"
              f"{'成立' if a_lo > p['dl'] else '不成立'}")
    print(f"  5MIN 中轨 {p['mid5']:.2f}：全天最高 {high:.2f} → "
          f"{'触及' if high >= p['mid5'] else '未触及'}（收复需收上，收盘 {close:.2f}）")
    print(f"  A路目标 {p['t1']:.2f}：{'已到' if high >= p['t1'] else '未到'}"
          f"（最高 {high:.2f}）")
    in_a = p["a_lo"] <= close <= p["a_hi"]
    in_b = p["b_lo"] <= close <= p["b_hi"]
    print(f"  收盘 {close:.2f} vs A路区间 {p['a_lo']}-{p['a_hi']} → {'命中' if in_a else '未命中'}"
          f"；vs B路区间 {p['b_lo']}-{p['b_hi']} → {'命中' if in_b else '未命中'}")
    if not in_b and close < p["b_lo"]:
        print(f"  ⚠ 收盘低于 B路区间下沿 {p['b_lo']} 达 {p['b_lo']-close:.1f} 点"
              f"（B路极限 {p['b_lim']} 亦被击穿 {p['b_lim']-close:.1f} 点）")
    print(f"  裁决线 {p['dl']:.2f} / 生死线 {p['death']:.2f}："
          f"{'双双失守' if close < min(p['dl'], p['death']) else '守住其一' if close < max(p['dl'], p['death']) else '全部守住'}")
    print()
