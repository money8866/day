"""
BQS (BuyQualityScore) 回测框架
===============================
基于TDX本地日线数据，对BQS四维因子做历史回测验证。
评估不同BQS分值区间对未来持有期收益的预测能力。

BQS公式: Flow(Momentum(25%) + (100-Risk)(20%) + CRE(20%)

回测方法:
  1. 从 bull_stocks_qualified.csv 加载合格标的
  2. 用 tdx_loader 批量加载日线数据（含技术指标）
  3. 每只股票每个交易日计算 BQS 代理分
  4. 记录未来 1/3/5/10/20 日收益
  5. 按 BQS 分档 (0-20/20-40/40-60/60-80/80-100) 统计胜率

用法:
    python backtest_bqs.py                               # 全量回测
    python backtest_bqs.py --quick                        # 快速（少股票+少日期）
    python backtest_bqs.py --stocks 50                    # 限制股票数
    python backtest_bqs.py --start 20260101               # 指定开始日期
    python backtest_bqs.py --end 20260720                 # 指定结束日期
    python backtest_bqs.py --output bqs_bt_result.csv     # 保存明细
"""
import os
import sys
import time
import logging
import argparse
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# ── TDX数据加载 ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_trading_system.data.tdx_loader import load_daily, load_batch, calc_all_indicators

# TDX安装根目录
_TDX_ROOT = r"C:\new_tdx"
# 默认股池路径
_DEFAULT_POOL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'report_daily', 'bull_stocks_qualified.csv')


# ============================================================
# BQS代理分计算（基于TDX技术指标）
# ============================================================
# BQS = Flow(资金分) × 35% + Momentum(动量分) × 25%
#       + (100 - Risk)(风险惩罚) × 20% + CRE(筹码轮换效率) × 20%

