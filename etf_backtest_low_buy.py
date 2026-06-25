"""
ETF低吸高胜率策略回测
测试7种低吸策略在37只ETF上的表现（2023-2026）
策略:
  S1: RSI<35 超卖买入
  S2: 价格回踩MA20均线
  S3: 价格回踩MA60均线
  S4: 从20日高点回落>10%（黄金坑）
  S5: 布林下轨反弹
  S6: MACD金叉
  S7: RSI<35 + MACD金叉共振
"""

import os, sys, time, json
import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta

os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

CACHE_DIR = r'D:\mystock\dragon\cache'
ETF_POOL = {
    '半导体': '512480.SH',
    '人工智能': '159819.SZ',
    '算力': '561210.SH',
    '机器人': '562500.SH',
    '软件': '515230.SH',
    '通信': '515880.SH',
    '新能源': '516160.SH',
    '光伏': '515790.SH',
    '储能': '159566.SZ',
    '军工': '512660.SH',
    '创新药': '159992.SZ',
    '消费电子': '159732.SZ',
    '黄金': '518880.SH',
    '证券': '512880.SH',
    '红利': '515180.SH',
    '银行': '512800.SH',
    '消费': '159928.SZ',
    '酒': '512690.SH',
    '电池': '159755.SZ',
    '有色金属': '516650.SH',
    '芯片': '159995.SZ',
    '化工': '159870.SZ',
    '半导体设备': '159516.SZ',
    '煤炭': '515220.SH',
    '游戏': '159869.SZ',
    '金融科技': '159851.SZ',
    '电力': '159611.SZ',
    '电网设备': '561380.SH',
    '新能源车': '515030.SH',
    '航空航天': '159227.SZ',
    '医疗器械': '159883.SZ',
    '食品饮料': '159736.SZ',
    '钢铁': '515210.SH',
}

# ─────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────
def load_etf_data(ts_code: str, start='20230101', end='20260624') -> pd.DataFrame:
    cache = os.path.join(CACHE_DIR, f'etf_{ts_code.replace(".","_")}_{end}.pkl')
    if os.path.exists(cache):
        try:
            df = pd.read_pickle(cache)
            return df
        except Exception:
            pass
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values('trade_date').reset_index(drop=True)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.rename(columns={'vol': 'volume'}, inplace=True)
        # 计算指标
        df['returns'] = df['close'].pct_change()
        df['ma5']   = df['close'].rolling(5).mean()
        df['ma10']  = df['close'].rolling(10).mean()
        df['ma20']  = df['close'].rolling(20).mean()
        df['ma60']  = df['close'].rolling(60).mean()
        df['ma120'] = df['close'].rolling(120).mean()
        # RSI
        delta = df['close'].diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss.clip(lower=1e-10)))
        # MACD
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = ema12 - ema26
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        # 布林带
        df['bb_mid']   = df['close'].rolling(20).mean()
        df['bb_std']   = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
        df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
        # 20日高点
        df['high_20d'] = df['close'].rolling(20).max()
        df['drawdown'] = (df['close'] - df['high_20d']) / df['high_20d']
        # 成交量比
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / df['vol_ma20']
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            df.to_pickle(cache)
        except Exception:
            pass
        return df
    except Exception as e:
        print(f'  [ERR] {ts_code}: {e}')
        return pd.DataFrame()


# ─────────────────────────────────────────────
# 7种低吸策略信号
# ─────────────────────────────────────────────
def signal_s1_rsi_oversold(row, prev):
    """S1: RSI<35 超卖买入，RSI>60 卖出"""
    rsi = row.get('rsi', 50)
    return 1 if rsi < 35 else (-1 if rsi > 60 else 0)

def signal_s2_ma20_pullback(row, prev):
    """S2: 价格回踩MA20均线（当天最低<MA20<当天收盘），收复MA20后卖出"""
    close = row['close']
    ma20 = row['ma20']
    low  = row['low']
    if pd.isna(ma20) or ma20 == 0:
        return 0
    # 买入：最低价触及MA20但收盘在MA20上方
    if low < ma20 <= close:
        return 1
    # 卖出：收盘跌破MA20
    if close < ma20:
        return -1
    return 0

