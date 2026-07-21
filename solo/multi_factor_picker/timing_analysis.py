"""
57只DoubleScore通过股票的择时分析
基于多维度技术指标 + 资金流向的综合判断
"""
import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import OrderedDict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import DataFetcher
from loguru import logger

# 复用 main.py 的配置加载
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location("main_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
main_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_mod)
load_config = main_mod.load_config
get_token = main_mod.get_token

logger.remove()
logger.add(sys.stderr, level="INFO")


def _ma(arr, n):
    if len(arr) < n:
        return None
    return float(np.mean(arr[-n:]))


def _calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    arr = np.array(closes, dtype=float)
    ema_fast = pd.Series(arr).ewm(span=fast).mean().values
    ema_slow = pd.Series(arr).ewm(span=slow).mean().values
    dif = ema_fast - ema_slow
    dea = pd.Series(dif).ewm(span=signal).mean().values
    bar = 2 * (dif - dea)
    return dif[-1], dea[-1], bar[-1]


def _calc_kdj(highs, lows, closes, n=9):
    if len(closes) < n:
        return None, None, None
    recent_h = max(highs[-n:])
    recent_l = min(lows[-n:])
    if recent_h == recent_l:
        return 50, 50, 50
    rsv = (closes[-1] - recent_l) / (recent_h - recent_l) * 100
    k = rsv
    d = k
    for i in range(1, 3):
        k = (2/3) * k + (1/3) * rsv
        d = (2/3) * d + (1/3) * k
    j = 3 * k - 2 * d
    return k, d, j


def _calc_rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = 0, 0
    for i in range(-n, 0):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_bollinger(closes, n=20):
    if len(closes) < n:
        return None, None, None
    ma = np.mean(closes[-n:])
    std = np.std(closes[-n:])
    upper = ma + 2 * std
    lower = ma - 2 * std
    return upper, ma, lower


def _calc_vol_ratio(vols, n=5, m=20):
    if len(vols) < max(n, m):
        return None
    recent_avg = np.mean(vols[-n:])
    hist_avg = np.mean(vols[-m:])
    if hist_avg == 0:
        return None
    return recent_avg / hist_avg


def _calc_moneyflow_trend(moneyflow_series):
    """计算近5日主力资金净流入趋势"""
    if moneyflow_series is None or len(moneyflow_series) == 0:
        return None, None
    recent = moneyflow_series[-5:] if len(moneyflow_series) >= 5 else moneyflow_series
    total = sum(recent)
    avg = total / len(recent)
    return total, avg


