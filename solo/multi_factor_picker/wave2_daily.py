# -*- coding: utf-8 -*-
r"""
二波行情每日盘中/盘后扫描器
识别：一波拉升后的调整/横盘形态 → 给出最优入场条件信号

运行方式：
  日间盘中(9:35-14:55):  py D:\\mystock\\solo\\multi_factor_picker\\wave2_daily.py
  盘后(16:00+):           py D:\mystock\solo\multi_factor_picker\wave2_daily.py --mode close
  定时任务: 独立bat调用

数据：Tushare (stk_factor + daily_basic + moneyflow)
建议扫描范围：用户自选股池 / 近期强势板块龙头 / 全市场（约3000只，耗时~15分钟）
"""
import os, sys, time, json, datetime, io
sys.path.insert(0, r'D:\mystock')
# 安全设置stdout编码（避免Windows终端输出乱码或吞输出）
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass
else:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except:
        pass

os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import pandas as pd
import numpy as np
import tushare as ts
from collections import defaultdict

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

# 缓存目录（与 tushare_quant.py 共用）
CACHE_DIR = r'D:\mystock\cache_daily'
os.makedirs(CACHE_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════
# 缓存API调用（与 tushare_quant.py 共用缓存）
# ═══════════════════════════════════════════════════════
def _read_cache(cache_file):
    """读取CSV缓存"""
    try:
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file)
            if not df.empty and 'trade_date' in df.columns:
                df['trade_date'] = df['trade_date'].astype(str)
                return df
    except:
        pass
    return None

def _save_cache(df, cache_file):
    """保存CSV缓存"""
    try:
        if df is not None and not df.empty:
            df.to_csv(cache_file, index=False)
    except:
        pass

def cached_daily(ts_code, start_date, end_date):
    """缓存版 pro.daily()，与 tushare_quant.py 共用 {ts_code}.csv"""
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    
    # 读缓存
    df_cache = _read_cache(cache_file)
    if df_cache is not None:
        # 检查缓存是否覆盖所需日期范围
        cached_dates = set(df_cache['trade_date'].values)
        has_start = start_date in cached_dates
        has_end = end_date in cached_dates
        if has_start and has_end:
            mask = (df_cache['trade_date'] >= start_date) & (df_cache['trade_date'] <= end_date)
            subset = df_cache[mask].copy()
            if len(subset) >= 40:
                return subset.sort_values('trade_date').reset_index(drop=True)
    
    # 缓存缺失，调API
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    time.sleep(0.06)
    
    if df is None or df.empty:
        return None
    
    df['trade_date'] = df['trade_date'].astype(str)
    
    # 合并到缓存文件（保留历史数据）
    if df_cache is not None:
        combined = pd.concat([df_cache, df]).drop_duplicates(subset='trade_date').sort_values('trade_date')
        _save_cache(combined, cache_file)
    else:
        _save_cache(df, cache_file)
    
    return df.sort_values('trade_date').reset_index(drop=True)

def cached_stk_factor_pro(ts_code, start_date, end_date):
    """缓存版 pro.stk_factor_pro()，直接含MA/RSI/MACD等计算值"""
    cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}.csv")
    
    df_cache = _read_cache(cache_file)
    if df_cache is not None:
        cached_dates = set(df_cache['trade_date'].values)
        has_start = start_date in cached_dates
        has_end = end_date in cached_dates
        if has_start and has_end:
            mask = (df_cache['trade_date'] >= start_date) & (df_cache['trade_date'] <= end_date)
            subset = df_cache[mask].copy()
            if not subset.empty:
                return subset.sort_values('trade_date').reset_index(drop=True)
    
    df = pro.stk_factor_pro(ts_code=ts_code, start_date=start_date, end_date=end_date)
    time.sleep(0.06)
    
    if df is not None and not df.empty:
        df['trade_date'] = df['trade_date'].astype(str)
        if df_cache is not None:
            # keep='last' 确保相同日期使用新数据（复权因子更新时）
            combined = pd.concat([df_cache, df]).drop_duplicates(subset='trade_date', keep='last').sort_values('trade_date')
            _save_cache(combined, cache_file)
        else:
            _save_cache(df, cache_file)
        return df.sort_values('trade_date').reset_index(drop=True)
    return df

