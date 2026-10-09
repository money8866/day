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
import re
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import tushare as ts

# 选股落库跟踪（失败不阻塞主流程）：仅当日「下蹲买点」进跟踪表，供 stock_pick_db.py tracking 回填 T+N
# （20260930 收敛：TOP3/观察/强买 等其余信号不再落库，仅保留在报告里）
try:
    from stock_pick_db import record_picks as _PICK_RECORD
except Exception:
    _PICK_RECORD = None

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

# 起量台阶识别参数（20260927，_vol_step_days；20260930 起兼作「缩量比」的基准选择，参数敏感度已检验不脆弱）
VOL_STEP_RATIO_JUMP = 1.5    # 起量判据：5日均量 / 前5日均量
VOL_T0_VOLBASE_MIN = 3.0     # 起量判据：T0当日量 / 起量前20日均量（不含当日，20261002 由「含当日20日量比」改口径，阈值 2.0→2.5 补偿；20261003 用户要求「拉高量能放大幅度」2.5→3.0，剔除楚江新材 002171.SZ 0916/0917 这类 T0 量比仅 2.72 的贴线票）
VOL_STEP_MIN = 1.2           # 台阶维持：起量后每日 5日均量 >= 起量前一日 5日均量 × 本值
VOL_STEP_MAX_D = 60          # T0 到今日的最大允许交易日跨度（20261003 用户要求 15→60：T0 识别后跟踪 60 个交易日；
                             #   跟踪期间若收盘跌破「T0 前一交易日收盘价」，该 T0 即作废、不再跟踪，见 _vol_step_days）
VOL_T0_MIN_PCT = 5.0         # T0 标志日：第一根放量阳线当日涨幅下限（20260930 用户要求；20261003 起涨停日/涨停次日豁免）
VOL_SHRINK_MIN_STEP = 0.50   # 缩量比下限：下蹲日量 / T0 日量（20260930 用户要求 0.35→0.55→0.50）
VOL_ACTIVITY_MIN = 2.0       # 量能活跃度：起跳段（T0~今日）均量 / 起量前20日均量（20261002 替代原「含当日20日量比」自比口径）
VOL_SQUAT_EXPAND_VOLR_MAX = 1.6   # 「扩大」下蹲分支的量比上限（当日量/20日均量，20261003 用户要求 1.2→1.6：纳入「回调后十字星/小实体但量能仅持平」的回踩，如 0923 大金重工 量比1.22、0929 天顺风能 量比1.52；优选分支仍守 1.2）
VOL_SQUAT_DRYUP_DVOL5_MAX = -50.0  # 下蹲剔除门槛①「缩量过快」：5日量能变化 <= 本值（%，20261006 用户要求）
VOL_SQUAT_DRYUP_STREAK_MIN = 3     # 下蹲剔除门槛②「持续缩量」：连续缩量天数 >= 本值（20261006 用户要求）
#   两条件同时满足 → 剔除下蹲买点（放量资金已撤、量能枯竭，非健康缩量回踩企稳）
#   典型：002584 西陇科学 0917 暴量见顶后 9 日单边缩量（5日量能变化 -52.8%、连续缩量3天、量能降至峰值25%）

# ===== 基本面否决层（20261008 用户要求）=====
# 用户明确「不要把基本面做成复杂评分，只做 3级 Gate」，故只用少量财务判据做定性分档，
# 不引入任何加权系数、不参与 FinalEntryScore / 量能爆发评分：
#   F0 恶化 → 命中「任意 2 条 F0 判据」→ 下蹲信号剔除出池（降级，不再可执行/落库）
#   F1 中性 → 正常进入技术模型（默认档，数据缺失亦归此档，fail-soft 不误判 F0）
#   F2 改善 → 命中「F2 判据（A 口径：扣非同比>+15% 必需 + 营收/现金流至少 1 条）」→ 下蹲池内进入优先池（排序前置），并在报告标注
# 判据（3 条财务，20261008 口径拍板：行业景气改善先不做）：
#   F0：①扣非利润同比 <-30%  ②营收同比 <-20%  ③经营现金流为负且同比 <-50%  ④最近两期ROE为负且恶化（任意 2 条）
#   F2：①扣非利润同比 >+15%（硬门槛，必需）+ ②营收同比 >0 / ③经营现金流为正 至少 1 条
# 数据源（20261008 口径拍板「两者结合」，见 _load_fundamental_gate）：
#   实盘 = fin_ind_*_full.parquet 快照的 dt_netprofit_yoy（扣非同比）；
#   回测/兜底 = stock_data.db :: fina_indicator_cache 按 ann_date as-of 的 netprofit_yoy（净利同比代理）。
FIN_GATE_DT_YOY_F0 = -30.0    # F0①：扣非利润同比下限
FIN_GATE_OR_YOY_F0 = -20.0    # F0②：营收同比下限
FIN_GATE_OCF_YOY_F0 = -50.0   # F0③：经营现金流同比下限（须同时为负）
FIN_GATE_DT_YOY_F2 = 15.0     # F2①：扣非利润同比下限
FIN_GATE_DB_PATH = r"D:\mystock\cache_daily\stock_data.db"   # fina_indicator_cache 所在库（= stock_cache.DB_PATH）


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
# 主题关联（复用 theme_stock_map / theme_score_v2 报告）
# =========================
def _load_mainline_rotation_themes(trade_date):
    """读取 theme_score_v2.py 生成的 theme_analysis_v2_{date}.txt，
    提取 主线(▶)+轮动(▸) 主题作为主题过滤范围（与 tushare_quant.filter_by_top_themes 同源）。

    报告行格式：
      主线: ▶ 半导体 [主升] 情绪+趋势共振 质量90 | 策略:龙头+中军 | 趋势81 情绪74 涨停13 迁移11.0
      轮动: ▸ 可控核聚变 [升温] 非主线 质量55 | 趋势50 综合51 涨停1 迁移6.9
      回避: ✕ 消费 [退潮] 综合34 → 【坚决回避/清仓】
    返回 {主题名: {kind, stage, trend_score, sentiment_score, composite_score, trade_signal, ...}}
    """
    path = os.path.join(BASE_DIR, "report_daily", f"theme_analysis_v2_{trade_date}.txt")
    if not os.path.exists(path):
        print(f"[主题关联] 未找到主题评分报告: {path}")
        return {}

    themes = {}
    section = None
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()
    for ln in lines:
        ln = ln.strip()
        if ln.startswith('### 第一部分'):
            section = 'mainline'
            continue
        if ln.startswith('### 第二部分'):
            section = 'rotation'
            continue
        if ln.startswith('### 第三部分'):
            section = 'junk'
            continue
        if ln.startswith('### 主线与轮动交易决策表'):
            break
        if section not in ('mainline', 'rotation', 'junk'):
            continue
        if not (ln.startswith('▶') or ln.startswith('▸') or ln.startswith('✕')):
            continue
        m = re.match(r'^[▶▸✕]\s+(\S+)\s+\[([^\]]+)\](.*)$', ln)
        if not m:
            continue
        theme, stage, rest = m.group(1), m.group(2), m.group(3)
        _num = lambda pat: (lambda mm: float(mm.group(1)) if mm else 0.0)(re.search(pat, rest))
        themes[theme] = {
            'theme': theme, 'stage': stage, 'kind': section,
            'trend_score': _num(r'趋势([\d.]+)'),
            'sentiment_score': _num(r'情绪([\d.]+)'),
            'composite_score': _num(r'综合([\d.]+)'),
            'trade_signal': '看多' if section == 'mainline' else ('回避' if section == 'junk' else '关注'),
        }
    # 补充解析"主线与轮动交易决策表"：报告第三部分 ✕ 只列前3个，
    # 其余回避主题在决策表中以"回避"类型出现，补全到 junk 集合
    in_decision = False
    for ln in lines:
        if ln.startswith('### 主线与轮动交易决策表'):
            in_decision = True
            continue
        if in_decision:
            if ln.startswith('### '):
                break
            m = re.match(r'^\s*\d+\s+(\S+)\s+回避\s+\S+\s+\S+\s+\S+\s+(\S+)', ln)
            if m and m.group(1) not in themes:
                themes[m.group(1)] = {
                    'theme': m.group(1), 'stage': m.group(2), 'kind': 'junk',
                    'trend_score': 0, 'sentiment_score': 0, 'composite_score': 0,
                    'trade_signal': '回避',
                }
    return themes


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
        else:
            # 不再回退到无日期后缀的 V6 旧文件：过期主题结果会让当日
            # 评分静默使用旧数据（热度/阶段信号失真），宁可走"数据不足"。
            print(f"[V8] 引擎结果不存在: {v8_json_path or v8_csv_path}")
            print(f"  请先运行: python theme_alpha_v6/main.py --date {expected_date or ''}")
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
    junk_themes_info = {}
    theme_state_map = {}
    try:
        theme_report_data = _load_mainline_rotation_themes(TRADE_DATE)
        if theme_report_data:
            for tname, r in theme_report_data.items():
                if r.get('kind') == 'junk':
                    # 回避区主题单独保留：命中时标注"(回避)"
                    junk_themes_info[tname] = r
                    continue
                keep_themes.append(tname)
                theme_state_map[tname] = {
                    'theme_state': r.get('trade_signal', ''),
                    'trend_score': float(r.get('trend_score', 0) or 0),
                    'sentiment_score': float(r.get('sentiment_score', 0) or 0),
                    'composite_score': float(r.get('composite_score', 0) or 0),
                    'cycle_phase': r.get('stage', ''),
                }
    except Exception as e:
        print(f"[添加主题] 读取主题评分报告失败: {e}")

    if not keep_themes:
        # 主线/轮动为空（如20260819弱市全部主题进回避区）：不提前返回，
        # 降级为仅用回避区主题做"(回避)"标注，避免全部显示"无主题"丢失信息
        print("[添加主题] 主线/轮动为空，降级为仅标注回避区主题")

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
            # 无主线/轮动匹配：检查是否属于回避区主题（标注"(回避)"）
            junk_hit = ''
            for jname in junk_themes_info:
                if ts_code in theme_stock_map.get(jname, {}):
                    junk_hit = jname
                    break
            if junk_hit:
                jv = junk_themes_info[junk_hit]
                matched_themes.append(f"{junk_hit}(回避)")
                match_scores.append(0)
                secondary_themes_list.append('')
                theme_states_list.append('回避')
                cycle_phases.append(jv.get('stage', ''))
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


