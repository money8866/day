"""
ETF主线轮动策略 - Tushare版 (收盘后运行)
策略: 多因子动量评分 (动量+量能+波动率+相对强弱)
ETF池: 37只行业ETF (全验证)
数据源: Tushare API
"""
from dotenv import load_dotenv
import os, datetime, pandas as pd, numpy as np, json, time
# 定位到项目根目录 d:\mystock，确保 .env 和 cache_daily 路径不变
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
WECHAT_KEY = os.getenv("WECHAT_KEY")
import tushare as ts
import requests
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

STATE_FILE = os.path.join(_BASE_DIR, "etf_mainline_state_tushare.json")
MOM_PERIOD = 20
REBAL_DAYS = 60
TOP_N = 1

# ──────────────────────────────────────────
# 缓存配置（复用 cache_daily 目录）
# ──────────────────────────────────────────
CACHE_DIR = r"D:\mystock\cache_daily"
ETF_FUND_CACHE_DIR = os.path.join(CACHE_DIR, "etf_fund")
ETF_CONS_CACHE_DIR = os.path.join(CACHE_DIR, "etf_cons")
os.makedirs(ETF_FUND_CACHE_DIR, exist_ok=True)
os.makedirs(ETF_CONS_CACHE_DIR, exist_ok=True)

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

def _cache_key_fund(ts_code):
    """ETF基金净值缓存key: 基于TRADE_DATE确保当天最新"""
    return os.path.join(ETF_FUND_CACHE_DIR, f"{ts_code}_{TRADE_DATE}.csv")

def _cache_key_cons(ts_code):
    """ETF成份股缓存key"""
    safe_name = ts_code.replace('.', '_')
    return os.path.join(ETF_CONS_CACHE_DIR, f"{safe_name}_{TRADE_DATE}.csv")

def _cache_key_stock(ts_code):
    """个股日线缓存key"""
    safe_name = ts_code.replace('.', '_')
    return os.path.join(ETF_CONS_CACHE_DIR, f"stock_{safe_name}_{TRADE_DATE}.csv")

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
    '黄金': '518880', '沪深300': '510300', '创业板': '159915',
    '上证50': '510050','双创ETF':'588300','科创ETF':'588050',
    '工业母机': '159667',
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


def get_last_trade_date():
    now = datetime.datetime.now()
    if now.hour < 15:
        query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)


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


def get_etf_constituents(ts_code):
    """
    获取ETF成份股列表
    根据代码前缀选择接口：1开头→深圳 etf_sz_cons，5/6开头→上海 etf_sh_cons
    返回：[(con_code, con_name, qty, cpr), ...]
    """
    # 优先读缓存
    cache_file = _cache_key_cons(ts_code)
    cached = _read_cache(cache_file)
    if cached is not None:
        result = []
        for _, row in cached.iterrows():
            result.append({
                'con_code': row.get('con_code', ''),
                'con_name': row.get('con_name', ''),
                'qty': row.get('qty', 0),
                'cpr': row.get('cpr', 0),
            })
        return result

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
        
        # 只保留最近一个交易日的成份股列表
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        _save_cache(df, cache_file)
        
        result = []
        for _, row in df.iterrows():
            result.append({
                'con_code': row.get('con_code', ''),
                'con_name': row.get('con_name', ''),
                'qty': row.get('qty', 0),
                'cpr': row.get('cpr', 0),
            })
        return result
    except Exception as e:
        print(f"  [WARN] 获取{ts_code}成份股失败: {e}")
        return []


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


