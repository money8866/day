# -*- coding: utf-8 -*-
"""
深度对比 20260430 科创半导体 vs 20260515 机器人
直接调用集成好的主程序引擎，确保数据一致
"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")

import pandas as pd
import numpy as np
import time
from datetime import datetime
from dotenv import load_dotenv
import tushare as ts

load_dotenv(r"d:/mystock/config/.env")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

CACHE_DIR = r"D:\mystock\cache_daily"
ETF_FUND_DIR = os.path.join(CACHE_DIR, "etf_fund")
ETF_CONS_DIR = os.path.join(CACHE_DIR, "etf_cons")
os.makedirs(ETF_CONS_DIR, exist_ok=True)


def load_etf_fund(ts_code, end_date):
    """加载ETF基金日线，确保有60天以上数据"""
    fp = os.path.join(ETF_FUND_DIR, f"{ts_code}_{end_date}.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp)
    else:
        # 从Tushare下载
        import time
        df = pro.fund_daily(ts_code=ts_code,
                            start_date='20250101',
                            end_date=end_date)
        df.to_csv(fp, index=False)
        time.sleep(0.15)

    df['trade_date'] = pd.to_datetime(df['trade_date'], format="%Y%m%d")
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def load_index(index_code, end_date):
    """加载指数数据"""
    fp = os.path.join(CACHE_DIR, f"{index_code}.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp)
    else:
        df = pro.index_daily(ts_code=index_code, end_date=end_date)
        df.to_csv(fp, index=False)
        time.sleep(0.15)
    df['trade_date'] = pd.to_datetime(df['trade_date'], format="%Y%m%d")
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def get_constituents(etf_code, trade_date_str):
    """获取ETF成份股，带缓存"""
    cache_fp = os.path.join(ETF_CONS_DIR, f"cons_{etf_code.replace('.','_')}_{trade_date_str}.csv")
    if os.path.exists(cache_fp):
        return pd.read_csv(cache_fp)
    try:
        df = pro.index_weight(index_code=etf_code, start_date=trade_date_str, end_date=trade_date_str)
        if df is None or df.empty:
            df = pro.fund_portfolio(ts_code=etf_code, end_date=trade_date_str)
        if df is not None and not df.empty:
            df.to_csv(cache_fp, index=False)
        time.sleep(0.15)
        return df
    except Exception as e:
        print(f"    获取成份股失败: {e}")
        return pd.DataFrame()


def load_stock_data(ts_code, end_date, days=150):
    """加载个股日线"""
    cache_fp = os.path.join(ETF_CONS_DIR, f"stock_{ts_code.replace('.','_')}_{end_date}.csv")
    if os.path.exists(cache_fp):
        df = pd.read_csv(cache_fp)
    else:
        start = (datetime.strptime(end_date, "%Y%m%d") - pd.Timedelta(days=days)).strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end_date)
        if df is not None and not df.empty:
            df.to_csv(cache_fp, index=False)
        time.sleep(0.12)
    if df is None or df.empty:
        return None
    df['trade_date'] = pd.to_datetime(df['trade_date'], format="%Y%m%d")
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def analyze_case(name, etf_ts_code, trade_date_str, benchmark_code="000300.SH"):
    """完整分析一个案例"""
    print(f"\n{'═'*70}")
    print(f"  {name} ({etf_ts_code}) @ {trade_date_str}")
    print(f"{'═'*70}")

    # 加载ETF数据
    etf_df = load_etf_fund(etf_ts_code, trade_date_str)
    print(f"  ETF数据: {len(etf_df)} 天, {etf_df['trade_date'].iloc[0].strftime('%Y%m%d')} ~ {etf_df['trade_date'].iloc[-1].strftime('%Y%m%d')}")

    # 加载沪深300
    bm_df = load_index(benchmark_code, trade_date_str)

    # 获取成份股
    cons_df = get_constituents(etf_ts_code, trade_date_str)
    print(f"  成份股: {len(cons_df)} 只")

    # 加载成份股数据（前30只）
    stock_data = {}
    if not cons_df.empty:
        # 找成份股代码列 (symbol优先, 因为fund_portfolio的ts_code是ETF本身的代码)
        code_col = None
        for c in ['con_code', 'symbol', 'ts_code']:
            if c in cons_df.columns:
                code_col = c
                # 排除ts_code列全等于etf_code的情况
                if c == 'ts_code' and cons_df['ts_code'].nunique() == 1:
                    code_col = None
                    continue
                break
        if code_col:
            for _, row in cons_df.head(30).iterrows():
                con_code = str(row[code_col]).strip()
                if '.' in con_code:
                    # 已经是完整的ts_code格式 (如 688409.SH)
                    con_ts = con_code
                else:
                    # 纯数字代码，补全后缀
                    if con_code.startswith('6') or con_code.startswith('9'):
                        con_ts = f"{con_code}.SH"
                    elif con_code.startswith('0') or con_code.startswith('3'):
                        con_ts = f"{con_code}.SZ"
                    else:
                        continue
                sdf = load_stock_data(con_ts, trade_date_str)
                if sdf is not None and len(sdf) >= 25:
                    stock_data[con_ts] = sdf
            print(f"  加载成份股数据: {len(stock_data)} 只")

    # 计算后续实际收益
    future_end = (datetime.strptime(trade_date_str, "%Y%m%d") + pd.Timedelta(days=120)).strftime("%Y%m%d")
    etf_full = load_etf_fund(etf_ts_code, future_end)
    td = pd.to_datetime(trade_date_str, format="%Y%m%d")
    future_df = etf_full[etf_full['trade_date'] >= td].reset_index(drop=True)
    if len(future_df) > 1:
        base_price = future_df['close'].iloc[0]
        print(f"\n  【实际后续表现】")
        for h in [5, 10, 20, 40, 60]:
            if len(future_df) > h:
                ret = (future_df['close'].iloc[h] / base_price - 1) * 100
                print(f"    {h}日收益: {ret:+.2f}%")
        # 最大回撤（60日内）
        sub = future_df.head(60)
        running_max = sub['close'].cummax()
        dd = (sub['close'] / running_max - 1) * 100
        print(f"    60日最大回撤: {dd.min():.2f}%")
        # 最高收益
        print(f"    60日内最高收益: {(sub['close'].max() / base_price - 1) * 100:+.2f}%")

    # 调用主题持续性引擎
    from theme_persistence import ThemePersistenceEngine
    engine = ThemePersistenceEngine()
    result = engine.score_theme(
        theme_name=name,
        etf_code=etf_ts_code,
        etf_df=etf_df,
        benchmark_df=bm_df,
        stock_data=stock_data,
        trade_date=trade_date_str
    )

    print(f"\n  【主题持续性评分】: {result['persistence_score']:.1f} / 100")
    print(f"  状态: {result['theme_state']} | 信号: {result['investment_signal']} | 拥挤度: {result['crowding_score']:.1f}")
    print(f"  预期持续: {result['expected_duration']} | 轮动风险: {result['rotation_risk']}")

    print(f"\n  【六大模块细分】")
    print(f"    1. 趋势稳定性:   {result['trend_stability']:>5.1f}  (25%)")
    print(f"    2. 广度扩张:     {result['breadth_expansion']:>5.1f}  (25%)")
    print(f"    3. 龙头持续性:   {result['leader_persistence']:>5.1f}  (20%)")
    print(f"    4. 资金一致性:   {result['capital_consistency']:>5.1f}  (15%)")
    print(f"    5. 催化剂持续:   {result['catalyst_duration']:>5.1f}  (15%)")
    print(f"    6. 拥挤度惩罚:   {result['crowding_penalty']:>5.1f}  (-)")

    # 详细子项
    td = result.get('trend_detail', {})
    print(f"\n  【趋势稳定性详情】")
    print(f"    MA20={td.get('ma20', 0):.4f}, MA60={td.get('ma60', 0):.4f}")
    print(f"    20D收益={td.get('ret_20d', 0):+.2f}%, 60D收益={td.get('ret_60d', 0):+.2f}%, vs CSI300={td.get('rs_vs_csi300', 0):+.2f}%")
    print(f"    60日最大回撤={td.get('max_drawdown_60d', 0):.2f}%")

    bd = result.get('breadth_detail', {})
    print(f"\n  【广度扩张详情】")
    print(f"    站上MA20: {bd.get('above_ma20_count', 0)}/{bd.get('total_stocks', 0)} ({bd.get('breadth_ratio_pct', 0)}%)")
    print(f"    20日广度变化: {bd.get('breadth_change_20d', 0):+.1f}%")
    print(f"    强势股数: {bd.get('strong_stock_count', 0)}, 涨停数: {bd.get('limit_up_today', 0)}")

    ld = result.get('leader_detail', {})
    print(f"\n  【龙头持续性详情】")
    print(f"    Top5龙头: {ld.get('top_leaders', [])[:5]}")
    print(f"    平均RS百分位: {ld.get('avg_leader_rs', 0):.3f}")
    print(f"    健康龙头: {ld.get('healthy_leaders', 0)}/{ld.get('total_leaders', 0)}")
    print(f"    稳定性比: {ld.get('stability_ratio', 0):.3f}")

    cd = result.get('capital_detail', {})
    print(f"\n  【资金一致性详情】")
    print(f"    20D均额: {cd.get('avg_amount_20d', 0):.0f}, 60D均额: {cd.get('avg_amount_60d', 0):.0f}, 比率: {cd.get('amount_ratio', 0):.3f}")
    print(f"    近20日流入天数: {cd.get('inflow_days_20d', 0)}/20")
    print(f"    成交额变异系数: {cd.get('amount_cv', 0):.3f}")

    crd = result.get('crowding_detail', {})
    print(f"\n  【拥挤度详情】")
    print(f"    价格分位: {crd.get('price_percentile', 0):.1f}%, 量能分位: {crd.get('volume_percentile', 0):.1f}%, 波动分位: {crd.get('volatility_percentile', 0):.1f}%")

    print()
    return result


if __name__ == "__main__":
    # 案例1: 科创半导体 20260430 - 成功
    r1 = analyze_case("科创半导体", "588170.SH", "20260430")

    # 案例2: 机器人 20260515 - 失败
    r2 = analyze_case("机器人", "562500.SH", "20260515")

    # 对比总结
    print(f"\n{'═'*70}")
    print(f"  对比总结: 成功(科创半导体) vs 失败(机器人)")
    print(f"{'═'*70}")
    print(f"  {'维度':<18} {'科创半导体':>10} {'机器人':>10} {'差值':>8} {'关键?'}")
    print(f"  {'─'*55}")
    diff_total = r1['persistence_score'] - r2['persistence_score']
    print(f"  {'持续性总分':<18} {r1['persistence_score']:>10.1f} {r2['persistence_score']:>10.1f} {diff_total:>+8.1f}")

    dims = [
        ('趋势稳定性', 'trend_stability', 0.25),
        ('广度扩张', 'breadth_expansion', 0.25),
        ('龙头持续性', 'leader_persistence', 0.20),
        ('资金一致性', 'capital_consistency', 0.15),
        ('催化剂持续', 'catalyst_duration', 0.15),
        ('拥挤度惩罚', 'crowding_penalty', 1.0),
    ]
    for name, key, weight in dims:
        v1 = r1[key]
        v2 = r2[key]
        diff = v1 - v2
        weighted_diff = diff * weight
        key_marker = "★" if abs(weighted_diff) >= 3 else ""
        print(f"  {name:<18} {v1:>10.1f} {v2:>10.1f} {diff:>+8.1f} {key_marker}")

    print(f"\n  注释: ★ = 加权后贡献≥3分的关键差异")
    print(f"{'═'*70}")
