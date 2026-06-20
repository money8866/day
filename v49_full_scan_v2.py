# -*- coding: utf-8 -*-
"""
V4.9 全市场上涨空间扫描（SQLite主题匹配版）
修复V4.8的问题：
  1. 模式B评分收紧（满分115→85），避免全体100分封顶
  2. 提高BUY阈值（75→85），过滤弱信号
  3. 突破确认加强（量比>1.3硬约束+0.5%误差）
  4. **新增：读取SQLite主题缓存进行完整匹配**
"""

import sys, os, glob, math, time, json
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import load_dotenv
import tushare as ts
import sqlite3

BASE_DIR   = r'D:\mystock'
CACHE_DIR  = os.path.join(BASE_DIR, 'cache_daily')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')
THEME_DIR  = os.path.join(BASE_DIR, 'report_daily')
THEME_CACHE_DB = os.path.join(BASE_DIR, 'solo', 'bak0615', 'cache_backbone_tushare', 'theme_portfolio.db')

# ========== Tushare初始化 ==========
load_dotenv(os.path.join(BASE_DIR, 'config', '.env'))
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ========== 股票名称+行业映射 ==========
def load_stock_info_map():
    """加载股票名称+行业映射"""
    try:
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
        info_map = {}
        for _, row in df.iterrows():
            info_map[row['ts_code']] = {
                'name': row['name'],
                'industry': row.get('industry', '')
            }
        print(f'  📋 股票信息映射：{len(info_map)}只')
        return info_map
    except Exception as e:
        print(f'  ⚠️ 加载股票信息失败：{e}')
        return {}

STOCK_INFO_MAP = load_stock_info_map()

# ========== 主题评分加载 ==========
def load_theme_scores():
    """从theme_evolution_*.json加载主题评分"""
    pattern = os.path.join(THEME_DIR, 'theme_evolution_*.json')
    files = glob.glob(pattern)
    if not files:
        print('  ⚠️ 未找到theme_evolution文件')
        return {}
    
    latest_file = max(files, key=os.path.getmtime)
    print(f'  📊 加载主题评分：{os.path.basename(latest_file)}')
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    theme_score_map = {}  # theme_name → score
    for theme in data.get('theme_table', []):
        theme_score_map[theme['theme']] = theme.get('score', 50)
    
    print(f'  ✅ 加载{len(theme_score_map)}个主题评分')
    return theme_score_map

THEME_SCORE_MAP = load_theme_scores()

# ========== SQLite主题缓存加载 ==========
def load_theme_portfolio_cache():
    """从SQLite缓存加载主题-股票映射和主题定义"""
    if not os.path.exists(THEME_CACHE_DB):
        print(f'  ⚠️ 未找到主题缓存：{THEME_CACHE_DB}')
        return {}, {}
    
    try:
        conn = sqlite3.connect(THEME_CACHE_DB)
        cursor = conn.cursor()
        
        # 加载themes表（主题定义）
        cursor.execute('SELECT theme_name, industry, keywords FROM themes')
        themes_def = {}
        for row in cursor.fetchall():
            theme_name, industry, keywords = row
            themes_def[theme_name] = {
                'industry': industry.split(',') if industry else [],
                'keywords': keywords.split(',') if keywords else []
            }
        
        # 加载portfolio表（股票-主题映射）
        cursor.execute('SELECT ts_code, name, theme_name, layer, purity FROM portfolio')
        stock_theme_map = {}  # ts_code → [(theme_name, layer, purity), ...]
        for row in cursor.fetchall():
            ts_code, stock_name, theme_name, layer, purity = row
            if ts_code not in stock_theme_map:
                stock_theme_map[ts_code] = []
            stock_theme_map[ts_code].append((theme_name, layer, purity))
        
        conn.close()
        print(f'  ✅ 加载主题缓存：{len(themes_def)}个主题，{len(stock_theme_map)}只股票')
        return themes_def, stock_theme_map
    except Exception as e:
        print(f'  ⚠️ 加载主题缓存失败：{e}')
        return {}, {}

THEMES_DEF, STOCK_THEME_CACHE = load_theme_portfolio_cache()

