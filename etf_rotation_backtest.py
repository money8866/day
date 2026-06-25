"""
ETF轮动策略回测（2023-2026）
对比6种轮动策略，找出最优方案
"""

import os, sys, time
import numpy as np
import pandas as pd
import tushare as ts

os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

CACHE_DIR = r'D:\mystock\dragon\cache'
ETF_POOL = {
    '半导体': '512480.SH', '人工智能': '159819.SZ', '算力': '561210.SH',
    '机器人': '562500.SH', '软件': '515230.SH', '通信': '515880.SH',
    '新能源': '516160.SH', '光伏': '515790.SH', '储能': '159566.SZ',
    '军工': '512660.SH', '创新药': '159992.SZ', '消费电子': '159732.SZ',
    '黄金': '518880.SH', '证券': '512880.SH', '红利': '515180.SH',
    '银行': '512800.SH', '消费': '159928.SZ', '酒': '512690.SH',
    '电池': '159755.SZ', '有色金属': '516650.SH', '芯片': '159995.SZ',
    '化工': '159870.SZ', '半导体设备': '159516.SZ', '煤炭': '515220.SH',
    '游戏': '159869.SZ', '金融科技': '159851.SZ', '电力': '159611.SZ',
    '电网设备': '561380.SH', '新能源车': '515030.SH', '航空航天': '159227.SZ',
    '医疗器械': '159883.SZ', '食品饮料': '159736.SH', '钢铁': '515210.SH',
}

START = '20230101'
END   = '20260624'

def load_etf_data(ts_code: str) -> pd.DataFrame:
    df = pro.fund_daily(ts_code=ts_code, start_date=START, end_date=END)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.rename(columns={'vol': 'volume', 'pct_chg': 'ret'}, inplace=True)
    df['mom_20'] = df['close'].pct_change(20)
    df['mom_60'] = df['close'].pct_change(60)
    df['mom_5']  = df['close'].pct_change(5)
    delta = df['close'].diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - (100 / (1 + gain / loss.clip(lower=1e-10)))
    df['ma20']  = df['close'].rolling(20).mean()
    df['ma60']  = df['close'].rolling(60).mean()
    df['ma200'] = df['close'].rolling(200).mean()
    df['vol20'] = df['volume'].rolling(20).mean()
    df['vr']    = df['volume'] / df['vol20']
    df['atr20'] = df['close'].rolling(20).std()
    df['atr_ratio'] = df['atr20'] / df['close']
    return df

# ── 合并成一个大表 ──────────────────────────────────────────────────
print("加载所有ETF数据...")
all_dfs = {}
for name, code in ETF_POOL.items():
    print(f"  {name}...", end=' ', flush=True)
    df = load_etf_data(code)
    if not df.empty:
        df = df.rename(columns={
            'close': f'close_{name}', 'ret': f'ret_{name}',
            'mom_20': f'mom20_{name}', 'mom_60': f'mom60_{name}',
            'mom_5': f'mom5_{name}', 'rsi': f'rsi_{name}',
            'ma20': f'ma20_{name}', 'ma60': f'ma60_{name}',
            'ma200': f'ma200_{name}', 'vr': f'vr_{name}',
            'atr_ratio': f'atr_{name}',
        })
        all_dfs[name] = df[['trade_date', f'close_{name}', f'ret_{name}',
                             f'mom20_{name}', f'mom60_{name}', f'mom5_{name}',
                             f'rsi_{name}', f'ma20_{name}', f'ma60_{name}',
                             f'ma200_{name}', f'vr_{name}', f'atr_{name}']]
        print(f"OK({len(df)}行)")
    else:
        print(f"失败")

# 合并
base = list(all_dfs.values())[0][['trade_date']].copy()
for name, df in all_dfs.items():
    base = base.merge(df, on='trade_date', how='outer')

base = base.sort_values('trade_date').reset_index(drop=True)
base['trade_date'] = pd.to_datetime(base['trade_date'])
# 删除全是空的ETF列
base = base.loc[:, base.notna().sum() > base.shape[0] * 0.5]
print(f"\n合并后: {len(base)}个交易日, {len(all_dfs)}只ETF\n")

