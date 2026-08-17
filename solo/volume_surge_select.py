"""
VSW（Volume Surge + Wide-swing）量能爆发+宽幅震荡选股程序 — VSW V2（独立版）

从 tushare_quant.py 提取的"量能爆发+宽幅震荡"选股策略：
像火星人/时代电气/奥比中光/沃顿科技那样的"近期量能大幅放大创历史新高量能，且区间股价宽幅震荡"。

复用缓存：
- SQLite daily_cache（stock_cache.py）— 日线行情
- market_{date}.csv — 全市场快照（市值/名称）
- theme_stock_map / theme_alpha_v6_result — 主题关联

用法：
  python volume_surge_select.py [YYYYMMDD] [--no-chip] [--simple]
"""
import os
import sys
import time
import json
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import tushare as ts

warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = r"d:\mystock"
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")

def _load_tushare_token():
    """环境变量优先, 回退读取 config/.env"""
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    env_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", ".env"),
    ]
    for env_path in env_candidates:
        env_path = os.path.normpath(env_path)
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TUSHARE_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        if token:
                            os.environ["TUSHARE_TOKEN"] = token
                            return token
    return ""


TUSHARE_TOKEN = _load_tushare_token()

pro = None
try:
    pro = ts.pro_api(TUSHARE_TOKEN)
except Exception as e:
    print(f"Token 设置失败: {e}")
    sys.exit(1)


def _get_df():
    """获取 DataFetcher 单例（不可用则返回 None，调用方降级到 pro）"""
    global _df_singleton
    if _df_singleton is not None:
        return _df_singleton
    try:
        from multi_factor_picker.data_fetcher import DataFetcher
    except Exception:
        return None
    try:
        token = os.getenv("TUSHARE_TOKEN") or TUSHARE_TOKEN
        if not token:
            return None
        config = {
            'cache': {'enabled': True, 'dir': os.path.join(BASE_DIR, 'multi_factor_picker', 'cache'), 'expire_hours': 168},
            'tushare': {'max_retry': 3, 'retry_delay': 5}
        }
        _df_singleton = DataFetcher(token, config)
    except Exception:
        return None
    return _df_singleton


_df_singleton = None


def _df_daily_by_code(ts_code, start_date=None, end_date=None, fields=None):
    """pro.daily(ts_code=...) 的 DataFetcher 优先版"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date, fields=fields)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    kw = {'ts_code': ts_code}
    if start_date is not None: kw['start_date'] = start_date
    if end_date is not None: kw['end_date'] = end_date
    if fields is not None: kw['fields'] = fields
    return pro.daily(**kw)


def _df_daily_by_date(trade_date):
    """pro.daily(trade_date=...) 的 DataFetcher 优先版"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_daily(trade_date)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    return pro.daily(trade_date=trade_date)


def _df_daily_basic_by_date(trade_date, fields=None):
    """pro.daily_basic(trade_date=...) 的 DataFetcher 优先版"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_daily_basic(trade_date)
            if r is not None and len(r) > 0:
                if fields:
                    cols = [c.strip() for c in fields.split(',') if c.strip() in r.columns]
                    if cols:
                        r = r[cols]
                return r
        except Exception:
            pass
    kw = {'trade_date': trade_date}
    if fields: kw['fields'] = fields
    return pro.daily_basic(**kw)


def _df_stock_list(list_status='L'):
    """pro.stock_basic(list_status=...) 的 DataFetcher 优先版"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_stock_list(list_status=list_status)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    return pro.stock_basic(exchange='', list_status=list_status)


def _df_trade_cal(start_date=None, end_date=None):
    """pro.trade_cal(...) 的 DataFetcher 优先版"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_trade_cal(start_date=start_date, end_date=end_date)
            if r is not None:
                return r
        except Exception:
            pass
    kw = {'exchange': ''}
    if start_date is not None: kw['start_date'] = start_date
    if end_date is not None: kw['end_date'] = end_date
    return pro.trade_cal(**kw)


# =========================
# 交易日
# =========================
def get_last_trade_date():
    """获取最近的交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    try:
        cal = _df_trade_cal(start_date='20200101', end_date=query_date)
        cal = cal[cal['is_open'] == 1]
        last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
        return str(last_trade_date)
    except Exception:
        return query_date


