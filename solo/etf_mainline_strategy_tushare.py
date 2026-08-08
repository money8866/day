"""
ETF主线轮动策略 - Tushare版 (收盘后运行)
策略: 动量轮动与趋势跟踪组合算法 (最强中线主线)
  选品: RSI + 20日/60日/120日复合动量, 排名Top2~3且站上20日均线
  趋势: EMA(12)/EMA(50)双均线金叉 + MACD零轴上方二次金叉
  离场: 动量排名跌出Top20% 或 跌破30日离场线(MA30)
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
import tushare as ts
import requests
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

STATE_FILE = os.path.join(os.path.dirname(__file__), "etf_mainline_state_tushare.json")
MOM_PERIOD = 20
REBAL_DAYS = 60
TOP_N = 1
DYNAMIC_EXIT_TOP_PCT = 0.20 # 动态退出: 动量排名跌出Top20%则触发调仓
MIN_HOLD_DAYS = 5           # 动态退出保护: 最少持仓5个交易日才允许动态退出

# ──────────────────────────────────────────
# 缓存配置（复用 cache_daily 目录）
# ──────────────────────────────────────────
CACHE_DIR = r"D:\mystock\cache_daily"
ETF_FUND_CACHE_DIR = os.path.join(CACHE_DIR, "etf_fund")
ETF_SHARE_CACHE_DIR = os.path.join(CACHE_DIR, "etf_share")
os.makedirs(ETF_FUND_CACHE_DIR, exist_ok=True)
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


def _cache_key_share(ts_code, trade_date):
    """ETF份额规模缓存key"""
    safe_name = ts_code.replace('.', '_')
    return os.path.join(ETF_SHARE_CACHE_DIR, f"share_{safe_name}_{trade_date}.csv")


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
    '工业母机': '159667', '科创半导体':'588170',
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


def classify_market_state(benchmark_df, mom_period=20):
    """
    分类市场状态: trending(趋势) / oscillating(震荡) / declining(下跌)
    输入: benchmark_df (沪深300日线)
    输出: (state_label, description)
    """
    if benchmark_df is None or len(benchmark_df) < mom_period:
        return 'oscillating', '数据不足'

    close = benchmark_df['close'].values
    ret_20 = (close[-1] / close[-mom_period] - 1) * 100
    ma20 = np.mean(close[-mom_period:])
    above_ma20 = close[-1] > ma20
    ma20_prev = np.mean(close[-mom_period-1:-1]) if len(close) > mom_period else ma20
    ma20_direction = 'up' if ma20 > ma20_prev else 'down'

    if ret_20 > 3 and above_ma20 and ma20_direction == 'up':
        state = 'trending'
        desc = f"趋势上涨(沪深300:{ret_20:+.2f}%,站上MA20)"
    elif ret_20 < -5:
        state = 'declining'
        desc = f"下跌(沪深300:{ret_20:+.2f}%)"
    elif ret_20 < -3 and not above_ma20:
        state = 'declining'
        desc = f"下跌(沪深300:{ret_20:+.2f}%,跌破MA20)"
    else:
        state = 'oscillating'
        desc = f"震荡(沪深300:{ret_20:+.2f}%)"

    return state, desc


def calculate_multi_factor_score(df, benchmark_df, mom_period=20):
    """
    动量轮动 + 趋势跟踪组合评分（简化版）
    ============================================
    核心逻辑（行业ETF中线主线）：
      选品: 复合动量(20日/60日/120日加权) + RSI + 20日均线上方
      趋势: EMA(12)/EMA(50) 双均线金叉 + MACD零轴上方(二次金叉)
      离场: 30日离场线(MA30)标记，供卖出判定

    因子:
      1. 复合动量 mom_weighted (20日0.5 + 60日0.3 + 120日0.2) → 截面排名(调用处)
      2. 动量加速度 mom_accel = 20日 - 60日
      3. RSI(14) 强度
      4. 趋势确认 trend_quality: EMA多头/金叉 + MACD零轴上 + 20日均线上方
      5. 风险调整 risk_adj (回撤惩罚)
      6. 相对强弱 rel_strength (对沪深300)

    注: 截面动量排名分(mom_cross_score)由调用处统一计算后注入
    """
    min_len = max(mom_period, 60) + 5
    if len(df) < min_len:
        return None

    close = df['close']
    n = len(close)

    # === 多窗口复合动量 (20日/60日/120日加权) ===
    mom_20d  = close.pct_change(20).iloc[-1] * 100 if n >= 21 else 0
    mom_60d  = close.pct_change(60).iloc[-1] * 100 if n >= 61 else mom_20d
    mom_120d = close.pct_change(120).iloc[-1] * 100 if n >= 121 else mom_60d
    # 复合动量: 中期为主, 长期为续航确认
    mom_weighted = mom_20d * 0.50 + mom_60d * 0.30 + mom_120d * 0.20

    # === 动量加速度 ===
    mom_accel = mom_20d - mom_60d  # 正=加速, 负=减速

    # === RSI(14) ===
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - 100 / (1 + rs)
    rsi_last = 50.0
    if len(rsi) > 0 and not np.isnan(rsi.iloc[-1]):
        rsi_last = float(rsi.iloc[-1])
    # 映射: RSI 30→0, 70→100 (强度线性)
    rsi_score = max(0, min(100, (rsi_last - 30) / 40 * 100))

    # === 趋势确认: EMA(12)/EMA(50) + MACD + MA20 ===
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()

    ema_bull = ema12.iloc[-1] > ema50.iloc[-1]          # EMA12>EMA50 多头排列
    # 近5日是否发生 EMA 金叉
    golden_cross = False
    for k in range(1, min(6, n)):
        if ema12.iloc[-k] > ema50.iloc[-k] and ema12.iloc[-k-1] <= ema50.iloc[-k-1]:
            golden_cross = True
            break
    macd_above_zero = dif.iloc[-1] > 0                   # DIF 在零轴上方
    # 近10日 MACD 零轴上方二次金叉（DIF 上穿 DEA 且 DIF>0）
    macd_second_golden = False
    for k in range(1, min(11, n)):
        if (dif.iloc[-k] > dea.iloc[-k] and dif.iloc[-k-1] <= dea.iloc[-k-1]
                and dif.iloc[-k] > 0):
            macd_second_golden = True
            break

    ma20 = close.rolling(20).mean()
    ma30 = close.rolling(30).mean()
    above_ma20 = close.iloc[-1] > ma20.iloc[-1]
    above_ma30 = close.iloc[-1] > ma30.iloc[-1]          # 30日离场线

    # 趋势质量分 0-100
    trend_quality = 0.0
    trend_quality += 30 if ema_bull else 0
    trend_quality += 15 if golden_cross else 0
    trend_quality += 25 if above_ma20 else 0
    trend_quality += 15 if macd_above_zero else 0
    trend_quality += 15 if macd_second_golden else 0
    trend_quality = min(100, trend_quality)

    # === 风险调整 (20日回撤惩罚) ===
    close_tail = close.tail(mom_period)
    cummax = close_tail.cummax()
    drawdown = (close_tail - cummax) / cummax
    max_dd = drawdown.min() * 100  # 负值
    risk_adj_score = max(0, min(100, 100 + max_dd * 5))  # 回撤0%→100, -20%→0

    # === 相对强弱 (对沪深300, 20日) ===
    if benchmark_df is not None and len(benchmark_df) >= mom_period + 1:
        bm_return = benchmark_df['close'].pct_change(mom_period).iloc[-1] * 100
        relative_strength = mom_20d - bm_return
        rel_score = max(0, min(100, 50 + relative_strength))
    else:
        rel_score = 50

    # === 量能(简化, 仅保留量比信号) ===
    vol = df.get('vol', None)
    if vol is not None and len(vol) >= mom_period:
        recent_vol = vol.tail(5).mean()
        prev_vol = vol.tail(mom_period).head(mom_period - 5).mean()
        vol_ratio = recent_vol / (prev_vol + 1e-6)
        vol_score = max(0, min(100, 50 + (vol_ratio - 1) * 50))
    else:
        vol_score = 50

    return {
        'momentum': round(mom_20d, 2),       # 显示用(20日原始涨幅)
        'mom_weighted': round(mom_weighted, 2),  # 截面排名输入
        'mom_accel': round(mom_accel, 2),    # 加速度原始值(截面排名输入)
        'accel_score': round(mom_accel, 2),  # 占位(由调用处截面排名覆盖)
        'vol_score': round(vol_score, 2),
        'risk_adj': round(risk_adj_score, 2),
        'rel_strength': round(rel_score, 2),
        'trend_quality': round(trend_quality, 2),
        'rsi': round(rsi_last, 2),
        'rsi_score': round(rsi_score, 2),
        'ema_bull': ema_bull,
        'golden_cross': golden_cross,
        'macd_above_zero': macd_above_zero,
        'macd_second_golden': macd_second_golden,
        'above_ma20': above_ma20,
        'above_ma30': above_ma30,
        'shrink_stability': 50,              # 已废弃, 保留字段兼容
        # 占位: 截面排名分由调用处注入
        'mom_cross_score': None,
        # 综合分待调用处注入 mom_cross_score 后计算
        'total_score': None,
    }


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

    result_message += f"  ETF主线轮动策略(多因子动量评分 A股优化版)\n"
    result_message += f"  因子权重: 动态调节(根据市场状态自适应)\n\n"

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

    # === 获取ETF份额规模数据（用于规模提示） ===
    print("  正在获取ETF份额规模数据...")
    share_data = {}
    for name, code in ETF_POOL.items():
        ts_code = codes_ts[code]
        share_cache = _cache_key_share(ts_code, TRADE_DATE)
        sdf = _read_cache(share_cache)
        if sdf is None:
            try:
                sdf = pro.etf_share_size(ts_code=ts_code,
                                         start_date=(today - datetime.timedelta(days=30)).strftime("%Y%m%d"),
                                         end_date=TRADE_DATE)
                _save_cache(sdf, share_cache)
                time.sleep(0.15)
            except Exception:
                continue
        if sdf is not None and len(sdf) > 0 and 'total_share' in sdf.columns:
            sdf["trade_date"] = pd.to_datetime(sdf["trade_date"], format="%Y%m%d")
            sdf = sdf.sort_values("trade_date").reset_index(drop=True)
            share_data[code] = sdf

    bm_ts = "000300.SH"
    cache_file = os.path.join(ETF_FUND_CACHE_DIR, f"idx_000300_{TRADE_DATE}.csv")
    benchmark_df = _read_cache(cache_file)
    if benchmark_df is not None and 'vol' not in benchmark_df.columns:
        benchmark_df = None
    if benchmark_df is None:
        try:
            benchmark_df = pro.index_daily(ts_code=bm_ts,
                                           start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                           end_date=TRADE_DATE,)
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

    # === 市场状态分类 ===
    market_state, state_desc = classify_market_state(benchmark_df, MOM_PERIOD)
    print(f"  市场状态: {state_desc}")

    # 根据市场状态选择权重矩阵 (简化版: 动量轮动+趋势跟踪)
    # trending(趋势): 复合动量为主, 趋势确认为辅
    # oscillating(震荡): 动量+加速度+RSI 均衡
    # declining(下跌): 风险+相对优先, 偏防御
    WEIGHT_MATRIX = {
        'trending':   {'mom_cross': 0.40, 'accel': 0.10, 'rsi': 0.10, 'vol': 0.10, 'risk': 0.10, 'rel': 0.05, 'trend': 0.15, 'shrink': 0.00},
        'oscillating':{'mom_cross': 0.25, 'accel': 0.20, 'rsi': 0.15, 'vol': 0.10, 'risk': 0.10, 'rel': 0.05, 'trend': 0.15, 'shrink': 0.00},
        'declining':  {'mom_cross': 0.25, 'accel': 0.05, 'rsi': 0.10, 'vol': 0.05, 'risk': 0.25, 'rel': 0.15, 'trend': 0.15, 'shrink': 0.00},
    }
    w = WEIGHT_MATRIX.get(market_state, WEIGHT_MATRIX['oscillating'])
    weight_desc = (f"复合动量{w['mom_cross']:.0%}+加速度{w['accel']:.0%}+RSI{w['rsi']:.0%}+量价{w['vol']:.0%}+"
                   f"风险{w['risk']:.0%}+相对{w['rel']:.0%}+趋势{w['trend']:.0%}")
    result_message += f"  市场状态: {state_desc}\n"
    result_message += f"  当前权重: {weight_desc}\n\n"

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

        # === 份额规模提示 ===
        share_signal = ""
        sdf = share_data.get(code)
        if sdf is not None and len(sdf) >= 2:
            share_latest = sdf['total_share'].iloc[-1]
            share_prev = sdf['total_share'].iloc[0]
            share_chg = (share_latest - share_prev) / share_prev * 100 if share_prev > 0 else 0
            if day_chg < 0 and share_chg > 0:
                share_signal = f"逆势加仓({share_chg:+.1f}%)"
            elif day_chg < 0 and share_chg < 0:
                share_signal = f"减仓中({share_chg:+.1f}%)"

        rankings.append({
            "code": code,
            "name": code_to_name.get(code, code),
            "close": latest,
            "day_chg": round(day_chg, 2),
            "share_signal": share_signal,
            **factors
        })

    # === 截面动量排名 (mom_weighted 升序排名 → 百分位0-100) ===
    # 第1名(最强)=100分, 最后一名≈0分
    valid = [r for r in rankings if r.get('mom_weighted') is not None]
    n_total = len(valid)
    if n_total > 1:
        # 按mom_weighted升序排序, 计算每个ETF的百分位
        sorted_by_mom = sorted(valid, key=lambda x: x['mom_weighted'])
        for i, r in enumerate(sorted_by_mom):
            # 百分位排名: 第1名(最强)取最大值
            rank_pct = (i / (n_total - 1)) * 100  # 0~100
            r['mom_cross_score'] = round(rank_pct, 2)

    # === 截面加速度排名 (mom_accel 升序 → 百分位0-100) ===
    # 修复: 原绝对映射(50+mom_accel*10)在普跌市中 mom_5d-mom_20d 大面积为正且封顶100,
    # 导致 18/35 只ETF加速度同分、完全失去区分度, 下跌ETF借机排第一。
    # 改为截面百分位后, 加速度只在池内比较, 消除饱和失真。
    valid_accel = [r for r in rankings if r.get('mom_accel') is not None]
    n_accel = len(valid_accel)
    if n_accel > 1:
        sorted_by_accel = sorted(valid_accel, key=lambda x: x['mom_accel'])
        for i, r in enumerate(sorted_by_accel):
            rank_pct = (i / (n_accel - 1)) * 100
            r['accel_score'] = round(rank_pct, 2)

    # === 计算综合分 (动态权重 · 市场状态调节器) ===
    for r in rankings:
        mom_cross = r.get('mom_cross_score') if r.get('mom_cross_score') is not None else 50
        total = (
            mom_cross * w['mom_cross'] +
            r['accel_score'] * w['accel'] +
            r.get('rsi_score', 50) * w['rsi'] +
            r['vol_score'] * w['vol'] +
            r['risk_adj'] * w['risk'] +
            r['rel_strength'] * w['rel'] +
            r['trend_quality'] * w['trend']
        )
        r['total_score'] = round(total, 2)

    rankings.sort(key=lambda x: x['total_score'], reverse=True)

    print(f"\n  --- 多因子综合评分 TOP 10 [{state_desc}] ---")
    print(f"  {'序号':>2} {'名称':<8} {'代码':<8} {'综合分':>6} {'复合动量':>6} {'加速度':>6} {'RSI':>5} {'量价':>6} {'风险':>6} {'相对':>6} {'趋势':>6} {'规模提示'}")
    print(f"  {'-'*105}")

    for i, r in enumerate(rankings[:10]):
        sig = r.get('share_signal', '')
        mc = r.get('mom_cross_score')
        mc_str = f"{mc:>6.1f}" if mc is not None else "   N/A"
        print(f"  {i+1:>2}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f} "
              f"{mc_str} {r['accel_score']:>6.1f} {r.get('rsi', 50):>5.1f} {r['vol_score']:>6.1f} {r['risk_adj']:>6.1f} "
              f"{r['rel_strength']:>6.1f} {r['trend_quality']:>6.1f}  {sig}")

    result_message += f"  ---多因子评分 TOP 5 [{state_desc}] ---\n"
    for i, r in enumerate(rankings[:5]):
        sig = r.get('share_signal', '')
        sig_text = f" [{sig}]" if sig else ""
        result_message += f"  {i+1}. {r['name']}({r['code']}) 综合分:{r['total_score']:.1f} 动量:{r['momentum']:+.2f}%{sig_text}\n"

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

        # === 调仓触发条件2/3: 动态退出 (动量排名跌出Top20% 或 跌破MA30离场线; 持仓满5天保护) ===
        elif days_since >= MIN_HOLD_DAYS:
            top_pct_n = max(1, int(len(rankings) * DYNAMIC_EXIT_TOP_PCT))
            hold_rank = next((i+1 for i, r in enumerate(rankings) if r['code'] == hc), len(rankings))
            hold_factors = next((r for r in rankings if r['code'] == hc), None)
            below_ma30 = bool(hold_factors is None or not hold_factors.get('above_ma30', True))
            if hold_rank > top_pct_n:
                need_rebalance = True
                rebalance_reason = f"动量排名跌出Top{top_pct_n}(当前第{hold_rank}/{len(rankings)}名, 持仓{days_since}天)"
            elif below_ma30:
                need_rebalance = True
                rebalance_reason = f"跌破30日离场线(MA30, 持仓{days_since}天)"

        # === 提示: 持仓不满5天但已触发离场信号 ===
        elif days_since < MIN_HOLD_DAYS:
            top_pct_n = max(1, int(len(rankings) * DYNAMIC_EXIT_TOP_PCT))
            hold_rank = next((i+1 for i, r in enumerate(rankings) if r['code'] == hc), len(rankings))
            hold_factors = next((r for r in rankings if r['code'] == hc), None)
            below_ma30 = bool(hold_factors is None or not hold_factors.get('above_ma30', True))
            if hold_rank > top_pct_n or below_ma30:
                print(f"  [提示] 动量排名第{hold_rank}名(跌出Top{top_pct_n})或跌破MA30, 但持仓仅{days_since}天<{MIN_HOLD_DAYS}天保护期, 暂不调仓")
                result_message += f"\n[保护期] 触发离场信号但持仓{days_since}天<{MIN_HOLD_DAYS}天, 暂不调仓\n"

    if need_rebalance:
        # === 选品: 排名Top 2~3 且站上20日均线(优先), 全部跌破则取第一名兜底 ===
        target = None
        for r in rankings[:min(3, len(rankings))]:
            if r.get('above_ma20', True):
                target = r
                break
        if target is None:
            target = rankings[0]
        print(f"\n  {'='*40}")
        result_message += f"{'='*40}\n"

        print(f"  [调仓信号] 需要调仓! 原因: {rebalance_reason}")
        result_message += f"[调仓信号] 需要调仓! 原因: {rebalance_reason}\n"

        print(f"  目标: {target['name']} ({target['code']})")
        result_message += f"目标 {target['name']} ({target['code']})\n"

        print(f"  综合评分: {target['total_score']:.1f}")
        result_message += f"综合评分 {target['total_score']:.1f}\n"

        trend_flags = []
        if target.get('ema_bull'): trend_flags.append("EMA多头")
        if target.get('golden_cross'): trend_flags.append("金叉")
        if target.get('macd_above_zero'): trend_flags.append("MACD零轴上")
        if target.get('macd_second_golden'): trend_flags.append("二次金叉")
        trend_flag_str = "/".join(trend_flags) if trend_flags else "无"

        print(f"  动量: {target['momentum']:+.2f}% | RSI: {target.get('rsi', 50):.1f} | 趋势: {trend_flag_str}")
        result_message += f"动量:{target['momentum']:+.2f}% RSI:{target.get('rsi', 50):.1f} 趋势:{trend_flag_str}\n"

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
            "rsi_at_buy": target.get('rsi', 50),
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


    print(f"\n  {'='*60}")

    # ========== 提前定义报告目录 ==========
    report_dir = os.path.join(os.path.dirname(__file__), '..', 'report_daily')
    os.makedirs(report_dir, exist_ok=True)

    # ========== 保存微信汇总报告 ==========
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