# ── 策略1: 60日动量轮动(基准) ──────────────────────────────────────
def strategy_momentum_60d(df, top_n=1, lookback=60, rebalance_days=20):
    """60日动量最强，等权轮动top_n，调仓周期rebalance_days"""
    names = list(ETF_POOL.keys())
    portfolio = []
    current_holdings = []
    last_rebalance = None

    for i in range(lookback+5, len(df)):
        date = df.iloc[i]['trade_date']
        if last_rebalance is not None and (i - last_rebalance) < rebalance_days:
            # 持有不动
            daily_ret = sum(df.iloc[i][f'ret_{n}'] / 100 for n in current_holdings) / len(current_holdings) if current_holdings else 0
            if portfolio and i > 0:
                portfolio[-1]['ret'] = daily_ret
            continue

        # 计算动量，用昨天的数据避免lookahead
        idx = i - 1
        scores = {}
        for n in names:
            col = f'mom60_{n}'
            if col in df.columns and not pd.isna(df.iloc[idx][col]):
                scores[n] = df.iloc[idx][col]

        if not scores:
            continue

        # 排序取top_n
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        current_holdings = [x[0] for x in top]

        if portfolio and len(portfolio[-1]['holdings']) > 0:
            # 今天换手，上一仓位收益计算到昨天
            last_ret = sum(df.iloc[i-1][f'ret_{n}'] / 100 for n in portfolio[-1]['holdings']) / len(portfolio[-1]['holdings']) if portfolio[-1]['holdings'] else 0
            portfolio[-1]['ret'] = last_ret

        portfolio.append({'date': date, 'holdings': current_holdings, 'scores': dict(top), 'ret': 0})
        last_rebalance = i

    # 计算累计收益
    if not portfolio:
        return 0, 0
    # 把每段收益乘起来
    total = 1.0
    peak = 1.0
    max_dd = 0
    for p in portfolio:
        r = p.get('ret', 0) or 0
        total *= (1 + r)
        peak = max(peak, total)
        dd = (peak - total) / peak
        max_dd = max(max_dd, dd)
        p['cum'] = total

    rets = [p.get('ret', 0) for p in portfolio if p.get('ret', 0) != 0]
    win_rate = sum(1 for r in rets if r > 0) / len(rets) if rets else 0
    return total - 1, max_dd

# ── 策略2: RSI均值回归轮动 ─────────────────────────────────────────
def strategy_rsi_reversion(df, ob=70, os=30, top_n=1, rebalance_days=20):
    """RSI超卖买，RSI超买卖出，轮动top_n"""
    names = list(ETF_POOL.keys())
    portfolio = []
    current_holdings = []
    last_rebalance = None

    for i in range(20+5, len(df)):
        date = df.iloc[i]['trade_date']
        if last_rebalance is not None and (i - last_rebalance) < rebalance_days:
            daily_ret = sum(df.iloc[i][f'ret_{n}'] / 100 for n in current_holdings) / len(current_holdings) if current_holdings else 0
            if portfolio:
                portfolio[-1]['ret'] = daily_ret
            continue

        # 选RSI最低的top_n只（超卖区边缘）
        scores = {}
        for n in names:
            col = f'rsi_{n}'
            if col in df.columns and not pd.isna(df.iloc[i][col]):
                rsi_val = df.iloc[i][col]
                # 排除趋势向下的（MA60下方）
                ma_col = f'ma60_{n}'
                above_ma = True
                if ma_col in df.columns and not pd.isna(df.iloc[i][ma_col]):
                    above_ma = df.iloc[i][f'close_{n}'] > df.iloc[i][ma_col]
                if rsi_val < os and above_ma:
                    scores[n] = rsi_val  # RSI越低越好

        if not scores:
            # 没有超卖，看RSI有没有高的要卖
            to_sell = [n for n in current_holdings if df.iloc[i][f'rsi_{n}'] > ob]
            if to_sell:
                current_holdings = [n for n in current_holdings if n not in to_sell]

            daily_ret = sum(df.iloc[i][f'ret_{n}'] / 100 for n in current_holdings) / max(len(current_holdings),1)
            if portfolio:
                portfolio[-1]['ret'] = daily_ret
            portfolio.append({'date': date, 'holdings': current_holdings[:], 'ret': 0})
            last_rebalance = i
            continue

        top = sorted(scores.items(), key=lambda x: x[1])[:top_n]  # RSI最低
        new_holdings = [x[0] for x in top]
        changed = set(new_holdings) != set(current_holdings)
        current_holdings = new_holdings

        if changed:
            if portfolio and len(portfolio[-1]['holdings']) > 0:
                last_ret = sum(df.iloc[i-1][f'ret_{n}'] / 100 for n in portfolio[-1]['holdings']) / len(portfolio[-1]['holdings'])
                portfolio[-1]['ret'] = last_ret
            portfolio.append({'date': date, 'holdings': current_holdings[:], 'scores': dict(top), 'ret': 0})
            last_rebalance = i

    if not portfolio:
        return 0, 0
    total = 1.0
    peak = 1.0
    max_dd = 0
    for p in portfolio:
        r = p.get('ret', 0) or 0
        total *= (1 + r)
        peak = max(peak, total)
        dd = (peak - total) / peak
        max_dd = max(max_dd, dd)
    rets = [p.get('ret', 0) for p in portfolio if p.get('ret', 0) != 0]
    win_rate = sum(1 for r in rets if r > 0) / len(rets) if rets else 0
    return total - 1, max_dd