def analyze_timing(ts_code: str, fetcher: DataFetcher) -> dict:
    """
    对单只股票做择时分析，返回综合评分和信号

    Returns:
        dict with keys: ts_code, name, trend, trend_score, signals, timing_score, grade, detail
    """
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')

    daily = fetcher.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date)
    if daily is None or len(daily) < 30:
        return None

    daily = daily.sort_values('trade_date').reset_index(drop=True)
    closes = daily['close'].values.astype(float)
    highs = daily['high'].values.astype(float)
    lows = daily['low'].values.astype(float)
    opens = daily['open'].values.astype(float)
    vols = daily['vol'].values.astype(float) if 'vol' in daily.columns else None
    amounts = daily['amount'].values.astype(float) if 'amount' in daily.columns else None

    # ── 1. 均线趋势 ──
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    price = float(closes[-1])

    trend_score = 50  # 基准分

    # 多头排列评分
    if ma5 and ma10 and ma20 and ma60:
        # 多头排列: MA5 > MA10 > MA20 > MA60
        if ma5 > ma10 > ma20 > ma60:
            if price > ma5:
                trend_score = 90  # 多头强势
            else:
                trend_score = 80  # 多头回调
        elif ma5 > ma10 > ma20:
            trend_score = 70  # 短中期多头
        elif ma5 > ma10 and ma20 > ma60:
            trend_score = 60  # 短期多头，中期震荡
        elif ma5 < ma10 < ma20:
            trend_score = 35  # 短期空头
        elif ma5 < ma10 < ma20 < ma60:
            trend_score = 20  # 空头排列
        else:
            trend_score = 45  # 震荡

    # 价格相对均线位置评分
    above_ma20_cnt = sum(1 for c in closes[-10:] if ma20 and c > ma20)
    if ma20 and above_ma20_cnt >= 8:
        trend_score = max(trend_score, 75)
    elif ma20 and above_ma20_cnt <= 2:
        trend_score = min(trend_score, 30)

    # ── 2. MACD ──
    dif, dea, bar = _calc_macd(closes)
    macd_signal = ''
    if dif is not None and dea is not None:
        # 金叉/死叉判断（最近3根K线）
        prev_dif, prev_dea = None, None
        if len(closes) > 30:
            prev_dif, prev_dea, _ = _calc_macd(closes[:-1])
        if prev_dif is not None and prev_dea is not None:
            if prev_dif <= prev_dea and dif > dea:
                macd_signal = 'MACD金叉'
            elif prev_dif >= prev_dea and dif < dea:
                macd_signal = 'MACD死叉'

    # ── 3. KDJ ──
    k, d, j = _calc_kdj(highs, lows, closes)
    kdj_signal = ''
    if k is not None and j is not None:
        if j < 20:
            kdj_signal = 'KDJ超卖'
        elif j > 100:
            kdj_signal = 'KDJ超买'
        elif k > d and j > 80:
            kdj_signal = 'KDJ高位金叉'

    # ── 4. RSI ──
    rsi = _calc_rsi(closes)
    rsi_signal = ''
    if rsi is not None:
        if rsi > 80:
            rsi_signal = 'RSI超买'
        elif rsi < 30:
            rsi_signal = 'RSI超卖'
        elif rsi > 60:
            rsi_signal = 'RSI强势'
        elif rsi > 50:
            rsi_signal = 'RSI偏强'
        else:
            rsi_signal = 'RSI偏弱'

    # ── 5. 布林带 ──
    upper, mid, lower = _calc_bollinger(closes)
    bollinger_signal = ''
    if upper is not None:
        if price >= upper:
            bollinger_signal = '布林上轨(压力)'
        elif price <= lower:
            bollinger_signal = '布林下轨(支撑)'
        elif price >= mid:
            bollinger_signal = '布林中轨上方'

    # ── 6. 成交量 ──
    vol_signal = ''
    vol_ratio = _calc_vol_ratio(vols) if vols is not None else None
    if vol_ratio is not None:
        if vol_ratio > 2.0:
            vol_signal = '放量(>2倍)'
        elif vol_ratio > 1.5:
            vol_signal = '放量(>1.5倍)'
        elif vol_ratio < 0.5:
            vol_signal = '缩量(<0.5倍)'
        elif vol_ratio < 0.7:
            vol_signal = '缩量'

    # ── 7. 资金流向 ──
    try:
        mf = fetcher.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
        mf_signal = ''
        mf_score = 0
        if mf is not None and len(mf) > 0:
            mf = mf.sort_values('trade_date').reset_index(drop=True)
            net_cols = [c for c in ['net_mf_vol', 'net_mf_amount', 'buy_elg_vol', 'sell_elg_vol',
                                     'buy_lg_vol', 'sell_lg_vol'] if c in mf.columns]
            if net_cols:
                recent_net = mf[net_cols].iloc[-5:].sum()
                if 'net_mf_amount' in recent_net.index:
                    net_amount = float(recent_net['net_mf_amount'])
                    if net_amount > 0:
                        mf_signal = '主力净流入'
                        mf_score = 15
                    else:
                        mf_signal = '主力净流出'
                        mf_score = -15
                elif 'net_mf_vol' in recent_net.index:
                    net_vol = float(recent_net['net_mf_vol'])
                    if net_vol > 0:
                        mf_signal = '主力净流入'
                        mf_score = 15
                    else:
                        mf_signal = '主力净流出'
                        mf_score = -15
        else:
            mf_signal = '无资金流数据'
    except Exception:
        mf_signal = ''
        mf_score = 0

    # ── 8. 近5日涨跌幅 ──
    if len(closes) >= 5:
        ret_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    else:
        ret_5d = 0
    if len(closes) >= 20:
        ret_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
    else:
        ret_20d = 0

    # ── 综合评分 ──
    # 趋势权重 50% + MACD/KDJ 20% + 成交量 10% + 资金流向 20%
    base = trend_score

    if macd_signal == 'MACD金叉':
        base += 10
    elif macd_signal == 'MACD死叉':
        base -= 15

    if kdj_signal == 'KDJ超买':
        base -= 10
    elif kdj_signal == 'KDJ超卖':
        base += 8

    if rsi_signal == 'RSI超买':
        base -= 5
    elif rsi_signal == 'RSI超卖':
        base += 5

    if vol_ratio is not None:
        if vol_ratio > 1.5 and ret_5d > 0:
            base += 8  # 价涨量增
        elif vol_ratio > 1.5 and ret_5d < 0:
            base -= 8  # 价跌量增
        elif vol_ratio < 0.6 and ret_5d > 0:
            base -= 3  # 价涨量缩（背离）

    base += mf_score

    # 近5日强势
    if ret_5d > 5:
        base += 5
    elif ret_5d < -5:
        base -= 5

    # 最终评分裁剪
    timing_score = max(0, min(100, base))

    # 评级
    if timing_score >= 80:
        grade = 'A'
    elif timing_score >= 65:
        grade = 'B'
    elif timing_score >= 50:
        grade = 'C'
    elif timing_score >= 35:
        grade = 'D'
    else:
        grade = 'E'

    # 信号汇总
    signals = [s for s in [macd_signal, kdj_signal, bollinger_signal, vol_signal, mf_signal] if s]

    # 趋势标签
    if trend_score >= 75:
        trend = '多头趋势'
    elif trend_score >= 55:
        trend = '震荡偏多'
    elif trend_score >= 40:
        trend = '震荡整理'
    else:
        trend = '空头趋势'

    detail = {
        'price': round(price, 2),
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma60': round(ma60, 2) if ma60 else None,
        'rsi': round(rsi, 1) if rsi else None,
        'macd_bar': round(bar, 2) if bar is not None else None,
        'ret_5d': round(ret_5d, 2),
        'ret_20d': round(ret_20d, 2),
        'vol_ratio': round(vol_ratio, 2) if vol_ratio else None,
        'trend_score': trend_score,
    }

    return {
        'ts_code': ts_code,
        'trend': trend,
        'trend_score': trend_score,
        'signals': ' | '.join(signals) if signals else '',
        'timing_score': timing_score,
        'grade': grade,
        'detail': detail,
    }