def signal_s3_ma60_pullback(row, prev):
    """S3: 价格回踩MA60均线"""
    close = row['close']
    ma60 = row['ma60']
    low  = row['low']
    if pd.isna(ma60) or ma60 == 0:
        return 0
    if low < ma60 <= close:
        return 1
    if close < ma60:
        return -1
    return 0

def signal_s4_golden_pit(row, prev):
    """S4: 从20日高点回落>10%买入，涨回-5%卖出"""
    dd = row.get('drawdown', 0)
    if pd.isna(dd):
        return 0
    # 买入：回落>10%
    if dd < -0.10:
        return 1
    # 卖出：反弹>-5%（接近高点）
    if dd > -0.05:
        return -1
    return 0

def signal_s5_bb_lower(row, prev):
    """S5: 布林下轨反弹"""
    close = row['close']
    bb_l  = row.get('bb_lower', 0)
    bb_m  = row.get('bb_mid', 0)
    if pd.isna(bb_l) or bb_l == 0:
        return 0
    # 买入：触及下轨后反弹（收盘>下轨）
    if close > bb_l and row['low'] <= bb_l * 1.02:
        return 1
    # 卖出：回到中轨
    if close > bb_m:
        return -1
    return 0

def signal_s6_macd_cross(row, prev):
    """S6: MACD金叉买入，死叉卖出"""
    macd = row.get('macd', 0)
    sig  = row.get('macd_signal', 0)
    p_macd = prev.get('macd', 0) if prev is not None else 0
    p_sig  = prev.get('macd_signal', 0) if prev is not None else 0
    if pd.isna(macd) or pd.isna(sig):
        return 0
    # 金叉
    if p_macd <= p_sig and macd > sig:
        return 1
    # 死叉
    if p_macd >= p_sig and macd < sig:
        return -1
    return 0

def signal_s7_rsi_macd_combo(row, prev):
    """S7: RSI<35 + MACD金叉共振"""
    rsi = row.get('rsi', 50)
    macd = row.get('macd', 0)
    sig  = row.get('macd_signal', 0)
    p_macd = prev.get('macd', 0) if prev is not None else 0
    p_sig  = prev.get('macd_signal', 0) if prev is not None else 0
    if pd.isna(rsi) or pd.isna(macd):
        return 0
    golden_cross = (p_macd <= p_sig and macd > sig)
    if rsi < 35 and golden_cross:
        return 1
    # 卖出：RSI>65
    if rsi > 65:
        return -1
    return 0


STRATEGIES = {
    'S1_RSI超卖':    signal_s1_rsi_oversold,
    'S2_MA20回踩':   signal_s2_ma20_pullback,
    'S3_MA60回踩':   signal_s3_ma60_pullback,
    'S4_黄金坑':      signal_s4_golden_pit,
    'S5_布林下轨':    signal_s5_bb_lower,
    'S6_MACD金叉':    signal_s6_macd_cross,
    'S7_RSI+MACD共振': signal_s7_rsi_macd_combo,
}


# ─────────────────────────────────────────────
# 回测引擎
# ─────────────────────────────────────────────
def backtest_etf(df: pd.DataFrame, strategy_fn, name: str,
                 hold_days: int = 20, stop_loss: float = -0.05) -> dict:
    """固定持有N天后卖出，统计胜率/收益"""
    df = df.dropna(subset=['close', 'rsi']).copy()
    if len(df) < 60:
        return None

    trades = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        sig = strategy_fn(row, prev)

        if position is None:
            if sig == 1:
                position = {
                    'buy_date':  row['trade_date'],
                    'buy_price': row['close'],
                    'buy_idx':   i,
                }
        else:
            days_held = i - position['buy_idx']
            ret = (row['close'] - position['buy_price']) / position['buy_price']

            # 卖出条件
            should_sell = (
                sig == -1 or
                days_held >= hold_days or
                ret <= stop_loss
            )
            if should_sell:
                trades.append({
                    'name':      name,
                    'strategy':  name,
                    'buy_date':  position['buy_date'],
                    'buy_price': position['buy_price'],
                    'sell_date': row['trade_date'],
                    'sell_price': row['close'],
                    'hold_days': days_held,
                    'return':    ret,
                    'stopped':   ret <= stop_loss,
                    'timed':     days_held >= hold_days,
                    'signal_exit': sig == -1,
                })
                position = None

    if len(trades) < 3:
        return None

    rets = [t['return'] for t in trades]
    wins = [r for r in rets if r > 0]

    return {
        'strategy':     name,
        'n_trades':    len(trades),
        'win_rate':    len(wins) / len(rets),
        'avg_return':  np.mean(rets),
        'avg_hold':    np.mean([t['hold_days'] for t in trades]),
        'stop_rate':   sum(1 for t in trades if t['stopped']) / len(trades),
        'time_exit_rate': sum(1 for t in trades if t['timed']) / len(trades),
        'median_return': np.median(rets),
        'max_return':  max(rets),
        'min_return':  min(rets),
        'profit_factor': abs(sum(r for r in rets if r>0) / sum(r for r in rets if r<0)) if sum(r for r in rets if r<0) != 0 else 99,
    }