# ── 策略3: 趋势跟随(MA200) ──────────────────────────────────────────
def strategy_ma200_trend(df, top_n=1, rebalance_days=20):
    """站上MA200买入，跌破MA200卖出"""
    names = list(ETF_POOL.keys())
    portfolio = []
    current_holdings = []
    last_rebalance = None

    for i in range(200+5, len(df)):
        date = df.iloc[i]['trade_date']
        if last_rebalance is not None and (i - last_rebalance) < rebalance_days:
            daily_ret = sum(df.iloc[i][f'ret_{n}'] / 100 for n in current_holdings) / max(len(current_holdings),1)
            if portfolio:
                portfolio[-1]['ret'] = daily_ret
            continue

        # 站上MA200且动量最强
        scores = {}
        for n in names:
            c_col = f'close_{n}'
            ma_col = f'ma200_{n}'
            mom_col = f'mom60_{n}'
            if c_col in df.columns and ma_col in df.columns and mom_col in df.columns:
                if not pd.isna(df.iloc[i][c_col]) and not pd.isna(df.iloc[i][ma_col]):
                    if df.iloc[i][c_col] > df.iloc[i][ma_col]:
                        scores[n] = df.iloc[i][mom_col]

        if not scores:
            current_holdings = []

        if scores:
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
            new_holdings = [x[0] for x in top]
        else:
            new_holdings = []

        changed = set(new_holdings) != set(current_holdings)
        current_holdings = new_holdings

        if changed:
            if portfolio and len(portfolio[-1]['holdings']) > 0:
                last_ret = sum(df.iloc[i-1][f'ret_{n}'] / 100 for n in portfolio[-1]['holdings']) / max(len(portfolio[-1]['holdings']),1)
                portfolio[-1]['ret'] = last_ret
            portfolio.append({'date': date, 'holdings': current_holdings[:], 'ret': 0})
            last_rebalance = i

    if not portfolio:
        return 0, 0
    total = 1.0
    peak = 1.0
    max_dd = 0
    for p in portfolio:
        r = p.get('ret', 0) or 0
        total *= (1 + r)
        peak = max(peak, total)
        dd = (peak - total) / peak
        max_dd = max(max_dd, dd)
    return total - 1, max_dd

