# -*- coding: utf-8 -*-
"""TOP20 BullScore 个股实时行情分析 v2"""
import os, sys, time, datetime
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import numpy as np
import tushare as ts

# ── Tushare 设置 ─────────────────────────────────────
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# ── 股票列表（按 BullScore 排名）──────────────────────
TOP20 = [
    ("002602", "世纪华通"),
    ("002709", "天赐材料"),
    ("688002", "睿创微纳"),
    ("603659", "璞泰来"),
    ("002985", "北摩高科"),
    ("688183", "生益电子"),
    ("688525", "佰维存储"),
    ("603379", "三美股份"),
    ("603256", "宏和科技"),
    ("300476", "胜宏科技"),
    ("603893", "瑞芯微"),
    ("688519", "南亚新材"),
    ("300548", "长芯博创"),
    ("300604", "长川科技"),
    ("688127", "蓝特光学"),
    ("688025", "杰普特"),
    ("688313", "仕佳光子"),
    ("300475", "香农芯创"),
    ("001389", "广合科技"),
    ("002558", "巨人网络"),
]

def ts_code(code):
    if code.startswith('688') or (len(code) == 6 and int(code) >= 600000):
        return f"{code}.SH"
    elif code.startswith('4') or code.startswith('8'):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"

# ── 获取最近交易日 ───────────────────────────────────
trade_date = pro.trade_cal(exchange='SSE', end_date=datetime.date.today().strftime('%Y%m%d'))
if trade_date is not None and len(trade_date) > 0:
    is_open = trade_date[trade_date['is_open'] == 1]
    if len(is_open) > 0:
        LAST_TRADE = is_open.iloc[-1]['cal_date']
    else:
        LAST_TRADE = datetime.date.today().strftime('%Y%m%d')
else:
    LAST_TRADE = datetime.date.today().strftime('%Y%m%d')

print(f"最近交易日: {LAST_TRADE}")

# ── 获取今日实时行情（pro.daily 包含今收/今开/最高/最低）────
today_str = datetime.date.today().strftime('%Y%m%d')
print("获取今日行情数据...")
today_data = {}
try:
    df_today = pro.daily(trade_date=today_str)
    if df_today is not None and len(df_today) > 0:
        for _, row in df_today.iterrows():
            today_data[row['ts_code']] = {
                'close': row['close'],
                'pct_chg': row['pct_chg'],
                'vol': row['vol'],
                'amount': row.get('amount', 0),
            }
        print(f"  今日数据: {len(today_data)} 只")
except Exception as e:
    print(f"  今日数据获取失败（使用昨日）: {e}")

# ── 获取日线历史（MA/布林带/RSI）───────────────────────
def get_indicators(ts_code_full):
    try:
        # 取最近60个交易日
        df = pro.daily(ts_code=ts_code_full, limit=60)
        if df is None or len(df) == 0:
            return None
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].astype(float)

        # MA
        ma5 = closes.rolling(5).mean().iloc[-1]
        ma10 = closes.rolling(10).mean().iloc[-1]
        ma20 = closes.rolling(20).mean().iloc[-1]
        ma30 = closes.rolling(30).mean().iloc[-1]
        ma60 = closes.rolling(60).mean().iloc[-1] if len(df) >= 60 else closes.ewm(span=60).mean().iloc[-1]

        # 昨收（今日前的收盘价，用于计算今涨跌）
        prev_close = closes.iloc[-2] if len(closes) >= 2 else closes.iloc[-1]

        # RSI-14
        delta = closes.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = (100 - 100 / (1 + rs)).iloc[-1]

        # 布林带（20日）
        boll_mid = closes.rolling(20).mean().iloc[-1]
        boll_std = closes.rolling(20).std().iloc[-1]
        boll_upper = boll_mid + 2 * boll_std
        boll_lower = boll_mid - 2 * boll_std

        # 近期高低点
        high20 = closes.tail(20).max()
        low20 = closes.tail(20).min()
        high60 = closes.tail(60).max()
        low60 = closes.tail(60).min()

        # 趋势判断
        if ma5 > ma20 > ma30:
            trend = 'strong_up'
        elif ma5 > ma20:
            trend = 'up'
        elif ma5 < ma20:
            trend = 'down'
        else:
            trend = 'neutral'

        return {
            'close': closes.iloc[-1],
            'prev_close': prev_close,
            'pct_chg': (closes.iloc[-1] / prev_close - 1) * 100 if prev_close > 0 else 0,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma30': ma30, 'ma60': ma60,
            'rsi': rsi,
            'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
            'high20': high20, 'low20': low20, 'high60': high60, 'low60': low60,
            'trend': trend,
        }
    except Exception as e:
        return None

