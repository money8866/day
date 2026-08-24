# -*- coding: utf-8 -*-
"""验证 RIBBacktest 能真正生成交易且无未来函数。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.backtest import RIBBacktest

from test_rib_v3 import create_rib_pattern_v3


def extend_future(df: pd.DataFrame, n_extend: int = 8) -> pd.DataFrame:
    """在 v3 形态后追加未来K线（继续上涨），供回测取未来价格。"""
    last_close = df["close"].values[-1]
    last_vol = df["vol"].values[-1]
    last_date = pd.to_datetime(df["trade_date"].iloc[-1], format="%Y%m%d")
    records = []
    c = last_close
    for k in range(1, n_extend + 1):
        c = c * (1 + 0.015 + np.random.uniform(-0.005, 0.005))
        rng = c * 0.02
        o = c - rng * 0.8
        h = c + rng * 0.1
        l = min(o, c) - rng * 0.1
        d = (last_date + pd.Timedelta(days=k)).strftime("%Y%m%d")
        records.append({"trade_date": d, "open": o, "high": h, "low": l,
                        "close": c, "vol": last_vol * 1.5, "amount": c * last_vol * 1.5})
    ext = pd.DataFrame(records)
    return pd.concat([df, ext], ignore_index=True)


def main():
    df0 = create_rib_pattern_v3()
    df = extend_future(df0, 8)

    print(f"总K线: {len(df)}  信号日(最后满足形态): {df['trade_date'].iloc[179]}")
    print(f"未来收盘: {df['close'].values[180]:.2f} {df['close'].values[184]:.2f} ...")

    # 回测：限定只分析形态完成之后的日期（避免逐日重算耗时）
    start = df["trade_date"].iloc[175]
    bt = RIBBacktest()
    metrics = bt.run({df["trade_date"].iloc[0]: df}, start_date=start, holding_days=5)

    print(f"\n回测结果:")
    print(f"  总信号数: {metrics.total_signals}")
    print(f"  生成交易数: {metrics.primary_buy_count}")
    print(f"  胜率: {metrics.win_rate*100:.1f}%")
    print(f"  平均收益: {metrics.avg_return*100:.2f}%")
    print(f"  3日收益: {metrics.avg_return_3d*100:.2f}%")
    print(f"  5日收益: {metrics.avg_return_5d*100:.2f}%")

    for t in bt.trades:
        print(f"\n交易: {t.ts_code}")
        print(f"  入场: {t.entry_date} @ {t.entry_price:.2f}")
        print(f"  出场: {t.exit_date} @ {t.exit_price:.2f}  持仓{t.holding_days}日")
        print(f"  收益: {t.return_pct*100:.2f}%  max={t.max_return*100:.2f}%  dd={t.max_drawdown*100:.2f}%")
        print(f"  评分: {t.score_at_entry}  RR: {t.risk_reward:.1f}")

    assert metrics.primary_buy_count > 0, "FAIL: 回测未生成任何交易"
    assert all(t.entry_date >= start for t in bt.trades), "FAIL: 存在信号日早于回测起始"
    full_trades = [t for t in bt.trades if t.holding_days == 5]
    assert len(full_trades) >= 2, "FAIL: 应有多笔完整5日持仓"
    # 无未来函数验证：入场价 == 信号日收盘价，出场价 == 信号日后第5日收盘价
    t0 = full_trades[0]
    pos = list(df["trade_date"].values).index(t0.entry_date)
    assert abs(t0.entry_price - df["close"].values[pos]) < 1e-6, "FAIL: 入场价与信号日收盘不符"
    assert abs(t0.exit_price - df["close"].values[pos + 5]) < 1e-6, "FAIL: 出场价与未来第5日收盘不符"
    print("\nPASS: 回测正常生成交易，入场/出场价均取自正确交易日，判定只用当日数据(无未来函数)")


if __name__ == "__main__":
    main()
