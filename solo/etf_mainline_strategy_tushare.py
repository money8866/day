"""
ETF主线轮动策略 - Tushare版 (收盘后运行)
策略: 多因子动量评分 (动量+量能+波动率+相对强弱)
ETF池: 37只行业ETF (全验证)
数据源: Tushare API

用法:
    python etf_mainline_strategy_tushare.py              # 最新交易日
    python etf_mainline_strategy_tushare.py --date 20260713  # 指定历史日期回溯
    python etf_mainline_strategy_tushare.py --date 20260713 --backtest  # 回溯模式(不发送通知、不更新状态)
"""
from dotenv import load_dotenv
import os, datetime, pandas as pd, numpy as np, json, time, argparse
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
WECHAT_KEY = os.getenv("WECHAT_KEY")
import tushare as ts
import requests
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

STATE_FILE = os.path.join(os.path.dirname(__file__), "etf_mainline_state_tushare.json")
MOM_PERIOD = 20
REBAL_DAYS = 60
TOP_N = 1
DYNAMIC_EXIT_TOPN = 5       # 动态退出: 跌出TOP5则触发调仓
MIN_HOLD_DAYS = 5           # 动态退出保护: 最少持仓5个交易日才允许动态退出

# ──────────────────────────────────────────
# 缓存配置（复用 cache_daily 目录）
# ──────────────────────────────────────────
CACHE_DIR = r"D:\mystock\cache_daily"
ETF_FUND_CACHE_DIR = os.path.join(CACHE_DIR, "etf_fund")
ETF_CONS_CACHE_DIR = os.path.join(CACHE_DIR, "etf_cons")
ETF_CONS_JSON = os.path.join(CACHE_DIR, "etf_constituents_all.json")
ETF_SHARE_CACHE_DIR = os.path.join(CACHE_DIR, "etf_share")
os.makedirs(ETF_FUND_CACHE_DIR, exist_ok=True)
os.makedirs(ETF_CONS_CACHE_DIR, exist_ok=True)
os.makedirs(ETF_SHARE_CACHE_DIR, exist_ok=True)

def _read_cache(filepath):
    try:
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            if not df.empty:
                return df
    except Exception:
        pass
    return None

def _save_cache(df, filepath):
    try:
        if df is not None and not df.empty:
            df.to_csv(filepath, index=False)
    except Exception:
        pass

def _cache_key_fund(ts_code, trade_date):
    """ETF基金净值缓存key"""
    return os.path.join(ETF_FUND_CACHE_DIR, f"{ts_code}_{trade_date}.csv")

def _cache_key_cons(ts_code, trade_date):
    """ETF成份股缓存key"""
    safe_name = ts_code.replace('.', '_')
    return os.path.join(ETF_CONS_CACHE_DIR, f"{safe_name}_{trade_date}.csv")

def _cache_key_share(ts_code, trade_date):
    """ETF份额规模缓存key"""
    safe_name = ts_code.replace('.', '_')
    return os.path.join(ETF_SHARE_CACHE_DIR, f"share_{safe_name}_{trade_date}.csv")

def _cache_key_stock(ts_code, trade_date):
    """个股日线缓存key"""
    safe_name = ts_code.replace('.', '_')
    return os.path.join(ETF_CONS_CACHE_DIR, f"stock_{safe_name}_{trade_date}.csv")

ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '工业母机': '159667','科创半导体':'588170',
}

BENCHMARK_CODE = '510300'


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_last_trade_date(specified_date=None):
    """
    获取最近交易日
    specified_date: 指定日期(YYYYMMDD格式字符串)，若为None则自动计算当前最近交易日
    """
    if specified_date:
        query_date = specified_date
    else:
        now = datetime.datetime.now()
        if now.hour < 15:
            query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')
        else:
            query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": f"ETF每日分析 - {TRADE_DATE}",
        "desp": msg
    }
    requests.post(url, data=data)


def send_pushplus(msg, token):
    """通过 PushPlus 推送微信消息（支持markdown）"""
    if not token:
        return
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": f"ETF每日分析 - {TRADE_DATE}",
        "content": msg,
        "template": "markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get("code") == 200:
            print("✅ PushPlus 已发送")
        else:
            print(f"⚠️ PushPlus 发送失败: {result.get('msg', '未知错误')}")
    except Exception as e:
        print(f"⚠️ PushPlus 异常: {e}")


