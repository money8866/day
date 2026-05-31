# -*- coding: utf-8 -*-
"""数据引擎 - Tushare + 东方财富 + 通达信本地缓存"""
import os, json, pickle, struct, time, requests
import pandas as pd
import numpy as np
import tushare as ts
from datetime import datetime, timedelta

from config import (
    TUSHARE_TOKEN, CACHE_DIR, TDX_SH_LDAY, TDX_SZ_LDAY,
    EASTMONEY, STOCK_FILTER
)

_ts_api = None

def get_ts_api():
    global _ts_api
    if _ts_api is None:
        ts.set_token(TUSHARE_TOKEN)
        _ts_api = ts.pro_api()
    return _ts_api

# ============================================================
# 通用缓存
# ============================================================
def _cache_path(name):
    return os.path.join(CACHE_DIR, f"{name}.pkl")

def _cache_date_path(name):
    return os.path.join(CACHE_DIR, f"{name}_date.txt")

def _cache_is_fresh(name, max_hours=4):
    dp = _cache_date_path(name)
    if not os.path.exists(dp):
        return False
    try:
        last = datetime.fromisoformat(open(dp).read().strip())
        return (datetime.now() - last).total_seconds() < max_hours * 3600
    except:
        return False

def _cache_save(name, data):
    with open(_cache_path(name), "wb") as f:
        pickle.dump(data, f)
    with open(_cache_date_path(name), "w") as f:
        f.write(datetime.now().isoformat())

def _cache_load(name):
    p = _cache_path(name)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return pickle.load(f)
    return None