def batch_chip_alpha(stocks, lookback_days=20, end_date=None):
    """end_date 必须传目标交易日：缺省时引擎会回退到运行当天，历史日期重算将引入前视数据"""
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
            r = engine.analyze(ts_code, end_date=end_date, lookback_days=lookback_days)
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
    _os = None
    try:
        from chip_alpha_v5 import calc_opportunity_score
        _os = calc_opportunity_score(v5_result)
    except Exception:
        _os = None
    return {
        'Alpha_Structure': a.get('Structure', 50),
        'Alpha_Flow': a.get('Flow', 50),
        'Alpha_Momentum': a.get('Momentum', 50),
        'Alpha_Composite': a.get('Composite', 50),
        'Alpha_Grade': a.get('Grade', 'C'),
        'Risk_Score': r.get('Composite', 50),
        'Risk_Level': r.get('Level', 'Medium'),
        'Trend_State': t.get('current_state', 'Unknown'),
        'Trend_Desc': t.get('description', ''),
        'Next_State': tr.get('primary_next', 'Unknown'),
        'Next_Prob': tr.get('primary_prob', 0),
        'Action': d.get('action', 'Hold'),
        'Confidence': d.get('confidence', 50),
        'DecisionSummary': d.get('combined', ''),
        'Opportunity_Score': _os['score'] if _os else 50.0,
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


def _vol_step_days(vol_arr, close_arr, open_arr, pre_close_arr, zt_line=9.8, allow_zt=False):
    """起量台阶识别（20260927；20260930 起 T0 须为「涨幅>5% 的放量阳线」，并作为缩量比的基准）。

    语义（用户口径，20261002 修正）：某日起量（5日均量较前5日均量跳升 >=1.5 倍，
    且当日量 >= 起量前20日均量（不含当日）VOL_T0_VOLBASE_MIN 倍，
    且当日为放量阳线、涨幅 >VOL_T0_MIN_PCT），此后每日 5日均量始终 >= 起量前一日 5日均量的 1.2 倍
    → 量能台阶维持、资金未走。该日即 T0 标志日。
    返回 (距 T0 天数 d, 相对 T0 收盘涨幅小数, T0 当日涨幅小数, T0 开盘价)；无合格 T0 返回 None。

    涨停兜底（20261003 用户要求）：「T0 可以是涨停，涨停次日放量就不要求涨幅」。
    调用方先用严格口径（allow_zt=False）找 T0；找不到时才以 allow_zt=True 再找一次。
    兜底口径下，若 T0 日 i 为涨停日（涨幅 >= zt_line）或其前一交易日 i-1 为涨停日，豁免
      (a) 5日均量「新跳升」条件（涨停通常是放量的第 2、3 天，5日均量比已 >=1.5）
      (d) 阳线 + 涨幅 >VOL_T0_MIN_PCT 条件（涨停日涨幅天然足够；涨停次日常为小阳/平盘）
    但量能条件不豁免：T0 当日量仍须 >= 起跳前20日均量 3.0 倍，且台阶维持到今日。
    之所以两段式而非直接豁免：直接豁免会让「已有严格 T0」的个股 T0 被改认到更近的涨停次日，
    从而改写缩量比基准与距T0开盘（实测大港股份 002077.SZ 的 T0 由 0909 涨停日被改认为 0910 涨停
    次日 +2.26%，导致 0930 缩量比 0.546→0.414、距T0开盘 +9.27%→-0.65%，原本的 0930 下蹲信号丢失）。

    优先认涨停日本身（20261006 用户要求，天龙股份 603266.SH 案例）：兜底拆成两轮扫，
      第一轮只豁免「涨停日本身」（_zt_exm = is_zt[i]），第二轮才放宽到「涨停日或涨停次日」。
      原因：单轮由近及远扫描会先撞上「涨停次日」并立即返回 —— 天龙股份 0915 涨停（5日均量比
      仅 1.38 不足 1.5，严格口径不认），单轮兜底把 T0 认到 0916（涨停次日，当日量为全段最大），
      缩量比基准被抬高 1.49 倍，把 0923/0924 的缩量比从 0.70 压到 0.47，误剔两个下蹲买点；
      两轮后 0915（涨停日本身）被正确认作 T0。

    T0 跟踪窗口与失效（20261003 用户要求）：
      (1) T0 识别后跟踪至多 VOL_STEP_MAX_D 个交易日（15→60）：起量日可回溯更远，
          解决「放量后回调较久、起量日滑出窗口 → 无合格 T0 被整段剔除」的漏检
          （如千金药业 600479.SH 的 T0=0826，到 0930 已距 22 个交易日，旧 15 日窗口早失效）。
      (2) 跟踪期间（T0 次日起至今日）若任一交易日**收盘跌破「T0 前一交易日收盘价」**
          （即整波放量被完全回吐、起涨基石被击穿）→ 该 T0 作废、不再跟踪，继续向前找更早的 T0。
          判据用收盘价（与策略内其它止损口径一致），不用盘中最低价，避免上下影误杀。

    量比口径说明（20261002 用户要求）：T0 与「量能活跃度」一律用「起跳后 vs 起跳前基线」，
    基线为起量前 20 日均量（不含 T0 当日及起跳段）；不再使用「当日量 / 含当日20日均量」的自比口径
    （该口径分母含起量巨量，会自我稀释，如百普赛斯 20260807 涨停放量日被稀释为 1.99 < 2.0 而误剔）。

    标定（下蹲事件池 n=383，T+1开盘买/T+5收盘/盘中-7%止损/含0.25%成本）：
      T0 = 量能台阶起点、不限涨幅：n=164 42.1%/-0.15%/止损37.2%
      T0 再加「涨幅>5% 且阳线」  ：n=89  51.7%/+0.57%/止损32.6%
        （阈值敏感度：>3% 46.8% / >4% 48.5% / >5% 51.7% / >6% 48.4% / >7% 49.1% → 5% 最优）
      无合格 T0 者            ：n=294 35.4%/-0.94%/止损39.1%
      有量能台阶但 T0<=5% 者   ：n=75  30.7%/-1.01%/止损42.7%（最差，进一步支持 5% 阈值）

    口径改后重标定（20261002，事件基底 n=6040，回测口径 T+1开盘买/T+5收盘/T0开盘价止损/含0.25%成本）：
      新口径是旧口径的**严格超集**（旧基准 k1_old=2.0 命中 12 例，全部落在新口径 k1=2.2/k2=2.0 的 17 例内，
      旧口径额外命中 0 例）——即旧的「近20日自比 max>=2.0 & mean>=1.4」闸门确实在额外过滤，且多出的 5 例
      净值偏负（-3.12/+0.90/+5.87/-12.75/+0.93，均值 -1.63%），故新口径需上调阈值以补偿分母变小后的放大。
      T0 阈值（k2=0，只扫 k1）：2.0 → n=34 50.0%/+0.44% · 2.2 → n=30 50.0%/+0.75%
                               2.5 → n=20 55.0%/+2.61% · 3.0 → n=12 58.3%/+3.57%
      活跃度阈值（k1=2.5）：1.8 → n=16 50.0%/+2.63% · **2.0 → n=14 57.1%/+3.38%** · 2.2 → n=13 53.8%/+3.18%
      最终取值 k1=VOL_T0_VOLBASE_MIN=2.5、k2=VOL_ACTIVITY_MIN=2.0 → n=14 57.1%/+3.38%/止损28.6%，
      与改动前旧口径基线（n=12 58.3%/+4.36%/止损16.7%）选择性相当（差值在 n≈12~14 的噪声范围内）。
      k1 上调（20261003 用户要求「拉高量能放大的幅度」，楚江新材 002171.SZ 0916/0917 T0 量比仅 2.72）：
      k1=3.0 即上表「3.0 → n=12 58.3%/+3.57%」，胜率与均收益均优于 k1=2.5，故采用 3.0。

    涨停兜底实测（20261003）：天顺风能 002531.SZ 20260910 涨停（5日均量比 1.39 不足 1.5）、
      20260915 涨停（前一日 5日均量比 3.83 已 >=1.5），严格口径两头不落 → 0916 起「无合格 T0」被全数剔除；
      兜底后 0915 被认作 T0，0916~0918/0924~0930 共 7 日通过全部闸门。
    """
    n = len(vol_arr)
    if n < 40:
        return None
    v = pd.Series(vol_arr)
    ma5 = v.rolling(5).mean().values
    ratio = (v.rolling(5).mean() / v.rolling(5).mean().shift(5)).values
    # 起量前20日均量（不含当日，20261002）：T0 当日量相对「起跳前基线」的放大倍数
    pre20 = v.rolling(20).mean().shift(1).values
    v_t0 = v / np.maximum(pre20, 1)
    # 涨停识别（20261003 用户要求：T0 可为涨停日，涨停次日放量不要求涨幅）
    _pct_all = np.where(pre_close_arr > 0,
                        (close_arr / np.maximum(pre_close_arr, 1e-9) - 1) * 100, -999.0)
    is_zt = _pct_all >= zt_line
    k = n - 1
    # 兜底豁免模式（20261006 用户要求，天龙股份 603266.SH 案例）：严格口径只有一轮（无豁免）；
    #   兜底口径分两轮扫——第一轮只豁免「涨停日本身」（优先认涨停日），第二轮才放宽到
    #   「涨停日或涨停次日」。单轮由近及远会先撞上「涨停次日」并立即返回，从而把 T0 认错
    #   （天龙股份 0915 涨停被跳过、认到 0916 涨停次日，缩量比基准抬高 1.49 倍、误剔 0923/0924）。
    _exm_modes = [(False, False)] if not allow_zt else [(True, False), (True, True)]
    for _use_zt, _allow_prev_zt in _exm_modes:
        for i in range(k - 1, max(-1, k - 1 - VOL_STEP_MAX_D), -1):
            if i - 1 < 0 or np.isnan(ratio[i]) or np.isnan(ratio[i - 1]):
                continue
            _zt_t0 = bool(is_zt[i])
            _zt_prev = bool(is_zt[i - 1])
            # 兜底口径下涨停日（第二轮起含涨停次日）豁免 (a)(d)（涨停本身即标志性放量）
            _zt_exm = _use_zt and (_zt_t0 or (_allow_prev_zt and _zt_prev))
            if not _zt_exm:
                if ratio[i] < VOL_STEP_RATIO_JUMP or ratio[i - 1] >= VOL_STEP_RATIO_JUMP:
                    continue
            if np.isnan(v_t0[i]) or v_t0[i] < VOL_T0_VOLBASE_MIN or not (ma5[i - 1] > 0):
                continue
            seg = ma5[i:k + 1] / ma5[i - 1]
            if np.isnan(seg).any() or seg.min() < VOL_STEP_MIN:
                continue
            if pre_close_arr[i] <= 0:
                continue
            # T0 失效判定（20261003 用户要求）：跟踪期间（T0 次日起至今日）若任一交易日
            #   收盘跌破「T0 前一交易日收盘价」→ 整波放量已被完全回吐、起涨基石被击穿，
            #   该 T0 作废、不再跟踪，继续向前找更早的 T0。判据用收盘价，不用盘中最低价。
            if i + 1 <= k and float(np.min(close_arr[i + 1:k + 1])) < float(pre_close_arr[i]):
                continue
            _t0_pct = (close_arr[i] / pre_close_arr[i] - 1) * 100
            # (d) 放量阳线且涨幅达标：兜底口径下涨停日 / 涨停次日豁免（20261003）
            if not _zt_exm:
                if close_arr[i] <= open_arr[i] or _t0_pct <= VOL_T0_MIN_PCT:
                    continue
            return (k - i, float(close_arr[k] / close_arr[i] - 1), float(_t0_pct),
                    float(open_arr[i]))
    return None


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
        open_arr = recent['open'].values.astype(float)
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
        amp_gt8_count = int(np.sum(amplitude > 8))
        # 日均振幅 avg_amplitude 改到 T0 识别之后再算（20261003 用户要求：只统计「放量后」的大幅波动）

        range_high = float(np.max(high_arr))
        range_low = float(np.min(low_arr))
        range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0

        price_change = (close_arr[-1] / close_arr[0] - 1) * 100 if close_arr[0] > 0 else 0

        # 硬条件（20260821放宽后回测为负优化，恢复原阈值：胜率67%/均+8.96% 优于放宽后30%/-0.11%）
        if max_vol_ratio < 2.6:
            return None
        # 量能爆发频次硬条件（用户要求20260927：量比>2 的天数须 >=5，原为 >=3）
        if vol_ratio_gt2 < 5:
            return None
        # 日均振幅硬条件 avg_amplitude<4.5（原数值）已下移至 T0 识别之后 ——
        #   20261003 用户要求改口径：改测「放量后（T0 起）」的日均振幅，需先定位 T0 才能计算
        if range_swing < 35:
            return None
        # 区间涨幅(200日)仅展示不做硬过滤：短线策略不设长周期涨跌幅限制
        # 排除今日大跌/跌停
        today_pct = (close_arr[-1] / pre_close_arr[-1] - 1) * 100 if pre_close_arr[-1] > 0 else 0
        if today_pct <= -7.0:
            return None
        # 涨停线（双创(30x/688) 20%，主板 10%）
        # 20261003 用户要求「去掉涨停剔除」：不再因当日涨停直接剔除，涨停日可进池，
        #   改由后续「下蹲结构 / 距MA20·MA30 位置 / 缩量比」等结构条件把关；
        #   该线仍透传给 _vol_step_days 用于「涨停兜底」T0 识别。
        _code6 = str(ts_code).split('.')[0]
        _zt_line = 19.5 if _code6.startswith(('300', '301', '688')) else 9.8
        if len(df) < 180:
            return None
        if vol_vs_hist_pct < 50:
            return None

        # MA20 位置门槛已删除（20261003 用户要求「放弃和均线的比较」）：
        #   原「收盘不得跌破 MA20 的 95%」（距MA20 < -5% 直接剔除）属均线比较，
        #   下跌价位改由 _vol_step_days 的 T0 失效门槛单一口径把关（跟踪期间收盘跌破 T0 前一交易日收盘价 → T0 作废）。
        #   （此前 20261003 已先删除「10日变化 < -1% 或 20日变化 < -2% → 剔除」）

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

        # 起量台阶 / T0 标志日（用户口径 20260930：近期第一根「放量阳线、涨幅>5%」的起量日）
        #   20261003 涨停兜底：严格口径找不到 T0 时，允许「涨停日 / 涨停次日」作 T0（见 _vol_step_days）
        _step = _vol_step_days(vol_arr, close_arr, open_arr, pre_close_arr, _zt_line)
        if _step is None:
            _step = _vol_step_days(vol_arr, close_arr, open_arr, pre_close_arr, _zt_line, allow_zt=True)
        if _step is None:
            return None

        # 日均振幅（20261003 用户要求改口径）：只统计「放量后（T0 标志日起至今）」的日均振幅，
        #   替代原「近120日」长期均振幅 —— 原口径会整段误杀「放量前长期低波动、放量后剧烈震荡」的品种
        #   （千金药业 600479.SH：T0=0826 起量后日均振幅 ≈9%，近120日仅 ≈3.5%，被旧口径挡在池外）。
        #   口径一致：振幅硬门槛、amp_score、报告「日均振幅」下游均随此定义改为「放量后」。
        _t0_i = len(vol_arr) - 1 - _step[0]
        avg_amplitude = float(np.mean(amplitude[_t0_i:]))
        if avg_amplitude < 4.5:
            return None

        # 量能活跃度硬条件（20261002 用户口径修正）：「起跳后每日成交显著高于起跳前均量」
        #   分子 = 起跳段（T0~今日，含 T0）均量；分母 = 起量前20日均量（不含 T0 当日，即起跳前基线）
        #   替代原「近20日 量比=当日量/含当日20日均量，max>=2.0 且 mean>=1.4」的自比口径
        #   （自比口径分母含起量巨量会自我稀释，且以「今日」为锚会随下蹲时长漂移）
        #   标定：k1=2.5 时 1.8→n=16 50.0%/+2.63% · 2.0→n=14 57.1%/+3.38% · 2.2→n=13 53.8%/+3.18%
        #   （回测口径 T+1开盘买/T+5收盘/T0开盘价止损/含0.25%成本；详见 _vol_step_days docstring）
        _base_pre20 = float(np.mean(vol_arr[max(0, _t0_i - 20):_t0_i])) if _t0_i > 0 else 0.0
        _surge_mean = float(np.mean(vol_arr[_t0_i:]))
        vol_activity = _surge_mean / max(_base_pre20, 1)
        if _base_pre20 <= 0 or vol_activity < VOL_ACTIVITY_MIN:
            return None

        # 缩量比：下蹲日量 / T0 日量（量能缩小过快则剔除）
        # 基准锚点（用户指定）：海峡创新 300300.SZ T0=0904 量 1,563,037 → 0923 下蹲日量
        #   880,260 = 0.563，属健康回踩（次日 +19.98%）；光电股份 600184.SH 0914 起量但当日仅
        #   +3.00% 不达 5% → 无合格 T0 剔除（量能连缩 6 天，后续 -4.00%/-7.15%）。
        # 标定（下蹲事件池 n=383，T+1开盘买/T+5收盘/盘中-7%止损/含0.25%成本）：
        #   无合格 T0 n=294 仅 35.4%/-0.94%/止损39.1% → 硬过滤剔除（见 _vol_step_days 标定）
        #   合格 T0 n=89：缩量比 >=0.35 → n=64 56.2%/+1.28%/止损29.7%
        #                 <0.35  → n=25 40.0%/-1.27%/止损40.0%
        #                 >=0.45 → n=45 62.2%/+2.58%/止损24.4%（阈值可再收紧，暂守 0.35）
        # 20260930 用户要求阈值 0.35→0.55→0.50（旧口径全量重标定，T0 合格事件池 2025-10~2026-09 n=99，
        #   T+1开盘买/T+5收盘/盘中-7%止损/含0.25%成本）：
        #   缩量比下限 >=0.35 n=72 51.4%/+0.90%/止损30.6%
        #              >=0.40 n=59 52.5%/+1.43%/止损28.8%
        #              >=0.45 n=50 58.0%/+2.38%/止损26.0%
        #              >=0.50 n=37 62.2%/+3.71%/止损21.6%  ← 现行门槛（样本更足且胜率优于 0.55 档）
        #              >=0.55 n=22 59.1%/+4.30%/止损22.7%
        #              >=0.60 n=15 53.3%/+3.41%/止损20.0%
        #   区间分档：[0.35,0.45) n=22 36.4%/-2.46% · [0.45,0.50) n=13 46.2%/-1.43%
        #             [0.50,0.55) n=15 66.7%/+2.85% · [0.55,∞) n=22 59.1%/+4.30%
        #   备注：0.50 恰在弱档 [0.45,0.50) 之上切一刀，兼顾样本量与胜率
        # 20261002 T0/量能活跃度改口径后（下蹲事件池 n=27）：池内缩量比最小值 ≈0.51，
        #   即 0.50 门槛已不再起筛选作用（>=0.35/0.40/0.45/0.50 均为 n=27 48.1%/+0.88%/止损40.7%）；
        #   区间分档 [0.50,0.55) n=13 61.5%/+0.66% · [0.55,∞) n=14 35.7%/+1.09% → 暂保留 0.50 门槛
        _shrink_base = float(vol_arr[len(vol_arr) - 1 - _step[0]])
        _vol_shrink = float(vol_arr[-1]) / max(_shrink_base, 1)
        vol_shrink_ratio = round(_vol_shrink, 2)
        if _vol_shrink < VOL_SHRINK_MIN_STEP:
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
        # ===== P0：导出力能爆发 5 个分量的原始值（20261007）=====
        # 动机：total_score 的 5 个分量全是 min(值/固定阈值, 1)*权重 的封顶项，
        #   放量行情中普遍顶格 → 实测池内评分中位 93.3、>=95 占比 41.7%、=100 占 16.7%，
        #   「评分>=70/75」门槛通过率 99.6%/98.3% → 门槛形同虚设（20260930 强买 37 只的根因）。
        # 处置：分数无法在 detect 内修正（逐只调用、无池上下文），
        #   故此处只导出原始分量，由 run() 在全池收集完后算「每日池内截面百分位」
        #   （cross_score），供强买门槛与报告展示使用。入池门槛仍用原 total_score>=65，
        #   以保证入池口径与历史回测可比。
        # 标定（下蹲事件池 n=9180，2025-01~2026-09，T+1开盘买/T+5收盘/止损位=T0前一日收盘价）：
        #   评分饱和度：唯一值 326 → 7639，中位 93.3 → 51.1，>=95 占比 41.7% → 0.0%
        #   门槛通过率：>=70 99.6% → 10.7%   >=75 98.3% → 4.8%（门槛恢复区分力）
        #   排序（每日Top3）：旧 41.5%/-0.35%/止21% → 新 44.0%/-0.08%/止20%（分年一致改善）
        #   但与「距MA5 第1键」组合后增益很小：44.7% → 44.9%（距MA5 已承担主要排序工作）
        #   **方向性结论（重要）**：两种口径一致指向「分数越低越好」——
        #     距MA20>0 子池内，旧评分 <90档 -0.37% / 96~100档 -0.87%（跨度 0.5pp）；
        #     截面分位 <50档 -0.09% / >80档 -1.67%（跨度 1.58pp，区分度 3 倍）。
        #     故原强买门槛「total_score>=70/75」（要求高分）在筛的恰是过热档 → 方向错，
        #     已于 20261007 改为用截面分位的高分位（见 _strong_buy_gate）。
        _comp = {
            'vol': float(max_vol_ratio),
            'freq': float(vol_ratio_gt2),
            'amp': float(avg_amplitude),
            'big_amp': float(amp_gt8_count),
            'swing': float(range_swing),
        }

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

        # MACD 状态（20260927 起降为纯描述字段）：只写入 MACD状态/死叉临界 供报告展示，
        # 不再决定标的入池、评级、资格、评分或排序（用户定调「MACD 没意义」）
        macd_status = ''
        if prev_bar < 0 < cur_bar:
            macd_status = '刚刚红柱 ✅'
        elif cur_bar < 0 and cur_bar > prev_bar > prev2_bar:
            macd_status = '即将红柱（绿柱连续缩短）'
        elif cur_bar > 0 and prev_bar > 0 and cur_bar < abs(macd_bar[-4]) * 0.7:
            macd_status = '红柱回调缩短（趋势延续）'
        elif cur_bar > 0 and prev_bar > 0 and cur_bar > prev_bar and prev_bar < prev2_bar:
            macd_status = '红柱回调后反弹（趋势延续）'

        # 死叉临界识别（20260817落地）：红柱回调缩短分支内，红柱已缩至极小 → 1~2日内可能死叉
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
        ma30_latest = pd.Series(close_arr).rolling(30).mean().values[-1]
        pos_ma30 = (close_latest / ma30_latest - 1) * 100 if not np.isnan(ma30_latest) and ma30_latest > 0 else 0

        # ===== V2.0 次日新开仓模型输入（扩展字段，20260821） =====
        _ma5 = pd.Series(close_arr).rolling(5).mean().values[-1]
        _ma10 = pd.Series(close_arr).rolling(10).mean().values[-1]
        pos_ma5 = (close_latest / _ma5 - 1) * 100 if not np.isnan(_ma5) and _ma5 > 0 else 0
        pos_ma10 = (close_latest / _ma10 - 1) * 100 if not np.isnan(_ma10) and _ma10 > 0 else 0
        _chg5 = (close_arr[-1] / close_arr[-6] - 1) * 100 if len(close_arr) >= 6 and close_arr[-6] > 0 else 0
        _chg10 = (close_arr[-1] / close_arr[-11] - 1) * 100 if len(close_arr) >= 11 and close_arr[-11] > 0 else 0
        # 60日涨幅（下蹲排序用：前期涨幅越小，二次启动的透支风险越低）
        _chg60 = (close_arr[-1] / close_arr[-61] - 1) * 100 if len(close_arr) >= 61 and close_arr[-61] > 0 else 0
        _up_streak = 0
        for _k in range(len(close_arr) - 1, 0, -1):
            if close_arr[_k] > close_arr[_k - 1]:
                _up_streak += 1
            else:
                break
        _yang_streak = 0
        for _k in range(len(close_arr) - 1, 0, -1):
            if close_arr[_k] > pre_close_arr[_k]:
                _yang_streak += 1
            else:
                break
        _tr_arr = np.maximum(high_arr - low_arr, np.abs(close_arr - pre_close_arr))
        _atr20_now = float(np.mean(_tr_arr[-20:]))
        _atr20_prev = float(np.mean(_tr_arr[-40:-20])) if len(_tr_arr) >= 40 else _atr20_now
        atr_expand = _atr20_now / max(_atr20_prev, 0.01)
        _upper_shadow = 0.0
        for _k in range(max(0, len(close_arr) - 3), len(close_arr)):
            _rng = max(high_arr[_k] - low_arr[_k], 0.01)
            _us = (high_arr[_k] - max(close_arr[_k], pre_close_arr[_k])) / _rng
            _upper_shadow = max(_upper_shadow, _us)
        _red_shrink = 0
        for _k in range(len(macd_bar) - 1, max(0, len(macd_bar) - 6), -1):
            if macd_bar[_k] < macd_bar[_k - 1] and macd_bar[_k] > 0:
                _red_shrink += 1
            else:
                break
        # 量价结构：近10日 涨日量均 vs 跌日量均（>1 = 上涨放量/回调缩量健康）
        _up_vols, _dn_vols = [], []
        for _k in range(max(0, len(close_arr) - 10), len(close_arr)):
            _pc = close_arr[_k - 1] if _k > 0 else close_arr[_k]
            if close_arr[_k] > _pc:
                _up_vols.append(vol_arr[_k])
            elif close_arr[_k] < _pc:
                _dn_vols.append(vol_arr[_k])
        _vol_up_ratio = (np.mean(_up_vols) / max(np.mean(_dn_vols), 0.01)) if _up_vols and _dn_vols else 1.0
        # MA20 趋势（供新开仓模型位置判断）
        # 注：ma20_full 原定义在已删除的「MA20 位置门槛(G6)」段内（20261003 删除均线比较时一并移除），
        #   此处按邻接口径补回，仅用于 ma20_trend 展示字段，不参与筛选。
        ma20_full = pd.Series(close_arr).rolling(20).mean().values
        if len(ma20_full) >= 21 and not np.isnan(ma20_full[-1]) and not np.isnan(ma20_full[-11]) and ma20_full[-11] > 0:
            _ma20_chg = (ma20_full[-1] / ma20_full[-11] - 1) * 100
        else:
            _ma20_chg = 0.0
        if _ma20_chg >= 0.5:
            ma20_trend = 'up'
        elif _ma20_chg >= -0.5:
            ma20_trend = 'flat'
        else:
            ma20_trend = 'down'

        # ===== 下蹲买点（20260927落地；20260930 位置门槛放宽；20261003 扩大小实体K线分支；
        #       20261003 用户要求「放弃和均线的比较 / 距20日高也不要」：删除两支内全部均线条件与距20日高区间，
        #       下跌价位只由「跌破 T0 前一交易日收盘价 → T0 作废」这一门槛单一口径把关，见 _vol_step_days）=====
        # 语义：量能爆发/宽幅震荡结构已成立（T0 未失效），当日缩量回踩 → 提前于突破日给低吸买点
        #       （用户要求：下蹲时即发信号，而非等突破后再追）
        # 20261003 用户要求「放弃和均线的比较」「距20日高也不要，是指跌破T0日前一日的收盘价则不再跟踪这个门槛已经有了」：
        #   删除 优选/扩大 两支中的所有均线比较（MA5>MA10>MA20 多头、距MA5≤1%、距MA20≤5%(优选)/≤10%(扩大)、
        #   距MA30≥0%）与「距20日高」区间条件。下跌价位只由 _vol_step_days 的 T0 失效门槛
        #   （跟踪期间收盘跌破 T0 前一交易日收盘价 → 该 T0 作废）单一口径把关，避免多重冗余把关。
        #   注：MA5<MA10 的二次启动前回踩不再被排除（与用户偏好一致）；「距MA20/距MA30/距MA5/距20日高」仍作为展示字段输出。
        # 20261003 用户要求「把下蹲买点扩大，找到回调后的小阴线小阳线或十字星」：
        #   在原有「优选」分支（缩量回踩、当日缩量企稳）之外，新增「扩大」分支 ——
        #   回调后出现 小阴线/小阳线/十字星（小实体K线，实体≤2%）。
        # 标定回测（2025-01-01~2026-09-24, 事件池=本函数基础硬过滤, T+1开盘买/T+5收盘, 盘中-7%止损, 含0.25%成本）：
        #   旧口径（距MA20 0~5%）n=616 胜率46.6% 均+0.69% 止损率29.5% | 2025 48.2%/+0.67% | 2026 42.4%/+0.75%
        #   20260930 用户要求「放宽到30日均线之上」：下限由 距MA20>=0 放宽为 距MA30>=0，
        #   上限仍为 距MA20<=5%（不追高不变）→ 该放宽未单独重标定
        #   20261002 T0/量能活跃度改口径后，下蹲事件池（T0合格+下蹲结构）n=27，见下方与报告内数字
        #   20261003 删除均线与距20日高条件后未重标定（门槛放宽 → 池会变大，胜率待重算）
        _hh20 = float(np.max(high_arr[-20:])) if len(high_arr) >= 20 else 0.0
        dist_hh20 = (close_latest / _hh20 - 1) * 100 if _hh20 > 0 else 0.0
        # 止损空间门槛（20261001 B 方案）已于 20261003 用户要求删除：下蹲条件不再要求
        #   「下蹲日收盘距 T0 开盘价≥5%」，下跌价位只由 _vol_step_days 的 T0 失效门槛把关。
        t0_open = round(float(_step[3]), 2)
        # ===== 止损位口径 20261006 用户要求：改用「T0 前一交易日收盘价」=====
        # 口径含义：跌破「T0 起涨前最后一个交易日的收盘价」即止损 —— 即整波放量被完全回吐、
        #   起涨基石被击穿（与 _vol_step_days 的 T0 作废门槛**同一条线**，单一口径把关）。
        # 口径对照（下蹲事件池 n=8733，2025-01~2026-09，T+1开盘买/T+5收盘/含0.25%成本）：
        #   口径                        胜率%   均收益%  止损率%
        #   T0前一日收盘价（新）         41.5    -0.29    12.2
        #   T0开盘价（原）              40.0    -0.27    16.8
        #   分年一致（非单年噪声）：
        #     2025  新 43.4%/+0.42%/止 9.4%   原 42.6%/+0.43%/止12.1%
        #     2026  新 40.9%/-0.51%/止13.1%   原 39.2%/-0.48%/止18.2%
        #   新口径止损线距 T+1 开盘价：中位 -17.5%（5%分位 -45.7%、95%分位 -3.5%），
        #   即多数票的止损空间有 10~20%，实盘可执行，且比原口径更宽松（止损率降 4.6pp）。
        # 曾评估但否决的替代口径（同样按 n=8733 实测）：
        #   「下蹲日（T-1）收盘价」作止损线：胜率 3.3%/止损率 96.6% —— **不可用**。
        #     原因：下蹲日本身是十字星/小实体，收盘价几乎就等于次日的实际开盘价
        #     （实测 T+1开盘 相对 T-1收盘 的中位跳空 -0.26%，56% 的票次日直接低开跌破该线），
        #     止损线紧贴入场价 → 持仓 5 日内几乎必然被触及，策略退化为"每天止损"。
        #   「事件日收盘价 ×(1-buffer)」缓冲扫描：0%/2%/3%/5%/7%/10% → 胜率
        #     3.3%/14.3%/21.2%/31.5%/37.3%/41.5%，止损率 96.6%/84.3%/75.6%/58.3%/42.7%/24.3%
        #     —— 即需 10% 缓冲才接近原口径胜率，但止损线已远离「起涨基石」语义。
        # 故取 T0 前一日收盘价：既是用户指定口径，也是唯一同时满足
        #   ①与 T0 作废门槛同源 ②止损率低于原口径 ③胜率高于原口径 的方案。
        _t0_prev_close = float(pre_close_arr[_t0_i]) if _t0_i > 0 else 0.0
        stop_price = round(_t0_prev_close, 2) if _t0_prev_close > 0 else t0_open
        # 小实体K线识别（20261003 用户要求「把下蹲买点扩大，找到回调后的小阴线小阳线或十字星」）
        #   实体 = |收盘-开盘| / 前收；小阴/小阳：实体 ≤2.0%；十字星：实体 ≤0.5% 或 实体/振幅 ≤0.25
        _today_open = float(open_arr[-1])
        _prev_close = float(pre_close_arr[-1]) if pre_close_arr[-1] > 0 else close_latest
        _body_pct = abs(close_latest - _today_open) / _prev_close * 100 if _prev_close > 0 else 0.0
        _range_pct = (float(high_arr[-1]) - float(low_arr[-1])) / _prev_close * 100 if _prev_close > 0 else 0.0
        _is_doji = (_body_pct <= 0.5) or (_range_pct > 0 and _body_pct / _range_pct <= 0.25)
        _is_small_body = _body_pct <= 2.0
        _kname = '十字星' if _is_doji else ('小阳线' if close_latest >= _today_open else '小阴线')
        # 量能形态（20261006 提前至此处计算：既供下蹲提示字段展示，也供「缩量过快且持续缩量」剔除门槛）
        _shrink_streak = 0
        for _k in range(len(vol_arr) - 1, 0, -1):
            if vol_arr[_k] < vol_arr[_k - 1]:
                _shrink_streak += 1
            else:
                break
        _dvol5 = (vol_arr[-1] / vol_arr[-6] - 1) * 100 if len(vol_arr) >= 6 and vol_arr[-6] > 0 else 0.0
        _v10_mean = float(np.mean(vol_arr[-10:]))
        _v3v10 = float(np.mean(vol_arr[-3:])) / _v10_mean if _v10_mean > 0 else 1.0
        # 下蹲剔除（20261006 用户要求「002584 这种缩量过快且持续缩量的应该剔除」）：
        #   缩量过快（5日量能变化<=-50%）且 持续缩量（连续缩量>=3天）→ 放量资金已撤、量能枯竭，
        #   不构成健康缩量回踩企稳，不给下蹲买点（阈值见 VOL_SQUAT_DRYUP_* 常量）。
        _vol_dry_up = (_dvol5 <= VOL_SQUAT_DRYUP_DVOL5_MAX
                       and _shrink_streak >= VOL_SQUAT_DRYUP_STREAK_MIN)
        squat_buy = False
        squat_grade = ''
        squat_reason = ''
        # 分支1（优选）：缩量回踩
        if (not _vol_dry_up
                and total_score >= 65
                and today_vol_ratio <= 1.2
                and today_pct <= 1.0):
            squat_buy = True
            squat_grade = '优选'
            squat_reason = f'下蹲买点（缩量回踩 量比={today_vol_ratio:.2f}）'
        # 分支2（扩大，20261003）：回调后的小阴线/小阳线/十字星
        #   与分支1差异：①放宽为小实体K线（实体≤2%，含十字星）；②小阳线涨幅上限 +1.0%→+2.5%；
        #   ③量比上限 1.2→1.6。
        #   （20261003 用户要求：均线条件、距20日高条件、止损空间条件已全部删除 —— 下跌价位由
        #     「跌破 T0 前一交易日收盘价」这一 T0 失效门槛单一口径把关，见 _vol_step_days）
        #   共同门槛（量能结构≥65）不变。
        elif (not _vol_dry_up
                and total_score >= 65
                and _is_small_body
                and today_vol_ratio <= VOL_SQUAT_EXPAND_VOLR_MAX
                and -3.0 <= today_pct <= 2.5):
            squat_buy = True
            squat_grade = '扩大'
            squat_reason = (f'下蹲买点·扩大（回调后{_kname} 实体={_body_pct:.2f}%，'
                            f'缩量 量比={today_vol_ratio:.2f}）')

        # ===== 下蹲提示字段（20260927，仅提示展示，不参与筛选/排序）=====
        # T0 标志日：量能抬升后 5日均量未回落到起量前水平 → 资金未走（见 _vol_step_days 标定）
        # （_step 已在上方近端缩量检查处计算，且此处必为合格 T0）
        vol_step_days = _step[0]
        vol_step_px = round(_step[1] * 100, 1)
        t0_pct = round(_step[2], 1)
        # t0_open（T0 标志日开盘价，止损位）已在上方计算并透传为「止损位」展示字段
        # 量能形态（连续缩量天数 / 5日量能变化率 / 近3日与10日量能比）
        #   —— 已在「下蹲剔除」处提前计算（_shrink_streak / _dvol5 / _v10_mean / _v3v10），此处直接复用

        # MACD 硬门槛已删除（20260927）：不再出现「MACD 方向未确认 → return None」，
        # 标的入池由基础量能爆发结构（total_score>=65）及下方 强买/观察/下蹲 归类决定

        # 注：原 is_fresh_red / is_red_retrace / is_red_bounce / macd_unconfirmed 四个布尔量
        #   已随下方强买规则重写一并删除（除此处外全文件无其它引用）。
        #   MACD 现在的唯一去处是 result 里的 'MACD状态' / '死叉临界' 两个纯展示字段。

        # ===== 强买信号重写（20261006）：解除「唯 MACD 论」绑定 =====
        # 问题：MACD 已于 20260927 明确退出决策链（"用户定调 MACD 没意义"，且回测显示其分支
        #   无区分度：死叉临界 39.2%/+0.11% 不差于红柱确认 37.9%/+0.23%），但原 5 条强买规则里
        #   有 4 条以 is_fresh_red / is_red_retrace / is_red_bounce 为必要条件 →
        #   「MACD 不参与决策」与「强买几乎全由 MACD 决定」自相矛盾，实际触发面被收窄到
        #   恰好 MACD 刚翻红的那一天，属口径事故。
        # 处置：强买改由「量价结构 + 位置 + 评分」三维判定，MACD 状态退为纯展示字段
        #   （'MACD状态'/'死叉临界' 仍写入 result 供报告查看，不参与本段任何判定）。
        # 替换口径（均为已计算好的非 MACD 字段）：
        #   缩量回踩   = 今日量比 <1.2 且 涨日量/跌日量 >=1.2（近10日阳线放量、阴线缩量）
        #   位置       = 距MA20（回踩深度）/ 距20日高（追高程度）/ MA20趋势（方向）
        #   量能未失控 = 今日量比 <2.0（防放量滞涨）
        #   趋势延续   = MA20趋势向上 + 距20日高 >-8%（未过度冲高回落）
        # 说明：强买仍只作「形态标注」，不参与 FinalEntryScore 排序、不落库（见 _track_picks），
        #   故此改动影响面限于报告展示层的标记数量。
        # P0（20261007）：本段仍用**绝对评分** total_score 作门槛，因 detect 逐只调用、
        #   无池上下文，无法在此算截面百分位。绝对评分的门槛虚设问题（>=70 通过率 99.6%）
        #   已由 run() 的 `_refine_strong_buy_by_cross_pct` 在池内事后修正：
        #   池内会剔除「过热档」并对量级过低的票降级 —— 因实证两种口径都指向「分数越低越好」，
        #   绝对评分「>=70」实际是在要求过热档。该函数在 run() 中调用、只改强买标记，
        #   不改下蹲信号（唯一落库信号），故入池/落库口径完全不变。
        strong_buy = False
        strong_buy_reason = ''
        vr_now = today_vol_ratio
        vup = _vol_up_ratio
        shrink_pullback_ok = (vr_now < 1.2 and vup >= 1.2)
        vol_ok = vr_now < 2.0
        if pos_ma20 < 0 and shrink_pullback_ok and total_score >= 70:
            strong_buy = True
            strong_buy_reason = (f'回踩MA20下方({pos_ma20:.1f}%)+缩量回踩'
                                f'(量比{vr_now:.2f}/涨跌量比{vup:.2f})+评分{total_score:.0f}(形态)')
        elif (retrace_type == '中回调' and total_score >= 70 and vol_ok
              and ma20_trend in ('up', 'flat')):
            strong_buy = True
            strong_buy_reason = f'中回调+评分{total_score:.0f}+量比{vr_now:.2f}未失控(形态)'
        elif (retrace_type == '浅回调' and total_score >= 75 and vol_ok
              and dist_hh20 > -3.0):
            strong_buy = True
            strong_buy_reason = (f'浅回调+高评分{total_score:.0f}+距20日高{dist_hh20:+.1f}%'
                                f'(未追高,形态)')
        elif (65 <= total_score < 80 and 1.0 <= vr_now < 1.5 and -3 <= pos_ma20 < 0):
            strong_buy = True
            strong_buy_reason = f'评分65-80+量比1.0-1.5+回踩MA20(形态)'
        elif (ma20_trend == 'up' and total_score >= 70 and vr_now >= 0.9
              and vol_ok and dist_hh20 > -8.0):
            strong_buy = True
            strong_buy_reason = (f'MA20向上+评分{total_score:.0f}+量比{vr_now:.2f}达标'
                                f'+距20日高{dist_hh20:+.1f}%(趋势延续形态)')

        # 观察信号（20260927：MACD 不再参与归类 —— 除「强买/下蹲」外的量能爆发标的统一归入观察跟踪）
        watch = False
        watch_reason = ''
        if not strong_buy and not squat_buy:
            watch = True
            watch_reason = '观察·量能爆发结构成立，等待量价突破确认（仅跟踪，非买入依据）'

        wave_surge = False
        wave_surge_reason = ''
        wave_w1_gain = 0.0
        wave_w2_retrace = 0.0
        wave_dist_h1 = 0.0

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
            'close': round(float(close_full[-1]), 2),
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
            '量能活跃度': round(vol_activity, 2),
            '今日涨跌幅': round(today_pct, 2),
            'MACD状态': macd_status,
            '死叉临界': death_cross_risk,
            '回撤类型': retrace_type,
            '距MA20': round(pos_ma20, 1),
            '距MA30': round(pos_ma30, 1),
            '距MA5': round(pos_ma5, 1),
            '距MA10': round(pos_ma10, 1),
            '距20日高': round(dist_hh20, 1),
            '5日涨幅': round(_chg5, 1),
            '10日涨幅': round(_chg10, 1),
            '60日涨幅': round(_chg60, 1),
            '连续上涨天数': _up_streak,
            '连续阳线天数': _yang_streak,
            'ATR扩张': round(atr_expand, 2),
            '近3日最大上影': round(_upper_shadow, 2),
            '红柱缩短天数': _red_shrink,
            '涨日量/跌日量': round(_vol_up_ratio, 2),
            'MA20趋势': ma20_trend,
            '强买信号': strong_buy,
            '强买原因': strong_buy_reason,
            '观察信号': watch,
            '观察原因': watch_reason,
            '下蹲信号': squat_buy,
            '下蹲等级': squat_grade,
            '下蹲原因': squat_reason,
            '起量台阶天数': vol_step_days,
            '起量台阶涨幅': vol_step_px,
            'T0涨幅': t0_pct,
            'T0开盘价': t0_open,
            'T0前一日收盘价': stop_price,
            '止损位': stop_price,   # 止损位 = T0 前一交易日收盘价（20261006 用户要求；原为 T0 开盘价）
            '缩量比': vol_shrink_ratio,
            '连续缩量天数': _shrink_streak,
            '5日量能变化': round(_dvol5, 1),
            '近3日量能比': round(_v3v10, 2),
            '蓄势大涨信号': wave_surge,
            '蓄势大涨原因': wave_surge_reason,
            '波浪W1涨幅': round(wave_w1_gain * 100, 1) if wave_surge else 0,
            '波浪W2回调': round(wave_w2_retrace * 100, 1) if wave_surge else 0,
            '波浪距H1': round(wave_dist_h1 * 100, 1) if wave_surge else 0,
            # ===== P0（20261007）：5 个评分分量原始值，供 run() 算池内截面百分位 =====
            '_comp_vol': _comp['vol'], '_comp_freq': _comp['freq'],
            '_comp_amp': _comp['amp'], '_comp_big_amp': _comp['big_amp'],
            '_comp_swing': _comp['swing'],
        }
        return result
    except Exception:
        return None