def cached_daily_basic(ts_code, start_date, end_date):
    """缓存版 pro.daily_basic()"""
    cache_file = os.path.join(CACHE_DIR, f"daily_basic_{ts_code}.csv")
    
    df_cache = _read_cache(cache_file)
    if df_cache is not None:
        cached_dates = set(df_cache['trade_date'].values)
        has_start = start_date in cached_dates
        has_end = end_date in cached_dates
        if has_start and has_end:
            mask = (df_cache['trade_date'] >= start_date) & (df_cache['trade_date'] <= end_date)
            subset = df_cache[mask].copy()
            if not subset.empty:
                return subset.sort_values('trade_date').reset_index(drop=True)
    
    df = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date,
                          fields='ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb')
    time.sleep(0.06)
    
    if df is not None and not df.empty:
        df['trade_date'] = df['trade_date'].astype(str)
        if df_cache is not None:
            combined = pd.concat([df_cache, df]).drop_duplicates(subset='trade_date').sort_values('trade_date')
            _save_cache(combined, cache_file)
        else:
            _save_cache(df, cache_file)
        return df.sort_values('trade_date').reset_index(drop=True)
    return df

# ═══════════════════════════════════════════════════════
# 参数配置
# ═══════════════════════════════════════════════════════
SURGE_DAYS = 20        # 一波拉升窗口
SURGE_MIN = 0.20       # 一波最低涨幅 20%
PULLBACK_MIN = 0.05   # 最小回调（排除无回调）
ADJUST_MAX = 60       # 调整期最长60天

# 最优入场条件（100%成功率组合，来自wave2回测）
# 格式：(形态, 条件名, 条件函数, 优先级, 止损%, 目标%上方)
WINNING_COMBOS = [
    # ── 放量回调 ──
    {'pattern': '放量回调', 'name': 'MACD金叉+MA20上方', 'priority': 1,
     'condition': lambda s: s.get('macd_crossed', False) and s.get('above_ma20', False),
     'stop_pct': 0.05, 'target_pct': 0.20, 'min_score': 80},
    {'pattern': '放量回调', 'name': 'RSI<40+MA20上方', 'priority': 2,
     'condition': lambda s: s.get('rsi_now', 100) < 40 and s.get('above_ma20', False),
     'stop_pct': 0.05, 'target_pct': 0.18, 'min_score': 80},
    # ── V型急跌 ──
    {'pattern': 'V型急跌', 'name': 'RSI<35+MA60上方', 'priority': 1,
     'condition': lambda s: s.get('rsi_now', 100) < 35 and s.get('above_ma60', False),
     'stop_pct': 0.03, 'target_pct': 0.25, 'min_score': 80},
    {'pattern': 'V型急跌', 'name': 'RSI<40+MACD金叉', 'priority': 2,
     'condition': lambda s: s.get('rsi_now', 100) < 40 and s.get('macd_crossed', False),
     'stop_pct': 0.04, 'target_pct': 0.22, 'min_score': 80},
    # ── 深度回调 ──
    {'pattern': '深度回调', 'name': 'RSI<50+MA60上方', 'priority': 1,
     'condition': lambda s: s.get('rsi_now', 100) < 50 and s.get('above_ma60', False),
     'stop_pct': 0.06, 'target_pct': 0.25, 'min_score': 80},
    {'pattern': '深度回调', 'name': 'MACD金叉+MA20上方', 'priority': 2,
     'condition': lambda s: s.get('macd_crossed', False) and s.get('above_ma20', False),
     'stop_pct': 0.05, 'target_pct': 0.20, 'min_score': 80},
    # ── 强势横盘 ──
    {'pattern': '强势横盘', 'name': 'RSI<50+缩量(<0.8x)', 'priority': 1,
     'condition': lambda s: s.get('rsi_now', 100) < 50 and s.get('vol_ratio', 99) < 0.8,
     'stop_pct': 0.03, 'target_pct': 0.30, 'min_score': 85},
    {'pattern': '强势横盘', 'name': 'MACD金叉+MA20上方', 'priority': 2,
     'condition': lambda s: s.get('macd_crossed', False) and s.get('above_ma20', False),
     'stop_pct': 0.04, 'target_pct': 0.28, 'min_score': 85},
    # ── 缩量回调 ──
    {'pattern': '缩量回调', 'name': 'RSI<50+MA60上方', 'priority': 1,
     'condition': lambda s: s.get('rsi_now', 100) < 50 and s.get('above_ma60', False),
     'stop_pct': 0.06, 'target_pct': 0.20, 'min_score': 80},
    # ── 三角收敛 ──
    {'pattern': '三角收敛', 'name': 'RSI<50+MA60上方', 'priority': 1,
     'condition': lambda s: s.get('rsi_now', 100) < 50 and s.get('above_ma60', False),
     'stop_pct': 0.05, 'target_pct': 0.18, 'min_score': 80},
]

