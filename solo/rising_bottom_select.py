"""
RB（Rising Bottom · 底部不断抬高）—— 中长线趋势形态选股

形态定义（对标 301550.SZ 斯菱智驱 2026-07~10）：
  一轮中长下跌见底后，每一次回踩的低点都比上一次更高（底部不断抬高），
  同时高点也在抬高、MA60 持续上行；买点落在「抬高后的回踩位」，而不是加速末段。

判定链（全部为「收盘后」可复现的口径，无未来函数）：
  1) 摆动低点：窗口内 low 为前后各 PIVOT_K 根的最低点 → 摆动低点；
  2) 递升低点链：低点序列里，若新低点 < 前一低点 × LOW_TOL 则视为「破位」、链重新起算；
     取最近一条链（即当前正在走的这条），要求低点数 >= MIN_LOWS；
  3) 底部抬高幅度：(链末低点 / 链首低点 - 1) >= MIN_ELEV_PCT；
  4) 底部已探明：链首低点须贴近窗口最低价（<= 窗口最低 × BOTTOM_TOL），
     即这条抬升链是从真正的低点起步的，不是半山腰截取的一段；
  5) 高点抬高：窗口最高价出现在窗口后段（位置 >= HH_POS_MIN），趋势重心在上移；
  6) 中期趋势：MA20 近 MA20_SLOPE_BARS 日变化率 >= MA20_SLOPE_MIN_PCT，MA20 >= MA60 ×0.995，
     且收盘 >= MA60 × PRICE_MA60_MIN；
  7) 不追高：现价距链末低点 <= MAX_EXT_FROM_LOW_PCT，距 MA60 <= MAX_DIST_MA60_PCT；
  8) 形态新鲜：链末低点距今天 <= LAST_LOW_AGE_MAX 根（老形态已失效，等新的回踩）；
  9) 量价健康度：涨放量/跌缩量、近5日无放量砸盘、量能不枯竭（见 vol_price_health）。

主题/概念联动（20261028 新增）：
  命中池本身就是「同一形态」的横截面，若同一题材下同时有多只票在抬高底部，
  说明是产业/资金主线在做，而非个股孤立行为。故统计「今日命中池内同概念家数」，
  家数越多 → 主题分越高（0~15），综合分 = 技术分 + 主题分，排序与优选档均按综合分。
  剔除股通/融资融券/机构重仓等交易属性标签，宽泛概念（人工智能等）家数折半。

与 VSW / W7 的区别：
  - VSW 要「量能爆发 + 宽幅震荡」，W7 要「天量 T0 后的二波」，都属于放量短中线；
  - RB 只认「价格结构的底部抬高」，不要求放量，持有周期放到 20~60 个交易日，
    因此量能只作为评分项（回踩缩量加分），不作为门槛。

用法：
  python rising_bottom_select.py [YYYYMMDD] [--no-mail]   # 默认取缓存里最新交易日，跑完单独发邮件
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
import json
import re

import numpy as np
import pandas as pd

CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
BASIC_PATH = os.path.join(CACHE_DIR, "stock_basic.csv")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

STRATEGY_ID = "rb_trend"
STRATEGY_NAME = "RB 底部抬高(中长线)"

# ── 形态参数（20261028 以 301550.SZ 标定，均可回溯调整）────────────────────
WINDOW = 150              # 形态观察窗口（交易日，约 7 个月）
PIVOT_K = 5               # 摆动点判定：前后各 5 根
MIN_LOWS = 4              # 递升低点链最少低点数（含链首）：3 个低点只够「两次抬高」，噪声大
MIN_CHAIN_SPAN = 35       # 链首→链末低点的最小跨度（交易日）：中长线形态，不能是几周内的短促反弹
LOW_TOL = 0.97            # 相邻低点容差：新低点 >= 前低 ×0.97 视为「抬高」（不破前低 3%）
MIN_ELEV_PCT = 20.0       # 链首→链末低点的累计抬高下限（%）
BOTTOM_TOL = 1.10         # 链首低点须 <= 窗口最低价 ×1.10（确认底部已探明）
HH_POS_MIN = 0.55         # 窗口最高价的位置须 >= 窗口的 55%（高点在后段 → 重心上移）
# 中期趋势判据用 MA20 斜率 + MA20/MA60 相对位置，而不是 MA60 斜率：
# 301550 这类「深跌后 V 型抬高」在 20261015 时 MA60 仍含 5 月 170→62 的崩塌段，
# 近 20 日 MA60 斜率仅 +0.18%（lag 40 更是 -15.8%），会把形态直接判死；
# 而 MA20 近 20 日 +6.8% 且 MA20(94.34) 已在 MA60(85.67) 上方 —— 这才是「中期转折已成立」。
MA20_SLOPE_BARS = 20      # MA20 斜率观察长度（交易日）
MA20_SLOPE_MIN_PCT = 2.0  # MA20 近 20 日变化率下限（%）
MA20_MA60_MIN = 0.995     # MA20 >= MA60 ×0.995（短均线在长均线上方/刚金叉）
PRICE_MA60_MIN = 0.97     # 收盘 >= MA60 ×0.97（中期趋势未破）
MAX_DIST_MA60_PCT = 15.0  # 收盘距 MA60 上限（%）：离得太远说明已加速，不是回踩位
MAX_EXT_FROM_LOW_PCT = 20.0  # 收盘距链末低点上限（%）：不追高，等回踩位
MIN_EXT_FROM_LOW_PCT = -3.0  # 收盘距链末低点下限（%）：跌破最近低点 3% 视为形态走坏
LAST_LOW_AGE_MAX = 40     # 链末低点距今天的最大根数（形态新鲜度）
MIN_BARS = 260            # 上市满一年才参与
SCORE_WATCH = 60.0        # 观察档
SCORE_PICK = 72.0         # 优选档

# ── 量价健康度过滤（20261028）──────────────────────────────────────────────
# 底部抬高只说明「价格结构」在改善，还要确认是资金在承接、而不是缩量阴跌里画出来的抬高：
#   ① 量价配合：近 60 日「上涨日均量 / 下跌日均量」>= 0.85 —— 涨没人跟、跌有人砸 = 派发特征；
#   ② 无放量砸盘：近 5 日不得出现「量比 >= 2.0 且 当日跌幅 <= -3%」，高位放量长阴一律剔除；
#   ③ 量能不枯竭：近 5 日均量 / 近 60 日均量 >= 0.50 —— 缩成死票的抬高没有资金承接。
# 三条均为硬过滤（不是扣分项），阈值取得较宽，只剔除明显的量价背离。
VPH_LOOKBACK = 60         # 量价配合统计窗口（交易日）
VPH_UP_DN_VOL_MIN = 0.85  # 上涨日均量 / 下跌日均量 下限
VPH_DRYUP_MIN = 0.50      # 近5日均量 / 近60日均量 下限
VPH_DUMP_VOLR = 2.0       # 放量砸盘判据：当日量 / 前20日均量
VPH_DUMP_PCT = -3.0       # 放量砸盘判据：当日涨幅上限（%）
VPH_DUMP_DAYS = 5         # 砸盘检查窗口：最近 N 根

# ── 主题/概念联动（20261028 用户口径「同主题的数量越多越好」）─────────────────
# 数据源：cache_daily/theme_stock_map_latest.json（当日 v2，约 3000 只）与
#         theme_stock_map_latest_v1.json（0803 全量，约 5300 只），两者取并集补全。
# 逻辑：统计「今日 RB 命中池」内同一概念的家数，家数越多 → 主题分越高 → 排名越靠前；
#      主题分同时可以让技术分略逊但题材共振明确的标的晋升「优选」。
THEME_MIN_CNT = 2         # 至少 2 家才认为存在共振
THEME_FULL_CNT = 7        # 同一概念 7 家以上拿满主题分
THEME_BONUS_MAX = 15.0    # 主题联动分上限（综合分 = 技术分 + 主题分，满分 115）
THEME_BROAD_FACTOR = 0.5  # 宽泛概念（人工智能/数据中心等）家数折半，避免大主题垄断
SCORE_PICK_THEME = 65.0   # 主题晋升：技术分达到此值且主题分达标即可升「优选」
THEME_BONUS_PROMOTE = 9.0 # 主题晋升所需的主题分（约同概念 4~5 家）

# ── 邮件推送（与项目内 daily_news_digest / theme3_opportunity 同一 agently-cli 通道）──
MAIL_CLI = r"C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd"
MAIL_TO = "stock1975@qq.com"

# 交易属性/风格/财务/区域标签：不是产业链题材，不能用来做共振统计
_CONCEPT_SKIP = re.compile(
    r'股通|融资融券|转融券|转融通|融券标的|同股同权|龙头|大盘股|中盘股|小盘股|微盘股|'
    r'高价股|低价股|微利股|亏损股|白马股|茅指数|MSCI|标准普尔|标普|富时|沪深\d|上证\d|'
    r'中证\d|深证\d|深成|创业板综|创业成份|科创50|创业板50|AH股|B股|次新股|涨停|高振幅|'
    r'股权激励|高送转|纳入|央企央资|政府控股|双百企业|出海\d|独角兽|分拆上市|中科院系|'
    r'高校|国企改革|中特估|专精特新|创投|顺周期|周期股|机构重仓|基金增仓|基金减仓|'
    r'基金重仓|私募新进|QFII|社保|养老金|破发|破净|破增发价|百元股|转债标的|昨日|'
    r'地方国资|省国资|国资|国家大基金|参股|举牌|股权转让|ST板块|壳资源|'
    r'多板|连板|打板|龙虎榜|游资|热门|活跃股|换手率|成交额|量比|振幅|'
    r'东方财富|同花顺|雪球|热股|人气榜|关注度|'
    r'高成长股|成长股|价值股|趋势股|反转股|超跌|强势股|题材股|业绩股|白马|'
    r'大盘成长|中盘成长|小盘成长|大盘价值|消费风格|科技风格|金融风格|周期风格|风格指数|'
    # 财务/业绩类标签
    r'预盈预增|预增|预减|业绩预|中报|年报|季报|首亏|扭亏|续盈|略增|略减|净利润|营收|'
    r'高派息|高股息|送转|回购|拟增持|员工持股|重组|摘帽|解禁|'
    # 区域/政策泛主题
    r'长江三角|长三角|珠三角|京津冀|粤港澳|西部大开发|东北振兴|中部崛起|自贸区|'
    r'海南|雄安|一带一路|新型城镇化|乡村振兴|地方国企'
)
# 大而全的宽泛概念：几乎什么股都能挂，家数天然靠前，统计时折半
_CONCEPT_BROAD = re.compile(
    r'人工智能|大数据|云计算|TMT|自主可控|信创|国产软件|数字经济|AIGC|DeepSeek|'
    r'机器人概念|华为概念|消费电子产业|半导体产业|新能源车|大消费|芯片概念|'
    r'无人驾驶|车联网|智能汽车|激光雷达|数据中心|5G概念|算力|AI应用|AI智能体|'
    r'多模态AI|数据要素|网络安全|互联网服务|元宇宙|新型工业化|电商|物联网|'
    r'节能环保|专精特新|国产替代|军民融合'
)

# 落库（失败不阻塞主流程）
try:
    from stock_pick_db import record_picks as _PICK_RECORD, upsert_strategy as _UPSERT_STRATEGY
except Exception:
    _PICK_RECORD = None
    _UPSERT_STRATEGY = None


# ── 数据读取 ──────────────────────────────────────────────────────────────
_BARS_SQL = """SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pct_chg, d.vol, d.amount
  FROM daily_cache AS d
 WHERE d.trade_date >= ? AND d.trade_date <= ?
 ORDER BY d.ts_code, d.trade_date"""


def _qfq(df):
    """前复权价格（按每股序列末端的复权因子统一缩放），保证低点/高点可比。"""
    if "adj_factor" not in df.columns or df["adj_factor"].isna().all():
        return df
    adj = df.groupby("ts_code")["adj_factor"].ffill()
    last = adj.groupby(df["ts_code"]).transform("last")
    ok = adj.notna() & last.notna() & (last > 0)
    for col in ("open", "high", "low", "close"):
        src = df[col].astype(float)
        df[col] = np.where(ok, src * adj / last, src)
    return df


def load_bars(conn, end_date, warmup_days=320):
    """读取 end_date 之前 warmup_days 个自然日内的全市场日线（含复权因子）。"""
    start = (pd.to_datetime(end_date) - pd.Timedelta(days=int(warmup_days * 1.6))).strftime("%Y%m%d")
    q = """SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pct_chg, d.vol, d.amount,
                  a.adj_factor
           FROM daily_cache AS d
           LEFT JOIN adj_factor_cache AS a
             ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
          WHERE d.trade_date >= ? AND d.trade_date <= ?
          ORDER BY d.ts_code, d.trade_date"""
    df = pd.read_sql_query(q, conn, params=(start, end_date))
    if df.empty:
        return df
    for col in ("open", "high", "low", "close", "pct_chg", "vol", "amount", "adj_factor"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close", "high", "low", "vol"])


def load_names(end_date):
    """名称/行业：优先当日快照，回退 stock_basic.csv。"""
    out = {}
    snap = os.path.join(CACHE_DIR, "market_" + str(end_date) + ".csv")
    if os.path.exists(snap):
        s = pd.read_csv(snap, dtype={"ts_code": str})
        for _, r in s.iterrows():
            out[r["ts_code"]] = (r.get("name", ""), r.get("industry", ""))
    if os.path.exists(BASIC_PATH):
        b = pd.read_csv(BASIC_PATH, dtype={"ts_code": str})
        for _, r in b.iterrows():
            out.setdefault(r["ts_code"], (r.get("name", ""), r.get("industry", "")))
    return out


def index_close(conn, end_date, bars=90):
    """上证指数收盘序列（RS 用）。"""
    q = "SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000001.SH' AND trade_date<=? ORDER BY trade_date DESC LIMIT ?"
    try:
        d = pd.read_sql_query(q, conn, params=(end_date, bars))
    except Exception:
        return None
    if d.empty:
        return None
    d = d.sort_values("trade_date")
    return pd.to_numeric(d["close"], errors="coerce").to_numpy(dtype=float)


# ── 主题/概念 ─────────────────────────────────────────────────────────────
def load_concepts():
    """个股概念标签：theme_stock_map 当日版 + 全量版取并集（缺一只补一只）。

    返回 {ts_code: [concept, ...]}，已剔除交易属性/风格类标签。
    """
    out = {}
    files = ["theme_stock_map_latest.json", "theme_stock_map_latest_v1.json"]
    # 兜底：latest 缺失时取目录里最新的两个全量快照
    if not os.path.exists(os.path.join(CACHE_DIR, files[0])):
        snap = sorted(
            (f for f in os.listdir(CACHE_DIR)
             if f.startswith("theme_stock_map_2") and f.endswith(".json")),
            reverse=True)[:2]
        files = snap or files
    for fn in files:
        path = os.path.join(CACHE_DIR, fn)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for code, info in (data.get("stocks") or {}).items():
            if not isinstance(info, dict):
                continue
            cs = [c for c in (info.get("concepts") or [])
                  if isinstance(c, str) and c and not _CONCEPT_SKIP.search(c)]
            if cs:
                out.setdefault(code, [])
                for c in cs:
                    if c not in out[code]:
                        out[code].append(c)
    return out


def attach_themes(hits, concepts):
    """按「今日 RB 命中池」内的同概念家数打分，并重排。

    命中池本身即「同一形态」的集合，若同一题材下有三五只同时出现抬高底部，
    说明是产业/资金主线在做，而不是个股孤立行为 —— 家数越多越优先。
    """
    cnt = {}
    for h in hits:
        h["concepts"] = list(concepts.get(h["ts_code"], ()))
        for c in h["concepts"]:
            cnt[c] = cnt.get(c, 0) + 1
    for h in hits:
        best, best_eff, best_raw = "", 0.0, 0
        for c in h["concepts"]:
            raw = cnt.get(c, 0)
            if raw < THEME_MIN_CNT:
                continue
            eff = raw * (THEME_BROAD_FACTOR if _CONCEPT_BROAD.search(c) else 1.0)
            if eff > best_eff:
                best, best_eff, best_raw = c, eff, raw
        bonus = min(max(best_eff - 1.0, 0.0) / max(THEME_FULL_CNT - 1.0, 1.0),
                    1.0) * THEME_BONUS_MAX
        h["theme"] = best
        h["theme_cnt"] = best_raw
        h["theme_bonus"] = round(float(bonus), 1)
        h["total"] = round(float(h["score"]) + bonus, 1)
    # 同题材家数优先（综合分已含主题分），其次技术分
    hits.sort(key=lambda x: (-x["total"], -x["theme_cnt"], -x["score"]))
    return cnt


# ── 形态识别 ──────────────────────────────────────────────────────────────
def vol_price_health(close, vol):
    """量价健康度：返回指标 dict；明显背离返回 None（硬过滤，见上方参数注释）。"""
    if len(close) < 30 or len(vol) < 30:
        return None
    k = min(VPH_LOOKBACK, len(close) - 1)
    c, v = close[-k - 1:], vol[-k - 1:]
    d = np.diff(c)
    up_v, dn_v = v[1:][d > 0], v[1:][d < 0]
    if len(up_v) < 5 or len(dn_v) < 5:
        return None
    up_dn = float(np.mean(up_v)) / max(float(np.mean(dn_v)), 1e-9)
    if up_dn < VPH_UP_DN_VOL_MIN:
        return None

    base20 = float(np.mean(vol[-25:-5])) if len(vol) >= 25 else float(np.mean(vol[:-5]))
    m = min(VPH_DUMP_DAYS, len(vol) - 1)
    vr = vol[-m:] / max(base20, 1e-9)
    pct = (close[-m:] / close[-m - 1:-1] - 1) * 100
    if np.any((vr >= VPH_DUMP_VOLR) & (pct <= VPH_DUMP_PCT)):
        return None

    v5 = float(np.mean(vol[-5:]))
    v60 = float(np.mean(vol[-60:])) if len(vol) >= 60 else float(np.mean(vol))
    dry = v5 / max(v60, 1e-9)
    if dry < VPH_DRYUP_MIN:
        return None
    return {"up_dn_vol": round(up_dn, 2), "vol_dryup": round(dry, 2)}


def pivot_lows(low, k=PIVOT_K):
    """摆动低点索引：low[i] 为 [i-k, i+k] 区间最低。"""
    idx = []
    n = len(low)
    for i in range(k, n - k):
        w = low[i - k:i + k + 1]
        if low[i] <= w.min() + 1e-9:
            idx.append(i)
    return idx


def rising_chain(low, pivots, tol=LOW_TOL):
    """从摆动低点里取「最近一条」递升链（破位即重新起算）。"""
    chain = []
    for i in pivots:
        if not chain or low[i] >= low[chain[-1]] * tol:
            chain.append(i)
        else:
            chain = [i]
    return chain


def evaluate(g, idx_close, rs_base=None):
    """单只股票的形态判定；返回 dict（命中）或 None。g 已按 trade_date 升序。"""
    n = len(g)
    if n < MIN_BARS:
        return None
    w = g.iloc[-WINDOW:].reset_index(drop=True)
    _off = len(g) - len(w)
    low = w["low"].to_numpy(dtype=float)
    high = w["high"].to_numpy(dtype=float)
    close = w["close"].to_numpy(dtype=float)
    vol = w["vol"].to_numpy(dtype=float)

    if vol[-1] <= 0 or close[-1] <= 0:
        return None
    # 当日大跌/跌停不列：买点落在崩塌日没有意义
    if len(close) >= 2 and (close[-1] / close[-2] - 1) * 100 <= -7.0:
        return None

    piv = pivot_lows(low)
    if len(piv) < MIN_LOWS:
        return None
    chain = rising_chain(low, piv)
    if len(chain) < MIN_LOWS:
        return None

    lows_chain = [float(low[i]) for i in chain]
    elev = lows_chain[-1] / lows_chain[0] - 1 if lows_chain[0] > 0 else 0.0
    if elev * 100 < MIN_ELEV_PCT:
        return None
    # 底部已探明：链首低点贴近窗口最低价
    if lows_chain[0] > float(low.min()) * BOTTOM_TOL:
        return None
    # 高点抬高：抬升段（链首低点起）内的最高价落在该段后段 → 重心在上移，不是反弹即回落
    seg_high = high[chain[0]:]
    hh_pos = float(np.argmax(seg_high)) / max(len(seg_high) - 1, 1)
    if hh_pos < HH_POS_MIN:
        return None
    # 形态新鲜度
    last_age = len(low) - 1 - chain[-1]
    if last_age > LAST_LOW_AGE_MAX:
        return None
    # 链条跨度：中长线形态须横跨足够多的交易日
    if chain[-1] - chain[0] < MIN_CHAIN_SPAN:
        return None

    # 中期趋势：MA20 上行 + 短均线位于长均线上方 + 价在 MA60 上方
    c = g["close"].to_numpy(dtype=float)
    if n < 60 + MA20_SLOPE_BARS:
        return None
    ma20_now = float(np.mean(c[-20:]))
    ma20_prev = float(np.mean(c[-20 - MA20_SLOPE_BARS:-MA20_SLOPE_BARS]))
    ma60_now = float(np.mean(c[-60:]))
    if ma20_prev <= 0 or ma60_now <= 0:
        return None
    ma20_slope = (ma20_now / ma20_prev - 1) * 100
    if ma20_slope < MA20_SLOPE_MIN_PCT:
        return None
    if ma20_now < ma60_now * MA20_MA60_MIN:
        return None
    px = float(close[-1])
    if px < ma60_now * PRICE_MA60_MIN:
        return None

    dist_ma60 = (px / ma60_now - 1) * 100
    ext_low = (px / lows_chain[-1] - 1) * 100
    if dist_ma60 > MAX_DIST_MA60_PCT:
        return None
    if not (MIN_EXT_FROM_LOW_PCT <= ext_low <= MAX_EXT_FROM_LOW_PCT):
        return None

    # 量价健康度（硬过滤）：涨放量/跌缩量、近5日无放量砸盘、量能不枯竭
    vph = vol_price_health(close, vol)
    if vph is None:
        return None

    # ── 评分（100 分）──
    s_lows = min(len(chain), 6) / 6 * 20                     # 低点数量（底部抬高次数）
    s_elev = min(elev, 1.0) * 20                              # 抬高幅度（≥100% 满分）
    s_slope = min(max(ma20_slope, 0.0), 10.0) / 10 * 20     # MA20 斜率（+10% 满分）
    s_pos = max(0.0, 1 - max(dist_ma60, 0.0) / MAX_DIST_MA60_PCT) * 15   # 离 MA60 越近越好
    s_entry = max(0.0, 1 - max(ext_low, 0.0) / MAX_EXT_FROM_LOW_PCT) * 15  # 越贴近最近低点越好
    # 量能健康度：回踩期缩量（近5日均量 / 前20日均量）在 0.6~1.5 之间最健康
    vr20 = float(np.mean(vol[-5:])) / max(float(np.mean(vol[-25:-5])), 1e-9)
    s_vol = 6.0 if 0.6 <= vr20 <= 1.5 else (3.0 if 0.45 <= vr20 <= 2.0 else 0.0)
    # 量价配合：上涨日均量 / 下跌日均量（≥1.2 满分，0.85 为下限、0 分）
    s_vp = max(0.0, min((vph["up_dn_vol"] - VPH_UP_DN_VOL_MIN) / 0.35, 1.0)) * 4.0
    # 相对强度：近 60 日涨幅 - 沪指近 60 日涨幅
    s_rs = 5.0
    if rs_base is not None and len(rs_base) >= 61:
        i_ret = rs_base[-1] / rs_base[-61] - 1
        s_ret = c[-1] / c[-61] - 1
        rs = (s_ret - i_ret) * 100
        s_rs = 5.0 + max(min(rs / 20.0, 1.0), -1.0) * 5.0
    score = s_lows + s_elev + s_slope + s_pos + s_entry + s_vol + s_vp + s_rs

    stop = lows_chain[-1] * 0.97
    target = px + 2.0 * (px - stop)
    return {
        "score": round(float(score), 1),
        "close": round(px, 2),
        "pct_chg": round(float((px / float(g["close"].iloc[-2]) - 1) * 100) if n >= 2 else 0.0, 2),
        "low_cnt": len(chain),
        "elev_pct": round(elev * 100, 1),
        "lows": [round(x, 2) for x in lows_chain],
        "first_low_date": str(g["trade_date"].iloc[_off + chain[0]]),
        "last_low_date": str(g["trade_date"].iloc[_off + chain[-1]]),
        "last_low": round(lows_chain[-1], 2),
        "last_low_age": last_age,
        "ma20": round(ma20_now, 2),
        "ma60": round(ma60_now, 2),
        "ma20_slope": round(ma20_slope, 1),
        "dist_ma60": round(dist_ma60, 1),
        "ext_low": round(ext_low, 1),
        "vol_ratio20": round(vr20, 2),
        "up_dn_vol": vph["up_dn_vol"],
        "vol_dryup": vph["vol_dryup"],
        "hh_pos": round(hh_pos * 100, 0),
        "stop_price": round(stop, 2),
        "target_price": round(target, 2),
    }


def _fmt_reason(r):
    return ("底部%d次抬高(%.2f→%.2f, %+.1f%%)｜MA20上行%+.1f%%｜距MA60 %+.1f%%｜"
            "距最近低点%+.1f%%｜量比%.2f｜涨跌量比%.2f" % (
                r["low_cnt"], r["lows"][0], r["lows"][-1], r["elev_pct"],
                r["ma20_slope"], r["dist_ma60"], r["ext_low"], r["vol_ratio20"],
                r["up_dn_vol"]))


# ── 主流程 ────────────────────────────────────────────────────────────────
def scan(trade_date=None, verbose=True):
    conn = sqlite3.connect(DB_PATH)
    try:
        if not trade_date:
            row = conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()
            trade_date = str(row[0]) if row and row[0] else datetime.now().strftime("%Y%m%d")
        df = load_bars(conn, trade_date)
        names = load_names(trade_date)
        rs_base = index_close(conn, trade_date)
    finally:
        conn.close()

    if df.empty:
        if verbose:
            print("[RB] %s 无行情数据" % trade_date)
        return trade_date, []

    hits = []
    for ts_code, g in df.groupby("ts_code", sort=False):
        if not ts_code.endswith((".SZ", ".SH")):
            continue
        if ts_code.startswith(("8", "43", "83", "87", "92")):
            continue  # 北交所
        name, industry = names.get(ts_code, ("", ""))
        if "ST" in str(name) or "退" in str(name):
            continue
        if str(g["trade_date"].iloc[-1]) != str(trade_date):
            continue  # 当日停牌
        try:
            r = evaluate(g, rs_base)
        except Exception:
            r = None
        if not r:
            continue
        r.update(ts_code=ts_code, stock_name=name, industry=industry)
        hits.append(r)

    hits.sort(key=lambda x: -x["score"])
    concepts = load_concepts()
    attach_themes(hits, concepts)
    if verbose:
        print("[RB] %s 命中 %d 只（主题覆盖 %d 只）" % (
            trade_date, len(hits), sum(1 for h in hits if h["theme"])))
        for i, h in enumerate(hits[:30], 1):
            print("  %2d. %s %-6s 综合%5.1f(技%5.1f+题%4.1f) 收%8.2f %+6.2f%% | %s | %s(%d家)"
                  % (i, h["ts_code"], h["stock_name"], h["total"], h["score"],
                     h["theme_bonus"], h["close"], h["pct_chg"], _fmt_reason(h),
                     h["theme"] or "-", h["theme_cnt"]))
    return trade_date, hits


def write_report(trade_date, hits):
    os.makedirs(REPORT_DIR, exist_ok=True)
    picks = [h for h in hits if _is_pick(h)]
    # 主题共振：今日命中池内同概念家数排行（家数越多越优先）
    theme_rank = {}
    for h in hits:
        for c in h.get("concepts", ()):
            theme_rank.setdefault(c, []).append(h)
    theme_rank = sorted(((c, lst) for c, lst in theme_rank.items() if len(lst) >= THEME_MIN_CNT),
                        key=lambda kv: (-len(kv[1]), -sum(x["total"] for x in kv[1])))
    path = os.path.join(REPORT_DIR, "rising_bottom_%s.md" % trade_date)
    lines = ["# RB 底部抬高（中长线）· %s" % trade_date, "",
             "形态：中长下跌见底后，回踩低点逐级抬高（≥%d 次、累计抬高 ≥%.0f%%），"
             "MA20 上行、短均线站上 MA60，买点落在抬高后的回踩位。命中 %d 只，优选 %d 只。"
             % (MIN_LOWS, MIN_ELEV_PCT, len(hits), len(picks)), "",
             "综合分 = 技术分 + 主题分（0~%.0f，今日命中池内同题材家数越多越高，"
             "%d 家拿满）。排序按综合分。" % (THEME_BONUS_MAX, THEME_FULL_CNT), ""]

    if theme_rank:
        lines += ["## 主题共振（同题材家数）", "",
                  "| 题材 | 命中家数 | 代表标的 | 平均综合分 |",
                  "|---|---|---|---|"]
        for name, lst in theme_rank[:15]:
            rep = "、".join(x["stock_name"] for x in lst[:6])
            lines.append("| %s | %d | %s | %.1f |"
                         % (name, len(lst), rep, sum(x["total"] for x in lst) / len(lst)))
        lines.append("")

    lines += ["## 命中明细", "",
              "| 代码 | 名称 | 收盘 | 当日 | 综合 | 技术 | 主题 | 同题材家数 | 低点数 | 底部抬高 "
              "| MA20斜率 | 距MA60 | 距最近低点 | 涨跌量比 | 最近低点日 | 止损 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for h in hits:
        lines.append("| %s | %s | %.2f | %+.2f%% | %.1f | %.1f | %s | %d | %d | %+.1f%% "
                     "| %+.1f%% | %+.1f%% | %+.1f%% | %.2f | %s | %.2f |"
                     % (h["ts_code"], h["stock_name"], h["close"], h["pct_chg"], h["total"],
                        h["score"], h["theme"] or "-", h["theme_cnt"], h["low_cnt"],
                        h["elev_pct"], h["ma20_slope"], h["dist_ma60"], h["ext_low"],
                        h["up_dn_vol"], h["last_low_date"], h["stop_price"]))
    lines += ["", "## 逐只要点", ""]
    for h in hits:
        lines.append("**%s（%s）** 综合 %.1f（技术 %.1f + 主题 %.1f，%s %d 家）：%s；"
                     "止损 %.2f，参考目标 %.2f"
                     % (h["stock_name"], h["ts_code"], h["total"], h["score"],
                        h["theme_bonus"], h["theme"] or "无共振题材", h["theme_cnt"],
                        _fmt_reason(h), h["stop_price"], h["target_price"]))
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _is_pick(h):
    """优选档：技术分达标，或「技术基本过关 + 同题材共振明显」者晋升。"""
    if h["score"] >= SCORE_PICK:
        return True
    return h["score"] >= SCORE_PICK_THEME and h.get("theme_bonus", 0.0) >= THEME_BONUS_PROMOTE


def record(trade_date, hits):
    """落库：仅写入技术分 >= 观察档的标的（strategy_id = rb_trend）。"""
    if _PICK_RECORD is None:
        return 0
    rows = []
    for i, h in enumerate(hits, 1):
        if h["score"] < SCORE_WATCH:
            continue
        rows.append({
            "ts_code": h["ts_code"], "stock_name": h["stock_name"], "close": h["close"],
            "pct_chg": h["pct_chg"], "score": h["total"], "rank_no": i,
            "industry": h.get("industry", ""), "concept": h.get("theme", ""),
            "signal": "RB_底部抬高", "action": "优选" if _is_pick(h) else "观察",
            "reason": _fmt_reason(h),
            "stop_price": h["stop_price"], "target_price": h["target_price"],
            "details": dict(
                {k: h[k] for k in ("low_cnt", "elev_pct", "lows", "last_low_date",
                                   "last_low", "ma20", "ma60", "ma20_slope", "dist_ma60",
                                   "ext_low", "vol_ratio20", "up_dn_vol", "vol_dryup")},
                tech_score=h["score"], theme=h.get("theme", ""),
                theme_cnt=h.get("theme_cnt", 0), theme_bonus=h.get("theme_bonus", 0.0)),
        })
    n = _PICK_RECORD(STRATEGY_ID, STRATEGY_NAME, rows, pick_date=trade_date, replace=True)
    if _UPSERT_STRATEGY is not None:
        # record_picks 内层 upsert 未带 category/horizon，这里补正为「中长线」
        _UPSERT_STRATEGY(STRATEGY_ID, STRATEGY_NAME, category="趋势形态", horizon="mid",
                         description="中长线：回踩低点逐级抬高 + MA20 上行，买在抬高后的回踩位；"
                                     "同题材命中家数越多综合分越高")
    return n


def _md_to_mail_html(md, title, subtitle):
    """Markdown 报告 → 邮件 HTML（与 daily_news_digest 同一排版口径）。"""
    import markdown2
    body = markdown2.markdown(md, extras=["tables"])
    css = """