# =========================
# V2.0 次日开盘新开仓优先模型（20260821）
# 核心原则：趋势强 ≠ 适合新开仓。排名第一 = 趋势健康 + 位置合理 + 主题支持 + 次日高开风险可控。
# FinalEntryScore = BaseQuality + TrendContinuation + EntryTiming + ThemeResonance
#                 + VolumeStructure + ChipStructure + MarketFit
#                 - ExtensionPenalty - ExhaustionPenalty - GapRiskPenalty
#                 - ThemeCyclePenalty - FailureRisk
# =========================
THEME_STAGE_BONUS = {'启动': 15, '升温': 12, '发酵': 8, '主升': 5,
                     '高潮': -10, '分化': -5, '退潮': -15, '': 0}

# FinalEntryScore 位置分二次标定（20260927）：量能分/趋势延续/市场适配/动量 四项移出排序后，
# 位置分量的离散度不足以覆盖原 65/75/85 标尺（原口径的方差由被剥离四项携带），故做线性重标定：
#   final = ENTRY_POS_SCALE × 位置分 + ENTRY_POS_BASE
# 标定口径（回测池 2025-01~2026-09 / 6,628 事件, cs=0）：
#   原口径中位 48.3 / 上限 69.7（>=65 占 1.7%）；位置分中位 22.0 / 上限 37.0
#   两点拟合 ⇒ SCALE=1.43, BASE=16.9，中位与上限同时对齐原口径
# 说明：线性正变换不改排序，仅恢复量纲，使 65/75/85 三档重新可达
ENTRY_POS_SCALE = 1.43
ENTRY_POS_BASE = 16.9