def calculate_multi_factor_score(df, benchmark_df, mom_period=20):
    """
    计算多因子综合评分
    因子1: 20日动量 (40%)
    因子2: 量能配合 (25%) - 近期量能是否放大
    因子3: 风险调整收益 (20%) - 动量/波动率
    因子4: 相对强弱 (15%) - 相对于沪深300的表现
    """
    if len(df) < mom_period + 5:
        return None

    close = df['close']

    mom_20d = close.pct_change(mom_period).iloc[-1] * 100

    vol = df.get('vol', None)
    if vol is None or len(vol) < mom_period:
        vol_score = 50
    else:
        recent_vol_avg = vol.tail(5).mean()
        hist_vol_avg = vol.tail(mom_period).mean()
        vol_ratio = recent_vol_avg / (hist_vol_avg + 1e-6)
        vol_score = min(vol_ratio * 50, 100)

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

    total_score = (
        mom_20d * 0.40 +
        vol_score * 0.25 +
        risk_adj_score * 0.20 +
        rel_score * 0.15
    )

    return {
        'momentum': round(mom_20d, 2),
        'vol_score': round(vol_score, 2),
        'risk_adj': round(risk_adj_score, 2),
        'rel_strength': round(rel_score, 2),
        'total_score': round(total_score, 2)
    }