# ── 分析师数据 ───────────────────────────────────────
ANALYST_DATA = {
    "002602": {"赛道": "数据要素/AI游戏",   "profit_yoy": 447.2, "roe": 25.4,  "pe_avg": 40},
    "002709": {"赛道": "锂电电解液",         "profit_yoy": 180.9, "roe": 34.5,  "pe_avg": 30},
    "688002": {"赛道": "红外/商业航天",       "profit_yoy": 124.1, "roe": 25.2,  "pe_avg": 45},
    "603659": {"赛道": "负极/氟化工",         "profit_yoy": 88.2,  "roe": 14.7,  "pe_avg": 28},
    "002985": {"赛道": "军工航空航天",         "profit_yoy": 952.3, "roe": 10.3,  "pe_avg": 55},
    "688183": {"赛道": "PCB/AI服务器",        "profit_yoy": 343.8, "roe": 29.0,  "pe_avg": 35},
    "688525": {"赛道": "存储芯片/AI算力",      "profit_yoy": 520.2, "roe": 135.2, "pe_avg": 65},
    "603379": {"赛道": "氟化工制冷剂",         "profit_yoy": 163.8, "roe": 23.2,  "pe_avg": 25},
    "603256": {"赛道": "高端玻纤/PCB",         "profit_yoy": 785.5, "roe": 20.2,  "pe_avg": 40},
    "300476": {"赛道": "PCB/AI服务器",         "profit_yoy": 273.5, "roe": 29.6,  "pe_avg": 35},
    "603893": {"赛道": "AI芯片/端侧",          "profit_yoy": 74.8,  "roe": 27.9,  "pe_avg": 55},
    "688519": {"赛道": "PCB覆铜板",             "profit_yoy": 377.6, "roe": 20.8,  "pe_avg": 32},
    "300548": {"赛道": "光通信器件",            "profit_yoy": 175.1, "roe": 40.9,  "pe_avg": 45},
    "300604": {"赛道": "半导体测试设备",         "profit_yoy": 187.7, "roe": 28.2,  "pe_avg": 55},
    "688127": {"赛道": "光学元组件",             "profit_yoy": 76.7,  "roe": 22.2,  "pe_avg": 40},
    "688025": {"赛道": "激光/光通信",            "profit_yoy": 124.5, "roe": 16.1,  "pe_avg": 45},
    "688313": {"赛道": "光芯片/AI算力",           "profit_yoy": 473.2, "roe": 28.0,  "pe_avg": 55},
    "300475": {"赛道": "存储模组",               "profit_yoy": 169.5, "roe": 110.6, "pe_avg": 40},
    "001389": {"赛道": "PCB/服务器",             "profit_yoy": 50.2,  "roe": 21.8,  "pe_avg": 30},
    "002558": {"赛道": "AI+游戏",                "profit_yoy": 23.6,  "roe": 28.6,  "pe_avg": 35},
}

# ── 买点/止损/目标价计算 ──────────────────────────────
def calc_levels(code, ind, cur, ma5, ma10, ma20, rsi, boll_l, boll_m, trend):
    """计算买/卖/止损"""
    fin = ANALYST_DATA.get(code, {})

    # ── 买点逻辑 ──
    # 1. 强势股（MA5>MA20）：等回踩MA5 或 布林下轨支撑
    # 2. 震荡股：布林下轨买
    # 3. RSI超卖（<35）：MA10支撑买
    candidates = []
    if ma5 > 0:
        candidates.append(('MA5', ma5))
    if boll_l > 0 and boll_l < cur * 0.95:
        candidates.append(('布林下轨', boll_l))
    if ma10 > 0:
        candidates.append(('MA10', ma10))
    if rsi < 40 and ma20 > 0:
        candidates.append(('MA20/RSI底', ma20))

    if not candidates:
        entry = cur * 0.97
        entry_logic = '现价-3%'
    else:
        # 取最接近当前价的下方支撑（安全边际）
        below = [(n, v) for n, v in candidates if v < cur]
        if below:
            best = min(below, key=lambda x: abs(x[1] - cur))
        else:
            best = candidates[-1]  # 用最低支撑
        entry = round(best[1], 2)
        entry_logic = best[0]

    # ── 止损 ──
    # 强势股：MA20止损（跌破趋势结束）
    # 普通：入场价下方8-10%
    if trend == 'strong_up':
        stop = round(ma20, 2) if ma20 > 0 else round(entry * 0.93, 2)
    elif rsi < 40:
        stop = round(ma20 * 0.97, 2) if ma20 > 0 else round(entry * 0.90, 2)
    else:
        stop = round(entry * 0.91, 2)

    stop_pct = round((1 - stop / entry) * 100, 1) if entry > 0 else 0

    # ── 目标价（1年）──
    # 保守：当前价 × (1 + 利润增速 × 0.3)
    # 合理：当前价 × (1 + 利润增速 × 0.5)
    profit_yoy = fin.get('profit_yoy', 50) / 100
    target_conservative = round(cur * (1 + profit_yoy * 0.30), 2)
    target_reasonabl = round(cur * (1 + profit_yoy * 0.45), 2)
    target_aggressive = round(cur * (1 + profit_yoy * 0.60), 2)
    upside_conservative = round((target_conservative / cur - 1) * 100, 1) if cur > 0 else 0

    # ── 风险收益比 ──
    risk = round(entry - stop, 2)
    reward_conservative = round(target_conservative - entry, 2)
    rr = round(reward_conservative / risk, 1) if risk > 0 else 0

    return {
        'entry': entry,
        'entry_logic': entry_logic,
        'stop': stop,
        'stop_pct': stop_pct,
        'target_con': target_conservative,
        'target_reason': target_reasonabl,
        'target_aggr': target_aggressive,
        'upside': upside_conservative,
        'rr': rr,
    }

