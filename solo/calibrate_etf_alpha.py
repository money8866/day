"""
ETF Alpha Engine V2 — 权重校准 & 多日期回测
===========================================
目标: 通过历史回测校准5个评分维度的权重，最大化对未来收益的预测能力。

策略: 先运行排名保存维度分 → 再离线测试权重变体（避免重复扫描成分股）
"""
import os, sys, json, time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv("d:/mystock/config/.env")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tushare as ts

from etf_alpha_engine_v2 import ETFAlphaEngineV2, ETFBacktester, ETF_POOL

ts.set_token(os.getenv('TUSHARE_TOKEN'))
PRO = ts.pro_api()

# ─── 待测试的权重变体 ───
WEIGHT_VARIANTS = [
    {'alpha':0.35,'leader':0.20,'trend':0.20,'flow':0.15,'risk':0.10},  # 默认
    {'alpha':0.40,'leader':0.20,'trend':0.15,'flow':0.15,'risk':0.10},  # 提高Alpha
    {'alpha':0.45,'leader':0.15,'trend':0.15,'flow':0.15,'risk':0.10},
    {'alpha':0.30,'leader':0.15,'trend':0.30,'flow':0.15,'risk':0.10},  # 提高Trend
    {'alpha':0.25,'leader':0.15,'trend':0.35,'flow':0.15,'risk':0.10},
    {'alpha':0.30,'leader':0.15,'trend':0.20,'flow':0.25,'risk':0.10},  # 提高Flow
    {'alpha':0.25,'leader':0.15,'trend':0.20,'flow':0.30,'risk':0.10},
    {'alpha':0.30,'leader':0.30,'trend':0.15,'flow':0.15,'risk':0.10},  # 提高Leader
    {'alpha':0.25,'leader':0.35,'trend':0.15,'flow':0.15,'risk':0.10},
    {'alpha':0.30,'leader':0.20,'trend':0.20,'flow':0.20,'risk':0.10},  # 均衡
    {'alpha':0.28,'leader':0.18,'trend':0.22,'flow':0.22,'risk':0.10},
    {'alpha':0.32,'leader':0.18,'trend':0.22,'flow':0.18,'risk':0.10},
    {'alpha':0.35,'leader':0.22,'trend':0.22,'flow':0.16,'risk':0.05},  # 降低Risk
    {'alpha':0.35,'leader':0.20,'trend':0.20,'flow':0.20,'risk':0.05},
]

FORWARD_DAYS = [5, 10, 20]


def calc_fwd_returns(code: str, base_date: str) -> Dict:
    """计算ETF在排名日后的5/10/20日收益"""
    extra = 45  # 日历天数需覆盖20+个交易日（含假期）
    base_dt = datetime.strptime(base_date, '%Y%m%d')
    start = (base_dt - timedelta(days=10)).strftime('%Y%m%d')
    end = (base_dt + timedelta(days=extra)).strftime('%Y%m%d')
    try:
        time.sleep(0.12)
        df = PRO.fund_daily(ts_code=code, start_date=start, end_date=end)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    df = df.sort_values('trade_date').reset_index(drop=True)
    mask = df['trade_date'].astype(str) == base_date
    if not mask.any():
        return {}
    idx = df[mask].index[0]
    base_p = df.iloc[idx]['close']

    rets = {}
    for fd in FORWARD_DAYS:
        if idx + fd < len(df):
            rets[f'fwd_{fd}d'] = (df.iloc[idx+fd]['close'] - base_p) / base_p * 100
        else:
            rets[f'fwd_{fd}d'] = None
    rets['base_close'] = base_p
    return rets


def run_single_ranking(date_str: str) -> Optional[pd.DataFrame]:
    """
    对单个日期运行排名，保存维度分到CSV供后续分析。
    """
    engine = ETFAlphaEngineV2(verbose=False)
    try:
        df = engine.rank_etfs(etf_pool=ETF_POOL, end_date=date_str, force_scan=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    # 加上未来收益
    fwd_data = {}
    for _, r in df.iterrows():
        code = r['ETF代码']
        fwd_data[code] = calc_fwd_returns(code, date_str)

    fwd_cols = [f'fwd_{fd}d' for fd in FORWARD_DAYS]
    for col in fwd_cols:
        df[col] = df['ETF代码'].map(lambda c: fwd_data.get(c, {}).get(col))
    df['base_close'] = df['ETF代码'].map(lambda c: fwd_data.get(c, {}).get('base_close'))

    # 保存
    out = f"report_daily/etf_v2_ranking_{date_str}.csv"
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"  [保存] {out} ({len(df)} 只ETF)")
    return df


