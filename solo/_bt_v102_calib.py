# -*- coding: utf-8 -*-
"""V10.2 阈值校准：基于 2026 全年真实分布，重新设计 S4/S5 判定口径。

设计原则（私募风控视角）：
  1. 「连续2日确认」要求同一口径在两天都成立 → 单日阈值必须用「可持续」的水位，
     不能用「脉冲式」的极值（例如单日 +10pp 广度跳升本市场全年只有 72 天，
     且两天连着发生几乎不存在）。
  2. 修复确认应看「方向 + 水平」的组合，而不是单日跳变。
  3. 卖压衰减的核心是「跌停收敛」而非「广度跳升」。
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
import market_regime_v10 as v10

DB = r"D:/mystock/cache_daily/stock_data.db"


def build():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>='20260101' ORDER BY trade_date")]
    rows = []
    for d in dates:
        df = pd.read_sql_query(
            "SELECT ts_code,pct_chg FROM daily_cache WHERE trade_date=?", conn, params=(d,))
        if df.empty:
            continue
        up = int((df.pct_chg > 0).sum()); dn = int((df.pct_chg < 0).sum())
        br = up / (up + dn) * 100 if (up + dn) else None
        thr = df.ts_code.map(lambda c: 20.0 if str(c)[:3] in ('300', '301', '688') else 10.0)
        dt = int((df.pct_chg <= -thr * 0.99).sum())
        rows.append((d, br, dt))
    conn.close()
    df = pd.DataFrame(rows, columns=['d', 'br', 'dt'])
    df['pdt'] = df.dt.shift(1)
    df['dpp'] = df.br - df.br.shift(1)
    df['br2'] = df.br.shift(1)
    df['br3'] = df.br.shift(2)
    return df


def consec(mask, need=2):
    """连续 need 日成立的天数（含第 need 日起到末日）。"""
    run = 0; n = 0
    for v in mask:
        run = run + 1 if v else 0
        if run >= need:
            n += 1
    return n


def main():
    df = build()
    df.to_csv('_bt_out/_v102_breadth.csv', index=False)
    print(f"样本 {len(df)} 个交易日\n")

    print("=" * 74)
    print("一、S4 卖压衰减：现有口径 vs 改造口径")
    print("=" * 74)
    print("现有 easing 证据共5项，其中真正可持续的只有'跌停收敛'。")
    print("原判定要求 >=2 项命中。逐项真实命中率：")
    print(f"  跌停减少(<=pdt*0.8)      : {((df.dt<=df.pdt*0.8)&(df.pdt>0)).sum():>3} 天")
    print(f"  跌停绝对减少(>=10只)      : {((df.pdt-df.dt)>=10).sum():>3} 天")
    print(f"  广度回升>=8pp            : {(df.dpp>=8).sum():>3} 天")
    print(f"  连续2日广度回升(累计>=8)   : {((df.br-df.br2)>=8).sum():>3} 天")
    print()
    # 改造：S4 = 跌停收敛（可持续） + 广度不再恶化
    for dt_thr in (0.8, 0.9, 1.0):
        for br_thr in (0, 3, 5):
            m = (df.dt <= df.pdt * dt_thr) & (df.pdt > 0) & (df.dpp >= br_thr)
            print(f"  dt<=pdt*{dt_thr} 且 dpp>={br_thr:>2}: 单日 {m.sum():>3} 天, 连续2日 {consec(m):>3} 天")

    print()
    print("=" * 74)
    print("二、S5 修复确认：现有口径 vs 改造口径")
    print("=" * 74)
    print("现有 S5 需同时满足：trend_ok + breadth_ok + sentiment_ok")
    print("  breadth_ok = br>=55 AND dpp>=10  ← 「单日跳升10pp」是脉冲条件")
    print()
    print("改造候选（方向+水平 组合）：")
    print("  [C1] br连续2日>=45 且 当日dpp>=-2（不再恶化）")
    for th in (40, 45, 50):
        m = (df.br >= th) & (df.br2 >= th) & (df.dpp >= -2)
        print(f"       br连续2日>={th} & dpp>=-2: 单日 {m.sum():>3} 天, 连续2日 {consec(m):>3} 天")
    print()
    print("  [C2] br 连续3日抬升 且 当前 br>=40")
    m3 = (df.br > df.br2) & (df.br2 > df.br3) & (df.br >= 40)
    print(f"       单日 {m3.sum():>3} 天, 连续2日 {consec(m3):>3} 天")
    print()
    print("  [C3] 3日累计抬升>=8pp 且 br>=40")
    m4 = ((df.br - df.br3) >= 8) & (df.br >= 40)
    print(f"       单日 {m4.sum():>3} 天, 连续2日 {consec(m4):>3} 天")
    print()
    print("  [C4] br>=40 且 跌停收敛 且 广度较3日前抬升>=5pp")
    m5 = (df.br >= 40) & (df.dt <= df.pdt * 0.9) & (df.pdt > 0) & ((df.br - df.br3) >= 5)
    print(f"       单日 {m5.sum():>3} 天, 连续2日 {consec(m5):>3} 天")

    print()
    print("=" * 74)
    print("三、改造后 S4→S5 的接力可行性")
    print("=" * 74)
    s4 = (df.dt <= df.pdt * 0.9) & (df.pdt > 0) & (df.dpp >= 0)
    s5 = (df.br >= 45) & (df.br2 >= 45) & (df.dpp >= -2)
    print(f"  S4候选单日 {s4.sum():>3} 天, 连续2日 {consec(s4):>3} 天")
    print(f"  S5候选单日 {s5.sum():>3} 天, 连续2日 {consec(s5):>3} 天")
    both = s4 & s5
    print(f"  两者同日   {both.sum():>3} 天")
    seq = s5 & ~s4
    print(f"  S5先行(前一日S4) : {(s5.shift(1)&s4).sum():>3} 天")

    print()
    print("=" * 74)
    print("四、仓位权限阶梯校准：S5 出现后能否突破60%")
    print("=" * 74)
    print("当前权限表：S1=60 S2=30 S3=5 S4=10 S5=60 S6=100")
    print("→ S5(修复确认) 权限 60% 与 S1(正常) 相同，这是设计缺陷：")
    print("   从恐慌修复回来的市场，风险预算应比「正常市况」更保守，")
    print("   但从「熊市反弹」的市场，权限又该有提升空间 —— 两者混为一谈。")
    print()
    print("建议：拆分为 S4(观察 10%) → S5(修复 40%) → S6(主升 100%)，")
    print("     并在 S1=60 / S5=40 之间形成清晰的减仓梯度；")
    print("     S6 才放开到 100%，且需满仓授权。")


if __name__ == '__main__':
    main()