def compute_bqs_scores(df: pd.DataFrame, params: dict = None) -> dict:
    """
    从TDX日线数据计算BQS四维因子代理分。

    Parameters
    ----------
    df : DataFrame
        含 calc_all_indicators 输出列的日线数据（至少30行）
        trade_date, open, high, low, close, vol,
        ma5/10/20/60, macd_diff/dea/bar, kdj_k/d/j,
        rsi_6/12/24, boll_mid, vol_ratio, dist_ma5/10/20/60
    params : dict
        可调参数

    Returns
    -------
    dict: {flow_score, momentum_score, risk_score, cre_score, bqs}
    """
    if df is None or len(df) < 30:
        return {'flow_score': 50, 'momentum_score': 50, 'risk_score': 50,
                'cre_score': 50, 'bqs': 50}

    p = params or {}
    df = df.sort_values('trade_date').reset_index(drop=True)
    last = df.iloc[-1]
    c = df['close']
    v = df['vol']
    h = df['high']
    lo = df['low']
    cur = float(last['close'])

    # ── 提取技术指标 ──
    ma5 = float(last.get('ma5', c.tail(5).mean()))
    ma10 = float(last.get('ma10', c.tail(10).mean()))
    ma20 = float(last.get('ma20', c.tail(20).mean()))
    ma60 = float(last.get('ma60', _ma(c, 60)))
    vr = float(last.get('vol_ratio', 1.0))
    macd_dif = float(last.get('macd_diff', 0))
    macd_dea = float(last.get('macd_dea', 0))
    rsi6 = float(last.get('rsi_6', 50))
    rsi12 = float(last.get('rsi_12', 50))
    rsi24 = float(last.get('rsi_24', 50))
    kdj_k = float(last.get('kdj_k', 50))
    kdj_d = float(last.get('kdj_d', 50))
    kdj_j = float(last.get('kdj_j', 50))
    dist_ma5 = float(last.get('dist_ma5', 0))
    dist_ma10 = float(last.get('dist_ma10', 0))
    dist_ma20 = float(last.get('dist_ma20', 0))
    dist_ma60 = float(last.get('dist_ma60', 0))

    # ── 1. Flow Score (0~100) ──
    # 量比基础分
    if vr >= 2.0:
        flow_vol = min(100, 40 + (vr - 2.0) * 20)  # 2.0→40, 3.0→60, 4.0→80
    elif vr >= 1.5:
        flow_vol = 30 + (vr - 1.5) * 20  # 1.5→30, 2.0→40
    elif vr >= 1.0:
        flow_vol = 20 + (vr - 1.0) * 20  # 1.0→20, 1.5→30
    elif vr >= 0.7:
        flow_vol = 10 + (vr - 0.7) * 33  # 0.7→10, 1.0→20
    else:
        flow_vol = max(0, 10 + (vr - 0.7) * 33)  # 缩量惩罚

    # 量能稳定性（连续5日量比 > 0.8）
    if len(v) >= 6:
        recent_vr = [float(v.iloc[-i-1]) / max(float(c.tail(21).mean()), 1)
                     for i in range(5)]
        stable_days = sum(1 for r in recent_vr if r > 0.8)
    else:
        stable_days = 0

    flow_stability = min(30, stable_days * 6)  # 每稳定一天+6

    # 价格-量能配合（上涨放量）
    pct = float(last.get('pct_chg', 0))
    flow_price_vol = 0
    if pct > 0 and vr > 1.2:
        flow_price_vol = 15  # 上涨放量
    elif pct > 0 and vr > 0.8:
        flow_price_vol = 8   # 上涨平量

    flow_score = min(100, flow_vol + flow_stability + flow_price_vol)

    # ── 2. Momentum Score (0~100) ──
    # MACD强度
    if macd_dif > macd_dea > 0:
        mom_macd = 30
    elif macd_dif > macd_dea and macd_dif > 0:
        mom_macd = 20
    elif macd_dif > 0:
        mom_macd = 10
    elif macd_dif > macd_dea:
        mom_macd = 5  # MACD底部金叉
    else:
        mom_macd = 0

    # RSI综合
    rsi_avg = (rsi6 + rsi12 + rsi24) / 3.0
    if 55 <= rsi_avg <= 75:
        mom_rsi = 25
    elif rsi_avg >= 50:
        mom_rsi = 18
    elif rsi_avg >= 40:
        mom_rsi = 10
    elif rsi_avg >= 30:
        mom_rsi = 5
    else:
        mom_rsi = 0

    # KDJ强度
    if kdj_j > kdj_k > 50:
        mom_kdj = 20
    elif kdj_j > kdj_k and kdj_k > 30:
        mom_kdj = 12
    elif kdj_j > kdj_k:
        mom_kdj = 6
    else:
        mom_kdj = 0

    # 均线斜率（5日均线方向）
    ma5_slope = (ma5 - float(df.iloc[-6]['ma5'])) / max(float(df.iloc[-6]['ma5']), 1) * 100 \
        if len(df) >= 6 and 'ma5' in df.columns else 0
    if ma5_slope > 1.0:
        mom_slope = 25 if ma5_slope > 2.0 else 15
    elif ma5_slope > 0:
        mom_slope = 8
    else:
        mom_slope = 0  # 均线下行不扣分

    momentum_score = min(100, mom_macd + mom_rsi + mom_kdj + mom_slope)

    # ── 3. Risk Score (0~100, 越高越危险) ──
    # 远离均线风险（价格远离MA20 = 回调风险）
    abs_dist_ma20 = abs(dist_ma20)
    if abs_dist_ma20 > 20:
        risk_ma = min(40, 15 + (abs_dist_ma20 - 20) * 2)
    elif abs_dist_ma20 > 10:
        risk_ma = 5 + (abs_dist_ma20 - 10) * 1.0
    elif abs_dist_ma20 > 5:
        risk_ma = (abs_dist_ma20 - 5) * 1.0
    else:
        risk_ma = 0

    # 量比极端风险
    if vr > 3.0:
        risk_vol = min(30, 10 + (vr - 3.0) * 10)
    elif vr > 2.0:
        risk_vol = 5 + (vr - 2.0) * 5
    elif vr < 0.3:
        risk_vol = 20  # 极度缩量 = 流动性风险
    else:
        risk_vol = 0

    # 60日回撤风险
    high_60 = float(c.tail(60).max()) if len(c) >= 60 else cur
    dd60 = (high_60 - cur) / max(high_60, 1) * 100
    risk_dd = min(30, dd60 * 0.8)  # 回撤10%→8, 30%→24

    # 波动率风险（boll宽度/价格比例）
    boll_width = float(last.get('boll_width', 0))
    risk_volatility = min(20, boll_width * 50) if boll_width > 0 else 0

    risk_score = min(100, risk_ma + risk_vol + risk_dd + risk_volatility)

    # ── 4. CRE Score (0~100, 筹码轮换效率) ──
    # 价格在均线簇中的位置（越接近MA20中心越好）
    ma20_is_center = 1 - min(abs(dist_ma20) / 15, 1)  # 离MA20越近越好
    cre_ma_center = ma20_is_center * 30

    # 均线排列健康度（MA5 > MA10 > MA20 = 多头排列 = 轮换效率高）
    if ma5 > ma10 > ma20:
        cre_ma_align = 30
    elif ma5 > ma10 and ma10 > ma20:
        cre_ma_align = 20
    elif ma5 > ma20:
        cre_ma_align = 10
    else:
        cre_ma_align = 5

    # 短期波动收敛（价格在BOLL中轨附近）
    boll_mid = float(last.get('boll_mid', ma20))
    boll_dist = abs(cur - boll_mid) / max(boll_mid, 1) * 100
    if boll_dist < 3:
        cre_consolidate = 20
    elif boll_dist < 6:
        cre_consolidate = 12
    elif boll_dist < 10:
        cre_consolidate = 6
    else:
        cre_consolidate = 0

    # 成交量稳定性（避免放量异常 = 轮换平稳）
    vol_stable = max(0, 20 - abs(vr - 1.0) * 15)

    cre_score = min(100, cre_ma_center + cre_ma_align + cre_consolidate + vol_stable)

    # ── BQS = Flow×35% + Momentum×25% + (100-Risk)×20% + CRE×20% ──
    bqs = (flow_score * 0.35 + momentum_score * 0.25 +
           (100 - risk_score) * 0.20 + cre_score * 0.20)
    bqs = max(0, min(100, bqs))

    return {
        'flow_score': round(flow_score, 1),
        'momentum_score': round(momentum_score, 1),
        'risk_score': round(risk_score, 1),
        'cre_score': round(cre_score, 1),
        'bqs': round(bqs, 1),
    }