# ── 策略4: 风险平价轮动 ─────────────────────────────────────────────
def strategy_risk_parity(df, lookback=60, rebalance_days=20, n=3):
    """波动率倒数加权，选动量最强n只"""
    names = list(ETF_POOL.keys())
    portfolio = []
    weights = {}
    current_holdings = []
    last_rebalance = None

    for i in range(lookback+5, len(df)):
        date = df.iloc[i]['trade_date']
        if last_rebalance is not None and (i - last_rebalance) < rebalance_days:
            daily_ret = sum(weights.get(h,0) * df.iloc[i][f'ret_{h}'] / 100 for h in current_holdings)
            if portfolio:
                portfolio[-1]['ret'] = daily_ret
            continue

        scores = {}
        for etf_name in names:
            col = f'mom60_{etf_name}'
            if col in df.columns and not pd.isna(df.iloc[i][col]):
                scores[etf_name] = df.iloc[i][col]

        if not scores:
            continue

        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:n]
        top_names = [x[0] for x in top]
        # 风险平价权重（ATR倒数）
        total_inv_atr = sum(1/max(df.iloc[i][f'atr_{x}'], 0.001) for x in top_names)
        weights = {x: (1/max(df.iloc[i][f'atr_{x}'], 0.001)) / total_inv_atr for x in top_names}

        current_holdings = top_names
        if portfolio and len(portfolio[-1]['holdings']) > 0:
            last_ret = sum(weights.get(h,0) * df.iloc[i-1][f'ret_{h}'] / 100 for h in portfolio[-1]['holdings'])
            portfolio[-1]['ret'] = last_ret

        portfolio.append({'date': date, 'holdings': top_names[:], 'weights': weights.copy(), 'ret': 0})
        last_rebalance = i

    if not portfolio:
        return 0, 0
    total = 1.0
    peak = 1.0
    max_dd = 0
    for p in portfolio:
        r = p.get('ret', 0) or 0
        total *= (1 + r)
        peak = max(peak, total)
        dd = (peak - total) / peak
        max_dd = max(max_dd, dd)
    return total - 1, max_dd

# ── 策略5: 双均线金叉/死叉轮动 ──────────────────────────────────────
def strategy_ma_cross(df, rebalance_days=10):
    """MA20上穿MA60金叉买，死叉卖"""
    names = list(ETF_POOL.keys())
    portfolio = []
    current_holdings = []
    last_rebalance = None

    for i in range(60+5, len(df)):
        date = df.iloc[i]['trade_date']
        if last_rebalance is not None and (i - last_rebalance) < rebalance_days:
            daily_ret = sum(df.iloc[i][f'ret_{n}'] / 100 for n in current_holdings) / max(len(current_holdings),1)
            if portfolio:
                portfolio[-1]['ret'] = daily_ret
            continue

        scores = {}
        for n in names:
            c20 = f'ma20_{n}'
            c60 = f'ma60_{n}'
            c60m = f'mom60_{n}'
            if c20 in df.columns and c60 in df.columns and c60m in df.columns:
                if pd.isna(df.iloc[i][c20]) or pd.isna(df.iloc[i][c60]):
                    continue
                prev_i = max(0, i-1)
                # 金叉
                if df.iloc[prev_i][c20] <= df.iloc[prev_i][c60] and df.iloc[i][c20] > df.iloc[i][c60]:
                    scores[n] = df.iloc[i][c60m]  # 动量排序

        if not scores:
            # 检查死叉
            for n in list(current_holdings):
                c20 = f'ma20_{n}'
                c60 = f'ma60_{n}'
                if pd.isna(df.iloc[i][c20]) or pd.isna(df.iloc[i][c60]):
                    continue
                prev_i = max(0, i-1)
                if df.iloc[prev_i][c20] >= df.iloc[prev_i][c60] and df.iloc[i][c20] < df.iloc[i][c60]:
                    current_holdings = [x for x in current_holdings if x != n]

        if scores:
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:1]
            current_holdings = [x[0] for x in top]

        if portfolio and set(current_holdings) != set(portfolio[-1].get('holdings',[])):
            if portfolio[-1].get('holdings'):
                last_ret = sum(df.iloc[i-1][f'ret_{n}'] / 100 for n in portfolio[-1]['holdings']) / max(len(portfolio[-1]['holdings']),1)
                portfolio[-1]['ret'] = last_ret
            portfolio.append({'date': date, 'holdings': current_holdings[:], 'ret': 0})
            last_rebalance = i

    if not portfolio:
        return 0, 0
    total = 1.0
    peak = 1.0
    max_dd = 0
    for p in portfolio:
        r = p.get('ret', 0) or 0
        total *= (1 + r)
        peak = max(peak, total)
        dd = (peak - total) / peak
        max_dd = max(max_dd, dd)
    return total - 1, max_dd

