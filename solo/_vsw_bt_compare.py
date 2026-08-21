# -*- coding: utf-8 -*-
"""VSW 放宽量能门槛前后 8月可开仓信号 T+5 回测对比
口径: 信号日盘后选股 → 次日开盘买入 → 持有5个交易日 → 盘中-7%止损
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import volume_surge_select as vsw

STOP = -7.0
HOLD = 5

# 放宽前（仅3天可开仓）
OLD = [
    ('20260806', '002432.SZ', '九安医疗'),
    ('20260810', '603118.SH', '共进股份'),
    ('20260814', '600126.SH', '杭钢股份'),
]
# 放宽后（7天可开仓，含同日多个B级）
NEW = [
    ('20260806', '002432.SZ', '九安医疗'),
    ('20260810', '603118.SH', '共进股份'),
    ('20260811', '600468.SH', '百利电气'),
    ('20260814', '600126.SH', '杭钢股份'),
    ('20260814', '300058.SZ', '蓝色光标'),
    ('20260817', '605128.SH', '上海沿浦'),
    ('20260817', '600191.SH', '华资实业'),
    ('20260818', '605128.SH', '上海沿浦'),
    ('20260818', '600191.SH', '华资实业'),
    ('20260819', '688315.SH', '诺禾致源'),
]


def trade(code, sig_date):
    df = vsw.get_hist_data(code)
    if df is None or df.empty:
        return None, '无数据'
    df['trade_date'] = df['trade_date'].astype(str)
    hits = df.index[df['trade_date'] == sig_date].tolist()
    if not hits:
        return None, '无信号日K线'
    i = hits[0]
    if i + 1 >= len(df):
        return None, '无次日数据'
    buy_open = float(df.iloc[i + 1]['open'])
    if buy_open <= 0:
        return None, '次日开盘无效'
    # 从买入日起持有 HOLD 个交易日，先检查止损
    last = min(i + 1 + HOLD, len(df) - 1)
    for j in range(i + 1, last + 1):
        low = float(df.iloc[j]['low'])
        if low / buy_open - 1 <= STOP / 100.0:
            return STOP, '止损'
    if i + 1 + HOLD >= len(df):
        # 数据不足完整持有
        ndays = last - (i + 1) + 1
        ret = (float(df.iloc[last]['close']) / buy_open - 1) * 100.0
        return ret, f'数据不足(仅{ndays}日)'
    ret = (float(df.iloc[i + 1 + HOLD]['close']) / buy_open - 1) * 100.0
    return ret, 'T+5收盘'


def run(signals, label):
    print(f"\n===== {label} =====")
    rets = []
    for d, code, name in signals:
        r, note = trade(code, d)
        tag = f'{r:+.2f}%' if r is not None else 'N/A'
        print(f"  {d} {name}({code}) T+5: {tag}  [{note}]")
        if r is not None:
            rets.append(r)
    if rets:
        arr = rets
        wins = [x for x in arr if x > 0]
        print(f"  合计 {len(arr)} 笔 | 胜率 {len(wins)/len(arr)*100:.0f}% | "
              f"均收益 {sum(arr)/len(arr):+.2f}% | 止损 {arr.count(STOP)} 笔 | "
              f"最大亏损 {min(arr):+.2f}% | 最大盈利 {max(arr):+.2f}%")
    else:
        print("  无可统计信号")
    return rets


if __name__ == '__main__':
    o = run(OLD, '放宽前 可开仓信号 (3天)')
    n = run(NEW, '放宽后 可开仓信号 (7天)')
    if o and n:
        print(f"\n放宽后信号数 {len(o)}→{len(n)}，对比均收益 {sum(o)/len(o):+.2f}% vs {sum(n)/len(n):+.2f}%")