# ── 辅助函数 ──
def _ma(s, n):
    if len(s) < n:
        return float(s.iloc[-1]) if len(s) > 0 else 0.0
    return float(s.tail(n).mean())


# ============================================================
# 股池加载
# ============================================================

def load_qualified(csv_path: str = None) -> pd.DataFrame:
    """从 bull_stocks_qualified.csv 加载合格标的"""
    path = csv_path or _DEFAULT_POOL
    if not os.path.exists(path):
        logger.warning(f"股池文件不存在: {path}")
        # 尝试从 scan_chip_alpha_v5 最近结果加载
        alt = os.path.join(os.path.dirname(_DEFAULT_POOL), 'chip_alpha_v5_scan_result.csv')
        if os.path.exists(alt):
            logger.info(f"改用扫描结果: {alt}")
            return pd.read_csv(alt, dtype=str).fillna('')
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str).fillna('')
    logger.info(f"加载合格股池: {len(df)} 只标的")
    return df


# ============================================================
# 回测核心
# ============================================================

def backtest_stock(df: pd.DataFrame, params: dict = None,
                   min_history: int = 60) -> List[dict]:
    """
    对单只股票进行逐日BQS回测。
    每个交易日 T（有足够回看数据），计算BQS并记录未来N日收益。
    """
    if df is None or len(df) < min_history + 20:
        return []

    df = df.sort_values('trade_date').reset_index(drop=True)
    # 预计算全量指标（避免O(n²)重复计算）
    full_ind = calc_all_indicators(df)
    rows = []

    for t in range(min_history, len(df) - 20):
        window_ind = full_ind.iloc[:t+1]
        scores = compute_bqs_scores(window_ind, params)
        close_t = float(df.iloc[t]['close'])

        fwd = {}
        for n in [1, 3, 5, 10, 20]:
            if t + n < len(df):
                fwd[f'ret_{n}d'] = (float(df.iloc[t+n]['close']) / close_t - 1) * 100
            else:
                fwd[f'ret_{n}d'] = None

        rows.append({
            'trade_date': df.iloc[t]['trade_date'],
            'ts_code': df.iloc[t].get('ts_code', ''),
            'bqs': scores['bqs'],
            'flow_score': scores['flow_score'],
            'momentum_score': scores['momentum_score'],
            'risk_score': scores['risk_score'],
            'cre_score': scores['cre_score'],
            **fwd,
        })

    return rows


# ============================================================
# 评估
# ============================================================

# BQS分档
BQS_BINS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
BQS_LABELS = ['0-20', '20-40', '40-60', '60-80', '80-100']