def _extension_penalty(dist20, chg5, chg10):
    """乖离惩罚：区分趋势强度与开仓赔率，高位强势股不能以高趋势分抵消位置风险

    ===== 20261006 重写：单调递增 → 倒U型双侧惩罚（原实现方向与实证相反）=====
    原实现（单调递增，距MA20 越高罚越重，0 → -30）：
        其隐含假设是「距MA20 越大 = 越追高 = 越危险」。但全市场实证（下蹲事件池
        n=8733，2025-01~2026-09，T+1开盘买/T+5收盘/止损位=T0开盘价）显示恰恰相反：

          距MA20 档位        n      胜率%   均收益%  止损率%
          (-∞, -5%]      748     38.1    -0.10    29.8
          (-5%, 0%]     1066     35.5    -0.45    25.2
          (0%, 5%]      1987     36.8    -0.61    19.5
          (5%, 10%]     2226     42.0    -0.23    14.0
          (10%, 15%]    1348     42.2    -0.22    12.2
          (15%, 20%]     658     44.1    +0.07     8.2
          (20%, 25%]     326     41.7    +0.34    10.1
          (25%, 35%]     267     45.7    +0.70     6.7   ← 最优
          (35%, +∞)      107     41.1    -1.01     4.7   ← 最差

        → 收益是**倒U型**：0~5% 档最差（-0.61%），25~35% 档最优（+0.70%），
          仅 >35% 才转负（-1.01%）。原实现却对 25~35%（最优档）罚 -24.2 分、
          对 0~5%（最差档）罚 0 分 —— **惩罚方向与收益方向完全相反**。
        → 止损率则单调下降（29.8% → 4.7%），说明「距MA20 低」的风险不在止损、
          在小幅阴跌（胜率不占优、收益被拖平），属"卡在半山腰"的震荡态；
          这类票该被罚，但理由是「无趋势方向」，不是「乖离大」。
        → Spearman raw IC 仅 +0.0024（几乎无单调线性关系）→ **不能当连续权重线性代入**，
          只能按分档定性使用。分年方向不稳（2025 -0.0220 / 2026 +0.0105），
          故本项只做「明显最差的两端」扣分，中间段一律不罚，避免过度拟合噪声。

    新实现（倒U型，两侧惩罚）：
        深破位侧（距MA20 <0）：已跌到均线下方 → 趋势已破坏，罚
        中间段（0% ~ 35%）：实证最优区（含 25~35% 最优档），不罚
        极端追高侧（>35%）：实证最差档，罚最重
      叠加项保留原口径（急涨+高乖离叠加惩罚），阈值随主形状同步调整。

    影响：全池平均罚分由 -3.9 收敛到 -2.1，且罚分与收益符号对齐。
    注：本项仍只作 FinalEntryScore 的一个分量（满幅 -45），不是唯一位置口径；
        位置主口径仍是 _entry_timing（距MA20 分 S/A/B/C）。
    """
    if dist20 < -10:
        p = -20
    elif dist20 < -5:
        p = -12
    elif dist20 < 0:
        p = -5
    elif dist20 <= 35:
        p = 0
    elif dist20 <= 45:
        p = -12
    else:
        p = -25
    if dist20 > 35 and chg5 > 20:
        p -= 8
    if dist20 > 45 and chg10 > 30:
        p -= 15
    return max(p, -45)


def _exhaustion_penalty(s):
    """衰竭惩罚：买在加速末端的风险（连续大阳+异常放量+高乖离 / 5日暴涨+主题高潮 / 高位放量+长上影）"""
    p = 0
    d20 = s.get('距MA20', 0)
    c5 = s.get('5日涨幅', 0)
    streak = s.get('连续阳线天数', 0)
    vr = s.get('今日量比', 1)
    us = s.get('近3日最大上影', 0)
    stage = s.get('非一日游阶段', '')
    if streak >= 3 and vr >= 1.8 and d20 > 20:
        p -= 15
    if c5 > 25 and stage == '高潮':
        p -= 12
    if d20 > 15 and vr >= 1.8 and us >= 0.5:
        p -= 10
    # 注意：MACD 已不参与任何计罚（20260927），此处不再考虑死叉临界
    return max(p, -20)


def _gap_risk(s, env_weak=False):
    """T1GapRisk 次日高开低走风险: 0-2 Low / 3-5 Medium / 6-8 High / >=9 Extreme"""
    pts = 0
    d20 = s.get('距MA20', 0)
    c5 = s.get('5日涨幅', 0)
    c10 = s.get('10日涨幅', 0)
    atr = s.get('ATR扩张', 1)
    streak = s.get('连续上涨天数', 0)
    us = s.get('近3日最大上影', 0)
    vr = s.get('今日量比', 1)
    stage = s.get('非一日游阶段', '')
    if d20 > 25:
        pts += 5
    elif d20 > 15:
        pts += 3
    if c5 > 25:
        pts += 3
    elif c5 > 15:
        pts += 2
    if c10 > 25:
        pts += 2
    if atr >= 1.5:
        pts += 2
    if streak >= 4:
        pts += 2
    if us >= 0.5:
        pts += 2
    if d20 > 15 and vr >= 1.8:
        pts += 3
    if stage == '高潮':
        pts += 2
    if env_weak:
        pts += 1
    if pts >= 9:
        return 'Extreme', -20
    if pts >= 6:
        return 'High', -12
    if pts >= 3:
        return 'Medium', -5
    return 'Low', 0


def _entry_timing(s):
    """EntryTiming：今日收盘后明天是否适合新开仓（S/A/B/C 位置分级）

    ===== 20261006 重写：位置分档由「单调递减」改为「倒U型」，并与 _extension_penalty 去重 =====
    原实现把 距MA20 当成越低越好的单调量（<=10 → S/A，<=15 → A，<=25 → B，>25 → C），
    与 _extension_penalty 的单调递增惩罚**方向一致但阈值重叠**，导致同一变量被三重计入：
      ① EntryTiming（占 FES 位置权重 35%）② ExtensionPenalty（满幅 -45）③ GapRisk（+3/+5）
    实测 FES 在「距MA20 -5% → 35%」区间的跨度达 **58.6 分**（满分 100 的 59%），
    其余字段全固定 —— 即 FES 事实上是个「距MA20 单变量查表」，量能/筹码/主题
    只能在小数点后起作用。这是「放弃均线比较」未兑现到排序层的根因。

    实证修正（下蹲事件池 n=8733，见 _extension_penalty docstring 分档表）：
      距MA20 的收益呈倒U型，0~5% 最差（-0.61%）、25~35% 最优（+0.70%）、>35% 转负（-1.01%）。
      故位置分档改为「过低/过高两端扣分，中间段给高权重」：
        <=0%（跌破均线，趋势破坏）→ B/C
        0~35%（实证最优区，含 25~35 最优档）→ S/A
        35~45% → C
        >45%（极端乖离）→ X（强制过滤）
      这样距MA20 对 FES 的贡献从「单调 58.6 分跨度」压缩为「两端惩罚、中段平坦」，
      位置不再由单一均线距离主导，量能结构/筹码分获得实际区分空间。

    保留不变：
      · shrink_pullback（缩量回踩）与 ma20_trend 方向仍是 S/A 的必要条件
      · forbid（距MA20>35 且 10日涨幅>30 → X）由 >35 放宽到 >45，
        因实证 25~35% 是最优档、原 forbid 阈值会把最优档误杀
      · S/A/B/C 分级语义不变（B=58/C=40 底分沿用，避免 Rating 档位整体漂移）
    """
    d20 = s.get('距MA20', 0)
    c5 = s.get('5日涨幅', 0)
    c10 = s.get('10日涨幅', 0)
    vr = s.get('今日量比', 1)
    vol_up_ratio = s.get('涨日量/跌日量', 1)
    streak = s.get('连续阳线天数', 0)
    ma20_trend = s.get('MA20趋势', 'flat')
    shrink_pullback = (vr < 1.2 and vol_up_ratio >= 1.2)
    # 强制过滤：极端乖离 + 急速上涨（原阈值 >35/>30，实证 25~35 为最优档故放宽到 >45）
    forbid = (d20 > 45 and c10 > 30)
    grade = 'C'
    score = 40
    if d20 <= 0:
        # 已跌破 MA20：趋势破坏。即便浅破位也只给 C，深破位（<-10）直接 X。
        if d20 < -10:
            grade = 'C'
            score = 32
        else:
            grade = 'C'
            score = 45
    elif d20 <= 35:
        # 实证最优区（含 25~35% 最优档）：位置分不再随 d20 单调下滑
        strong_pos = (d20 <= 20 and ma20_trend in ('up', 'flat')
                      and shrink_pullback and streak <= 3)
        mid_pos = (d20 <= 30 and ma20_trend in ('up', 'flat')
                   and (shrink_pullback or vr < 1.5))
        if strong_pos:
            grade = 'S'
            score = 90
        elif mid_pos:
            grade = 'A'
            score = 80
        elif s.get('所属状态') == '看多' and shrink_pullback:
            grade = 'B'
            score = 68
        elif d20 <= 20:
            # 中间段但趋势向下 / 未缩量：位置本身不差，方向存疑 → B 底分
            grade = 'B'
            score = 62
        else:
            # 20~35% 且无主题/缩量配合：位置偏高且动能未验证 → B 底分
            grade = 'B'
            score = 58
    else:
        # >35%：实证最差档（>35% 档均收益 -1.01%）
        grade = 'C'
        score = 38
    if forbid:
        grade = 'X'
        score = 35
    return score, grade, forbid


def _theme_resonance(s):
    """主题共振：个股上涨 vs 个股+板块+主线资金共振（0~15）"""
    theme = s.get('所属主题', '') or ''
    state = s.get('所属状态', '')
    stage = s.get('非一日游阶段', '')
    if '(回避)' in theme or state == '回避':
        return 0
    stage_bonus = THEME_STAGE_BONUS.get(stage, 0)
    leader_bonus = 0
    if s.get('_is_leader'):
        leader_bonus += 15
    elif s.get('_is_mainline'):
        leader_bonus += 10
    elif s.get('_is_rotation'):
        leader_bonus += 4
    if s.get('_is_mainline') and s.get('主题匹配度', 0) >= 80:
        leader_bonus += 2
    return max(0, min(15, stage_bonus + leader_bonus))


def _theme_cycle_penalty(s):
    """主题周期扣分：高潮/分化/退潮阶段不适合新开仓"""
    stage = s.get('非一日游阶段', '')
    if stage == '退潮' or '(回避)' in (s.get('所属主题', '') or ''):
        return -15
    if stage == '高潮':
        return -10
    if stage == '分化':
        return -5
    if stage == '主升':
        return -2
    return 0


