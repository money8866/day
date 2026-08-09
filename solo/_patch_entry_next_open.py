# -*- coding: utf-8 -*-
"""一次性补丁: volume_surge_strategy.py 买入价 信号日收盘 → 次日开盘
回测验证: 次日开盘买入 均收益 +1.76% vs 收盘买入 +1.08%
运行: python _patch_entry_next_open.py   (幂等, 已应用则跳过)
"""
import io, sys

TARGET = r"d:\mystock\tdx_backtest\volume_surge_strategy.py"
MARK = "# ENTRY_NEXT_OPEN_PATCHED"  # 已打过补丁的标记

with io.open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

if MARK in src:
    print("[Patch] 已应用过, 跳过")
    sys.exit(0)

# 0) 文件头加补丁标记
src = "# ENTRY_NEXT_OPEN_PATCHED 买入价=次日开盘(回测+1.76%优于收盘+1.08%)\n" + src

# 1) 头部打印注明买入方式
src = src.replace("大盘过滤: 三指数动量>+3%",
                  "大盘过滤: 三指数动量>+3%  买入: 次日开盘", 1)

# 2) 撮合段: 收盘买入 → 次日开盘买入
old = """                i = idx[0]
                buy_close = df.iloc[i]["close"]
                # 硬止损检查：持有期内任何一天的最低价跌破止损位
                exit_idx = i + hold_days
                if exit_idx >= len(df):
                    exit_idx = len(df) - 1
                stopped = False
                for j in range(i + 1, exit_idx + 1):
                    low_price = df.iloc[j]["low"]
                    if low_price / buy_close - 1 <= stop_loss_pct / 100:
                        ret = stop_loss_pct
                        stopped = True
                        break
                if not stopped:
                    if i + hold_days < len(df):
                        sell_close = df.iloc[i + hold_days]["close"]
                        ret = (sell_close / buy_close - 1) * 100
                    else:
                        continue
                all_returns.append(ret)"""
new = """                i = idx[0]
                if i + 1 >= len(df):      # 需有次日数据(次日开盘买入)
                    continue
                buy_close = df.iloc[i + 1]["open"]   # 次日开盘价买入
                # 硬止损检查：买入后持有期内任何一天的最低价跌破止损位
                exit_idx = i + 1 + hold_days
                if exit_idx >= len(df):
                    exit_idx = len(df) - 1
                stopped = False
                for j in range(i + 2, exit_idx + 1):
                    low_price = df.iloc[j]["low"]
                    if low_price / buy_close - 1 <= stop_loss_pct / 100:
                        ret = stop_loss_pct
                        stopped = True
                        break
                if not stopped:
                    if i + 1 + hold_days < len(df):
                        sell_close = df.iloc[i + 1 + hold_days]["close"]
                        ret = (sell_close / buy_close - 1) * 100
                    else:
                        continue
                all_returns.append(ret)"""
assert old in src, "撮合段未匹配!"
src = src.replace(old, new, 1)

with io.open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)
print("[Patch] 次日开盘买入 应用成功 ✔")
