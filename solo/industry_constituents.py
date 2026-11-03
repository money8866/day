# -*- coding: utf-8 -*-
"""行业成分股与基本面（V10.7 STRUCTURAL_ALPHA_EXCEPTION 的数据基础）

=========================================
要解决的问题：
  V10.3 的主线判定只看**行业 ETF 自身走势**（相对强度 + 趋势），
  有两个结构性盲区：
    1. 一只权重股拉升就能把行业 ETF 拉起来 → 假行业强势；
    2. 看不到行业**内部**是否真的有赚钱效应（成分股是否普涨）。

  本模块用「行业 ETF 的股票持仓」把行业拆到成分股层：
    · 成分股广度   —— 行业内上涨家数占比（当日 / 20 日）
    · 权重集中度   —— 第一权重股权重 + 其贡献度
    · 剔除头部后的行业收益 —— 排除「单只权重股拉升」的虚假强势
    · 领涨股趋势   —— 领涨股是否站上 MA20 / 20 日收益
    · 成分股基本面 —— 最新已公告财报的净利同比 / ROE 中位数

数据来源：
  · 行业 ETF 持仓：tushare fund_portfolio（含 stk_mkv_ratio 权重）
      → 落库 solo/cache_industry/industry_holdings.db
  · 成分股行情：  cache_daily/stock_data.db → daily_cache
  · 成分股基本面：cache_daily/stock_data.db → fina_indicator_cache

★ 无未来数据保证：
  · 持仓只取 ann_date <= trade_date 的最新一期；
  · 财报只取 ann_date <= trade_date 的最新一期；
  · 行情只取 trade_date <= 当日。
"""
import os
import sys
import json
import sqlite3
import datetime
from typing import Optional, List, Dict, Any, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

CACHE_DIR = r"D:\mystock\cache_daily"
STOCK_DB = os.path.join(CACHE_DIR, "stock_data.db")
IND_DB = os.path.join(BASE_DIR, "cache_industry", "industry_holdings.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_holdings (
    ts_code   TEXT,
    ann_date  TEXT,
    end_date  TEXT,
    con_code  TEXT,
    weight    REAL,
    PRIMARY KEY (ts_code, ann_date, con_code)
);
CREATE INDEX IF NOT EXISTS idx_holdings_ann ON etf_holdings(ts_code, ann_date);