# ── 主流程 ────────────────────────────────────────────
print(f"\n{'='*90}")
print(f"BullScore TOP20 个股技术分析  —  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  数据截止: {LAST_TRADE}")
print(f"{'='*90}\n")

results = []
for code, name in TOP20:
    tc = ts_code(code)
    ind = get_indicators(tc)
    fin = ANALYST_DATA.get(code, {})

    if ind is None:
        print(f"[跳过] {name} ({code}) — 无数据")
        continue

    cur = ind['close']
    ma5 = ind['ma5']
    ma10 = ind['ma10']
    ma20 = ind['ma20']
    ma60 = ind['ma60']
    rsi = ind['rsi']
    boll_l = ind['boll_lower']
    boll_m = ind['boll_mid']
    trend = ind['trend']
    pct_chg = ind['pct_chg']

    levels = calc_levels(code, fin, cur, ma5, ma10, ma20, rsi, boll_l, boll_m, trend)

    results.append({
        'code': code, 'name': name,
        'ts_code': tc,
        'close': cur,
        'pct_chg': pct_chg,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'rsi': rsi,
        'boll_u': ind['boll_upper'], 'boll_m': boll_m, 'boll_l': boll_l,
        'high20': ind['high20'], 'low20': ind['low20'],
        'trend': trend,
        **fin,
        **levels,
    })

    time.sleep(0.12)

# ── 打印结果 ──────────────────────────────────────────
print(f"\n{'代码':>6} {'名称':<8} {'现价':>7} {'涨跌%':>6} {'MA5':>7} {'MA20':>7} {'MA60':>7} {'RSI14':>6} {'趋势':<10}")
print('-'*80)
for r in results:
    pct = r['pct_chg']
    pct_str = f"{pct:+.2f}%" if r['close'] > 0 else "N/A"
    print(f"{r['code']:>6} {r['name']:<8} {r['close']:>7.2f} {pct_str:>8} "
          f"{r['ma5']:>7.2f} {r['ma20']:>7.2f} {r['ma60']:>7.2f} "
          f"{r['rsi']:>6.1f} {r['trend']:<10}")
    time.sleep(0.05)

print(f"\n{'='*90}")
print("【买点 / 止损 / 目标价建议】")
print(f"{'='*90}")
print(f"{'代码':>6} {'名称':<8} {'现价':>7} {'买点':>7} {'止损':>7} {'止损%':>5} {'目标(保守)':>10} {'目标(合理)':>10} {'上涨空间':>8} {'风险收益':>7}")
print('-'*90)
for r in results:
    if r['close'] <= 0:
        continue
    print(f"{r['code']:>6} {r['name']:<8} {r['close']:>7.2f} "
          f"{r['entry']:>7.2f} {r['stop']:>7.2f} {r['stop_pct']:>5.1f} "
          f"{r['target_con']:>10.2f} {r['target_reason']:>10.2f} "
          f"{r['upside']:>+7.1f}% {r['rr']:>6.1f}x")

print(f"\n{'='*90}")
print("【核心分析摘要】")
print(f"{'='*90}")