def validate_trade_date(date_str):
    """验证日期是否为有效交易日，如果不是则返回最近的有效交易日"""
    try:
        cal = _df_trade_cal(start_date=date_str, end_date=date_str)
        if not cal.empty and cal.iloc[0]['is_open'] == 1:
            return date_str
        cal = _df_trade_cal(
            start_date=(datetime.strptime(date_str, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d'),
            end_date=date_str
        )
        cal = cal[cal['is_open'] == 1]
        last_valid = cal[cal['cal_date'] <= date_str]['cal_date'].max()
        if last_valid:
            print(f"[警告] {date_str} 不是交易日，使用最近交易日: {last_valid}")
            return str(last_valid)
        return date_str
    except Exception as e:
        print(f"[警告] 日期验证失败: {e}，使用原日期: {date_str}")
        return date_str


TRADE_DATE = get_last_trade_date()


# =========================
# 大盘环境提示（三指数动量，仅作参考，不再硬性拦截）
# =========================
# 回测验证(2024-01~2026-08, 每日Top3, T+5, 止损-7%)按动量分组:
#   强市>+3%: 47.7%/+1.64% | 震荡偏强0~3%: 33.8%/-1.09% | 震荡偏弱-3~0%: 36.9%/-0.11% | 弱市<=-3%: 41.8%/-0.06%
# 闸门越严整体期望越高，但震荡期个股差异大，故仅作环境提示，由用户自行选择
INDEX_CODES_3 = ["000001.SH", "000300.SH", "399006.SZ"]
INDEX_NAMES_3 = {"000001.SH": "上证", "000300.SH": "沪深300", "399006.SZ": "创业板"}
MOM_GATE_THRESHOLD = 3.0


def _mom_env(avg):
    """按三指数20日动量均值返回环境档位 (label, 回测胜率参考)"""
    if avg > MOM_GATE_THRESHOLD:
        return "🟢 强市", "47.7%/+1.64%"
    if avg > 0:
        return "🟡 震荡偏强", "33.8%/-1.09%"
    if avg > -3:
        return "🟠 震荡偏弱", "36.9%/-0.11%"
    return "🔴 弱市", "41.8%/-0.06%"


def get_index_momentum(target_date=None):
    """三指数20日动量均值(%) 与各指数动量, 用于大盘环境提示.
    Returns: dict(date/mom20_avg/per/env/win_ref) 或 None(数据不足)
    """
    end = str(target_date or TRADE_DATE)
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
    per, dates = {}, {}
    for code in INDEX_CODES_3:
        try:
            df = pro.index_daily(ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            if len(df) < 21:
                continue
            close = df['close'].astype(float)
            per[code] = round(float(close.iloc[-1] / close.iloc[-21] - 1) * 100, 2)
            dates[code] = str(df['trade_date'].iloc[-1])
        except Exception as e:
            print(f"[Index] {code} 动量获取失败: {e}")
    if not per:
        return None
    avg = float(np.mean(list(per.values())))
    dset = set(dates.values())
    env, win_ref = _mom_env(avg)
    return {
        'date': dset.pop() if len(dset) == 1 else sorted(dset)[-1],
        'mom20_avg': round(avg, 2),
        'per': per,
        'env': env,
        'win_ref': win_ref,
        'gate': avg > MOM_GATE_THRESHOLD,
    }


def _print_market_tip(tip):
    """打印大盘环境提示"""
    s = " ".join(f"{INDEX_NAMES_3[c]}={tip['per'][c]:+.1f}%" for c in tip['per'])
    print(f"\n[大盘提示] {tip['date']} 三指数20日动量均值 {tip['mom20_avg']:+.1f}% ({s}) → {tip['env']}")
    print(f"           回测参考(T+5): {tip['win_ref']}")


# =========================
# 股票名称映射
# =========================
def load_stock_dict():
    """获取股票代码和名称映射（优先本地 stock_basic 缓存）"""
    try:
        from stock_cache import load_stock_basic
        sb = load_stock_basic()
        if sb is not None and 'ts_code' in sb.columns and 'name' in sb.columns:
            stock_dict = {}
            for _, row in sb.iterrows():
                stock_dict[str(row['symbol'])] = row['name']
                stock_dict[str(row['ts_code'])] = row['name']
            return stock_dict
    except Exception:
        pass
    try:
        df = _df_stock_list(list_status='L')
        stock_dict = {}
        for _, row in df.iterrows():
            stock_dict[str(row['symbol'])] = row['name']
            stock_dict[str(row['ts_code'])] = row['name']
        return stock_dict
    except Exception:
        return {}


STOCK_DICT = load_stock_dict()


def get_stock_name(code):
    return STOCK_DICT.get(code, code)


# =========================
# 历史数据（复用 SQLite daily_cache 缓存）
# =========================
def get_hist_data(ts_code):
    """获取单股历史日线数据（优先 SQLite daily_cache，缺失才调 API 并回写）"""
    try:
        from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
        _, max_date = get_daily_cache_range(ts_code)
        if max_date is not None and str(max_date) >= TRADE_DATE:
            df = get_daily_cache(ts_code, '20250101', TRADE_DATE)
            if df is not None and not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                return df[df['trade_date'] <= TRADE_DATE].sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    try:
        df = _df_daily_by_code(ts_code, start_date='20250101', end_date=TRADE_DATE)
        if df is None or df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)
        try:
            from stock_cache import batch_insert_daily_cache
            batch_insert_daily_cache(df)
        except Exception:
            pass
        time.sleep(0.15)
    except Exception as e:
        print(f"{ts_code} 下载失败:", e)
        return None
    return df


# =========================
# 批量预取历史数据（复用 SQLite daily_cache）
# =========================
def batch_prefetch_hist_data(codes, start_date='20250101'):
    """在主循环之前批量预取所有股票数据到本地缓存（V2: 统一用 SQLite daily_cache）"""
    if not codes:
        return
    from stock_cache import get_daily_cache_range, batch_insert_daily_cache

    cached = []
    missing = []
    for ts_code in codes:
        try:
            _, max_date = get_daily_cache_range(ts_code)
            if max_date is not None and str(max_date) >= TRADE_DATE:
                cached.append(ts_code)
                continue
        except Exception:
            pass
        missing.append(ts_code)

    print(f"  批量预取: 传入 {len(codes)} 只, 缓存命中 {len(cached)} / 仍需下载 {len(missing)}")
    if not missing:
        return

    batch_size = 20
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        try:
            ts_list = ",".join(batch)
            df = pro.daily(ts_code=ts_list, start_date=start_date, end_date=TRADE_DATE)
            if df is not None and not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                try:
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
                print(f"  批次 {i // batch_size + 1}: 成功下载 {df['ts_code'].nunique()}/{len(batch)} 只")
            else:
                print(f"  批次 {i // batch_size + 1}: 下载返回空")
            time.sleep(0.15)
        except Exception as e:
            print(f"  批次 {i // batch_size + 1} 下载失败: {e}")
            for ts_code in batch:
                try:
                    single_df = _df_daily_by_code(ts_code, start_date=start_date, end_date=TRADE_DATE)
                    if single_df is not None and not single_df.empty:
                        single_df['trade_date'] = single_df['trade_date'].astype(str)
                        try:
                            batch_insert_daily_cache(single_df)
                        except Exception:
                            pass
                    time.sleep(0.15)
                except Exception:
                    pass


# =========================
# 全市场快照（复用 market_{date}.csv 缓存）
# =========================
def get_market():
    cache_file = os.path.join(CACHE_DIR, f"market_{TRADE_DATE}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            if not df.empty:
                return df
        except Exception as e:
            print(f"[缓存] 市场数据读取失败: {e}")
    daily = _df_daily_by_date(TRADE_DATE)
    basic = _df_stock_list(list_status='L')
    if basic is not None and len(basic) > 0:
        basic = basic[['ts_code', 'name']]
    mv = _df_daily_basic_by_date(TRADE_DATE, fields='ts_code,total_mv')
    df = daily.merge(basic, on='ts_code', how='left').merge(mv, on='ts_code', how='left')
    try:
        df.to_csv(cache_file, index=False)
        print(f"[缓存] 市场数据已保存: {cache_file}")
    except Exception:
        pass
    return df


# =========================
# 主题关联（复用 theme_stock_map / theme_alpha_v6_result 缓存）
# =========================
def _v8_stage_to_signal(d_stage, score):
    """V8 天数阶段 → V6 信号映射"""
    if d_stage in ("D1-D2",):
        if score >= 60: return "强买"
        elif score >= 50: return "看多"
        else: return "关注"
    if d_stage in ("D3",):
        if score >= 65: return "强买"
        elif score >= 50: return "看多"
        else: return "关注"
    if d_stage in ("D4-D5",):
        if score >= 60: return "强买"
        elif score >= 45: return "看多"
        else: return "关注"
    if d_stage in ("D6-D7",):
        return "关注"
    if d_stage in ("D8+", "潜伏期", "数据不足"):
        return "中性"
    return "中性"


def _load_v6_result(expected_date=None):
    """加载 Theme Alpha V8.0 引擎结果（优先 V8 CSV，其次 V8 JSON，最后 V6 JSON）"""
    v8_csv_path = v8_json_path = None
    if expected_date:
        v8_csv_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache',
                                   f'theme_alpha_v6_result_v8_{expected_date}.csv')
        v8_json_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache',
                                    f'theme_alpha_v6_result_v8_{expected_date}.json')
    v6_result_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache', 'theme_alpha_v6_result.json')
    source = None
    if v8_csv_path and os.path.exists(v8_csv_path):
        import csv
        data = []
        NUMERIC_FIELDS = {'排名', 'V7综合得分', '资金分', '梯队分', '趋势分', '基础分',
                          '资金_换手率Z分', '资金_自由流通市值流入比', '资金_大阳线渗透率',
                          '梯队_龙头涨幅', '梯队_龙头创新高', '梯队_龙头连板',
                          '梯队_中军数量', '梯队_中军5日涨幅', '梯队_中军破位比例',
                          '梯队_中军缩量比例', '梯队_跟风>3%比例', '梯队_跟风>5%比例',
                          '梯队_跟风上涨比例', '趋势_RSRS强度', '趋势_均线多头天数',
                          '基础_催化得分', '基础_业绩预期分', '基础_事件驱动分',
                          'T_start', 'T_MA', 'R_volume'}
        with open(v8_csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                for field in NUMERIC_FIELDS:
                    if field in row and row[field]:
                        try:
                            row[field] = float(row[field]) if '.' in row[field] else int(row[field])
                        except ValueError:
                            pass
                data.append(row)
        source = "V8_CSV"
    else:
        if v8_json_path and os.path.exists(v8_json_path):
            load_path = v8_json_path
            source = "V8"
        elif os.path.exists(v6_result_path):
            load_path = v6_result_path
            source = "V6"
        else:
            return None
        try:
            with open(load_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return None
    if not data:
        return None
    # V8/V8_CSV → V6 字段兼容映射
    if source in ("V8", "V8_CSV"):
        for r in data:
            r['theme'] = r.get('主题', '')
            r['composite_score'] = r.get('V7综合得分', 0)
            r['stage'] = r.get('D阶段', r.get('V7阶段', ''))
            r['trade_signal'] = _v8_stage_to_signal(r.get('D阶段', ''), r.get('V7综合得分', 0))
            r['trend_score'] = r.get('趋势分', 0)
            r['sentiment_score'] = 0
            r['continuation_score'] = 0
            r['alpha_gate'] = ''
            r['leader'] = ''
            r['divergence_buy'] = ''
            r['theme_status'] = ''
            if not r.get('trade_date'):
                r['trade_date'] = expected_date or ''
    return data


def _load_theme_stock_map_from_json():
    """从 JSON 缓存加载主题-个股映射"""
    theme_stock_map = {}
    name_map_basic = {}
    stock_basic_industry = {}
    stock_concepts = {}
    json_path = os.path.join(STOCK_DATA_DIR, "cache_daily", "theme_stock_map_latest.json")
    if not os.path.exists(json_path):
        return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts
    themes = data.get("themes", {}) or {}
    stocks = data.get("stocks", {}) or {}
    for theme_name, stock_list in themes.items():
        if not isinstance(stock_list, list):
            continue
        code_map = {}
        for item in stock_list:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if not code:
                continue
            code_map[code] = {
                "via": item.get("via", ""),
                "chain_distance": item.get("chain_distance", 0),
                "industry_match": bool(item.get("industry_match", False)),
                "score": item.get("score", 0),
            }
        theme_stock_map[theme_name] = code_map
    for code, info in stocks.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name")
        if name:
            name_map_basic[code] = name
        industry = info.get("industry")
        if industry:
            stock_basic_industry[code] = industry
        concepts = info.get("concepts")
        if isinstance(concepts, list):
            stock_concepts[code] = concepts
    return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts


def add_themes_to_stocks_no_filter(result_df):
    """给股票添加主题信息，但不做过滤（保留所有股票）"""
    if result_df is None or result_df.empty:
        return result_df

    keep_themes = []
    theme_state_map = {}
    try:
        v6_data = _load_v6_result(TRADE_DATE)
        if v6_data:
            for r in v6_data:
                tname = r.get('theme', '') if isinstance(r, dict) else ''
                if tname:
                    keep_themes.append(tname)
                    theme_state_map[tname] = {
                        'theme_state': r.get('trade_signal', ''),
                        'trend_score': float(r.get('trend_score', 0) or 0),
                        'sentiment_score': float(r.get('sentiment_score', 0) or 0),
                        'composite_score': float(r.get('composite_score', 0) or 0),
                        'cycle_phase': r.get('stage', ''),
                    }
    except Exception as e:
        print(f"[添加主题] 读取V6结果失败: {e}")

    if not keep_themes:
        return result_df

    try:
        theme_stock_map, _, _, _ = _load_theme_stock_map_from_json()
    except Exception as e:
        print(f"[添加主题] load_theme_stock_map_from_json 失败: {e}")
        return result_df

    matched_themes, match_scores, theme_states_list = [], [], []
    cycle_phases, secondary_themes_list = [], []
    for _, row in result_df.iterrows():
        ts_code = row['代码']
        theme_hits = []
        for theme_name in keep_themes:
            stocks = theme_stock_map.get(theme_name, {})
            if ts_code in stocks:
                s_info = stocks[ts_code]
                s_score = s_info.get('score', 0) if isinstance(s_info, dict) else 0
                theme_hits.append((theme_name, s_score))
        if theme_hits:
            theme_hits.sort(key=lambda x: -x[1])
            found_theme = theme_hits[0][0]
            secondary_theme = theme_hits[1][0] if len(theme_hits) >= 2 and theme_hits[1][1] > 0 else ''
            matched_themes.append(found_theme)
            match_scores.append(theme_hits[0][1] if theme_hits[0][1] > 0 else 100)
            secondary_themes_list.append(secondary_theme)
            st = theme_state_map.get(found_theme, {})
            theme_states_list.append(st.get("theme_state", ""))
            cycle_phases.append(st.get("cycle_phase", ""))
        else:
            matched_themes.append('')
            match_scores.append(0)
            secondary_themes_list.append('')
            theme_states_list.append('')
            cycle_phases.append('')

    result_df = result_df.copy()
    result_df['所属主题'] = matched_themes
    result_df['主题匹配度'] = match_scores
    result_df['次强主题'] = secondary_themes_list
    result_df['所属状态'] = theme_states_list
    result_df['非一日游阶段'] = cycle_phases
    print(f"[添加主题] 已给 {len(result_df)} 只股票添加主题信息")
    return result_df


# =========================
# Chip Alpha 注入（可选，复用 chip_alpha_engine_v2 / chip_alpha_v5）
# =========================
def get_chip_alpha_engine():
    global _chip_alpha_engine
    if _chip_alpha_engine is None:
        try:
            from chip_alpha_engine_v2 import ChipAlphaEngineV2
            _chip_alpha_engine = ChipAlphaEngineV2(token=TUSHARE_TOKEN)
        except Exception as e:
            print(f"[ChipAlpha] 引擎初始化失败: {e}")
            return None
    return _chip_alpha_engine


_chip_alpha_engine = None


def batch_chip_alpha(stocks, lookback_days=20):
    engine = get_chip_alpha_engine()
    if engine is None:
        return {}
    results = {}
    total = len(stocks)
    for i, s in enumerate(stocks):
        ts_code = s.get('代码') or s.get('code') or s.get('ts_code', '')
        if not ts_code:
            continue
        try:
            r = engine.analyze(ts_code, lookback_days=lookback_days)
            results[ts_code] = r
            if (i + 1) % 10 == 0:
                print(f"[ChipAlpha] 批量计算 {i+1}/{total}")
        except Exception as e:
            print(f"[ChipAlpha] {ts_code} 计算失败: {e}")
    return results


def extract_chip_alpha_factors(chip_result):
    if not chip_result:
        return {'ChipTrendScore': 50, 'ChipGrade': 'C', 'ChipStage': '未知',
                'CRE_Score': 50, 'ChipMomentum_Score': 50, 'PressureDecay_Score': 50,
                'Absorption_Score': 50, 'CenterVelocity_Score': 50}
    f = chip_result.get('Factors', {})
    dim = chip_result.get('DimensionScores', {})
    return {
        'ChipTrendScore': chip_result.get('ChipTrendScore', 50),
        'ChipGrade': chip_result.get('Grade', 'C'),
        'ChipStage': {'Accumulation': '吸筹中', 'Distribution': '派发中',
                      'Expansion': '扩张期', 'Early Trend': '早期趋势',
                      'Collapse': '崩溃', 'Unknown': '未知'}.get(
            chip_result.get('TrendStage', ''), chip_result.get('TrendStage', '未知')),
        'CRE_Score': f.get('CRE', {}).get('score', 50),
        'ChipMomentum_Score': f.get('ChipMomentum', {}).get('score', 50),
        'PressureDecay_Score': f.get('PressureDecay', {}).get('score', 50),
        'Absorption_Score': f.get('Absorption', {}).get('score', 50),
        'CenterVelocity_Score': f.get('CenterVelocity', {}).get('score', 50),
        'Structure_Score': dim.get('Structure', {}).get('score', 50),
        'Flow_Score': dim.get('Flow', {}).get('score', 50),
        'Momentum_Score': dim.get('Momentum', {}).get('score', 50),
    }


def get_chip_alpha_suggestion(stock_dict):
    score = stock_dict.get('ChipTrendScore', 50)
    cre = stock_dict.get('CRE_Score', 50)
    mom = stock_dict.get('ChipMomentum_Score', 50)
    pressure = stock_dict.get('PressureDecay_Score', 50)
    absorption = stock_dict.get('Absorption_Score', 50)
    stage = stock_dict.get('ChipStage', '未知')
    if score >= 75 and cre >= 65 and mom >= 60:
        return "积极参与", f"趋势强+CRE高+动量足，{stage}，可沿5日线持有"
    if score >= 65 and cre >= 50 and mom >= 50:
        if pressure >= 80:
            return "可逢低介入", f"趋势向好+上方压力轻，{stage}，回踩MA20低吸"
        if cre >= 60 and mom >= 60:
            return "可逢低介入", f"趋势转强+效率提升，{stage}，分批建仓"
        return "关注观察", f"趋势中性偏强，{stage}，等待CRE/动量进一步确认"
    if score >= 55 and cre >= 40:
        if mom >= 60 and pressure >= 70:
            return "左侧关注", f"动量转强+压力轻，但趋势分未达标，小仓位试错"
        if absorption >= 60:
            return "左侧关注", f"吸筹中+吸筹质量尚可，{stage}，等待启动信号"
        return "纳入观察", f"筹码改善中，{stage}，等待趋势确认信号"
    if mom >= 65 and pressure >= 80:
        return "左侧试错", f"动量加速+压力极轻，但趋势分偏低，轻仓试错"
    if score < 40:
        return "回避", f"筹码结构弱，{stage}，暂不参与"
    return "观望等待", f"筹码中性，{stage}，等待明确信号"


_chip_alpha_v5_engine = None


def get_chip_alpha_v5_engine():
    global _chip_alpha_v5_engine
    if _chip_alpha_v5_engine is None:
        try:
            from chip_alpha_v5 import ChipAlphaV5Engine
            _chip_alpha_v5_engine = ChipAlphaV5Engine(token=TUSHARE_TOKEN)
        except Exception as e:
            print(f"[ChipAlphaV5] 引擎初始化失败: {e}")
            return None
    return _chip_alpha_v5_engine


def batch_chip_alpha_v5(v2_results):
    engine = get_chip_alpha_v5_engine()
    if engine is None:
        return {}
    v5_results = {}
    total = len(v2_results)
    for i, (ts_code, v2_r) in enumerate(v2_results.items()):
        try:
            v5 = engine.analyze_from_v2(v2_r)
            v5_results[ts_code] = v5
            if (i + 1) % 20 == 0:
                print(f"[ChipAlphaV5] 升级 {i+1}/{total}")
        except Exception as e:
            print(f"[ChipAlphaV5] {ts_code} 升级失败: {e}")
    return v5_results


def extract_chip_alpha_v5_factors(v5_result):
    if not v5_result:
        return {'Alpha_Structure': 50, 'Alpha_Flow': 50, 'Alpha_Momentum': 50,
                'Alpha_Composite': 50, 'Alpha_Grade': 'C', 'Risk_Score': 50,
                'Risk_Level': 'Medium', 'Trend_State': 'Unknown', 'Trend_Desc': '',
                'Next_State': 'Unknown', 'Next_Prob': 0, 'Action': 'Hold',
                'Confidence': 50, 'DecisionSummary': '', 'Opportunity_Score': 50.0}
    a = v5_result.get('alpha', {})
    r = v5_result.get('risk', {})
    t = v5_result.get('trend', {})
    d = v5_result.get('decision', {})
    tr = t.get('transition', {})
    return {
        'Alpha_Structure': a.get('structure', 50),
        'Alpha_Flow': a.get('flow', 50),
        'Alpha_Momentum': a.get('momentum', 50),
        'Alpha_Composite': a.get('composite', 50),
        'Alpha_Grade': a.get('grade', 'C'),
        'Risk_Score': r.get('total', 50),
        'Risk_Level': r.get('level', 'Medium'),
        'Trend_State': t.get('state', 'Unknown'),
        'Trend_Desc': t.get('description', ''),
        'Next_State': tr.get('target', 'Unknown'),
        'Next_Prob': tr.get('probability', 0),
        'Action': d.get('action', 'Hold'),
        'Confidence': d.get('confidence', 50),
        'DecisionSummary': d.get('summary', ''),
        'Opportunity_Score': v5_result.get('opportunity', {}).get('score', 50.0),
    }


# =========================
# 量能爆发+宽幅震荡策略核心（提取自 tushare_quant.py）
# =========================
_WAVE_PIVOT_WINDOW = 5
_WAVE_W1_MIN_GAIN = 0.40
_WAVE_W1_MAX_GAIN = 2.00
_WAVE_W2_MIN = 0.20
_WAVE_W2_MAX = 0.85


def _find_wave_pivots(df, window=_WAVE_PIVOT_WINDOW):
    """识别价格枢轴点(局部极值)"""
    highs = df['high'].values
    lows = df['low'].values
    dates = df['trade_date'].values
    n = len(df)
    pivots = []
    for i in range(window, n - window):
        left_h = highs[i - window:i]
        right_h = highs[i + 1:i + 1 + window]
        left_l = lows[i - window:i]
        right_l = lows[i + 1:i + 1 + window]
        if highs[i] >= np.max(left_h) and highs[i] >= np.max(right_h):
            pivots.append({'idx': i, 'date': str(dates[i]), 'price': float(highs[i]), 'kind': 'high'})
        if lows[i] <= np.min(left_l) and lows[i] <= np.min(right_l):
            pivots.append({'idx': i, 'date': str(dates[i]), 'price': float(lows[i]), 'kind': 'low'})
    pivots.sort(key=lambda p: p['idx'])
    out = [pivots[0]] if pivots else []
    for p in pivots[1:]:
        last = out[-1]
        if p['kind'] == last['kind']:
            if (p['kind'] == 'high' and p['price'] > last['price']) or \
               (p['kind'] == 'low' and p['price'] < last['price']):
                out[-1] = p
        else:
            out.append(p)
    return out


def _find_simple_wave(pivots):
    """从枢轴点序列中识别L0->H1->L2波浪结构"""
    if len(pivots) < 3:
        return None
    best_wave = None
    best_score = -1.0
    for i in range(len(pivots) - 2):
        if pivots[i]['kind'] != 'low':
            continue
        L0 = pivots[i]
        H1 = None
        for j in range(i + 1, len(pivots)):
            if pivots[j]['kind'] == 'high':
                H1 = pivots[j]
                break
        if H1 is None:
            continue
        L2 = None
        for j in range(i + 2, len(pivots)):
            if pivots[j]['kind'] == 'low':
                L2 = pivots[j]
                break
        if L2 is None:
            continue
        w1_gain = (H1['price'] - L0['price']) / max(L0['price'], 0.01)
        w2_retrace = (H1['price'] - L2['price']) / max(H1['price'] - L0['price'], 0.01)
        if w1_gain < _WAVE_W1_MIN_GAIN or w1_gain > _WAVE_W1_MAX_GAIN:
            continue
        if not (_WAVE_W2_MIN <= w2_retrace <= _WAVE_W2_MAX):
            continue
        if L2['price'] <= L0['price'] or L2['idx'] < H1['idx']:
            continue
        score = w1_gain * 10
        if score > best_score:
            best_score = score
            best_wave = {'L0': L0, 'H1': H1, 'L2': L2, 'w1_gain': w1_gain, 'w2_retrace': w2_retrace}
    return best_wave


def _detect_wave_surge_ready(df):
    """波浪结构+蓄势大涨检测（返回 (wave_ok, w1_gain, w2_retrace, dist_to_h1)）"""
    try:
        if df is None or len(df) < 60:
            return False, 0.0, 0.0, 0.0
        pivots = _find_wave_pivots(df)
        wave = _find_simple_wave(pivots)
        if wave is None:
            return False, 0.0, 0.0, 0.0
        w1_gain = wave['w1_gain']
        w2_retrace = wave['w2_retrace']
        if w2_retrace >= 0.70:
            return False, w1_gain, w2_retrace, 0.0
        today_close = float(df['close'].values[-1])
        dist_to_h1 = (today_close / wave['H1']['price'] - 1)
        return True, w1_gain, w2_retrace, dist_to_h1
    except Exception:
        return False, 0.0, 0.0, 0.0


def detect_volume_surge_swing(ts_code, name, _df_override=None):
    """检测量能爆发+宽幅震荡模式"""
    try:
        df = _df_override if _df_override is not None else get_hist_data(ts_code)
        if df is None or len(df) < 180:
            return None
        recent = df.tail(200)
        if len(recent) < 60:
            return None
        vol_arr = recent['vol'].values.astype(float)
        high_arr = recent['high'].values.astype(float)
        low_arr = recent['low'].values.astype(float)
        close_arr = recent['close'].values.astype(float)
        pre_close_arr = recent['pre_close'].values.astype(float)

        vol_ma20 = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
        vol_ratio = vol_arr / np.maximum(vol_ma20, 1)
        max_vol_ratio = float(np.nanmax(vol_ratio))
        vol_ratio_gt2 = int(np.sum(vol_ratio > 2.0))
        vol_ratio_gt3 = int(np.sum(vol_ratio > 3.0))

        hist_vol_max = float(np.max(df['vol'].values.astype(float)))
        recent_vol_max = float(np.max(vol_arr))
        vol_vs_hist_pct = (recent_vol_max / hist_vol_max * 100) if hist_vol_max > 0 else 0

        amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
        avg_amplitude = float(np.mean(amplitude[-120:]))
        amp_gt8_count = int(np.sum(amplitude > 8))

        range_high = float(np.max(high_arr))
        range_low = float(np.min(low_arr))
        range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0

        price_change = (close_arr[-1] / close_arr[0] - 1) * 100 if close_arr[0] > 0 else 0

        # 硬条件
        if max_vol_ratio < 2.6:
            return None
        if vol_ratio_gt2 < 3:
            return None
        # 近20日量能活跃度硬条件（用户要求20260815：量能放大须发生在近期，而非仅靠历史峰值过关）
        # 共进股份启动前形态：近20日 max≈2.48/mean≈1.63；华光环能近20日 max=2.70/mean=1.21 被过滤
        _vol_ratio_20 = vol_ratio[-20:]
        if len(_vol_ratio_20) < 20:
            return None
        if float(np.nanmax(_vol_ratio_20)) < 2.0:
            return None
        if float(np.nanmean(_vol_ratio_20)) < 1.4:
            return None
        if avg_amplitude < 4.5:
            return None
        if range_swing < 35:
            return None
        # 区间涨幅(200日)仅展示不做硬过滤：短线策略不设长周期涨跌幅限制
        # 排除今日大跌/跌停
        today_pct = (close_arr[-1] / pre_close_arr[-1] - 1) * 100 if pre_close_arr[-1] > 0 else 0
        if today_pct <= -7.0:
            return None
        # 排除今日涨停（用户偏好：只做低吸/小中阳突破，过滤涨停股）
        # 双创(30x/688)涨停线20%，主板涨停线10%
        _code6 = str(ts_code).split('.')[0]
        _zt_line = 19.5 if _code6.startswith(('300', '301', '688')) else 9.8
        if today_pct >= _zt_line:
            return None
        if len(df) < 180:
            return None
        if vol_vs_hist_pct < 50:
            return None

        # MA20趋势检查：20日均线必须走平或上行
        ma20_full = pd.Series(df['close'].values.astype(float)).rolling(20, min_periods=20).mean().values
        if len(ma20_full) >= 41:
            ma20_now = float(ma20_full[-1])
            ma20_10ago = float(ma20_full[-11]) if not np.isnan(ma20_full[-11]) else ma20_now
            ma20_20ago = float(ma20_full[-21]) if not np.isnan(ma20_full[-21]) else ma20_now
            if (not np.isnan(ma20_now) and not np.isnan(ma20_10ago) and ma20_10ago > 0
                    and not np.isnan(ma20_20ago) and ma20_20ago > 0):
                ma20_chg_10d = (ma20_now / ma20_10ago - 1) * 100
                ma20_chg_20d = (ma20_now / ma20_20ago - 1) * 100
                if ma20_chg_10d < -1.0 or ma20_chg_20d < -2.0:
                    return None
            close_latest = float(close_arr[-1])
            if close_latest < ma20_now * 0.95:
                return None

        # 近期量能活跃度检查：短期成交量对比前期基量（识别近期放量）
        _df200 = df.tail(200) if len(df) >= 200 else df
        _vol200 = _df200['vol'].values.astype(float)
        _high200 = _df200['high'].values.astype(float)
        _low200 = _df200['low'].values.astype(float)
        _close200 = _df200['close'].values.astype(float)

        _peak_vol_idx = int(np.argmax(_vol200))
        _peak_vol_price = float(_high200[_peak_vol_idx])

        # 近期量用最近10日均量（20日均量易被早期缩量稀释），基量用前10~40日均量（排除近期放量段）
        _recent_vol = float(np.mean(_vol200[-10:])) if len(_vol200) >= 10 else float(np.mean(_vol200))
        _base_vol = float(np.mean(_vol200[-40:-10])) if len(_vol200) >= 40 else float(np.mean(_vol200[:max(len(_vol200) // 2, 5)]))
        _base_vol = max(_base_vol, 1)
        _vol_vs_base = _recent_vol / _base_vol
        if _vol_vs_base < 1.1:
            return None

        # 近期均量 vs 高点5日均量
        _peak_vol_start = max(0, _peak_vol_idx - 5)
        _peak_vol_end = min(len(_vol200), _peak_vol_idx + 6)
        _peak_5d_vol = float(np.mean(_vol200[_peak_vol_start:_peak_vol_end])) if _peak_vol_end > _peak_vol_start else _recent_vol
        _peak_5d_vol = max(_peak_5d_vol, 1)
        _vol_vs_peak = _recent_vol / _peak_5d_vol
        if _vol_vs_peak < 0.5:
            return None

        # ABC结构计算（仅用于回撤类型分类，不做长周期波浪硬过滤）
        _a_low = float(np.min(_low200[:_peak_vol_idx + 1]))
        _a_gain = (_peak_vol_price / _a_low - 1) * 100 if _a_low > 0 else 0

        # B浪回撤计算（仅用于回撤类型分类，不做斐波那契硬过滤）
        if _peak_vol_idx < len(_low200) - 3:
            _b_low = float(np.min(_low200[_peak_vol_idx:]))
            _b_drop = (1 - _b_low / _peak_vol_price) * 100
            _retrace_ratio = _b_drop / _a_gain * 100 if _a_gain > 0 else 0
        else:
            _b_low = close_arr[-1]
            _b_drop = 0
            _retrace_ratio = 0

        # 评分
        vol_score = min(max_vol_ratio / 5.0, 1) * 30
        freq_score = min(vol_ratio_gt2 / 7, 1) * 20
        amp_score = min(avg_amplitude / 7, 1) * 20
        big_amp_score = min(amp_gt8_count / 15, 1) * 15
        swing_score = min(range_swing / 60, 1) * 15
        total_score = vol_score + freq_score + amp_score + big_amp_score + swing_score

        if total_score < 65:
            return None

        # MACD信号判断
        close_full = df['close'].values.astype(float)
        ema12 = pd.Series(close_full).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close_full).ewm(span=26, adjust=False).mean().values
        macd_dif = ema12 - ema26
        macd_dea = pd.Series(macd_dif).ewm(span=9, adjust=False).mean().values
        macd_bar = 2 * (macd_dif - macd_dea)

        cur_bar = float(macd_bar[-1])
        prev_bar = float(macd_bar[-2]) if len(macd_bar) >= 2 else cur_bar
        prev2_bar = float(macd_bar[-3]) if len(macd_bar) >= 3 else prev_bar

        macd_status = ''
        macd_pass = False
        if prev_bar < 0 < cur_bar:
            macd_status = '刚刚红柱 ✅'
            macd_pass = True
        elif cur_bar < 0 and cur_bar > prev_bar > prev2_bar:
            macd_status = '即将红柱（绿柱连续缩短）'
            macd_pass = True
        elif cur_bar > 0 and prev_bar > 0 and cur_bar < abs(macd_bar[-4]) * 0.7:
            macd_status = '红柱回调缩短（趋势延续）'
            macd_pass = True
        elif cur_bar > 0 and prev_bar > 0 and cur_bar > prev_bar and prev_bar < prev2_bar:
            macd_status = '红柱回调后反弹（趋势延续）'
            macd_pass = True

        if not macd_pass:
            # MACD未确认不入场（蓄势大涨信号仅展示，见主路径）
            return None

        # 死叉临界识别（20260817落地）：红柱回调缩短分支内，红柱已缩至极小 → 1~2日内可能实叉
        # 例：顺钠000533(20260817) DIF-DEA=+0.043/红柱=0.085，距死叉仅一步却曾被排TOP1
        # 判据：cur_bar < max(0.15, 近20日红柱峰值*20%) → 红柱剩余度不足、贴近零轴
        death_cross_risk = False
        if macd_status == '红柱回调缩短（趋势延续）':
            _red_peak20 = float(np.max(np.maximum(macd_bar[-20:], 0))) if len(macd_bar) >= 20 else cur_bar
            if cur_bar < max(0.15, _red_peak20 * 0.2):
                death_cross_risk = True

        today_vol_ratio = float(vol_ratio[-1]) if len(vol_ratio) > 0 else 0

        if _retrace_ratio < 30:
            retrace_type = '浅回调'
        elif _retrace_ratio < 50:
            retrace_type = '中回调'
        else:
            retrace_type = '深回调'

        close_latest = float(close_arr[-1])
        ma20_latest = pd.Series(close_arr).rolling(20).mean().values[-1]
        pos_ma20 = (close_latest / ma20_latest - 1) * 100 if not np.isnan(ma20_latest) and ma20_latest > 0 else 0

        is_fresh_red = (macd_status == '刚刚红柱 ✅')
        is_red_retrace = (macd_status == '红柱回调缩短（趋势延续）')
        is_red_bounce = (macd_status == '红柱回调后反弹（趋势延续）')

        # 强买信号判定（形态参考：同等闸门回测强买全量44.8%不低于Top3，但"强买优先"排序负优化，不作买入排序依据）
        strong_buy = False
        strong_buy_reason = ''
        if pos_ma20 < 0 and is_fresh_red:
            strong_buy = True
            strong_buy_reason = '回踩MA20下方+MACD刚红柱(形态)'
        elif retrace_type == '中回调' and is_fresh_red:
            strong_buy = True
            strong_buy_reason = '中回调+MACD刚红柱(形态)'
        elif retrace_type == '浅回调' and is_fresh_red and total_score >= 70:
            strong_buy = True
            strong_buy_reason = '浅回调+刚红柱+高评分(形态)'
        elif 65 <= total_score < 80 and 1.0 <= today_vol_ratio < 1.5 and -3 <= pos_ma20 < 0:
            strong_buy = True
            strong_buy_reason = '评分65-80+量比1.0-1.5+回踩MA20(形态)'
        elif (is_red_retrace or is_red_bounce) and total_score >= 70 and today_vol_ratio >= 0.9 and not death_cross_risk:
            strong_buy = True
            strong_buy_reason = '红柱回调+高评分+量比达标(趋势延续)'

        # 观察信号（即将红柱，等待确认；死叉临界票转为观察保留，避免被剔除）
        watch = False
        watch_reason = ''
        if not strong_buy and not is_fresh_red and not is_red_retrace and not is_red_bounce:
            watch = True
            watch_reason = '观察·等待红柱（MACD绿柱连续缩短，即将金叉，可关注翻红确认）'
        elif death_cross_risk and not strong_buy:
            watch = True
            watch_reason = '观察·⚠️死叉临界（红柱已缩至极小，MACD 1~2日内可能实叉，等待方向选择）'

        wave_surge = False
        wave_surge_reason = ''
        wave_w1_gain = 0.0
        wave_w2_retrace = 0.0
        wave_dist_h1 = 0.0

        if not strong_buy and not watch:
            return None

        # 蓄势大涨信号（仅展示，不入硬过滤）
        _w_ok, _w1, _w2, _dist = _detect_wave_surge_ready(df)
        if _w_ok:
            wave_surge = True
            wave_surge_reason = (f'波浪蓄势大涨(W1={_w1*100:.0f}% W2={_w2*100:.0f}% 距H1={_dist*100:+.1f}%)')
            wave_w1_gain = _w1
            wave_w2_retrace = _w2
            wave_dist_h1 = _dist

        result = {
            '代码': ts_code, '名称': name,
            '量能爆发评分': round(total_score, 1),
            '最大量比': round(max_vol_ratio, 2),
            '量比>2天数': vol_ratio_gt2,
            '量比>3天数': vol_ratio_gt3,
            '日均振幅': round(avg_amplitude, 2),
            '巨震天数(>8%)': amp_gt8_count,
            '区间振幅': round(range_swing, 1),
            '区间涨幅': round(price_change, 1),
            '近历史最高量%': round(vol_vs_hist_pct, 0),
            '今日量比': round(today_vol_ratio, 2),
            '今日涨跌幅': round(today_pct, 2),
            'MACD状态': macd_status,
            '死叉临界': death_cross_risk,
            '回撤类型': retrace_type,
            '距MA20': round(pos_ma20, 1),
            '强买信号': strong_buy,
            '强买原因': strong_buy_reason,
            '观察信号': watch,
            '观察原因': watch_reason,
            '蓄势大涨信号': wave_surge,
            '蓄势大涨原因': wave_surge_reason,
            '波浪W1涨幅': round(wave_w1_gain * 100, 1) if wave_surge else 0,
            '波浪W2回调': round(wave_w2_retrace * 100, 1) if wave_surge else 0,
            '波浪距H1': round(wave_dist_h1 * 100, 1) if wave_surge else 0,
        }
        return result
    except Exception:
        return None


# =========================
# 主流程
# =========================
def run(target_date=None, with_chip=True, simple=False):
    """运行量能爆发+宽幅震荡选股

    Args:
        target_date: 目标日期 YYYYMMDD
        with_chip: 是否注入 Chip Alpha（默认开启）
        simple: 简易模式，只输出列表不保存报告
    """
    global TRADE_DATE
    if target_date:
        target_date = str(target_date)
        TRADE_DATE = validate_trade_date(target_date)
        print(f"\n{'='*60}")
        print(f"[VSW V2 量能选股] 目标日期: {TRADE_DATE}")
        print(f"{'='*60}\n")

    market = get_market()
    if market is None or market.empty:
        print("❌ 市场数据为空，无法选股")
        return []

    # 目标股池：总市值 > 80亿
    _filtered = market[market['total_mv'].fillna(0) > 800000]
    _filtered_codes = set(_filtered['ts_code'].tolist())
    print(f'\n[目标股池] 总市值>80亿共 {len(_filtered_codes)} 只，开始扫描...')

    # 大盘环境提示（三指数动量，仅作参考，不拦截输出）
    market_tip = None
    try:
        market_tip = get_index_momentum(TRADE_DATE)
    except Exception as e:
        print(f"[大盘提示] 指数动量获取异常: {e}")
    if market_tip:
        _print_market_tip(market_tip)
    else:
        print("[大盘提示] 指数动量数据不足，跳过环境提示")

    # 批量预取（复用缓存）
    try:
        print(f"[批量预取] 共 {len(_filtered_codes)} 只，检查本地缓存...")
        batch_prefetch_hist_data(list(_filtered_codes))
    except Exception as e:
        print(f"[批量预取] 失败（继续逐只获取）: {e}")

    results = []
    total = len(_filtered_codes)
    for i, _code in enumerate(_filtered_codes):
        _vname = get_stock_name(_code)
        _vres = detect_volume_surge_swing(_code, _vname)
        if _vres:
            results.append(_vres)
        if (i + 1) % 100 == 0:
            print(f"  扫描进度 {i+1}/{total}，命中 {len(results)}")

    results = sorted(results, key=lambda x: -x['量能爆发评分'])
    print(f'[量能宽幅震荡] 命中 {len(results)} 只')

    # 主题注入（复用缓存，无额外API）
    if results:
        try:
            _vs_df = pd.DataFrame(results)
            _vs_df = add_themes_to_stocks_no_filter(_vs_df)
            results = _vs_df.to_dict('records')
        except Exception as e:
            print(f"[主题注入] 失败: {e}")

    # Chip Alpha 注入（可选）
    if with_chip and results:
        try:
            print(f"[ChipAlpha] 批量计算 {len(results)} 只...")
            _chip_results = batch_chip_alpha(results, lookback_days=20)
            for s in results:
                _code = s.get('代码', '')
                _chip_r = _chip_results.get(_code)
                s.update(extract_chip_alpha_factors(_chip_r))
                _sug, _reason = get_chip_alpha_suggestion(s)
                s['ChipSuggestion'] = _sug
                s['ChipSuggestionReason'] = _reason
            _v5_results = batch_chip_alpha_v5(_chip_results)
            for s in results:
                _code = s.get('代码', '')
                _v5_r = _v5_results.get(_code)
                s.update(extract_chip_alpha_v5_factors(_v5_r))
        except Exception as e:
            print(f"[ChipAlpha] 注入失败: {e}")

    for _v in results[:10]:
        _theme = _v.get('所属主题', '') or '无主题'
        _stage = _v.get('非一日游阶段', '') or ''
        _stage_str = f' 阶段={_stage}' if _stage else ''
        print(f"  {_v['名称']}({_v['代码']}) 评分{_v['量能爆发评分']} 主题={_theme}{_stage_str} MACD={_v['MACD状态']}{' ⚠️死叉临界' if _v.get('死叉临界') else ''}")

    _output_report(results, simple=simple, market_tip=market_tip)
    return results


def _chip_v5_line(s):
    """筹码+V5 明细行（AI 输出共用格式，供报告与 tushare_quant 读取）"""
    _chip_score = s.get('ChipTrendScore', 50)
    _cre_score = s.get('CRE_Score', 50)
    _mom_score = s.get('ChipMomentum_Score', 50)
    _chip_sug = s.get('ChipSuggestion', '观望等待')
    _v5s = s.get('Alpha_Structure', 50)
    _v5f = s.get('Alpha_Flow', 50)
    _v5m = s.get('Alpha_Momentum', 50)
    _v5c = s.get('Alpha_Composite', 50)
    _v5g = s.get('Alpha_Grade', 'C')
    _risk = s.get('Risk_Score', 50)
    _state = s.get('Trend_State', 'Unknown')
    _act = s.get('Action', 'Hold')
    _conf = s.get('Confidence', 50)
    _os = s.get('Opportunity_Score', 50)
    return (f"  筹码: 趋势{_chip_score:.0f}/CRE{_cre_score:.0f}/动量{_mom_score:.0f} "
            f"| V5:{_v5s:.0f}/{_v5f:.0f}/{_v5m:.0f}({_v5c:.0f}/{_v5g}) "
            f"| 风险={_risk:.0f} | {_state}→{_act}({_conf:.0f}%) "
            f"| 机会={_os:.0f} | {_chip_sug}")


def _output_report(results, simple=False, market_tip=None):
    """输出大盘提示 + 算法Top3 + 强买/观察/蓄势三类信号 + 保存报告"""
    if not results:
        print("\n今日无量能爆发信号")
        return

    vs_strong_buy = sorted([x for x in results if x.get('强买信号')], key=lambda x: -x['量能爆发评分'])
    vs_watch = sorted([x for x in results if x.get('观察信号') and not x.get('强买信号')], key=lambda x: -x['量能爆发评分'])
    vs_wave_surge = sorted([x for x in results if x.get('蓄势大涨信号')], key=lambda x: -x['量能爆发评分'])

    lines = [f"# VSW V2 量能爆发+宽幅震荡选股 — {TRADE_DATE}", ""]

    # 大盘环境提示（三指数动量，仅作参考，不再硬性拦截）
    if market_tip:
        _s = " ".join(f"{INDEX_NAMES_3[c]}={market_tip['per'][c]:+.1f}%"
                      for c in market_tip['per'])
        lines.append(f"> 大盘环境: {market_tip['env']} | 三指数20日动量均值 {market_tip['mom20_avg']:+.1f}% ({_s})")
        lines.append(f"> 回测参考(T+5胜率/均收益): {market_tip['win_ref']} | 环境仅供自行决策，不构成买入拦截")
        lines.append("> 买入方式: 次日开盘 · 持有T+5 · 盘中-7%止损 · 每日Top3")
        lines.append("")

    # 🎯 算法输出 TOP3（r4：距MA20贴地优先(<=+3%)+红柱回调缩短分支优先+距MA20升序+评分次级）
    # 回测结论(2024-01~2026-08, 三指数动量>+3%闸门, T+5, 止损-7%): 每日只数5→3 胜率45.7%→47.7%、均收益+1.35%→+1.64%;
    # "强买优先Top5"排序为负优化(41.3%/+0.90% vs 纯评分Top5 41.9%/+1.41%)，故买入排序只用 距MA20+评分 组合，强买仅作形态参考
    # r4 相对 r3 改动(20260814回测)：叠加MACD形态偏好——红柱回调缩短④=49.7%/+2.29%为全分支最优(中位+0.00%唯一非负)，
    #   红柱回调后反弹⑤=37.7%、刚刚红柱③=37.1%次之，即将红柱②=32.0%/-0.30%最差(共进形态全量负期望)；
    #   贴地≤3%仍为第一优先级(全量∩≤3% 47.6% vs 全量44.3%；红柱回调∩≤3% 55.5%为全池最高胜率组合)
    # 20260804~14 实测：距MA20<=3%贴地票8笔全盈利(均+11.7%)，距>+7%追高票2笔止损(-7%)，支撑贴地优先
    _MACD_RANK = {
        '红柱回调缩短（趋势延续）': 0,   # ④ 49.7%/+2.29% 最优
        '红柱回调后反弹（趋势延续）': 1,   # ⑤ 37.7%/+0.65%
        '刚刚红柱 ✅': 1,               # ③ 37.1%/+0.64%
        '即将红柱（绿柱连续缩短）': 2,     # ② 32.0%/-0.30% 最差
    }
    # 死叉临界(20260817落地)：红柱已缩至极小、1~2日内可能实叉的票整体降末档（优先级最高，先于贴地/形态），
    # 避免"启动高位+死叉临界+无量反弹"组合占据TOP1；若非临界票不足3只，死叉临界票仍带⚠️标注补入
    def _macd_rank(_r):
        _rk = _MACD_RANK.get(_r['MACD状态'], 1)
        return 9 if _r.get('死叉临界', False) else _rk
    vs_top5 = sorted(results, key=lambda x: (
        x.get('死叉临界', False),               # ① 死叉临界整体末档（最高优先级，先于贴地）
        x['距MA20'] > 3,                        # ② 距MA20<=+3% 贴地/回调到位 优先
        _macd_rank(x),                          # ③ 红柱回调缩短分支优先
        x['距MA20'],                            # ④ 距MA20升序（负值/贴地更靠前）
        -x['量能爆发评分'],                       # ⑤ 同级评分降序（高分优先）
    ))[:3]
    lines.append("## 🎯 算法输出 TOP3（次日开盘买入候选，自行选择）")
    if market_tip:
        lines.append(f"【环境提示】{market_tip['env']}，回测参考(T+5): {market_tip['win_ref']} | 是否买入请自行决策")
    for i, _vr in enumerate(vs_top5, 1):
        _t = f"主题={_vr.get('所属主题','') or '无主题'}" + (f" | 阶段={_vr.get('非一日游阶段','')}" if _vr.get('非一日游阶段') else "")
        lines.append(f"【TOP{i}】{_vr['名称']}({_vr['代码']}) 评分{_vr['量能爆发评分']:.0f} {_vr['回撤类型']} 距MA20={_vr['距MA20']:+.1f}%")
        lines.append(f"  {_t}")
        _macd_tag = _vr['MACD状态'] + (' ⚠️死叉临界' if _vr.get('死叉临界') else '')
        lines.append(f"  MACD={_macd_tag} | 量比={_vr['今日量比']} | 区间涨幅={_vr['区间涨幅']:.1f}% | 振幅={_vr['区间振幅']:.1f}%")
        lines.append(_chip_v5_line(_vr))
    lines.append("")

    lines.append("## 🔥 量能爆发·强买信号（形态参考，非买入排序依据）")
    if vs_strong_buy:
        lines.append("【筛选条件】①距MA20<0%+刚红柱 ②中回调+刚红柱 ③浅回调+刚红柱+评分>=70 ④评分65-80+量比1.0-1.5+距MA20-3~0%")
        for i, _vr in enumerate(vs_strong_buy[:10], 1):
            lines.append(f"【强买{i}】{_vr['名称']}({_vr['代码']}) 评分{_vr['量能爆发评分']:.0f} {_vr['回撤类型']} 距MA20={_vr['距MA20']:+.1f}%")
            lines.append(f"  {_vr['强买原因']}")
            _t = f"主题={_vr.get('所属主题','') or '无主题'}" + (f" | 阶段={_vr.get('非一日游阶段','')}" if _vr.get('非一日游阶段') else "")
            lines.append(f"  {_t}")
            lines.append(f"  MACD={_vr['MACD状态']} | 量比={_vr['今日量比']} | 区间涨幅={_vr['区间涨幅']:.1f}% | 振幅={_vr['区间振幅']:.1f}%")
            lines.append(_chip_v5_line(_vr))
    else:
        lines.append("今日无强买信号（需等待MACD刚红柱+中/浅回调+距MA20近的条件共振）")
    lines.append("【回测提示】同等闸门(2024-01~2026-08, T+5, 止损-7%)：强买全量胜率44.8%/均+1.73%并不低于评分Top3；『强买优先』排序是负优化(41.3%/+0.90% vs 纯评分Top5 41.9%/+1.41%)，强买仅作形态参考，买入以🎯TOP3为准")
    lines.append("")

    if vs_watch:
        lines.append("## 👀 量能爆发·观察信号（MACD即将红柱，等待翻红确认）")
        for i, _vr in enumerate(vs_watch[:10], 1):
            lines.append(f"【观察{i}】{_vr['名称']}({_vr['代码']}) 评分{_vr['量能爆发评分']:.0f} {_vr['回撤类型']} 距MA20={_vr['距MA20']:+.1f}%")
            lines.append(f"  {_vr['观察原因']}")
            _t = f"主题={_vr.get('所属主题','') or '无主题'}" + (f" | 阶段={_vr.get('非一日游阶段','')}" if _vr.get('非一日游阶段') else "")
            lines.append(f"  {_t}")
            lines.append(f"  MACD={_vr['MACD状态']} | 量比={_vr['今日量比']}")
            lines.append(_chip_v5_line(_vr))
        lines.append("")

    if vs_wave_surge:
        lines.append("## 🌊 量能爆发·蓄势大涨信号（波浪结构+量能爆发结合）")
        for i, _vr in enumerate(vs_wave_surge[:10], 1):
            lines.append(f"【蓄势{i}】{_vr['名称']}({_vr['代码']}) 评分{_vr['量能爆发评分']:.0f}")
            lines.append(f"  {_vr['蓄势大涨原因']}")
            lines.append(f"  W1={_vr['波浪W1涨幅']:.0f}% W2={_vr['波浪W2回调']:.0f}% 距H1={_vr['波浪距H1']:+.1f}%")
            lines.append(_chip_v5_line(_vr))

    msg = "\n".join(lines)
    print("\n" + msg)

    if simple:
        return
    out_path = os.path.join(REPORT_DIR, f"volume_surge_{TRADE_DATE}.md")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(msg)
        print(f"\n✅ 报告已保存: {out_path}")
    except Exception as e:
        print(f"⚠️ 报告保存失败: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='VSW V2 量能爆发+宽幅震荡选股（独立版）')
    parser.add_argument('target_date', nargs='?', default=None, help='目标日期 YYYYMMDD')
    parser.add_argument('--no-chip', action='store_true', help='不注入 Chip Alpha')
    parser.add_argument('--simple', action='store_true', help='简易模式（不保存报告）')
    args = parser.parse_args()
    run(target_date=args.target_date, with_chip=not args.no_chip, simple=args.simple)


if __name__ == '__main__':
    main()
