"""德方纳米 BWave第二波信号失败分析 + 当前底背离信号评估

分析两个问题:
  1. 第二波信号(06-18~07-08)为什么失败?
     - 同一个launch_idx=353被连续触发10+次
     - 价格从68跌到54,跌了-20%
  2. 当前底背离信号(07-08)是否值得介入?
     - 对比第一波成功的底背离信号(02-24)
     - 对比第二波失败的启动信号(06-18)
"""
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from bwave_strategy import (
    get_data, detect_awave, detect_bwave, detect_bwave_relaxed,
    check_launch_signal, detect_bwave_divergence,
    calc_bwave_score, calc_divergence_score,
)

TARGET = '300769.SZ'
NAME = '德方纳米'


def fmt_date(d) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """补充技术指标用于分析。"""
    df = df.copy()
    close = df['close'].values
    vol = df['vol'].values

    df['ma5'] = df['ma_bfq_5']
    df['ma10'] = df['ma_bfq_10']
    df['ma20'] = df['ma_bfq_20']
    df['ma60'] = df['ma_bfq_60']
    df['ma120'] = df['ma_120']

    df['dif'] = df['macd_dif_bfq']
    df['dea'] = df['macd_dea_bfq']
    df['macd'] = df['macd_bfq']
    df['rsi6'] = df['rsi_bfq_6']

    df['vol_ma5'] = pd.Series(vol).rolling(5, min_periods=1).mean().values
    df['vol_ma20'] = pd.Series(vol).rolling(20, min_periods=1).mean().values

    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=14).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=14).mean().values
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    df['rsi14'] = 100 - 100 / (1 + rs)

    df['atr14'] = df['atr']

    return df


def print_daily_table(df: pd.DataFrame, start_date: str, end_date: str, a_high: float, b_low: float):
    """打印指定区间的每日技术指标表。"""
    seg = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
    print(f"\n    {'日期':<12}{'收盘':>8}{'涨跌':>8}{'MA5':>8}{'MA20':>8}{'MA60':>8}{'DIF':>8}{'DEA':>8}{'MACD':>8}{'RSI6':>6}{'量比5/20':>10}")
    prev_close = None
    for _, r in seg.iterrows():
        chg = ((r['close'] / prev_close - 1) * 100) if prev_close else 0
        vr = r['vol_ma5'] / r['vol_ma20'] if r['vol_ma20'] > 0 else 0
        flag = ''
        if r['close'] < b_low:
            flag = ' ⚠破B低'
        if r['close'] > a_high:
            flag = ' ⚡破A高'
        print(f"    {fmt_date(r['trade_date']):<12}{r['close']:>8.2f}{chg:>+7.1f}%"
              f"{r['ma5']:>8.2f}{r['ma20']:>8.2f}{r['ma60']:>8.2f}"
              f"{r['dif']:>+8.3f}{r['dea']:>+8.3f}{r['macd']:>+8.3f}{r['rsi6']:>6.1f}{vr:>10.2f}{flag}")
        prev_close = r['close']