CREATE TABLE IF NOT EXISTS sw_industry (
    index_code   TEXT PRIMARY KEY,
    industry_name TEXT,
    level        TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS sw_member (
    index_code TEXT,
    con_code   TEXT,
    in_date    TEXT,
    out_date   TEXT,
    PRIMARY KEY (index_code, con_code, in_date)
);
CREATE INDEX IF NOT EXISTS idx_sw_member ON sw_member(index_code, con_code);
"""


# ============================================================
# 配置
# ============================================================
DEFAULT_CONFIG = {
    "lookback_days": 60,          # 计算成分股 MA / 收益的回看窗口
    "min_constituents": 20,       # 成分股样本不足 → 广度 UNKNOWN
    "breadth_up_ratio_min": 0.50,  # 行业内部上涨家数占比下限
    "breadth_up20_ratio_min": 0.55,  # 20 日上涨家数占比下限
    "top_weight_max": 0.20,      # 第一权重股权重上限（超过需额外检验）
    "ex_top1_ret_min": 0.0,      # 剔除第一权重股后，成分股加权 20 日收益下限
    "leader_above_ma20": True,   # 领涨股需站上 MA20
    "fundamental_min_coverage": 0.5,  # 基本面数据覆盖率下限
    "fundamental_netprofit_yoy_min": 0.0,   # 成分股净利同比中位数下限
    "fundamental_roe_min": 0.0,  # 成分股 ROE 中位数下限
}


# ★ ETF 池与申万一级行业的名称别名
#   ETF 池把「建筑材料 + 建筑装饰」合并为「建筑建材」（两者成分高度重叠），
#   申万一级则是两个独立行业 → 广度统计时展开为两个行业合并。
SW_ALIAS = {
    "建筑建材": ["建筑材料", "建筑装饰"],
}


def load_config(path: str = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = path or os.path.join(BASE_DIR, "config_v107_refine.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in (user.get("structural_alpha") or {}).items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    return cfg


# ============================================================
# 数据库
# ============================================================
def _ro_conn():
    if not os.path.exists(STOCK_DB):
        return None
    try:
        return sqlite3.connect(f"file:{STOCK_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=30)
    except Exception:
        return None


def _write_conn():
    os.makedirs(os.path.dirname(IND_DB), exist_ok=True)
    conn = sqlite3.connect(IND_DB, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ============================================================
# 持仓抓取（tushare fund_portfolio）
# ============================================================
def fetch_holdings(codes: List[str], refresh: bool = False,
                   progress: bool = False) -> int:
    """抓取 ETF 持仓并落库。返回写入的持仓条目数。"""
    import etf_mainline as em
    codes = list(dict.fromkeys(codes))
    if not codes:
        return 0

    wconn = _write_conn()
    try:
        have = set()
        if not refresh:
            have = {r[0] for r in wconn.execute(
                "SELECT DISTINCT ts_code FROM etf_holdings").fetchall()}

        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(BASE_DIR), 'config', '.env'))
        import tushare as ts
        ts.set_token(os.getenv('TUSHARE_TOKEN'))
        pro = ts.pro_api()

        total = 0
        import time
        for i, code in enumerate(codes, 1):
            if code in have:
                continue
            df = None
            for attempt in range(3):
                try:
                    df = pro.fund_portfolio(ts_code=code)
                    break
                except Exception as e:
                    print(f"  {code} 持仓抓取失败(第{attempt+1}次): {e}", flush=True)
                    time.sleep(3 + 3 * attempt)
            if df is None or len(df) == 0:
                continue
            d = df.copy()
            d['ann_date'] = d['ann_date'].astype(str)
            d['end_date'] = d['end_date'].astype(str)
            # 只保留最新 4 期，控制体积
            keep_ann = sorted(d['ann_date'].unique(), reverse=True)[:4]
            d = d[d['ann_date'].isin(keep_ann)]
            rows = []
            for r in d.itertuples(index=False):
                w = r.stk_mkv_ratio
                try:
                    w = float(w) if w is not None else None
                except Exception:
                    w = None
                rows.append((code, str(r.ann_date), str(r.end_date),
                             str(r.symbol), w))
            if rows:
                wconn.executemany(
                    "INSERT OR REPLACE INTO etf_holdings "
                    "(ts_code, ann_date, end_date, con_code, weight) VALUES (?,?,?,?,?)",
                    rows)
                wconn.commit()
                total += len(rows)
            if progress:
                print(f"  [{i}/{len(codes)}] {code} 累计 {total} 条")
        return total
    finally:
        try:
            wconn.close()
        except Exception:
            pass


# ============================================================
# 申万一级行业成分（tushare index_classify / index_member）
# ============================================================
def fetch_sw_industries(refresh: bool = False, progress: bool = False) -> int:
    """抓取申万一级行业分类与成分股。返回写入的成分股条目数。

    ★ SW L1 的行业名与 etf_mainline.ETF_POOL 的行业名一致（均为申万一级），
      因此可以一一对应。
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(BASE_DIR), 'config', '.env'))
    import tushare as ts
    ts.set_token(os.getenv('TUSHARE_TOKEN'))
    pro = ts.pro_api()

    wconn = _write_conn()
    total = 0
    try:
        have = set()
        if not refresh:
            have = {r[0] for r in wconn.execute(
                "SELECT index_code FROM sw_industry").fetchall()}

        try:
            cls = pro.index_classify(level='L1', src='SW2021')
        except Exception as e:
            print(f"index_classify 失败: {e}")
            return 0
        if cls is None or len(cls) == 0:
            return 0
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        wconn.executemany(
            "INSERT OR REPLACE INTO sw_industry "
            "(index_code, industry_name, level, updated_at) VALUES (?,?,?,?)",
            [(str(r.index_code), str(r.industry_name), str(r.level), now)
             for r in cls.itertuples(index=False)])
        wconn.commit()

        import time
        for i, r in enumerate(cls.itertuples(index=False), 1):
            code = str(r.index_code)
            if code in have and not refresh:
                continue
            try:
                m = pro.index_member(index_code=code)
            except Exception as e:
                print(f"  {code} 成分抓取失败: {e}", flush=True)
                time.sleep(2)
                continue
            if m is None or len(m) == 0:
                continue
            rows = []
            for mm in m.itertuples(index=False):
                out_d = mm.out_date
                out_s = None if (out_d is None or str(out_d) == 'None'
                                 or str(out_d) == 'nan') else str(out_d)
                rows.append((code, str(mm.con_code),
                             str(mm.in_date) if mm.in_date else None, out_s))
            wconn.executemany(
                "INSERT OR REPLACE INTO sw_member "
                "(index_code, con_code, in_date, out_date) VALUES (?,?,?,?)", rows)
            wconn.commit()
            total += len(rows)
            if progress:
                print(f"  [{i}/{len(cls)}] {code} {r.industry_name} → 累计 {total} 条")
        return total
    finally:
        try:
            wconn.close()
        except Exception:
            pass