def evaluate_bqs(all_results: List[dict]) -> pd.DataFrame:
    """
    按BQS分档分组统计胜率/收益/Sharpe。
    同时统计四因子单独的预测能力。
    """
    df = pd.DataFrame(all_results)
    if df.empty:
        return pd.DataFrame()

    ret_cols = [c for c in df.columns if c.startswith('ret_')]

    # ── BQS分档统计 ──
    groups = []
    df['bqs_bin'] = pd.cut(df['bqs'], bins=[b[0] for b in BQS_BINS] + [100],
                           labels=BQS_LABELS, right=False)

    for label, grp in df.groupby('bqs_bin', observed=True):
        row = {'分档': label, '信号数': len(grp)}
        for rc in ret_cols:
            valid = grp[rc].dropna()
            if len(valid) > 0:
                row[f'{rc}_胜率%'] = (valid > 0).mean() * 100
                row[f'{rc}_平均收益%'] = round(valid.mean(), 3)
                row[f'{rc}_中位收益%'] = round(valid.median(), 3)
                row[f'{rc}_P90'] = round(valid.quantile(0.9), 3)
                row[f'{rc}_P10'] = round(valid.quantile(0.1), 3)
                row[f'{rc}_Sharpe'] = round(valid.mean() / max(valid.std(), 0.1), 3)
            else:
                for suffix in ['胜率%', '平均收益%', '中位收益%', 'P90', 'P10', 'Sharpe']:
                    row[f'{rc}_{suffix}'] = 0
        groups.append(row)

    result = pd.DataFrame(groups)
    bin_order = {l: i for i, l in enumerate(BQS_LABELS)}
    result['_sort'] = result['分档'].map(bin_order)
    result = result.sort_values('_sort').drop(columns=['_sort'])

    # ── 单调性检验 ──
    ret_key_5d = 'ret_5d'
    monotonicity = ""
    if ret_key_5d in df.columns:
        bin_means = df.groupby('bqs_bin', observed=True)[ret_key_5d].mean()
        increasing = all(bin_means.iloc[i] <= bin_means.iloc[i+1]
                        for i in range(len(bin_means)-1)) if len(bin_means) >= 2 else False
        decreasing = all(bin_means.iloc[i] >= bin_means.iloc[i+1]
                        for i in range(len(bin_means)-1)) if len(bin_means) >= 2 else False
        if increasing:
            monotonicity = "✅ 单调递增（BQS越高收益越好）"
        elif decreasing:
            monotonicity = "✅ 单调递减（BQS越低收益越好，需检查符号）"
        else:
            monotonicity = "⚠️ 非单调"

    # ── 四因子Rank IC ──
    factor_cols = ['flow_score', 'momentum_score', 'risk_score', 'cre_score', 'bqs']
    rank_ic = {}
    for rc in ret_cols:
        valid = df[factor_cols + [rc]].dropna()
        if len(valid) < 20:
            continue
        for fc in factor_cols:
            ic = valid[fc].corr(valid[rc], method='spearman')
            rank_ic.setdefault(rc, {})[fc] = round(ic, 4)

    return result, monotonicity, rank_ic


# ============================================================
# 打印报告
# ============================================================