def analyze_constituent_rotation(constituents, top_etf_name, today, pro, benchmark_df, mom_period=20):
    """
    最强ETF成份股轮动分析 + 操作建议
    对每只成份股计算多因子评分 + 阶段分类 + 操作建议
    
    阶段分类:
    - 🔥 主升浪: 排名前25% + 距前高<5% + 量比>1.2
    - 🚀 启动:   排名上升 + 距前高>5% + 放量
    - 📈 补涨:   排名上升 + 距前高>10% + 前期弱势
    - ⚠️ 过热:   排名前10% + 距前高<3% + 量比>2.0
    
    操作建议:
    - 🟢买入: 主升浪/启动 + 有空间 + 放量
    - 🟡关注: 补涨 + 排名上升
    - 其他: 过滤不显示
    
    Returns:
        (console_text, md_text, top3_list) — 控制台文本, markdown文本, 可操作前3列表
    """
    lines = []   # 控制台输出
    md_lines = []  # markdown输出（微信推送用）
    
    lines.append(f"\n  --- {top_etf_name} 成份股轮动分析 ---")
    md_lines.append(f"\n**{top_etf_name} 成份股轮动分析**")
    
    stock_results = []
    
    def _ema(arr, period):
        """计算指数移动平均"""
        if len(arr) < period:
            return np.full_like(arr, np.nan)
        result = np.full_like(arr, np.nan)
        result[period - 1] = np.mean(arr[:period])
        k = 2 / (period + 1)
        for i in range(period, len(arr)):
            result[i] = arr[i] * k + result[i - 1] * (1 - k)
        return result
    
    # ... data fetching unchanged ...
    
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
            cache_file = _cache_key_stock(con_ts_code)
            con_df = _read_cache(cache_file)
            if con_df is None:
                con_df = pro.daily(ts_code=con_ts_code,
                                   start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                   fields="ts_code,trade_date,open,close,low,vol")
                _save_cache(con_df, cache_file)
                time.sleep(0.1)
            
            if con_df is None or len(con_df) < mom_period + 10:
                continue
            
            con_df = con_df.copy()
            con_df["trade_date"] = pd.to_datetime(con_df["trade_date"], format="%Y%m%d")
            con_df = con_df.sort_values("trade_date").reset_index(drop=True)
            
            factors = calculate_multi_factor_score(con_df, benchmark_df, mom_period)
            if factors is None:
                continue
            
            close_arr = con_df['close'].values
            vol_arr = con_df['vol'].values
            
            mom5 = (close_arr[-1] / close_arr[-6] - 1) * 100 if len(close_arr) >= 6 else 0
            mom20 = (close_arr[-1] / close_arr[-21] - 1) * 100 if len(close_arr) >= 21 else 0
            high_60 = close_arr[-60:].max() if len(close_arr) >= 60 else close_arr.max()
            dist_to_high = (close_arr[-1] / high_60 - 1) * 100
            vol_5 = vol_arr[-5:].mean()
            vol_20 = vol_arr[-20:].mean()
            vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0
            
            # 技术形态指标（简化版）
            open_arr = con_df['open'].values if 'open' in con_df.columns else None
            low_arr = con_df['low'].values if 'low' in con_df.columns else None
            pct_chg = (close_arr[-1] / close_arr[-2] - 1) * 100 if len(close_arr) >= 2 else 0
            ma5_val = np.mean(close_arr[-5:]) if len(close_arr) >= 5 else close_arr[-1]
            ma10_val = np.mean(close_arr[-10:]) if len(close_arr) >= 10 else close_arr[-1]
            ma20_val = np.mean(close_arr[-20:]) if len(close_arr) >= 20 else close_arr[-1]
            ma60_val = np.mean(close_arr[-60:]) if len(close_arr) >= 60 else close_arr[-1]
            above_ma60_pct = (close_arr[-1] / ma60_val - 1) * 100 if ma60_val > 0 else 0
            
            # 前日收盘价低于MA10或MA20（弱转强判定用）
            pre_close = close_arr[-2] if len(close_arr) >= 2 else close_arr[-1]
            pre_below_ma10 = pre_close < ma10_val
            pre_below_ma20 = pre_close < ma20_val
            pre_close_below = pre_below_ma10 or pre_below_ma20
            
            # 最低点低于MA5
            low_below_ma5 = low_arr is not None and low_arr[-1] < ma5_val
            
            # MA5上升（当前MA5 > 3天前MA5）
            ma5_3ago = np.mean(close_arr[-8:-3]) if len(close_arr) >= 8 else ma5_val
            ma5_rising = ma5_val > ma5_3ago
            
            # MACD计算：DIF 3日内由负转正
            ema12 = _ema(close_arr, 12)
            ema26 = _ema(close_arr, 26)
            dif_arr = ema12 - ema26
            dif_now = dif_arr[-1] if not np.isnan(dif_arr[-1]) else 0
            dif_3ago = dif_arr[-4] if len(dif_arr) >= 4 and not np.isnan(dif_arr[-4]) else 0
            macd_cross_3d = dif_3ago < 0 and dif_now > 0
            
            stock_results.append({
                'code': con_code,
                'name': con_name,
                'total_score': factors['total_score'],
                'mom5': round(mom5, 2),
                'mom20': round(mom20, 2),
                'dist_to_high': round(dist_to_high, 2),
                'vol_ratio': round(vol_ratio, 2),
                'pct_chg': round(pct_chg, 2),
                'above_ma60_pct': round(above_ma60_pct, 2),
                'pre_close_below': pre_close_below,
                'low_below_ma5': low_below_ma5,
                'ma5_rising': ma5_rising,
                'macd_cross_3d': macd_cross_3d,
                'factors': factors,
            })
        except Exception:
            pass
    
    if len(stock_results) < 5:
        msg = f"  成份股有效数据不足({len(stock_results)}只)，无法分析"
        lines.append(msg)
        md_lines.append(msg)
        return "\n".join(lines), "\n".join(md_lines), []
    
    # === 排名计算 ===
    df = pd.DataFrame(stock_results)
    df['mom5_rank'] = df['mom5'].rank(ascending=False, pct=True)
    df['mom20_rank'] = df['mom20'].rank(ascending=False, pct=True)
    df['rank_change'] = df['mom20_rank'] - df['mom5_rank']
    
    # 涨幅排名（绝对排名，非百分比）
    df['pct_rank_abs'] = df['pct_chg'].rank(ascending=False)  # 1=涨幅最大
    total_stocks = len(df)
    df['pct_top3'] = df['pct_rank_abs'] <= 3  # 涨幅前3
    
    # === 启动检测（简化版）===
    def detect_technical_launch(row):
        """
        启动条件（三要素）：
        1. 最低点低于MA5（盘中洗盘至均线附近）
        2. 当日超过5%的中阳
        3. MA5上升 或 MACD的DIF 3日内由负转正
        """
        c1 = row['low_below_ma5']
        c2 = row['pct_chg'] > 5.0
        c3 = row['ma5_rising'] or row['macd_cross_3d']
        
        if c1 and c2 and c3:
            reasons = []
            if c1: reasons.append('破MA5')
            if c2: reasons.append(f"+{row['pct_chg']:.0f}%")
            if row['ma5_rising']: reasons.append('MA5升')
            if row['macd_cross_3d']: reasons.append('MACD转正')
            return True, ' '.join(reasons)
        return False, ''
    
    # === 阶段分类 ===
    def classify_stage(row):
        # 弱转强：前日收盘低于MA10或MA20 + 当日涨幅排前3
        if row['pre_close_below'] and row['pct_top3']:
            return '💪弱转强'
        
        # 启动信号
        is_launch, launch_reason = detect_technical_launch(row)
        if is_launch:
            return '🚀启动'
        
        # 🔥主升浪：乖离率已拉开，趋势明确
        if (row['mom5_rank'] <= 0.25 and row['dist_to_high'] > -8 and 
            row['vol_ratio'] > 1.0):
            return '🔥主升浪'
        elif row['rank_change'] > 0.1 and row['dist_to_high'] < -5:
            return '📈补涨'
        elif row['mom5_rank'] <= 0.10 and row['dist_to_high'] > -3 and row['vol_ratio'] > 1.8:
            return '⚠️过热'
        elif row['mom5_rank'] <= 0.25:
            return '🔥主升浪'
        elif row['rank_change'] > 0.05:
            return '📈补涨'
        else:
            return '➡️整理'
    
    df['stage'] = df.apply(classify_stage, axis=1)
    
    # === 操作建议 ===
    def get_action(row):
        stage = row['stage']
        score = row['total_score']
        dist = row['dist_to_high']
        vol_r = row['vol_ratio']
        rc = row['rank_change']
        
        # 弱转强：直接买入
        if stage == '💪弱转强':
            return '🟢买入'
        # 启动信号：刚突破，低门槛买入
        if stage == '🚀启动' and score >= 50 and dist > -15:
            return '🟢买入'
        if stage == '🔥主升浪' and score >= 60 and dist > -10 and vol_r > 1.0:
            return '🟢买入'
        if stage == '📈补涨' and rc > 0.05 and dist > -20:
            return '🟡关注'
        if stage == '⚠️过热':
            return '🔴回避'
        if rc < -0.2:
            return '🔴回避'
        if vol_r < 0.7:
            return '🔴回避'
        if stage == '➡️整理' and score >= 65 and dist > -12 and vol_r > 1.0:
            return '🟡关注'
        return '⚪观望'
    
    df['action'] = df.apply(get_action, axis=1)
    
    action_order = {'🟢买入': 0, '🟡关注': 1, '⚪观望': 2, '🔴回避': 3}
    df['action_order'] = df['action'].map(action_order)
    stage_order = {'💪弱转强': 0, '🚀启动': 1, '🔥主升浪': 2, '📈补涨': 3, '⚠️过热': 4, '➡️整理': 5}
    df['stage_order'] = df['stage'].map(stage_order)
    df = df.sort_values(['action_order', 'total_score'], ascending=[True, False]).reset_index(drop=True)
    
    # === 可操作列表 ===
    actionable = df[df['action'].isin(['🟢买入', '🟡关注'])].copy()
    
    if actionable.empty:
        msg = "  当前无符合条件的可操作标的"
        lines.append(msg)
        md_lines.append(msg)
        return "\n".join(lines), "\n".join(md_lines), []
    
    # --- 控制台输出（表格）---
    lines.append(f"  {'代码':<10} {'名称':<8} {'综合分':>5} {'建议':>6} {'阶段':>6} {'排名变化':>6} {'距前高%':>6} {'量比':>5} {'5日%':>5}")
    lines.append(f"  {'-'*66}")
    top3_list = []
    for i, (_, r) in enumerate(actionable.iterrows()):
        rank_change_str = f"+{r['rank_change']:.0%}" if r['rank_change'] > 0 else f"{r['rank_change']:.0%}"
        line = (f"  {r['code']:<10} {r['name']:<8} {r['total_score']:>5.1f} "
                f"{r['action']:>6} {r['stage']:>6} {rank_change_str:>6} {r['dist_to_high']:>+5.1f}% "
                f"{r['vol_ratio']:>4.1f} {r['mom5']:>+4.1f}%")
        lines.append(line)
        if i < 3:
            top3_list.append({
                'code': r['code'], 'name': r['name'],
                'total_score': r['total_score'], 'action': r['action'],
                'stage': r['stage'], 'mom5': r['mom5'],
            })
    
    # 控制台汇总
    lines.append(f"  {'-'*66}")
    buy = actionable[actionable['action'] == '🟢买入']
    watch = actionable[actionable['action'] == '🟡关注']
    summary_parts = []
    if len(buy) > 0: summary_parts.append(f"🟢买入{len(buy)}只")
    if len(watch) > 0: summary_parts.append(f"🟡关注{len(watch)}只")
    lines.append(f"  可操作: {' | '.join(summary_parts)}")
    
    stage_counts = df['stage'].value_counts()
    stage_parts = []
    for s in ['💪弱转强', '🚀启动', '🔥主升浪', '📈补涨', '⚠️过热', '➡️整理']:
        cnt = stage_counts.get(s, 0)
        if cnt > 0: stage_parts.append(f"{s}{cnt}只")
    lines.append(f"  全貌: {' | '.join(stage_parts)}")
    
    top_stages = actionable[actionable['stage'].isin(['🔥主升浪', '🚀启动', '📈补涨'])].head(3)
    if len(top_stages) >= 2:
        stage_seq = '→'.join(top_stages['stage'].tolist())
        lines.append(f"  轮动路径: {stage_seq}")
    
    # --- Markdown输出（微信推送用）---
    md_lines.append("")
    for i, (_, r) in enumerate(actionable.iterrows()):
        short_code = r['code'].replace('.SZ', '').replace('.SH', '')
        rc_str = f"+{r['rank_change']:.0%}" if r['rank_change'] > 0 else f"{r['rank_change']:.0%}"
        md_lines.append(f"- **{r['name']}({short_code})** {r['action']} {r['stage']} | 评分:{r['total_score']:.0f} 排名:{rc_str} 距前高:{r['dist_to_high']:+.1f}% 量比:{r['vol_ratio']:.1f}")
    
    md_lines.append("")
    if len(buy) > 0:
        buy_names = '、'.join([f"**{r['name']}**" for _, r in buy.head(3).iterrows()])
        md_lines.append(f"**优先关注**: {buy_names}")
    elif len(watch) > 0:
        watch_names = '、'.join([f"**{r['name']}**" for _, r in watch.head(3).iterrows()])
        md_lines.append(f"**可关注**: {watch_names}")
    
    # 保存轮动结果供外部读取（写入项目根的 cache_daily 目录，路径不变）
    try:
        weak2strong = actionable[actionable['stage'] == '💪弱转强']
        rot_data = {
            'trade_date': str(TRADE_DATE),
            'etf_name': top_etf_name,
            'etf_stage_summary': {s: int(stage_counts.get(s, 0)) for s in ['💪弱转强', '🚀启动', '🔥主升浪', '📈补涨', '⚠️过热', '➡️整理']},
            'actionable': [{'code': r['code'].replace('.SZ','').replace('.SH',''), 'name': r['name'],
                            'action': r['action'], 'stage': r['stage'], 'score': r['total_score'],
                            'pct_chg': r['pct_chg']} for _, r in actionable.iterrows()],
            'weak2strong': [{'code': r['code'].replace('.SZ','').replace('.SH',''), 'name': r['name'],
                             'score': r['total_score'], 'pct_chg': r['pct_chg']} for _, r in weak2strong.iterrows()],
            'top3': top3_list,
        }
        import json as _json
        rot_path = os.path.join(CACHE_DIR, 'etf_rotation_tips.json')
        os.makedirs(os.path.dirname(rot_path), exist_ok=True)
        with open(rot_path, 'w', encoding='utf-8') as f:
            _json.dump(rot_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    
    return "\n".join(lines), "\n".join(md_lines), top3_list


def main():
    today = datetime.datetime.strptime(TRADE_DATE, "%Y%m%d")
    result_message = ""

    print("=" * 60)
    print("  ETF主线轮动策略 Tushare版 (多因子动量评分)")
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
        cache_file = _cache_key_fund(ts_code)
        df = _read_cache(cache_file)
        if df is None:
            try:
                df = pro.fund_daily(ts_code=ts_code,
                                    start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                    fields="ts_code,trade_date,close")
                _save_cache(df, cache_file)
                time.sleep(0.25)
            except Exception as e:
                print(f"  [WARN] {name}({ts_code}) error: {e}")
                time.sleep(0.5)
                continue
        if df is not None and len(df) > 0:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            all_data[code] = df

    bm_ts = "510300.SH"
    cache_file = _cache_key_fund(bm_ts)
    benchmark_df = _read_cache(cache_file)
    if benchmark_df is None:
        try:
            benchmark_df = pro.fund_daily(ts_code=bm_ts,
                                           start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                           fields="ts_code,trade_date,close")
            _save_cache(benchmark_df, cache_file)
        except Exception as e:
            print(f"  [WARN] 沪深300数据获取失败: {e}")
    if benchmark_df is not None and len(benchmark_df) > 0:
        benchmark_df["trade_date"] = pd.to_datetime(benchmark_df["trade_date"], format="%Y%m%d")
        benchmark_df = benchmark_df.sort_values("trade_date").reset_index(drop=True)
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
        factors = calculate_multi_factor_score(df, bm_for_etf, MOM_PERIOD)
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
    print(f"  {'序号':>2} {'名称':<8} {'代码':<8} {'综合分':>6} {'动量':>7} {'量能':>6} {'风险':>6} {'相对':>6}")
    print(f"  {'-'*60}")

    for i, r in enumerate(rankings[:10]):
        print(f"  {i+1:>2}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f} "
              f"{r['momentum']:>+7.2f}% {r['vol_score']:>6.1f} {r['risk_adj']:>6.1f} {r['rel_strength']:>6.1f}")

    result_message += f"  ---多因子评分 TOP 5 ---\n"
    for i, r in enumerate(rankings[:5]):
        result_message += f"  {i+1}. {r['name']}({r['code']}) 综合分:{r['total_score']:.1f} 动量:{r['momentum']:+.2f}%\n"

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

    if state is None:
        need_rebalance = True
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
        if days_since >= REBAL_DAYS:
            need_rebalance = True

    if need_rebalance:
        target = rankings[0]
        print(f"\n  {'='*40}")
        result_message += f"{'='*40}\n"

        print(f"  [调仓信号] 需要调仓!")
        result_message += f"[调仓信号] 需要调仓!\n"

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
        save_state(new_state)
        result_message += f"状态已更新! 累计第{new_state['rebalance_count']}次调仓\n"
        print(f"状态已更新! 累计第{new_state['rebalance_count']}次调仓")
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

    # ========== 最强ETF成份股轮动分析 ==========
    if rankings:
        top_etf = rankings[0]
        top_etf_code = top_etf['code']
        top_etf_name = top_etf['name']
        top_etf_ts_code = codes_ts.get(top_etf_code,
            f"{top_etf_code}.SZ" if top_etf_code.startswith('1')
            else f"{top_etf_code}.SH")
        
        constituents = get_etf_constituents(top_etf_ts_code)
        if constituents:
            rotation_text, rotation_md, top3 = analyze_constituent_rotation(
                constituents, top_etf_name, today, pro, benchmark_df, MOM_PERIOD
            )
            print(rotation_text)
            result_message += rotation_md + "\n"
        else:
            print(f"  [WARN] 无法获取{top_etf_name}成份股")
            result_message += f"  [WARN] 无法获取{top_etf_name}成份股\n"

    print(f"\n  {'='*60}")

    send_wechat(
        result_message.replace("\n", "\n\n"),
        os.getenv("WECHAT_SCKEY")
    )

    send_pushplus(result_message, os.getenv("PUSHPLUS"))


if __name__ == "__main__":
    main()
