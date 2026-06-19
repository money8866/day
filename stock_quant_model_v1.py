# -*- coding: utf-8 -*-
"""
个股量化基本面选股模型 v1.1
修复版：IB池正确使用total_score，IA池使用v7数据+news信号加成
"""

import sys, os, json, time, math
import numpy as np

# ========== 路径配置 ==========
BASE_DIR   = r'D:\mystock'
V7_FILE    = os.path.join(BASE_DIR, 'solo', 'report_daily', 'h1_超预期评分v7_20260619.json')
IB_FILE    = os.path.join(BASE_DIR, 'solo', 'report_daily', 'ib_h1_acceleration_20260619.json')
THEME_FILE = os.path.join(BASE_DIR, 'theme.json')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')

# ========== 因子权重 ==========
WEIGHTS = {'growth': 0.35, 'quality': 0.20, 'value': 0.15, 'momentum': 0.15, 'expectation': 0.15}

# ========== 新闻信号库（已验证）==========
NEWS_SIGNALS = {
    '688525.SH': {'order': 10, 'guidance': 10, 'capacity': 8, 'hot': True,
        'note': 'Q1净利28.99亿(H1预估50-60亿+)；18.6亿美元锁单；AI端侧存储11.75亿(+496%)'},
    '688498.SH': {'order': 7,  'guidance': 9,  'capacity': 7, 'hot': True,
        'note': '西部证券2026E:6.6亿(上调61%)；CW光源毛利率72%+'},
    '300438.SZ': {'order': 8,  'guidance': 10, 'capacity': 8, 'hot': True,
        'note': '东吴证券目标价121元(+169%)；Q1净利3.23亿超2025全年；储能扩产'},
    '301308.SZ': {'order': 5,  'guidance': 5,  'capacity': 6, 'hot': False,
        'note': 'AMD联合调优完成，商业化推进中'},
    '603268.SH': {'order': 5,  'guidance': 5,  'capacity': 3, 'hot': False,
        'note': 'v7满分10分；重组+军工+社保；数据待验证'},
    '688766.SH': {'order': 5,  'guidance': 5,  'capacity': 5, 'hot': False,
        'note': '存储芯片景气；Q1净利2.51亿'},
}