# ─────────────────────────────────────────────
# 主回测
# ─────────────────────────────────────────────
print("=" * 65)
print("  ETF低吸高胜率策略回测 (2023-2026)")
print("=" * 65)

all_results = []

for name, ts_code in ETF_POOL.items():
    print(f"\n[{name}] {ts_code} ...", end=' ', flush=True)
    df = load_etf_data(ts_code)
    if df.empty or len(df) < 60:
        print(f"数据不足({len(df)}行)，跳过")
        continue
    print(f"加载{df.shape[0]}行")

    for sname, sfn in STRATEGIES.items():
        res = backtest_etf(df, sfn, f'{sname}({name})')
        if res:
            res['etf'] = name
            res['ts_code'] = ts_code
            all_results.append(res)

print(f"\n\n{'='*65}")
print("  汇总结果（按胜率排序）")
print("=" * 65)

if not all_results:
    print("没有足够的交易记录")
    sys.exit(0)

results_df = pd.DataFrame(all_results)
results_df = results_df.sort_values('win_rate', ascending=False)

# 打印全局汇总
summary = results_df.groupby('strategy').agg({
    'win_rate':     'mean',
    'avg_return':   'mean',
    'median_return':'mean',
    'n_trades':     'sum',
    'stop_rate':    'mean',
    'avg_hold':     'mean',
    'profit_factor':'mean',
}).round(4)

summary = summary.sort_values('win_rate', ascending=False)
print(f"\n{'策略':<22} {'胜率':>7} {'均收益':>8} {'中位收益':>9} {'盈亏比':>7} {'止损率':>7} {'平均持仓':>8} {'总交易':>7}")
print("-" * 75)
for s, row in summary.iterrows():
    print(f"{s:<22} {row['win_rate']:>6.1%} {row['avg_return']:>7.2%} {row['median_return']:>8.2%} {row['profit_factor']:>7.2f} {row['stop_rate']:>6.1%} {row['avg_hold']:>7.1f}d {int(row['n_trades']):>6d}")

# 打印各ETF各策略详情（TOP10）
print(f"\n\n{'='*65}")
print("  各ETF策略明细（胜率TOP20）")
print("=" * 65)
top = results_df.nlargest(20, 'win_rate')
print(f"\n{'策略':<25} {'ETF':<8} {'胜率':>7} {'均收益':>8} {'中位收益':>9} {'盈亏比':>7} {'总交易':>6}")
print("-" * 75)
for _, r in top.iterrows():
    print(f"{r['strategy']:<25} {r['etf']:<8} {r['win_rate']:>6.1%} {r['avg_return']:>7.2%} {r['median_return']:>8.2%} {r['profit_factor']:>7.2f} {int(r['n_trades']):>5d}")

# 保存
out_dir = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(out_dir, exist_ok=True)
results_df.to_csv(os.path.join(out_dir, 'etf_low_buy_backtest.csv'), index=False, encoding='utf-8-sig')
summary.to_csv(os.path.join(out_dir, 'etf_low_buy_summary.csv'), encoding='utf-8-sig')
print(f"\n\n结果已保存到 {out_dir}")