# ============================================================
# Tushare 数据
# ============================================================
def fetch_stock_daily(ts_code, start_date="20200101", end_date=None, use_cache=True):
    """个股日线(前复权)"""
    cache_name = f"daily_{ts_code}"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            if end_date:
                return d[d['trade_date'] <= end_date]
            return d

    pro = get_ts_api()
    df = pro.daily(ts_code=ts_code, start_date=start_date,
                   end_date=end_date or datetime.now().strftime("%Y%m%d"),
                   fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_chg")
    if df is not None and len(df) > 0:
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        df = df.sort_values('trade_date').reset_index(drop=True)
        # 前复权
        try:
            adj = pro.adj_factor(ts_code=ts_code, start_date=start_date)
            if adj is not None and len(adj) > 0:
                adj['trade_date'] = pd.to_datetime(adj['trade_date'], format='%Y%m%d')
                adj = adj.sort_values('trade_date').reset_index(drop=True)
                latest_adj = adj['adj_factor'].iloc[-1]
                adj_map = dict(zip(adj['trade_date'], adj['adj_factor']))
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = df.apply(
                        lambda r: r[col] * adj_map.get(r['trade_date'], 1) / latest_adj, axis=1)
        except:
            pass
        _cache_save(cache_name, df)
    return df

def fetch_index_daily(ts_code="000001.SH", start_date="20200101", use_cache=True):
    """指数日线"""
    cache_name = f"idx_{ts_code}"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    pro = get_ts_api()
    df = pro.index_daily(ts_code=ts_code, start_date=start_date,
                         fields="ts_code,trade_date,open,high,low,close,vol")
    if df is not None and len(df) > 0:
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        df = df.sort_values('trade_date').reset_index(drop=True)
        _cache_save(cache_name, df)
    return df

def fetch_market_breadth(trade_date, use_cache=True):
    """涨跌家数"""
    cache_name = f"breadth_{trade_date}"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    pro = get_ts_api()
    df = pro.daily(trade_date=trade_date,
                   fields="ts_code,trade_date,pct_chg")
    if df is not None and len(df) > 0:
        up = len(df[df['pct_chg'] > 0])
        down = len(df[df['pct_chg'] < 0])
        flat = len(df[df['pct_chg'] == 0])
        total = len(df)
        result = {
            'trade_date': trade_date,
            'up': up, 'down': down, 'flat': flat, 'total': total,
            'ad_ratio': up / max(total, 1)
        }
        _cache_save(cache_name, result)
        return result
    return {'trade_date': trade_date, 'up': 0, 'down': 0, 'flat': 0,
            'total': 0, 'ad_ratio': 0.5}

def fetch_stock_basic(use_cache=True):
    """股票基本信息(上市日期, 市值等)"""
    cache_name = "stock_basic"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    pro = get_ts_api()
    df = pro.stock_basic(fields="ts_code,symbol,name,area,industry,list_date")
    if df is not None:
        df['list_date'] = pd.to_datetime(df['list_date'], format='%Y%m%d')
        _cache_save(cache_name, df)
    return df

def fetch_daily_basic(ts_code=None, trade_date=None, fields=None, use_cache=True):
    """每日基本面(PE, PB, 总市值, 流通市值, 换手率)"""
    if fields is None:
        fields = "ts_code,trade_date,close,pe,pb,total_mv,circ_mv,turnover_rate"
    cache_name = f"daily_basic_{trade_date or ts_code}"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    pro = get_ts_api()
    df = pro.daily_basic(ts_code=ts_code, trade_date=trade_date, fields=fields)
    if df is not None and len(df) > 0:
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        _cache_save(cache_name, df)
    return df

# ============================================================
# 东财 板块数据
# ============================================================
def _em_fetch(url, params, retries=2):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("data") and data["data"].get("diff"):
                return data["data"]["diff"]
        except:
            time.sleep(0.5)
    return []

def fetch_concept_boards(use_cache=True):
    """概念板块排行 (涨幅/资金流/领涨股)"""
    cache_name = "em_concepts"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    params = {
        "pn": "1", "pz": "80",
        "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3",  # 按涨幅排序
        "fs": "m:90+t:3",  # 概念板块
        "fields": "f2,f3,f4,f8,f12,f14,f62,f104,f105,f128,f140,f141,f136"
    }
    raw = _em_fetch(EASTMONEY["concept_url"], params)
    if raw:
        # f12=代码, f14=名称, f3=涨跌幅, f62=主力净流入
        # f128=领涨股名称, f140=领涨股代码, f136=领涨股涨幅
        # f104=板块内股票数, f105=下跌股票数
        df = pd.DataFrame(raw)
        df = df.rename(columns={
            "f12": "code", "f14": "name", "f3": "pct_chg",
            "f62": "main_net_inflow", "f128": "lead_name",
            "f140": "lead_code", "f136": "lead_pct",
            "f104": "stock_count", "f105": "down_count"
        })
        for c in ["pct_chg", "main_net_inflow", "lead_pct"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        _cache_save(cache_name, df)
        return df
    return pd.DataFrame()

def fetch_industry_boards(use_cache=True):
    """行业板块排行"""
    cache_name = "em_industries"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    params = {
        "pn": "1", "pz": "50",
        "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:2",  # 行业板块
        "fields": "f2,f3,f4,f8,f12,f14,f62,f104,f105,f128,f140,f141"
    }
    raw = _em_fetch(EASTMONEY["industry_url"], params)
    if raw:
        df = pd.DataFrame(raw)
        df = df.rename(columns={
            "f12": "code", "f14": "name", "f3": "pct_chg",
            "f62": "main_net_inflow", "f128": "lead_name",
            "f140": "lead_code", "f136": "lead_pct",
            "f104": "stock_count", "f105": "down_count"
        })
        for c in ["pct_chg", "main_net_inflow", "lead_pct"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        _cache_save(cache_name, df)
        return df
    return pd.DataFrame()

def fetch_board_stocks(board_code, use_cache=True):
    """获取板块成分股"""
    cache_name = f"board_stocks_{board_code}"
    if use_cache:
        d = _cache_load(cache_name)
        if d is not None:
            return d

    params = {
        "pn": "1", "pz": "200",
        "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3",
        "fs": f"b:{board_code}",
        "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f15,f16,f17,f18"
    }
    # f12=代码, f14=名称, f2=最新价, f3=涨跌幅, f5=成交量, f6=成交额,
    # f7=量比, f8=换手率, f15=最高, f16=最低, f17=开盘, f18=昨收
    raw = _em_fetch(EASTMONEY["stock_list_url"], params)
    if raw:
        df = pd.DataFrame(raw)
        df = df.rename(columns={
            "f12": "code", "f14": "name", "f2": "price",
            "f3": "pct_chg", "f5": "volume", "f6": "amount",
            "f7": "vol_ratio", "f8": "turnover_rate",
            "f15": "high", "f16": "low", "f17": "open", "f18": "pre_close"
        })
        for c in ["price", "pct_chg", "volume", "amount", "vol_ratio",
                   "turnover_rate", "high", "low", "open", "pre_close"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        _cache_save(cache_name, df)
        return df
    return pd.DataFrame()

# ============================================================
# 通达信本地数据
# ============================================================
def parse_tdx_day(filepath):
    """解析通达信日线二进制文件"""
    data = []
    try:
        with open(filepath, "rb") as f:
            while True:
                row = f.read(32)
                if len(row) < 32:
                    break
                vals = struct.unpack("IIIIIfII", row)
                # date=vals[0], open=vals[1]/100, high=vals[2]/100, low=vals[3]/100,
                # close=vals[4]/100, vol=vals[5], amount=vals[6], (reserved=vals[7])
                date_int = vals[0]
                if date_int < 19900101:
                    continue
                date_str = str(date_int)
                year, month, day = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
                try:
                    dt = pd.Timestamp(year, month, day)
                except:
                    continue
                data.append({
                    "trade_date": dt,
                    "open": vals[1] / 100.0,
                    "high": vals[2] / 100.0,
                    "low": vals[3] / 100.0,
                    "close": vals[4] / 100.0,
                    "vol": vals[5],
                })
    except FileNotFoundError:
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    return df.sort_values("trade_date").reset_index(drop=True)

def get_tdx_daily(symbol, market="sz"):
    """读取通达信日线, symbol如'159516', market='sh'/'sz'"""
    dirpath = TDX_SH_LDAY if market == "sh" else TDX_SZ_LDAY
    files = os.listdir(dirpath)
    # 通达信文件名: symbol.day (如 sz159516.day)
    target = f"{market}{symbol}.day"
    if target not in files:
        # 尝试大写
        target = f"{market}{symbol}".upper() + ".day"
    filepath = os.path.join(dirpath, target)
    return parse_tdx_day(filepath)

# ============================================================
# 技术指标
# ============================================================
def calc_ma(df, periods=[5, 10, 20, 60]):
    """计算均线"""
    for p in periods:
        df[f'ma{p}'] = df['close'].rolling(p).mean()
    return df

def calc_rsi(df, period=14):
    """RSI"""
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(window=period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period, min_periods=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    df[f'rsi_{period}'] = 100 - 100 / (1 + rs)
    return df

def calc_macd(df, fast=12, slow=26, signal=9):
    """MACD"""
    exp1 = df['close'].ewm(span=fast, adjust=False).mean()
    exp2 = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_dif'] = exp1 - exp2
    df['macd_dea'] = df['macd_dif'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = 2 * (df['macd_dif'] - df['macd_dea'])
    return df

# ============================================================
# 通达信全量替代方案 (TDX Local)
# ============================================================

# 通达信指数代码映射
TDX_INDEX_MAP = {
    "000001.SH": ("sh", "000001"),   # 上证指数
    "399001.SZ": ("sz", "399001"),   # 深证成指
    "399006.SZ": ("sz", "399006"),   # 创业板指
    "000688.SH": ("sh", "000688"),   # 科创50
    "399303.SZ": ("sz", "399303"),   # 国证2000
    "000852.SH": ("sh", "000852"),   # 中证1000
    "000905.SH": ("sh", "000905"),   # 中证500
}

def get_tdx_all_dates():
    """从通达信文件提取所有交易日(去重合并)"""
    cache_name = "tdx_all_dates"
    d = _cache_load(cache_name)
    if d is not None:
        return d
    date_set = set()
    for dirpath in [TDX_SH_LDAY, TDX_SZ_LDAY]:
        if not os.path.exists(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.endswith('.day'):
                continue
            df = parse_tdx_day(os.path.join(dirpath, fname))
            if len(df) > 0:
                for dt in df['trade_date']:
                    date_set.add(dt)
    dates = sorted(date_set)
    _cache_save(cache_name, dates)
    return dates

def fetch_index_daily_tdx(ts_code="000001.SH", start_date=None):
    """通达信版指数日线"""
    info = TDX_INDEX_MAP.get(ts_code)
    if not info:
        return pd.DataFrame()
    market, symbol = info
    df = get_tdx_daily(symbol, market)
    if len(df) == 0:
        return df
    if start_date:
        sd = pd.Timestamp(start_date)
        df = df[df['trade_date'] >= sd]
    return df.reset_index(drop=True)

def fetch_stock_daily_tdx(ts_code, start_date="20200101"):
    """通达信版个股日线"""
    # ts_code格式: 000001.SZ / 600000.SH
    if '.' in ts_code:
        symbol, market_suffix = ts_code.split('.')
        market = market_suffix.lower()
    else:
        symbol = ts_code
        market = 'sz' if ts_code.startswith(('0', '3')) else 'sh'
    df = get_tdx_daily(symbol, market)
    if len(df) == 0:
        return df
    if start_date:
        sd = pd.Timestamp(start_date)
        df = df[df['trade_date'] >= sd]
    return df.reset_index(drop=True)

def fetch_market_breadth_tdx(trade_date_str):
    """通达信版涨跌家数(遍历全部日线文件)"""
    cache_name = f"breadth_tdx_{trade_date_str}"
    d = _cache_load(cache_name)
    if d is not None:
        return d
    target = pd.Timestamp(trade_date_str)
    up, down, flat, total, big_up, big_down = 0, 0, 0, 0, 0, 0
    for dirpath in [TDX_SH_LDAY, TDX_SZ_LDAY]:
        if not os.path.exists(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.endswith('.day'):
                continue
            # 跳过指数(指数文件名以0或3开头且非6位数字股票代码)
            # 简单处理: 只统计6位代码且非纯指数
            base = fname.replace('.day', '').lower()
            if len(base) != 7:  # 格式: sh600000 / sz000001
                continue
            code = base[2:]
            # 跳过指数代码 (000001-000999 上海指数, 399001-399999 深圳指数)
            if base.startswith('sh') and code.startswith('000') and int(code) < 1000:
                continue
            if base.startswith('sz') and code.startswith('399'):
                continue
            if base.startswith('sh') and code.startswith('000') and int(code) < 1000:
                continue

            df = parse_tdx_day(os.path.join(dirpath, fname))
            if len(df) == 0:
                continue
            # 取最后两行计算涨跌
            if len(df) < 2:
                continue
            last = df.iloc[-1]
            if last['trade_date'] != target:
                continue
            prev = df.iloc[-2]
            total += 1
            if prev['close'] > 0:
                pct = (last['close'] - prev['close']) / prev['close'] * 100
                if pct > 0:
                    up += 1
                elif pct < 0:
                    down += 1
                else:
                    flat += 1
                if pct > 5:
                    big_up += 1
                elif pct < -5:
                    big_down += 1
    result = {
        'trade_date': trade_date_str,
        'up': up, 'down': down, 'flat': flat, 'total': total,
        'big_up': big_up, 'big_down': big_down,
        'ad_ratio': up / max(total, 1)
    }
    _cache_save(cache_name, result)
    return result

def get_trade_dates_tdx(start="20200101", end=None):
    """通达信版交易日列表"""
    dates = get_tdx_all_dates()
    sd = pd.Timestamp(start)
    ed = pd.Timestamp(end) if end else pd.Timestamp.now()
    filtered = [d.strftime('%Y%m%d') for d in dates if sd <= d <= ed]
    return filtered

def count_trade_days_tdx(start_date_str, end_date_str):
    """通达信版交易日计数"""
    dates = get_trade_dates_tdx()
    count = 0
    for d in dates:
        if start_date_str < d <= end_date_str:
            count += 1
    return count

# ============================================================
# 交易日历 (Tushare备用)
# ============================================================
def get_trade_dates(start="20200101", end=None):
    """获取交易日列表"""
    end = end or datetime.now().strftime("%Y%m%d")
    cache_name = f"cal_{start}_{end}"
    d = _cache_load(cache_name)
    if d:
        return d
    pro = get_ts_api()
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end, is_open='1')
    if df is not None and len(df) > 0:
        dates = sorted(df['cal_date'].tolist())
        _cache_save(cache_name, dates)
        return dates
    return []

def count_trade_days(start_date_str, end_date_str):
    """计算两个日期间的交易日数"""
    dates = get_trade_dates()
    count = 0
    for d in dates:
        if start_date_str < d <= end_date_str:
            count += 1
    return count

# ============================================================
# 同花顺板块接口 (需6000积分)
# ============================================================
def fetch_ths_index(type='N', use_cache=True):
    """获取同花顺板块列表
    type: N=概念, I=行业, R=地域, S=特色, ST=风格, TH=主题, BB=宽基
    """
    cache_name = f"ths_index_{type}"
    if use_cache and _cache_is_fresh(cache_name, max_hours=24):
        return _cache_load(cache_name)
    
    pro = get_ts_api()
    df = pro.ths_index(type=type)
    if df is not None and len(df) > 0:
        _cache_save(cache_name, df)
    return df

def fetch_ths_board_daily(ts_code, start_date=None, end_date=None, use_cache=True):
    """获取同花顺板块日线数据
    ts_code: 板块代码如 885343.TI (稀土永磁)
    返回: trade_date, close, open, high, low, pct_change, vol, turnover_rate
    """
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    
    cache_name = f"ths_daily_{ts_code}_{start_date}_{end_date}"
    if use_cache and _cache_is_fresh(cache_name, max_hours=4):
        return _cache_load(cache_name)
    
    pro = get_ts_api()
    df = pro.ths_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        df = df.sort_values('trade_date')
        _cache_save(cache_name, df)
    return df

def fetch_all_concept_boards_daily(trade_date=None, use_cache=True):
    """获取所有概念板块当日数据
    概念板块 ts_code 以 885 开头 (如 885343.TI 稀土永磁)
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')
    
    cache_name = f"ths_all_concepts_{trade_date}"
    if use_cache and _cache_is_fresh(cache_name, max_hours=4):
        return _cache_load(cache_name)
    
    # 获取概念板块列表（只取真正的概念，排除沪深300等指数）
    concepts = fetch_ths_index(type='N', use_cache=True)
    if concepts is None or len(concepts) == 0:
        return None
    
    # 概念板块特征：ts_code 以 885 开头，name 不含"样本股"/"成份股"
    real_concepts = concepts[
        concepts['ts_code'].str.startswith('885') &
        ~concepts['name'].str.contains('样本股|成份股|指数', na=False)
    ]
    
    pro = get_ts_api()
    df = pro.ths_daily(trade_date=trade_date)
    
    if df is not None and len(df) > 0:
        # 只保留概念板块
        df = df[df['ts_code'].isin(real_concepts['ts_code'])]
        df = df.merge(real_concepts[['ts_code','name']], on='ts_code', how='left')
        df = df.sort_values('pct_change', ascending=False)
        _cache_save(cache_name, df)
    return df

def fetch_all_industry_boards_daily(trade_date=None, use_cache=True):
    """获取所有行业板块当日数据
    行业板块 ts_code 以 884 或 700 开头
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')
    
    cache_name = f"ths_all_industry_{trade_date}"
    if use_cache and _cache_is_fresh(cache_name, max_hours=4):
        return _cache_load(cache_name)
    
    industries = fetch_ths_index(type='I', use_cache=True)
    if industries is None or len(industries) == 0:
        return None
    
    # 行业板块特征：ts_code 以 884 开头（细分行业）
    real_industries = industries[industries['ts_code'].str.startswith('884')]
    
    pro = get_ts_api()
    df = pro.ths_daily(trade_date=trade_date)
    
    if df is not None and len(df) > 0:
        df = df[df['ts_code'].isin(real_industries['ts_code'])]
        df = df.merge(real_industries[['ts_code','name']], on='ts_code', how='left')
        df = df.sort_values('pct_change', ascending=False)
        _cache_save(cache_name, df)
    return df

def fetch_ths_member(ts_code, use_cache=True):
    """获取同花顺板块成分股
    ts_code: 板块代码如 885800.TI (小米概念)
    返回: ts_code, con_code, con_name, is_new
    """
    cache_name = f"ths_member_{ts_code}"
    if use_cache and _cache_is_fresh(cache_name, max_hours=24):
        return _cache_load(cache_name)
    
    pro = get_ts_api()
    df = pro.ths_member(ts_code=ts_code)
    if df is not None and len(df) > 0:
        # 只保留最新成分股 (is_new='Y')
        if 'is_new' in df.columns:
            df = df[df['is_new'] == 'Y']
        _cache_save(cache_name, df)
    return df

# ============================================================
# 中证2000成分股 (精确列表, 替代市值过滤)
# ============================================================
def fetch_csi2000_stocks(use_cache=True):
    """获取中证2000指数成分股(精确列表)
    
    接口: index_weight(ts_code='932000.CSI')
    积分要求: >=2000分
    数据频率: 月度(每月末更新)
    
    返回: set of 纯代码 {'000519', '600183', ...}
          (去掉交易所后缀, 匹配 dragon_score.py 中的 code 字段)
    """
    cache_name = "csi2000_stocks"
    if use_cache and _cache_is_fresh(cache_name, max_hours=24):
        result = _cache_load(cache_name)
        if result is not None:
            return result

    pro = get_ts_api()
    try:
        df = pro.index_weight(index_code='932000.CSI')
    except Exception as e:
        print(f"⚠️ 中证2000成分股获取失败: {e}")
        return None  # None表示获取失败, 调用方需fallback

    if df is None or len(df) == 0:
        print("⚠️ 中证2000成分股返回空")
        return None

    # 提取 con_code, 去掉交易所后缀 (.SH -> 空, .SZ -> 空)
    # con_code 格式: 000519.SZ, 600183.SH
    code_set = set()
    for _, row in df.iterrows():
        con_code = row.get('con_code', '')
        if not con_code:
            continue
        pure_code = con_code.split('.')[0]  # 000519.SZ -> 000519
        code_set.add(pure_code)

    print(f"CSI2000 stocks: {len(code_set)} loaded")
    _cache_save(cache_name, code_set)
    return code_set