# ========== 主题强度映射表（用于估算未评分主题）==========
THEME_STRENGTH_MAP = {
    # S级主线（80-90分）
    '光通信': 82, 'AI服务器与算力基建': 85, 'AI应用': 80, 'AI文化娱乐': 78,
    '人形机器人': 82, '智能驾驶': 80, '半导体材料': 78, '半导体设备': 85,
    '先进封装': 82, 'AI终端': 80, 'AI能源链': 75,
    # A级强势（65-79分）
    'AI模型与AI Agent': 75, '数据中心瓶颈硬件链': 72, '核聚变': 70,
    '电子元件': 72, '化学制品': 65, '有色金属': 68,
    # B级中性（50-64分）
    '新材料': 60, '新能源': 65, '生物医药': 58,
    '食品饮料': 55, '房地产': 50, '银行': 52,
    '证券': 55, '保险': 52, '电力': 60,
}

def get_theme_strength_score(theme_name):
    """获取主题强度评分（优先用theme_evolution，其次用映射表，最后用默认）"""
    # 1. 优先用theme_evolution评分
    if theme_name in THEME_SCORE_MAP:
        return THEME_SCORE_MAP[theme_name]
    
    # 2. 用主题强度映射表
    if theme_name in THEME_STRENGTH_MAP:
        return THEME_STRENGTH_MAP[theme_name]
    
    # 3. 用关键词估算
    score = 50  # 默认B级
    high_keywords = ['AI', '算力', '智能', '机器人', '半导体', '光通信', '新能源', '电池']
    mid_keywords = ['电子', '通信', '数据', '软件', '医疗', '航空']
    
    for kw in high_keywords:
        if kw in theme_name:
            score = max(score, 75)
            break
    else:
        for kw in mid_keywords:
            if kw in theme_name:
                score = max(score, 62)
                break
    
    return score

def calc_theme_bonus(ts_code, theme_name, layer, purity):
    """根据主题强度和角色计算加成"""
    # 获取主题评分
    theme_score = get_theme_strength_score(theme_name)
    
    # 主题强度分级
    if theme_score >= 80:
        strength = 'S'
    elif theme_score >= 65:
        strength = 'A'
    elif theme_score >= 50:
        strength = 'B'
    else:
        strength = 'C'
    
    # 根据强度+角色计算加成
    if layer == 'core':
        if strength == 'S':
            bonus = 1.30  # S级主线核心股 +30%
        elif strength == 'A':
            bonus = 1.25  # A级核心股 +25%
        elif strength == 'B':
            bonus = 1.20  # B级核心股 +20%
        else:
            bonus = 1.15  # C级核心股 +15%
    else:  # related
        if strength == 'S':
            bonus = 1.20  # S级主线相关股 +20%
        elif strength == 'A':
            bonus = 1.15  # A级相关股 +15%
        elif strength == 'B':
            bonus = 1.10  # B级相关股 +10%
        else:
            bonus = 1.05  # C级相关股 +5%
    
    return strength, bonus, round(theme_score, 1)

def match_stock_theme(ts_code, stock_name, stock_industry):
    """从SQLite缓存匹配个股所属主题"""
    if not STOCK_THEME_CACHE or ts_code not in STOCK_THEME_CACHE:
        return None, 0.0, None
    
    # 选择加成最高的主题
    best_theme = None
    best_bonus = 1.0
    best_strength = None
    best_score = None
    
    for theme_name, layer, purity in STOCK_THEME_CACHE[ts_code]:
        strength, bonus, theme_score = calc_theme_bonus(ts_code, theme_name, layer, purity)
        
        if bonus > best_bonus:
            best_bonus = bonus
            best_theme = theme_name
            best_strength = strength
            best_score = theme_score
    
    return best_theme, best_bonus, best_strength, best_score

# ========== 技术指标计算 ==========
def calc_rsi(close_series, period=14):
    """计算RSI"""
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

def calc_macd(close_series):
    """计算MACD"""
    ema12 = close_series.ewm(span=12, adjust=False).mean()
    ema26 = close_series.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif.iloc[-1], dea.iloc[-1], macd.iloc[-1]

