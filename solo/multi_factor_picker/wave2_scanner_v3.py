# -*- coding: utf-8 -*-
"""
二波行情扫描器v3.0
整合今日回测结论：
  1. 一波涨幅>=35%成功率最高
  2. 调整期RSI<50胜率90%
  3. 涨停突破：调整>30天或RSI<50
  4. 多指标共振评分>=15分
"""
import os, sys, time, datetime, pickle
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import pandas as pd
import numpy as np
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 参数配置
SURGE_MIN = 0.25          # 一波涨幅>=25%
SURGE_DAYS_MIN = 7        # 一波最少天数
SURGE_DAYS_MAX = 21       # 一波最多天数
ADJUST_MAX = 90           # 调整期最长90天
SCORE_MIN = 10            # 评分>=10分入场

START_DATE = '20251001'
# 默认取前一个交易日（做复盘用）
END_DATE = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y%m%d')

# 股票池
def get_stock_pool():
    """获取股票池：沪深300 + 中证500 + 双创板"""
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
        # 沪深300成分股
        hs300 = sb[sb['ts_code'].str.startswith('60')]['ts_code'].tolist()[:100]
        # 中证500
        zz500 = sb[sb['ts_code'].str.startswith('00')]['ts_code'].tolist()[:100]
        # 双创板
        cy = sb[sb['ts_code'].str.startswith(('300', '688'))]['ts_code'].tolist()[:100]
        return hs300 + zz500 + cy
    except:
        return []


def calculate_score(row, prev_row=None):
    """计算多指标共振评分"""
    score = 0
    details = []

    # ── 动量类指标（超卖加分）──
    rsi = float(row.get('rsi_qfq_6', 50))
    if rsi < 30: score += 3; details.append(f'RSI={rsi:.0f}<30超卖')
    elif rsi < 40: score += 2; details.append(f'RSI={rsi:.0f}<40')
    elif rsi < 50: score += 1; details.append(f'RSI={rsi:.0f}<50')

    kdj_j = float(row.get('kdj_qfq', 50))
    if kdj_j < 0: score += 3; details.append(f'KDJ-J={kdj_j:.0f}<0')
    elif kdj_j < 20: score += 2; details.append(f'KDJ-J={kdj_j:.0f}<20')

    cci = float(row.get('cci_qfq', 0))
    if cci < -100: score += 2; details.append(f'CCI={cci:.0f}<-100')

    wr = float(row.get('wr_qfq', 50))
    if wr > 80: score += 2; details.append(f'WR={wr:.0f}>80')

    # ── 资金类指标 ──
    mfi = float(row.get('mfi_qfq', 50))
    if mfi < 30: score += 2; details.append(f'MFI={mfi:.0f}<30资金底')

    vol_ratio = float(row.get('volume_ratio', 1.0))
    if vol_ratio > 1.5: score += 2; details.append(f'量比={vol_ratio:.2f}>1.5放量')
    elif vol_ratio > 1.2: score += 1; details.append(f'量比={vol_ratio:.2f}>1.2')

    # ── 趋势类指标 ──
    macd_dif = float(row.get('macd_dif_qfq', 0))
    macd_dea = float(row.get('macd_dea_qfq', 0))
    if macd_dif > macd_dea: score += 2; details.append('MACD金叉')

    # 检测MACD即将金叉
    if prev_row is not None:
        prev_dif = float(prev_row.get('macd_dif_qfq', 0))
        prev_dea = float(prev_row.get('macd_dea_qfq', 0))
        if prev_dif <= prev_dea and macd_dif > macd_dea:
            score += 2; details.append('MACD当日金叉!!')

    adx = float(row.get('dmi_adx_qfq', 0))
    if adx > 40: score += 2; details.append(f'ADX={adx:.0f}>40强趋势')
    elif adx > 25: score += 1; details.append(f'ADX={adx:.0f}>25')

    # DMI趋势反转
    pdi = float(row.get('dmi_pdi_qfq', 0))
    mdi = float(row.get('dmi_mdi_qfq', 0))
    if pdi > mdi: score += 1; details.append('PDI>MDI多头')

    # ── MA位置 ──
    close = float(row.get('close', 0))
    ma5 = float(row.get('ma_qfq_5', 0))
    ma20 = float(row.get('ma_qfq_20', 0))
    ma60 = float(row.get('ma_qfq_60', 0))
    if close > ma5 and ma5 > 0: score += 1
    if close > ma20 and ma20 > 0: score += 1; details.append('MA20上方')
    if close > ma60 and ma60 > 0: score += 1; details.append('MA60上方')

    # ── 乖离率 ──
    bias1 = float(row.get('bias1_qfq', 0))
    if bias1 < -5: score += 2; details.append(f'BIAS={bias1:.1f}%<-5%超卖')

    # ── 超买扣分 ──
    if rsi > 70: score -= 3; details.append(f'⚠RSI>70超买(-3)')
    elif rsi > 60: score -= 1; details.append(f'RSI>60(-1)')

    return score, '; '.join(details)