COMMENTS = {
    "002602": "利润暴增447%超预期，AI+游戏双主线。RSI 36低位低吸机会，MA5上方可建仓，止损设布林下轨。赛道优质，回调MA5是最佳买点。",
    "002709": "锂电电解液龙头，ROE 34.5%顶级，利润增速181%。回踩MA5（52元）低吸，止损设MA10（50元）或-10%。上涨空间充足。",
    "688002": "红外+商业航天双轮驱动，RSI 66偏强。已突破布林上轨，等回踩MA5（142元）再买，止损设MA20（131元），上涨空间约50%。",
    "603659": "负极材料+氟化工双主业，利润增速88%。RSI 52健康，趋势完好，等回踩布林下轨，止损-10%，稳健标的。",
    "002985": "军工航空航天，利润暴增952%超高速。RSI 32低位低吸机会强！现价29.54已是支撑区间，等反弹至MA5（30元）突破跟进，止损设布林下轨。",
    "688183": "PCB龙头深度受益AI服务器，利润增速344%。回踩MA5（137元）或布林中轨（128元）是买点，止损布林下轨，上涨空间充足。",
    "688525": "存储芯片龙头，利润暴增520%，RSI 68偏热。短线追高需谨慎，等回踩MA10（328元附近），止损设MA20（315元）。",
    "603379": "氟化工制冷剂龙头，RSI 55健康。等回踩布林下轨（价格需计算），止损-10%，赛道持续受益制冷剂景气。",
    "603256": "高端玻纤利润暴增786%爆发，ROE 20.2%。强势股，等回踩5日线买入，止损布林中轨。",
    "300476": "PCB+AI服务器受益标的，ROE 29.6%，RSI 52量价健康。回踩MA5（359元）是买点，止损MA20（354元）或-9%。",
    "603893": "AI芯片设计龙头，瑞芯微RSI 61偏强。等回踩10日线（价格需关注），止损-10%，端侧AI需求持续。",
    "688519": "覆铜板材料受益算力需求，利润增速378%亮眼，RSI 77超买！需等回踩布林中轨或MA10再买，止损设MA20。",
    "300548": "光通信器件龙头，ROE 40.9%顶级，RSI 62。趋势完美，回踩5日线（280元）是理想买点，止损布林下轨。",
    "300604": "半导体测试设备龙头，ROE 28.2%，RSI 65高位。强势股等回调-8%以内可分批建仓，止损设MA20。",
    "688127": "光学元组件，RSI 55估值合理。回踩布林下轨（76元附近）或MA10是买点，止损布林下轨-8%。",
    "688025": "激光设备受益光通信扩产，RSI 59偏强。回踩5日线（440元）或布林中轨买入，止损MA20或-10%。",
    "688313": "光芯片龙头深度受益AI算力，利润暴增473%，RSI 72偏高。高位强势，等回踩10日线（173元）再买，止损-10%。",
    "300475": "存储模组需求旺盛，RSI 73偏高偏热。需等待回踩布林中轨或MA5（239元）附近，止损MA20（195元），否则空间有限。",
    "001389": "服务器PCB供应商，ROE 21.8%，RSI 53健康。回踩10日线（186元）是买点，止损布林中轨，上涨空间约50%。",
    "002558": "AI+游戏双概念，ROE 28.6%优质。RSI 36低位低位机会，等反弹至MA5（24.5元）突破跟进，止损布林下轨。",
}

for r in results:
    code = r['code']
    comment = COMMENTS.get(code, '')
    print(f"\n【{r['name']} ({code})】")
    print(f"  现价: {r['close']:.2f} | 今日涨跌: {r['pct_chg']:+.2f}% | RSI: {r['rsi']:.0f}")
    print(f"  赛道: {r.get('赛道','')} | 利润增速: {r.get('profit_yoy',0):.1f}% | ROE: {r.get('roe',0):.1f}%")
    print(f"  均线: MA5={r['ma5']:.2f} / MA20={r['ma20']:.2f} / MA60={r['ma60']:.2f}")
    print(f"  布林: {r['boll_l']:.2f} ~ {r['boll_m']:.2f} ~ {r['boll_u']:.2f}")
    print(f"  ✅ 建议买点: {r['entry']:.2f} 元（{r['entry_logic']}）")
    print(f"  🔻 止损点:   {r['stop']:.2f} 元（-{r['stop_pct']:.1f}%）")
    print(f"  🎯 目标价:  {r['target_con']:.2f}（保守）~ {r['target_reason']:.2f}（合理）~ {r['target_aggr']:.2f}（乐观）")
    print(f"  📈 上涨空间: {r['upside']:+.1f}% | 风险收益比: {r['rr']:.1f}x")
    print(f"  💬 点评: {comment}")

print(f"\n{'='*90}")
print(f"⚠️  免责声明: 本分析仅供参考，不构成投资建议。RSI>75为超买，RSI<35为超卖。")
print(f"   止损设置根据个人风险承受能力调整。")
