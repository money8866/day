# -*- coding: utf-8 -*-
"""一次性补丁: volume_surge_strategy.py 大盘过滤 HS300 MA20>MA60 → 三指数20日动量>+3%
运行: python _patch_mom_gate.py   (幂等, 已应用则跳过)
"""
import io, sys

TARGET = r"d:\mystock\tdx_backtest\volume_surge_strategy.py"
MARK = "MOM_GATE_THRESHOLD"  # 已打过补丁的标记

with io.open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

# 兜底修复1: 旧变量引用 hs300_trend → idx3_mom20 (即使主补丁已应用也执行)
fixed = False
if "if hs300_trend:" in src:
    src = src.replace("if hs300_trend:", "if idx3_mom20:")
    fixed = True
    print("[Patch] 修复 hs300_trend 残留引用")

# 兜底修复2: merge suffixes 冲突 → 改用 dict 并集求均值
old_merge = """        mom_parts = []
        for _code in ("000001.SH", "000300.SH", "399006.SZ"):
            _df = load_kline(_code, start_date=load_start, end_date=end_date)
            if not _df.empty:
                _df = precompute_indicators(_df)
                _mom = (_df["close"] / _df["close"].shift(20) - 1) * 100
                mom_parts.append(
                    pd.DataFrame({"trade_date": _df["trade_date"].values,
                                  "mom20": _mom.values}))
        if len(mom_parts) == 3:
            m_df = mom_parts[0]
            for _p in mom_parts[1:]:
                m_df = m_df.merge(_p, on="trade_date", suffixes=("", "_x"))
            _cols = [c for c in m_df.columns if c.startswith("mom20")]
            idx3_mom20 = dict(zip(m_df["trade_date"], m_df[_cols].mean(axis=1)))"""
new_merge = """        mom_maps = []
        for _code in ("000001.SH", "000300.SH", "399006.SZ"):
            _df = load_kline(_code, start_date=load_start, end_date=end_date)
            if not _df.empty:
                _df = precompute_indicators(_df)
                _mom = (_df["close"] / _df["close"].shift(20) - 1) * 100
                mom_maps.append(dict(zip(_df["trade_date"].values,
                                         _mom.values)))
        if len(mom_maps) == 3:
            _all = sorted(set().union(*[set(m) for m in mom_maps]))
            for _d in _all:
                _vals = [m[_d] for m in mom_maps
                         if _d in m and not pd.isna(m[_d])]
                if len(_vals) == 3:
                    idx3_mom20[_d] = float(np.mean(_vals))"""
if old_merge in src:
    src = src.replace(old_merge, new_merge, 1)
    fixed = True
    print("[Patch] 修复 merge suffixes 冲突")

if MARK in src:
    if fixed:
        with io.open(TARGET, "w", encoding="utf-8") as f:
            f.write(src)
        print("[Patch] 追加修复完成 ✔")
    else:
        print("[Patch] 已应用过, 跳过")
    sys.exit(0)

# 1) 模块级常量 (插入到 def run_backtest 之前)
const = (
    "# 大盘过滤阈值: 三指数(上证/沪深300/创业板指)20日动量均值(%), 高于此值才允许交易\n"
    "MOM_GATE_THRESHOLD = 3.0\n"
    "\n\n"
)
src = src.replace("def run_backtest(", const + "def run_backtest(", 1)

# 2) 头部打印文案
src = src.replace("大盘过滤: 是", "大盘过滤: 三指数动量>+3%", 1)

# 3) HS300 趋势加载块 → 三指数动量加载块
old_load = """    # --- 加载大盘状态（沪深300 MA20/MA60 趋势） ---
    hs300_trend = {}  # trade_date -> True(多头)
    try:
        hs300_df = load_kline("000300.SH", start_date=load_start, end_date=end_date)
        if not hs300_df.empty:
            hs300_df = precompute_indicators(hs300_df)
            for _, row in hs300_df.iterrows():
                hs300_trend[row["trade_date"]] = (
                    row["ma20"] > row["ma60"]
                )
        print(f"[Market] HS300 趋势数据: {len(hs300_trend)} 天, "
              f"多头={sum(1 for v in hs300_trend.values() if v)} 天")
    except Exception as e:
        print(f"[Market] HS300 加载失败: {e}，不过滤")"""
new_load = """    # --- 加载大盘状态（三指数 20日动量均值） ---
    idx3_mom20 = {}  # trade_date -> 三指数20日动量均值(%)
    try:
        mom_parts = []
        for _code in ("000001.SH", "000300.SH", "399006.SZ"):
            _df = load_kline(_code, start_date=load_start, end_date=end_date)
            if not _df.empty:
                _df = precompute_indicators(_df)
                _mom = (_df["close"] / _df["close"].shift(20) - 1) * 100
                mom_parts.append(
                    pd.DataFrame({"trade_date": _df["trade_date"].values,
                                  "mom20": _mom.values}))
        if len(mom_parts) == 3:
            m_df = mom_parts[0]
            for _p in mom_parts[1:]:
                m_df = m_df.merge(_p, on="trade_date", suffixes=("", "_x"))
            _cols = [c for c in m_df.columns if c.startswith("mom20")]
            idx3_mom20 = dict(zip(m_df["trade_date"], m_df[_cols].mean(axis=1)))
            _gt = sum(1 for v in idx3_mom20.values() if v > MOM_GATE_THRESHOLD)
            print(f"[Market] 三指数动量数据: {len(idx3_mom20)} 天, "
                  f"动量>+{MOM_GATE_THRESHOLD}% 天={_gt}")
    except Exception as e:
        print(f"[Market] 三指数加载失败: {e}，不过滤")"""
assert old_load in src, "加载块未匹配!"
src = src.replace(old_load, new_load, 1)

# 4) 逐日过滤: hs300_trend → idx3_mom20
old_daily = """        # === 大盘趋势过滤 ===
        td_bull = hs300_trend.get(td)
        if td_bull is not None and not td_bull:
            daily_counts.append(0)
            market_skipped_days += 1
            continue"""
new_daily = """        # === 大盘过滤: 三指数20日动量均值 > 阈值 ===
        td_mom20 = idx3_mom20.get(td)
        if td_mom20 is not None and td_mom20 <= MOM_GATE_THRESHOLD:
            daily_counts.append(0)
            market_skipped_days += 1
            continue"""
assert old_daily in src, "逐日过滤块未匹配!"
src = src.replace(old_daily, new_daily, 1)

with io.open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)
print("[Patch] 应用成功 ✔")