# 扫描范围：None=全市场，list=指定股票池
DEFAULT_POOL = None  # 或 ['000001.SZ', '600519.SH', ...]

# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════
def get_today_str():
    today = datetime.date.today()
    return today.strftime('%Y%m%d')

def get_latest_trade_date():
    """获取最近交易日（向前推，最多5天）"""
    for days_back in range(5):
        d = datetime.date.today() - datetime.timedelta(days=days_back)
        ds = d.strftime('%Y%m%d')
        try:
            df = pro.trade_cal(exchange='SSE', start_date=ds, end_date=ds)
            if df is not None and len(df) > 0 and df.iloc[0]['is_open'] == 1:
                return str(df.iloc[0]['cal_date'])
        except:
            pass
    return (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y%m%d')

def classify_pattern(df, surge_end_idx, recent_end_idx):
    """
    根据wave1高点之后的调整形态分类
    df: 日线DataFrame
    surge_end_idx: wave1高点位置（index）
    recent_end_idx: 最近分析日位置（index）
    返回: (pattern_name, pattern_data_dict)
    """
    n = len(df)
    if surge_end_idx >= recent_end_idx:
        return None, {}

    # 从wave1高点到现在的数据
    post = df.iloc[surge_end_idx:recent_end_idx+1].copy()
    if len(post) < 3:
        return '其他', {}

    closes = post['close'].values
    highs = post['high'].values
    lows = post['low'].values
    vols = post['vol'].values

    wave1_high = closes[0]  # 高点价格
    wave1_low_in_surge = closes[0]
    pullback_max = (wave1_high - closes.min()) / wave1_high  # 最大回调幅度
    pullback_days = len(post) - 1

    # 基准：wave1前20日均量
    if surge_end_idx >= 20:
        base_vol = df.iloc[surge_end_idx-20:surge_end_idx]['vol'].mean()
    else:
        base_vol = vols.mean()
    vol_ratio = vols.mean() / base_vol if base_vol > 0 else 1.0

    # MA位置
    ma5 = df.iloc[recent_end_idx:recent_end_idx+1]['ma5'].values[0]
    ma10 = df.iloc[recent_end_idx:recent_end_idx+1]['ma10'].values[0]
    ma20 = df.iloc[recent_end_idx:recent_end_idx+1]['ma20'].values[0]
    ma60 = df.iloc[recent_end_idx:recent_end_idx+1]['ma60'].values[0]
    current_price = closes[-1]

    above_ma5 = current_price > ma5 if not np.isnan(ma5) else False
    above_ma10 = current_price > ma10 if not np.isnan(ma10) else False
    above_ma20 = current_price > ma20 if not np.isnan(ma20) else False
    above_ma60 = current_price > ma60 if not np.isnan(ma60) else False

    # V型：10天内急跌>10%
    v_crash = False
    if len(post) <= 10 and pullback_max > 0.10:
        v_crash = True

    # 三角收敛：振幅逐周递减
    triangle = False
    if len(post) >= 10:
        weekly_ranges = []
        for w in range(0, len(post)-2, 5):
            chunk = post.iloc[w:min(w+5, len(post))]
            if len(chunk) >= 3:
                weekly_ranges.append(chunk['high'].max() - chunk['low'].min())
        if len(weekly_ranges) >= 2:
            if weekly_ranges[-1] < weekly_ranges[-2] * 0.85:
                triangle = True

    # MACD金叉：当前DIF>DEA
    macd_dif = df.iloc[recent_end_idx:recent_end_idx+1]['macd_dif'].values[0]
    macd_dea = df.iloc[recent_end_idx:recent_end_idx+1]['macd_dea'].values[0]
    macd_crossed = (macd_dif > macd_dea) if (not np.isnan(macd_dif) and not np.isnan(macd_dea)) else False

    # RSI
    rsi_now = df.iloc[recent_end_idx:recent_end_idx+1]['rsi_6'].values[0]
    rsi_now = rsi_now if not np.isnan(rsi_now) else 50.0

    # 量能比（近5日均量/基准量）
    vol_ratio_5d = vols[-5:].mean() / base_vol if base_vol > 0 else 1.0

    # 分类逻辑
    if v_crash and pullback_days <= 10:
        pattern = 'V型急跌'
    elif pullback_max < 0.10 and pullback_days <= 15:
        pattern = '强势横盘'
    elif pullback_max >= 0.10 and pullback_max < 0.20 and vol_ratio > 0.80:
        pattern = '放量回调'
    elif pullback_max >= 0.10 and pullback_max < 0.20 and vol_ratio <= 0.80:
        pattern = '缩量回调'
    elif pullback_max >= 0.20 and triangle:
        pattern = '三角收敛'
    elif pullback_max >= 0.20:
        pattern = '深度回调'
    elif triangle:
        pattern = '三角收敛'
    else:
        pattern = '其他'

    return pattern, {
        'pullback_max': pullback_max,
        'pullback_days': pullback_days,
        'vol_ratio': vol_ratio_5d,
        'above_ma5': above_ma5,
        'above_ma10': above_ma10,
        'above_ma20': above_ma20,
        'above_ma60': above_ma60,
        'macd_crossed': macd_crossed,
        'macd_dif': macd_dif,
        'macd_dea': macd_dea,
        'rsi_now': rsi_now,
        'wave1_high': wave1_high,
        'current_price': current_price,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
    }

# ═══════════════════════════════════════════════════════
# 扫描单只股票
# ═══════════════════════════════════════════════════════
def scan_stock(ts_code, lookback=90):
    """
    扫描单只股票是否有二波行情机会
    lookback: 回看天数
    返回: dict 或 None
    """
    end_date = get_latest_trade_date()
    start_date = (datetime.date.today() - datetime.timedelta(days=lookback+30)).strftime('%Y%m%d')

    try:
        # 日线（缓存版，与 tushare_quant.py 共用）
        daily = cached_daily(ts_code, start_date, end_date)
        if daily is None or len(daily) < 40:
            return None

        # 技术因子（缓存版，使用 stk_factor_pro，MA/RSI 等已计算好）
        factor = cached_stk_factor_pro(ts_code, start_date, end_date)

        # 基本面（缓存版）
        basic = cached_daily_basic(ts_code, start_date, end_date)

        # 合并（stk_factor_pro 字段：使用 _qfq 前复权版本，避免除权日指标失真）
        df = daily.copy()
        if factor is not None and len(factor) > 0:
            factor_rename = {
                'ma_qfq_5': 'ma5', 'ma_qfq_10': 'ma10', 'ma_qfq_20': 'ma20', 'ma_qfq_60': 'ma60',
                'macd_qfq': 'macd', 'macd_dif_qfq': 'macd_dif', 'macd_dea_qfq': 'macd_dea',
                'rsi_qfq_6': 'rsi_6', 'rsi_qfq_12': 'rsi_12', 'rsi_qfq_24': 'rsi_24',
                'kdj_k_qfq': 'kdj_k', 'kdj_d_qfq': 'kdj_d', 'kdj_qfq': 'kdj_j',
                'boll_upper_qfq': 'boll_upper', 'boll_mid_qfq': 'boll_mid', 'boll_lower_qfq': 'boll_lower',
                'cci_qfq': 'cci',
            }
            # 只取factor表中实际存在的列，避免缺失列名导致KeyError
            valid_cols = ['trade_date'] + [k for k in factor_rename if k in factor.columns]
            valid_rename = {k: v for k, v in factor_rename.items() if k in factor.columns}
            factor_subset = factor[valid_cols].rename(columns=valid_rename)
            df = df.merge(factor_subset, on='trade_date', how='left')
        if basic is not None and len(basic) > 0:
            df = df.merge(basic[['trade_date','turnover_rate','volume_ratio','pe_ttm','pb']],
                          on='trade_date', how='left')

        df = df[df['vol'] > 0].reset_index(drop=True)
        if len(df) < 40:
            return None

        # ⚠️ 关键修复：用 close_qfq（前复权）替代 close（未复权）
        # 未复权价在除权日产生虚假跳空，导致涨幅/回调幅度/RSI全部失真
        if 'close_qfq' in df.columns:
            df['close_bfq'] = df['close']
            df['close'] = df['close_qfq']
        if 'high_qfq' in df.columns:
            df['high'] = df['high_qfq']
        if 'low_qfq' in df.columns:
            df['low'] = df['low_qfq']

        # 已从 stk_factor_pro 获取MA值，无需手动 rolling 计算

        # === 找最近一波20%+拉升 ===
        # 从倒数第ADJUST_MAX天开始向前找
        for end_idx in range(len(df) - 1, ADJUST_MAX, -1):
            window_start = end_idx - SURGE_DAYS
            if window_start < 0:
                break

            window_closes = df.iloc[window_start:end_idx+1]['close'].values
            low_idx_in_window = np.argmin(window_closes)
            high_idx_in_window = np.argmax(window_closes)

            # 低点必须在高点之前
            if high_idx_in_window <= low_idx_in_window:
                continue
            if (high_idx_in_window - low_idx_in_window) > SURGE_DAYS - 2:
                continue

            wave1_gain = (window_closes[high_idx_in_window] - window_closes[low_idx_in_window]) / window_closes[low_idx_in_window]
            if wave1_gain < SURGE_MIN:
                continue

            # wave1高点在df中的绝对索引
            wave1_high_idx = window_start + high_idx_in_window
            wave1_low_idx = window_start + low_idx_in_window

            # wave1高点之后至今
            post_df = df.iloc[wave1_high_idx:]
            if len(post_df) < 2:
                continue

            # 形态分类
            pattern, pdata = classify_pattern(df, wave1_high_idx, len(df)-1)
            if pattern is None or pattern == '其他':
                continue

            # 根据板块类型过滤形态
            # 双创板（创业板/科创板）：只保留 V型急跌、深度回调、放量回调
            # 主板：只保留强势横盘、V型急跌、放量回调
            is_cyb_kcb = ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689')
            if is_cyb_kcb:
                valid_patterns = ['V型急跌', '深度回调', '放量回调']
            else:
                valid_patterns = ['强势横盘', 'V型急跌', '放量回调']
            if pattern not in valid_patterns:
                continue

            # 计算从wave1高点到现在的回调
            current_price = df.iloc[-1]['close']
            wave1_high_price = df.iloc[wave1_high_idx]['close']
            pullback = (wave1_high_price - current_price) / wave1_high_price

            # 忽略回调<5%（无意义调整）
            if pullback < PULLBACK_MIN:
                continue

            # 基本评分（基于历史回测数据）
            success_rate = {
                '强势横盘': 0.986, 'V型急跌': 0.949, '放量回调': 0.904,
                '深度回调': 0.862, '其他': 0.856, '缩量回调': 0.776, '三角收敛': 0.758
            }.get(pattern, 0.5)

            base_score = int(success_rate * 100)

            # 应用最优组合过滤
            for combo in sorted(WINNING_COMBOS, key=lambda x: x['priority']):
                if combo['pattern'] == pattern and combo['condition'](pdata):
                    entry_price = current_price
                    stop_price = round(entry_price * (1 - combo['stop_pct']), 2)
                    target_price = round(entry_price * (1 + combo['target_pct']), 2)
                    risk_pct = combo['stop_pct'] * 100
                    reward_pct = combo['target_pct'] * 100

                    return {
                        'ts_code': ts_code,
                        'name': ts_code,
                        'pattern': pattern,
                        'combo': combo['name'],
                        'wave1_gain': round(wave1_gain * 100, 1),
                        'pullback': round(pullback * 100, 1),
                        'pullback_days': pdata.get('pullback_days', 0),
                        'rsi_now': round(pdata['rsi_now'], 1),
                        'vol_ratio': round(pdata['vol_ratio'], 2),
                        'above_ma20': pdata['above_ma20'],
                        'above_ma60': pdata['above_ma60'],
                        'macd_crossed': pdata['macd_crossed'],
                        'current_price': round(current_price, 2),
                        'wave1_high': round(wave1_high_price, 2),
                        'entry_price': entry_price,
                        'stop_price': stop_price,
                        'target_price': target_price,
                        'risk_pct': risk_pct,
                        'reward_pct': reward_pct,
                        'rr_ratio': round(reward_pct / risk_pct, 1),
                        'base_score': base_score,
                    }

        return None

    except Exception as e:
        return None

# ═══════════════════════════════════════════════════════
# 获取股票池
# ═══════════════════════════════════════════════════════
def format_stock_code(code):
    """将股票代码格式化为标准格式（如 2602 -> 002602.SZ）"""
    code = str(code).strip()
    
    # 如果已经是完整格式（包含.SH或.SZ），直接返回
    if code.endswith('.SH') or code.endswith('.SZ'):
        return code
    
    # 补前导零到6位
    code = code.zfill(6)
    
    # 根据前缀判断交易所
    if code.startswith('60') or code.startswith('688'):
        return code + '.SH'
    elif code.startswith('00') or code.startswith('30') or code.startswith('20'):
        return code + '.SZ'
    else:
        return code + '.SZ'  # 默认深圳


def get_stock_pool():
    """获取扫描股票池"""
    if DEFAULT_POOL:
        return DEFAULT_POOL

    # 优先从 bull_stocks.csv 读取（用户指定）
    bull_stocks_file = os.path.join(OUT_DIR, 'bull_stocks.csv')
    if os.path.exists(bull_stocks_file):
        try:
            df = pd.read_csv(bull_stocks_file, encoding='utf-8-sig')
            if 'ts_code' in df.columns:
                stocks = df['ts_code'].dropna().unique().tolist()
                # 格式化为标准股票代码格式
                stocks = [format_stock_code(s) for s in stocks if s]
                print(f'  从 bull_stocks.csv 读取 {len(stocks)} 只股票')
                return stocks
        except Exception as e:
            print(f'  bull_stocks.csv 读取失败: {e}')

    # 从主题股票映射文件读取（优先）
    theme_file = r'D:\mystock\cache_daily\theme_stock_map_latest.json'
    if os.path.exists(theme_file):
        try:
            with open(theme_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'themes' in data:
                stocks = set()
                for theme_name, stock_list in data['themes'].items():
                    for stock in stock_list:
                        code = stock.get('code', '')
                        if code:
                            stocks.add(code)
                stocks = list(stocks)[:100]  # 取前100只
                print(f'  从主题映射读取 {len(stocks)} 只股票')
                return stocks
        except Exception as e:
            print(f'  主题映射读取失败: {e}')

    # 从本地缓存读取近期强势股
    cache_dir = r'D:\mystock\cache_daily'
    for fname in os.listdir(cache_dir) if os.path.exists(cache_dir) else []:
        if 'sector' in fname.lower() or 'block' in fname.lower():
            fpath = os.path.join(cache_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 10:
                    stocks = data[:100] if isinstance(data[0], str) else [d.get('code', d.get('ts_code', '')) for d in data[:100]]
                    stocks = [s for s in stocks if s]
                    if stocks:
                        print(f'  从缓存读取 {len(stocks)} 只: {fname}')
                        return stocks
            except:
                pass

    # 备用：用 stock_basic 获取全市场主板（限制前200只用于快速测试）
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
        # 优先上海主板+深圳主板+创业板
        mask = sb['ts_code'].str.startswith(('600', '601', '603', '000', '002', '300'))
        stocks = sb[mask]['ts_code'].tolist()[:200]
        print(f'  stock_basic 池: {len(stocks)} 只（取前200）')
        return stocks
    except Exception as e:
        print(f'  stock_basic 获取失败: {e}')
        return []

# ═══════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════
def generate_pdf_report(results, total_scanned):
    """生成PDF分析报告"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # 注册中文字体
    font_paths = [
        r'C:\Windows\Fonts\simhei.ttf',
        r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\msyhbd.ttc',
    ]
    font_name = 'Helvetica'
    for fp in font_paths:
        try:
            pdfmetrics.registerFont(TTFont('CNFont', fp))
            font_name = 'CNFont'
            break
        except:
            continue

    trade_date = get_latest_trade_date()
    pdf_path = os.path.join(OUT_DIR, f'wave2_daily_{trade_date}.pdf')

    doc = SimpleDocTemplate(
        pdf_path, pagesize=landscape(A4),
        topMargin=15*mm, bottomMargin=15*mm,
        leftMargin=10*mm, rightMargin=10*mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TCN', parent=styles['Title'],
        fontName=font_name, fontSize=18, alignment=1, spaceAfter=6*mm)
    sub_style = ParagraphStyle('SCN', parent=styles['Normal'],
        fontName=font_name, fontSize=10, alignment=1, spaceAfter=4*mm)
    hdr_style = ParagraphStyle('HCN', parent=styles['Normal'],
        fontName=font_name, fontSize=8, alignment=1)
    cel_style = ParagraphStyle('CCN', parent=styles['Normal'],
        fontName=font_name, fontSize=7.5, alignment=1)

    elements = []
    elements.append(Paragraph("二波行情扫描报告", title_style))
    elements.append(Paragraph(
        f"扫描日期: {trade_date}  |  扫描: {total_scanned}只  |  信号: {len(results)}个",
        sub_style))
    elements.append(Spacer(1, 3*mm))

    if not results:
        elements.append(Paragraph("今日无二波信号", cel_style))
        doc.build(elements)
        print(f"  PDF报告已生成: {pdf_path}")
        return pdf_path

    # 形态分布摘要
    pc = {}
    for r in results:
        p = r['pattern']
        pc[p] = pc.get(p, 0) + 1
    summary = "形态分布: " + " | ".join(f"{p}: {c}只" for p, c in sorted(pc.items(), key=lambda x: -x[1]))
    elements.append(Paragraph(summary, sub_style))
    elements.append(Spacer(1, 3*mm))

    # 表格
    headers = ['股票代码', '形态', '入场条件', '一波涨幅%', '回调%',
               'RSI', '现价', '入场价', '止损价', '目标价', '信号分', '盈亏比']
    col_widths = [32*mm, 22*mm, 42*mm, 18*mm, 14*mm,
                  12*mm, 18*mm, 18*mm, 18*mm, 18*mm, 14*mm, 14*mm]

    data_rows = [[Paragraph(h, hdr_style) for h in headers]]
    for r in results:
        data_rows.append([
            Paragraph(r['ts_code'], hdr_style),
            Paragraph(r['pattern'], cel_style),
            Paragraph(r['combo'], cel_style),
            Paragraph(f"{r['wave1_gain']:.1f}", cel_style),
            Paragraph(f"{r['pullback']:.1f}", cel_style),
            Paragraph(f"{r['rsi_now']:.1f}", cel_style),
            Paragraph(f"{r['current_price']:.2f}", cel_style),
            Paragraph(f"{r['entry_price']:.2f}", cel_style),
            Paragraph(f"{r['stop_price']:.2f}", cel_style),
            Paragraph(f"{r['target_price']:.2f}", cel_style),
            Paragraph(f"{r['base_score']}", cel_style),
            Paragraph(f"{r['rr_ratio']:.1f}", cel_style),
        ])

    t = Table(data_rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    # 高亮TOP3
    for i in range(min(3, len(results))):
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d4efdf')),
        ]))
    elements.append(t)
    elements.append(Spacer(1, 4*mm))

    note_style = ParagraphStyle('NCN', parent=styles['Normal'],
        fontName=font_name, fontSize=8, textColor=colors.HexColor('#888888'))
    elements.append(Paragraph("* 绿色高亮 = 信号分TOP3", note_style))
    elements.append(Paragraph(f"* 生成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", note_style))

    doc.build(elements)
    print(f"  PDF报告已生成: {pdf_path}")
    return pdf_path


def main():
    today = datetime.date.today()
    print("=" * 70)
    print(f"二波行情每日扫描  {today.strftime('%Y-%m-%d')}  周{['一','二','三','四','五','六','日'][today.weekday()]}")
    print("=" * 70)

    stocks = get_stock_pool()
    print(f"\n扫描股票池: {len(stocks)} 只\n")

    results = []
    total = len(stocks)
    for i, code in enumerate(stocks):
        if (i+1) % 20 == 0 or i == 0:
            print(f"  进度: {i+1}/{total} ({code})...")

        result = scan_stock(code)
        if result:
            results.append(result)
            print(f"  ★ [{result['pattern']}] {code} 回调{result['pullback']}% | RSI={result['rsi_now']} | {result['combo']}")

        time.sleep(0.12)  # 限速

    print(f"\n{'='*70}")
    print(f"扫描完成: {len(stocks)} 只中发现 {len(results)} 个二波信号")

    if not results:
        print("  今日无二波信号（可能市场整体偏弱或无合适形态）")
        generate_pdf_report(results, total)
        return results

    # 按 base_score 排序
    results.sort(key=lambda x: (x['base_score'], x['rr_ratio']), reverse=True)

    # 显示结果
    print(f"\n{'码':<14} {'形态':<8} {'条件':<20} {'回调%':>6} {'RSI':>5} {'信号分':>5} {'建议价':>8} {'止损':>8} {'目标':>8} {'盈亏比':>6}")
    print('-'*100)
    for r in results:
        print(f"{r['ts_code']:<14} {r['pattern']:<8} {r['combo']:<20} "
              f"{r['pullback']:>6.1f} {r['rsi_now']:>5.1f} {r['base_score']:>5} "
              f"{r['entry_price']:>8.2f} {r['stop_price']:>8.2f} {r['target_price']:>8.2f} {r['rr_ratio']:>6.1f}")

    # 保存CSV/JSON
    df_results = pd.DataFrame(results)
    csv_path = os.path.join(OUT_DIR, f'wave2_daily_{get_latest_trade_date()}.csv')
    df_results.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存: {csv_path}")

    json_path = os.path.join(OUT_DIR, f'wave2_daily_{get_latest_trade_date()}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'date': get_latest_trade_date(), 'results': results, 'total_scanned': total}, f, ensure_ascii=False, indent=2)

    # 生成PDF报告
    generate_pdf_report(results, total)

    return results

if __name__ == '__main__':
    results = main()