def scan_single_stock(csv_path):
    """扫描单只股票"""
    try:
        df = pd.read_csv(csv_path)
        if len(df) < 60:
            return None
        
        df = df.tail(100).reset_index(drop=True)
        close = df['close'].values
        vol = df['vol'].values if 'vol' in df.columns else df['volume'].values
        high = df['high'].values
        
        # 提取代码
        fname = os.path.basename(csv_path)
        ts_code = fname.replace('.csv', '')
        
        # 基本信息
        c = close[-1]
        c1 = close[-2]
        pct_chg = (c - c1) / c1 * 100 if c1 > 0 else 0
        
        # 均线
        ma5 = np.mean(close[-5:])
        ma10 = np.mean(close[-10:])
        ma20 = np.mean(close[-20:])
        ma60 = np.mean(close[-60:])
        
        # 量比
        vol_ma20 = np.mean(vol[-20:])
        vol_ratio = vol[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
        
        # RSI
        close_series = pd.Series(close)
        rsi14 = calc_rsi(close_series, 14)
        rsi2 = calc_rsi(close_series, 2)
        
        # MACD
        dif, dea, macd_val = calc_macd(close_series)
        
        # 20日新高
        high20d = np.max(high[-20:])
        breakout_pct = (c - high20d) / high20d * 100 if high20d > 0 else 0
        
        # ========== 模式A：回踩买点 ==========
        score_a = 0
        
        # 趋势（30分）
        if ma5 > ma10 > ma20 > ma60:
            trend_score_a = 30
        elif ma5 > ma10 > ma20:
            trend_score_a = 20
        elif ma5 > ma10:
            trend_score_a = 10
        else:
            trend_score_a = 0
        score_a += trend_score_a
        
        # 回踩（25分）
        ma5_dist = (c - ma5) / ma5 * 100
        if -8 <= ma5_dist <= -5:
            pullback_score = 25
        elif -15 <= ma5_dist < -8:
            pullback_score = 20  # 深调博弈
        elif -5 < ma5_dist <= -2:
            pullback_score = 15
        elif -2 < ma5_dist <= 0:
            pullback_score = 10
        else:
            pullback_score = 0
        score_a += pullback_score
        
        # 量能（20分）
        vol_ratio_score_a = min(20, max(0, 20 * (0.6 - vol_ratio) / 0.3)) if vol_ratio < 0.6 else 0
        score_a += vol_ratio_score_a
        
        # RSI（15分）
        if rsi2 < 5 and rsi14 < 35:
            rsi_score = 15
        elif rsi2 < 10:
            rsi_score = 10
        else:
            rsi_score = 5
        score_a += rsi_score
        
        # MACD（10分）
        if dif > dea and dif > 0:
            macd_score = 10
        elif dif > dea:
            macd_score = 7
        else:
            macd_score = 0
        score_a += macd_score
        
        # 回踩不破趋势奖励
        if pullback_score > 0 and ma5 > ma20:
            score_a += 5
        
        # ========== 模式B：突破跟踪 ==========
        score_b = 0
        
        # 突破确认（量比>1.3硬约束）
        if vol_ratio > 1.3 and breakout_pct >= -0.5:
            # 趋势（30分）
            if ma5 > ma10 > ma20 > ma60:
                trend_score_b = 30
            elif ma5 > ma10 > ma20:
                trend_score_b = 20
            else:
                trend_score_b = 10
            score_b += trend_score_b
            
            # 量能（30分）
            vol_ratio_score_b = min(30, max(0, 30 * (vol_ratio - 1.3) / 0.7))
            score_b += vol_ratio_score_b
            
            # 动量（25分）
            momentum_score = min(25, max(0, 25 * pct_chg / 5))
            score_b += momentum_score
        
        # ========== 选择最优模式 ==========
        if score_b > score_a and c >= high20d * 0.995:
            final_score = score_b
            mode = 'BREAKOUT'
        else:
            final_score = score_a
            mode = 'PULLBACK'
        
        # ========== 主题加成 ==========
        stock_info = STOCK_INFO_MAP.get(ts_code, {})
        stock_name = stock_info.get('name', ts_code)
        stock_industry = stock_info.get('industry', '')
        
        theme_name, theme_bonus, theme_strength, theme_score = match_stock_theme(ts_code, stock_name, stock_industry)
        strength_icon = {'S': '🔴', 'A': '🟠', 'B': '🟡', 'C': '🟢'}.get(theme_strength, '⚪')
        theme_str = f"{strength_icon}{theme_strength}:{theme_bonus:.2f}" if theme_strength else "1.00"
        theme_score_str = f"({theme_score:.0f}分)" if theme_score else ""
        
        # 应用加成（上限100分）
        final_score = min(100, final_score * theme_bonus)
        
        # ========== 信号判定 ==========
        if final_score >= 85:
            signal = 'BUY'
        elif final_score >= 55:
            signal = 'WATCH'
        else:
            signal = 'AVOID'
        
        # 预估涨幅
        upside_pct = max(0, (final_score - 55) * 0.6)
        
        return {
            'ts_code': ts_code,
            'name': stock_name,
            'close': c,
            'pct_chg': pct_chg,
            'mode': mode,
            'score': round(final_score, 1),
            'signal': signal,
            'vol_ratio': round(vol_ratio, 2),
            'breakout_pct': round(breakout_pct, 1),
            'theme': theme_name,
            'theme_strength': theme_strength,
            'theme_score': theme_score,
            'theme_bonus': round(theme_bonus, 2),
            'upside': round(upside_pct, 1)
        }
        
    except Exception as e:
        return None

# ========== 主扫描函数 ==========
def full_market_scan():
    """全市场扫描"""
    print('=' * 70)
    print('V4.9 全市场上涨空间扫描（SQLite主题匹配版）')
    print('=' * 70)
    
    # 扫描所有缓存文件
    csv_files = glob.glob(os.path.join(CACHE_DIR, '*.csv'))
    print(f'\n📂 缓存文件：{len(csv_files)}只\n')
    
    results = []
    start_time = time.time()
    
    # 并行扫描
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(scan_single_stock, f): f for f in csv_files}
        count = 0
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
            count += 1
            if count % 500 == 0:
                print(f'  已完成 {count}/{len(csv_files)} ({count*100//len(csv_files)}%)')
    
    elapsed = time.time() - start_time
    print(f'\n⏱️ 耗时：{elapsed:.1f}秒\n')
    
    # 排序
    results = sorted(results, key=lambda x: (x['signal'] == 'BUY', x['score']), reverse=True)
    
    # 统计
    buy_count = sum(1 for r in results if r['signal'] == 'BUY')
    watch_count = sum(1 for r in results if r['signal'] == 'WATCH')
    breakout_count = sum(1 for r in results if r['mode'] == 'BREAKOUT')
    pullback_count = sum(1 for r in results if r['mode'] == 'PULLBACK')
    
    print('📊 信号统计：')
    print(f'  BUY       = {buy_count}只')
    print(f'  WATCH     = {watch_count}只')
    print(f'  BREAKOUT  = {breakout_count}只')
    print(f'  PULLBACK  = {pullback_count}只')
    
    # 输出表格
    print('\n' + '=' * 120)
    print(f' {"排名":<4} {"代码":<12} {"名称":<10} {"收盘":<8} {"模式":<10} {"综合":<6} {"信号":<6} {"量比":<6} {"主题加成":<8} {"预估"}')
    print('-' * 120)
    
    for i, r in enumerate(results[:40], 1):
        signal_icon = '✅' if r['signal'] == 'BUY' else '🔍'
        mode_icon = '🚀突破' if r['mode'] == 'BREAKOUT' else '📉回踩'
        strength_icon = {'S': '🔴', 'A': '🟠', 'B': '🟡', 'C': '🟢'}.get(r.get('theme_strength'), '⚪')
        theme_score = r.get('theme_score', 0)
        theme_str = f"{strength_icon}{r['theme_strength']}:{r['theme_bonus']:.2f}({theme_score:.0f})" if r.get('theme_strength') else "1.00"
        print(f' {i:<4} {r["ts_code"]:<12} {r["name"]:<10} {r["close"]:<8.2f} {mode_icon:<10} {r["score"]:<6.1f} {signal_icon:<6} {r["vol_ratio"]:<6.2f} {theme_str:<8} {r["upside"]}%')
    
    # 保存JSON
    output_file = os.path.join(OUTPUT_DIR, f'v49_sqlite_scan_{datetime.now().strftime("%Y%m%d")}.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{output_file}')
    
    return results

if __name__ == '__main__':
    full_market_scan()