# ── 策略6: 相对强弱 + 绝对动量 ──────────────────────────────────────
def strategy_dual_filter(df, lookback=60, rebalance_days=20, top_n=1):
    """相对动量最强 + 绝对动量>0"""
    names = list(ETF_POOL.keys())
    portfolio = []
    current_holdings = []
    last_rebalance = None

    for i in range(lookback+5, len(df)):
        date = df.iloc[i]['trade_date']
        if last_rebalance is not None and (i - last_rebalance) < rebalance_days:
            daily_ret = sum(df.iloc[i][f'ret_{n}'] / 100 for n in current_holdings) / max(len(current_holdings),1)
            if portfolio:
                portfolio[-1]['ret'] = daily_ret
            continue

        scores = {}
        for n in names:
            col = f'mom60_{n}'
            if col in df.columns and not pd.isna(df.iloc[i][col]):
                if df.iloc[i][col] > 0:  # 绝对动量必须为正
                    scores[n] = df.iloc[i][col]

        if not scores:
            current_holdings = []
        else:
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
            current_holdings = [x[0] for x in top]

        if portfolio and set(current_holdings) != set(portfolio[-1].get('holdings',[])):
            if portfolio[-1].get('holdings'):
                last_ret = sum(df.iloc[i-1][f'ret_{n}'] / 100 for n in portfolio[-1]['holdings']) / max(len(portfolio[-1]['holdings']),1)
                portfolio[-1]['ret'] = last_ret
            portfolio.append({'date': date, 'holdings': current_holdings[:], 'ret': 0})
            last_rebalance = i

    if not portfolio:
        return 0, 0
    total = 1.0
    peak = 1.0
    max_dd = 0
    for p in portfolio:
        r = p.get('ret', 0) or 0
        total *= (1 + r)
        peak = max(peak, total)
        dd = (peak - total) / peak
        max_dd = max(max_dd, dd)
    return total - 1, max_dd

# ── 基准: 持有沪深300 ──────────────────────────────────────────────
def strategy_hs300_benchmark(df):
    """等权持有所有ETF"""
    names = list(ETF_POOL.keys())
    total_ret = 1.0
    peak = 1.0
    max_dd = 0
    daily_rets = []
    for i in range(200, len(df)):
        rets = [df.iloc[i][f'ret_{n}'] / 100 for n in names
                if f'ret_{n}' in df.columns and not pd.isna(df.iloc[i].get(f'ret_{n}'))]
        if not rets:
            continue
        daily = sum(rets) / len(rets)
        daily_rets.append(daily)
        total_ret *= (1 + daily)
        peak = max(peak, total_ret)
        dd = (peak - total_ret) / peak
        max_dd = max(max_dd, dd)

    if not daily_rets:
        return 0, 0, 0, 0
    annual_ret = (total_ret - 1) / (len(daily_rets) / 252)
    annual_vol = np.std(daily_rets) * np.sqrt(252)
    sharpe = annual_ret / annual_vol if annual_vol > 0 else 0
    return total_ret - 1, max_dd, sharpe, annual_ret

# ── 主回测 ─────────────────────────────────────────────────────────
print("=" * 70)
print("  ETF轮动策略回测 (2023-01 至 2026-06)")
print("=" * 70)

results = {}

print("\n[1] 60日动量轮动(单只)...")
ret, dd = strategy_momentum_60d(base, top_n=1, lookback=60, rebalance_days=20)
results['S1_60日动量单只'] = {'ret': ret, 'dd': dd}

print("[2] 60日动量轮动(3只)...")
ret, dd = strategy_momentum_60d(base, top_n=3, lookback=60, rebalance_days=20)
results['S1_60日动量3只'] = {'ret': ret, 'dd': dd}

print("[3] RSI均值回归(单只)...")
ret, dd = strategy_rsi_reversion(base, ob=70, os=30, top_n=1, rebalance_days=20)
results['S2_RSI均值回归'] = {'ret': ret, 'dd': dd}

