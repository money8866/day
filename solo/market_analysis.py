#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
大盘分析与仓位建议
- 分析大盘情绪和趋势
- 游资标准仓位建议
"""
import sys
import json

# Windows GBK 控制台输出修复:安全方式（Python 3.7+）
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import time
import sqlite3
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

# ================
# 环境配置
# ================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 阻止 tushare 写入根目录
original_expanduser = os.path.expanduser
safe_cache_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
os.makedirs(safe_cache_dir, exist_ok=True)

# daily_cache 完整性阈值：全市场约5500只，少于5000视为缓存不完整（部分写入），需强制重拉
DAILY_CACHE_MIN_ROWS = 5000

def daily_cache_complete(trade_date):
    """判断某交易日 daily_cache 是否已缓存完整全市场数据（防部分写入导致成交额/涨跌失真）"""
    try:
        from stock_cache import get_daily_by_date_count
        return get_daily_by_date_count(trade_date) >= DAILY_CACHE_MIN_ROWS
    except Exception:
        return False

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(safe_cache_dir, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

# 加载环境变量
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env'))
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# =========================
# 获取最近交易日
# =========================

def get_last_trade_date():

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 获取交易日历（带本地缓存 + 降级，网络不可用时不崩溃）
    # =========================
    cal = None
    try:
        cal = pro.trade_cal(
            exchange='',
            start_date='20200101',
            end_date=query_date
        )
    except Exception as e:
        print(f"[交易日历] 网络获取失败，改用本地缓存/周历降级: {e}")

    if cal is not None and not cal.empty:
        cal = cal[cal['is_open'] == 1]
        # 顺手落一份本地缓存，供网络不可用时使用
        try:
            with open(os.path.join(safe_cache_dir, 'trade_cal_cache.json'), 'w', encoding='utf-8') as f:
                json.dump(sorted(cal['cal_date'].astype(str).tolist()), f)
        except Exception:
            pass
        # 最近交易日
        last_trade_date = cal[
            cal['cal_date'] <= query_date
        ]['cal_date'].max()
        return str(last_trade_date)

    # 降级1：本地日历缓存
    try:
        with open(os.path.join(safe_cache_dir, 'trade_cal_cache.json'), encoding='utf-8') as f:
            days = json.load(f)
        cand = [d for d in days if d <= query_date]
        if cand:
            print(f"[交易日历] 使用本地缓存，最近交易日 {cand[-1]}")
            return cand[-1]
    except Exception:
        pass

    # 降级2：按自然周历推（周一~周五为交易日）
    dt = datetime.strptime(query_date, '%Y%m%d')
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    print(f"[交易日历] 使用周历降级，最近交易日 {dt.strftime('%Y%m%d')}")
    return dt.strftime('%Y%m%d')


TRADE_DATE = get_last_trade_date()

# ============================================================
# 当日指数快照降级源（腾讯行情）
# ------------------------------------------------------------
# 背景（2026-11-02 事故）：tushare index_daily 当日数据常在收盘后 30~60 分钟
# 才就绪，盘后推送（15:50）跑在就绪前，cached_index_daily 静默返回缓存中
# 最后一行的旧数据（10-30 收盘 3599.02），而宽度/涨停数据走的是当日已就绪
# 的接口，导致报告"指数旧 + 宽度新"自相矛盾、状态机误判快速下跌。
# 规则：任何取到指数 K 线/概况的地方，末行 trade_date 必须等于目标交易日，
# 不等则用本函数补当日行，绝不静默使用旧数据。
# ============================================================

_TS_QCODE_MAP = {
    "000001.SH": "sh000001", "000300.SH": "sh000300", "000852.SH": "sh000852",
    "932000.CSI": "sh932000", "399006.SZ": "sz399006", "000688.SH": "sh000688",
    "000905.SH": "sh000905", "000016.SH": "sh000016", "899050.BJ": "bj899050",
}

# 东财 secid（腾讯缺失时的二级兜底，如中证2000 932000.CSI）
_EM_SECID_MAP = {
    "000001.SH": "1.000001", "000300.SH": "1.000300", "000852.SH": "1.000852",
    "932000.CSI": "2.932000", "399006.SZ": "0.399006", "000688.SH": "1.000688",
    "000905.SH": "1.000905", "000016.SH": "1.000016", "899050.BJ": "0.899050",
}


def _eastmoney_index_snapshot(ts_code, trade_date=None):
    """东财实时快照取当日指数（腾讯缺失时的二级兜底）。返回 dict 或 None"""
    import urllib.request
    secid = _EM_SECID_MAP.get(str(ts_code))
    if not secid:
        return None
    try:
        url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=" + secid +
               "&fields=f43,f44,f45,f46,f47,f48,f60,f170")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        f = (d.get("data") or {})
        close = float(f.get("f43", 0)) / 100
        pre_close = float(f.get("f60", 0)) / 100
        if close <= 0 or pre_close <= 0:
            return None
        return {
            "trade_date": str(trade_date or TRADE_DATE),
            "open": float(f.get("f46", 0)) / 100,
            "high": float(f.get("f44", 0)) / 100,
            "low": float(f.get("f45", 0)) / 100,
            "close": close, "pre_close": pre_close,
            "pct_chg": float(f.get("f170", 0)) / 100,
            "vol": float(f.get("f47", 0)),
            "amount": float(f.get("f48", 0)),
        }
    except Exception as e:
        print(f"[Index] 东财快照兜底失败({ts_code}): {e}")
    return None


def tencent_index_snapshot(ts_code, trade_date=None):
    """实时快照取当日指数 OHLC（tushare 当日未就绪时降级用；腾讯优先、东财兜底）。
    返回 dict 或 None（取不到绝不编造）。"""
    import urllib.request
    qcode = _TS_QCODE_MAP.get(str(ts_code))
    if qcode:
        try:
            url = "https://qt.gtimg.cn/q=" + qcode
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
            for line in raw.strip().split(";"):
                line = line.strip()
                if "=" not in line:
                    continue
                v = line.split('="')[1].rstrip('"').split("~")
                if len(v) < 38:
                    continue
                close, pre_close = float(v[3]), float(v[4])
                if close <= 0 or pre_close <= 0:
                    break
                return {
                    "trade_date": str(trade_date or TRADE_DATE),
                    "open": float(v[5]), "high": float(v[33]), "low": float(v[34]),
                    "close": close, "pre_close": pre_close,
                    "pct_chg": round((close / pre_close - 1) * 100, 4),
                    "vol": float(v[36]) * 100, "amount": float(v[37]) * 10000,
                }
        except Exception as e:
            print(f"[Index] 腾讯快照降级失败({ts_code}): {e}")
    # 腾讯不可用/无代码 → 东财兜底
    snap = _eastmoney_index_snapshot(ts_code, trade_date)
    if snap is not None:
        print(f"[Index] {ts_code} 东财快照兜底: {snap['close']} ({snap['pct_chg']:+.2f}%)")
    return snap


# ============================================================
# 交易日历工具（P0：让"前一交易日"名副其实）
# ============================================================
# 背景：原实现用 `WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1` 取"前一日"，
# 实际取到的是"上一个有记录的日期"。DB 目前 198 个交易日里只有 105 天有记录，
# 缺失时滞回基准会悄悄退化成多日前的值，阶梯确认的"连续天数"随之失真。

_trade_cal_cache = {}


def get_trade_calendar(start_date='20200101', end_date=None):
    """获取交易日历（内存缓存）。失败返回 None。"""
    key = (start_date, end_date)
    if key in _trade_cal_cache:
        return _trade_cal_cache[key]
    days = None
    try:
        end = end_date or datetime.now().strftime('%Y%m%d')
        cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end)
        if cal is not None and not cal.empty:
            days = sorted(cal[cal['is_open'] == 1]['cal_date'].astype(str).tolist())
    except Exception as e:
        print(f"[交易日历] 获取失败（将降级为『上一个有记录的日期』）: {e}")
    _trade_cal_cache[key] = days
    return days


def is_trading_day(date_str):
    """判断日期是否为交易日。日历不可用时返回 True（保守放行，不阻断主流程）。"""
    if not date_str:
        return True
    d = str(date_str).replace('-', '')
    cal = get_trade_calendar('20200101')
    if not cal:
        return True
    return d in set(cal)


def get_prev_trading_day(date_str):
    """返回严格早于 date_str 的最近交易日；找不到返回 None。"""
    d = str(date_str).replace('-', '')
    cal = get_trade_calendar('20200101')
    if not cal:
        return None
    prev = [x for x in cal if x < d]
    return prev[-1] if prev else None


def cleanup_invalid_trade_dates(verbose=True):
    """清理数据库中不在交易日历里的记录（如历史误写入的 20260619）。

    非交易日记录会让「前一交易日」取值、连续回升天数等逻辑出现错位基准。
    """
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    if not os.path.exists(db_path):
        return 0
    cal = get_trade_calendar('20200101')
    if not cal:
        return 0
    valid = set(cal)
    conn = sqlite3.connect(db_path)
    removed = 0
    detail = []
    try:
        cur = conn.cursor()
        for table in ('overall_analysis', 'limit_stats', 'index_analysis'):
            try:
                rows = cur.execute(f'SELECT DISTINCT trade_date FROM {table}').fetchall()
            except Exception:
                continue
            bad = [r[0] for r in rows if r[0] and str(r[0]).replace('-', '') not in valid]
            for d in bad:
                try:
                    cur.execute(f'DELETE FROM {table} WHERE trade_date = ?', (d,))
                    removed += cur.rowcount
                    detail.append((table, d))
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()
    if verbose and detail:
        for t, d in detail:
            print(f"[交易日校验] 清理非交易日记录: {t} <- {d}")
        print(f"[交易日校验] 共清理 {removed} 条")
    return removed

# 指数K线取数窗口（自然日）：calc_ma_scores 需 ≥60 根K线，否则 L1 指数趋势层整体退化为
# 中性50分（ma60_slope 还需 ≥70 根）。90 自然日 ≈ 61 交易日，恰好卡在门槛上——
# 20261013 等日期曾整层失效。放宽到 180 自然日（≈120 交易日）留足缓冲。
INDEX_KLINE_LOOKBACK_DAYS = 180
START_DATE = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=INDEX_KLINE_LOOKBACK_DAYS)).strftime('%Y%m%d')
print(f"[Init] 交易日: {TRADE_DATE}  分析区间: {START_DATE} ~ {TRADE_DATE}")

# ================
# 缓存函数（SQLite版本）
# ================
DB_PATH = os.path.join(safe_cache_dir, 'cache.db')

def init_db():
    """初始化SQLite数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache_data (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            expire_time INTEGER,
            created_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def cache_get(name, trade_date=None, **kwargs):
    if trade_date is None:
        trade_date = TRADE_DATE
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{trade_date}"
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data, expire_time FROM cache_data WHERE key = ?', (cache_key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row
            # 检查是否过期（0 或 None 表示永不过期）
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                # 已过期，删除
                cursor.execute('DELETE FROM cache_data WHERE key = ?', (cache_key,))
                conn.commit()
                return None
            # 返回缓存数据
            from io import StringIO
            return pd.read_csv(StringIO(data_str))
    except Exception as e:
        print(f"[Cache] get error: {e}")
    finally:
        conn.close()
    return None

def cache_set(name, data, trade_date=None, expire_hours=None, **kwargs):
    if trade_date is None:
        trade_date = TRADE_DATE
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{trade_date}"
    
    # 默认永不过期（expire_hours 为 None 或 <=0 时）
    if expire_hours and expire_hours > 0:
        expire_time = int(time.time()) + expire_hours * 3600
    else:
        expire_time = 0  # 0 表示永不过期
    created_at = int(time.time())
    
    # 将DataFrame转为字符串
    from io import StringIO
    buffer = StringIO()
    data.to_csv(buffer, index=False)
    data_str = buffer.getvalue()
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        ''', (cache_key, data_str, expire_time, created_at))
        conn.commit()
    except Exception as e:
        print(f"[Cache] set error: {e}")
    finally:
        conn.close()

# 初始化数据库
init_db()

# ================
# 获取指数数据
# ================
# 指数K线缓存校验：不足此根数视为窗宽不足的旧缓存，强制重取
INDEX_KLINE_MIN_BARS = 100


def get_index_kline(ts_code="000300.SH", trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    start_date = (datetime.strptime(trade_date, "%Y%m%d")
                  - timedelta(days=INDEX_KLINE_LOOKBACK_DAYS)).strftime("%Y%m%d")

    cached = cache_get(name="index_kline", trade_date=trade_date, ts_code=ts_code)

    # 检查缓存数据是否包含目标日期且窗宽达标（避免复用按旧窗宽缓存的短序列）
    if cached is not None:
        if 'trade_date' in cached.columns and not cached.empty:
            max_date = str(cached['trade_date'].max())
            if max_date == str(trade_date) and len(cached) >= INDEX_KLINE_MIN_BARS:
                print(f"[Index] 缓存命中且包含目标日期: {ts_code} ({trade_date})")
                return cached
            else:
                print(f"[Index] 缓存不可用（最新日期: {max_date}, 根数: {len(cached)}, "
                      f"需要: {trade_date} 且 ≥{INDEX_KLINE_MIN_BARS}根），重新拉取")
        else:
            print(f"[Index] 缓存数据格式异常，重新拉取")
    
    print(f"[Index] 拉取 {ts_code} 数据: {start_date} ~ {trade_date}")
    # 指数行情统一走 index_daily_cache（缓存优先，缺口自动增量补），失败再回落直连 API
    try:
        from stock_cache import cached_index_daily
        df = cached_index_daily(ts_code, start_date, trade_date)
    except Exception as e:
        print(f"[Index] index_daily_cache 不可用({e})，回退直连")
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=trade_date)
    if df is None or df.empty:
        # V2: 优先 daily_cache 表
        try:
            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
            _, _max_date = get_daily_cache_range(ts_code)
            if _max_date is not None and str(_max_date) >= str(trade_date):
                df = get_daily_cache(ts_code, start_date, trade_date)
                if df is not None and not df.empty:
                    df['trade_date'] = df['trade_date'].astype(str)
        except Exception:
            pass
        if df is None or df.empty:
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=trade_date)
            if df is not None and not df.empty:
                try:
                    from stock_cache import batch_insert_daily_cache
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
    
    if df is not None and not df.empty:
        df = df.sort_values('trade_date').reset_index(drop=True)
        # 当日数据未就绪防护（2026-11-02 事故修复）：末行不是目标交易日时，
        # 用腾讯实时快照补当日行，绝不静默沿用旧数据
        last_dt = str(df.iloc[-1]['trade_date'])
        if last_dt != str(trade_date):
            print(f"[Index] ⚠️ {ts_code} 末行 {last_dt} ≠ 目标 {trade_date}"
                  f"（tushare 当日未就绪），腾讯快照降级")
            snap = tencent_index_snapshot(ts_code, trade_date)
            if snap is not None:
                df = pd.concat([df, pd.DataFrame([snap])], ignore_index=True)
                print(f"[Index] ✅ 腾讯快照补当日: close={snap['close']}"
                      f" ({snap['pct_chg']:+.2f}%)")
        cache_set(name="index_kline", data=df, trade_date=trade_date, ts_code=ts_code)
        print(f"[Index] 数据已缓存: {trade_date}")
    else:
        print(f"[Index] 未获取到数据")

    return df

# ================
# 计算趋势指标（新方案：游资视角）
# ================
def calc_intraday_heat(df, zt_count=0, zhaban_rate=0.0):
    """当日情绪热度（0~100）—— 反映"今天盘面有多热"，与结构分互补。

    ★ P2 配套：情绪分（calc_sentiment_score）重构为「结构质量分」后，
      其语义从"情绪冷热"变成了"结构是否有利"（高波动筑底/低位=高分）。
      而 classify_market_regime 的判定规则依赖的是**原始语义**
      （"情绪≥70 → 主升加速期，可追高连板"、"情绪<40 → 顶部分歧期"），
      直接用结构分会误判（例如高波动筑底时结构分高，但当天其实是冰点）。

      因此把"当日热度"独立出来供分类逻辑使用，语义与阈值保持不变：
        涨跌方向与强度 (40) + 连涨跌 (25) + 封板质量 (20) + 振幅 (15)

    Returns: (heat_score, heat_status)
    """
    if df is None or len(df) < 20:
        return 50.0, "无数据"

    latest = df.iloc[-1]
    pct_chg = latest['pct_chg'] if 'pct_chg' in df.columns else 0

    # 1. 涨跌方向与强度 (40分)
    if pct_chg >= 2:
        direction_score = 40
    elif pct_chg >= 1:
        direction_score = 30
    elif pct_chg >= 0:
        direction_score = 20
    elif pct_chg >= -1:
        direction_score = 10
    elif pct_chg >= -2:
        direction_score = 3
    else:
        direction_score = 0

    # 2. 连涨跌 (25分)
    up_streak = down_streak = 0
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] > 0:
                up_streak += 1
            else:
                break
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] < 0:
                down_streak += 1
            else:
                break
    streak_score = 12 + up_streak * 3 - down_streak * 3
    streak_score = min(25, max(0, streak_score))

    # 3. 封板质量 (20分)
    zt_quality_score = 10
    if zt_count > 0:
        if zhaban_rate < 10:
            zt_quality_score = 20
        elif zhaban_rate < 20:
            zt_quality_score = 16
        elif zhaban_rate < 35:
            zt_quality_score = 10
        else:
            zt_quality_score = 4
    elif zhaban_rate > 50:
        zt_quality_score = 0

    # 4. 振幅 (15分)
    amplitude = (latest['high'] - latest['low']) / latest['low'] * 100
    amp_score = min(15, max(0, 7.5 + amplitude * 0.5))

    heat = max(0, min(100, direction_score + streak_score + zt_quality_score + amp_score))
    if heat >= 70:
        status = "情绪高涨"
    elif heat >= 50:
        status = "情绪温和"
    elif heat >= 30:
        status = "情绪低迷"
    else:
        status = "情绪退潮"
    return heat, status


def calc_trend_score(df, up_count=0, total_count=3000):
    """
    趋势分 = MA_SCORE(50) + INDEX_SCORE(30) + BREADTH_SCORE(20)

    ★ P2 说明（权重量标定结论）：
      曾尝试用「趋势分分档单调性」反推权重（网格搜索 ma/idx/br 全部组合），
      结果所有候选组合的 ρ(趋势分, fwd5) 都在 0.068~0.075 之间（差异 <0.01），
      **调权重对预测力毫无改善**，且把 br（广度）权重调高会让趋势分变成反向指标
      （br 单独分档的单调性 ρ=-0.400）。
      因此：**权重保持 50/30/20 不变**，把精力放在新增真正有效的
      「位置状态 / 波动率状态」两个维度上（见 calc_position_vol_regime）。

    MA_SCORE（50分）- 均线趋势 + 动量加速（权重提升，降低单日广度波动影响）：
    - 多头排列 + 加速上涨    50
    - 多头排列但减速        40
    - 多头平稳              45
    - MA5 > MA10            38
    - MA5 > MA20            25
    - 空头但企稳             22
    - 全空头排列             12
    - 其他                  18

    INDEX_SCORE（30分）- 指数站位：
    - 站上20日线 30
    - 站上10日线 20
    - 站上5日线 10
    - 全部跌破 0

    BREADTH_SCORE（20分）- 市场广度（降低权重，防止单日广度波动驱动仓位跳变）：
    - >70%    20
    - 60%     17
    - 50%     14
    - 40%      8
    - 30%      4
    - <30%     0
    """
    if df is None or len(df) < 20:
        return 50.0, "无数据", {}
    
    latest = df.iloc[-1]
    
    # 计算均线
    ma5 = df['close'].rolling(5).mean().iloc[-1]
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    
    # 计算MA斜率（动量加速因子）
    ma5_series = df['close'].rolling(5).mean()
    ma20_series = df['close'].rolling(20).mean()
    ma5_slope = 0.0
    ma20_slope = 0.0
    if len(ma5_series) >= 6 and not pd.isna(ma5_series.iloc[-6]) and ma5_series.iloc[-6] > 0:
        ma5_slope = (ma5 - ma5_series.iloc[-6]) / ma5_series.iloc[-6] * 100
    if len(ma20_series) >= 21 and not pd.isna(ma20_series.iloc[-21]) and ma20_series.iloc[-21] > 0:
        ma20_slope = (ma20 - ma20_series.iloc[-21]) / ma20_series.iloc[-21] * 100
    
    # -------------------
    # MA_SCORE（50分）- 均线趋势 + 动量加速修正
    # -------------------
    ma_score = 0
    momentum_note = ""
    if ma5 > ma10 > ma20:
        # 多头排列：判断加速还是减速
        if ma5_slope > 2 and ma20_slope > 0.5:
            ma_score = 50  # 加速上涨
            momentum_note = "加速上涨"
        elif ma5_slope < 0.5:
            ma_score = 40  # 多头但减速
            momentum_note = "多头减速"
        else:
            ma_score = 45  # 多头平稳
            momentum_note = "多头平稳"
    elif ma5 > ma10:
        ma_score = 38
        momentum_note = "短多头"
    elif ma5 > ma20:
        ma_score = 25
        momentum_note = "弱反弹"
    elif ma5 < ma10 < ma20:
        # 空头排列：判断是否企稳
        if ma5_slope > 0 and len(ma5_series) >= 4 and ma5 > ma5_series.iloc[-4]:
            ma_score = 22  # 空头但企稳
            momentum_note = "空头企稳"
        else:
            ma_score = 12
            momentum_note = "空头加速"
    else:
        ma_score = 18  # 其他情况
        momentum_note = "趋势不明"
    
    # -------------------
    # INDEX_SCORE（30分）- 指数站位
    # -------------------
    index_score = 0
    if latest['close'] > ma20:
        index_score = 30
    elif latest['close'] > ma10:
        index_score = 20
    elif latest['close'] > ma5:
        index_score = 10
    else:
        index_score = 0
    
    # -------------------
    # BREADTH_SCORE（20分）- 市场广度
    # -------------------
    breadth_score = 0
    if total_count > 0:
        up_ratio = up_count / total_count * 100
        if up_ratio > 70:
            breadth_score = 20
        elif up_ratio >= 60:
            breadth_score = 17
        elif up_ratio >= 50:
            breadth_score = 14
        elif up_ratio >= 40:
            breadth_score = 8
        elif up_ratio >= 30:
            breadth_score = 4
        else:
            breadth_score = 0
    else:
        # 如果没有上涨家数数据，用指数涨幅代替
        pct_chg = float(latest.get('pct_chg', 0))
        if pct_chg > 2:
            breadth_score = 17
        elif pct_chg > 1:
            breadth_score = 14
        elif pct_chg > 0:
            breadth_score = 10
        elif pct_chg > -1:
            breadth_score = 6
        else:
            breadth_score = 3
    
    # 计算总分
    trend_score = ma_score + index_score + breadth_score
    trend_score = max(0, min(100, trend_score))
    
    # 判断趋势状态（综合趋势分和均线排列）
    if trend_score >= 75 and ma5 > ma10 > ma20 and latest['close'] > ma5:
        trend_status = "上升趋势"
    elif trend_score >= 65 and ma5 > ma10:
        trend_status = "上升趋势"
    elif trend_score >= 60:
        trend_status = "震荡偏强"
    elif trend_score >= 45:
        trend_status = "震荡整理"
    elif trend_score >= 30:
        trend_status = "震荡偏弱"
    else:
        trend_status = "下降趋势"
    
    detail = {
        'ma_score': ma_score, 'index_score': index_score, 'breadth_score': breadth_score,
        'ma5_slope': round(ma5_slope, 2), 'ma20_slope': round(ma20_slope, 2),
        'momentum_note': momentum_note,
    }
    # ★ P2：附加两个有实证前瞻力的维度（供上层观察与风控使用）
    regime = calc_position_vol_regime(df)
    detail['pos_pct'] = round(regime['pos_pct'], 1)
    detail['vol_pct'] = round(regime['vol_pct'], 1)
    detail['pos_note'] = ('高位（均值回归风险）' if regime['pos_pct'] >= 80 else
                           '偏低位' if regime['pos_pct'] < 40 else '中位')
    detail['vol_note'] = ('高波（筑底期）' if regime['vol_pct'] >= 85 else
                           '低波（上涨末期）' if regime['vol_pct'] < 30 else '常态')
    return trend_score, trend_status, detail

# ================
# 计算情绪指标
# ================
def calc_position_vol_regime(df, pos_lookback=60, vol_lookback=120):
    """计算「位置状态」与「波动率状态」两个择时维度（★ P2 新增）。

    ── 数据依据（2026-01~10 全样本 168 个交易日，中证2000）──

    位置分位（收盘在 60 日区间中的分位）分档 vs 后续收益：
        0-20   N=26  fwd5 -0.80%  胜率 46.2%
        20-40  N=25  fwd5 +0.44%  胜率 60.0%   ← 最好
        40-60  N=56  fwd5 -0.09%  胜率 48.2%
        60-80  N=38  fwd5 -1.35%  胜率 34.2%
        80-100 N=23  fwd5 -1.86%  胜率 30.4%   ← 最差（高位均值回归风险）
        ρ(pos60, fwd5) = -0.206 (p=0.008)；ρ(pos60, fwd10) = -0.257 (p=0.001)

    波动率分位（20 日标准差在自身 120 日历史中的分位）分档：
        0-30   N=33  fwd5 -2.12%  胜率 24.2%   ← 最差（低波 = 上涨末期）
        30-60  N=68  fwd5 -0.80%  胜率 44.1%
        60-85  N=39  fwd5 -1.75%  胜率 35.9%
        85-100 N=28  fwd5 +2.99%  胜率 78.6%   ← 最好（高波 = 恐慌后筑底）
        ρ(std20, fwd5) = +0.352 (p<0.001)；ρ(std20, fwd10) = +0.540 (p<0.001)

    这两个是全样本中唯二对未来收益有稳定显著预测力的维度。
    原情绪分中 60 分的「当日涨跌复读」对未来的相关性全部不显著（p=0.31~0.88），
    等于用「今天涨了」当「明天会涨」。本次重构把这 60 分替换为这两类结构性维度。

    Returns: dict(pos_pct, vol_pct, vol_std20)
    """
    res = {'pos_pct': 50.0, 'vol_pct': 50.0, 'vol_std20': 0.0}
    if df is None or len(df) < 25:
        return res
    try:
        d = df.reset_index(drop=True)
        # 位置分位：收盘在近 N 日 [最低, 最高] 区间中的位置
        w = d['close'].tail(pos_lookback)
        if len(w) >= 20:
            lo, hi = float(w.min()), float(w.max())
            if hi > lo:
                res['pos_pct'] = (float(d['close'].iloc[-1]) - lo) / (hi - lo) * 100
        # 波动率分位：20 日标准差在自身近 N 日历史中的分位
        if 'pct_chg' in d.columns:
            std_series = d['pct_chg'].rolling(20).std().dropna()
            if len(std_series) >= 20:
                hist = std_series.tail(vol_lookback)
                cur = float(std_series.iloc[-1])
                res['vol_pct'] = float((hist < cur).mean() * 100)
                res['vol_std20'] = cur
    except Exception:
        pass
    return res


def calc_sentiment_score(df, zt_count=0, zhaban_rate=0.0, total_amount=0):
    """
    情绪评分（★ P2 重构）
    = 封板质量(25) + 量能结构(25) + 波动率状态(25) + 成交额趋势(15) + 位置状态(10)

    ★ P2 重构要点：删除了原有的「涨跌方向(25) + 连涨跌(20) + 振幅(15)」共 60 分。
      这三项与「当日涨跌幅」的相关系数分别为 0.985 / 0.781 / 0.885，
      而对 fwd1 / fwd5 的相关性全部不显著（p=0.31 / 0.51 / 0.70），
      属于典型的「用今天涨了当明天会涨」。重构后 100 分全部来自
      结构性（封板质量）与历史性（量能/波动率/位置）维度。

    保留的显著维度：量能 ρ(fwd5)=+0.136 (p=0.08)、
    波动率状态 ρ(fwd5)=+0.352 (p<0.001)、位置状态 ρ(fwd5)=-0.206 (p=0.008)。

    保持返回 2 元组 (score, status)，与既有调用方（quant_trading_system.py）兼容。
    """
    if df is None or len(df) < 20:
        return 50.0, "无数据"

    # ==============================
    # 1. 涨停封板质量 (25分)
    #    炸板率越低 → 封板质量越高（结构性指标，与当日涨跌无关）
    # ==============================
    zt_quality_score = 12   # 默认中性
    if zt_count > 0:
        if zhaban_rate < 10:
            zt_quality_score = 25      # 情绪极强
        elif zhaban_rate < 20:
            zt_quality_score = 20
        elif zhaban_rate < 35:
            zt_quality_score = 12
        else:
            zt_quality_score = 5       # 炸板率高 = 情绪分歧
    elif zhaban_rate > 50:
        zt_quality_score = 0           # 极端炸板，情绪崩溃

    # ==============================
    # 2. 量能结构 (25分)
    #    相对量能（5日/20日量比），**不再与当日涨跌方向耦合**
    #    ρ(vol_ratio, fwd5) = +0.136 (p=0.08)
    # ==============================
    vol5 = df['vol'].tail(5).mean()
    vol20 = df['vol'].tail(20).mean()
    vol_ratio = vol5 / vol20 if vol20 > 0 else 1

    if vol_ratio >= 1.5:
        vol_score = 25
    elif vol_ratio >= 1.2:
        vol_score = 20
    elif vol_ratio >= 1.0:
        vol_score = 15
    elif vol_ratio >= 0.85:
        vol_score = 9
    else:
        vol_score = 4

    # ==============================
    # 3. 波动率状态 (25分) —— 全样本最强前瞻维度
    #    ρ(std20, fwd5)=+0.352 (p<0.001)，ρ(std20, fwd10)=+0.540 (p<0.001)
    #    低波 = 上涨末期（差），高波 = 恐慌后筑底（好）
    # ==============================
    regime = calc_position_vol_regime(df)
    vol_pct = regime['vol_pct']
    if vol_pct >= 85:
        vol_state_score = 25
    elif vol_pct >= 60:
        vol_state_score = 17
    elif vol_pct >= 30:
        vol_state_score = 11
    else:
        vol_state_score = 4

    # ==============================
    # 4. 成交额趋势 (15分)
    #    5日/20日成交额比，去掉原实现的涨跌方向耦合
    # ==============================
    amount_trend_score = 8    # 默认中性
    if 'amount' in df.columns and len(df) >= 20:
        amt_5d = df['amount'].tail(5).mean()
        amt_20d = df['amount'].tail(20).mean()
        if amt_20d > 0:
            amt_ratio = amt_5d / amt_20d
            if amt_ratio >= 1.4:
                amount_trend_score = 15
            elif amt_ratio >= 1.15:
                amount_trend_score = 12
            elif amt_ratio >= 0.95:
                amount_trend_score = 8
            elif amt_ratio >= 0.8:
                amount_trend_score = 5
            else:
                amount_trend_score = 2

    # ==============================
    # 5. 位置状态 (10分)
    #    ρ(pos60, fwd5)=-0.206 (p=0.008)，位置越高（均值回归风险）→ 分数越低
    # ==============================
    pos_pct = regime['pos_pct']
    if pos_pct >= 80:
        pos_score = 2
    elif pos_pct >= 60:
        pos_score = 5
    elif pos_pct >= 40:
        pos_score = 8
    elif pos_pct >= 20:
        pos_score = 10      # 20~40 区间后续收益最好
    else:
        pos_score = 7

    # ==============================
    # 综合评分
    # ==============================
    sentiment_score = (zt_quality_score + vol_score + vol_state_score
                       + amount_trend_score + pos_score)
    sentiment_score = max(0, min(100, sentiment_score))

    if sentiment_score >= 70:
        sentiment_status = "情绪高涨"
    elif sentiment_score >= 50:
        sentiment_status = "情绪温和"
    elif sentiment_score >= 30:
        sentiment_status = "情绪低迷"
    else:
        sentiment_status = "情绪退潮"

    return sentiment_score, sentiment_status

# ================
# 大盘整体概况（成交额/涨跌家数/涨停跌停/炸板率）
# ================
def get_market_overview(trade_date=None):
    """获取大盘整体概况数据"""
    if trade_date is None:
        trade_date = TRADE_DATE
    
    overview = {
        "sh_index": 0, "sh_pct": 0,
        "total_amount": 0,
        "up_count": 0, "down_count": 0,
        "zt_count": 0, "dt_count": 0,
        "zb_count": 0, "zb_rate": 0,
    }

    try:
        # 上证指数（用 index_daily 接口；当日未就绪时腾讯快照降级）
        sh_df = pro.index_daily(ts_code="000001.SH", start_date=trade_date, end_date=trade_date)
        if sh_df is not None and not sh_df.empty:
            overview["sh_index"] = sh_df.iloc[0]["close"]
            overview["sh_pct"] = sh_df.iloc[0]["pct_chg"]
        else:
            snap = tencent_index_snapshot("000001.SH", trade_date)
            if snap is not None:
                overview["sh_index"] = snap["close"]
                overview["sh_pct"] = snap["pct_chg"]
                print(f"[Overview] ⚠️ tushare 上证 {trade_date} 未就绪，"
                      f"腾讯快照降级: {snap['close']} ({snap['pct_chg']:+.2f}%)")

        # 全市场成交额（日线所有股票 amount 之和，千元→亿元）— V3: daily_cache 表需完整性校验
        try:
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            daily_df = None
            if daily_cache_complete(trade_date):
                daily_df = get_daily_by_date(trade_date)
            if daily_df is None or daily_df.empty:
                daily_df = pro.daily(trade_date=trade_date)
                if daily_df is not None and not daily_df.empty:
                    try:
                        batch_insert_daily_cache(daily_df)
                    except Exception:
                        pass
        except Exception:
            daily_df = None
            try:
                from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
                if daily_cache_complete(trade_date):
                    daily_df = get_daily_by_date(trade_date)
            except Exception:
                pass
            if daily_df is None or daily_df.empty:
                daily_df = pro.daily(trade_date=trade_date)
                if daily_df is not None and not daily_df.empty:
                    try:
                        from stock_cache import batch_insert_daily_cache
                        batch_insert_daily_cache(daily_df)
                    except Exception:
                        pass
        if daily_df is not None and not daily_df.empty:
            total_amt = daily_df["amount"].sum() / 100000  # 千元→亿元
            overview["total_amount"] = round(total_amt, 0)
            up_count = (daily_df["pct_chg"] > 0).sum()
            down_count = (daily_df["pct_chg"] < 0).sum()
            overview["up_count"] = up_count
            overview["down_count"] = down_count

        time.sleep(0.3)

        # 涨停池（官方 limit_list_d: U涨停/D跌停/Z炸板）
        zt_df = pro.limit_list_d(trade_date=trade_date, limit_type='U')
        if zt_df is not None and not zt_df.empty:
            overview["zt_count"] = len(zt_df)
        time.sleep(0.15)

        # 跌停池
        dt_df = pro.limit_list_d(trade_date=trade_date, limit_type='D')
        if dt_df is not None and not dt_df.empty:
            overview["dt_count"] = len(dt_df)
        time.sleep(0.15)

        # 炸板池
        zb_df = pro.limit_list_d(trade_date=trade_date, limit_type='Z')
        if zb_df is not None and not zb_df.empty:
            overview["zb_count"] = len(zb_df)
        time.sleep(0.15)

        # 炸板率 = 炸板数 / (涨停数 + 炸板数)
        total_zt_zb = overview["zt_count"] + overview["zb_count"]
        if total_zt_zb > 0:
            overview["zb_rate"] = round(overview["zb_count"] / total_zt_zb * 100, 1)

    except Exception as e:
        print(f"   [市场概况] 获取失败: {e}")

    return overview


def print_market_overview(overview, trade_date=None):
    """打印大盘整体概况"""
    if trade_date is None:
        trade_date = TRADE_DATE
    print(f"\n📊 大盘整体概况（{trade_date}）")
    print(f"  {'='*50}")
    print(f"  上证指数: {overview['sh_index']:.2f}  ({overview['sh_pct']:+.2f}%)")
    print(f"  全市场成交额: {overview['total_amount']:.0f}亿")
    print(f"  上涨家数: {overview['up_count']}  下跌家数: {overview['down_count']}")
    print(f"  涨停: {overview['zt_count']}  跌停: {overview['dt_count']}  炸板: {overview['zb_count']}")
    print(f"  炸板率: {overview['zb_rate']}%")
    print(f"  {'='*50}")


# ================
# 主分析流程
# ================
def _fallback_prev_recorded_date(table, d):
    """交易日历不可用时的降级：取该表中最后一个早于 d 的 trade_date。"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f'SELECT trade_date FROM {table} WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1',
            (d,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _fetch_prev_value(table, column, trade_date):
    """★ P0 修复：按**真正的上一交易日**读取表中某列的值。

    原实现用 `WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1`，
    取到的是"上一个有记录的日期"而非"上一交易日"。DB 目前 198 个交易日里
    只有 105 天有记录，缺失时滞回基准会悄悄退化成多日前的值，
    阶梯确认机制（连续回升天数）的天数统计随之失真。

    降级路径：交易日历取不到时，退回"上一个有记录的日期"并打印告警。
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    d = str(trade_date).replace('-', '')
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    if not os.path.exists(db_path):
        return None

    prev_day = get_prev_trading_day(d)
    if prev_day is None:
        prev_day = _fallback_prev_recorded_date(table, d)
        if prev_day is None:
            return None
    else:
        # 上一交易日若无记录（缺失日），再退回该表最近的有记录日期
        conn0 = sqlite3.connect(db_path)
        try:
            cur0 = conn0.cursor()
            cur0.execute(f'SELECT 1 FROM {table} WHERE trade_date = ?', (prev_day,))
            if cur0.fetchone() is None:
                prev_day = _fallback_prev_recorded_date(table, d) or prev_day
        except Exception:
            pass
        finally:
            conn0.close()

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT {column} FROM {table} WHERE trade_date = ?', (prev_day,))
        row = cur.fetchone()
        if row and row[0] is not None:
            return row[0]
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _get_prev_trend_score(trade_date=None):
    """从数据库读取前一交易日的趋势分（用于趋势确认机制）"""
    if trade_date is None:
        trade_date = TRADE_DATE
    try:
        val = _fetch_prev_value('overall_analysis', 'trend_score', trade_date)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return None


def _get_prev_position(trade_date=None):
    """从数据库读取前一交易日的仓位（用于滞回机制）"""
    if trade_date is None:
        trade_date = TRADE_DATE
    try:
        # ★ 修 A：0 是合法仓位（空仓），不能被 `if row and row[0]` 当成"无数据"
        #   否则前一日空仓会被判为 prev=None，次日直接跳到 base_target 造成"一日跳档"
        val = _fetch_prev_value('overall_analysis', 'total_position', trade_date)
        if val is not None:
            return int(val)
    except Exception:
        pass
    return None


def _get_prev_up_ratio(trade_date=None):
    """从 limit_stats 读取前一交易日的上涨家数占比（%）。

    用于 V9.9 广度一票否决的「钝化解除」：昨日仍在否决区(<35%)时，
    今日仅勉强过线(<55%)不立刻解除防守，避免单日广度翻转造成仓位跳档。"""
    if trade_date is None:
        trade_date = TRADE_DATE
    try:
        val = _fetch_prev_value('limit_stats', 'up_ratio', trade_date)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return None


def _writeback_v99_result(trade_date, v99_result):
    """★ P0 修复：V9.9 结果整行回写数据库。

    原实现只 UPDATE `total_position`，导致 DB 里出现口径混合：
      total_position = V9.9 的值（已被覆盖）
      market_status / position_reason = 老引擎的值（未覆盖）
    下游读到的是互相矛盾的两套口径（如 10/30：pos=0/情绪冰点退潮 vs status=主跌退潮期）。

    现在同步回写仓位、状态、理由，并打 engine_version 标记来源；
    老引擎的原始仓位保留在 legacy_position 列，可完整追溯。

    仅 UPDATE 已存在行；若当日行缺失则静默跳过（不伪造数据）。
    """
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    if not os.path.exists(db_path):
        return
    target = int(getattr(v99_result, 'position_target', 0) or 0)
    status = getattr(v99_result, 'market_regime', '') or ''
    action = getattr(v99_result, 'action', '') or ''
    detail = getattr(v99_result, 'action_detail', '') or ''
    sentence = getattr(v99_result, 'one_sentence', '') or ''
    reason = f"V9.9 {status} | 动作:{action}{('(' + detail + ')') if detail else ''} | {sentence}"

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE overall_analysis SET total_position = ?, market_status = ?, "
            "position_reason = ?, engine_version = ? WHERE trade_date = ?",
            (target, status, reason, 'V9.9', str(trade_date)))
        conn.commit()
        if cur.rowcount > 0:
            print(f"[V9.9] 已回写数据库: {trade_date} -> 仓位{target}% / 状态{status} "
                  f"(engine_version=V9.9)")
        else:
            print(f"[V9.9] 当日无记录可回写: {trade_date}")
    finally:
        conn.close()


def _get_recent_trend_scores(trade_date=None, days=10):
    """从数据库读取最近N个**交易日**的趋势分（用于计算连续回升天数）

    ★ P0：原实现用 `ORDER BY trade_date DESC LIMIT ?` 取"最近N条记录"，
    跳过的是"缺失的记录"而非"缺失的交易日"，导致连续天数统计把
    中间隔了休市/未记录的日子当成连续。改为按交易日历逐日回溯。
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    d = str(trade_date).replace('-', '')

    # 1) 取日历上最近 N 个交易日（严格早于 d）
    cal = get_trade_calendar('20200101')
    target_days = []
    if cal:
        prev = [x for x in cal if x < d]
        target_days = prev[-days:][::-1]      # 升序
    else:
        # 降级：日历不可用时退回"最近N条记录"
        try:
            db_path = os.path.join(safe_cache_dir, "market_analysis.db")
            if not os.path.exists(db_path):
                return []
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT trade_date, trend_score FROM overall_analysis
                WHERE trade_date < ? AND trend_score IS NOT NULL
                ORDER BY trade_date DESC LIMIT ?
            ''', (d, days))
            rows = cursor.fetchall()
            conn.close()
            if rows:
                return [(r[0], float(r[1])) for r in rows][::-1]
        except Exception:
            pass
        return []

    if not target_days:
        return []

    # 2) 逐日查 DB，缺失的交易日跳过（保持"交易日"口径）
    try:
        db_path = os.path.join(safe_cache_dir, "market_analysis.db")
        if not os.path.exists(db_path):
            return []
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        out = []
        for td in target_days:
            cursor.execute(
                'SELECT trend_score FROM overall_analysis WHERE trade_date = ?', (td,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                out.append((td, float(row[0])))
        conn.close()
        return out
    except Exception:
        return []


def _calc_consecutive_rise_days(recent_scores, current_score):
    """
    计算从底部反弹的连续回升天数
    算法：从今日往回数，找到最近的最低点，然后计算从低点到今日的连续上升天数
    """
    if not recent_scores or len(recent_scores) < 1:
        return 0
    
    all_scores = [s[1] for s in recent_scores] + [current_score]
    
    min_idx = 0
    min_val = all_scores[0]
    for i in range(1, len(all_scores)):
        if all_scores[i] < min_val:
            min_val = all_scores[i]
            min_idx = i
    
    if min_idx == len(all_scores) - 1:
        return 0
    
    consecutive = 0
    for i in range(min_idx, len(all_scores) - 1):
        if all_scores[i + 1] > all_scores[i]:
            consecutive += 1
        else:
            consecutive = 0
    
    return consecutive


def _calc_ma20_slope(index_results):
    """
    计算主要指数的MA20斜率，判断中期趋势方向
    返回：(斜率均值, 是否向下)
    斜率以5日MA20变动百分比计算
    """
    slopes = []
    for r in index_results:
        detail = r.get('trend_detail', {})
        ma20_slope = detail.get('ma20_slope', 0)
        slopes.append(ma20_slope)
    
    if not slopes:
        return 0.0, False
    
    avg_slope = sum(slopes) / len(slopes)
    return avg_slope, avg_slope < -0.3


def _apply_position_filters(
    position,
    trend_score,
    market_regime,
    prev_trend_score,
    recent_scores,
    ma20_slope,
    ma20_down
):
    """
    V8.1 仓位过滤器：三重防护机制
    1. MA20斜率过滤：中期趋势向下时，仓位上限锁死25%
    2. 阶梯式确认：底部反弹按连续回升天数阶梯提升仓位上限
    3. 状态联动仓位：冰点反弹期仓位强制锁定在10-20%
    """

    filters_applied = []
    
    # --- 第一重：MA20斜率过滤 ---
    if ma20_down and position > 25:
        position = min(position, 25)
        filters_applied.append(f"MA20向下({ma20_slope:.2f}%)，仓位上限锁死25%")
    
    # --- 第二重：阶梯式确认 ---
    if prev_trend_score is not None and prev_trend_score < 40:
        consecutive_days = _calc_consecutive_rise_days(recent_scores, trend_score)
        
        if consecutive_days <= 1:
            step_cap = 15
        elif consecutive_days == 2:
            step_cap = 25
        elif consecutive_days == 3:
            step_cap = 35
        else:
            step_cap = 50
        
        if position > step_cap:
            position = min(position, step_cap)
            filters_applied.append(f"底部反弹第{consecutive_days}天，阶梯上限{step_cap}%")
    
    # --- 第三重：状态联动 ---
    if market_regime == "冰点反弹期":
        if position > 20:
            position = min(position, 20)
            filters_applied.append("冰点反弹期，仓位上限20%")
    elif market_regime == "主跌退潮期":
        if position > 5:
            position = min(position, 5)
            filters_applied.append("主跌退潮期，仓位上限5%")
    
    return int(position), filters_applied


def analyze_market(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    print("=" * 80)
    print(f"📊 大盘分析与仓位建议 ({trade_date})")
    print("=" * 80)

    # 大盘整体概况
    overview = get_market_overview(trade_date)
    print_market_overview(overview, trade_date)
    
    # 获取主要指数数据
    indices = {
        "上证指数": "000001.SH",
        "沪深300": "000300.SH",
        "中证2000": "932000.CSI"
    }
    
    results = []
    
    for name, code in indices.items():
        print(f"\n📈 正在分析: {name} ({code})")
        
        df = get_index_kline(code, trade_date)
        
        if df is None or df.empty:
            print(f"   ❌ 无数据")
            continue
        
        latest = df.iloc[-1]
        up_count = overview.get('up_count', 0)
        total_count = overview.get('up_count', 0) + overview.get('down_count', 0)
        trend_score, trend_status, trend_detail = calc_trend_score(df, up_count, total_count)
        zt_count = overview.get('zt_count', 0)
        zhaban_rate = overview.get('zb_rate', 0)
        # 情绪分（P2 重构）：结构质量分 —— 反映"结构是否有利"
        sentiment_score, sentiment_status = calc_sentiment_score(df, zt_count, zhaban_rate, overview.get('total_amount', 0))
        # 当日热度（P2 配套）：反映"今天盘面有多热"，供市场状态分类保持原语义
        heat_score, heat_status = calc_intraday_heat(df, zt_count, zhaban_rate)
        
        print(f"   趋势分: {trend_score:.1f} -> {trend_status} [{trend_detail.get('momentum_note', '')}]")
        print(f"   结构分: {sentiment_score:.1f} → {sentiment_status}"
              f"  | 当日热度: {heat_score:.1f} → {heat_status}")
        print(f"   最新收: {latest['close']:.2f}  涨跌: {latest['pct_chg']:.2f}%")
        
        results.append({
            "name": name,
            "code": code,
            "trend_score": trend_score,
            "trend_status": trend_status,
            "sentiment_score": sentiment_score,
            "sentiment_status": sentiment_status,
            "heat_score": heat_score,
            "heat_status": heat_status,
            "close": latest['close'],
            "pct_chg": latest['pct_chg'],
            "trend_detail": trend_detail,
        })
    
    # 综合建议
    if results:
        # 获取 TOP3 主题趋势分
        theme_top3_scores = get_top3_theme_scores(trade_date)
        
        # 计算市场趋势总评分
        trend_score, index_trend, theme_trend = calculate_market_trend_score(results, theme_top3_scores)
        
        # 获取前一日仓位（从数据库读取）
        prev_position = _get_prev_position(trade_date)
        
        # 获取前一日趋势分（用于趋势确认机制）
        prev_trend_score = _get_prev_trend_score(trade_date)
        
        # 计算综合情绪分（各指数均值）
        avg_sentiment = sum(r['sentiment_score'] for r in results) / len(results)
        # ★ P2：市场状态分类用「当日热度」，保持"情绪冷热"原语义不变
        avg_heat = sum(r['heat_score'] for r in results) / len(results)
        
        # 使用带滞回的仓位调整
        zhaban_rate = overview.get('zb_rate', 0)
        market_status, position_range, position = get_market_status_and_position(
            trend_score, prev_position=prev_position, sentiment_score=avg_sentiment
        )
        
        # 市场状态分类（传入前日趋势分用于趋势确认）
        # ★ P2：传入当日热度而非结构分（分类规则依赖"情绪冷热"语义）
        market_regime, regime_reason = classify_market_regime(
            trend_score, avg_heat, zhaban_rate=zhaban_rate,
            prev_trend_score=prev_trend_score
        )
        
        # 持仓结构建议
        portfolio_structure = suggest_portfolio_structure(market_regime)
        
        # V8.1 三重仓位过滤器
        recent_scores = _get_recent_trend_scores(trade_date, days=10)
        ma20_slope, ma20_down = _calc_ma20_slope(results)
        
        position, filter_reasons = _apply_position_filters(
            position, trend_score, market_regime,
            prev_trend_score, recent_scores,
            ma20_slope, ma20_down
        )
        
        # 如果仓位被过滤器压低，同步更新市场状态和持仓结构
        if filter_reasons:
            if position <= 10:
                if market_regime not in ("主跌退潮期", "冰点反弹期"):
                    market_regime = "冰点反弹期"
                    portfolio_structure = suggest_portfolio_structure(market_regime)
            elif position <= 25:
                if market_regime in ("主升加速期",):
                    market_regime = "震荡轮动期"
                    portfolio_structure = suggest_portfolio_structure(market_regime)
        
        reason_addon = " | ".join(filter_reasons) if filter_reasons else ""
        
        reason = f"当前市场处于【{market_regime}】阶段 - {regime_reason}"
        if reason_addon:
            reason += f" | [V8.1过滤] {reason_addon}"
        if prev_position is not None and prev_position != position:
            reason += f"（前日仓位{prev_position}% -> 今日{position}%，滞回机制生效）"
        elif prev_position is not None and prev_position == position:
            reason += f"（维持前日仓位{prev_position}%，滞回机制维持不变）"
        
        print("\n" + "=" * 80)
        print("🎯 综合分析结论")
        print("=" * 80)
        
        print(f"\n【指数趋势分】")
        for r in results:
            momentum = r.get('trend_detail', {}).get('momentum_note', '')
            print(f"  {r['name']}: {r['trend_status']} ({r['trend_score']:.1f}) [{momentum}]")
        
        print(f"\n【市场趋势总评分】")
        print(f"  指数趋势 (IndexTrend): {index_trend:.1f}")
        print(f"  主题趋势 (ThemeTrend): {theme_trend:.1f}")
        print(f"  总趋势分 (TrendScore): {trend_score:.1f}")
        print(f"  市场状态: {market_status}")
        
        print(f"\n【市场情绪】")
        for r in results:
            print(f"  {r['name']}: 结构分 {r['sentiment_score']:.1f}（{r['sentiment_status']}）"
                  f" | 当日热度 {r['heat_score']:.1f}（{r['heat_status']}）")
        print(f"  综合结构分: {avg_sentiment:.1f}   综合当日热度: {avg_heat:.1f}")
        
        print(f"\n【市场状态分类】")
        print(f"  状态: {market_regime}")
        print(f"  理由: {regime_reason}")
        
        print(f"\n【持仓结构建议】")
        for k, v in portfolio_structure.items():
            print(f"  {k}: {v}")
        
        print(f"\n💡 总体仓位建议: {position}% ({position_range})")
        print(f"   理由: {reason}")
        
        # ===== 涨跌停 & 连板统计 =====
        print(f"\n{'='*80}")
        print("📊 涨跌停 & 连板统计")
        print('='*80)
        limit_stats = get_limit_up_down_stats(trade_date)
        max_lb = calc_max_limit_height(trade_date)
        limit_stats['max_limit_height'] = max_lb
        
        print(f"  涨停: {limit_stats['zt_count']}只 | 跌停: {limit_stats['dt_count']}只")
        print(f"  炸板: {limit_stats['zhaban_count']}只 | 炸板率: {limit_stats['broken_rate']:.1f}%")
        print(f"  最高连板: {max_lb}板")
        print(f"  上涨: {limit_stats['up_count']}只({limit_stats['up_ratio']}%) | 下跌: {limit_stats['down_count']}只({limit_stats['down_ratio']}%)")
        
        # 保存涨跌停数据到数据库（供实时监控读取）
        save_limit_stats_to_cache(limit_stats)
        save_limit_stats_to_database(limit_stats)
        
        # 保存结果
        save_result(results, position, reason, None, overview,
                    trend_score, index_trend, theme_trend, market_status, trade_date,
                    limit_stats, max_lb)
        
        # 保存到数据库（market_status 存 market_regime，更准确反映市场状态）
        save_to_database(trade_date, results, position, reason, 
                        trend_score, index_trend, theme_trend, market_regime)
        
        # ===== V9.0 动态仓位引擎 =====
        print(f"\n{'='*80}")
        print("🚀 V9.0 短线动态仓位引擎 (Regime-Aware Adaptive Position Engine)")
        print(f"{'='*80}")
        
        try:
            # 获取六大指数K线（为V9提供完整数据）
            index_data_dict = _build_index_data_dict_for_v9(trade_date)
            
            # 获取历史涨跌停数据（用于动量/风险加速度计算）
            hist_limit = None
            try:
                hist_limit = get_historical_data(days=20)
                if hist_limit is not None and hist_limit.empty:
                    hist_limit = None
            except Exception:
                pass
            
            # 运行 V9 引擎
            v9_result, v9_report = run_v9_position_engine_v9(
                overview=overview,
                limit_stats=limit_stats,
                max_limit_height=max_lb,
                theme_top3_scores=theme_top3_scores,
                trade_date=trade_date,
                prev_position=prev_position,
                hist_limit=hist_limit,
                index_data_dict=index_data_dict,
            )
            
            if v9_result is not None:
                # 输出 V9 报告
                print(v9_report)
                
                # 保存 V9 报告到文件
                v9_report_file = os.path.join(safe_cache_dir, f"position_engine_v9_{trade_date}.txt")
                try:
                    with open(v9_report_file, 'w', encoding='utf-8') as f:
                        f.write(v9_report)
                    print(f"[V9引擎] 报告已保存: {v9_report_file}")
                except Exception as e:
                    print(f"[V9引擎] 保存报告失败: {e}")
            else:
                print("[V9引擎] 运行失败，结果为空")
                
        except Exception as v9_error:
            print(f"[V9引擎] 运行异常: {v9_error}")
            import traceback
            traceback.print_exc()
        
        # ===== V9.9 极简仓位建议 =====
        try:
            from position_engine_v95 import PositionEngineV99
            from position_engine_v9 import MarketDataBundle as V99Bundle
            v99_engine = PositionEngineV99()
            v99_data = V99Bundle()
            v99_data.index_data = index_data_dict
            v99_data.overview = overview
            v99_data.limit_stats = limit_stats
            v99_data.max_limit_height = max_lb
            v99_data.theme_top3_scores = theme_top3_scores or []
            v99_data.hist_limit = hist_limit
            # ★ 修 C：前一日上涨家数占比，供「广度一票否决」钝化解除使用
            try:
                v99_data.prev_up_ratio = _get_prev_up_ratio(trade_date)
            except Exception:
                v99_data.prev_up_ratio = None
            
            v99_result = v99_engine.analyze(v99_data, prev_position)
            v99_report = v99_engine.generate_report(v99_result)
            
            # ★ P0：V9.9 整行回写（仓位 + 状态 + 理由 + engine_version），供次日滞回链式读取
            #   原实现只回写 total_position，导致 DB 里仓位是 V9.9 的、状态却是老引擎的口径。
            try:
                _writeback_v99_result(trade_date, v99_result)
            except Exception as wb_err:
                print(f"[V9.9] 目标仓位回写失败: {wb_err}")
            
            print(f"\n{'='*80}")
            print("⚡ V9.9 极简仓位建议")
            print(f"{'='*80}")
            print(v99_report)

            # ===== 指数日线补齐（V10.6：防止静默沿用旧指数数据）=====
            # ★ 必须在 V10.0/V10.1 分析**之前**：
            #   index_daily_cache 若缺当日（tushare 15:00 后才就绪），
            #   _query_index_hist 返回的序列末行是上一交易日 →
            #   报告会把昨天的收盘价/涨幅标成今天的（实测 20261103 事故：
            #   报告写上证 +1.08%，实际 -0.82%，据此写「放量上涨」完全颠倒）。
            try:
                from _index_backfill import backfill as _idx_backfill
                from _index_backfill import missing_index_dates as _idx_missing
                _idx_gaps = _idx_missing()
                if _idx_gaps:
                    _n = _idx_backfill(_idx_gaps)
                    print(f"[Index] 补入 {_n} 行（缺口: {_idx_gaps}）")
                else:
                    print("[Index] 指数缓存完整")
            except Exception as idx_error:
                print(f"[Index] 指数补齐异常（可能沿用旧数据）: {idx_error}")

            # ===== V10.0 市场环境判断（节点日识别 + 五状态机 + 动态仓位建议）=====
            # 与 V9.9 的关系：V10.0 只输出「环境状态 + 仓位空间」，
            # 不替代 V9.9 的仓位建议，也不介入任何个股买卖信号。
            try:
                from market_regime_v10 import (
                    analyze_trade_date as v10_analyze, render_v10_report as v10_render,
                    load_config as v10_load_cfg)
                v10_cfg = v10_load_cfg()
                v10_res = v10_analyze(trade_date, cfg=v10_cfg, persist=True)
                v10_text = v10_render(v10_res["snapshot"], v10_res["node"],
                                      v10_res["decision"], v10_res["advice"], v10_cfg)
                print(f"\n{v10_text}")
                v10_file = os.path.join(safe_cache_dir, f"market_regime_v10_{trade_date}.txt")
                try:
                    with open(v10_file, 'w', encoding='utf-8') as f:
                        f.write(v10_text)
                    print(f"[V10.0] 报告已保存: {v10_file}")
                except Exception as e:
                    print(f"[V10.0] 报告保存失败: {e}")
            except Exception as v10_error:
                print(f"[V10.0] 运行异常（不影响 V9.9 结果）: {v10_error}")
                import traceback
                traceback.print_exc()

            # ===== ETF 行情面板更新（主线判定的数据基础）=====
            # ★ 必须放在 V10.1 分析**之前**：主线模块用 etf_daily.db 的
            #   行业 nav 序列做相对强度/趋势判定。面板若有缺口，
            #   ret20/excess20 的实际跨度就不是 20 天，days_strong 回溯也会被打断。
            #   实测 2026-11-03：面板最新只到 10-30，缺 11-02，
            #   当日实时价接在 10-30 之后 → 少算了 11-02 一整天的涨跌。
            try:
                from _etf_backfill import backfill as _etf_backfill
                import etf_mainline as _etf_mod
                _etf_panel = _etf_mod.load_etf_panel()
                _etf_gaps = []
                try:
                    from _etf_backfill import missing_dates as _etf_missing
                    _etf_gaps = _etf_missing(_etf_panel)
                except Exception:
                    pass
                if _etf_gaps:
                    _n = _etf_backfill(_etf_gaps, _etf_panel)
                    print(f"[ETF] 补入 {_n} 行（缺口: {_etf_gaps}）")
                else:
                    print(f"[ETF] 面板完整（最新 {_etf_panel.trade_date.max()}）")
            except Exception as etf_error:
                print(f"[ETF] 面板更新异常（不影响主线既有判定）: {etf_error}")

            # ===== V10.1 动态仓位管理（S6强势主升 + 仓位权限 + 满仓授权 + 组合风险）=====
            # 与 V10.0 的关系：V10.1 在 V10.0 五状态机上叠加 S6，
            # 并把「市场状态 / 仓位权限 / 实际建议仓位」三件事彻底分开。
            # ★ 本模块只输出建议，不执行任何交易，不改动原有个股买卖信号。
            try:
                from market_regime_v101 import (
                    analyze_trade_date_v101 as v101_analyze,
                    render_v101_report as v101_render,
                    load_config as v101_load_cfg)
                v101_cfg = v101_load_cfg()
                v101_res = v101_analyze(trade_date, cfg=v101_cfg, persist=False)
                v101_text = v101_render(v101_res, v101_cfg)
                print(f"\n{v101_text}")
                v101_file = os.path.join(safe_cache_dir, f"market_regime_v101_{trade_date}.txt")
                try:
                    with open(v101_file, 'w', encoding='utf-8') as f:
                        f.write(v101_text)
                    print(f"[V10.1] 报告已保存: {v101_file}")
                except Exception as e:
                    print(f"[V10.1] 报告保存失败: {e}")
            except Exception as v101_error:
                print(f"[V10.1] 运行异常（不影响 V9.9/V10.0 结果）: {v101_error}")
                import traceback
                traceback.print_exc()

            # ===== V10.7 增量优化（量价口径 / S4卖压衰减确认 / S5反弹确认 / 结构性机会 / 仓位语义分离）=====
            # ★ 纯增量：内部完整复用 V10.0 + V10.1 的决策链路，
            #   只在其上叠加三层确认与状态门控，并保留 V10.1 原决策记录以便新旧对比。
            #   任何异常都不影响上面的 V9.9 / V10.0 / V10.1 结果。
            try:
                from market_regime_v107 import (
                    analyze_trade_date_v107 as v107_analyze,
                    render_v107_report as v107_render,
                    render_v107_summary as v107_summary,
                    load_config as v107_load_cfg)
                v107_cfg = v107_load_cfg()
                v107_res = v107_analyze(trade_date, cfg=v107_cfg, persist=False)
                v107_text = v107_render(v107_res, v107_cfg)
                print(f"\n{v107_text}")
                v107_file = os.path.join(safe_cache_dir, f"market_regime_v107_{trade_date}.txt")
                try:
                    with open(v107_file, 'w', encoding='utf-8') as f:
                        f.write(v107_text)
                    print(f"[V10.7] 报告已保存: {v107_file}")
                except Exception as e:
                    print(f"[V10.7] 报告保存失败: {e}")
                # 关键字段摘要（供日报/推送直接引用）
                try:
                    v107_sum = v107_summary(v107_res)
                    print("[V10.7] 关键结论：" + json.dumps(
                        {k: v107_sum[k] for k in (
                            "市场状态", "卖压衰减确认", "反弹确认", "结构性机会",
                            "最强行业", "行业持续性", "行业内部广度",
                            "主动试探权限", "建议仓位", "仓位硬上限", "满仓授权")},
                        ensure_ascii=False))
                    with open(os.path.join(safe_cache_dir,
                                           f"market_regime_v107_summary_{trade_date}.json"),
                              'w', encoding='utf-8') as f:
                        json.dump(v107_sum, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"[V10.7] 摘要生成失败: {e}")
            except Exception as v107_error:
                print(f"[V10.7] 运行异常（不影响 V9.9/V10.0/V10.1 结果）: {v107_error}")
                import traceback
                traceback.print_exc()

            # ===== 生成前日状态快照（供次日盘中连续计算使用）=====
            # 盘中分析需要「前一日」作为基准：广度 5/20 日均、状态机投票链、
            # 以及**昨日涨停股名单**（盘中算晋级率与强股次日表现的唯一来源）。
            # 从刚落盘的盘后报告提取，口径与收盘完全一致，且省掉盘中重算全表。
            try:
                import _prev_snapshot as _pvs
                _snap = _pvs.build(trade_date=str(trade_date), use_cache=False)
                print(f"[快照] 前日基准已生成: {trade_date}"
                      f"（涨停名单 {_snap.get('limit_up_count')} 只，"
                      f"广度序列 {len(_snap.get('breadth_series') or [])} 日）")
            except Exception as _snap_error:
                print(f"[快照] 生成失败（不影响其他结果）: {_snap_error}")
            
            # 保存报告（直接替换 market_analysis_yyyymmdd.txt）
            # ★ 兼容行：老引擎 save_result 写的也是这个文件名，但格式是
            #   「总体仓位建议: N%」；V9.9 覆盖后格式变成「当前目标: N%」，
            #   导致下游 tushare_quant.py / theme_alpha_engine.py 的正则
            #   `总体仓位建议:\s*(\d+)%` 抓不到 → 大盘仓位恒为 0。
            #   这里补一行兼容格式，让下游正则继续可用。
            _compat = ""
            try:
                _pos = int(v99_result.position_target)
                _compat = f"\n💡 总体仓位建议: {_pos}%\n"
            except Exception:
                pass
            v99_report_file = os.path.join(safe_cache_dir, f"market_analysis_{trade_date}.txt")
            try:
                with open(v99_report_file, 'w', encoding='utf-8') as f:
                    f.write(v99_report + _compat)
                print(f"[V9.9] 报告已保存: {v99_report_file}")
            except Exception as e:
                print(f"[V9.9] 保存报告失败: {e}")

            # 一致性检查结果
            checks = getattr(v99_result, 'consistency_checks', {})
            if checks:
                all_pass = all(v.startswith("PASS") for v in checks.values())
                print("")
                print("[V9.9] 一致性检查：%s" % ("✅ 全部通过" if all_pass else "❌ 存在失败"))
                for name, res in checks.items():
                    print("  %s：%s" % (name, res))
                
        except Exception as v99_error:
            print(f"[V9.9] 运行异常: {v99_error}")
            import traceback
            traceback.print_exc()
            import traceback
            traceback.print_exc()
        
        return results, position, reason, [], overview
    
    return None, 0, "", [], None

# ================
# 保存结果
# ================
def save_result(results, position, reason, style_allocations=None, overview=None, 
                trend_score=None, index_trend=None, theme_trend=None, market_status=None, trade_date=None,
                limit_stats=None, max_lb=0):
    if trade_date is None:
        trade_date = TRADE_DATE
    out_file = os.path.join(safe_cache_dir, f"market_analysis_{trade_date}.csv")
    df = pd.DataFrame(results)
    df.to_csv(out_file, index=False, encoding='utf-8-sig')
    print(f"\n✅ 结果已保存: {out_file}")
    
    # 简单保存文本
    txt_file = os.path.join(safe_cache_dir, f"market_analysis_{trade_date}.txt")
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("大盘分析与仓位建议\n")
        f.write("=" * 80 + "\n")
        # 大盘整体概况
        if overview:
            f.write(f"\n【大盘整体概况】\n")
            f.write(f"  上证指数: {overview['sh_index']:.2f}  ({overview['sh_pct']:+.2f}%)\n")
            f.write(f"  全市场成交额: {overview['total_amount']:.0f}亿\n")
            f.write(f"  上涨: {overview['up_count']}  下跌: {overview['down_count']}\n")
            f.write(f"  涨停: {overview['zt_count']}  跌停: {overview['dt_count']}  炸板: {overview['zb_count']}  炸板率: {overview['zb_rate']}%\n")
        
        # 指数趋势分
        f.write(f"\n【指数趋势分】\n")
        for r in results:
            f.write(f"  {r['name']}: {r['trend_status']} ({r['trend_score']:.1f})\n")
        
        # 市场趋势总评分
        if trend_score is not None:
            f.write(f"\n【市场趋势总评分】\n")
            f.write(f"  指数趋势 (IndexTrend): {index_trend:.1f}\n")
            f.write(f"  主题趋势 (ThemeTrend): {theme_trend:.1f}\n")
            f.write(f"  总趋势分 (TrendScore): {trend_score:.1f}\n")
            f.write(f"  市场状态: {market_status}\n")
        
        # 市场情绪
        f.write(f"\n【市场情绪】\n")
        for r in results:
            f.write(f"  {r['name']}: 结构分 {r['sentiment_score']:.1f}（{r['sentiment_status']}）"
                    f" | 当日热度 {r['heat_score']:.1f}（{r['heat_status']}）\n")
        
        f.write(f"\n总体仓位建议: {position}%\n")
        f.write(f"理由: {reason}\n")
        
        if style_allocations:
            f.write(f"\n风格个股仓位分配:\n")
            f.write(f"  {'指数':<12} {'风格':<12} {'状态':<12} {'建议仓位':<10} {'理由':<30}\n")
            f.write(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*10} {'-'*30}\n")
            for r, style, style_pos, style_reason in style_allocations:
                f.write(f"  {r['name']:<12} {style:<12} {r['trend_status']:<12} {style_pos:<10}% {style_reason:<30}\n")
        
        # 涨跌停 & 连板统计
        if limit_stats:
            f.write(f"\n【涨跌停 & 连板统计】\n")
            f.write(f"  涨停: {limit_stats['zt_count']}只 | 跌停: {limit_stats['dt_count']}只\n")
            f.write(f"  炸板: {limit_stats['zhaban_count']}只 | 炸板率: {limit_stats['broken_rate']:.1f}%\n")
            f.write(f"  最高连板: {max_lb}板\n")
            f.write(f"  上涨: {limit_stats['up_count']}只({limit_stats['up_ratio']}%)")
            f.write(f" | 下跌: {limit_stats['down_count']}只({limit_stats['down_ratio']}%)\n")

# ================
# 读取TOP3主题趋势分
# ================
# 主题热度 TOP3 均值 → 旧主题趋势分口径 的稳健线性折算。
# 样本：14 个交易日（20260724~20260923），锚点按 V2.3 口径的 TOP3 强度分布拟合。
# 旧口径 TOP3 均值 中位 59.9 / IQR 12.5；V2.3 TOP3 均值 中位 84.9 / IQR 6.8。直接替换会让
# ThemeTrend 抬高约 30 分（TrendScore 各占 50% → 抬高约 15 分），使 market_status 仓位档整体
# 上移，故按分布对齐折算。
# ⚠ 注意（20260927 下游统一切 V2.4）：本组锚点仍为 V2.3 分布拟合值。V2.4 的 Heat 合成口径与
#   成员分母均已变化，待 V2.4 历史累积后需按同样方法重拟合，否则折算存在系统性偏移。
V23_TOP3_MEDIAN, V23_TOP3_IQR = 84.9, 6.8
LEGACY_TOP3_MEDIAN, LEGACY_TOP3_IQR = 59.9, 12.5


def _v23_top3_to_legacy(scores):
    """把 V2.3 的 TOP3 强度折算到旧主题趋势分口径（保持 ThemeTrend 的分布与波动不变）"""
    k = LEGACY_TOP3_IQR / V23_TOP3_IQR
    avg = sum(scores) / len(scores)
    mapped = min(100.0, max(0.0, LEGACY_TOP3_MEDIAN + (avg - V23_TOP3_MEDIAN) * k))
    return [round(min(100.0, max(0.0, s - avg + mapped)), 2) for s in scores]


def get_top3_theme_scores(trade_date=None):
    """
    从主题系统生成的结果中读取 TOP3 主题强度
    数据源优先级：
      1) 主题热度 V2.4：report_daily/theme_heat_v24_{date}.json
         （TOP3 = TODAY+WEEK 综合 Heat，即主题强度唯一来源）
      2) report_daily/theme_scores.db      （theme_score_v2 每日产出，旧源兜底）
      3) cache_backbone_tushare/theme_trend_sentiment.db （旧引擎兜底）
      4) theme_trend_sentiment.csv          （最后兜底，无日期过滤）
    返回: [score1, score2, score3] 或 None
    """
    if trade_date is None:
        trade_date = TRADE_DATE

    # 优先：主题热度 V2.4 的 TOP3（TODAY+WEEK 综合强度）
    try:
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)
        import theme_heat_v22 as theme_heat
        top = theme_heat.top_heat(3, trade_date)
        if top:
            scores = _v23_top3_to_legacy([s for _, s in top])
            print(f"[Theme] 从主题热度V2.4读取 {trade_date} TOP3: "
                  + "、".join(f"{t}{s:.1f}" for t, s in top)
                  + f" → 折算旧口径 {[round(x, 1) for x in scores]}")
            return scores
        print(f"[Theme] 主题热度V2.4 无 {trade_date} 结果，回退旧主题库")
    except Exception as e:
        print(f"[Theme] 主题热度V2.4 读取失败: {e}，回退旧主题库")

    # 兜底1：theme_score_v2 产出的最新主题库（report_daily/theme_scores.db）
    v2_db = os.path.join(BASE_DIR, "report_daily", "theme_scores.db")
    if os.path.exists(v2_db):
        try:
            conn = sqlite3.connect(v2_db)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT trend_score FROM theme_scores 
                WHERE trade_date = ? 
                ORDER BY rank ASC 
                LIMIT 3
            ''', (trade_date,))
            rows = cursor.fetchall()
            conn.close()
            if rows:
                scores = [row[0] for row in rows]
                print(f"[Theme] 从最新主题库读取 {trade_date} TOP3主题趋势分: {scores}")
                return scores
        except Exception as e:
            print(f"[Theme] 从最新主题库读取主题评分失败: {e}")
        print(f"[Theme] 最新主题库无 {trade_date} 记录，尝试旧引擎兜底")

    # 兜底1：旧引擎 theme_trend_sentiment.db（可能停更，用于向后兼容）
    theme_db = os.path.join(safe_cache_dir, "theme_trend_sentiment.db")
    if os.path.exists(theme_db):
        try:
            conn = sqlite3.connect(theme_db)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT trend_score FROM theme_scores 
                WHERE trade_date = ? 
                ORDER BY rank ASC 
                LIMIT 3
            ''', (trade_date,))
            rows = cursor.fetchall()
            conn.close()
            if rows:
                scores = [row[0] for row in rows]
                print(f"[Theme] 从数据库读取 {trade_date} TOP3主题趋势分: {scores}")
                return scores
        except Exception as e:
            print(f"[Theme] 从数据库读取主题评分失败: {e}")
    
    # 如果没有，尝试从 CSV 读取最新数据
    theme_csv = os.path.join(safe_cache_dir, "theme_trend_sentiment.csv")
    if os.path.exists(theme_csv):
        try:
            df = pd.read_csv(theme_csv, encoding='utf-8-sig')
            if not df.empty and 'trend_score' in df.columns:
                # 按 rank 排序，取前3个
                df = df.sort_values('rank').head(3)
                scores = df['trend_score'].tolist()
                if scores:
                    print(f"[Theme] 从 CSV 读取 TOP3主题趋势分: {scores}")
                    return scores
        except Exception as e:
            print(f"[Theme] 读取主题评分失败: {e}")
    
    print("[Theme] 未找到主题评分数据")
    return None


# ================
# 计算市场趋势总评分（新评分体系）
# ================
def calculate_market_trend_score(
    index_results,
    theme_top3_scores = None
):
    """
    计算市场趋势总评分：
    IndexTrend = sh_score * 0.5 + hs300_score * 0.3 + cyb_score * 0.2
    ThemeTrend = TOP3主题平均分
    TrendScore = IndexTrend * 0.5 + ThemeTrend * 0.5
    
    涨跌幅惩罚：主要指数大跌时适当扣分
    情绪惩罚：情绪退潮扣分
    """
    # 提取指数趋势分
    sh_score = 0
    hs300_score = 0
    zz2000_score = 0
    
    for r in index_results:
        if r['name'] == '上证指数':
            sh_score = r['trend_score']
        elif r['name'] == '沪深300':
            hs300_score = r['trend_score']
        elif r['name'] == '中证2000':
            zz2000_score = r['trend_score']
    
    # 计算指数趋势分
    index_trend = sh_score * 0.5 + hs300_score * 0.3 + zz2000_score * 0.2
    
    # 计算主题趋势分（TOP3主题平均分）
    if theme_top3_scores and len(theme_top3_scores) >= 3:
        theme_trend = sum(theme_top3_scores[:3]) / 3
    elif theme_top3_scores:
        theme_trend = sum(theme_top3_scores) / len(theme_top3_scores)
    else:
        theme_trend = index_trend * 0.8
    
    # 计算总趋势分（指数和主题各占一半）
    trend_score = index_trend * 0.5 + theme_trend * 0.5
    
    # 涨跌幅惩罚：主要指数大跌时扣分（适度）
    for r in index_results:
        pct_chg = r.get('pct_chg', 0)
        if pct_chg < -1.0 and r['name'] == '上证指数':
            penalty = abs(pct_chg) * 3  # 跌1%扣3分
            trend_score -= penalty
            print(f"  [惩罚] {r['name']}跌{pct_chg:.1f}%，扣{penalty:.0f}分")
        if pct_chg < -3.0:
            extra_penalty = abs(pct_chg) * 2
            trend_score -= extra_penalty
            print(f"  [惩罚] {r['name']}大跌{pct_chg:.1f}%，额外扣{extra_penalty:.0f}分")
    
    # 情绪惩罚：情绪退潮扣分
    sentiment_statuses = [r.get('sentiment_status', '') for r in index_results]
    if any(s == '情绪退潮' for s in sentiment_statuses):
        trend_score -= 5
        print(f"  [惩罚] 市场情绪退潮，扣5分")
    
    # 限制在 0-100 范围内
    trend_score = min(100, max(0, trend_score))
    
    return trend_score, index_trend, theme_trend


def get_market_status_and_position(trend_score, prev_position=None, sentiment_score=None, breadth_up_ratio=None):
    """
    带滞回的仓位调整：
    - 升仓需超过区间中值+3分才升仓
    - 降仓需低于区间中值-3分才降仓
    - 避免在边界附近频繁切换
    """
    tiers = [
        (80, "强趋势",   "70~80%", 75),
        (70, "趋势良好", "55~70%", 60),
        (60, "震荡偏强", "40~60%", 50),
        (50, "震荡",     "30~50%", 40),
        (40, "弱势",     "20~30%", 25),
        (35, "退潮",     "5~15%",  10),
        (0,  "主跌段",   "0~5%",   0),
    ]
    
    new_tier_idx = 0
    for i, (threshold, _status, _pos_range, pos) in enumerate(tiers):
        if trend_score >= threshold:
            new_tier_idx = i
            break
    
    # 滞回机制：如果有前一日仓位，在非极端区域避免频繁切换
    if prev_position is not None and 30 < trend_score < 80:
        prev_tier_idx = _position_to_tier_idx(prev_position, tiers)
        if abs(new_tier_idx - prev_tier_idx) == 0:
            new_tier_idx = prev_tier_idx
        elif abs(new_tier_idx - prev_tier_idx) == 1:
            threshold = tiers[min(new_tier_idx, prev_tier_idx)][0]
            if abs(trend_score - threshold) <= 3:
                new_tier_idx = prev_tier_idx
    
    # 单日仓位变化上限：±15%（防止暴跌后单日反弹导致仓位跳变）
    if prev_position is not None:
        raw_position = tiers[new_tier_idx][3]
        max_change = 15
        if raw_position > prev_position + max_change:
            # 加仓受限：找不超过 prev+15 的最高档位
            target = prev_position + max_change
            for i, (_th, _st, _pr, pos) in enumerate(tiers):
                if pos <= target:
                    new_tier_idx = i
                    break
        # 降仓不受限（该跑就跑）
    
    status = tiers[new_tier_idx][1]
    position_range = tiers[new_tier_idx][2]
    position = tiers[new_tier_idx][3]
    return status, position_range, position


def _position_to_tier_idx(position, tiers):
    """将仓位百分比映射到档位索引"""
    for i, (_threshold, _status, _pos_range, pos) in enumerate(tiers):
        if position >= pos - 5:
            return i
    return len(tiers) - 1


def classify_market_regime(trend_score, sentiment_score, breadth_up_ratio=None, zhaban_rate=None,
                           prev_trend_score=None):
    """
    市场状态分类（指导持仓结构而非仅仓位）：
    1. 主升加速期：趋势>=75 + 情绪>=70
    2. 震荡轮动期：趋势50-75 + 情绪40-60
    3. 顶部分歧期：趋势>=60 但 情绪<40 或 炸板率>35%
    4. 主跌退潮期：趋势<40 + 情绪<30
    5. 冰点反弹期：趋势30-45 + 情绪<20
    
    趋势确认机制：从前一日主跌/冰点状态反弹时，需连续2日趋势分回升才确认状态升级，
    防止单日反弹被误判为趋势反转。
    """
    # 趋势确认：如果前日趋势分极低（<30），今日即使反弹也不能直接跳到"震荡轮动"
    if prev_trend_score is not None and prev_trend_score < 30:
        if trend_score >= 50:
            # 前日主跌，今日反弹到50+，但仍需确认，暂定为"冰点反弹期"
            return "冰点反弹期", f"前日趋势分{prev_trend_score:.0f}极低，今日反弹至{trend_score:.0f}但仍需确认，试探性建仓严格止损"
        elif trend_score >= 40:
            return "冰点反弹期", f"前日趋势分{prev_trend_score:.0f}，今日反弹至{trend_score:.0f}，情绪修复中但仍需确认"
    
    if trend_score >= 60 and sentiment_score is not None and sentiment_score < 40:
        return "顶部分歧期", "趋势在但情绪骤降，减仓兑现，切换防御品种"
    if trend_score >= 60 and zhaban_rate is not None and zhaban_rate > 35:
        return "顶部分歧期", "炸板率过高，情绪分歧严重，减仓兑现"
    if trend_score >= 75 and sentiment_score is not None and sentiment_score >= 70:
        return "主升加速期", "趋势与情绪共振，重仓主线龙头，可追高连板"
    if 50 <= trend_score < 75:
        return "震荡轮动期", "结构性行情为主，高抛低吸，快进快出"
    if 30 <= trend_score < 45 and sentiment_score is not None and sentiment_score < 25:
        return "冰点反弹期", "情绪冰点，试探性建仓超跌反弹，严格止损"
    if trend_score < 35 and sentiment_score is not None and sentiment_score < 30:
        return "主跌退潮期", "趋势与情绪双弱，空仓等待"
    if trend_score >= 45:
        return "震荡轮动期", "结构性行情为主，高抛低吸"
    return "主跌退潮期", "市场偏弱，空仓或极轻仓等待"


def suggest_portfolio_structure(market_regime):
    """根据市场状态给出持仓结构建议"""
    structure_map = {
        "主升加速期": {
            "集中度": "集中持仓3-5只",
            "持有周期": "短线为主，3-5日",
            "止损幅度": "5-8%（趋势加速期容错高）",
            "选股偏好": "追强主线龙头，打板/半路",
            "操作要点": "敢于追高，顺势加仓，不轻易止盈",
        },
        "震荡轮动期": {
            "集中度": "分散持仓5-8只",
            "持有周期": "T+1或T+2为主",
            "止损幅度": "3%（严格执行）",
            "选股偏好": "低吸回流，避免追高",
            "操作要点": "高抛低吸，控制换手率，不恋战",
        },
        "顶部分歧期": {
            "集中度": "减至2-3只",
            "持有周期": "T+1，快进快出",
            "止损幅度": "2-3%（收紧止损）",
            "选股偏好": "切换到防御性品种/低位补涨",
            "操作要点": "兑现利润，不追高，防范突然杀跌",
        },
        "主跌退潮期": {
            "集中度": "空仓或仅1-2只",
            "持有周期": "不参与或日内了结",
            "止损幅度": "不适用（不参与）",
            "选股偏好": "仅做超跌反弹，严格止损",
            "操作要点": "管住手，等待情绪冰点和趋势企稳",
        },
        "冰点反弹期": {
            "集中度": "试探2-3只",
            "持有周期": "1-3日反弹",
            "止损幅度": "3-5%",
            "选股偏好": "超跌+缩量到位+首板",
            "操作要点": "小仓位试探，确认反弹再加仓",
        },
    }
    return structure_map.get(market_regime, {
        "集中度": "3-5只",
        "持有周期": "短线",
        "止损幅度": "3%",
        "选股偏好": "中性",
        "操作要点": "观望为主",
    })


# ================
# SQLite数据库操作
# ================
def init_database():
    """初始化数据库表"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建指数分析表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS index_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            index_name TEXT NOT NULL,
            index_code TEXT NOT NULL,
            trend_score REAL,
            trend_status TEXT,
            sentiment_score REAL,
            sentiment_status TEXT,
            close_price REAL,
            pct_change REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_date, index_code)
        )
    ''')
    
    # 创建总体分析表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS overall_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            total_position INTEGER,
            position_reason TEXT,
            trend_score REAL,
            index_trend REAL,
            theme_trend REAL,
            market_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建涨跌停统计表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS limit_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            zt_count INTEGER DEFAULT 0,
            dt_count INTEGER DEFAULT 0,
            broken_rate REAL DEFAULT 0.0,
            zhaban_count INTEGER DEFAULT 0,
            max_limit_height INTEGER DEFAULT 0,
            up_count INTEGER DEFAULT 0,
            down_count INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            up_ratio REAL DEFAULT 0.0,
            down_ratio REAL DEFAULT 0.0,
            updated TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 添加新列（如果已存在表但缺少新列）
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN trend_score REAL")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN index_trend REAL")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN theme_trend REAL")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN market_status TEXT")
    except:
        pass
    # ★ P0：区分记录来源引擎，避免 total_position(V9.9) 与 market_status(老引擎) 口径混合
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN engine_version TEXT")
    except:
        pass
    # 老引擎原始仓位留档（V9.9 回写后仍可追溯）
    try:
        cursor.execute("ALTER TABLE overall_analysis ADD COLUMN legacy_position INTEGER")
    except:
        pass

    conn.commit()
    conn.close()
    return db_path

def save_to_database(trade_date, results, total_position, position_reason,
                     trend_score=None, index_trend=None, theme_trend=None, market_status=None):
    """保存分析结果到数据库

    ★ P0：写入前校验 trade_date 是否为交易日 —— 非交易日记录会让
    「前一交易日」取值、连续回升天数等逻辑出现错位基准（如历史的 20260619）。
    """
    # ★ P0：非交易日直接拒绝写入
    if not is_trading_day(trade_date):
        print(f"❌ [交易日校验] {trade_date} 不是交易日，跳过写入（避免污染基准）")
        return

    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 保存各指数分析
        for r in results:
            cursor.execute('''
                INSERT OR REPLACE INTO index_analysis
                (trade_date, index_name, index_code, trend_score, trend_status,
                 sentiment_score, sentiment_status, close_price, pct_change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                r['name'],
                r['code'],
                r['trend_score'],
                r['trend_status'],
                r['sentiment_score'],
                r['sentiment_status'],
                r['close'],
                r['pct_chg']
            ))

        # 保存总体分析
        # engine_version='legacy'：此时 total_position 仍是老引擎仓位，
        # 随后由 V9.9 回写整行（含 engine_version='V9.9'）
        cursor.execute('''
            INSERT OR REPLACE INTO overall_analysis
            (trade_date, total_position, position_reason, trend_score, index_trend,
             theme_trend, market_status, engine_version, legacy_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (trade_date, total_position, position_reason, trend_score, index_trend,
              theme_trend, market_status, 'legacy', total_position))

        conn.commit()
        print(f"\n✅ 数据已保存到数据库: {db_path}")
    except Exception as e:
        print(f"\n❌ 数据库保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def save_limit_stats_to_database(data):
    """保存涨跌停统计数据到数据库（供 realtime_theme_monitor.py 调用）

    ★ P0：写入前校验 trade_date 是否为交易日。"""
    td = data.get('trade_date', TRADE_DATE)
    if not is_trading_day(td):
        print(f"❌ [交易日校验] {td} 不是交易日，跳过 limit_stats 写入")
        return
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO limit_stats 
            (trade_date, zt_count, dt_count, broken_rate, zhaban_count, max_limit_height,
             up_count, down_count, total, up_ratio, down_ratio, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('trade_date', TRADE_DATE),
            data.get('zt_count', 0),
            data.get('dt_count', 0),
            data.get('broken_rate', 0.0),
            data.get('zhaban_count', 0),
            data.get('max_limit_height', 0),
            data.get('up_count', 0),
            data.get('down_count', 0),
            data.get('total', 0),
            data.get('up_ratio', 0.0),
            data.get('down_ratio', 0.0),
            data.get('updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        ))
        conn.commit()
        print(f"[涨跌停DB] 已保存至 limit_stats: {data.get('trade_date')}")
    except Exception as e:
        print(f"[涨跌停DB] 保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def get_historical_data(days=30):
    """获取历史数据分析"""
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    
    # 获取各指数历史数据
    query = '''
        SELECT trade_date, index_name, trend_score, trend_status, 
               sentiment_score, sentiment_status, close_price, pct_change
        FROM index_analysis
        ORDER BY trade_date DESC
        LIMIT ?
    '''
    df = pd.read_sql_query(query, conn, params=(days * 3,))
    
    conn.close()
    return df

def check_consecutive_sentiment(index_name, sentiment_type, consecutive_days=3):
    """
    检查连续情绪状态
    sentiment_type: 'high' (高情绪) or 'low' (低情绪)
    """
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 获取最近N天的数据
    cursor.execute('''
        SELECT sentiment_status, sentiment_score, trade_date
        FROM index_analysis
        WHERE index_name = ?
        ORDER BY trade_date DESC
        LIMIT ?
    ''', (index_name, consecutive_days))
    
    rows = cursor.fetchall()
    conn.close()
    
    if len(rows) < consecutive_days:
        return None
    
    # 检查条件
    if sentiment_type == 'high':
        # 高情绪：情绪高涨或分数>=70
        is_high = all(
            (row[0] == '情绪高涨' or row[1] >= 70) 
            for row in rows
        )
        if is_high:
            return {
                'type': 'high',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    elif sentiment_type == 'low':
        # 低情绪：情绪退潮或分数<=30
        is_low = all(
            (row[0] == '情绪退潮' or row[1] <= 30) 
            for row in rows
        )
        if is_low:
            return {
                'type': 'low',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    
    return None

def check_consecutive_trend(index_name, trend_type, consecutive_days=3):
    """
    检查连续趋势状态
    trend_type: 'up' (上升) or 'down' (下降)
    """
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT trend_status, trend_score, trade_date
        FROM index_analysis
        WHERE index_name = ?
        ORDER BY trade_date DESC
        LIMIT ?
    ''', (index_name, consecutive_days))
    
    rows = cursor.fetchall()
    conn.close()
    
    if len(rows) < consecutive_days:
        return None
    
    if trend_type == 'up':
        is_up = all(row[0] in ['上升趋势', '震荡偏强'] for row in rows)
        if is_up:
            return {
                'type': 'up',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    elif trend_type == 'down':
        is_down = all(row[0] in ['下降趋势', '震荡偏弱'] for row in rows)
        if is_down:
            return {
                'type': 'down',
                'index': index_name,
                'days': consecutive_days,
                'start_date': rows[-1][2],
                'end_date': rows[0][2],
                'scores': [row[1] for row in reversed(rows)]
            }
    
    return None

def analyze_sentiment_trend_alerts():
    """分析并返回情绪和趋势的提醒"""
    alerts = []
    indices = ['沪深300', '上证指数', '中证2000']
    
    for idx in indices:
        # 检查连续高情绪
        high_sent = check_consecutive_sentiment(idx, 'high', 3)
        if high_sent:
            alerts.append({
                'category': '情绪',
                'level': '⚠️',
                'message': f"{idx}已连续{high_sent['days']}天高情绪（最近分数：{high_sent['scores']}），注意情绪过热"
            })
        
        # 检查连续低情绪
        low_sent = check_consecutive_sentiment(idx, 'low', 3)
        if low_sent:
            alerts.append({
                'category': '情绪',
                'level': '💡',
                'message': f"{idx}已连续{low_sent['days']}天低情绪（最近分数：{low_sent['scores']}），可能是情绪冰点"
            })
        
        # 检查连续上升趋势
        up_trend = check_consecutive_trend(idx, 'up', 3)
        if up_trend:
            alerts.append({
                'category': '趋势',
                'level': '🚀',
                'message': f"{idx}已连续{up_trend['days']}天强势（最近分数：{up_trend['scores']}）"
            })
        
        # 检查连续下降趋势
        down_trend = check_consecutive_trend(idx, 'down', 3)
        if down_trend:
            alerts.append({
                'category': '趋势',
                'level': '📉',
                'message': f"{idx}已连续{down_trend['days']}天弱势（最近分数：{down_trend['scores']}）"
            })
    
    return alerts


def get_limit_up_down_stats(trade_date=None):
    """
    获取涨跌停数据和炸板率（移植自 tushare_quant.py）
    返回: {zt_count, dt_count, zt_codes, dt_codes, broken_rate, zhaban_count}
    """
    if trade_date is None:
        trade_date = TRADE_DATE
    
    zt_codes = []
    dt_codes = []
    broken_rate = 0.0
    zhaban_count = 0
    
    try:
        # 方法1：使用每日行情数据计算真实的涨跌停（收盘价）— V3: daily_cache 表需完整性校验
        try:
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            daily = None
            if daily_cache_complete(trade_date):
                daily = get_daily_by_date(trade_date)
            if daily is None or daily.empty:
                daily = pro.daily(trade_date=trade_date)
                if daily is not None and not daily.empty:
                    try:
                        batch_insert_daily_cache(daily)
                    except Exception:
                        pass
        except Exception:
            daily = None
            try:
                from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
                if daily_cache_complete(trade_date):
                    daily = get_daily_by_date(trade_date)
            except Exception:
                pass
            if daily is None or daily.empty:
                daily = pro.daily(trade_date=trade_date)
                if daily is not None and not daily.empty:
                    try:
                        from stock_cache import batch_insert_daily_cache
                        batch_insert_daily_cache(daily)
                    except Exception:
                        pass
        if daily is not None and not daily.empty:
            # 计算涨跌停阈值（简化版：主板10%，科创板/创业板20%）
            daily['is_kcb'] = daily['ts_code'].str.startswith(('688', '301'))
            daily['is_cn'] = daily['ts_code'].str.startswith('300')
            daily['limit_up'] = daily.apply(
                lambda x: 20.0 if x['is_kcb'] or x['is_cn'] else 10.0, axis=1
            )
            daily['limit_down'] = -daily['limit_up']
            
            # 真实涨停：收盘价涨幅接近涨停价（>=99%的涨停幅度）
            zt_mask = (daily['pct_chg'] >= daily['limit_up'] * 0.99) & (daily['pct_chg'] < daily['limit_up'] + 0.1)
            # 真实跌停：收盘价跌幅接近跌停价
            dt_mask = (daily['pct_chg'] <= daily['limit_down'] * 0.99) & (daily['pct_chg'] > daily['limit_down'] - 0.1)
            
            zt_codes = daily[zt_mask]['ts_code'].tolist()
            dt_codes = daily[dt_mask]['ts_code'].tolist()
            
            print(f"[涨跌停] 涨停(真实收盘): {len(zt_codes)}只")
            print(f"[涨跌停] 跌停(真实收盘): {len(dt_codes)}只")
            
            # 获取炸板数据（盘中触及涨停但未封住）
            try:
                limit_df = pro.limit_list_d(trade_date=trade_date)
                if limit_df is not None and not limit_df.empty:
                    # limit='D'表示最终封住, limit='Z'表示炸板
                    zhaban_codes = limit_df[limit_df['limit'] == 'Z']['ts_code'].astype(str).tolist()
                    zhaban_count = len(zhaban_codes)
                    
                    # 炸板率 = 炸板数 ÷ (封住数 + 炸板数)
                    total_touch = len(zt_codes) + zhaban_count
                    if total_touch > 0:
                        broken_rate = (zhaban_count / total_touch) * 100
                    print(f"[涨跌停] 炸板率: {broken_rate:.1f}% (炸板{zhaban_count}只/触及涨停{total_touch}只)")
            except Exception as e:
                print(f"[涨跌停] 获取炸板数据失败: {e}")
        
        # 如果以上方法都失败，使用limit_list_d官方接口作为备选
        if not zt_codes and not dt_codes:
            print("[涨跌停] 使用limit_list_d官方接口备选...")
            try:
                d_zt = pro.limit_list_d(trade_date=trade_date, limit_type='U')
                if d_zt is not None and not d_zt.empty:
                    zt_codes = d_zt['ts_code'].astype(str).tolist()
                    print(f"[涨跌停] 涨停(limit_list_d备选): {len(zt_codes)}只")
            except Exception as e:
                print(f"[涨跌停] limit_list_d涨停失败: {e}")
            
            try:
                d_dt = pro.limit_list_d(trade_date=trade_date, limit_type='D')
                if d_dt is not None and not d_dt.empty:
                    dt_codes = d_dt['ts_code'].astype(str).tolist()
                    print(f"[涨跌停] 跌停(limit_list_d备选): {len(dt_codes)}只")
            except Exception as e:
                print(f"[涨跌停] limit_list_d跌停失败: {e}")
    
    except Exception as e:
        print(f"[涨跌停] 获取失败: {e}")
    
    # 获取涨跌比例数据
    up_count = 0
    down_count = 0
    total_count = 0
    up_ratio = 0.0
    down_ratio = 0.0
    
    try:
        # V3: daily_cache 表需完整性校验
        try:
            from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
            daily = None
            if daily_cache_complete(trade_date):
                daily = get_daily_by_date(trade_date)
            if daily is None or daily.empty:
                daily = pro.daily(trade_date=trade_date)
                if daily is not None and not daily.empty:
                    try:
                        batch_insert_daily_cache(daily)
                    except Exception:
                        pass
        except Exception:
            daily = None
            try:
                from stock_cache import get_daily_by_date, get_daily_by_date_count, batch_insert_daily_cache
                if daily_cache_complete(trade_date):
                    daily = get_daily_by_date(trade_date)
            except Exception:
                pass
            if daily is None or daily.empty:
                daily = pro.daily(trade_date=trade_date)
                if daily is not None and not daily.empty:
                    try:
                        from stock_cache import batch_insert_daily_cache
                        batch_insert_daily_cache(daily)
                    except Exception:
                        pass
        if daily is not None and not daily.empty:
            up_count = int((daily['pct_chg'] > 0).sum())
            down_count = int((daily['pct_chg'] < 0).sum())
            total_count = len(daily)
            if total_count > 0:
                up_ratio = round(up_count / total_count * 100, 1)
                down_ratio = round(down_count / total_count * 100, 1)
    except Exception as e:
        print(f"[涨跌比例] 获取失败: {e}")
    
    result = {
        "zt_count": len(zt_codes),
        "dt_count": len(dt_codes),
        "zt_codes": zt_codes,
        "dt_codes": dt_codes,
        "broken_rate": round(broken_rate, 1),
        "zhaban_count": zhaban_count,
        "trade_date": trade_date,
        "updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # 新增涨跌比例数据
        "up_count": up_count,
        "down_count": down_count,
        "total": total_count,
        "up_ratio": up_ratio,
        "down_ratio": down_ratio
    }
    
    # 保存到缓存文件（供实时监控读取）
    save_limit_stats_to_cache(result)
    
    return result


def calc_max_limit_height(trade_date=None):
    """计算最高连板高度（移植自 tushare_quant.py）"""
    if trade_date is None:
        trade_date = TRADE_DATE
    
    try:
        # 优先使用 akshare 免费接口
        import akshare as ak
        try:
            zt_df = ak.stock_zt_pool_em(date=trade_date)
            if zt_df is not None and not zt_df.empty:
                if '连板数' in zt_df.columns:
                    zt_df['连板数'] = pd.to_numeric(zt_df['连板数'], errors='coerce').fillna(1).astype(int)
                    max_lb = int(zt_df['连板数'].max())
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return max_lb
                elif '连扳数' in zt_df.columns:
                    zt_df['连扳数'] = pd.to_numeric(zt_df['连扳数'], errors='coerce').fillna(1).astype(int)
                    max_lb = int(zt_df['连扳数'].max())
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return max_lb
        except Exception as ak_error:
            print(f"[连板高度] akshare获取失败: {ak_error}")
        
        # akshare失败时，尝试 tushare pro 接口
        if pro is not None:
            zt_df = pro.limit_step(trade_date=trade_date)
            if zt_df is not None and not zt_df.empty:
                if 'nums' in zt_df.columns:
                    max_lb = int(zt_df['nums'].max())
                    print(f"[连板高度] tushare limit_step: 最高连板 {max_lb} 板")
                    return max_lb
    except Exception as e:
        print(f"[连板高度] 计算失败: {e}")
    
    return 0


def save_limit_stats_to_cache(data):
    """将涨跌停统计数据保存到缓存文件"""
    import json
    
    # 保存每日统计文件
    daily_cache_file = os.path.join(BASE_DIR, 'cache_daily', f'full_market_stats_{data["trade_date"]}.json')
    os.makedirs(os.path.dirname(daily_cache_file), exist_ok=True)
    
    try:
        with open(daily_cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[涨跌停] 缓存已保存: {daily_cache_file}")
    except Exception as e:
        print(f"[涨跌停] 保存缓存失败: {e}")


# ============================================================
# V9.0 仓位引擎集成
# ============================================================

def run_v9_position_engine_v9(
    overview: dict,
    limit_stats: dict,
    max_limit_height: int,
    theme_top3_scores: list = None,
    trade_date: str = None,
    prev_position: float = None,
    hist_limit: pd.DataFrame = None,
    index_data_dict: dict = None
) -> tuple:
    """
    运行 V9.0 动态仓位引擎
    
    Args:
        overview: 市场概况字典
        index_results: 指数分析结果列表
        limit_stats: 涨跌停统计
        max_limit_height: 最高连板高度
        theme_top3_scores: TOP3 主题评分
        trade_date: 交易日
        prev_position: 前一日推荐仓位（用于滞回）
        hist_limit: 历史涨跌停数据
        index_data_dict: 指数原始K线数据 {name: DataFrame}
    
    Returns:
        (v9_result, v9_report_text)
    """
    try:
        from position_engine_v9 import PositionEngineV9, MarketDataBundle
    except ImportError as e:
        print(f"[V9引擎] 导入失败: {e}")
        return None, ""
    
    data = MarketDataBundle()
    
    # 1. 指数数据
    if index_data_dict:
        data.index_data = index_data_dict
    else:
        # 如果没有原始K线，从 index_results 中尽可能提取（信息有限）
        pass
    
    # 2. 市场概况
    data.overview = overview or {}
    
    # 3. 涨跌停统计
    data.limit_stats = limit_stats or {}
    
    # 4. 连板高度
    data.max_limit_height = max_limit_height or 0
    
    # 5. 主题数据
    data.theme_top3_scores = theme_top3_scores or []
    
    # 6. 历史数据
    data.hist_limit = hist_limit
    
    # 7. 前一日结果（用于滞回和状态迁移）
    if prev_position is not None:
        from position_engine_v9 import EngineV9Result as _EVR
        prev = _EVR()
        prev.position.recommended_position = float(prev_position)
        # 从历史数据中尝试获取 prev_regime
        if hist_limit is not None and len(hist_limit) >= 2:
            pass  # 暂不深追，先用 UNKNOWN
        data.prev_result = prev
    
    # 8. 组合回撤（暂时没有，后续可扩展）
    data.portfolio_drawdown = 0.0
    
    # 运行引擎
    engine = PositionEngineV9()
    result = engine.analyze(data)
    result.trade_date = trade_date or ""
    
    # 生成报告
    report = engine.generate_report(result)
    
    return result, report


def _build_index_data_dict_for_v9(trade_date: str = None) -> dict:
    """
    为 V9 引擎构建指数数据字典
    复用 get_index_kline 获取六大指数K线
    """
    v9_indices = [
        ("上证指数", "000001.SH"),
        ("沪深300", "000300.SH"),
        ("中证1000", "000852.SH"),
        ("中证2000", "932000.CSI"),
        ("创业板指", "399006.SZ"),
        ("科创50", "000688.SH"),
    ]
    
    index_data = {}
    for name, code in v9_indices:
        try:
            df = get_index_kline(code, trade_date)
            if df is not None and not df.empty:
                index_data[name] = df
        except Exception as e:
            print(f"[V9引擎] 获取{name}数据失败: {e}")
    
    return index_data


if __name__ == '__main__':
    # 初始化数据库
    init_database()

    # ★ P0：清理历史遗留的非交易日记录（如 20260619），避免基准错位
    cleanup_invalid_trade_dates()

    # 运行分析（analyze_market 内部已完成所有计算和保存）
    result = analyze_market()
    
    if result and len(result) >= 4:
        results, total_position, position_reason, style_allocations, overview = result
        
        # 检查提醒
        print("\n" + "=" * 80)
        print("🔔 动态跟踪提醒")
        print("=" * 80)
        
        alerts = analyze_sentiment_trend_alerts()
        if alerts:
            for alert in alerts:
                print(f"\n{alert['level']} 【{alert['category']}】{alert['message']}")
        else:
            print("\n✅ 暂无特殊提醒")
    else:
        print("\n❌ 分析未完成")