def calc_score_from_dims(row: pd.Series, w: Dict) -> float:
    """从维度分计算综合评分"""
    return (
        w['alpha'] * row['成份股质量'] +
        w['leader'] * row['龙头强度'] +
        w['trend'] * row['趋势强度'] +
        w['flow'] * row['资金强度'] +
        w['risk'] * row['风险调整']
    )


def evaluate_weights(ranking_df: pd.DataFrame, w: Dict) -> Dict:
    """
    对一组权重计算IC和分层收益。
    直接用维度分重新计算综合评分，不重新运行引擎。
    """
    df = ranking_df.copy()
    df['综合评分'] = df.apply(lambda r: calc_score_from_dims(r, w), axis=1)
    df = df.sort_values('综合评分', ascending=False).reset_index(drop=True)

    # Rank IC
    results = {}
    for fd in FORWARD_DAYS:
        col = f'fwd_{fd}d'
        valid = df[['综合评分', col]].dropna()
        if len(valid) < 5:
            results[f'ic_{fd}d'] = 0.0
            results[f'ic_n_{fd}d'] = 0
            continue
        from scipy.stats import spearmanr
        try:
            sp, _ = spearmanr(valid['综合评分'], valid[col])
        except Exception:
            sp = 0.0
        results[f'ic_{fd}d'] = round(sp, 4)
        results[f'ic_n_{fd}d'] = len(valid)

    # 分层收益 (Q5高分组 vs Q1低分组)
    for fd in FORWARD_DAYS:
        col = f'fwd_{fd}d'
        valid = df[[col]].dropna()
        if len(valid) < 10:
            continue
        df_valid = df.dropna(subset=[col]).copy()
        try:
            df_valid['分层'] = pd.qcut(df_valid['综合评分'], q=5,
                                        labels=['Q1(低)','Q2','Q3','Q4','Q5(高)'],
                                        duplicates='drop')
        except Exception:
            continue
        for label in ['Q1(低)', 'Q5(高)']:
            grp = df_valid[df_valid['分层'] == label]
            if grp.empty:
                continue
            avg_ret = grp[col].mean()
            win_rate = (grp[col] > 0).sum() / max(len(grp), 1) * 100
            results[f'layer_{fd}d_{label}_收益'] = round(float(avg_ret), 2)
            results[f'layer_{fd}d_{label}_胜率'] = round(win_rate, 1)

        q5 = df_valid[df_valid['分层'] == 'Q5(高)'][col].mean()
        q1 = df_valid[df_valid['分层'] == 'Q1(低)'][col].mean()
        results[f'layer_{fd}d_Q5-Q1差'] = round(float(q5 - q1), 2)

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='ETF Alpha V2 权重校准')
    parser.add_argument('--dates', type=str, default='',
                        help='回测日期，逗号分隔')
    parser.add_argument('--fast', action='store_true',
                        help='仅用20260430')
    parser.add_argument('--rank_only', action='store_true',
                        help='仅生成排名，不校准')
    args = parser.parse_args()

    if args.dates:
        dates = [d.strip() for d in args.dates.split(',') if d.strip()]
    elif args.fast:
        dates = ['20260430']
    else:
        dates = [
            '20260529','20260430','20260331',
            '20260227','20260123',
            '20251231','20251128',
        ]

    print("=" * 70)
    print("ETF Alpha Engine V2 权重校准")
    print("=" * 70)
    print(f"回测日期: {len(dates)} 个: {dates[0]}~{dates[-1]}")
    print(f"权重变体: {len(WEIGHT_VARIANTS)} 组")
    print(f"远期检测: {FORWARD_DAYS}")
    print()

    # Step 1: 生成所有日期的排名（含未来收益）
    print("[Step 1] 生成排名数据...")
    ranking_files = {}
    for date_str in dates:
        csv_path = f"report_daily/etf_v2_ranking_{date_str}.csv"
        if os.path.exists(csv_path) and not args.rank_only:
            print(f"  [缓存] {csv_path}")
            ranking_files[date_str] = pd.read_csv(csv_path)
        else:
            t1 = time.time()
            df = run_single_ranking(date_str)
            if df is not None:
                ranking_files[date_str] = df
            print(f"  耗时: {time.time()-t1:.0f}s")
    print(f"  有效排名: {len(ranking_files)}/{len(dates)}")

    if args.rank_only:
        print("[完成] 仅生成排名")
        return

    # Step 2: 遍历权重变体
    print()
    print("[Step 2] 权重校准...")
    all_rows = []

    for w_idx, w in enumerate(WEIGHT_VARIANTS):
        w_key = f"α={w['alpha']:.2f} L={w['leader']:.2f} T={w['trend']:.2f} F={w['flow']:.2f} R={w['risk']:.2f}"
        date_results = []

        for date_str, rank_df in ranking_files.items():
            ev = evaluate_weights(rank_df, w)
            ev['date'] = date_str
            date_results.append(ev)

        if not date_results:
            continue

        # 跨日期汇总
        ic_5 = np.mean([r.get('ic_5d', 0) for r in date_results])
        ic_10 = np.mean([r.get('ic_10d', 0) for r in date_results])
        ic_20 = np.mean([r.get('ic_20d', 0) for r in date_results])
        q5q1_20 = np.mean([r.get('layer_20d_Q5-Q1差', 0) for r in date_results])
        q5_win_20 = np.mean([r.get('layer_20d_Q5(高)_胜率', 0) for r in date_results])

        # 综合得分 = 40% IC_20 + 30% Q5-Q1差归一化 + 30% Q5胜率
        composite = ic_20 * 0.4 + (q5q1_20 / 20) * 0.3 + (q5_win_20 / 100) * 0.3

        row = {
            '权重': w_key,
            '有效日期': len(date_results),
            'IC_5日': round(ic_5, 4),
            'IC_10日': round(ic_10, 4),
            'IC_20日': round(ic_20, 4),
            'Q5-Q1_20日': round(q5q1_20, 2),
            'Q5胜率_20日': round(q5_win_20, 1),
            '综合得分': round(composite, 4),
        }
        all_rows.append(row)

        print(f"  [{w_idx+1:2d}/{len(WEIGHT_VARIANTS)}] {w_key}")
        print(f"      IC(5/10/20): {ic_5:.3f}/{ic_10:.3f}/{ic_20:.3f}  "
              f"Q5-Q1={q5q1_20:.1f}%  Q5胜率={q5_win_20:.0f}%  "
              f"综合={composite:.3f}")

    if not all_rows:
        print("\n[校准] 无有效结果")
        return

    df = pd.DataFrame(all_rows)
    df = df.sort_values('综合得分', ascending=False).reset_index(drop=True)

    print()
    print("─" * 70)
    print("TOP 5 权重组合")
    print("─" * 70)
    for i, (_, r) in enumerate(df.head(5).iterrows(), 1):
        print(f"\n  #{i}: {r['权重']}")
        print(f"      IC(5/10/20): {r['IC_5日']:.3f}/{r['IC_10日']:.3f}/{r['IC_20日']:.3f}")
        print(f"      Q5-Q1差: {r['Q5-Q1_20日']:.1f}%  Q5胜率: {r['Q5胜率_20日']:.0f}%")
        print(f"      综合: {r['综合得分']:.3f}  (日期: {r['有效日期']})")

    best = df.iloc[0]
    print()
    print("═" * 70)
    print("推荐权重")
    print("═" * 70)
    print(f"  {best['权重']}")
    print(f"  IC_20日={best['IC_20日']:.3f}  "
          f"Q5-Q1={best['Q5-Q1_20日']:.1f}%  "
          f"Q5胜率={best['Q5胜率_20日']:.0f}%")

    # 保存
    out_path = "report_daily/etf_v2_weight_calibration.csv"
    df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n校准结果已保存: {out_path}")

    # 提取最优权重配置
    parts = best['权重'].replace('α=','').replace('L=','').replace('T=','').replace('F=','').replace('R=','').split()
    best_weights = {
        'alpha': float(parts[0]), 'leader': float(parts[1]),
        'trend': float(parts[2]), 'flow': float(parts[3]), 'risk': float(parts[4]),
    }
    config_path = "report_daily/etf_v2_best_weights.json"
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump({
            'weights': best_weights,
            'metrics': {
                'ic_20d': best['IC_20日'],
                'q5_q1_20d': best['Q5-Q1_20日'],
                'q5_win_20d': best['Q5胜率_20日'],
            },
            'calibration_date': datetime.now().strftime('%Y%m%d'),
        }, f, ensure_ascii=False, indent=2)
    print(f"最优权重已保存: {config_path}")

    print(f"\n如需应用最优权重，运行:")
    print(f"  etf_alpha_engine_v2.py --date YYYYMMDD --weights {config_path}")


if __name__ == '__main__':
    main()