print("[4] MA200趋势跟随...")
ret, dd = strategy_ma200_trend(base, top_n=1, rebalance_days=20)
results['S3_MA200趋势'] = {'ret': ret, 'dd': dd}

print("[5] 风险平价轮动...")
ret, dd = strategy_risk_parity(base, lookback=60, rebalance_days=20, n=3)
results['S4_风险平价'] = {'ret': ret, 'dd': dd}

print("[6] 双均线金叉/死叉...")
ret, dd = strategy_ma_cross(base, rebalance_days=10)
results['S5_MA金叉死叉'] = {'ret': ret, 'dd': dd}

print("[7] 双过滤(动量+绝对正)...")
ret, dd = strategy_dual_filter(base, lookback=60, rebalance_days=20, top_n=1)
results['S6_双过滤'] = {'ret': ret, 'dd': dd}

print("\n[基准] 等权持有...")
ret, dd, sharpe, annual_ret = strategy_hs300_benchmark(base)
results['基准_等权持有'] = {'ret': ret, 'dd': dd}

# 计算年化收益和夏普
trading_days = len(base)
years = trading_days / 252
print(f"\n\n{'='*70}")
print(f"  回测结果汇总 ({trading_days}交易日 ≈ {years:.1f}年)")
print("=" * 70)
print(f"{'策略':<22} {'总收益':>10} {'年化收益':>10} {'最大回撤':>10} {'夏普比率':>10}")
print("-" * 70)

for name, r in sorted(results.items(), key=lambda x: x[1]['ret'], reverse=True):
    ann_ret = r['ret'] / years if years > 0 else 0
    # 简化夏普：用收益/回撤比估算
    risk_adj = r['ret'] / max(r['dd'], 0.001) if r['dd'] > 0 else 99
    print(f"{name:<22} {r['ret']:>9.1%} {ann_ret:>9.1%} {r['dd']:>9.1%} {risk_adj:>9.2f}")

print("=" * 70)
best = sorted(results.items(), key=lambda x: x[1]['ret'], reverse=True)[0]
print(f"\n🏆 最佳策略: {best[0]}，总收益 {best[1]['ret']:.1%}")

# ── 网格搜索最优参数 ──────────────────────────────────────────────
print(f"\n{'='*70}")
print("  参数优化: 60日动量轮动")
print("=" * 70)
print(f"{'调仓周期':>8} {'持仓数量':>8} {'动量周期':>8} {'总收益':>10} {'年化':>9} {'最大回撤':>10} {'夏普':>7}")
print("-" * 60)

param_results = []
for rebal in [10, 20, 60]:
    for top in [1, 3]:
        for lb in [20, 60]:
            try:
                ret, dd = strategy_momentum_60d(base, top_n=top, lookback=lb, rebalance_days=rebal)
                ann = ret / years if years > 0 else 0
                sharpe_est = ann / max(dd, 0.001)
                param_results.append({
                    'rebal': rebal, 'top': top, 'lb': lb,
                    'ret': ret, 'ann': ann, 'dd': dd, 'sharpe': sharpe_est
                })
            except Exception:
                pass

best_params = sorted(param_results, key=lambda x: x['ret'], reverse=True)[:15]
for p in best_params:
    print(f"{p['rebal']:>7}d {p['top']:>7} {p['lb']:>7}d {p['ret']:>9.1%} {p['ann']:>8.1%} {p['dd']:>9.1%} {p['sharpe']:>6.2f}")

# 保存
out_dir = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(out_dir, exist_ok=True)
df_out = pd.DataFrame(results).T
df_out.columns = ['总收益', '最大回撤']
df_out['年化收益'] = df_out['总收益'] / years
df_out['夏普估算'] = df_out['年化收益'] / df_out['最大回撤'].clip(lower=0.001)
df_out.to_csv(os.path.join(out_dir, 'etf_rotation_results.csv'), encoding='utf-8-sig')

df_params = pd.DataFrame(param_results)
df_params = df_params.sort_values('ret', ascending=False)
df_params.to_csv(os.path.join(out_dir, 'etf_rotation_params.csv'), index=False, encoding='utf-8-sig')
print(f"\n结果已保存到 {out_dir}")