def analyze_wave2_failure(df: pd.DataFrame, awave: dict, bwave: dict):
    """分析第二波BWave信号(06-18~07-08)失败原因。"""
    print(f"\n{'='*78}")
    print(f"  [1] 第二波BWave信号失败分析 (2026-06-18 ~ 2026-07-08)")
    print(f"{'='*78}")

    a_high = awave['end_price']
    b_low = bwave['low_price']
    print(f"  A浪高点: {a_high:.2f} ({fmt_date(awave['end_date'])})")
    print(f"  B浪低点: {b_low:.2f} ({fmt_date(bwave['low_date'])})")

    launch = check_launch_signal(df, awave, bwave)
    if launch:
        launch_idx = launch['launch_idx']
        launch_date = df.iloc[launch_idx]['trade_date']
        launch_price = df.iloc[launch_idx]['close']
        print(f"\n  启动信号触发日: {fmt_date(launch_date)}  收盘 {launch_price:.2f}")
        print(f"  启动信号详情:")
        print(f"    MACD金叉={launch.get('macd_golden',0)}  RSI金叉={launch.get('rsi_golden',0)}  "
              f"MA5上穿={launch.get('ma5_crossing',0)}")
        print(f"    突破平台={launch.get('break_platform',0)}  放量={launch.get('vol_surge',0)}  "
              f"RSI6={launch.get('rsi6',0):.1f}")
        print(f"    B浪反弹{launch.get('b_recovery',0):.1f}%  距A高{launch.get('dist_to_a_high',0):.1f}%")

    print(f"\n  启动信号触发后逐日技术指标:")
    print_daily_table(df, '20260615', '20260708', a_high, b_low)

    print(f"\n  失败原因分析:")

    print(f"\n  ❶ 启动信号触发在B浪反弹高位,非低位")
    trigger_price = launch_price if launch else 68.0
    rebound_from_low = (trigger_price / b_low - 1) * 100
    dist_to_high = (a_high / trigger_price - 1) * 100
    print(f"     触发价 {trigger_price:.2f}, 距B低反弹 +{rebound_from_low:.1f}%, 距A高 -{dist_to_high:.1f}%")
    print(f"     对比: 第一波底背离触发价41.25, 距A高42.8%(更低位)")

    print(f"\n  ❷ MACD金叉是反弹后的假金叉(零轴下方)")
    seg = df[(df['trade_date'] >= '20260601') & (df['trade_date'] <= '20260708')]
    dif_at_signal = seg[seg['trade_date'] >= '20260615']['dif'].iloc[0]
    dea_at_signal = seg[seg['trade_date'] >= '20260615']['dea'].iloc[0]
    print(f"     触发时 DIF={dif_at_signal:.3f}  DEA={dea_at_signal:.3f}")
    if dif_at_signal < 0:
        print(f"     ⚠ DIF<0,MACD金叉在零轴下方=反弹假信号,非趋势反转")
    else:
        print(f"     DIF>0,MACD金叉在零轴上方")

    print(f"\n  ❸ 信号被连续重复触发(同一个launch_idx)")
    print(f"     launch_idx=353 被触发了10+次,但实际只信号了一次")
    print(f"     价格从68.22(06-18)跌到54.83(07-08),跌幅-19.7%")
    print(f"     说明: 信号触发后没有止损机制,持续持仓会大幅亏损")

    print(f"\n  ❹ 突破平台=False,只是反弹未突破")
    print(f"     启动信号要求'突破平台 or MA5上穿 or MA10上穿'")
    print(f"     实际: 突破平台=0, MA5上穿只在06-18触发1次,之后失效")

    print(f"\n  ❺ 量能配合是假象")
    vol_at_signal = df[df['trade_date'] >= '20260615']['vol'].iloc[0]
    vol_ma20_at_signal = df[df['trade_date'] >= '20260615']['vol_ma20'].iloc[0]
    print(f"     触发日量比 = {vol_at_signal/vol_ma20_at_signal:.2f}")
    print(f"     但后续量能未能持续,价格跌穿所有短期均线")