def _volume_structure(s):
    """量价结构（0~12）：量价配合档位方向取反 + DistributionRisk（20260927 回测修正）

    原口径给「涨日量/跌日量」（阳线放量·阴线缩量）越高越多分（4→10），回测为负优化：
      2025-01~2026-09，T+1开盘买/T+5收盘，盘中-7%止损，含0.25%成本
      全市场 / VSW结构 Spearman IC：pvr_all -0.028/-0.026，health(放量阳线占比-放量阴线占比) -0.026/-0.032
      控制近5日动量后，各动量组内该比值高组的 T+5 均收益均低于低组（VSW P3组 0.51%→0.27%）
      → 量价配合越"完美"越可能是资金已充分进场，故档位方向整体取反。
    档位实测（VSW结构代理：maxvr200>=2.6 & amp120>=4.5 & swing200>=35，n=50.0万事件）：
      ratio<1.0 → 0.32%/44.4%(n=93.5k)；1.0<=ratio<1.2 → 0.21%/42.6%(n=200.7k)；
      ratio>=1.2 → 0.12%/42.4%(n=185.2k)，其中 vr 1.5~2.0 亚档 0.08%/39.9%(n=20.9k，噪声级)
      → 按 ratio 单调递减成立，档位简化为 9/7/5 三档；不再细分 ratio>=1.2 内部
        （原「配合+缩量」最低档 3 分已取消：会误伤「下蹲+缩量」形态）。
    注意：本档位整体较原口径抬升约 +0.74（原均值 5.81 → 6.55），属有意放宽，非错误。
    """
    vol_up_ratio = s.get('涨日量/跌日量', 1)
    vr = s.get('今日量比', 1)
    d20 = s.get('距MA20', 0)
    # 20260927: 取消原「配合+缩量(vr<1.5)」最低档 —— 该档会误伤「下蹲+缩量」形态
    # （海峡创新0923 ratio=1.25/vr=0.89 落此档，次日0924 +19.98%），改为 ratio>=1.2 统一 5 分
    if vol_up_ratio >= 1.2 and vr < 2.0:
        score = 5
    elif vol_up_ratio >= 1.0:
        score = 7
    else:
        score = 9
    if d20 > 15 and vr >= 2.0:
        score -= 6   # 高位突然巨量=DistributionRisk
        s['_distribution'] = True
    return max(0, min(12, score))


def _chip_structure(s):
    """筹码结构（0~10）：Chip趋势/CRE/动量 + V5 Alpha/机会，风险低者加成"""
    cs = s.get('ChipTrendScore', 50)
    cre = s.get('CRE_Score', 50)
    cm = s.get('ChipMomentum_Score', 50)
    v5c = s.get('Alpha_Composite', 50)
    os_ = s.get('Opportunity_Score', 50)
    risk = s.get('Risk_Score', 50)
    base = cs * 0.3 + cre * 0.2 + cm * 0.1 + v5c * 0.2 + os_ * 0.2
    if risk <= 10:
        base += 5
    elif risk <= 15:
        base += 2
    elif risk > 25:
        base -= 5
    return max(0, min(10, base / 10))


def _trend_continuation(s):
    """趋势延续（0~20）：MA20 方向 + 位置

    20260927：MACD 已从决策链移除，原「MACD状态健康度(3~7分) + 红柱缩短天数扣分」整段取消；
    权重并入 MA20 方向与位置，以保持 0~20 量纲（本分量已不参与 FinalEntryScore 排序，
    仅经 HoldScore / T1Score 作展示字段）。
    """
    ma20_trend = s.get('MA20趋势', 'flat')
    d20 = s.get('距MA20', 0)
    score = 0
    if ma20_trend == 'up':
        score += 12
    elif ma20_trend == 'flat':
        score += 8
    if d20 < 0:
        score += 5
    elif d20 <= 5:
        score += 3
    return max(0, min(20, score))


def _momentum_strength(s):
    """动量强度（0~10）：强者恒强 —— 温和强势的相对强度 + 量能放大 + 阳线连续性
    与 ExtensionPenalty 互补：此处奖健康强势（未过热），彼处罚过热乖离"""
    sc = 0
    c5 = s.get('5日涨幅', 0)
    c10 = s.get('10日涨幅', 0)
    vr = s.get('今日量比', 1)
    vol_up = s.get('涨日量/跌日量', 1)
    streak = s.get('连续阳线天数', 0)
    # 相对强度：温和上涨（过热部分交由 ExtensionPenalty 罚）
    if 3 <= c5 <= 18:
        sc += 3
    elif 0 <= c5 < 3:
        sc += 2
    elif 18 < c5 <= 30:
        sc += 1
    if 3 <= c10 <= 25:
        sc += 2
    elif 0 <= c10 < 3:
        sc += 1
    # 量能温和放大（换手/量比活跃度）
    if 1.0 <= vr < 2.0:
        sc += 2
    elif 0.8 <= vr < 1.0:
        sc += 1
    # 阳线连续性（强者恒强；连阳过多由 Exhaustion 计罚）
    if streak >= 1:
        sc += 1
    if streak >= 2:
        sc += 1
    # 上涨放量/下跌缩量
    if vol_up >= 1.2:
        sc += 1
    return max(0, min(10, sc))


def _market_fit(s, env_mult):
    """市场环境适配（0~8）：环境系数×位置惩罚（主题分值已取消，主题回避不再参与评分）"""
    base = 8 * env_mult
    if s.get('距MA20', 0) > 15:
        base *= 0.7
    return max(0, min(8, base))


def _failure_risk(s):
    """失败风险（0~20）：破位/无量滞涨/长上影/高位缩量组合（20260927 起 MACD 死叉临界不再计罚）"""
    p = 0
    if s.get('MA20趋势') == 'down':
        p += 6
    if s.get('今日量比', 1) < 0.8 and s.get('距MA20', 0) > 10:
        p += 4
    if s.get('近3日最大上影', 0) >= 0.6:
        p += 3
    if s.get('连续阳线天数', 0) == 0 and s.get('距MA20', 0) > 15:
        p += 4
    return min(p, 20)


def _entry_eligibility(s, gap):
    """次日开仓资格过滤：9 项至少满足 4 项，否则即使评分 99 也不能进 TOP3"""
    checks = 0
    if s.get('MA20趋势') in ('up', 'flat'):
        checks += 1
    if s.get('距MA20', 0) >= -3:
        checks += 1
    if s.get('距MA20', 0) < 20:
        checks += 1
    if not s.get('_distribution'):
        checks += 1
    if s.get('非一日游阶段') != '退潮' and '(回避)' not in (s.get('所属主题', '') or ''):
        checks += 1
    if gap != 'Extreme':
        checks += 1
    if s.get('Risk_Score', 50) <= 15:
        checks += 1
    if s.get('_et_score', 40) >= 55:   # S/A/B 级位置均合格（B级允许次日确认后介入）
        checks += 1
    if s.get('_mf_base', 8) >= 6.8:   # 弱势(×0.70) 不允许新开仓，震荡偏弱(×0.85)及以上允许
        checks += 1
    return checks >= 4


def _open_strategy(s, gap):
    """明日开盘执行方案"""
    d20 = s.get('距MA20', 0)
    if gap == 'Extreme':
        return ['✘ 高开风险 Extreme，默认不追', '✘ 观望，等待次日量价确认']
    if gap == 'High':
        return ['⚠ 高开风险较高', '✔ 仅回踩确认后小仓介入', '✘ 高开3%以上不追', '✘ 盘中冲高不接力']
    if gap == 'Medium' or d20 > 3:
        return ['✔ 高开≤3%可关注', '✔ 回踩不破昨日低点+VWAP上方运行确认', '✘ 高开3%~5%降低仓位，等回踩', '✘ 高开>5%不追']
    return ['✔ 平开/小高开可关注', '✔ 站稳VWAP后确认', '✔ 突破早盘高点可买', '✘ 高开超过5%不追']


def _compute_entry_v2(s, env_mult=1.0, env_weak=False):
    """计算 FinalEntryScore 全分量，写入 s；返回 s"""
    s['_distribution'] = False
    et_score, et_grade, forbid = _entry_timing(s)
    s['_et_score'] = et_score
    s['_mf_base'] = 8 * env_mult
    ext_pen = _extension_penalty(s.get('距MA20', 0), s.get('5日涨幅', 0), s.get('10日涨幅', 0))
    ex_pen = _exhaustion_penalty(s)
    gap, gap_pen = _gap_risk(s, env_weak)
    tr = _theme_resonance(s)
    tcp = _theme_cycle_penalty(s)
    vs = _volume_structure(s)
    cs = _chip_structure(s)
    mf = _market_fit(s, env_mult)
    tc = _trend_continuation(s)
    fr = _failure_risk(s)
    mom = _momentum_strength(s)
    # ===== FinalEntryScore 排序口径（20260927 修正：量能分/趋势延续/市场适配/动量 移出排序）=====
    # 回测（daily_cache 全市场 → 生产硬过滤+MACD确认池，2025-01~2026-09，6,628 事件 / 413 交易日，
    #       T+1开盘买 / T+5收盘 / 盘中-7%止损 / 含0.25%成本）：
    #   TOP1 胜率 原口径 37.5% → 位置/缩量类口径 45.5%，止损率 43.3% → 37.5%
    #   分年一致：2025 39.3%→46.7%；2026 35.1%→41.5%（2026 原口径 TOP1 均收益为 -0.82%）
    #   池内 Spearman IC 0.084 → 0.104（纯「距MA20 升序」IC 0.118 为上限）
    #   根因：tc/mf 系统性偏好"趋势强、乖离大"的高位票，与位置类分量对冲，把排序推向高位
    # 处置：四项降为门槛/展示分 —— bq 由硬过滤「量能爆发评分>=65」承担；
    #       tc→HoldScore、mom→EntryScore/T1Score、mf→_mf_base(供 _entry_eligibility) 仍在用
    # ENTRY_POS_SCALE/BASE 对位置分做线性重标定（见文件头常量注释），恢复 65/75/85 量纲
    _pos = et_score * 0.35 + vs + cs + ext_pen + ex_pen + gap_pen - fr
    final = round(ENTRY_POS_SCALE * _pos + ENTRY_POS_BASE, 1)
    final = max(0, min(100, final))
    if final >= 85 and gap != 'Extreme' and et_score >= 80:
        rating = 'S'
    elif final >= 75 and gap != 'Extreme':
        rating = 'A'
    elif final >= 65:
        rating = 'B'
    else:
        rating = 'C'
    eligible = _entry_eligibility(s, gap)
    s['TrendScore'] = round(s.get('量能爆发评分', 0), 1)                    # 趋势强度（0-100，沿用原趋势总分）
    s['HoldScore'] = round(tc * 1.2, 1)
    # 新开仓价值（0-100）：位置60% + 动量10% + 量价10% + 筹码10% + 市场10%（主题分值已取消）
    s['EntryScore'] = round(max(0, min(100, et_score * 0.6 + mom
                                       + vs / 12 * 10 + cs / 10 * 10 + mf / 8 * 10)), 1)
    s['EntryTimingScore'] = et_score
    s['EntryTimingGrade'] = et_grade
    s['T1Score'] = round(tc + et_score * 0.35 + vs + cs + mom, 1)
    s['T1Risk'] = gap
    s['GapRiskPenalty'] = gap_pen
    s['ExtensionPenalty'] = ext_pen
    s['ExhaustionPenalty'] = ex_pen
    s['ThemeCyclePenalty'] = tcp
    s['FailureRisk'] = fr
    s['ThemeResonance'] = tr
    s['VolumeStructure'] = vs
    s['ChipStructure'] = cs
    s['MarketFit'] = round(mf, 1)
    s['FinalEntryScore'] = final
    s['Rating'] = rating
    s['Eligible'] = eligible
    s['ForbidTOP'] = forbid
    s['GapAdvice'] = _open_strategy(s, gap)
    # 强趋势弱开仓：TrendScore>90 但 FinalEntryScore<65 → 禁止标 BUY，只能 HOLD/WATCH（规格硬性）
    if s.get('量能爆发评分', 0) > 90 and final < 65:
        s['_v2_label'] = 'HOLD/WATCH'
    elif rating in ('S', 'A') and eligible:
        s['_v2_label'] = 'BUY'
    elif rating == 'B' and eligible:
        s['_v2_label'] = 'BUY·需次日确认'
    else:
        s['_v2_label'] = 'WATCH'
    # ===== MACD 阀门已删除（20260927）=====
    # 原「死叉临界 / 即将红柱 → Rating=C + Eligible=False + _v2_label=WATCH」整段取消：
    # 标定回测显示 MACD 分支无区分度（死叉临界全量 39.2%/+0.11% 并不差于红柱确认 37.9%/+0.23%），
    # 故 MACD 不再拦截、不再参与评级/资格/排序判定
    return s


def _theme_ctx_from_report(trade_date):
    """构建主题上下文：主线集合/轮动集合/龙头映射（供 ThemeResonance 使用）"""
    ctx = {'mainline': set(), 'rotation': set(), 'leaders': {}}
    try:
        data = _load_mainline_rotation_themes(trade_date)
        for tname, r in data.items():
            if r.get('kind') == 'mainline':
                ctx['mainline'].add(tname)
            elif r.get('kind') == 'rotation':
                ctx['rotation'].add(tname)
    except Exception:
        pass
    try:
        v6 = _load_v6_result(trade_date) or []
        for r in v6:
            _t = r.get('theme', '')
            _ld = r.get('leader', '')
            if _t and _ld and _t not in ctx['leaders']:
                ctx['leaders'][_t] = str(_ld)
    except Exception:
        pass
    return ctx


def _add_cross_pct_score(results):
    """P0（20261007）：为每只票算「每日池内截面百分位评分」cross_score（0~100）

    为什么需要：量能爆发评分 total_score 的 5 个分量全是 min(值/固定阈值, 1)*权重 的封顶项，
      放量行情中普遍顶格 → 实测池内中位 93.3、>=95 占 41.7%、=100 占 16.7%，
      「评分>=70/75」通过率 99.6%/98.3% → 门槛形同虚设（20260930 强买 37 只的直接原因）。
    做法：把 5 个分量在**当日全池内**做 rank(pct=True) 加权求和。
      · 分量方向：vol / freq / big_amp / swing 为「越大越爆发」→ 升序分位；
        amp（日均振幅）过大者收益反而差（见 _extension_penalty 倒U型结论）→ 取反向分位。
      · 结果天然均匀分布在 0~100，永不饱和（唯一值 326 → 7639）。
    关键结论（n=9180）：两种口径**方向一致**都指向「分数越低越好」，
      故 cross_score 是「过热程度」而非「强度」——用法见 _refine_strong_buy_by_cross_pct。
    入池门槛 total_score>=65 不变（保持与历史回测可比），本函数只新增字段。
    """
    if not results:
        return
    _w = {'vol': 30.0, 'freq': 20.0, 'amp': 20.0, 'big_amp': 15.0, 'swing': 15.0}
    _spec = [('_comp_vol', _w['vol'], True), ('_comp_freq', _w['freq'], True),
             ('_comp_amp', _w['amp'], False),      # 振幅过大者收益差 → 反向
             ('_comp_big_amp', _w['big_amp'], True), ('_comp_swing', _w['swing'], True)]
    for s in results:
        s['量能截面分'] = 0.0
    for s in results:
        acc = 0.0
        for col, w, asc in _spec:
            if col not in s:
                continue
            acc += s[col] * w
        s['_acc_raw'] = acc
    # 池内分位（仅对有分量的票；无分量的票给 0）
    has = [s for s in results if '_acc_raw' in s]
    if len(has) >= 5:
        import numpy as _np
        vals = _np.array([s['_acc_raw'] for s in has], dtype=float)
        for s in has:
            s['量能截面分'] = round(float(_np.mean(vals <= s['_acc_raw']) * 100), 1)
    for s in results:
        s.pop('_acc_raw', None)


