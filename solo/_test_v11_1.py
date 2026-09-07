# -*- coding: utf-8 -*-
"""V11.1 改动端到端验证：资金行为反向 + 换手率负向因子。

外部接口（同花顺资金流 / 机构资金流 / 主题分 / 基本面 / YRI）全部打桩为基准值，
只验证本次改动涉及的打分链路：
  1. 资金行为：量能部分符号翻转（CAPITAL_REVERSE_VOL=True）
  2. 换手率：候选池内截面分位 → 负向得分，权重 24%
"""
import pandas as pd
import numpy as np
import tushare_quant as tq

PANEL = r'D:\mystock\solo\ic_output\panel_20230101_20260907.parquet'
DAY = '20260904'
N_POOL = 30

# ---------- 外部依赖打桩 ----------
tq._get_stock_moneyflow_features = lambda code: {'mf_available': False}
tq.calc_institutional_flow_score = lambda code: 0
tq.calc_tli_score = lambda *a, **k: (50, [])
tq.get_hot_list_best_rank_bonus = lambda *a, **k: (0, 9999, 0)
tq.calc_fundamental_score_v3 = lambda **k: {'base_score': 50, 'synergy_coeff': 1.0, 'logic': []}
tq.calc_yri_history = lambda *a, **k: {'错误': 'stub'}
tq._load_v2_theme_scores = lambda d: None
tq._load_v6_result = lambda d: None


def main():
    pan = pd.read_parquet(PANEL)
    pan['trade_date'] = pan['trade_date'].astype(str)
    day = pan[pan.trade_date == DAY]
    codes = day.ts_code.tolist()[:N_POOL]
    print(f'测试池: {len(codes)} 只 | 交易日 {DAY}')

    # ---- 1. 池内换手率分位 ----
    tq.TURNOVER_CACHE = dict(zip(day.ts_code, day.turnover_rate))
    n = tq.build_pool_turnover_pct(codes)
    print(f'池内分位构建: {n} 只')
    pct = pd.Series(tq.POOL_TURNOVER_PCT)
    tur = pd.Series({c: float(day.loc[day.ts_code == c, 'turnover_rate'].iloc[0])
                     for c in pct.index})
    print(f'  换手率 vs 池内分位 相关 = {tur.corr(pct):+.4f}   (应 ≈ +1)')

    # ---- 2. 逐只打分 ----
    rows = []
    for c in codes:
        d = pan[(pan.ts_code == c) & (pan.trade_date <= DAY)].tail(250)
        if len(d) < 60:
            continue
        d = d[['open', 'high', 'low', 'close', 'vol', 'amount']].reset_index(drop=True)
        try:
            s, rec, det, fp = tq.calc_unified_stock_score(
                d, c, theme='', theme_trend_score=0, theme_sentiment_score=0)
        except Exception as e:
            print(f'  ERR {c}: {type(e).__name__}: {e}')
            continue
        rows.append({'代码': c, '整合评分': s, '失败概率': fp,
                     '资金行为': det.get('资金行为'), '换手率得分': det.get('换手率得分'),
                     '量能爆发': det.get('量能爆发'), '位置安全': det.get('位置安全性'),
                     '突破质量': det.get('突破质量'), '换手率': tur.get(c)})
    R = pd.DataFrame(rows)
    print(f'\n成功打分: {len(R)} 只\n')
    print(R.head(12).to_string(index=False))

    print('\n' + '=' * 62)
    print(' 关键校验')
    print('=' * 62)
    c1 = R['换手率得分'].corr(R['换手率'])
    c2 = R['资金行为'].corr(R['量能爆发'])
    c3 = R['整合评分'].corr(R['换手率'])
    print(f'  换手率得分 vs 换手率    = {c1:+.4f}   (应 ≈ -1，负向因子)')
    print(f'  资金行为   vs 量能爆发  = {c2:+.4f}   (应为负，量能部分已反向)')
    print(f'  整合评分   vs 换手率    = {c3:+.4f}   (应为负)')

    # ---- 3. A/B：关闭两项改动，看排名变化 ----
    print('\n' + '=' * 62)
    print(' A/B 对比（同一批股票，关掉改动重算）')
    print('=' * 62)
    tq.CAPITAL_REVERSE_VOL = False
    tq.TURNOVER_FACTOR_ENABLED = False
    rows0 = []
    for c in R['代码']:
        d = pan[(pan.ts_code == c) & (pan.trade_date <= DAY)].tail(250)
        d = d[['open', 'high', 'low', 'close', 'vol', 'amount']].reset_index(drop=True)
        s, rec, det, fp = tq.calc_unified_stock_score(
            d, c, theme='', theme_trend_score=0, theme_sentiment_score=0)
        rows0.append({'代码': c, '整合评分_old': s, '失败概率_old': fp})
    R0 = pd.DataFrame(rows0)
    M = R.merge(R0, on='代码')
    M['排名变化'] = M['整合评分_old'].rank(ascending=False).astype(int) - \
                    M['整合评分'].rank(ascending=False).astype(int)
    print(f"  整合评分 均值: {M['整合评分_old'].mean():.1f} → {M['整合评分'].mean():.1f}")
    print(f"  失败概率 均值: {M['失败概率_old'].mean():.1f} → {M['失败概率'].mean():.1f}")
    print(f"  排名平均绝对变动: {M['排名变化'].abs().mean():.1f} 位 (池内 {len(M)} 只)")
    print(f"  高换手组(前50%) 平均排名变动: "
          f"{M.loc[M['换手率'] > M['换手率'].median(), '排名变化'].mean():+.1f} 位 (应为负=被降权)")
    print(f"  低换手组(后50%) 平均排名变动: "
          f"{M.loc[M['换手率'] <= M['换手率'].median(), '排名变化'].mean():+.1f} 位 (应为正=被提升)")
    M.to_csv(r'D:\mystock\solo\ic_output\v11_1_ab_test.csv', index=False, encoding='utf-8-sig')
    print('\n  明细已存: ic_output/v11_1_ab_test.csv')


if __name__ == '__main__':
    main()