def get_etf_constituents(ts_code, trade_date):
    """
    获取ETF成份股列表
    1. 优先读 etf_constituents_all.json（由 download_etf_constituents.py 生成，含权重weight）
    2. 缺失则走 API（etf_sz_cons / etf_sh_cons）并缓存到 etf_cons 目录
    返回：[{con_code, con_name, weight, qty, cpr}, ...]
    """
    # ---- 1. 读 etf_constituents_all.json ----
    if os.path.exists(ETF_CONS_JSON):
        try:
            with open(ETF_CONS_JSON, 'r', encoding='utf-8') as f:
                cons_map = json.load(f)
            etf_data = cons_map.get(ts_code)
            if etf_data and isinstance(etf_data, dict) and 'constituents' in etf_data:
                cons = etf_data['constituents']
                return [
                    {
                        'con_code': c.get('con_code', ''),
                        'con_name': c.get('con_name', ''),
                        'weight': c.get('weight', c.get('cpr', 0)),
                        'qty': c.get('qty', 0),
                        'cpr': c.get('cpr', 0),
                    }
                    for c in cons
                ]
            elif etf_data and isinstance(etf_data, list):
                # 旧格式兼容: 纯股票代码列表
                try:
                    sb = pro.stock_basic(list_status='L', fields='ts_code,name')
                    name_map = dict(zip(sb['ts_code'], sb['name']))
                except Exception:
                    name_map = {}
                return [{'con_code': c, 'con_name': name_map.get(c, ''),
                         'weight': 0, 'qty': 0, 'cpr': 0} for c in etf_data]
        except Exception:
            pass

    # ---- 2. 读单独CSV缓存（旧格式兼容） ----
    cache_file = _cache_key_cons(ts_code, trade_date)
    cached = _read_cache(cache_file)
    if cached is not None:
        result = []
        for _, row in cached.iterrows():
            result.append({
                'con_code': row.get('con_code', ''),
                'con_name': row.get('con_name', ''),
                'weight': row.get('weight', row.get('cpr', 0)),
                'qty': row.get('qty', 0),
                'cpr': row.get('cpr', 0),
            })
        return result

    # ---- 3. API 拉取 ----
    prefix = ts_code[0]
    try:
        if prefix == '1':
            df = pro.etf_sz_cons(
                ts_code=ts_code,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
        else:
            df = pro.etf_sh_cons(
                ts_code=ts_code,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
        if df is None or df.empty:
            return []

        df = df[df['trade_date'] <= trade_date]
        if df.empty:
            return []
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        _save_cache(df, cache_file)

        result = []
        for _, row in df.iterrows():
            result.append({
                'con_code': row.get('con_code', ''),
                'con_name': row.get('con_name', ''),
                'weight': row.get('weight', row.get('cpr', 0)),
                'qty': row.get('qty', 0),
                'cpr': row.get('cpr', 0),
            })
        return result
    except Exception as e:
        print(f"  [WARN] 获取{ts_code}成份股失败: {e}")
        return []


# ──────────────────────────────────────────
# 申万行业纯度因子 (Purity Score)
# ──────────────────────────────────────────
SW_INDUSTRY_CACHE = os.path.join(CACHE_DIR, "sw_industry_map.json")

# 每个ETF对应的"纯度行业白名单" (申万一级行业名称)
# 不在此白名单的成份股将被过滤, 位置让给纯度更高的股票
ETF_PURITY_WHITELIST = {
    '创新药': ['医药生物'],
    '医药':   ['医药生物'],
    '医疗器械': ['医药生物'],
    '半导体': ['电子'],
    '芯片': ['电子'],
    '半导体设备': ['电子', '机械设备'],
    '科创半导体': ['电子'],
    '人工智能': ['计算机', '通信', '电子', '传媒'],
    '软件': ['计算机'],
    '通信': ['通信'],
    '消费电子': ['电子'],
    '金融科技': ['计算机', '非银金融'],
    '游戏': ['传媒', '计算机'],
    '新能源': ['电力设备'],
    '光伏': ['电力设备'],
    '储能': ['电力设备'],
    '电池': ['电力设备', '有色金属'],
    '新能源车': ['汽车', '电力设备'],
    '军工': ['国防军工'],
    '航空航天': ['国防军工'],
    '机器人': ['机械设备', '电力设备', '电子', '家用电器'],
    '有色金属': ['有色金属'],
    '化工': ['基础化工', '石油石化'],
    '煤炭': ['煤炭'],
    '钢铁': ['钢铁'],
    '电力': ['公用事业'],
    '电网设备': ['电力设备', '公用事业'],
    '消费': ['食品饮料', '商贸零售', '纺织服饰', '社会服务', '家用电器', '农林牧渔', '轻工制造'],
    '食品饮料': ['食品饮料'],
    '酒': ['食品饮料'],
    '家电': ['家用电器'],
    '证券': ['非银金融'],
    '银行': ['银行'],
    '红利': [],  # 红利策略不限行业
    '工业母机': ['机械设备'],
}


def get_sw_industry(ts_code):
    """
    获取股票的申万一级行业分类 (带缓存)
    返回: l1_name (如"医药生物") 或 None
    """
    # 读缓存
    cache = {}
    if os.path.exists(SW_INDUSTRY_CACHE):
        try:
            with open(SW_INDUSTRY_CACHE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    if ts_code in cache:
        return cache[ts_code]

    # API查询
    try:
        df = pro.index_member_all(ts_code=ts_code)
        if df is not None and not df.empty:
            # 取最新记录 (is_new='Y')
            if 'is_new' in df.columns:
                df = df[df['is_new'] == 'Y']
            if not df.empty:
                l1_name = str(df.iloc[0].get('l1_name', ''))
                cache[ts_code] = l1_name
                # 写缓存
                with open(SW_INDUSTRY_CACHE, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
                return l1_name
    except Exception:
        pass

    cache[ts_code] = None
    with open(SW_INDUSTRY_CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return None


def filter_by_purity(constituents, etf_name, min_weight=2.0):
    """
    纯度因子过滤: 根据申万一级行业过滤非纯度成份股

    Args:
        constituents: [{con_code, con_name, weight, qty, cpr}, ...]
        etf_name: ETF名称 (如 '创新药')
        min_weight: 最小持仓占比阈值, 低于此值不过滤

    Returns:
        (filtered_list, removed_list, purity_ratio)
    """
    whitelist = ETF_PURITY_WHITELIST.get(etf_name, [])
    if not whitelist:
        return constituents, [], 1.0

    filtered = []
    removed = []

    for con in constituents:
        con_code = con.get('con_code', '')
        weight = float(con.get('weight', con.get('cpr', 0)) or 0)

        if weight > 0:
            filtered.append(con)
            continue

        l1_name = get_sw_industry(con_code)

        if l1_name and l1_name in whitelist:
            filtered.append(con)
        elif l1_name:
            removed.append({**con, 'l1_name': l1_name, 'reason': f'行业({l1_name})不在白名单'})
        else:
            filtered.append(con)

    purity_ratio = len(filtered) / len(constituents) if constituents else 0
    return filtered, removed, purity_ratio


def compute_stock_momentum_score(ts_code, pro, lookback_days=60):
    """
    轻量动量评分（0-100）
    因子：5日涨幅(25%) + 10日涨幅(25%) + 20日涨幅(25%) + 量价趋势(15%) + MACD状态(10%)
    """
    try:
        end_date = TRADE_DATE
        start_date = (datetime.datetime.strptime(end_date, "%Y%m%d") - 
                      datetime.timedelta(days=lookback_days)).strftime("%Y%m%d")
        
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date,
                      fields="trade_date,close,vol")
        if df is None or len(df) < 20:
            return None
        
        df = df.sort_values("trade_date").reset_index(drop=True)
        close = df['close']
        vol = df['vol']
        
        mom5 = close.pct_change(5).iloc[-1] * 100 if len(close) >= 5 else 0
        mom10 = close.pct_change(10).iloc[-1] * 100 if len(close) >= 10 else 0
        mom20 = close.pct_change(20).iloc[-1] * 100 if len(close) >= 20 else 0
        
        vol_recent = vol.tail(5).mean() if len(vol) >= 5 else 0
        vol_hist = vol.tail(20).mean() if len(vol) >= 20 else 0
        vol_ratio = vol_recent / (vol_hist + 1e-6) if vol_hist > 0 else 1
        
        ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else close.iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else close.iloc[-1]
        ma_trend = 1 if ma5 > ma20 else 0
        
        macd_df = pro.fina_indicator(ts_code=ts_code, start_date=start_date, end_date=end_date,
                                     fields="end_date,macd_dif,macd_dea,macd")
        macd_status = 0
        if macd_df is not None and len(macd_df) > 0:
            last = macd_df.sort_values("end_date").iloc[-1]
            dif = float(last.get('macd_dif', 0) or 0)
            dea = float(last.get('macd_dea', 0) or 0)
            macd_status = 1 if dif > dea else 0
        
        score = (
            min(100, max(-100, mom5)) * 0.25 +
            min(100, max(-100, mom10)) * 0.25 +
            min(100, max(-100, mom20)) * 0.25 +
            (100 if ma_trend and vol_ratio >= 1 else 50) * 0.15 +
            (100 if macd_status else 0) * 0.10
        )
        score = max(0, min(100, score))
        
        return {
            'score': round(score, 2),
            'mom5': round(mom5, 2),
            'mom10': round(mom10, 2),
            'mom20': round(mom20, 2),
            'vol_ratio': round(vol_ratio, 2),
            'ma_trend': ma_trend,
            'macd_status': macd_status,
        }
    except Exception as e:
        print(f"  [WARN] {ts_code}动量评分失败: {e}")
        return None


def normalize_score(series):
    """将序列归一化到0-100分"""
    if len(series) < 2:
        return pd.Series([50] * len(series), index=series.index)
    min_val = series.min()
    max_val = series.max()
    if max_val == min_val:
        return pd.Series([50] * len(series), index=series.index)
    return (series - min_val) / (max_val - min_val) * 100


def calculate_multi_factor_score(df, benchmark_df, mom_period=20, share_df=None):
    """
    计算多因子综合评分
    因子1: 20日动量 (40%)
    因子2: 量能配合 (25%) - 成交量放大 + ETF份额规模变化
    因子3: 风险调整收益 (20%) - 动量/波动率
    因子4: 相对强弱 (15%) - 相对于沪深300的表现
    """
    if len(df) < mom_period + 5:
        return None

    close = df['close']

    mom_20d = close.pct_change(mom_period).iloc[-1] * 100

    # 量能配合: 最近5日 vs 前15日(不重叠区间), 避免重叠稀释变化
    vol = df.get('vol', None)
    if vol is not None and len(vol) >= mom_period:
        recent_vol = vol.tail(5).mean()
        prev_vol = vol.tail(mom_period).head(mom_period - 5).mean()
        vol_ratio = recent_vol / (prev_vol + 1e-6)
        vol_score = min(max(vol_ratio * 50, 0), 100)
    else:
        # vol缺失时尝试用amount替代
        amount = df.get('amount', None)
        if amount is not None and len(amount) >= mom_period:
            recent_amt = amount.tail(5).mean()
            prev_amt = amount.tail(mom_period).head(mom_period - 5).mean()
            amt_ratio = recent_amt / (prev_amt + 1e-6)
            vol_score = min(max(amt_ratio * 50, 0), 100)
        else:
            vol_score = 50

    # ETF份额规模变化评分: 反映资金净申购/赎回流向
    share_score = 50  # 默认中性
    if share_df is not None and len(share_df) >= 10:
        share_df = share_df.sort_values("trade_date").reset_index(drop=True)
        if 'total_share' in share_df.columns:
            total_share = share_df['total_share']
            recent_share = total_share.tail(5).mean()
            prev_share = total_share.head(len(total_share) - 5).mean() if len(total_share) > 5 else total_share.mean()
            if prev_share > 0:
                share_change = (recent_share - prev_share) / prev_share * 100
                # 份额增长=资金流入=加分: +1%→65分, +3%→80分, +5%→100分
                # 份额减少=资金流出=减分: -1%→35分, -3%→20分
                share_score = max(0, min(100, 50 + share_change * 15))
    # 量能总分 = 成交量(60%) + 份额规模变化(40%)
    vol_score = vol_score * 0.60 + share_score * 0.40

    daily_returns = close.pct_change().dropna()
    if len(daily_returns) >= mom_period:
        volatility = daily_returns.tail(mom_period).std() * np.sqrt(252) * 100
        if volatility > 0:
            risk_adj_score = min(mom_20d / volatility * 10, 100)
        else:
            risk_adj_score = 50
    else:
        risk_adj_score = 50

    if len(benchmark_df) >= mom_period + 1:
        bm_return = benchmark_df['close'].pct_change(mom_period).iloc[-1] * 100
        relative_strength = mom_20d - bm_return
        rel_score = 50 + relative_strength
        rel_score = max(0, min(100, rel_score))
    else:
        rel_score = 50

    # 动量映射到0-100分: +0%→50分, +10%→70分, +25%→100分, -10%→30分
    mom_score = max(0, min(100, 50 + mom_20d * 2))
    total_score = (
        mom_score * 0.40 +
        vol_score * 0.25 +
        risk_adj_score * 0.20 +
        rel_score * 0.15
    )

    return {
        'momentum': round(mom_20d, 2),
        'vol_score': round(vol_score, 2),
        'risk_adj': round(risk_adj_score, 2),
        'rel_strength': round(rel_score, 2),
        'share_score': round(share_score, 2),
        'total_score': round(total_score, 2)
    }


def stock_alpha_ranking(constituents, top_etf_name, today, pro, etf_df, trade_date):
    """
    ETF Component Stock Alpha Ranking Engine V1.0
    
    对TOP1 ETF的成份股进行Alpha评分，选出未来20-60个交易日内
    最可能跑赢该ETF的股票。
    
    四模块评分体系：
    - Module 1: Relative Strength Alpha (40%) - 相对ETF的超额收益
    - Module 2: Trend Quality (25%) - 趋势质量
    - Module 3: Capital Strength (20%) - 资金强度
    - Module 4: Fundamental Quality (15%) - 基本面质量
    
    Returns: (console_text, csv_path, df_ranked)
    """
    lines = []
    
    lines.append(f"\n  {'='*70}")
    lines.append(f"  ETF Component Stock Alpha Ranking V1.0")
    lines.append(f"  ETF: {top_etf_name}  |  Trade Date: {trade_date}")
    lines.append(f"  {'='*70}")
    
    # ──────────────────────────────────────────
    # 1. STOCK POOL FILTER
    # ──────────────────────────────────────────
    lines.append(f"\n  [Step 1] Stock Pool Filter (target: 30-100 liquid stocks)")
    
    etf_close = etf_df['close'].values
    stock_results = []
    
    for con in constituents:
        con_code = con['con_code']
        con_name = con['con_name']
        if not con_code:
            continue
        
        if '.' in con_code:
            con_ts_code = con_code
        else:
            con_ts_code = f"{con_code}.SH" if con_code.startswith('6') else f"{con_code}.SZ"
        
        try:
            cache_file = _cache_key_stock(con_ts_code, trade_date)
            con_df = _read_cache(cache_file)
            if con_df is None:
                con_df = pro.daily(ts_code=con_ts_code,
                                   start_date=(today - datetime.timedelta(days=250)).strftime("%Y%m%d"),
                                   end_date=trade_date,
                                   fields="ts_code,trade_date,open,close,high,low,vol,amount")
                _save_cache(con_df, cache_file)
                time.sleep(0.1)
            
            if con_df is None or len(con_df) < 60:
                continue
            
            con_df = con_df.copy()
            con_df["trade_date"] = pd.to_datetime(con_df["trade_date"], format="%Y%m%d")
            con_df = con_df.sort_values("trade_date").reset_index(drop=True)
            con_df = con_df[con_df["trade_date"] <= today].reset_index(drop=True)
            
            if len(con_df) < 60:
                continue
            
            close_arr = con_df['close'].values
            vol_arr = con_df['vol'].values
            high_arr = con_df['high'].values
            low_arr = con_df['low'].values
            # Tushare pro.daily: amount=千元, vol=手(1手=100股)
            if 'amount' in con_df.columns:
                amount_arr = con_df['amount'].values * 1000  # 千元 → 元
            else:
                amount_arr = vol_arr * 100 * close_arr  # 手×100股/手×元/股 = 元
            
            # === FILTERS ===
            # 上市天数检查：至少需要60天数据用于MA计算
            listing_days = len(con_df)
            if listing_days < 60:
                continue
            avg_amount = amount_arr[-20:].mean() if listing_days >= 20 else amount_arr.mean()
            if avg_amount < 30000000:  # 日均成交额低于3000万元则过滤
                continue
            ma60 = np.mean(close_arr[-60:]) if len(close_arr) >= 60 else close_arr.mean()
            if close_arr[-1] < ma60:
                continue
            if vol_arr[-1] == 0 or vol_arr[-2] == 0:
                continue
            
            # ──────────────────────────────────────────
            # PHASE 1: Collect Raw Metrics (defer scoring to Phase 2 for cross-sectional ranking)
            # ──────────────────────────────────────────
            stock_ret_5 = (close_arr[-1] / close_arr[-6] - 1) * 100 if len(close_arr) >= 6 else 0
            today_ret = (close_arr[-1] / close_arr[-2] - 1) * 100 if len(close_arr) >= 2 else 0
            stock_ret_10 = (close_arr[-1] / close_arr[-11] - 1) * 100 if len(close_arr) >= 11 else 0
            stock_ret_20 = (close_arr[-1] / close_arr[-21] - 1) * 100 if len(close_arr) >= 21 else 0
            stock_ret_40 = (close_arr[-1] / close_arr[-41] - 1) * 100 if len(close_arr) >= 41 else 0
            stock_ret_60 = (close_arr[-1] / close_arr[-61] - 1) * 100 if len(close_arr) >= 61 else 0

            etf_ret_5 = (etf_close[-1] / etf_close[-6] - 1) * 100 if len(etf_close) >= 6 else 0
            etf_ret_10 = (etf_close[-1] / etf_close[-11] - 1) * 100 if len(etf_close) >= 11 else 0
            etf_ret_20 = (etf_close[-1] / etf_close[-21] - 1) * 100 if len(etf_close) >= 21 else 0
            etf_ret_40 = (etf_close[-1] / etf_close[-41] - 1) * 100 if len(etf_close) >= 41 else 0
            etf_ret_60 = (etf_close[-1] / etf_close[-61] - 1) * 100 if len(etf_close) >= 61 else 0

            alpha5 = stock_ret_5 - etf_ret_5
            alpha10 = stock_ret_10 - etf_ret_10
            alpha20 = stock_ret_20 - etf_ret_20
            alpha40 = stock_ret_40 - etf_ret_40
            alpha60 = stock_ret_60 - etf_ret_60

            # Alpha acceleration: short-term alpha - long-term alpha (KEY DIFFERENTIATOR)
            alpha_accel_5_20 = alpha5 - alpha20
            alpha_accel_10_40 = alpha10 - alpha40
            alpha_accel_20_60 = alpha20 - alpha60
            # 二阶加速度 (加速度的加速度): (alpha5-alpha10) - (alpha10-alpha20) = alpha5+alpha20-2*alpha10
            # 正值=近端加速比远端更快 = 爆发力正在增强而非衰减
            alpha_accel2_5_10_20 = alpha5 + alpha20 - 2 * alpha10

            # MA values
            ma5 = np.mean(close_arr[-5:]) if len(close_arr) >= 5 else close_arr[-1]
            ma10 = np.mean(close_arr[-10:]) if len(close_arr) >= 10 else close_arr[-1]
            ma20 = np.mean(close_arr[-20:]) if len(close_arr) >= 20 else close_arr[-1]
            ma20_prev = np.mean(close_arr[-21:-1]) if len(close_arr) >= 21 else ma20
            ma_slope = (ma20 / ma20_prev - 1) * 100 if ma20_prev > 0 else 0

            # Volatility (for coiled spring detection)
            ret_5d_arr = np.diff(np.log(close_arr[-6:])) * 100 if len(close_arr) >= 6 else np.array([0.0])
            ret_20d_arr = np.diff(np.log(close_arr[-21:])) * 100 if len(close_arr) >= 21 else np.array([0.0])
            vol_5d = float(np.std(ret_5d_arr)) if len(ret_5d_arr) > 1 else 5.0
            vol_20d = float(np.std(ret_20d_arr)) if len(ret_20d_arr) > 1 else 5.0
            vol_contraction = vol_5d / (vol_20d + 1e-6)  # <1 = contraction (coiled spring)

            # Position metrics
            high_60 = close_arr[-60:].max() if len(close_arr) >= 60 else close_arr.max()
            low_60 = close_arr[-60:].min() if len(close_arr) >= 60 else close_arr.min()
            dist_to_high = (close_arr[-1] / high_60 - 1) * 100
            pos_in_range = (close_arr[-1] - low_60) / (high_60 - low_60 + 1e-6) * 100
            pullback = (high_60 / close_arr[-1] - 1) * 100

            # Capital flow metrics
            amt_5 = amount_arr[-5:].mean() if len(amount_arr) >= 5 else amount_arr.mean()
            amt_20 = amount_arr[-20:].mean() if len(amount_arr) >= 20 else amount_arr.mean()
            amt_60 = amount_arr[-60:].mean() if len(amount_arr) >= 60 else amount_arr.mean()
            amt_accel_short = amt_5 / (amt_20 + 1e-6)  # short-term money inflow
            amt_accel_med = amt_20 / (amt_60 + 1e-6)

            # Volume-price: up-day vs down-day volume
            up_vols = []; down_vols = []
            for i in range(1, min(20, len(close_arr))):
                if close_arr[-i] > close_arr[-i-1]: up_vols.append(vol_arr[-i])
                elif close_arr[-i] < close_arr[-i-1]: down_vols.append(vol_arr[-i])
            up_avg = np.mean(up_vols) if up_vols else 0
            down_avg = np.mean(down_vols) if down_vols else 0
            vol_price_ratio = up_avg / (down_avg + 1e-6) if down_avg > 0 else 1.0

            # 突破质量评分: 量比+涨幅+突破前蓄势紧凑度
            vol_ratio_today = vol_arr[-1] / (vol_arr[-20:].mean() + 1e-6)
            low_10 = low_arr[-10:].min() if len(low_arr) >= 10 else low_arr[-1]
            high_10 = high_arr[-10:].max() if len(high_arr) >= 10 else high_arr[-1]
            low_20 = low_arr[-20:].min() if len(low_arr) >= 20 else low_arr[-1]
            high_20 = high_arr[-20:].max() if len(high_arr) >= 20 else high_arr[-1]
            range_10_pct = (high_10 / low_10 - 1) * 100
            range_20_pct = (high_20 / low_20 - 1) * 100
            pre_compress = range_10_pct / (range_20_pct + 1e-6)
            # 综合突破质量(0-100): 涨幅/量比/突破幅度/压缩程度各25分
            breakout_quality = (
                max(0, min(today_ret * 8, 25)) +
                max(0, min(vol_ratio_today * 12, 25)) +
                max(0, min(-dist_to_high * 5, 25)) +
                max(0, (1 - min(pre_compress, 1)) * 25)
            )

            # MA alignment (bullish stacking: close > MA5 > MA10 > MA20 > MA60)
            ma_alignment = 0
            if close_arr[-1] > ma5: ma_alignment += 1
            if close_arr[-1] > ma10: ma_alignment += 1
            if close_arr[-1] > ma20: ma_alignment += 1
            if close_arr[-1] > ma60: ma_alignment += 1
            if ma5 > ma10: ma_alignment += 1
            if ma10 > ma20: ma_alignment += 1
            if ma20 > ma60: ma_alignment += 1

            stock_results.append({
                'code': con_code, 'name': con_name,
                # Raw alphas
                'alpha5': alpha5, 'alpha10': alpha10,
                'alpha20': alpha20, 'alpha40': alpha40, 'alpha60': alpha60,
                'alpha_accel_5_20': alpha_accel_5_20,
                'alpha_accel_10_40': alpha_accel_10_40,
                'alpha_accel_20_60': alpha_accel_20_60,
                'alpha_accel2_5_10_20': alpha_accel2_5_10_20,
                # Returns
                'stock_ret5': stock_ret_5, 'stock_ret10': stock_ret_10,
                'stock_ret20': stock_ret_20, 'stock_ret60': stock_ret_60,
                # Trend
                'ma_alignment': ma_alignment, 'ma_slope': ma_slope,
                'close': close_arr[-1], 'ma60': ma60,
                # Volatility
                'vol_5d': vol_5d, 'vol_contraction': vol_contraction,
                # Position
                'dist_to_high': dist_to_high, 'pos_in_range': pos_in_range,
                'pullback': pullback,
                # Capital
                'amt_accel_short': amt_accel_short, 'amt_accel_med': amt_accel_med,
                'vol_price_ratio': vol_price_ratio,
                'breakout_quality': breakout_quality,
                'avg_amount': avg_amount,
            })
        except Exception:
            pass
    
    if len(stock_results) < 5:
        msg = f"  Not enough valid stocks ({len(stock_results)}), cannot rank"
        lines.append(msg)
        return "\n".join(lines), None, pd.DataFrame()
    
    df = pd.DataFrame(stock_results)
    
    # ════════════════════════════════════════════════════════════
    # PHASE 2: Cross-Sectional Percentile Scoring
    # Key principle: percentile-based scoring gives MAXIMUM differentiation
    # at the top end (TOP3 must score meaningfully higher than TOP4-5)
    # ════════════════════════════════════════════════════════════
    
    def _pct(series, ascending=True):
        """Percentile rank 0-100. ascending=True: higher value = higher score."""
        r = series.rank(pct=True)
        return r * 100 if ascending else (1 - r) * 100
    
    # ── MODULE 1: Alpha Acceleration + Level (35%) ──
    # 1a. Alpha level percentile (14%) - absolute outperformance vs ETF
    alpha_level_pct = (_pct(df['alpha10']) * 0.35 +
                       _pct(df['alpha20']) * 0.40 +
                       _pct(df['alpha60']) * 0.25)
    
    # 1b. Alpha acceleration percentile (21%) - THE KEY DIFFERENTIATOR
    #     Accelerating outperformance (alpha5 > alpha20 > alpha60) predicts
    #     continued future gains much better than alpha level alone
    #     + 二阶加速度: 近端加速 > 远端加速 = 爆发力增强
    alpha_accel_pct = (_pct(df['alpha_accel_5_20']) * 0.35 +
                       _pct(df['alpha_accel_10_40']) * 0.25 +
                       _pct(df['alpha_accel_20_60']) * 0.15 +
                       _pct(df['alpha_accel2_5_10_20']) * 0.25)
    
    module1_score = alpha_level_pct * 0.40 + alpha_accel_pct * 0.60
    
    # ── MODULE 2: Momentum & Trend Quality (25%) ──
    # 2a. MA alignment (8%) - bullish stacking (close > MA5 > MA10 > MA20 > MA60)
    ma_align_score = df['ma_alignment'] / 7 * 100
    
    # 2b. Momentum percentile (10%)
    momentum_pct = (_pct(df['stock_ret5']) * 0.30 +
                    _pct(df['stock_ret10']) * 0.35 +
                    _pct(df['stock_ret20']) * 0.35)
    
    # 2c. MA slope (4%) - trend direction strength
    ma_slope_score = _pct(df['ma_slope'])
    
    # 2d. Pullback depth (3%) - shallow pullback from high = healthy trend
    pullback_score = _pct(-df['pullback'])  # lower pullback is better
    
    module2_score = (ma_align_score * 0.32 + momentum_pct * 0.40 +
                     ma_slope_score * 0.16 + pullback_score * 0.12)
    
    # ── MODULE 3: Capital Flow Pattern (20%) ──
    # 3a. Short-term money inflow (8%)
    amt_inflow_score = _pct(df['amt_accel_short'])
    
    # 3b. Volume-price dominance (7%) - up-day volume > down-day volume
    vol_price_score = _pct(df['vol_price_ratio'])
    
    # 3c. Coiled spring: vol contraction + volume expansion (5%)
    #     Low recent volatility + recent money inflow = breakout setup
    vol_contraction_score = _pct(-df['vol_contraction'])  # lower ratio = contraction
    coiled_spring_score = vol_contraction_score * 0.5 + amt_inflow_score * 0.5
    
    module3_score = (amt_inflow_score * 0.40 + vol_price_score * 0.35 +
                     coiled_spring_score * 0.25)
    
    # ── MODULE 4: Position Quality + 突破质量 (12%) ──
    # 4a. Distance from 60D high (5%) - closer to high = momentum persistence
    dist_high_score = _pct(-df['dist_to_high'])
    
    # 4b. Position in 60D range (3%)
    pos_range_score = _pct(df['pos_in_range'])
    
    # 4c. Breakout proximity (2%) - at/near 60D high
    breakout_prox = df['dist_to_high'].apply(
        lambda x: 100 if x >= -2 else (80 if x >= -5 else (50 if x >= -10 else 20)))
    
    # 4d. 突破质量评分 (2%) - 今日涨幅/量比/突破幅度/蓄势紧凑度
    bq_score = _pct(df['breakout_quality'])
    
    module4_score = dist_high_score * 0.42 + pos_range_score * 0.25 + breakout_prox * 0.16 + bq_score * 0.17
    
    # ── MODULE 5: Volatility Quality (8%) ──
    # Low volatility = institutional accumulation, less noise, higher win rate
    module5_score = _pct(-df['vol_5d'])
    
    # === FINAL ALPHA SCORE ===
    df['alpha_score'] = (
        module1_score * 0.35 + module2_score * 0.25 +
        module3_score * 0.20 + module4_score * 0.12 +
        module5_score * 0.08
    ).clip(0, 100)
    
    # Store module scores for display
    df['relative_alpha'] = module1_score
    df['trend_quality'] = module2_score
    df['capital'] = module3_score
    df['fundamental'] = module4_score  # now "position quality"
    
    # === Special Factors (stronger for TOP3 differentiation) ===
    alpha_rank_pct = df['alpha_score'].rank(pct=True, ascending=False)
    
    # Leader Bonus: top 15% alpha + near 60D high → +8
    df['leader_bonus'] = 0
    df.loc[(alpha_rank_pct <= 0.15) & (df['dist_to_high'] >= -8), 'leader_bonus'] = 8
    
    # Breakout Bonus: 突破质量>=70分 + 距60日高<3% → +10 (highest probability setup)
    df['breakout_bonus'] = 0
    df.loc[(df['breakout_quality'] >= 70) & (df['dist_to_high'] >= -3), 'breakout_bonus'] = 10
    
    # Coiled Spring Bonus: vol contraction + near high + money inflow → +8
    df['spring_bonus'] = 0
    df.loc[(df['vol_contraction'] < 0.7) & (df['dist_to_high'] >= -10) &
           (df['amt_accel_short'] > 1.0), 'spring_bonus'] = 8
    
    # Crowding Penalty: extreme 5D return + extreme volume → -12
    ret_95 = df['stock_ret5'].quantile(0.95)
    df['crowding_penalty'] = 0
    df.loc[(df['stock_ret5'] > ret_95) & (df['amt_accel_short'] > 2.5), 'crowding_penalty'] = -12
    
    df['final_score'] = (
        df['alpha_score'] + df['leader_bonus'] + df['breakout_bonus'] +
        df['spring_bonus'] + df['crowding_penalty']
    ).clip(0, 100)
    
    df = df.sort_values('final_score', ascending=False).reset_index(drop=True)
    
    # === Trading Signals ===
    def assign_signal(row):
        s = row['final_score']; a20 = row['alpha20']
        if s > 85 and a20 > 5: return 'CORE_ALPHA'
        elif s >= 75: return 'STRONG'
        elif s >= 65: return 'WATCH'
        return 'AVOID'
    
    df['signal'] = df.apply(assign_signal, axis=1)
    df['expected_hold_days'] = df['signal'].map({'CORE_ALPHA': 40, 'STRONG': 30, 'WATCH': 20, 'AVOID': 0})
    
    # ── Console Output ──
    lines.append(f"  Valid stocks after filtering: {len(df)}")
    lines.append(f"")
    lines.append(f"  {'Rank':<5} {'Code':<10} {'Name':<8} {'Score':>6} {'Alpha5':>7} {'A20':>6} {'Accel2':>6} {'BQ':>4} {'Trend':>5} {'Cap':>5} {'Pos':>5} {'Bonus':>5} {'Signal':>12}")
    lines.append(f"  {'-'*96}")
    
    for i, (_, r) in enumerate(df.iterrows()):
        if i >= 15: break
        short_code = r['code'].replace('.SZ', '').replace('.SH', '')
        total_bonus = r.get('leader_bonus',0) + r.get('breakout_bonus',0) + r.get('spring_bonus',0) + r.get('crowding_penalty',0)
        bonus_str = f"{total_bonus:+d}" if total_bonus != 0 else "-"
        lines.append(f"  {i+1:<5} {short_code:<10} {r['name']:<8} {r['final_score']:>6.1f} "
                     f"{r['alpha5']:>+6.1f}% {r['alpha20']:>+5.1f}% "
                     f"{r.get('alpha_accel2_5_10_20', 0):>+5.1f} "
                     f"{r.get('breakout_quality', 0):>4.0f} "
                     f"{r['trend_quality']:>5.1f} {r['capital']:>5.1f} "
                     f"{r['fundamental']:>5.1f} {bonus_str:>5} {r['signal']:>12}")
    
    signal_counts = df['signal'].value_counts()
    lines.append(f"  {'-'*92}")
    sig_parts = []
    for s in ['CORE_ALPHA', 'STRONG', 'WATCH', 'AVOID']:
        cnt = signal_counts.get(s, 0)
        if cnt > 0: sig_parts.append(f"{s}:{cnt}")
    lines.append(f"  Signals: {' | '.join(sig_parts)}")
    
    core = df[df['signal'] == 'CORE_ALPHA']
    strong = df[df['signal'] == 'STRONG']
    if len(core) > 0:
        lines.append(f"  CORE_ALPHA: {'、'.join(core.head(3)['name'].tolist())}")
    if len(strong) > 0:
        lines.append(f"  STRONG: {'、'.join(strong.head(3)['name'].tolist())}")
    
    # TOP3 亮点
    lines.append(f"")
    lines.append(f"  ★ TOP3 推荐买入:")
    for i, (_, r) in enumerate(df.head(3).iterrows()):
        short_code = r['code'].replace('.SZ', '').replace('.SH', '')
        tags = []
        if r.get('breakout_bonus',0) > 0: tags.append("突破")
        if r.get('spring_bonus',0) > 0: tags.append("弹簧")
        if r.get('leader_bonus',0) > 0: tags.append("龙头")
        if r.get('crowding_penalty',0) < 0: tags.append("拥挤")
        tag_str = f" [{','.join(tags)}]" if tags else ""
        bq = r.get('breakout_quality', 0)
        accel2 = r.get('alpha_accel2_5_10_20', 0)
        lines.append(f"    {i+1}. {r['name']}({short_code}) Score={r['final_score']:.1f} "
                     f"Alpha5={r['alpha5']:+.1f}% Accel2={accel2:+.1f} BQ={bq:.0f}{tag_str}")
    
    # ── CSV Output ──
    csv_dir = os.path.join(CACHE_DIR, "etf_alpha_ranking")
    os.makedirs(csv_dir, exist_ok=True)
    safe_etf_name = top_etf_name.replace('/', '_').replace('\\', '_')
    csv_path = os.path.join(csv_dir, f"{safe_etf_name}_{trade_date}.csv")
    
    csv_df = df[['alpha_score', 'code', 'name', 'final_score',
                  'alpha5', 'alpha10', 'alpha20', 'alpha40', 'alpha60',
                  'alpha_accel_5_20', 'alpha_accel_10_40', 'alpha_accel_20_60', 'alpha_accel2_5_10_20',
                  'relative_alpha', 'trend_quality', 'capital', 'fundamental',
                  'leader_bonus', 'breakout_bonus', 'spring_bonus', 'crowding_penalty',
                  'dist_to_high', 'vol_contraction', 'amt_accel_short', 'breakout_quality',
                  'signal', 'expected_hold_days']].copy()
    csv_df.columns = ['AlphaBase', 'StockCode', 'StockName', 'FinalScore',
                       'Alpha5', 'Alpha10', 'Alpha20', 'Alpha40', 'Alpha60',
                       'Accel5_20', 'Accel10_40', 'Accel20_60', 'Accel2_5_10_20',
                       'RelAlphaScore', 'TrendScore', 'CapitalScore', 'PositionScore',
                       'LeaderBonus', 'BreakoutBonus', 'SpringBonus', 'CrowdingPenalty',
                       'DistToHigh', 'VolContraction', 'AmtAccelShort', 'BreakoutQuality',
                       'Signal', 'ExpectedHoldDays']
    csv_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    lines.append(f"\n  CSV Report: {csv_path}")

    return "\n".join(lines), csv_path, df


def main(trade_date=None, backtest_mode=False):
    global TRADE_DATE
    TRADE_DATE = get_last_trade_date(trade_date)
    
    today = datetime.datetime.strptime(TRADE_DATE, "%Y%m%d")
    result_message = ""

    print("=" * 60)
    if backtest_mode:
        print(f"  ETF主线轮动策略 Tushare版 (回溯模式)")
        print(f"  回溯日期: {TRADE_DATE}")
    else:
        print(f"  ETF主线轮动策略 Tushare版 (多因子动量评分)")
    print("=" * 60)

    result_message += f"  ETF主线轮动策略(多因子动量评分)\n"
    result_message += f"  因子权重: 动量40% + 量能25% + 风险调整20% + 相对强弱15%\n\n"

    codes_ts = {}
    for name, code in ETF_POOL.items():
        if code.startswith("5") or code.startswith("6"):
            codes_ts[code] = f"{code}.SH"
        else:
            codes_ts[code] = f"{code}.SZ"

    print("  正在获取Tushare数据...")
    all_data = {}
    for name, code in ETF_POOL.items():
        ts_code = codes_ts[code]
        cache_file = _cache_key_fund(ts_code, TRADE_DATE)
        df = _read_cache(cache_file)
        # 旧缓存可能缺少vol列,需要重新请求
        if df is not None and 'vol' not in df.columns:
            df = None
        if df is None:
            try:
                df = pro.fund_daily(ts_code=ts_code,
                                    start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                    end_date=TRADE_DATE,
                                    fields="ts_code,trade_date,open,close,high,low,vol,amount")
                _save_cache(df, cache_file)
                time.sleep(0.25)
            except Exception as e:
                print(f"  [WARN] {name}({ts_code}) error: {e}")
                time.sleep(0.5)
                continue
        if df is not None and len(df) > 0:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            df = df[df["trade_date"] <= today].reset_index(drop=True)
            all_data[code] = df

    # === 获取ETF份额规模数据（资金净申购/赎回流向） ===
    print("  正在获取ETF份额规模数据...")
    share_data = {}
    for name, code in ETF_POOL.items():
        ts_code = codes_ts[code]
        share_cache = _cache_key_share(ts_code, TRADE_DATE)
        sdf = _read_cache(share_cache)
        if sdf is None:
            try:
                sdf = pro.etf_share_size(ts_code=ts_code,
                                         start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                         end_date=TRADE_DATE)
                _save_cache(sdf, share_cache)
                time.sleep(0.25)
            except Exception as e:
                continue
        if sdf is not None and len(sdf) > 0 and 'total_share' in sdf.columns:
            sdf["trade_date"] = pd.to_datetime(sdf["trade_date"], format="%Y%m%d")
            sdf = sdf.sort_values("trade_date").reset_index(drop=True)
            share_data[code] = sdf

    bm_ts = "510300.SH"
    cache_file = _cache_key_fund(bm_ts, TRADE_DATE)
    benchmark_df = _read_cache(cache_file)
    if benchmark_df is not None and 'vol' not in benchmark_df.columns:
        benchmark_df = None
    if benchmark_df is None:
        try:
            benchmark_df = pro.fund_daily(ts_code=bm_ts,
                                           start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                           end_date=TRADE_DATE,
                                           fields="ts_code,trade_date,open,close,high,low,vol,amount")
            _save_cache(benchmark_df, cache_file)
        except Exception as e:
            print(f"  [WARN] 沪深300数据获取失败: {e}")
    if benchmark_df is not None and len(benchmark_df) > 0:
        benchmark_df["trade_date"] = pd.to_datetime(benchmark_df["trade_date"], format="%Y%m%d")
        benchmark_df = benchmark_df.sort_values("trade_date").reset_index(drop=True)
        benchmark_df = benchmark_df[benchmark_df["trade_date"] <= today].reset_index(drop=True)
    else:
        benchmark_df = None

    skipped = [name for name, code in ETF_POOL.items() if code not in all_data]
    if skipped:
        print(f"  [WARN] 缺失数据: {', '.join(skipped)}")

    max_date = max(df["trade_date"].max() for df in all_data.values())
    gap = (today - max_date).days
    print(f"  数据截止: {max_date.strftime('%Y-%m-%d')} (距今{gap}天)")

    code_to_name = {v: k for k, v in ETF_POOL.items()}

    rankings = []
    for code, df in all_data.items():
        bm_for_etf = benchmark_df if benchmark_df is not None else None
        share_df = share_data.get(code)
        factors = calculate_multi_factor_score(df, bm_for_etf, MOM_PERIOD, share_df=share_df)
        if factors is None:
            continue

        latest = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) >= 2 else latest
        day_chg = (latest - prev) / prev * 100

        rankings.append({
            "code": code,
            "name": code_to_name.get(code, code),
            "close": latest,
            "day_chg": round(day_chg, 2),
            **factors
        })

    rankings.sort(key=lambda x: x['total_score'], reverse=True)

    print(f"\n  --- 多因子综合评分 TOP 10 ---")
    print(f"  {'序号':>2} {'名称':<8} {'代码':<8} {'综合分':>6} {'动量':>7} {'量能':>6} {'份额':>6} {'风险':>6} {'相对':>6}")
    print(f"  {'-'*65}")

    for i, r in enumerate(rankings[:10]):
        print(f"  {i+1:>2}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f} "
              f"{r['momentum']:>+7.2f}% {r['vol_score']:>6.1f} {r['share_score']:>6.1f} {r['risk_adj']:>6.1f} {r['rel_strength']:>6.1f}")

    result_message += f"  ---多因子评分 TOP 5 ---\n"
    for i, r in enumerate(rankings[:5]):
        result_message += f"  {i+1}. {r['name']}({r['code']}) 综合分:{r['total_score']:.1f} 动量:{r['momentum']:+.2f}% 份额:{r['share_score']:.0f}\n"

    def count_trade_days(start_str, end_date):
        ref = all_data.get("512880")
        if ref is None:
            return 0
        start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d")
        mask = (ref["trade_date"] > start_dt) & (ref["trade_date"] <= end_date)
        return len(ref[mask])

    state = load_state()
    need_rebalance = False
    days_since = 0
    rebalance_reason = ""

    if state is None:
        need_rebalance = True
        rebalance_reason = "首次运行初始化"
        print(f"\n  [首次运行] 初始化策略...")
    else:
        days_since = count_trade_days(state["last_rebalance_date"], today)
        print(f"\n  当前持仓: {state['holding_name']} ({state['holding_code']})")
        result_message += f"\n**当前持仓:{state['holding_name']} ({state['holding_code']})**\n"

        print(f"  买入日期: {state['last_rebalance_date']}")
        result_message += f"买入日期 {state['last_rebalance_date']}\n"

        print(f"  买入价格: {state['buy_price']:.3f}")
        result_message += f"买入价格 {state['buy_price']:.3f}\n"

        print(f"  已过交易日: {days_since}/{REBAL_DAYS}")
        result_message += f"已过交易日 {days_since}/{REBAL_DAYS}\n"

        hc = state.get("holding_code")
        if hc and hc in all_data:
            latest = all_data[hc]["close"].iloc[-1]
            pnl = (latest - state["buy_price"]) / state["buy_price"] * 100
            print(f"  当前价格: {latest:.3f}  持仓收益: {pnl:+.2f}%")
            result_message += f"  持仓收益 {pnl:+.2f}%"

        # === 调仓触发条件1: 固定周期到期 ===
        if days_since >= REBAL_DAYS:
            need_rebalance = True
            rebalance_reason = f"固定周期到期({days_since}>={REBAL_DAYS}天)"

        # === 调仓触发条件2: 动态退出 (跌出TOP5 + 持仓满5天) ===
        elif days_since >= MIN_HOLD_DAYS:
            top_n_codes = {r['code'] for r in rankings[:DYNAMIC_EXIT_TOPN]}
            if hc not in top_n_codes:
                # 找到当前持仓在排名中的位置
                hold_rank = next((i+1 for i, r in enumerate(rankings) if r['code'] == hc), len(rankings))
                need_rebalance = True
                rebalance_reason = f"跌出TOP{DYNAMIC_EXIT_TOPN}(当前第{hold_rank}名, 持仓{days_since}天)"

        # === 提示: 持仓不满5天但已跌出TOP5 ===
        elif days_since < MIN_HOLD_DAYS:
            top_n_codes = {r['code'] for r in rankings[:DYNAMIC_EXIT_TOPN]}
            if hc not in top_n_codes:
                hold_rank = next((i+1 for i, r in enumerate(rankings) if r['code'] == hc), len(rankings))
                print(f"  [提示] 已跌出TOP{DYNAMIC_EXIT_TOPN}(第{hold_rank}名), 但持仓仅{days_since}天<{MIN_HOLD_DAYS}天保护期, 暂不调仓")
                result_message += f"\n[保护期] 跌出TOP{DYNAMIC_EXIT_TOPN}但持仓{days_since}天<{MIN_HOLD_DAYS}天, 暂不调仓\n"

    if need_rebalance:
        target = rankings[0]
        print(f"\n  {'='*40}")
        result_message += f"{'='*40}\n"

        print(f"  [调仓信号] 需要调仓! 原因: {rebalance_reason}")
        result_message += f"[调仓信号] 需要调仓! 原因: {rebalance_reason}\n"

        print(f"  目标: {target['name']} ({target['code']})")
        result_message += f"目标 {target['name']} ({target['code']})\n"

        print(f"  综合评分: {target['total_score']:.1f}")
        result_message += f"综合评分 {target['total_score']:.1f}\n"

        print(f"  动量: {target['momentum']:+.2f}% | 量能: {target['vol_score']:.1f} | 风险调整: {target['risk_adj']:.1f}")
        result_message += f"动量:{target['momentum']:+.2f}% 量能:{target['vol_score']:.1f} 风险调整:{target['risk_adj']:.1f}\n"

        print(f"  现价: {target['close']:.3f}")
        result_message += f"现价 {target['close']:.3f}\n"

        if state and state.get("holding_code"):
            old = state["holding_code"]
            if old in all_data:
                old_close = all_data[old]["close"].iloc[-1]
                old_pnl = (old_close - state["buy_price"]) / state["buy_price"] * 100
                print(f"  卖出: {state['holding_name']}  收益: {old_pnl:+.2f}%")
                result_message += f"卖出 {state['holding_name']}  收益 {old_pnl:+.2f}%\n"

        new_state = {
            "last_rebalance_date": max_date.strftime("%Y-%m-%d"),
            "holding_code": target["code"],
            "holding_name": target["name"],
            "buy_price": target["close"],
            "score_at_buy": target['total_score'],
            "momentum_at_buy": target['momentum'],
            "rebalance_count": (state.get("rebalance_count", 0) + 1) if state else 1,
        }
        if not backtest_mode:
            save_state(new_state)
            result_message += f"状态已更新! 累计第{new_state['rebalance_count']}次调仓\n"
            print(f"状态已更新! 累计第{new_state['rebalance_count']}次调仓")
        else:
            result_message += f"[回溯模式] 状态未保存\n"
            print(f"[回溯模式] 状态未保存")
    else:
        remain = REBAL_DAYS - days_since
        print(f"\n  距下次调仓还有 {remain} 个交易日")
        result_message += f"\n  距下次调仓还有 {remain} 个交易日\n"

        next_top = rankings[0]
        if next_top["code"] != state.get("holding_code"):
            result_message += f"[提示] 当前评分第一: {next_top['name']} ({next_top['total_score']:.1f})\n"
            print(f"  [提示] 当前评分第一: {next_top['name']} ({next_top['total_score']:.1f})")
            print(f"  与持仓不同，下次调仓将切换")
            result_message += f"与持仓不同，下次调仓将切换\n"

    print(f"\n  --- 评分垫底 5 ---")
    for i, r in enumerate(rankings[-5:]):
        print(f"  {len(rankings)-4+i:>2}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f}")

    # ========== TOP3 ETF 成份股 Alpha Ranking (带纯度过滤) ==========
    if rankings:
        top3_count = min(3, len(rankings))
        print(f"\n  {'='*60}")
        print(f"  Top{top3_count} ETF 成份股分析 (含申万行业纯度过滤)")
        print(f"  {'='*60}")
        for idx in range(top3_count):
            top_etf = rankings[idx]
            top_etf_code = top_etf['code']
            top_etf_name = top_etf['name']
            top_etf_ts_code = codes_ts.get(top_etf_code,
                f"{top_etf_code}.SZ" if top_etf_code.startswith('1')
                else f"{top_etf_code}.SH")
            
            constituents = get_etf_constituents(top_etf_ts_code, TRADE_DATE)
            if not constituents:
                print(f"\n  [第{idx+1}名] {top_etf_name}({top_etf_code}) - 无法获取成份股")
                continue

            # === 纯度因子过滤 ===
            filtered_cons, removed_cons, purity_ratio = filter_by_purity(
                constituents, top_etf_name, min_weight=2.0
            )

            print(f"\n  【第{idx+1}名】 {top_etf_name}({top_etf_code}) 综合分:{top_etf['total_score']:.1f}")
            print(f"  [纯度过滤] 成份股 {len(constituents)} → {len(filtered_cons)} 只 "
                  f"(纯度比 {purity_ratio*100:.1f}%)")
            if removed_cons:
                removed_names = [f"{r.get('con_name','?')}({r.get('l1_name', r.get('l2_name','?'))})" for r in removed_cons[:5]]
                print(f"  [过滤剔除] {', '.join(removed_names)}{'...' if len(removed_cons)>5 else ''}")
                result_message += f"\n[纯度过滤] 剔除{len(removed_cons)}只非纯度股: {', '.join(removed_names[:3])}\n"

            etf_df = all_data.get(top_etf_code)
            if etf_df is not None and filtered_cons:
                alpha_text, csv_path, df_ranked = stock_alpha_ranking(
                    filtered_cons, top_etf_name, today, pro, etf_df, TRADE_DATE
                )
                print(alpha_text)
                if df_ranked is not None and len(df_ranked) > 0:
                    result_message += f"\n**第{idx+1}名 {top_etf_name} Alpha TOP3**\n"
                    for i, (_, r) in enumerate(df_ranked.head(3).iterrows()):
                        short_code = r['code'].replace('.SZ', '').replace('.SH', '')
                        tags = []
                        if r.get('breakout_bonus',0) > 0: tags.append("突破")
                        if r.get('spring_bonus',0) > 0: tags.append("弹簧")
                        if r.get('leader_bonus',0) > 0: tags.append("龙头")
                        tag_str = f"[{','.join(tags)}]" if tags else ""
                        result_message += (f"{i+1}. {r['name']}({short_code}) "
                                           f"Score={r['final_score']:.1f} "
                                           f"Alpha5={r['alpha5']:+.1f}% {tag_str}\n")
            elif not filtered_cons:
                print(f"  [WARN] 纯度过滤后无可用成份股")
                result_message += f"\n[WARN] {top_etf_name} 过滤后无可用股\n"
            else:
                print(f"  [WARN] 无法获取{top_etf_name}的ETF日线数据")
                result_message += f"  [WARN] 无法获取{top_etf_name}的ETF日线数据\n"

    print(f"\n  {'='*60}")

    # ========== 保存微信汇总报告 ==========
    report_dir = os.path.join(os.path.dirname(__file__), '..', 'report_daily')
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"etf_mainline_summary_{TRADE_DATE}.txt")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(result_message)
        print(f"\n  汇总报告: {report_path}")
    except Exception as e:
        print(f"\n  [WARN] 汇总报告保存失败: {e}")

    if not backtest_mode:
        send_wechat(
            result_message.replace("\n", "\n\n"),
            os.getenv("WECHAT_SCKEY")
        )
        send_pushplus(result_message, os.getenv("PUSHPLUS"))
    else:
        print("  [回溯模式] 跳过微信推送")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='ETF主线轮动策略 Tushare版')
    parser.add_argument('--date', type=str, default=None, 
                        help='指定回溯日期(YYYYMMDD格式)，如 --date 20260713')
    parser.add_argument('--backtest', action='store_true', default=False,
                        help='启用回溯模式，不发送通知、不更新状态文件')
    args = parser.parse_args()
    
    main(trade_date=args.date, backtest_mode=args.backtest)