def _refine_strong_buy_by_cross_pct(results, top_pct=95.0, min_pct=10.0):
    """P0（20261007）：用池内截面百分位修正强买信号的「过热档 / 过弱档」问题

    问题：detect 内强买门槛用「绝对评分 >=70/75」，而绝对评分已饱和（通过率 99.6%），
      且实证两口径都指向「分数越低越好」——即「>=70」实际在**要求过热档**。
      20260930 强买 37 只即由此而来（其中多只 60日涨幅 >90%）。
    处置（只改强买标记，不动下蹲/入池/落库）：
      ① 过热降级：量能截面分 > top_pct 的票取消强买（高分档 = 相对池内过度放量）
      ② 过弱降级：量能截面分 < min_pct 的票取消强买（相对池内放量不足，不构成「量能爆发」）

    阈值标定（强买事件池 n=12874，2025-01~2026-09，T+1开盘买/T+5收盘/止损位=T0前一日收盘价）：
      保留区间           n      胜率%   均收益%
      全部（不修正）  12874    41.2    -0.41
      [10, 95)       10955    41.5    -0.35   ← 最优，取用
      [15, 70)（初版） 7106    41.9    -0.21
      分年一致改善：2025 -0.14% → -0.13%、2026 -0.48% → -0.45%
      （分档看：<15 档 39.6%/-0.71% 最差、70~90 档 39.6%/-0.79% 次差，
        中间 15~70 档 42%/-0.1..-0.3% 最好 → 保留中间段、两端降级）
      注：初版用 [15,70] 把 n 从 12874 砍到 7106（-45%），过度收紧；
        经扫描 15~95 的候选组合后放宽为 [10,95]，保留 n 更合理、均收益相当。
    保留：原本无强买标记的票不新增；下蹲买点不受影响（唯一落库信号）。
    """
    n_cut = n_keep = 0
    for s in results:
        if not s.get('强买信号'):
            continue
        p = s.get('量能截面分', 0.0)
        if p > top_pct:
            s['强买信号'] = False
            s['强买原因'] = (f"{s.get('强买原因','')}｜[P0过热降级] 量能截面分{p:.0f}>"
                             f"{top_pct:.0f}（实证高分档为相对过度放量）")
            n_cut += 1
        elif p < min_pct:
            s['强买信号'] = False
            s['强买原因'] = (f"{s.get('强买原因','')}｜[P0强度降级] 量能截面分{p:.0f}<"
                             f"{min_pct:.0f}（相对池内放量不足）")
            n_cut += 1
        else:
            n_keep += 1
    return n_keep, n_cut


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

    # 目标股池：总市值 > 30亿（剔除北交所 .BJ，用户规则：不碰北交所）+ 剔除 ST（20261003 用户要求；
    #   20261006 用户要求门槛由 50亿 下调至 30亿，使天龙股份 603266.SH 等中小盘可进池）
    _filtered = market[market['total_mv'].fillna(0) > 300000]
    _filtered = _filtered[~_filtered['ts_code'].str.endswith('.BJ')]
    if 'name' in _filtered.columns:
        _filtered = _filtered[~_filtered['name'].fillna('').astype(str).str.upper().str.contains('ST')]
    _filtered_codes = set(_filtered['ts_code'].tolist())
    print(f'\n[目标股池] 总市值>30亿共 {len(_filtered_codes)} 只（已剔除北交所/ST），开始扫描...')

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

    # ===== P0（20261007）：池内截面百分位 + 强买过热降级 =====
    # 必须在主题/Chip 注入之前做完（纯量价字段，无外部依赖），且要早于 _output_report。
    try:
        _add_cross_pct_score(results)
        _sb_keep, _sb_cut = _refine_strong_buy_by_cross_pct(results)
        _sq_n = len([x for x in results if x.get('下蹲信号')])
        print(f'[P0] 量能截面分已计算；强买标记 保留 {_sb_keep} / 过热或强度不足降级 {_sb_cut}'
              f'（下蹲信号 {_sq_n} 只不受影响）')
    except Exception as e:
        print(f"[P0] 截面分/强买修正失败(不影响主流程): {e}")

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
            _chip_results = batch_chip_alpha(results, lookback_days=20, end_date=TRADE_DATE)
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

    # ===== V2.0 次日新开仓评分（20260821）=====
    if results:
        try:
            _theme_ctx = _theme_ctx_from_report(TRADE_DATE)
            _env_label = (market_tip or {}).get('env', '') or ''
            if '强市' in _env_label:
                env_mult = 1.05
            elif '震荡偏强' in _env_label:
                env_mult = 1.0
            elif '震荡偏弱' in _env_label:
                env_mult = 0.85
            else:
                env_mult = 0.7
            env_weak = ('偏弱' in _env_label or '弱市' in _env_label)
            for s in results:
                _t = s.get('所属主题', '')
                _tn = str(_t).replace('(回避)', '')
                s['_is_mainline'] = _tn in _theme_ctx['mainline']
                s['_is_rotation'] = _tn in _theme_ctx['rotation']
                s['_is_leader'] = _tn in _theme_ctx['leaders'] and _theme_ctx['leaders'].get(_tn) == s.get('名称', '')
                _compute_entry_v2(s, env_mult=env_mult, env_weak=env_weak)
            results.sort(key=lambda x: (
                -x['FinalEntryScore'],
                -x['EntryTimingScore'],
                {'Extreme': 9, 'High': 8, 'Medium': 5, 'Low': 0}.get(x['T1Risk'], 5),
                -x.get('量能爆发评分', 0),
            ))
        except Exception as e:
            print(f"[V2.0评分] 注入失败: {e}")

    # ===== 基本面否决层（20261008 用户要求）：F0 恶化剔除 / F1 中性 / F2 改善优先 =====
    #   只作用于「下蹲信号」（唯一可执行+落库信号）：强买/观察/蓄势不动。
    #   F0 命中 → 该股下蹲信号置 False（自动从报告下蹲段/控制台摘要/落库中剔除），另立「基本面否决」段展示；
    #   F2 命中 → 打标 基本面Gate='F2'，由 _squat_rank_key 第 0 键前置为优先池。
    #   数据缺失一律归 F1（fail-soft），异常不阻塞主流程。
    try:
        _sq_codes = [x.get('代码') for x in results if x.get('下蹲信号')]
        if _sq_codes:
            _fin_map = _load_fundamental_gate(TRADE_DATE, _sq_codes)
            _n_f0 = _n_f2 = 0
            for x in results:
                _rec = _fin_map.get(x.get('代码'))
                _g, _rs = _fin_gate_decision(_rec) if _rec else ('F1', [])
                x['基本面Gate'] = _g
                x['基本面Gate原因'] = '｜'.join(_rs)
                if _rec:
                    x['基本面_扣非同比'] = _rec.get('dt_yoy')
                    x['基本面_营收同比'] = _rec.get('or_yoy')
                    x['基本面_现金流为正'] = _rec.get('ocf_pos')
                    x['基本面_现金流同比'] = _rec.get('ocf_yoy')
                if _g == 'F0' and x.get('下蹲信号'):
                    x['下蹲信号'] = False
                    x['基本面否决'] = True
                    _n_f0 += 1
                elif _g == 'F2':
                    _n_f2 += 1
            print(f'[基本面Gate] 下蹲池 {len(_sq_codes)} 只：'
                  f'F0 恶化否决剔除 {_n_f0} 只 | F2 改善优先 {_n_f2} 只 | 其余 F1 中性')
    except Exception as e:
        print(f"[基本面Gate] 计算失败(不影响主流程，全部按 F1 处理): {e}")

    # 控制台摘要：只列可执行的下蹲买点（唯一落库信号），其余给计数（20261007 P3）
    _sq = [x for x in results if x.get('下蹲信号')]
    _sb = [x for x in results if x.get('强买信号')]
    _wt = [x for x in results if x.get('观察信号') and not x.get('强买信号')]
    _wv = [x for x in results if x.get('蓄势大涨信号')]
    print(f'\n[信号分布] 下蹲(可执行/落库) {len(_sq)} | 强买(仅展示) {len(_sb)} | '
          f'观察(仅展示) {len(_wt)} | 蓄势(仅展示) {len(_wv)}')
    if not _sq:
        print('  今日无下蹲买点 → 不落库、不构成买入依据')
    for _v in sorted(_sq, key=_squat_rank_key)[:10]:
        _theme = _v.get('所属主题', '') or '无主题'
        _stage = _v.get('非一日游阶段', '') or ''
        _stage_str = f' 阶段={_stage}' if _stage else ''
        print(f"  {_v['名称']}({_v['代码']}) {_v.get('下蹲等级','')} 评分{_v['量能爆发评分']} "
              f"距MA5={_v.get('距MA5',0):+.1f}% 基本面={_v.get('基本面Gate','F1')} 主题={_theme}{_stage_str} "
              f"Entry={_v.get('FinalEntryScore','-')} {_v.get('Rating','')} T1Risk={_v.get('T1Risk','-')}")

    _output_report(results, simple=simple, market_tip=market_tip)
    if not simple:
        try:
            _track_picks(results, TRADE_DATE)
        except Exception as e:
            print(f"[VSW] stock_pick_db 写入失败(不影响报告): {e}", flush=True)
    return results


def _theme_effect_map(squat_list):
    """主题效应（20261006 用户要求）：下蹲池内同一主题出现 ≥2 只 → 标注「主题效应」。

    返回 {ts_code: (主题名, 同池只数)}，仅收录可计入效应的主题：
      - 跳过空主题 / '无主题' / 带 '(回避)' 后缀的回避区主题（回避主题不应计入效应）。
    命名刻意区别于评级里的个股维度 ThemeResonance「主题共振」：本项是池内维度的聚集标注。
    只作标注，不参与评分与排序，也不改变任何筛选门槛。
    """
    from collections import Counter
    _cnt = Counter()
    for x in squat_list:
        t = (x.get('所属主题') or '').strip()
        if not t or t == '无主题' or '(回避)' in t:
            continue
        _cnt[t] += 1
    return {x.get('代码'): (t, _cnt[t]) for x in squat_list
            for t in [(x.get('所属主题') or '').strip()]
            if t and t != '无主题' and '(回避)' not in t and _cnt[t] >= 2}


def _squat_rank_key(s):
    """下蹲分支独立排序键（20261006 重标定，方向反转已修正）。

    升序 = 越靠前越优。返回 (距MA5取负, 量能爆发评分, 60日涨幅)。

    ===== 旧键（20260927，方向已反转，作废）=====
      return (量能爆发评分, 距MA5, 60日涨幅)     # 评分升/距MA5升/60日升
      依据旧口径 n=453：评分<85 档 56.1%/+2.72% vs ≥85 档 38.4%/+0.62%；
                        距MA5<-3% 档 47.7%/+2.41% vs -1.5~1% 档 35.9%/-1.11%；
                        60日涨幅<0 档 48.4%/+1.81% vs 15~30% 档 31.2%/-0.80%。
      20261002 T0/量能活跃度改口径后复核（n=27）已发现「距MA5 越负越好」反转，
      但当时样本太小、注释如实记录「暂沿用旧口径结论」→ 键未改，实际选中新口径下最差的一档。

    ===== 新键（20261006，全市场重标定）=====
      重标定脚本 solo/_vsw_squat_recalib.py，事件池 = 本函数基础硬过滤 + 下蹲信号，
      区间 2025-01-01~2026-09-30、**n=8733**（旧口径仅 n=27/99/453，样本量差 20~300 倍），
      口径 T+1开盘买/T+5收盘/止损位=T0开盘价/含0.25%成本。
      全池基准：胜率 40.0% / 均收益 -0.27% / 止损率 16.8%。

      ① 距MA5 单变量分档（**单调，越不负越好**）——本次最关键发现：
         档位          n      胜率%   均收益%  止损率%
         <-5%       1376     35.6    -0.59    25.4
         -5~-3%     1283     38.0    -0.19    21.8
         -3~-1.5%   1307     37.6    -0.20    18.7
         -1.5~0%    1375     41.8    -0.11    14.4
         0~3%       2352     41.8    -0.32    13.1
         >3%        1040     44.7    -0.12     8.0
         → 止损率随距MA5 单调下降（25.4%→8.0%）、胜率单调上升（35.6%→44.7%）。
         旧结论「回踩越深越好」完全反了：深回踩者是「放量资金已撤、正在下跌途中」的票，
         止损率是浅回踩者的 3 倍。浅回踩至 near-0 甚至转正，才是「缩量企稳」的形态。
         Spearman raw IC 仅 +0.0038（线性无关）→ **必须作为第 1 排序键用（分组单调），
         不能当连续权重线性代入**。

      ② 量能爆发评分：维持升序（旧结论方向仍成立，但强度大幅减弱）。
         70~80 档 41.5%/-0.46% · 80~90 档 40.0%/-0.22% · 90~95 档 42.8%/+0.41%
         · >=95 档 38.0%/-0.69% · raw IC -0.0567（负相关=升序更优）。
         极高分档（>=95，均收益 -0.69%）确实最差 → 保留升序，降为第 2 键。

      ③ 60日涨幅：维持升序但**降为第 3 键**（几乎不产生区分度）。
         15~30% 档最优（42.4%/+0.06%），<0% 档最差（35.6%/-0.60%），
         但 >60% 档又回升到 39.2%（止损率仅 4.0%）→ 非单调、噪声大，raw IC -0.0483。

      每日 Top-N 组合实测（T+1开盘买/T+5收盘，最贴近实盘「每日下蹲池取前N」）：
         排序键                          Top1              Top3
         旧键 评分升/MA5升/60日升   39.6%/-0.82%   40.6%/-0.27%（止损 23.9%）
         新键 MA5降/评分升/60日升   44.2%/+0.37%   44.4%/-0.16%（止损 11.0%）
         仅 MA5降/评分升           —— 与上同（60日涨幅无贡献，保留仅作同分兜底）
         全池等权（参考）            40.0%/-0.27%
      → 新键 Top1 均收益由负转正（-0.82%→+0.37%）、Top3 止损率腰斩（23.9%→11.0%）。

      分年稳健性（Top3）：
         旧键  2025 41.1%/-0.03%/止18%   2026 40.3%/-0.36%/止26%
         新键  2025 46.9%/+0.61%/止 7%   2026 43.6%/-0.44%/止12%
      → 两年方向一致改善，非单年噪声。

    ===== 保留 60日涨幅 作为末位键的理由 =====
      它单独看几乎无区分度（新5 与 新5b 仅差此键，结果完全相同），但保留而非删除：
      作为同分兜底可让排序在「距MA5+评分均相同」的候选间保持确定性稳定，
      避免 pandas/groupby 排序抖动导致同一只票在报告中位置忽高忽低。

    ===== 第 0 键：基本面 Gate（20261008 用户要求）=====
      在最前追加 (基本面Gate != 'F2')：F2 改善股（优先池）整体前置，F1 中性随后。
      F0 恶化股在 run() 中已把「下蹲信号」置 False，不会进入本池，故此处无需考虑。
    """
    _g0 = 0 if s.get('基本面Gate') == 'F2' else 1
    return (_g0, -s.get('距MA5', 0), s.get('量能爆发评分', 0), s.get('60日涨幅', 0))


def _fin_num(v):
    """安全取数：NaN/None/非数 → None（用于基本面 Gate 的 fail-soft 判据）"""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:   # NaN
            return None
        return f
    except Exception:
        return None


def _load_fin_snapshot(trade_date):
    """最新一期全字段财务快照（含扣非同比 dt_netprofit_yoy），点内可用（end_date/ann_date 均 <= 决策日）。

    数据源：CACHE_DIR/fin_ind_*_full.parquet（backfill_fin_ind_2026H1.py 生成）。
    返回 (period, DataFrame) 或 None；仅用于实盘取「扣非同比」，回测历史期无快照时自动跳过。
    """
    import glob
    best = None
    for fp in glob.glob(os.path.join(CACHE_DIR, 'fin_ind_*_full.parquet')):
        try:
            _df = pd.read_parquet(fp)
        except Exception:
            continue
        if 'end_date' not in _df.columns or _df.empty:
            continue
        _df['end_date'] = _df['end_date'].astype(str)
        if 'ann_date' in _df.columns:
            _df = _df[_df['ann_date'].fillna('').astype(str) <= str(trade_date)]
        _df = _df[_df['end_date'] <= str(trade_date)]
        if _df.empty:
            continue
        _per = _df['end_date'].max()
        if best is None or _per > best[0]:
            best = (_per, _df[_df['end_date'] == _per].copy())
    return best


def _load_fin_db(trade_date, codes):
    """fina_indicator_cache 按 ann_date as-of 取每只最近两期（点内，可回测）。

    返回 {ts_code: {'netprofit_yoy','or_yoy','ocf_to_or','ocf_yoy','roe_now','roe_prev'}}。
    取最近两期 ROE 供 F0④「连续亏损扩大」判定；任一步失败均返回 {}（fail-soft）。
    """
    if not codes:
        return {}
    try:
        import sqlite3
        _ph = ','.join(['?'] * len(codes))
        con = sqlite3.connect(FIN_GATE_DB_PATH)
        try:
            _df = pd.read_sql_query(
                "SELECT ts_code, end_date, ann_date, netprofit_yoy, or_yoy, ocf_to_or, ocf_yoy, roe "
                f"FROM fina_indicator_cache WHERE ts_code IN ({_ph}) "
                "AND COALESCE(ann_date,'') <= ?",
                con, params=list(codes) + [str(trade_date)])
        finally:
            con.close()
    except Exception:
        return {}
    if _df is None or _df.empty:
        return {}
    _df = _df.sort_values(['ts_code', 'end_date'])
    out = {}
    for _code, _g in _df.groupby('ts_code'):
        _last = _g.iloc[-1]
        _prev_roe = _fin_num(_g.iloc[-2]['roe']) if len(_g) >= 2 else None
        out[_code] = {
            'netprofit_yoy': _fin_num(_last.get('netprofit_yoy')),
            'or_yoy': _fin_num(_last.get('or_yoy')),
            'ocf_to_or': _fin_num(_last.get('ocf_to_or')),
            'ocf_yoy': _fin_num(_last.get('ocf_yoy')),
            'roe_now': _fin_num(_last.get('roe')),
            'roe_prev': _prev_roe,
        }
    return out