def print_bqs_report(result_df: pd.DataFrame, monotonicity: str,
                     rank_ic: dict, hold_days: int = 10):
    """打印BQS回测报告"""
    ret_key = f'ret_{hold_days}d'
    print()
    print("━" * 100)
    print("  BQS 回测结果 — 按分档统计")
    print("━" * 100)

    if result_df.empty:
        print("  (无信号)")
        return

    cols = ['分档', '信号数', f'{ret_key}_胜率%', f'{ret_key}_平均收益%',
            f'{ret_key}_中位收益%', f'{ret_key}_Sharpe']
    cols = [c for c in cols if c in result_df.columns]
    display = result_df[cols].copy()
    print(display.to_string(index=False))
    print()

    print(f"  单调性: {monotonicity}")
    print()

    # 打印所有持有期的胜率对比
    print("─" * 100)
    print("  BQS分档 × 持有期 胜率矩阵")
    print("─" * 100)
    winrate_cols = [c for c in result_df.columns if c.endswith('_胜率%')]
    if winrate_cols:
        wr = result_df[['分档'] + winrate_cols].copy()
        wr.columns = ['分档'] + [c.replace('_胜率%', '') for c in winrate_cols]
        print(wr.to_string(index=False))
    print()

    # 四因子Rank IC
    print("─" * 100)
    print("  四因子 + BQS Rank IC（预测未来收益的相关性）")
    print("─" * 100)
    if rank_ic:
        ic_df = pd.DataFrame(rank_ic).T
        ic_df.index.name = '持有期'
        print(ic_df.to_string())
    print()


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='BQS回测框架')
    parser.add_argument('--quick', action='store_true', help='快速模式')
    parser.add_argument('--stocks', type=int, default=None, help='限制股票数')
    parser.add_argument('--start', default='20260101', help='开始日期 YYYYMMDD')
    parser.add_argument('--end', default=None, help='结束日期 YYYYMMDD (默认昨天)')
    parser.add_argument('--output', default=None, help='保存明细CSV路径')
    parser.add_argument('--hold', type=int, default=5, help='主要关注持有期(默认5天)')
    parser.add_argument('--input', default=None, help='股池CSV路径')
    parser.add_argument('--min-history', type=int, default=60, help='最小历史数据天数')
    args = parser.parse_args()

    t_start = time.time()

    # 日期
    end_date = args.end or (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    start_date = args.start

    # ── 1. 加载合格股池 ──
    logger.info("加载合格股池...")
    qualified = load_qualified(args.input)
    if qualified.empty:
        logger.error("无合格标的，退出")
        sys.exit(1)

    # 提取代码列表
    code_col = None
    for col in ['ts_code', 'code', '代码']:
        if col in qualified.columns:
            code_col = col
            break
    if code_col is None:
        logger.error(f"股池中无代码列: {qualified.columns.tolist()}")
        sys.exit(1)
    codes = []
    for _, row in qualified.iterrows():
        code = str(row.get(code_col, '')).strip()
        if not code:
            continue
        # 补充后缀
        if not code.endswith('.SH') and not code.endswith('.SZ') and not code.endswith('.BJ'):
            fc = code.zfill(6)
            if fc.startswith('6') or fc.startswith('9'):
                code = fc + '.SH'
            elif fc.startswith('8') or fc.startswith('4'):
                code = fc + '.BJ'
            else:
                code = fc + '.SZ'
        codes.append(code)

    codes = list(set(codes))
    logger.info(f"共 {len(codes)} 只标的")

    # 快速模式限制
    if args.quick:
        np.random.seed(42)
        codes = np.random.choice(codes, min(50, len(codes)), replace=False).tolist()
        logger.info(f"快速模式: 随机选取 {len(codes)} 只")
    elif args.stocks:
        np.random.seed(42)
        codes = np.random.choice(codes, min(args.stocks, len(codes)), replace=False).tolist()
        logger.info(f"限制 {len(codes)} 只")

    # ── 2. 批量加载TDX日线 ──
    logger.info(f"从TDX批量加载日线 ({start_date} ~ {end_date})...")
    t0 = time.time()
    all_data = load_batch(codes, _TDX_ROOT, start_date=start_date,
                          end_date=end_date, min_records=args.min_history)
    logger.info(f"TDX加载完成: {len(all_data)} 只成功, 耗时{time.time()-t0:.0f}s")

    if not all_data:
        logger.error("无可用数据，退出")
        sys.exit(1)

    # ── 3. 逐股回测 ──
    logger.info(f"开始BQS回测 ({len(all_data)} 只)...")
    all_rows = []
    t0 = time.time()
    for i, (code, stock_df) in enumerate(all_data.items()):
        rows = backtest_stock(stock_df, min_history=args.min_history)
        all_rows.extend(rows)
        if (i + 1) % 50 == 0:
            logger.info(f"  [{i+1}/{len(all_data)}] {code}: {len(rows)} 条信号, "
                       f"累计{len(all_rows)} 条")

    elapsed = time.time() - t0
    logger.info(f"回测完成: {len(all_rows)} 条信号, {len(all_data)} 只股票, "
               f"耗时{elapsed:.0f}s")

    if not all_rows:
        logger.warning("无信号生成")
        return

    # ── 4. 评估 ──
    result_df, monotonicity, rank_ic = evaluate_bqs(all_rows)
    print_bqs_report(result_df, monotonicity, rank_ic, args.hold)

    # ── 5. 保存 ──
    if args.output:
        out_path = args.output
    else:
        out_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'report_daily',
            f'bqs_backtest_{end_date}.csv'
        )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pd.DataFrame(all_rows).to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"明细已保存: {out_path}")

    # ── 6. 总耗时 ──
    total_time = time.time() - t_start
    logger.info(f"总耗时: {total_time:.0f}s")
    print()
    print("━" * 100)
    print("  回测完成")
    print("━" * 100)


if __name__ == '__main__':
    main()