def analyze_current_divergence(df: pd.DataFrame, awave: dict, bwave: dict):
    """分析当前底背离信号(07-08)是否值得介入。"""
    print(f"\n{'='*78}")
    print(f"  [2] 当前底背离信号评估 (2026-07-08)")
    print(f"{'='*78}")

    a_high = awave['end_price']
    b_low = bwave['low_price']
    current_price = float(df['close'].values[-1])
    current_date = str(df['trade_date'].values[-1])

    print(f"  当前价格: {current_price:.2f} ({fmt_date(current_date)})")
    print(f"  距A高: -{(a_high/current_price-1)*100:.1f}%")
    print(f"  距B低: +{(current_price/b_low-1)*100:.1f}%")

    div = detect_bwave_divergence(df, awave, bwave)
    if div:
        score = calc_divergence_score(awave, bwave, div)
        print(f"\n  底背离信号详情:")
        print(f"    BWaveScore = {score['total']}")
        print(f"    A浪质量(a_score×30%): {score.get('a_score',0)} -> {score.get('a_score',0)*0.30:.1f}")
        print(f"    B浪健康(b_score×25%): {score.get('b_score',0)} -> {score.get('b_score',0)*0.25:.1f}")
        print(f"    底背离(d_score×30%): {score.get('d_score',0)} -> {score.get('d_score',0)*0.30:.1f}")
        print(f"    趋势保持(t_score×15%): {score.get('t_score',0)} -> {score.get('t_score',0)*0.15:.1f}")
        print(f"\n    底背离信号触发日: idx={div['launch_idx']}")
        print(f"    DIF抬高: {div.get('dif_recovery',0):.1f}%")
        print(f"    RSI确认: {div.get('rsi_higher',False)}")
        print(f"    MACD绿柱缩短: {div.get('macd_shrinking',False)}")
        print(f"    DIF上穿DEA: {div.get('dif_cross_dea',False)}")

    print(f"\n  近期逐日技术指标(06-01 ~ 07-08):")
    print_daily_table(df, '20260601', '20260708', a_high, b_low)

    print(f"\n  与第一波成功底背离(02-24)对比:")
    print(f"  {'指标':<20}{'第一波(02-24)成功':<22}{'当前(07-08)':<22}{'评估':<20}")
    print(f"  {'-'*84}")

    wave1_price = 41.25
    wave1_dist_high = 42.8
    wave1_b_drop = 29.1
    wave1_dif = -0.5
    wave1_rsi = 43.1

    cur_dist_high = (a_high / current_price - 1) * 100
    cur_dif = df['dif'].values[-1]
    cur_rsi = df['rsi6'].values[-1]

    comparisons = [
        ('触发价', f'{wave1_price:.2f}', f'{current_price:.2f}',
         '当前更高' if current_price > wave1_price else '当前更低'),
        ('距A高', f'-{wave1_dist_high:.1f}%', f'-{cur_dist_high:.1f}%',
         '当前更接近A高' if cur_dist_high < wave1_dist_high else '当前距A高更远'),
        ('B浪回调幅度', f'-{wave1_b_drop:.1f}%', f'-{bwave["drop"]:.1f}%',
         '当前回调更浅' if bwave['drop'] < wave1_b_drop else '当前回调更深'),
        ('DIF值', f'{wave1_dif:.3f}', f'{cur_dif:.3f}',
         '当前DIF更高' if cur_dif > wave1_dif else '当前DIF更低'),
        ('RSI6', f'{wave1_rsi:.1f}', f'{cur_rsi:.1f}',
         '当前RSI更低(超卖)' if cur_rsi < wave1_rsi else '当前RSI更高'),
    ]
    for label, v1, v2, assess in comparisons:
        print(f"  {label:<20}{v1:<22}{v2:<22}{assess:<20}")

    print(f"\n  底背离结构验证:")
    b_low_idx = bwave['low_idx']
    seg_before_low = df.iloc[max(0, b_low_idx-5):b_low_idx+1]
    seg_after_low = df.iloc[b_low_idx:b_low_idx+20]

    dif_at_low = df.iloc[b_low_idx]['dif']
    dif_at_low_minus5 = df.iloc[max(0,b_low_idx-5)]['dif']
    dif_now = df['dif'].values[-1]

    low_idx_in_seg = seg_before_low['close'].idxmin()
    if low_idx_in_seg in seg_before_low.index:
        dif_at_price_low = seg_before_low.loc[low_idx_in_seg, 'dif']

    print(f"    B浪低点({fmt_date(bwave['low_date'])}) DIF = {dif_at_low:.3f}")
    print(f"    当前 DIF = {dif_now:.3f}")
    if dif_now > dif_at_low:
        print(f"    ✅ DIF抬高 {(dif_now-dif_at_low)/abs(dif_at_low)*100:.1f}% -> 底背离成立")
    else:
        print(f"    ❌ DIF未抬高 -> 底背离不成立")

    price_at_low = df.iloc[b_low_idx]['close']
    if current_price < price_at_low and dif_now > dif_at_low:
        print(f"    ✅ 经典底背离: 价格新低({current_price:.2f}<{price_at_low:.2f}) + DIF抬高")
    elif current_price > price_at_low:
        print(f"    ⚠ 价格未创新低(当前{current_price:.2f} > B低{price_at_low:.2f})")
        print(f"       这不是标准底背离,只是B浪反弹后的回落")


