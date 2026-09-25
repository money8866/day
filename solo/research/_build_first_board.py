"""三花聚顶研究 - 首板事件库构建（research/out/first_board_events.parquet）

首板定义：T0 出现涨停，且 T-20..T-1 无涨停（含 ST 5% 口径的保守排他）。
股票池：上市 >= 60 交易日；剔除 ST 名称股、退市股；保留北交所但打标签。
同时输出面板层面的检验统计，用于验证涨停识别口径是否合理。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
LOOKBACK = 20
MIN_SEQ = 60


def main():
    panel = pd.read_parquet(os.path.join(OUT, "panel.parquet"))
    basic = pd.read_parquet(os.path.join(OUT, "basic.parquet"))
    bmap = basic.set_index('ts_code')

    G = panel.groupby('ts_code', observed=True)
    lim_any = (panel['is_limit_up'] | panel['is_limit_up_5']).astype(np.float32)

    # T-1..T-20 是否出现涨停（shift(1) 后再 rolling，保证不含当日）
    prev = lim_any.groupby(panel['ts_code'], observed=True).shift(1)
    had_prev = prev.groupby(panel['ts_code'], observed=True).transform(
        lambda s: s.rolling(LOOKBACK, min_periods=LOOKBACK).max())
    panel['prev_lim20'] = had_prev.fillna(1.0)   # 不足 20 日视为"有"，避免早期样本误判
    # T-1 前累计涨幅（首板前 20 日）
    panel['prev_ret5'] = G['a_close'].transform(lambda s: s / s.shift(5) - 1.0).astype(np.float32)

    # ── 全市场涨停统计（口径体检）
    print("=== 涨停识别体检 ===")
    print("全样本涨停次数:", int(panel['is_limit_up'].sum()),
          "| 日均:", round(panel['is_limit_up'].sum() / panel['trade_date'].nunique(), 1))
    yr = panel.groupby(panel['trade_date'] // 10000, observed=True)['is_limit_up'].agg(['sum', 'mean'])
    print("分年涨停家数/占比:")
    print(yr.to_string())
    print("20cm 占比:", round(float(panel.loc[panel['is_limit_up'], 'limit_pct'].eq(20).mean()), 3))
    print("一字板占比:", round(float(panel.loc[panel['is_limit_up'], 'one_word'].mean()), 3))
    print("开过板占比:", round(float(panel.loc[panel['is_limit_up'], 'opened_board'].mean()), 3))

    # ── 首板事件
    pool = (panel['seq'] >= MIN_SEQ)
    fb = panel[pool & panel['is_limit_up'] & (panel['prev_lim20'] == 0)].copy()
    fb['is_st'] = fb['ts_code'].astype(str).map(
        bmap['is_st_name'].to_dict()).fillna(False).astype(bool)
    fb['is_delisted'] = fb['ts_code'].astype(str).map(
        bmap['is_delisted'].to_dict()).fillna(False).astype(bool)
    fb['name'] = fb['ts_code'].astype(str).map(bmap['name'].to_dict())
    fb['industry'] = fb['ts_code'].astype(str).map(bmap['industry'].to_dict())
    fb['list_date'] = fb['ts_code'].astype(str).map(bmap['list_date'].to_dict())
    # 主板事件必须真为 10% 级别（排除 5% 误判）
    fb = fb[~((fb['board'].astype(str) == 'MAIN') & (fb['pct_chg'] < 9.5))]

    print("\n=== 首板事件（未过滤）===", len(fb))
    print("按板块:\n", fb['board'].value_counts().to_string())
    print("按年:\n", (fb['trade_date'] // 10000).value_counts().sort_index().to_string())
    print("ST名称股:", int(fb['is_st'].sum()), "| 退市股:", int(fb['is_delisted'].sum()))

    fb = fb[~fb['is_st'] & ~fb['is_delisted']].copy()
    print("过滤 ST/退市后:", len(fb))

    # 北交所单列
    fb['is_bse'] = fb['board'].astype(str) == 'BSE'

    # ── 首板质量特征
    pre = fb['pre_close'].replace(0, np.nan)
    fb['amplitude'] = (fb['high'] - fb['low']) / pre
    fb['body'] = (fb['close'] - fb['open']) / pre
    fb['upper_shadow'] = (fb['high'] - fb[['open', 'close']].max(axis=1)) / pre
    fb['lower_shadow'] = (fb[['open', 'close']].min(axis=1) - fb['low']) / pre
    fb['t0_vr20'] = fb['vol'] / fb['vol_ma20']
    fb['t0_vs_ma20'] = fb['a_close'] / fb['ma20'] - 1.0
    fb['t0_vs_ma60'] = fb['a_close'] / fb['ma60'] - 1.0
    fb['t0_amount'] = fb['amount']
    fb['limit_pct'] = fb['limit_pct']
    fb['t0_ret20prev'] = fb['ret20_prev']
    fb['t0_ret5prev'] = fb['prev_ret5']
    # 首板质量三档：一字板或缩量涨停（抛压小、封板干净）为 Strong；
    # 巨量涨停（分歧大、易兑现）为 Weak；其余 Normal。
    t0q = np.where((fb['one_word'].values) | (fb['t0_vr20'].values <= 1.5), 'Strong',
                   np.where(fb['t0_vr20'].values >= 3.0, 'Weak', 'Normal'))
    fb['t0_quality'] = t0q

    keep = ['ts_code', 'name', 'industry', 'list_date', 'trade_date', 'board', 'is_bse',
            'open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount',
            'a_close', 'limit_price', 'limit_pct', 'one_word', 'opened_board',
            'amplitude', 'body', 'upper_shadow', 'lower_shadow',
            't0_vr20', 't0_vs_ma20', 't0_vs_ma60', 't0_amount',
            't0_ret20prev', 't0_ret5prev', 't0_quality',
            'ma5', 'ma10', 'ma20', 'ma60', 'vol_ma20']
    fb = fb[keep].reset_index(drop=True)
    p = os.path.join(OUT, "first_board_events.parquet")
    fb.to_parquet(p, index=False)
    print("\n已写", p, len(fb), "事件")

    print("\n首板质量分布:\n", fb['t0_quality'].value_counts().to_string())
    print("\n首板 T0 量比描述:\n", fb['t0_vr20'].describe().to_string())

    panel.to_parquet(os.path.join(OUT, "panel.parquet"), index=False, compression='zstd')
    print("panel 已回写（新增 prev_lim20 / prev_ret5）")


if __name__ == '__main__':
    main()