def _load_fundamental_gate(trade_date, codes):
    """基本面数据装配（20261008「两者结合」）：实盘快照扣非同比优先，DB 兜底并补最近两期 ROE。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    _snap = _load_fin_snapshot(trade_date)   # (period, df) or None
    _db = _load_fin_db(trade_date, codes)
    out = {}
    for _code in codes:
        rec = {'dt_yoy': None, 'or_yoy': None, 'ocf_pos': None, 'ocf_yoy': None,
               'roe_now': None, 'roe_prev': None, 'src': ''}
        if _snap is not None:
            _row = _snap[1][_snap[1]['ts_code'] == _code]
            if not _row.empty:
                _r = _row.iloc[-1]
                rec['dt_yoy'] = _fin_num(_r.get('dt_netprofit_yoy'))
                rec['or_yoy'] = _fin_num(_r.get('or_yoy'))
                if rec['or_yoy'] is None:
                    rec['or_yoy'] = _fin_num(_r.get('tr_yoy'))
                _ocfps = _fin_num(_r.get('ocfps'))
                rec['ocf_pos'] = (_ocfps > 0) if _ocfps is not None else None
                rec['ocf_yoy'] = _fin_num(_r.get('ocf_yoy'))
                rec['src'] = f"snapshot_{_snap[0]}"
        _d = _db.get(_code)
        if _d:
            if rec['dt_yoy'] is None:      # 回测/无快照：以净利同比为扣非同比代理
                rec['dt_yoy'] = _d.get('netprofit_yoy')
                rec['src'] = (rec['src'] + '+') if rec['src'] else ''
                rec['src'] += 'db_netprofit_yoy'
            if rec['or_yoy'] is None:
                rec['or_yoy'] = _d.get('or_yoy')
            if rec['ocf_pos'] is None:
                _o = _d.get('ocf_to_or')
                rec['ocf_pos'] = (_o > 0) if _o is not None else None
            if rec['ocf_yoy'] is None:
                rec['ocf_yoy'] = _d.get('ocf_yoy')
            rec['roe_now'] = _d.get('roe_now')
            rec['roe_prev'] = _d.get('roe_prev')
        out[_code] = rec
    return out


def _fin_gate_decision(rec):
    """按已确认口径判 F0/F1/F2，返回 (gate, [命中原因])。数据缺失一律不计命中（fail-soft）。"""
    if not rec:
        return 'F1', []
    _f0 = []
    if rec.get('dt_yoy') is not None and rec['dt_yoy'] < FIN_GATE_DT_YOY_F0:
        _f0.append(f"扣非利润同比{rec['dt_yoy']:+.1f}%(<-30%)")
    if rec.get('or_yoy') is not None and rec['or_yoy'] < FIN_GATE_OR_YOY_F0:
        _f0.append(f"营收同比{rec['or_yoy']:+.1f}%(<-20%)")
    if (rec.get('ocf_pos') is False and rec.get('ocf_yoy') is not None
            and rec['ocf_yoy'] < FIN_GATE_OCF_YOY_F0):
        _f0.append(f"经营现金流为负且同比{rec['ocf_yoy']:+.1f}%(<-50%)")
    if (rec.get('roe_now') is not None and rec.get('roe_prev') is not None
            and rec['roe_now'] < 0 and rec['roe_prev'] < 0 and rec['roe_now'] < rec['roe_prev']):
        _f0.append(f"最近两期ROE为负且恶化({rec['roe_prev']:.1f}%→{rec['roe_now']:.1f}%)")
    if len(_f0) >= 2:
        return 'F0', _f0
    _f2 = []
    if rec.get('dt_yoy') is not None and rec['dt_yoy'] > FIN_GATE_DT_YOY_F2:
        _f2.append(f"扣非利润同比{rec['dt_yoy']:+.1f}%(>+15%)")
    if rec.get('or_yoy') is not None and rec['or_yoy'] > 0:
        _f2.append(f"营收同比{rec['or_yoy']:+.1f}%(>0)")
    if rec.get('ocf_pos') is True:
        _f2.append("经营现金流为正")
    # A 口径（20261008 拍板）：F2 须以「扣非利润同比>+15%」为硬门槛，再叠加 ②/③ 至少 1 条；
    #   否则「营收>0 + 现金流为正」两条常见项即可凑满 2 条，F2 会覆盖全库约一半（区分度被稀释）。
    if (rec.get('dt_yoy') is not None and rec['dt_yoy'] > FIN_GATE_DT_YOY_F2
            and len(_f2) >= 2):
        return 'F2', _f2
    return 'F1', []


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
    vs_squat = sorted([x for x in results if x.get('下蹲信号')], key=_squat_rank_key)
    _res_map = _theme_effect_map(vs_squat)   # 主题效应标注（20261006）：同池同主题 ≥2 只

    lines = [f"# VSW V2 量能爆发+宽幅震荡选股 — {TRADE_DATE}", ""]

    # 大盘环境提示（三指数动量，仅作参考，不再硬性拦截）
    if market_tip:
        _s = " ".join(f"{INDEX_NAMES_3[c]}={market_tip['per'][c]:+.1f}%"
                      for c in market_tip['per'])
        lines.append(f"> 大盘环境: {market_tip['env']} | 三指数20日动量均值 {market_tip['mom20_avg']:+.1f}% ({_s})")
        lines.append(f"> 回测参考(T+5胜率/均收益): {market_tip['win_ref']} | 环境仅供自行决策，不构成买入拦截")
        lines.append("> 买入方式: 次日开盘 · 持有T+5 · 盘中-7%止损 · 每日Top3")
        lines.append("")

    # 🎯 算法输出 TOP3（V2.0 次日新开仓优先：FinalEntryScore 排序，20260821）
    # 旧 r4 排序仅作 V2.0 评分缺失时的降级兜底（20260927：MACD 分支排序已移除，改为纯位置/乖离排序）
    _env_label = (market_tip or {}).get('env', '') or ''
    if '强市' in _env_label:
        _env_mult = 1.05
    elif '震荡偏强' in _env_label:
        _env_mult = 1.0
    elif '震荡偏弱' in _env_label:
        _env_mult = 0.85
    else:
        _env_mult = 0.7
    if all(x.get('FinalEntryScore') is not None for x in results):
        vs_top3 = results[:3]
    else:
        vs_top3 = sorted(results, key=lambda x: (
            x.get('ForbidTOP', False), x['距MA20'] > 3, x['距MA20'], -x['量能爆发评分']))[:3]
    lines.append("## 🎯 算法输出 TOP3（T+1 次日开盘新开仓优先 · FinalEntryScore 排序）")
    if market_tip:
        lines.append(f"【环境提示】{market_tip['env']} | 新开仓系数 {_env_mult:.2f} | 回测参考(T+5): {market_tip['win_ref']} | 是否买入请自行决策")
    for i, _vr in enumerate(vs_top3, 1):
        _medals = ['🥇', '🥈', '🥉'][i - 1]
        _fe = _vr.get('FinalEntryScore')
        if _fe is not None:
            lines.append(f"【TOP{i} {_medals}】{_vr['名称']}({_vr['代码']}) FinalEntryScore={_fe:.1f} 评级={_vr.get('Rating', 'C')}")
            _wk = '⚠高位接力' if _vr.get('ForbidTOP') else ''
            _tag = f"{_vr.get('_v2_label', '')} {_wk}".strip()
            lines.append(f"  趋势={_vr.get('TrendScore', 0):.0f} | 开仓价值={_vr.get('EntryScore', 0):.0f} | "
                         f"EntryTiming={_vr.get('EntryTimingScore', 0):.0f}({_vr.get('EntryTimingGrade', 'C')}) | "
                         f"主题共振={_vr.get('ThemeResonance', 0):.0f} | T1GapRisk={_vr.get('T1Risk', '-')} | "
                         f"RiskScore={_vr.get('Risk_Score', 50):.0f} | {_tag}")
            lines.append(f"  位置: 距MA20={_vr['距MA20']:+.1f}% | 距MA5={_vr.get('距MA5', 0):+.1f}% | 距MA10={_vr.get('距MA10', 0):+.1f}% | "
                         f"5日涨幅={_vr.get('5日涨幅', 0):+.1f}% | 10日涨幅={_vr.get('10日涨幅', 0):+.1f}%")
            _t = f"主题={_vr.get('所属主题', '') or '无主题'}" + (f" | 阶段={_vr.get('非一日游阶段', '')}" if _vr.get('非一日游阶段') else "")
            lines.append(f"  {_t}")
            if _vr.get('_distribution'):
                _vq = '高位放量 ⚠Distribution'
            elif _vr.get('涨日量/跌日量', 1) >= 1.2 and _vr.get('今日量比', 1) < 1.5:
                _vq = '上涨放量→回调缩量→再次承接'
            else:
                _vq = '缩量整理·等待再放量'
            _macd_tag = (_vr['MACD状态'] or '未确认') + (' ⚠️死叉临界' if _vr.get('死叉临界') else '')
            lines.append(f"  量价: {_vq} | MACD={_macd_tag} | 量比={_vr['今日量比']} | "
                         f"区间涨幅={_vr['区间涨幅']:.1f}% | 振幅={_vr['区间振幅']:.1f}%")
            lines.append(_chip_v5_line(_vr))
            lines.append("  【开盘策略】")
            for _g in _vr.get('GapAdvice', []):
                lines.append(f"  {_g}")
            if _vr.get('ForbidTOP'):
                lines.append("  ⚠ 高位接力豁免：距MA20>35%且10日涨幅>30%，禁止常规开仓，仅重大事件+主升初期+龙头唯一+预期差时考虑，FinalEntryScore上限75")
        else:
            lines.append(f"【TOP{i} {_medals}】{_vr['名称']}({_vr['代码']}) 评分{_vr['量能爆发评分']:.0f} {_vr['回撤类型']} 距MA20={_vr['距MA20']:+.1f}%")
            _t = f"主题={_vr.get('所属主题', '') or '无主题'}" + (f" | 阶段={_vr.get('非一日游阶段', '')}" if _vr.get('非一日游阶段') else "")
            lines.append(f"  {_t}")
            _macd_tag = (_vr['MACD状态'] or '未确认') + (' ⚠️死叉临界' if _vr.get('死叉临界') else '')
            lines.append(f"  MACD={_macd_tag} | 量比={_vr['今日量比']} | 区间涨幅={_vr['区间涨幅']:.1f}% | 振幅={_vr['区间振幅']:.1f}%")
            lines.append(_chip_v5_line(_vr))
        lines.append("")
    lines.append("")

    # 🌱 下蹲买点（20260927新增：缩量回踩不破位 → 提前于突破日给低吸买点）
    if vs_squat:
        lines.append("## 🌱 下蹲买点（缩量回踩 · 提前于突破日发信号）")
        lines.append("【筛选条件】基础量能爆发/宽幅震荡结构成立(评分≥65)。"
                     "分两支：①优选=当日缩量(量比≤1.2) + 涨幅≤+1%；"
                     "②扩大=20261003新增，回调后的小阴线/小阳线/十字星（K线实体≤2%）+ 量比≤1.6 + 涨幅 -3~+2.5%。"
                     "下跌价位由「跌破 T0 前一交易日收盘价 → T0 作废」单一口径把关；均线、距20日高、止损空间条件已全部删除"
                     "（20261003 用户要求「放弃和均线的比较」「距20日高也不要」「止损空间删除」）；"
                     "另剔除「缩量过快且持续缩量」= 5日量能变化≤-50% 且 连续缩量≥3天（20261006 用户要求，"
                     "放量资金已撤、量能枯竭，非健康缩量回踩，典型 002584 西陇科学），两支均受此门槛约束")
        lines.append("【排序口径】本段不复用 FinalEntryScore（下蹲段内 FES 仅筹码分/高开风险参与，不描述下蹲形态）；"
                     "按 距MA5↑ → 量能爆发评分↑ → 60日涨幅↑ 排序，即「浅回踩至 MA5 附近（未过度下跌）+ 评分未过热 + 前期未透支」优先。"
                     "（20261006 全市场重标定 n=8733 改方向：旧口径「距MA5 越负越优」已反转为越不负越好，"
                     "深回踩档止损率 25.4% 为浅回踩档 8.0% 的 3 倍；Top3 胜率 40.6%→44.4%、止损率 23.9%→11.0%）"
                     "「优选/扩大」为形态分支标记（扩大=小实体K线企稳），均视为有效下蹲买点")
        lines.append("【主题效应】同一主题在本次下蹲池出现 ≥2 只 → 该主题下每只标注「🔗主题效应=主题名（同池N只）」，"
                     "表示资金在同一方向上多点开花，而非单点试盘；回避区主题 / 无主题不计入"
                     "（20261006 新增，仅标注、不参与评分与排序；勿与评级里的个股维度「主题共振 ThemeResonance」混淆）")
        lines.append("【基本面Gate】简单 3 级否决层，不做复杂评分（20261008 用户要求）；只作用于下蹲信号，"
                     "强买/观察/蓄势不受影响。F0 恶化 → 满足任意 2 条即从下蹲池剔除（不落库，另见文末「基本面否决」段）；"
                     "F1 中性 → 正常入池；F2 改善 → 进入优先池（排序前置，标注在个股行）。"
                     "判据 F0：①扣非利润同比<-30% ②营收同比<-20% ③经营现金流为负且同比<-50% ④最近两期ROE为负且恶化（任意 2 条）；"
                     "F2（A 口径）：①扣非利润同比>+15%（硬门槛，必需）+ ②营收同比>0 / ③经营现金流为正 至少 1 条。"
                     "数据缺失一律归 F1（fail-soft，不误判 F0）；「行业景气改善」本版先不做（20261008 口径拍板）")
        lines.append("【提示项】T0 标志日 / 起量台阶为展示字段；「缩量比」「量能活跃度」「量能形态(5日量能变化/连续缩量天数)」为筛选字段。"
                     "缩量比 = 下蹲日量 / T0日量，阈值 0.50（20260930 由 0.35→0.55→0.50）；"
                     "「止损位」= T0 前一交易日收盘价（展示+落库字段，20261006 用户要求；原为 T0 开盘价）")
        lines.append("  T0 标志日 = 近期第一根「放量阳线」：5日均量较前5日均量跳升≥1.5倍、"
                     "当日量≥起跳前20日均量的3.0倍（20261002 由「含当日20日均量2倍」改口径；20261003 由 2.5→3.0 拉高放大幅度）、"
                     "当日为阳线且涨幅>5%，且此后台阶维持到今日（量能未走）；无合格 T0 者直接剔除")
        lines.append("  涨停兜底（20261003 用户要求）：「T0 可以是涨停，涨停次日放量就不要求涨幅」——"
                     "严格口径找不到 T0 时启用兜底：T0 日为涨停日、或其前一交易日为涨停日时，"
                     "豁免「5日均量新跳升」与「阳线且涨幅>5%」，但 T0 当日量仍须≥起跳前20日均量3.0倍且台阶维持到今日")
        lines.append("  量能活跃度 = 起跳段（T0~今日，含T0）均量 / 起跳前20日均量，门槛≥2.0（20261002 新增）；"
                     "即「起跳后每日成交显著高于起跳前均量」，替代原「近20日自比 max≥2.0 & mean≥1.4」口径")
        lines.append("  基准锚点（用户指定）：海峡创新 0904 起量日量 1,563,037 → 0923 下蹲日量 880,260 "
                     "= 0.56 保留（次日 +19.98%）；光电股份 0914 起量日涨幅仅 +3.00% 不达 5% → 无合格 T0 剔除"
                     "（量能连缩 6 天，后续 -4.0%/-7.2%）")
        lines.append("  历史分组（旧口径全量重标定，T0 合格事件池 2025-10~2026-09 n=99，T+1开盘买/T+5收盘/盘中-7%止损/含0.25%成本）："
                     "阈值≥0.50 全体 n=37 62.2%/+3.71%/止损21.6%；缩量比分档 [0.35,0.45) n=22 36.4%/-2.46% · "
                     "[0.45,0.50) n=13 46.2%/-1.43% · [0.50,0.55) n=15 66.7%/+2.85% · ≥0.55 n=22 59.1%/+4.30%")
        lines.append("  改口径后重标定（20261002，下蹲事件池 2025-09~2026-09 n=27）：池内缩量比最小值≈0.51，"
                     "故 0.50 门槛不再额外筛选；缩量比分档 [0.50,0.55) n=13 61.5%/+0.66% · ≥0.55 n=14 35.7%/+1.09%")
        for i, _vr in enumerate(vs_squat[:10], 1):
            _fg = _vr.get('基本面Gate', 'F1')
            _fg_tag = ' ⭐F2优先' if _fg == 'F2' else ''
            lines.append(f"【下蹲{i}】{_vr['名称']}({_vr['代码']}) 评分{_vr['量能爆发评分']:.0f} "
                         f"等级={_vr.get('下蹲等级', '')} | 基本面={_fg}{_fg_tag}")
            if _fg == 'F2' and _vr.get('基本面Gate原因'):
                lines.append(f"  基本面改善：{_vr.get('基本面Gate原因')}")
            lines.append(f"  {_vr.get('下蹲原因', '')}")
            _t = f"主题={_vr.get('所属主题', '') or '无主题'}" + (
                f" | 阶段={_vr.get('非一日游阶段', '')}" if _vr.get('非一日游阶段') else "")
            _res = _res_map.get(_vr['代码'])
            if _res:
                _t += f" | 🔗主题效应={_res[0]}（同池{_res[1]}只）"
            lines.append(f"  {_t}")
            _macd_tag = (_vr['MACD状态'] or '未确认') + (' ⚠️死叉临界' if _vr.get('死叉临界') else '')
            lines.append(f"  MACD={_macd_tag} | 量比={_vr['今日量比']} | 距MA5={_vr.get('距MA5', 0):+.1f}% "
                         f"| 距20日高={_vr.get('距20日高', 0):+.1f}% | 60日涨幅={_vr.get('60日涨幅', 0):+.1f}%")
            _step_tag = (f"T0=d{_vr['起量台阶天数']}(涨{_vr.get('T0涨幅', 0):+.1f}%) "
                         f"相对T0{_vr['起量台阶涨幅']:+.1f}%")
            lines.append(f"  {_step_tag} | 缩量比={_vr.get('缩量比', 0):.2f} "
                         f"| 量能活跃度={_vr.get('量能活跃度', 0):.2f} "
                         f"| 量能形态=连续缩量{_vr.get('连续缩量天数', 0)}天 / "
                         f"5日量能{_vr.get('5日量能变化', 0):+.1f}% / 近3日=10日的{_vr.get('近3日量能比', 1) * 100:.0f}%")
            _stop_px = _vr.get('止损位')
            if _stop_px:
                _stop_dist = (_stop_px / _vr.get('close', 0) - 1) * 100 if _vr.get('close') else 0.0
                lines.append(f"  止损位={_stop_px:.2f}（T0前一交易日收盘价，距今收{_stop_dist:+.1f}%）")
            if _vr.get('FinalEntryScore') is not None:
                lines.append(f"  FinalEntryScore={_vr.get('FinalEntryScore')} 评级={_vr.get('Rating', 'C')}")
            lines.append(_chip_v5_line(_vr))
        lines.append("【执行】T+1 开盘买入 · 持有 T+5 · 止损位 = T0 前一交易日收盘价（跌破即出，20261006 用户要求；"
                     "原为 T0 开盘价）；下蹲买点为缩量回踩低吸结构，可直接建仓")
        lines.append("  止损口径说明：T0 前一交易日收盘价 = 起涨基石位，与 _vol_step_days 的 T0 作废门槛同一条线"
                     "（整波放量被完全回吐即离场），单一口径把关。（20261001 曾加「止损空间门槛」要求"
                     "下蹲日收盘 ≥ T0开盘价×1.05，20261003 已删除）")
        lines.append("  口径标定（20261006 全市场重标定，下蹲事件池 n=8733，2025-01~2026-09，"
                     "T+1开盘买/T+5收盘/含0.25%成本）："
                     "T0前一交易日收盘价 胜率41.5%/均收益-0.29%/止损12.2%（2025 43.4%/+0.42%/止9.4%，"
                     "2026 40.9%/-0.51%/止13.1%）；T0开盘价 胜率40.0%/-0.27%/止损16.8%"
                     "（2025 42.6%/+0.43%/止12.1%，2026 39.2%/-0.48%/止18.2%）")
        lines.append("  已否决口径：「下蹲日(T-1)收盘价」胜率仅3.3%/止损率96.6% —— 下蹲日为十字星/小实体，"
                     "其收盘价≈次日实际开盘价（中位跳空-0.26%，56%次日直接低开跌破），持仓5日内几乎必然止损，"
                     "策略退化为每日止损，故不可用")
        lines.append("  历史标定（20261002 改口径后，下蹲事件池 2025-09~2026-09，T+1开盘买/T+5收盘/含0.25%成本）："
                     "T0止损口径 n=14 57.1%/+3.38%/止损28.6%；-7%口径 n=14 57.1%/+3.59%"
                     "（20261003 删除均线/距20日高/止损空间条件、20261006 新增「缩量过快且持续缩量」剔除后均未重标定）")
        _n_sq = len(vs_squat)
        _n_sq_lo = sum(1 for x in vs_squat if x.get('量能爆发评分', 0) < 85)
        _sq_tier = (f"【分档提示】本批 n={_n_sq}：评分<85 档 {_n_sq_lo} 只 / ≥85 档 {_n_sq - _n_sq_lo} 只"
                    f" | 历史分组(20261002 改口径后 n=14)：全部落 ≥85 档 57.1%/+3.38%（<85 档样本为 0，"
                    f"旧口径的「<85 档更强」结论已不可复核）")
        lines.append(_sq_tier)
        lines.append(f"【T0/缩量提示】本批 n={_n_sq}：均具备合格 T0 标志日（放量阳线涨幅>5%，或涨停/涨停次日兜底）、"
                     f"量能活跃度≥2.0（20261002 新增）、缩量比≥0.50（20260930 由 0.35→0.55→0.50）、"
                     f"且均不属「缩量过快且持续缩量」（5日量能变化≤-50% 且 连续缩量≥3天 → 剔除，20261006 新增）")
        lines.append("【回测参考】现行算法下蹲买点(T0涨>5%或涨停/涨停次日兜底 + T0量≥起跳前20日3.0倍 + "
                     "量能活跃度≥2.0 + 缩量比≥0.50 + 非「缩量过快且持续缩量」) 2025-09~2026-09 n=14："
                     "T0开盘价止损口径 胜率57.1% / 均+3.38%（-7%口径 57.1% / +3.59%）"
                     "（20261003 删除均线/距20日高/止损空间条件、20261006 新增「缩量过快且持续缩量」剔除后均未重标定）")
        lines.append("")

    # 🧱 基本面否决（F0）：原下蹲候选被基本面恶化剔除，列此供复核（20261008 用户要求）
    _f0_list = sorted([x for x in results if x.get('基本面否决')], key=lambda x: -x.get('量能爆发评分', 0))
    if _f0_list:
        lines.append("## 🧱 基本面否决（F0 恶化 · 已从下蹲池剔除，不构成买入依据）")
        lines.append("说明：下列标的量能结构上本可给出下蹲买点，但基本面 Gate 判为 F0（满足任意 2 条恶化判据），"
                     "按 20261008 用户要求「基本面恶化 → VSW信号直接降级」剔除出池、不落库。")
        for i, _x in enumerate(_f0_list, 1):
            lines.append(f"【否决{i}】{_x['名称']}({_x['代码']}) 评分{_x.get('量能爆发评分', 0):.0f} "
                         f"等级={_x.get('下蹲等级', '')}")
            lines.append(f"  基本面原因：{_x.get('基本面Gate原因') or '（数据缺失未计入，异常）'}")
        lines.append("")

    # 🚨 排除的高分股票（V2.0：趋势强但位置/主题/风险不适合次日新开仓）
    # 只列「未进入 TOP3」的高分股，TOP3 已按 FinalEntryScore 排序，不在排除清单中重复出现
    _top3_codes = {x['代码'] for x in vs_top3}
    _excluded = []
    for _x in results:
        if _x['代码'] in _top3_codes:
            continue
        if _x.get('下蹲信号'):
            continue   # 下蹲买点单独成段，不在排除清单重复出现
        if _x.get('ForbidTOP'):
            _excluded.append((_x, '巨幅乖离+急速上涨（强制过滤）'))
        elif _x.get('Rating') == 'C' and _x.get('量能爆发评分', 0) >= 85:
            _reasons = []
            if _x.get('距MA20', 0) > 25:
                _reasons.append(f"距MA20={_x['距MA20']:+.1f}%")
            if _x.get('非一日游阶段') == '高潮':
                _reasons.append('主题高潮')
            if _x.get('非一日游阶段') == '退潮' or '(回避)' in (_x.get('所属主题', '') or ''):
                _reasons.append('主题退潮/回避')
            if _x.get('T1Risk') == 'Extreme':
                _reasons.append('T1GapRisk=Extreme')
            if _reasons:
                _excluded.append((_x, '；'.join(_reasons)))
    if _excluded:
        lines.append("## 🚨 排除的高分股票（趋势强 ≠ 适合次日新开仓）")
        for _x, _why in _excluded[:10]:
            lines.append(f"- {_x['名称']}({_x['代码']}) 原始评分{_x['量能爆发评分']:.0f} → FinalEntryScore={_x.get('FinalEntryScore', '-')} {_x.get('Rating', 'C')}")
            _xpos = f"  位置: 距MA20={_x['距MA20']:+.1f}% | 5日涨幅={_x.get('5日涨幅', 0):+.1f}% | 10日涨幅={_x.get('10日涨幅', 0):+.1f}% | T1GapRisk={_x.get('T1Risk', '-')}"
            if _x.get('非一日游阶段'):
                _xpos += f" | 阶段={_x['非一日游阶段']}"
            lines.append(_xpos)
            lines.append(f"  原因: {_why} | 趋势强/适合持股，但次日开仓赔率差")
        lines.append("")

    # 最终结论
    lines.append("## 最终结论（T+1 新开仓优先级）")
    _buyable = [x for x in results[:6] if x.get('Eligible') and x.get('Rating') in ('S', 'A', 'B') and not x.get('ForbidTOP')]
    if _buyable:
        _pri = ['第一优先', '第二优先', '第三优先']
        for _i, _b in enumerate(_buyable[:3]):
            _btag = '（低吸/次日确认优先）' if _b.get('Rating') == 'B' else ''
            lines.append(f"{_pri[_i]}：{_b['名称']}({_b['代码']}) FinalEntryScore={_b.get('FinalEntryScore', '-')} 评级={_b.get('Rating', '')}{_btag}")
        lines.append("只买：低乖离 + 趋势未坏 + 主线共振 + 回调结束 + 次日高开风险可控")
    else:
        lines.append("今日无高胜率可买标的：无同时满足 低乖离+趋势健康+主线共振+回调结束+高开风险可控 的候选 → **空仓观望**")
        _best = results[0] if results else None
        if _best:
            lines.append(f"最接近候选: {_best['名称']}({_best['代码']}) FinalEntryScore={_best.get('FinalEntryScore', '-')} 评级={_best.get('Rating', '')}（仍缺 主题共振/位置 等条件，仅观察）")
    lines.append("坚决避免：高位 + 高潮 + 巨大乖离 + 放量加速末端 + 预期一致性过强")
    _sq_opt = [x for x in vs_squat if x.get('下蹲等级') == '优选'][:2]
    if _sq_opt:
        lines.append("🌱 下蹲买点（优选）：" + "、".join(f"{x['名称']}({x['代码']})" for x in _sq_opt)
                     + " → 缩量回踩，提前于突破日，详见下方「下蹲买点」段")
    lines.append("")

    # ===== P3 强买/观察/蓄势 大幅降级为折叠摘要（20261007）=====
    # 变更原因（用户提出「20260930 信号偏多」后诊断）：
    #   实测 20260930 命中 68 只 = 下蹲 18 + 强买 37 + 观察 24；且这 3 类均为
    #   **纯展示字段**（强买/观察/蓄势 既不参与 FinalEntryScore 排序，也不写 stock_pick_db，
    #   唯一下蹲买点才落库），却在报告里各占 10 行明细 → 真正可执行的「下蹲 18 只」
    #   被 71 只不可执行的展示信号淹没，阅读噪声远大于信息量。
    # 处置：折叠为单行摘要（数量 + 名称列表），不再逐只展开。
    #   保留少量必要信息（数量与前若干名称）以便知道「今天有没有」，
    #   需要明细时可用 --debug / 直接看 stock_pick_db（仅下蹲落库）。
    _DISPLAY_MAX = 12   # 折叠摘要最多列出的名称数

    def _fold(names, total, limit=_DISPLAY_MAX):
        head = "、".join(names[:limit])
        more = f" 等{total}只" if total > limit else ""
        return f"{total} 只：{head}{more}"

    # 强买
    lines.append("## 🔥 强买信号（纯展示 · 不排序 · 不落库）")
    if vs_strong_buy:
        lines.append(f"  {_fold([x['名称'] for x in vs_strong_buy], len(vs_strong_buy))}")
        _r0 = vs_strong_buy[0]
        lines.append(f"  触发示例：{_r0['名称']} — {_r0['强买原因']}")
        lines.append("  ⚠️ 强买已不参与 FinalEntryScore 排序、不写 stock_pick_db，"
                     "**不可作为下单依据**；买入只看下方「下蹲买点」段。")
    else:
        lines.append("  今日无强买信号")
    lines.append("【历史标定】强买全量胜率 44.8%/均+1.73%（不低于评分Top3）；"
                 "但「强买优先」排序为负优化(41.3%/+0.90% vs 纯评分Top5 41.9%/+1.41%)，故仅作形态参考。")
    lines.append("")

    # 观察
    if vs_watch:
        lines.append("## 👀 观察信号（纯展示 · 结构成立但未构成下蹲买点）")
        lines.append(f"  {_fold([x['名称'] for x in vs_watch], len(vs_watch))}")
        lines.append("  ⚠️ 观察票尚未构成缩量回踩买点，仅供跟踪，不落库。")
        lines.append("")

    # 蓄势大涨
    if vs_wave_surge:
        lines.append("## 🌊 蓄势大涨信号（纯展示 · 与下蹲低吸逻辑无关）")
        lines.append(f"  {_fold([x['名称'] for x in vs_wave_surge], len(vs_wave_surge))}")
        _w0 = vs_wave_surge[0]
        lines.append(f"  形态示例：{_w0['名称']} — {_w0.get('蓄势大涨原因', '')}")
        lines.append("  ⚠️ 蓄势票属「W1大涨+W2回调」的强势回撤位（常见 W1 +80%~+170%），"
                     "与下蹲买点的「浅回踩低吸」是两种不同策略，**不可混看**；不落库。")
        lines.append("")

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


def _track_picks(results, trade_date):
    """当日信号落库 stock_pick_db（20260930 收敛）：
    只保留下蹲买点（与评级解耦：下蹲日 FES 常被死叉惩罚压低，但信号本身有独立回测支撑）；
    原先「Eligible 且 Rating∈(S,A,B)」的 TOP6 已不再落库，仅留在报告里。
    其余评级/择时/主题/距MA 等字段自动进 indicators；失败不阻塞主流程。
    """
    if _PICK_RECORD is None or not results:
        return

    def _mk(s, idx, signal, action):
        _rating = s.get('Rating', 'C')
        return {
            'ts_code': s.get('代码'), 'stock_name': s.get('名称'), 'close': s.get('close'),
            'pct_chg': s.get('今日涨跌幅'),
            'signal': signal,
            'action': action,
            'score': s.get('FinalEntryScore'), 'rank_no': idx,
            'reason': s.get('下蹲原因') or s.get('强买原因') or s.get('观察原因') or s.get('蓄势大涨原因') or '',
            'stop_price': s.get('止损位'),   # 止损位 = T0 前一交易日收盘价（20261006 用户要求；原为 T0 开盘价）
            'FinalEntryScore': s.get('FinalEntryScore'), 'Rating': _rating,
            'EntryTimingScore': s.get('EntryTimingScore'), 'EntryTimingGrade': s.get('EntryTimingGrade'),
            'T1Risk': s.get('T1Risk'), '量能爆发评分': s.get('量能爆发评分'),
            '距MA20': s.get('距MA20'), '5日涨幅': s.get('5日涨幅'),
            '所属主题': s.get('所属主题'), '非一日游阶段': s.get('非一日游阶段'),
            'ChipSuggestion': s.get('ChipSuggestion'),
            '基本面Gate': s.get('基本面Gate', 'F1'),   # 基本面否决层分档（20261008）：F2 优先 / F1 中性
        }

    rows = []
    _squat_all = [x for x in results if x.get('下蹲信号')]
    _res_map = _theme_effect_map(_squat_all)   # 主题效应标注（20261006）
    for s in sorted(_squat_all, key=_squat_rank_key):
        _opt = (s.get('下蹲等级') == '优选')
        _res = _res_map.get(s.get('代码'))
        # 信号名带上形态分支（20261003 用户要求：在下蹲后用括号标等级，便于库/跟踪池一眼区分）；
        # 命中主题效应再加「·主题效应」后缀（20261006 用户要求）
        _sig = ('VSW_下蹲(优选)' if _opt else 'VSW_下蹲(扩大)') + ('·主题效应' if _res else '') \
            + ('·F2优先' if s.get('基本面Gate') == 'F2' else '')
        _row = _mk(s, len(rows) + 1, _sig,
                   '可开仓·下蹲买点' if _opt else '可开仓·下蹲待确认(半仓)')
        if _res:
            _row['reason'] = f"{_row.get('reason') or ''}｜🔗主题效应：{_res[0]}（同池{_res[1]}只）"
        rows.append(_row)

    if not rows:
        print('[VSW] 今日无下蹲信号，stock_pick_db 无写入', flush=True)
        return
    n = _PICK_RECORD('vsw', 'VSW 量能爆发+宽幅震荡', rows, pick_date=trade_date,
                     replace=True)   # replace=True：清理当日旧记录，避免被剔除标的（如 002584）残留库/网页
    print(f'[VSW] stock_pick_db 写入 {n}/{len(rows)} 条 (strategy=vsw pick_date={trade_date})', flush=True)


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