def final_assessment(df: pd.DataFrame, awave: dict, bwave: dict):
    """综合评估当前是否值得介入。"""
    print(f"\n{'='*78}")
    print(f"  [3] 综合评估与决策建议")
    print(f"{'='*78}")

    current_price = float(df['close'].values[-1])
    a_high = awave['end_price']
    b_low = bwave['low_price']

    print(f"\n  📊 多空因素对比:")
    print(f"\n  ✅ 利多因素:")
    print(f"     1. 趋势保持满分100(MA60上方+缩量+ATR下降)")
    print(f"     2. A浪质量85分(涨幅85.2%+量比2.0+MA20上行)")
    print(f"     3. B浪回调-24.2%(健康回调区间20-35%)")
    print(f"     4. 底背离信号触发(BWaveScore=71.5)")

    print(f"\n  ❌ 利空因素:")
    cur_dif = df['dif'].values[-1]
    cur_dea = df['dea'].values[-1]
    cur_rsi = df['rsi6'].values[-1]
    ma5 = df['ma5'].values[-1]
    ma20 = df['ma20'].values[-1]
    ma60 = df['ma60'].values[-1]

    print(f"     1. 启动信号仅50分(MACD未金叉,DIF={cur_dif:.3f}<{cur_dea:.3f})")
    print(f"     2. RSI6={cur_rsi:.1f}(超卖区<30,有反弹需求但未确认)")
    print(f"     3. MA5={ma5:.2f} < MA20={ma20:.2f}(短期空头)")
    if current_price < b_low:
        print(f"     4. ⚠ 现价{current_price:.2f}已跌破B浪低点{b_low:.2f}!")
        print(f"        B浪结构可能失效,演变为下跌趋势")
    else:
        print(f"     4. 现价{current_price:.2f}仍高于B浪低点{b_low:.2f}")

    print(f"     5. 第二波启动信号(06-18)已失败,价格从68跌到54(-20%)")
    print(f"     6. 6月放量下跌,主力疑似出货")

    print(f"\n  🎯 关键判断:")
    if current_price < b_low:
        print(f"     ❌ 不建议介入")
        print(f"     原因: 价格已跌破B浪低点,波浪结构可能失效")
        print(f"     底背离信号虽然触发,但可能只是下跌中继的反弹")
        print(f"     对比第一波(02-24): 当时价格41.25在B低41.40附近,且后续MACD金叉确认")
    else:
        dist_to_blow = (current_price / b_low - 1) * 100
        if dist_to_blow < 5:
            print(f"     ⚠ 谨慎观望")
            print(f"     原因: 价格接近B浪低点(+{dist_to_blow:.1f}%),但启动信号未确认")
            print(f"     建议: 等待MACD金叉(DIF上穿DEA)+放量+RSI回到50以上再介入")
        else:
            print(f"     ⚠ 等待右侧确认")
            print(f"     原因: 价格距B低+{dist_to_blow:.1f}%,启动信号不足")
            print(f"     建议: 等待MACD金叉或突破MA20")

    print(f"\n  📌 操作建议:")
    print(f"     1. 激进策略: 当前价格轻仓试探(1/3仓),止损B低{b_low:.2f}下方3%={b_low*0.97:.2f}")
    print(f"     2. 稳健策略: 等MACD金叉确认后介入,止损B低下方")
    print(f"     3. 保守策略: 等价格站上MA20({ma20:.2f})再介入")
    print(f"     4. 不介入: 等价格跌破{b_low*0.97:.2f}放弃,或等新的大波段结构形成")

    print(f"\n  ⚠ 风险提示:")
    print(f"     1. 第二波信号已失败(-20%),说明BWave在下跌趋势中会反复触发假信号")
    print(f"     2. BWave没有止损机制,信号触发后持续持仓会扩大亏损")
    print(f"     3. 建议给BWave增加止损: 信号触发后跌破B低3%即止损")
    print(f"     4. 建议给BWave增加去重: 同一launch_idx只触发一次信号")


def main():
    print("=" * 78)
    print(f"  德方纳米({TARGET}) BWave深度分析")
    print(f"  第二波信号失败原因 + 当前底背离信号评估")
    print("=" * 78)

    df = get_data(TARGET)
    if df is None or len(df) < 250:
        print("数据不足")
        return

    df = enrich_indicators(df)
    print(f"历史数据: {fmt_date(df.iloc[0]['trade_date'])} ~ {fmt_date(df.iloc[-1]['trade_date'])} 共{len(df)}根")

    awave = detect_awave(df)
    if awave is None:
        print("未检测到A浪")
        return

    bwave = detect_bwave(df, awave)
    if bwave is None:
        bwave = detect_bwave_relaxed(df, awave)
    if bwave is None:
        print("未检测到B浪")
        return

    print(f"\n当前波浪结构:")
    print(f"  A浪: {fmt_date(awave['start_date'])}({awave['start_price']:.2f}) -> "
          f"{fmt_date(awave['end_date'])}({awave['end_price']:.2f})  +{awave['gain']:.1f}%")
    print(f"  B浪: 高{awave['end_price']:.2f} -> 低{bwave['low_price']:.2f}({fmt_date(bwave['low_date'])})  "
          f"-{bwave['drop']:.1f}%")

    analyze_wave2_failure(df, awave, bwave)
    analyze_current_divergence(df, awave, bwave)
    final_assessment(df, awave, bwave)


if __name__ == '__main__':
    main()