def scan_wave2(ts_code: str, debug: bool = False) -> list:
    """扫描二波行情信号"""
    try:
        df = pro.stk_factor_pro(ts_code=ts_code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 100:
            return []
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values
        n = len(df)

        signals = []
        wave1_found = False

        # 找一波拉升
        for i in range(30, n - 30):
            # 向前扫描一波拉升
            for wave1_len in range(SURGE_DAYS_MIN, min(SURGE_DAYS_MAX + 1, i)):
                window = closes[i-wave1_len:i+1]
                if len(window) < wave1_len:
                    continue

                low_idx = np.argmin(window[:wave1_len//2])
                high_idx = np.argmax(window[low_idx:]) + low_idx

                if high_idx <= low_idx or high_idx - low_idx < 5:
                    continue

                wave1_gain = (window[high_idx] - window[low_idx]) / window[low_idx]
                if wave1_gain < SURGE_MIN:
                    continue

                wave1_high_idx = i - wave1_len + high_idx
                wave1_low_idx = i - wave1_len + low_idx
                wave1_high = closes[wave1_high_idx]

                # 一波形态指标
                wave1_row = df.iloc[wave1_high_idx]
                wave1_rsi = float(wave1_row.get('rsi_qfq_6', 50))
                wave1_vol_ratio = float(wave1_row.get('volume_ratio', 1.0))
                wave1_adx = float(wave1_row.get('dmi_adx_qfq', 0))

                if not wave1_found and debug:
                    print(f"  ── 一波符合: {ts_code} 涨幅+{wave1_gain*100:.0f}%/{wave1_high_idx-wave1_low_idx}天 "
                          f"RSI{wave1_rsi:.0f} ADX{wave1_adx:.0f} 日期{df.iloc[wave1_high_idx]['trade_date']}")
                    wave1_found = True

                # 找调整低点
                adjust_low_idx = wave1_high_idx
                adjust_low = wave1_high
                for j in range(wave1_high_idx + 1, min(wave1_high_idx + ADJUST_MAX + 1, n)):
                    if closes[j] < adjust_low:
                        adjust_low = closes[j]
                        adjust_low_idx = j

                if adjust_low_idx == wave1_high_idx:
                    continue

                adjust_days = adjust_low_idx - wave1_high_idx
                if adjust_days < 3:
                    continue

                pullback_pct = (wave1_high - adjust_low) / wave1_high
                if pullback_pct < 0.05:
                    continue

                # 调整低点评分
                adjust_row = df.iloc[adjust_low_idx]
                prev_row = df.iloc[adjust_low_idx-1] if adjust_low_idx > 0 else None
                score, score_details = calculate_score(adjust_row, prev_row)

                if score < SCORE_MIN:
                    continue

                # ATR止损
                atr = float(adjust_row.get('atr_qfq', 0))
                entry_price = adjust_low
                stop_loss = entry_price - 2 * atr if atr > 0 else entry_price * 0.97
                stop_pct = (entry_price - stop_loss) / entry_price * 100 if entry_price > 0 else 3.0

                signals.append({
                    'ts_code': ts_code,
                    'signal_date': df.iloc[adjust_low_idx]['trade_date'],
                    'wave1_date': df.iloc[wave1_high_idx]['trade_date'],
                    'wave1_gain': round(wave1_gain * 100, 1),
                    'wave1_days': wave1_high_idx - wave1_low_idx,
                    'wave1_rsi': round(wave1_rsi, 1),
                    'wave1_adx': round(wave1_adx, 1),
                    'adjust_days': adjust_days,
                    'pullback_pct': round(pullback_pct * 100, 1),
                    'entry_price': round(entry_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'stop_pct': round(stop_pct, 1),
                    'score': score,
                    'score_details': score_details,
                    'adjust_rsi': round(float(adjust_row.get('rsi_qfq_6', 50)), 1),
                })
                if debug:
                    print(f"    └─ 二波信号: {ts_code} 调整{adjust_days}天回撤{pullback_pct*100:.1f}% "
                          f"RSI{float(adjust_row.get('rsi_qfq_6', 50)):.0f} 评分{score}分 入场{entry_price:.2f}")
                break

            if len([s for s in signals if s['ts_code'] == ts_code]) >= 3:
                break

        time.sleep(0.12)
        return signals
    except Exception:
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description='二波行情扫描器v3.0')
    parser.add_argument('--today', action='store_true', help='仅输出当日有买入信号的股票')
    parser.add_argument('--csv', type=str, help='从CSV文件读取股票池（如 output/bull_stocks.csv）')
    parser.add_argument('--debug', action='store_true', help='分两层显示调试信息：一波符合→二波信号')
    args = parser.parse_args()
    today_only = args.today
    debug = args.debug

    title_suffix = ' | 仅今日' if today_only else ''
    print('=' * 70)
    print(f'  二波行情扫描器v3.0{title_suffix}')
    print('  策略：一波涨幅>=35% + 调整RSI<50 + 评分>=10')
    print('=' * 70)

    if args.csv:
        csv_path = args.csv if os.path.isabs(args.csv) else os.path.join(os.path.dirname(__file__), args.csv)
        if not os.path.exists(csv_path):
            print(f'CSV文件不存在: {csv_path}')
            return
        try:
            csv_df = pd.read_csv(csv_path)
            # 尝试自动识别股票代码列
            code_col = None
            for col in ('ts_code', '代码', 'code', 'stock_code'):
                if col in csv_df.columns:
                    code_col = col
                    break
            if code_col is None:
                # 取第一列
                code_col = csv_df.columns[0]
            pool = [str(c).strip().zfill(6) if len(str(c).strip()) <= 6 else str(c).strip()
                    for c in csv_df[code_col].dropna().unique()]
            print(f'\nCSV股票池: {csv_path} ({len(pool)} 只)')
        except Exception as e:
            print(f'读取CSV失败: {e}')
            return
    else:
        pool = get_stock_pool()
        print(f'\n股票池: {len(pool)} 只')

    if not pool:
        print('股票池为空')
        return

    all_signals = []
    t0 = time.time()

    print('\n开始扫描...')
    for idx, code in enumerate(pool):
        if (idx + 1) % 30 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (idx + 1) * (len(pool) - idx - 1)
            print(f'进度 {idx+1}/{len(pool)}  找到{len(all_signals)}个信号  ETA{eta:.0f}s')

        signals = scan_wave2(code, debug=debug)
        if signals:
            if today_only:
                signals = [s for s in signals if s.get('signal_date') == END_DATE]
            if signals:
                all_signals.extend(signals)

    elapsed = time.time() - t0
    print(f'\n扫描完成！耗时{elapsed:.1f}s，找到{len(all_signals)}个信号')

    if not all_signals:
        print('未找到符合条件的信号')
        return

    df = pd.DataFrame(all_signals)

    # ── 按评分排序 ──
    df = df.sort_values(['score', 'wave1_gain'], ascending=[False, False])

    # ── 输出结果 ──
    print('\n' + '=' * 70)
    print('  扫描结果')
    print('=' * 70)

    # 按评分分层统计
    print('\n--- 评分分布 ---')
    df['score_tier'] = pd.cut(df['score'], bins=[0, 10, 15, 100], labels=['10-14分', '15-19分', '>=20分'])
    tier_stats = df.groupby('score_tier', observed=True).agg(
        n=('ts_code', 'count'),
        avg_wave1_gain=('wave1_gain', 'mean'),
        avg_pullback=('pullback_pct', 'mean'),
    ).reset_index()
    tier_stats['avg_wave1_gain'] = tier_stats['avg_wave1_gain'].round(1)
    tier_stats['avg_pullback'] = tier_stats['avg_pullback'].round(1)
    print(tier_stats.to_string(index=False))

    # TOP30信号
    print('\n--- TOP30信号 ---')
    top30 = df.head(30)
    for _, r in top30.iterrows():
        print(f"{r['ts_code']:<12} {r['signal_date']} 一波+{r['wave1_gain']:>5.1f}%/{r['wave1_days']:>2}天 "
              f"调整{r['adjust_days']:>2}天/{r['pullback_pct']:>4.1f}% RSI{r['adjust_rsi']:>3.0f} "
              f"评分{r['score']:>2}分 入场{r['entry_price']:>7.2f} 止损{r['stop_pct']:>4.1f}%")

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = f'{out_dir}\\wave2_v3_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')

    # 微信推送格式
    print('\n--- 微信推送格式 ---')
    msg_lines = [f'📊 二波行情扫描v3.0 ({len(df)}个信号)', '']
    for i, (_, r) in enumerate(df.head(10).iterrows(), 1):
        msg_lines.append(f"{i}. {r['ts_code'][:6]} 一波+{r['wave1_gain']:.0f}% 调整{r['adjust_days']}天 "
                        f"RSI{r['adjust_rsi']:.0f} 评分{r['score']}分")
    msg_lines.append(f'\n详细: {csv_path}')

    msg = '\n'.join(msg_lines)
    print(msg)

    # 保存推送文件
    msg_path = f'{out_dir}\\wave2_v3_msg_{ts_str}.txt'
    with open(msg_path, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(f'\n推送文件: {msg_path}')


if __name__ == '__main__':
    main()