body{margin:0;padding:0;background:#f5f6f8}
.wrap{max-width:980px;margin:0 auto;padding:18px 16px 32px;color:#1f2430;
  font-size:20px;line-height:1.7;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:26px;margin:6px 0 4px}
.sub{color:#6b7480;font-size:17px;margin:0 0 16px}
h2{font-size:22px;margin:24px 0 10px;padding-left:10px;border-left:5px solid #2f6fd0}
p{margin:10px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:15px}
th,td{border:1px solid #dfe3ea;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eef3fb;white-space:nowrap}
"""
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body>"
            "<div class=\"wrap\"><h1>%s</h1><div class=\"sub\">%s</div>%s</div></body></html>"
            % (title, css, title, subtitle, body))


def send_mail(md_path, trade_date, hits, picks):
    """把 RB 日报单独发一封邮件（agently-cli，正文 ≤1MB，失败只打日志不抛出）。"""
    if not os.path.exists(MAIL_CLI):
        print("[RB][邮件] 未找到 Agent Mail CLI: %s，跳过发送" % MAIL_CLI)
        return False
    try:
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
    except OSError as e:
        print("[RB][邮件] 读取报告失败: %s" % e)
        return False
    n_pick = len(picks)
    subject = "RB 底部抬高日报 - %s（命中 %d / 优选 %d）" % (trade_date, len(hits), n_pick)
    subtitle = ("中长线：回踩低点逐级抬高 + MA20 上行，买在抬高后的回踩位；"
                "综合分 = 技术分 + 主题分（今日命中池内同题材家数越多越高）")
    html = _md_to_mail_html(md, subject, subtitle)
    raw = html.encode("utf-8")
    if len(raw) > 1000 * 1024:
        html = raw[:1000 * 1024].decode("utf-8", "ignore") + "<p>（正文超过 1MB，已截断）</p>"
    body_path = os.path.join(REPORT_DIR, "_rb_mail_body.html")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(html)
    cmd = [MAIL_CLI, "message", "+send", "--to", MAIL_TO, "--subject", subject,
           "--body-file", os.path.basename(body_path), "--body-format", "html", "--confirmed"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=180, cwd=REPORT_DIR)
    except Exception as e:
        print("[RB][邮件] 调用异常: %s（正文留存 %s）" % (e, body_path))
        return False
    if r.returncode == 0 and '"ok": true' in (r.stdout or ""):
        print("[RB][邮件] 已发送 → %s｜%s" % (MAIL_TO, subject))
        try:
            os.remove(body_path)
        except OSError:
            pass
        return True
    detail = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")[:300]
    print("[RB][邮件] 发送失败 rc=%s %s（正文留存 %s）" % (r.returncode, detail, body_path))
    return False


def main():
    args = [a for a in sys.argv[1:] if a.strip()]
    no_mail = "--no-mail" in args
    args = [a for a in args if not a.startswith("--")]
    trade_date = args[0].strip() if args else None
    trade_date, hits = scan(trade_date)
    path = write_report(trade_date, hits)
    n = record(trade_date, hits)
    csv_path = os.path.join(REPORT_DIR, "rising_bottom_%s.csv" % trade_date)
    pd.DataFrame([{k: ((",".join(str(x) if isinstance(x, str) else "%.2f" % x for x in v))
                       if isinstance(v, list) else v)
                   for k, v in h.items()} for h in hits]).to_csv(
        csv_path, index=False, encoding="utf-8-sig")
    print("[RB] 报告 %s｜落库 %d 条｜明细 %s" % (path, n, csv_path))
    if no_mail:
        return
    if not hits:
        print("[RB][邮件] 当日无命中，跳过发送")
        return
    send_mail(path, trade_date, hits, [h for h in hits if _is_pick(h)])


if __name__ == "__main__":
    main()