def main():
    # 读取DoubleScore结果
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
    csv_files = [f for f in os.listdir(report_dir) if f.startswith('double_score_') and f.endswith('.csv') and f != 'double_score_top.csv']
    if not csv_files:
        logger.error("未找到 double_score CSV 文件")
        return
    latest_csv = sorted(csv_files)[-1]
    csv_path = os.path.join(report_dir, latest_csv)
    logger.info(f"读取: {csv_path}")
    df = pd.read_csv(csv_path, encoding='utf-8-sig')

    fetcher = DataFetcher(get_token(load_config()), load_config())

    results = []
    for _, row in df.iterrows():
        code_raw = str(row['代码']).strip()
        # 补全6位代码 + 交易所后缀
        code_padded = code_raw.zfill(6)
        if code_padded.startswith('6') or code_padded.startswith('9'):
            code_num = code_padded + '.SH'
        else:
            code_num = code_padded + '.SZ'
        name = row['名称']

        logger.info(f"分析: {name} ({code_num})")
        result = analyze_timing(code_num, fetcher)
        if result is None:
            logger.warning(f"  {name}: 数据不足，跳过")
            continue

        result['name'] = name
        result['股票代码'] = code_raw
        result['DoubleScore'] = row.get('DoubleScore', 0)
        result['核心逻辑'] = row.get('核心逻辑', '')
        d = result['detail']
        results.append(result)

        logger.info(f"  → 评分={result['timing_score']}/{result['grade']} 趋势={result['trend']} 信号={result['signals']}")

    if not results:
        logger.error("没有分析出有效结果")
        return

    out_df = pd.DataFrame([{
        '股票代码': r['股票代码'],
        '名称': r['name'],
        'DoubleScore': r['DoubleScore'],
        '择时评分': r['timing_score'],
        '评级': r['grade'],
        '趋势': r['trend'],
        '信号': r['signals'],
        '核心逻辑': r['核心逻辑'],
        '现价': r['detail']['price'],
        'MA5': r['detail']['ma5'],
        'MA10': r['detail']['ma10'],
        'MA20': r['detail']['ma20'],
        'MA60': r['detail']['ma60'],
        'RSI': r['detail']['rsi'],
        '近5日涨幅%': r['detail']['ret_5d'],
        '近20日涨幅%': r['detail']['ret_20d'],
        '量比': r['detail']['vol_ratio'],
    } for r in results])

    out_df = out_df.sort_values('择时评分', ascending=False).reset_index(drop=True)
    out_df.index = out_df.index + 1
    out_df.index.name = '排名'

    # 输出
    print('\n' + '='*120)
    print(f'  57只黑马股择时分析 (基于 {latest_csv})')
    print('='*120)
    print(f'  {"排名":>4} {"代码":<10} {"名称":<8} {"评分":>5} {"评级":>3} {"趋势":<10} {"近5日%":>7} {"近20日%":>8} {"RSI":>5} {"信号":<40}')
    print('  ' + '-'*110)
    for i, r in enumerate(results, 1):
        d = r['detail']
        print(f'  {i:>4} {r["股票代码"]:<10} {r["name"]:<8} {r["timing_score"]:>5} {r["grade"]:>3} {r["trend"]:<10} {d["ret_5d"]:>7.2f} {d["ret_20d"]:>8.2f} {d["rsi"] if d["rsi"] else "N/A":>5} {r["signals"][:40]:<40}')

    print('\n' + '='*120)
    print(f'  评级分布:')
    for g in ['A', 'B', 'C', 'D', 'E']:
        cnt = sum(1 for r in results if r['grade'] == g)
        if cnt > 0:
            print(f'    {g}: {cnt} 家')
    print(f'  平均择时评分: {np.mean([r["timing_score"] for r in results]):.1f}')
    print('='*120)

    # 输出详细推荐
    print('\n' + '='*120)
    print(f'  ★ 重点关注 (A级 + B级)')
    print('='*120)
    for r in results:
        if r['grade'] in ('A', 'B'):
            d = r['detail']
            ma_str = f"MA5={d['ma5']} MA10={d['ma10']} MA20={d['ma20']}"
            print(f'  [{r["grade"]}] {r["name"]:8s} ({r["股票代码"]}) 评分={r["timing_score"]} 趋势={r["trend"]} {ma_str} 信号={r["signals"]}')
            print(f'       逻辑: {r["核心逻辑"]}')

    print('\n' + '='*120)
    print(f'  ⚠ 谨慎观望 (D级 + E级)')
    print('='*120)
    for r in results:
        if r['grade'] in ('D', 'E'):
            d = r['detail']
            print(f'  [{r["grade"]}] {r["name"]:8s} ({r["股票代码"]}) 评分={r["timing_score"]} 趋势={r["trend"]} 近5日={d["ret_5d"]:.1f}% 信号={r["signals"]}')

    # 保存CSV
    csv_out = os.path.join(report_dir, f'timing_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
    out_df.to_csv(csv_out, encoding='utf-8-sig', index=True, index_label='排名')
    print(f'\n结果已保存: {csv_out}')


if __name__ == '__main__':
    main()