def sw_industry_map() -> Dict[str, str]:
    """行业名 → 申万 L1 指数代码。"""
    if not os.path.exists(IND_DB):
        return {}
    try:
        conn = sqlite3.connect(f"file:{IND_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=20)
    except Exception:
        return {}
    try:
        rows = conn.execute(
            "SELECT industry_name, index_code FROM sw_industry").fetchall()
        return {str(n): str(c) for n, c in rows}
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sw_members(industry: str, as_of: str) -> List[str]:
    """取某行业在 as_of 时点**在册**的成分股（申万 L1 全量）。

    ★ 只用 in_date <= as_of 且 out_date 为空或 > as_of 的成分 —— 无未来数据。
    """
    if not os.path.exists(IND_DB):
        return []
    imap = sw_industry_map()
    # ★ ETF 池把「建筑材料 + 建筑装饰」合并为「建筑建材」，此处做别名展开
    names = [industry]
    if industry in SW_ALIAS:
        names = list(SW_ALIAS[industry])
    idxs = [imap[n] for n in names if n in imap]
    if not idxs:
        return []
    try:
        conn = sqlite3.connect(f"file:{IND_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=20)
    except Exception:
        return []
    try:
        ph = ",".join("?" * len(idxs))
        rows = conn.execute(
            f"SELECT con_code, in_date, out_date FROM sw_member "
            f"WHERE index_code IN ({ph})", idxs).fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = []
    for con, in_d, out_d in rows:
        if in_d and str(in_d) > str(as_of):
            continue
        if out_d and str(out_d) <= str(as_of):
            continue
        out.append(str(con))
    return sorted(set(out))


# ============================================================
# 读取持仓
# ============================================================
def get_holdings(ts_code: str, as_of: str) -> List[Dict[str, Any]]:
    """取某 ETF 在 as_of（含）时点可得的**最新一期**持仓。

    ★ 只用 ann_date <= as_of 的数据 —— 不使用未来数据。
    """
    if not os.path.exists(IND_DB):
        return []
    try:
        conn = sqlite3.connect(f"file:{IND_DB.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=20)
    except Exception:
        return []
    try:
        row = conn.execute(
            "SELECT MAX(ann_date) FROM etf_holdings WHERE ts_code = ? AND ann_date <= ?",
            (ts_code, str(as_of))).fetchone()
        if not row or not row[0]:
            return []
        rows = conn.execute(
            "SELECT con_code, weight, ann_date, end_date FROM etf_holdings "
            "WHERE ts_code = ? AND ann_date = ?", (ts_code, row[0])).fetchall()
        out = []
        for con, w, ann, end in rows:
            out.append({"con_code": con, "weight": w, "ann_date": ann,
                        "end_date": end})
        return out
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def industry_constituents(industry: str, as_of: str,
                          etf_pool: dict = None) -> Tuple[List[str], Dict[str, Any]]:
    """取某行业在 as_of 时点的成分股集合与权重信息。

    返回 (成分股代码列表, 附加信息)
      附加信息：
        etf_code       —— 使用的代表 ETF（持仓最多/权重最新）
        weights        —— {con_code: weight}
        ann_date       —— 持仓公告日
        coverage       —— 有持仓数据的 ETF 数 / 该行业 ETF 总数
    """
    if etf_pool is None:
        import etf_mainline as em
        etf_pool = em.ETF_POOL
    codes = etf_pool.get(industry) or []
    if not codes:
        return [], {"etf_code": None, "weights": {}, "ann_date": None,
                    "coverage": "0/0"}

    best = None
    weights: Dict[str, float] = {}
    ann_date = None
    n_ok = 0
    for c in codes:
        hs = get_holdings(c, as_of)
        if not hs:
            continue
        n_ok += 1
        w = {h["con_code"]: (h["weight"] or 0.0) for h in hs}
        a = max(h["ann_date"] for h in hs)
        if best is None or len(hs) > len(best[1]):
            best = (c, hs)
        if ann_date is None or a > ann_date:
            ann_date = a
        for k, v in w.items():
            weights[k] = max(weights.get(k, 0.0), v)

    if best is None:
        return [], {"etf_code": None, "weights": {}, "ann_date": None,
                    "coverage": f"{n_ok}/{len(codes)}"}

    etf_code = best[0]
    etf_weights = {h["con_code"]: (h["weight"] or 0.0) for h in best[1]}
    return list(etf_weights.keys()), {
        "etf_code": etf_code,
        "weights": etf_weights,
        "ann_date": ann_date,
        "coverage": f"{n_ok}/{len(codes)}",
        "industry_weights": weights,
    }


# ============================================================
# 成分股行情
# ============================================================
def _constituent_quotes(codes: List[str], end_date: str,
                        lookback: int) -> Optional[pd.DataFrame]:
    """取成分股在截至 end_date 的 lookback 天行情。"""
    conn = _ro_conn()
    if conn is None or not codes:
        return None
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?", (str(end_date), int(lookback)))]
        if not dates:
            return None
        lo = dates[-1]
        ph = ",".join("?" * len(codes))
        df = pd.read_sql_query(
            f"SELECT ts_code, trade_date, close, pct_chg FROM daily_cache "
            f"WHERE trade_date BETWEEN ? AND ? AND ts_code IN ({ph})",
            conn, params=[lo, str(end_date)] + list(codes))
        return df if not df.empty else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fundamentals(codes: List[str], as_of: str) -> Dict[str, Dict[str, Any]]:
    """取成分股在 as_of 时点最新已公告的财务指标。"""
    conn = _ro_conn()
    if conn is None or not codes:
        return {}
    try:
        ph = ",".join("?" * len(codes))
        df = pd.read_sql_query(
            f"SELECT f.ts_code, f.ann_date, f.end_date, f.netprofit_yoy, f.or_yoy, "
            f"f.roe, f.grossprofit_margin FROM fina_indicator_cache f "
            f"JOIN (SELECT ts_code, MAX(ann_date) AS md FROM fina_indicator_cache "
            f"      WHERE ann_date <= ? GROUP BY ts_code) m "
            f"  ON f.ts_code = m.ts_code AND f.ann_date = m.md "
            f"WHERE f.ts_code IN ({ph})",
            conn, params=[str(as_of)] + list(codes))
        if df.empty:
            return {}
        out = {}
        for r in df.itertuples(index=False):
            out[str(r.ts_code)] = {
                "ann_date": str(r.ann_date), "end_date": str(r.end_date),
                "netprofit_yoy": r.netprofit_yoy, "or_yoy": r.or_yoy,
                "roe": r.roe, "grossprofit_margin": r.grossprofit_margin,
            }
        return out
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# 行业内部结构分析
# ============================================================
def analyze_industry(industry: str, trade_date: str, cfg: dict = None,
                     etf_pool: dict = None) -> Dict[str, Any]:
    """分析某行业在 trade_date 的内部结构（广度 / 权重集中度 / 基本面）。

    ★ 两套成分口径，各司其职：
      · 申万 L1 全量成分（index_member）→ 行业**内部广度**（上涨家数占比）
        （ETF 季报只披露前十大持仓，样本仅 10~15 只，不足以衡量行业广度）
      · 行业 ETF 披露持仓 + 权重（fund_portfolio）→ **权重集中度**，
        用于识别「单只权重股拉升造成的虚假行业强势」

    返回 dict，缺失项一律为 None（= UNKNOWN），绝不默认通过。
    """
    cfg = cfg or load_config()
    out: Dict[str, Any] = {
        "industry": industry,
        "trade_date": str(trade_date),
        "constituent_count": 0,
        "member_source": None,
        "etf_code": None,
        "holdings_ann_date": None,
        "holdings_coverage": None,
        "breadth_up_ratio": None,        # 当日上涨家数占比（全量成分）
        "breadth_up20_ratio": None,      # 20 日上涨家数占比（全量成分）
        "ex_best_breadth20": None,       # 剔除表现最好的 1 只成分后的 20 日广度
        "weighted_ret20": None,          # ETF 披露持仓加权 20 日收益
        "ex_top1_ret20": None,           # 剔除第一权重股后的加权 20 日收益
        "top_weight": None,              # 第一权重股权重
        "top_weight_ret20": None,        # 第一权重股 20 日收益
        "top_weight_code": None,
        "leader_code": None,             # 领涨股（20日收益最高且权重不低于中位者）
        "leader_ret20": None,
        "leader_above_ma20": None,
        "fundamental_coverage": None,    # 基本面数据覆盖率
        "median_netprofit_yoy": None,
        "median_roe": None,
        "notes": [],
    }

    # ---- 申万 L1 全量成分（广度口径）----
    members = sw_members(industry, trade_date)
    out["member_source"] = "SW2021_L1" if members else None
    out["constituent_count"] = len(members)

    # ---- ETF 披露持仓（权重口径）----
    codes_top, meta = industry_constituents(industry, trade_date, etf_pool)
    out["etf_code"] = meta.get("etf_code")
    out["holdings_ann_date"] = meta.get("ann_date")
    out["holdings_coverage"] = meta.get("coverage")
    weights = meta.get("weights") or {}

    universe = sorted(set(members) | set(codes_top))
    if not universe:
        out["notes"].append("行业成分股数据不可用（申万成分与 ETF 持仓均为空）")
        return out

    q = _constituent_quotes(universe, trade_date, cfg.get("lookback_days", 60))
    if q is None or q.empty:
        out["notes"].append("成分股行情不可用")
        return out

    q = q.sort_values(["ts_code", "trade_date"])
    last_date = q['trade_date'].iloc[-1]
    if str(last_date) != str(trade_date):
        out["notes"].append(
            f"成分股行情末行 {last_date} ≠ 目标 {trade_date}（当日数据未就绪）")

    # ---- 20 日收益（按成分股）----
    rets: Dict[str, float] = {}
    for code, g in q.groupby("ts_code"):
        g = g.sort_values("trade_date")
        if len(g) < 21:
            continue
        c = g['close'].astype(float)
        if c.iloc[-21] and c.iloc[-21] > 0:
            rets[str(code)] = float((c.iloc[-1] / c.iloc[-21] - 1) * 100)

    mem_set = set(members)

    # ---- 当日广度（全量成分）----
    today = q[(q['trade_date'] == last_date) & (q['ts_code'].isin(mem_set))]
    if len(today):
        valid = int(today['pct_chg'].notna().sum())
        if valid:
            out["breadth_up_ratio"] = round(
                int((today['pct_chg'] > 0).sum()) / valid, 4)

    # ---- 20 日广度（全量成分）----
    mem_rets = {c: r for c, r in rets.items() if c in mem_set}
    if mem_rets:
        pos = sum(1 for v in mem_rets.values() if v > 0)
        out["breadth_up20_ratio"] = round(pos / len(mem_rets), 4)
        # 剔除表现最好的 1 只成分后的广度（识别「单只股票拉动」）
        if len(mem_rets) >= 5:
            best_code = max(mem_rets, key=lambda c: mem_rets[c])
            rest = {c: r for c, r in mem_rets.items() if c != best_code}
            if rest:
                out["ex_best_breadth20"] = round(
                    sum(1 for v in rest.values() if v > 0) / len(rest), 4)
                out["ex_best_code"] = best_code
                out["ex_best_ret20"] = round(mem_rets[best_code], 3)

    # ---- 加权收益 / 剔除第一权重股（ETF 披露持仓口径）----
    if rets and weights:
        pairs = [(c, r, weights.get(c, 0.0)) for c, r in rets.items()
                 if c in weights]
        if pairs:
            tw = sum(w for _, _, w in pairs)
            if tw > 0:
                out["weighted_ret20"] = round(
                    sum(r * w for _, r, w in pairs) / tw, 3)
                top = max(pairs, key=lambda x: x[2])
                out["top_weight"] = round(top[2] / 100.0, 4)
                out["top_weight_ret20"] = round(top[1], 3)
                out["top_weight_code"] = top[0]
                rest = [(c, r, w) for c, r, w in pairs if c != top[0]]
                rw = sum(w for _, _, w in rest)
                if rw > 0:
                    out["ex_top1_ret20"] = round(
                        sum(r * w for _, r, w in rest) / rw, 3)

    # ---- 领涨股趋势 ----
    if rets and weights:
        ws = sorted(weights.values())
        med_w = ws[len(ws) // 2] if ws else 0.0
        cands = [c for c, r in rets.items() if weights.get(c, 0.0) >= med_w]
        if not cands:
            cands = list(rets.keys())
        if cands:
            leader = max(cands, key=lambda c: rets[c])
            out["leader_code"] = leader
            out["leader_ret20"] = round(rets[leader], 3)
            sub = q[q['ts_code'] == leader].sort_values("trade_date")
            c = sub['close'].astype(float)
            if len(c) >= 20:
                ma20 = float(c.tail(20).mean())
                out["leader_above_ma20"] = bool(float(c.iloc[-1]) > ma20)

    # ---- 基本面 ----
    fins = _fundamentals(universe, trade_date)
    if fins:
        cov = len(fins) / max(1, len(universe))
        out["fundamental_coverage"] = round(cov, 4)
        np_yoy = [v["netprofit_yoy"] for v in fins.values()
                  if v.get("netprofit_yoy") is not None]
        roe = [v["roe"] for v in fins.values() if v.get("roe") is not None]
        if np_yoy:
            out["median_netprofit_yoy"] = round(float(pd.Series(np_yoy).median()), 3)
        if roe:
            out["median_roe"] = round(float(pd.Series(roe).median()), 3)
    else:
        out["notes"].append("成分股基本面数据不可用")

    return out


def prefetch_all(trade_date: str, industries: List[str] = None,
                 progress: bool = False) -> int:
    """预先抓取候选行业的持仓（省去后续逐个调 tushare 的等待）。"""
    import etf_mainline as em
    inds = industries if industries else list(em.ETF_POOL.keys())
    codes = []
    for ind in inds:
        codes.extend(em.ETF_POOL.get(ind) or [])
    codes = sorted(set(codes))
    if progress:
        print(f"抓取 {len(codes)} 只行业 ETF 持仓…")
    return fetch_holdings(codes, progress=progress)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="行业成分股与基本面")
    ap.add_argument("--fetch", action="store_true", help="抓取 ETF 持仓")
    ap.add_argument("--industry", help="分析指定行业")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    if args.fetch:
        n = prefetch_all(args.date or datetime.date.today().strftime('%Y%m%d'),
                         progress=True)
        print(f"写入 {n} 条持仓")
    if args.industry:
        td = args.date or datetime.date.today().strftime('%Y%m%d')
        r = analyze_industry(args.industry, td)
        print(json.dumps(r, ensure_ascii=False, indent=2))
