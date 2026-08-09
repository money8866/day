# -*- coding: utf-8 -*-
"""升级回测框架 volume_surge_strategy.py 的每日Top5选择逻辑为 r2 方案 (幂等):
r2 = 距MA20升序优先(<=8%在前), >8% 排尾部补足.
回测验证: 胜率 40.1% -> 42.9% (+2.8pp), 笔数不减 (1106笔).

同时保留评分字段便于打印, 排序键改为 (距MA20>8, 距MA20).
"""
path = r"d:\mystock\tdx_backtest\volume_surge_strategy.py"
src = open(path, encoding="utf-8").read()

old = """        selected = []
        # 收集当日所有信号及其评分，按评分择优选择
        daily_candidates = []
        for ts_code, sig in signals_dict.items():
            idx_map = date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(td)
            if i is None or i >= len(sig):
                continue
            score = sig[i]
            if score > 0:
                daily_candidates.append((ts_code, float(score)))

        # 按评分取 Top N（默认每日最多5只）
        if daily_candidates:
            daily_candidates.sort(key=lambda x: -x[1])
            selected = [c[0] for c in daily_candidates[:max_daily]]"""

new = """        selected = []
        # 收集当日所有信号及其评分/距MA20 (r2排序需用)
        daily_candidates = []
        for ts_code, sig in signals_dict.items():
            idx_map = date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(td)
            if i is None or i >= len(sig):
                continue
            score = sig[i]
            if score > 0:
                _df = kline_dict.get(ts_code)
                if _df is None:
                    continue
                _ma20 = float(_df.iloc[i]["ma20"])
                _pos = (float(_df.iloc[i]["close"]) / _ma20 - 1) * 100 if _ma20 > 0 else 99.0
                daily_candidates.append((ts_code, float(score), _pos))

        # r2 择优: 距MA20升序优先(<=8%在前), >8% 排尾部补足
        if daily_candidates:
            daily_candidates.sort(key=lambda x: (x[2] > 8, x[2]))
            selected = [c[0] for c in daily_candidates[:max_daily]]"""

if old in src:
    src = src.replace(old, new)
    open(path, "w", encoding="utf-8").write(src)
    print("PATCHED: r2 rank applied")
else:
    print("ERROR: pattern not found (already patched or changed)")