# ========== 工具函数 ==========
def sf(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except:
        return default

def pct_score(value, all_vals, reverse=False):
    """value在all_vals中的百分位，0-100"""
    valid = [sf(x) for x in all_vals if x is not None and math.isfinite(sf(x))]
    if not valid:
        return 50.0
    n = len(valid)
    count = sum(1 for v in valid if v <= sf(value))
    pct = count / n
    return (1 - pct) * 100 if not reverse else pct * 100

# ========== 因子计算 ==========
def growth_factor(stock, global_rev_yoys, global_ni_yoys, global_scores):
    """成长因子（0-100）"""
    # 方案：使用该股票池的score百分位 + 营收增速百分位
    score = sf(stock.get('score', stock.get('total_score', 0)))
    rev_yoy = sf(stock.get('q1_26_yoy', stock.get('q1_rev_yoy', 0)))
    ni_yoy  = sf(stock.get('q1_ni_yoy', 0))
    h1_accel = sf(stock.get('h1_accel', stock.get('acceleration_gap', 0)))

    # score百分位（跨池）
    score_p   = pct_score(score, global_scores)
    rev_p     = pct_score(rev_yoy, global_rev_yoys)
    ni_p      = pct_score(ni_yoy, global_ni_yoys)
    accel_p   = pct_score(h1_accel, global_scores)

    return rev_p * 0.25 + ni_p * 0.25 + score_p * 0.30 + accel_p * 0.20

def quality_factor(stock):
    """质量因子（0-100）：PE合理 + 营收规模 + 净利润增速质量"""
    pe   = sf(stock.get('pe', 0))
    rev  = sf(stock.get('q1_rev_yi', 0))
    ni   = sf(stock.get('q1_ni_yi', 0))

    # PE评分（高成长股允许高PE）
    if pe <= 0 or pe > 800:
        pe_s = 50
    elif pe < 20:
        pe_s = 90
    elif pe < 40:
        pe_s = 82
    elif pe < 80:
        pe_s = 72
    elif pe < 150:
        pe_s = 60
    else:
        pe_s = max(30, 70 - (pe - 150) / 20)

    # 营收规模
    if rev > 80:
        rev_s = 95
    elif rev > 30:
        rev_s = 85
    elif rev > 10:
        rev_s = 75
    elif rev > 3:
        rev_s = 65
    else:
        rev_s = 55

    # 净利含金（净利/营收比例）
    if rev > 0 and ni > 0:
        margin = ni / rev
        if margin > 0.2:
            margin_s = 90
        elif margin > 0.1:
            margin_s = 80
        elif margin > 0.05:
            margin_s = 70
        else:
            margin_s = max(40, 60 + (margin - 0.05) * 400)
    else:
        margin_s = 50

    return pe_s * 0.4 + rev_s * 0.3 + margin_s * 0.3

def value_factor(stock):
    """价值因子（0-100）"""
    pe = sf(stock.get('pe', 0))
    mc = sf(stock.get('market_cap_yi', 0))

    # PEG（考虑成长性）
    rev_yoy = abs(sf(stock.get('q1_26_yoy', stock.get('q1_rev_yoy', 0))))
    if pe > 0 and rev_yoy > 0:
        peg = pe / rev_yoy
        if peg < 0.5:   peg_s = 95
        elif peg < 1.0: peg_s = 85
        elif peg < 2.0: peg_s = 72
        else:           peg_s = max(30, 60 - (peg - 2.0) * 10)
    else:
        peg_s = 60

    # 市值（弹性）
    if mc < 100:   cap_s = 85
    elif mc < 300: cap_s = 80
    elif mc < 800: cap_s = 75
    else:          cap_s = 72

    return peg_s * 0.6 + cap_s * 0.4

def momentum_factor(stock):
    """动量因子（0-100）"""
    q1_mom = sf(stock.get('q1_mom', 0))
    if q1_mom > 150: return 92
    elif q1_mom > 80: return 85
    elif q1_mom > 30: return 75
    elif q1_mom > 0:  return 65
    else:             return max(35, 50 + q1_mom / 3)

def expectation_factor(stock):
    """预期因子（0-100）：新闻信号加成"""
    news = stock.get('news', {})
    if not news:
        return 50
    # 订单×0.5 + 指引×0.3 + 产能×0.2
    raw = news.get('order', 0) * 0.5 + news.get('guidance', 0) * 0.3 + news.get('capacity', 0) * 0.2
    return min(100, raw * 10)

# ========== 主程序 ==========
def load_data():
    print('📂 加载数据...')

    # v7 IA池
    with open(V7_FILE, 'r', encoding='utf-8') as f:
        v7 = json.load(f)
    ia_map = {r['code']: r for r in v7.get('all_results', [])}
    ia_sorted = v7.get('results', [])
    print(f'  IA池(v7)：{len(ia_map)}只')

    # IB池
    with open(IB_FILE, 'r', encoding='utf-8') as f:
        ib_data = json.load(f)
    ib_scored = ib_data.get('scored', [])
    ib_all_list = ib_data.get('all_results', [])
    # 优先用scored（有评分），没有用all_results
    ib_map = {}
    for r in ib_scored:
        ib_map[r['code']] = r
    for item in ib_all_list:
        code = item.get('code', '')
        if code not in ib_map:
            ib_map[code] = item
    print(f'  IB池：{len(ib_map)}只')

    # 主题
    theme_map = {}
    if os.path.exists(THEME_FILE):
        with open(THEME_FILE, 'r', encoding='utf-8') as f:
            theme_map = json.load(f)
    print(f'  主题映射：{len(theme_map)}只')

    return ia_map, ib_map, theme_map

def merge(code, ia_map, ib_map, theme_map):
    """合并一只股票的完整数据"""
    stock = {}

    # 优先用IA池数据（v7更完整）
    if code in ia_map:
        stock = dict(ia_map[code])
        stock['_source'] = 'IA'
    elif code in ib_map:
        stock = dict(ib_map[code])
        stock['_source'] = 'IB'

    if not stock:
        return None

    # 补充主题
    if code in theme_map:
        stock['theme'] = theme_map[code]

    # 新闻信号
    stock['news'] = NEWS_SIGNALS.get(code, {})

    return stock

def run():
    print('=' * 70)
    print('个股量化基本面选股模型 v1.1  |  5因子体系  |  权重 G:35% Q:20% V:15% M:15% E:15%')
    print('=' * 70)

    ia_map, ib_map, theme_map = load_data()

    # 合并所有股票
    all_codes = sorted(set(list(ia_map.keys()) + list(ib_map.keys())))
    print(f'\n股票总数：{len(all_codes)}只\n')

    # 全局统计量
    all_rev_yoys  = [sf(s.get('q1_26_yoy', s.get('q1_rev_yoy', 0))) for s in list(ia_map.values()) + list(ib_map.values())]
    all_ni_yoys   = [sf(s.get('q1_ni_yoy', 0)) for s in list(ia_map.values()) + list(ib_map.values())]
    all_scores    = [sf(s.get('score', s.get('total_score', 0))) for s in list(ia_map.values()) + list(ib_map.values())]

    scored = []
    for code in all_codes:
        stock = merge(code, ia_map, ib_map, theme_map)
        if not stock:
            continue

        g = growth_factor(stock, all_rev_yoys, all_ni_yoys, all_scores)
        q = quality_factor(stock)
        v = value_factor(stock)
        m = momentum_factor(stock)
        e = expectation_factor(stock)

        final = g*WEIGHTS['growth'] + q*WEIGHTS['quality'] + v*WEIGHTS['value'] + m*WEIGHTS['momentum'] + e*WEIGHTS['expectation']

        scored.append({
            'code':           code,
            'name':           stock.get('name', stock.get('ts_name', '')),
            'theme':          stock.get('theme', ''),
            'pool':           stock.get('pool', stock.get('_source', '')),
            'source':         stock.get('_source', ''),
            'pe':             round(sf(stock.get('pe', 0)), 1),
            'market_cap_yi':  round(sf(stock.get('market_cap_yi', 0)), 1),
            'q1_rev_yi':      round(sf(stock.get('q1_rev_yi', 0)), 1),
            'q1_ni_yi':       round(sf(stock.get('q1_ni_yi', 0)), 1),
            'q1_rev_yoy':     round(sf(stock.get('q1_26_yoy', stock.get('q1_rev_yoy', 0))), 1),
            'q1_ni_yoy':      round(sf(stock.get('q1_ni_yoy', 0)), 1),
            'h1_accel':       round(sf(stock.get('h1_accel', stock.get('acceleration_gap', 0))), 1),
            'v7_score':       round(sf(stock.get('score', stock.get('total_score', 0))), 1),
            'growth_score':   round(g, 1),
            'quality_score':  round(q, 1),
            'value_score':    round(v, 1),
            'momentum_score': round(m, 1),
            'expect_score':   round(e, 1),
            'final_score':    round(final, 1),
            'news':           stock.get('news', {}),
        })

    scored.sort(key=lambda x: x['final_score'], reverse=True)

    # 标记等级
    for i, s in enumerate(scored):
        s['rank'] = '🥇' if i < 5 else ('🥈' if i < 15 else ('🥉' if i < 30 else ''))
        # 有新闻信号的标📰
        s['has_news'] = '📰' if s['news'].get('hot') else ('📝' if s['news'] else '')

    # 打印TOP40
    print(f'{'排名':^4} {'代码':<12} {'名称':<10} {'综合':>6} {'成长':>6} {'质量':>6} {'价值':>6} {'动量':>6} {'预期':>6} {'信号'}')
    print('-' * 85)
    for i, s in enumerate(scored[:40]):
        print(f'{i+1:^4} {s["code"]:<12} {s["name"]:<10} '
              f'{s["final_score"]:>6.1f} {s["growth_score"]:>6.1f} {s["quality_score"]:>6.1f} '
              f'{s["value_score"]:>6.1f} {s["momentum_score"]:>6.1f} {s["expect_score"]:>6.1f} '
              f'{s["has_news"]:>4}')

    # IA池单独排名
    ia_scored = [s for s in scored if s['source'] == 'IA']
    ib_scored = [s for s in scored if s['source'] == 'IB']
    print(f'\n📊 IA池({len(ia_scored)}只) TOP5：')
    for i, s in enumerate(ia_scored[:5]):
        news_note = s['news'].get('note', '')[:50] if s['news'] else ''
        print(f'  {i+1}. {s["name"]}({s["code"]}) {s["final_score"]:.1f}分 {news_note}')

    print(f'\n📊 IB池({len(ib_scored)}只) TOP5：')
    for i, s in enumerate(ib_scored[:5]):
        news_note = s['news'].get('note', '')[:50] if s['news'] else ''
        print(f'  {i+1}. {s["name"]}({s["code"]}) {s["final_score"]:.1f}分 {news_note}')

    # 保存
    out_file = os.path.join(OUTPUT_DIR, 'stock_quant_model_v1.1_20260619.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': '2026-06-19', 'version': 'v1.1',
            'weights': WEIGHTS,
            'total': len(scored),
            'ia_top5': ia_scored[:5],
            'ib_top5': ib_scored[:5],
            'results': scored,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')

    # TOP15 详细
    print('\n' + '=' * 85)
    print('TOP15 详细分析')
    print('=' * 85)
    for i, s in enumerate(scored[:15]):
        note = s['news'].get('note', '') if s['news'] else ''
        print(f'\n{i+1}. {s["name"]}（{s["code"]}）{s["rank"]}')
        print(f'   主题：{s["theme"]} | 池：{s["pool"]} | PE：{s["pe"]}x | 市值：{s["market_cap_yi"]}亿')
        print(f'   综合：{s["final_score"]:.1f} = 成长{s["growth_score"]:.1f} + 质量{s["quality_score"]:.1f} + 价值{s["value_score"]:.1f} + 动量{s["momentum_score"]:.1f} + 预期{s["expect_score"]:.1f}')
        print(f'   Q1营收：{s["q1_rev_yi"]}亿 | Q1增速：{s["q1_rev_yoy"]}% | H1加速：{s["h1_accel"]}pp')
        if note:
            print(f'   📰 {note}')

    return scored

if __name__ == '__main__':
    results = run()
