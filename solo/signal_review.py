# -*- coding: utf-8 -*-
"""选股信号二次复核（技术面 × 基本面）→ HTML 报告 + 邮件推送
================================================
定位：在 stock_pick_db.py tracking 回填之后运行，读取「本日新增选股信号」，
用本地缓存库的技术面 / 基本面数据做二次筛选，输出分级清单并邮件推送。

数据源（全程只读，不写任何库）：
  选股库  picks_db/stock_picks.db
          view v_pick_full（stock_pick + strategy + pick_tracking）
  行情库  D:\\mystock\\cache_daily\\stock_data.db
          daily_cache           OHLCV / vol / pct_chg（均线、量能、ATR）
          daily_basic_cache     换手率、量比、总市值、PE_TTM、PB、股息率
          fina_indicator_cache  ROE、营收/净利同比、毛利率、现金流比、负债率
  tushare_quant 复盘  D:\\mystock\\report_daily\\Final_Self_{date}.md
          （tushare_quant.py 当日产出，原文照录为报告末段；无产出则跳过）

打分口径：
  技术面 100 = 趋势结构30 + 量价配合25 + 位置空间20 + 动能15 + 风险健康10
  基本面 100 = ROE30 + 现金流质量20 + 营收增速20 + 净利增速15 + 毛利率10 + 负债率5
                （财务数据按 ann_date ≤ 决策日 as-of 取最近一期，缺失记中性 50 并标注）
  综合分 = 0.65 × 技术面 + 0.35 × 基本面（基本面缺失按 50 计）
  分级：优选(综合≥70 且 技术≥65 且 基本面≥50 且 无硬性风险 且 总市值≥80亿)
        观察(综合≥60 且 技术≥55 且 无硬性风险)
        剔除(其余，含触发硬性风险的标的)

产出：
  report_daily/signal_review_{date}.md    报告正文
  report_daily/signal_review_{date}.html  邮件正文（移动端优先 22px）
  邮件推送至 MAIL_TO（同项目其它模块共用 agently-cli 通道）

用法：
  python signal_review.py                    # 最近一交易日信号 → 报告 + 邮件
  python signal_review.py --date 20260930
  python signal_review.py --no-mail          # 只生成报告，不推送
  python signal_review.py --dry-run          # 不发邮件，保留 HTML 供人工检查
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PICK_DB = os.environ.get('PICK_DB_PATH', os.path.join(BASE_DIR, 'picks_db', 'stock_picks.db'))
MARKET_DB = os.environ.get('PICK_MARKET_DB', r'D:\mystock\cache_daily\stock_data.db')
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
TQ_REPORT_DIR = os.environ.get('TQ_REPORT_DIR', r'D:\mystock\report_daily')  # tushare_quant.py 产出目录

MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = os.environ.get('SIGNAL_REVIEW_MAIL_TO', 'stock1975@qq.com')

# ── 复核口径参数（改这里即可调档，无需动逻辑） ──
W_TECH = 0.65                 # 综合分中技术面权重
W_FUND = 0.35                 # 综合分中基本面权重
FUND_NEUTRAL = 50.0           # 财务数据缺失时的中性分
MV_MIN_YI = 80.0              # 优选要求：总市值 ≥ 80 亿
MV_HARD_YI = 50.0             # 硬性线：总市值 < 50 亿直接剔除
BIAS20_MAX = 25.0             # 硬性线：MA20 乖离率 > 25% 视为严重超买
DEBT_MAX = 85.0               # 硬性线：资产负债率 > 85%（非金融）剔除
T1_MIN, T2_MIN = 70.0, 60.0   # 优选 / 观察 综合分门槛
T1_TECH, T2_TECH = 65.0, 55.0  # 优选 / 观察 技术分门槛
T1_FUND = 50.0                # 优选基本面分门槛
BARS_LOOKBACK_DAYS = 260      # 取多少自然日行情用于算指标
ATR_N = 14

FINANCE_INDUSTRIES = ('银行', '保险', '证券', '多元金融', '信托')


# ──────────────────────────── 数据读取 ────────────────────────────

def resolve_trade_date(conn, want: str | None) -> tuple[str, bool]:
    """返回 (交易日, 是否为回退到最近日期)。默认取当日，当日无信号则回退最近有信号的一天。"""
    if want:
        return want, False
    today = datetime.now().strftime('%Y%m%d')
    has = conn.execute("SELECT 1 FROM stock_pick WHERE pick_date=? LIMIT 1", (today,)).fetchone()
    if has:
        return today, False
    row = conn.execute("SELECT MAX(pick_date) FROM stock_pick").fetchone()
    if not row or not row[0]:
        raise SystemExit('选股库中没有任何选股记录')
    return row[0], True


def load_signals(trade_date: str) -> list[dict]:
    """本日新增信号，按股票代码去重（同股多策略合并，主信号取评分最高的一条）。"""
    conn = sqlite3.connect(PICK_DB, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT strategy_id, strategy_name, ts_code, stock_name, close, pct_chg,
                   signal, action, score, rank_no, industry, reason,
                   stop_price, target_price
            FROM v_pick_full WHERE pick_date = ?
        """, (trade_date,)).fetchall()
    finally:
        conn.close()

    merged: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        cur = merged.get(d['ts_code'])
        if cur is None:
            d['strategies'] = [(d['strategy_name'] or d['strategy_id'], d['signal'] or '',
                                d['score'] or 0)]
            merged[d['ts_code']] = d
            continue
        cur['strategies'].append((d['strategy_name'] or d['strategy_id'], d['signal'] or '',
                                  d['score'] or 0))
        # 主信号取评分最高的那条记录
        if (d['score'] or 0) > (cur['score'] or 0):
            keep = cur['strategies']
            d['strategies'] = keep
            merged[d['ts_code']] = d
    out = list(merged.values())
    for d in out:
        d['strategies'].sort(key=lambda t: -t[2])
    return sorted(out, key=lambda d: -(d['score'] or 0))


def load_bars(codes: list[str], end_date: str) -> dict[str, pd.DataFrame]:
    if not codes or not os.path.exists(MARKET_DB):
        return {}
    start = (datetime.strptime(end_date, '%Y%m%d')
             - timedelta(days=BARS_LOOKBACK_DAYS)).strftime('%Y%m%d')
    ph = ','.join('?' * len(codes))
    conn = sqlite3.connect(MARKET_DB, timeout=30.0)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg "
            "FROM daily_cache WHERE ts_code IN (%s) AND trade_date <= ? AND trade_date >= ? "
            "AND close > 0 ORDER BY ts_code, trade_date" % ph,
            conn, params=codes + [end_date, start])
    finally:
        conn.close()
    return {c: g.reset_index(drop=True) for c, g in df.groupby('ts_code')}


def load_basic(codes: list[str], trade_date: str) -> dict[str, dict]:
    """估值/流动性快照：优先决策日，缺失则回退该日之前最近一期。"""
    if not codes or not os.path.exists(MARKET_DB):
        return {}
    ph = ','.join('?' * len(codes))
    conn = sqlite3.connect(MARKET_DB, timeout=30.0)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, turnover_rate, volume_ratio, total_mv, circ_mv, "
            "pe_ttm, pb, dv_ttm FROM daily_basic_cache "
            "WHERE ts_code IN (%s) AND trade_date <= ?" % ph,
            conn, params=codes + [trade_date])
    finally:
        conn.close()
    if df.empty:
        return {}
    df = df.sort_values(['ts_code', 'trade_date'])
    last = df.groupby('ts_code').tail(1)
    return {r['ts_code']: r.to_dict() for _, r in last.iterrows()}


def load_fina(codes: list[str], trade_date: str) -> dict[str, dict]:
    """财务面：as-of 决策日（ann_date ≤ 决策日）取最近报告期。"""
    if not codes or not os.path.exists(MARKET_DB):
        return {}
    ph = ','.join('?' * len(codes))
    conn = sqlite3.connect(MARKET_DB, timeout=30.0)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, end_date, ann_date, roe, netprofit_yoy, or_yoy, "
            "grossprofit_margin, ocf_to_or, debt_to_assets FROM fina_indicator_cache "
            "WHERE ts_code IN (%s) AND COALESCE(ann_date,'') <= ?" % ph,
            conn, params=codes + [trade_date])
    finally:
        conn.close()
    if df.empty:
        return {}
    df = df.sort_values(['ts_code', 'end_date'])
    last = df.groupby('ts_code').tail(1)
    return {r['ts_code']: r.to_dict() for _, r in last.iterrows()}


def load_tq_review(trade_date: str) -> tuple[str, str] | None:
    """读取 tushare_quant.py 当日复盘产出 Final_Self_{date}.md。

    返回 (来源文件名, 正文)；无产出返回 None（fail-soft，不影响复核报告主体）。
    """
    path = os.path.join(TQ_REPORT_DIR, 'Final_Self_%s.md' % trade_date)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read().strip()
    except OSError:
        return None
    return (os.path.basename(path), text) if text else None


# ──────────────────────────── 指标计算 ────────────────────────────

def _num(v):
    try:
        if v is None or v == '' or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _band(v, bands, default=0.0):
    """bands = [(下限, 分值)] 按上限升序给出；返回第一个满足 v < 上限 的分值。"""
    if v is None:
        return default
    for limit, pts in bands:
        if v < limit:
            return pts
    return bands[-1][1]


def tech_metrics(bars: pd.DataFrame | None, basic: dict | None) -> dict:
    """技术面指标 + 打分（0~100）。数据不足返回 None 分数。"""
    m = {'ok': False, 'score': None}
    if bars is None or len(bars) < 30:
        return m
    close = bars['close'].astype(float).values
    high = bars['high'].astype(float).values
    low = bars['low'].astype(float).values
    vol = bars['vol'].astype(float).values
    c = close[-1]

    def ma(n):
        return float(close[-n:].mean()) if len(close) >= n else None

    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    ma20_prev = float(close[-25:-5].mean()) if len(close) >= 25 else None
    ma60_prev = float(close[-65:-5].mean()) if len(close) >= 65 else None
    win = min(60, len(close))
    hi60, lo60 = float(high[-win:].max()), float(low[-win:].min())

    r5 = (c / close[-6] - 1) * 100 if len(close) >= 6 else None
    r20 = (c / close[-21] - 1) * 100 if len(close) >= 21 else None
    vol5 = float(vol[-6:-1].mean()) if len(vol) >= 6 and vol[-6:-1].mean() > 0 else None
    vr5 = (vol[-1] / vol5) if vol5 else None
    bias20 = (c / ma20 - 1) * 100 if ma20 else None
    pos60 = (c - lo60) / (hi60 - lo60) * 100 if hi60 > lo60 else None
    dd60 = (hi60 - c) / hi60 * 100 if hi60 > 0 else None
    tr = []
    for i in range(max(1, len(close) - ATR_N), len(close)):
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    atr_pct = (sum(tr) / len(tr) / c * 100) if tr and c else None
    turnover = _num((basic or {}).get('turnover_rate'))

    # A 趋势结构 30
    s_trend = 0.0
    if ma5 is not None:
        s_trend += 5 if c > ma5 else 0
    if ma5 is not None and ma10 is not None:
        s_trend += 5 if ma5 > ma10 else 0
    if ma10 is not None and ma20 is not None:
        s_trend += 5 if ma10 > ma20 else 0
    if ma20 and ma20_prev:
        s_trend += 8 if ma20 > ma20_prev else 0
    if ma60 and ma60_prev:
        s_trend += 4 if ma60 > ma60_prev else 0
    if ma60 is not None:
        s_trend += 3 if c > ma60 else 0

    # B 量价配合 25
    s_vol = _band(vr5, [(0.7, 6), (1.0, 11), (3.0, 15), (5.0, 9), (999, 6)], default=8)
    s_vol += _band(turnover, [(1.0, 4), (2.0, 7), (15.0, 10), (25.0, 7), (999, 4)], default=6)

    # C 位置与空间 20
    s_pos = _band(pos60, [(30.0, 3), (45.0, 6), (60.0, 9), (95.0, 12), (100.1, 9)], default=6)
    s_pos += _band(dd60, [(5.0, 8), (12.0, 6), (20.0, 4), (999, 2)], default=4)

    # D 动能 15
    s_mom = _band(r20, [(0.0, 3), (5.0, 7), (40.0, 10), (60.0, 7), (999, 3)], default=6)
    s_mom += _band(r5, [(-5.0, 3), (15.0, 5), (999, 3)], default=3)

    # E 风险健康 10
    s_risk = _band(bias20, [(10.0, 6), (18.0, 4), (25.0, 2), (999, 0)], default=4)
    s_risk += _band(atr_pct, [(4.0, 4), (7.0, 3), (10.0, 2), (999, 1)], default=2)

    m.update({
        'ok': True,
        'score': round(s_trend + s_vol + s_pos + s_mom + s_risk, 1),
        'close': c, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'r5': r5, 'r20': r20, 'vr5': vr5, 'bias20': bias20, 'pos60': pos60,
        'dd60': dd60, 'atr_pct': atr_pct, 'turnover': turnover,
        'above_ma20': bool(ma20 and c > ma20),
        'ma20_up': bool(ma20 and ma20_prev and ma20 > ma20_prev),
        'ma60_up': bool(ma60 and ma60_prev and ma60 > ma60_prev),
        'break_60d': bool(c >= hi60 * 0.999),
    })
    return m


def fund_metrics(fina: dict | None) -> dict:
    """基本面指标 + 打分（0~100）。无财务数据 → score=None。"""
    if not fina:
        return {'ok': False, 'score': None}
    roe = _num(fina.get('roe'))
    ocf = _num(fina.get('ocf_to_or'))
    or_yoy = _num(fina.get('or_yoy'))
    np_yoy = _num(fina.get('netprofit_yoy'))
    gm = _num(fina.get('grossprofit_margin'))
    debt = _num(fina.get('debt_to_assets'))

    s = 0.0
    s += _band(roe, [(0.0, 0), (3.0, 6), (6.0, 12), (10.0, 18), (15.0, 24), (999, 30)], default=15)
    s += _band(ocf, [(0.0, 2), (0.05, 8), (0.1, 12), (0.2, 16), (999, 20)], default=12)
    s += _band(or_yoy, [(-10.0, 2), (0.0, 5), (5.0, 9), (15.0, 13), (30.0, 17), (999, 20)], default=12)
    s += _band(np_yoy, [(-20.0, 2), (0.0, 6), (20.0, 10), (50.0, 13), (999, 15)], default=10)
    s += _band(gm, [(0.0, 0), (10.0, 4), (20.0, 6), (30.0, 8), (999, 10)], default=6)
    s += _band(debt, [(50.0, 5), (65.0, 3), (80.0, 2), (999, 0)], default=3)
    return {'ok': True, 'score': round(s, 1), 'roe': roe, 'ocf_to_or': ocf,
            'or_yoy': or_yoy, 'netprofit_yoy': np_yoy, 'grossprofit_margin': gm,
            'debt_to_assets': debt, 'end_date': fina.get('end_date'),
            'ann_date': fina.get('ann_date')}


# ──────────────────────────── 复核与分级 ────────────────────────────

def hard_flags(sig: dict, tech: dict, fund: dict, basic: dict | None) -> list[str]:
    """硬性风险：命中即剔除（技术面/基本面已明显走坏或不符合交易前提）。"""
    flags = []
    name = (sig.get('stock_name') or '').upper()
    if 'ST' in name or '退' in name:
        flags.append('ST/退市风险股')
    mv_yi = _num((basic or {}).get('total_mv'))
    mv_yi = mv_yi / 10000.0 if mv_yi else None
    if mv_yi is not None and mv_yi < MV_HARD_YI:
        flags.append('总市值仅 %.1f 亿（< %.0f 亿）' % (mv_yi, MV_HARD_YI))
    if tech.get('bias20') is not None and tech['bias20'] > BIAS20_MAX:
        flags.append('MA20 乖离 +%.1f%% 严重超买' % tech['bias20'])
    if tech.get('ma60') and tech.get('close') and tech['close'] < tech['ma60'] \
            and (tech.get('r20') or 0) < -10:
        flags.append('跌破 MA60 且 20 日跌幅 %.1f%%，趋势走坏' % tech['r20'])
    if fund.get('ok'):
        if (fund.get('netprofit_yoy') is not None and fund['netprofit_yoy'] < -50
                and fund.get('roe') is not None and fund['roe'] < 0):
            flags.append('净利同比 %.0f%% 且 ROE %.1f%%，业绩与盈利双杀'
                         % (fund['netprofit_yoy'], fund['roe']))
        debt = fund.get('debt_to_assets')
        ind = sig.get('industry') or ''
        if debt is not None and debt > DEBT_MAX and not any(k in ind for k in FINANCE_INDUSTRIES):
            flags.append('资产负债率 %.0f%%（> %.0f%%）' % (debt, DEBT_MAX))
    return flags


def tech_reason(tech: dict) -> str:
    if not tech.get('ok'):
        return '本地行情不足，无法评估技术面'
    parts = []
    if tech['above_ma20']:
        parts.append('站上 MA20（乖离 %+.1f%%）' % tech['bias20'])
    else:
        parts.append('仍处 MA20 下方（乖离 %+.1f%%）' % tech['bias20'])
    parts.append('MA20 %s' % ('上行' if tech['ma20_up'] else '走平/下行'))
    if tech['ma60_up'] and tech.get('close') and tech.get('ma60') and tech['close'] > tech['ma60']:
        parts.append('MA60 上行且价在其上')
    if tech.get('break_60d'):
        parts.append('站上 60 日最高（突破形态）')
    if tech.get('vr5') is not None:
        parts.append('量能 %s' % ('%.2f 倍放大' % tech['vr5'] if tech['vr5'] >= 1
                                 else '缩至 %.2f 倍' % tech['vr5']))
    if tech.get('turnover') is not None:
        parts.append('换手 %.1f%%' % tech['turnover'])
    if tech.get('pos60') is not None:
        parts.append('60 日区间位置 %.0f%%' % tech['pos60'])
    if tech.get('r20') is not None:
        parts.append('20 日 %+.1f%%' % tech['r20'])
    return '、'.join(parts)


def fund_reason(fund: dict) -> str:
    if not fund.get('ok'):
        return '无本期财务数据（按中性 50 计分）'
    p = _period_label(fund.get('end_date'))
    bits = []
    if fund.get('roe') is not None:
        bits.append('ROE %.1f%%' % fund['roe'])
    if fund.get('or_yoy') is not None:
        bits.append('营收同比 %+.1f%%' % fund['or_yoy'])
    if fund.get('netprofit_yoy') is not None:
        bits.append('净利同比 %+.1f%%' % fund['netprofit_yoy'])
    if fund.get('grossprofit_margin') is not None:
        bits.append('毛利率 %.1f%%' % fund['grossprofit_margin'])
    if fund.get('ocf_to_or') is not None:
        bits.append('现金流/营收 %.2f' % fund['ocf_to_or'])
    if fund.get('debt_to_assets') is not None:
        bits.append('负债率 %.1f%%' % fund['debt_to_assets'])
    return '%s %s' % (p, '、'.join(bits))


def _period_label(end_date) -> str:
    s = str(end_date or '')
    if len(s) != 8:
        return '最近报告期'
    y, md = s[:4], s[4:]
    return {'0331': '%sQ1' % y, '0630': '%sH1' % y,
            '0930': '%sQ3' % y, '1231': '%s年报' % y}.get(md, s)


def review(trade_date: str) -> tuple[list[dict], str]:
    """执行复核，返回 (明细列表, 行情数据截止日)"""
    sigs = load_signals(trade_date)
    if not sigs:
        return [], trade_date
    codes = [s['ts_code'] for s in sigs]
    bars_map = load_bars(codes, trade_date)
    basic_map = load_basic(codes, trade_date)
    fina_map = load_fina(codes, trade_date)

    data_date = trade_date
    for s in sigs:
        bars = bars_map.get(s['ts_code'])
        tech = tech_metrics(bars, basic_map.get(s['ts_code']))
        fund = fund_metrics(fina_map.get(s['ts_code']))
        basic = basic_map.get(s['ts_code']) or {}

        if bars is not None and len(bars):
            data_date = max(data_date, str(bars['trade_date'].iloc[-1]))
            # 部分策略落库时未写 close/pct_chg，用行情库当日值补齐，保证表格无空缺
            if s.get('close') is None:
                s['close'] = float(bars['close'].iloc[-1])
            if s.get('pct_chg') is None:
                s['pct_chg'] = _num(bars['pct_chg'].iloc[-1])

        fund_score = fund['score'] if fund['score'] is not None else FUND_NEUTRAL
        tech_score = tech['score']
        comp = None if tech_score is None else round(W_TECH * tech_score + W_FUND * fund_score, 1)

        mv = _num(basic.get('total_mv'))
        s.update({
            'tech': tech, 'fund': fund, 'basic': basic,
            'tech_score': tech_score, 'fund_score': fund_score,
            'fund_missing': fund['score'] is None,
            'comp': comp,
            'mv_yi': round(mv / 10000.0, 1) if mv else None,
            'pe_ttm': _num(basic.get('pe_ttm')), 'pb': _num(basic.get('pb')),
            'dv_ttm': _num(basic.get('dv_ttm')),
            'tushare_vr': _num(basic.get('volume_ratio')),
        })
        s['flags'] = hard_flags(s, tech, fund, basic)
        s['tier'] = _tier(s)
    order = {'优选': 0, '观察': 1, '剔除': 2}
    sigs.sort(key=lambda d: (order[d['tier']], -(d['comp'] if d['comp'] is not None else -1)))
    return sigs, data_date


def _tier(s: dict) -> str:
    if s['flags'] or s['tech_score'] is None:
        return '剔除'
    if (s['comp'] >= T1_MIN and s['tech_score'] >= T1_TECH and s['fund_score'] >= T1_FUND
            and (s['mv_yi'] is None or s['mv_yi'] >= MV_MIN_YI)):
        return '优选'
    soft = []
    if s['mv_yi'] is not None and s['mv_yi'] < MV_MIN_YI:
        soft.append('总市值 %.1f 亿不足 %.0f 亿' % (s['mv_yi'], MV_MIN_YI))
    s['soft'] = soft
    if s['comp'] >= T2_MIN and s['tech_score'] >= T2_TECH:
        return '观察'
    return '剔除'


def reject_reason(s: dict) -> str:
    if s['flags']:
        return '；'.join(s['flags'])
    if s['tech_score'] is None:
        return '本地行情数据缺失，无法复核'
    r = []
    if s['comp'] < T2_MIN:
        r.append('综合分 %.1f 低于观察线 %.0f' % (s['comp'], T2_MIN))
    if s['tech_score'] < T2_TECH:
        r.append('技术分 %.1f 低于观察线 %.0f' % (s['tech_score'], T2_TECH))
    return '；'.join(r) or '未达入选条件'


# ──────────────────────────── 报告生成 ────────────────────────────

def _fx(v, nd=1, suffix='', sign=False):
    if v is None:
        return '—'
    return ('%+.*f' if sign else '%.*f') % (nd, v) + suffix


def build_markdown(trade_date: str, sigs: list[dict], data_date: str) -> tuple[str, str]:
    n = len(sigs)
    n_strat = len({t for s in sigs for t, _, _ in s['strategies']})
    t1 = [s for s in sigs if s['tier'] == '优选']
    t2 = [s for s in sigs if s['tier'] == '观察']
    t3 = [s for s in sigs if s['tier'] == '剔除']
    lines = [
        '# 选股信号二次复核 · %s' % trade_date,
        '',
        '本日新增信号 **%d** 条，去重后 **%d** 只，覆盖 **%d** 个策略；'
        '二次筛选结果：优选 **%d** / 观察 **%d** / 剔除 **%d**。'
        % (n, n, n_strat, len(t1), len(t2), len(t3)),
        '',
        '口径：综合分 = 0.65 × 技术面 + 0.35 × 基本面（技术面 100 = 趋势30+量价25+位置20+动能15+风险10；'
        '基本面 100 = ROE30+现金流20+营收20+净利15+毛利10+负债5，按公告日 as-of 取最近报告期，'
        '缺失记中性 50）；优选 = 综合≥70 且 技术≥65 且 基本面≥50 且 总市值≥80 亿且无硬性风险；'
        '观察 = 综合≥60 且 技术≥55；其余剔除。行情数据截至 %s。' % data_date,
        '',
    ]

    head_cols = ('| 代码 | 名称 | 主信号 | 策略 | 综合 | 技术 | 基本面 | 收盘 | 当日 | '
                 '换手 | 量比 | 市值(亿) | PE_TTM |')
    sep = '|' + '---|' * 13

    def table(items):
        out = [head_cols, sep]
        for s in items:
            t = s['tech']
            out.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                s['ts_code'], s['stock_name'] or '',
                (s['signal'] or '') + ('·' + s['action'] if s.get('action') else ''),
                '/'.join(t[0] for t in s['strategies'][:3]),
                _fx(s['comp']), _fx(s['tech_score']), _fx(s['fund_score']),
                _fx(s.get('close'), 2), _fx(s.get('pct_chg'), 2, '%', sign=True),
                _fx(t.get('turnover'), 1, '%'), _fx(t.get('vr5'), 2, '倍'),
                _fx(s.get('mv_yi'), 1),
                '亏损' if (s.get('pe_ttm') is not None and s['pe_ttm'] <= 0)
                else _fx(s.get('pe_ttm'), 1)))
        return out

    lines += ['## 一、优选（%d 只）' % len(t1), '']
    if t1:
        lines += table(t1) + ['']
        lines += ['### 逐只要点', '']
        for s in t1:
            lines += ['**%s（%s）**' % (s['stock_name'] or s['ts_code'], s['ts_code']),
                      '- 技术面（%s 分）：%s' % (_fx(s['tech_score']), tech_reason(s['tech'])),
                      '- 基本面（%s 分）：%s' % (_fx(s['fund_score']), fund_reason(s['fund'])),
                      ]
            tip = []
            if s.get('stop_price'):
                tip.append('参考止损 %.2f' % s['stop_price'])
            if s.get('target_price'):
                tip.append('参考目标 %.2f' % s['target_price'])
            if s.get('reason'):
                tip.append('策略理由：%s' % str(s['reason'])[:120])
            if tip:
                lines.append('- ' + '；'.join(tip))
            lines.append('')
    else:
        lines += ['本日无标的通过优选门槛。', '']

    lines += ['## 二、观察（%d 只）' % len(t2), '']
    if t2:
        lines += table(t2) + ['']
        for s in t2:
            note = tech_reason(s['tech'])
            extra = ('；' + '；'.join(s.get('soft') or [])) if s.get('soft') else ''
            lines.append('- **%s（%s）** 综合 %s：%s%s'
                         % (s['stock_name'] or s['ts_code'], s['ts_code'],
                            _fx(s['comp']), note, extra))
        lines.append('')
    else:
        lines += ['本日无观察标的。', '']

    lines += ['## 三、剔除（%d 只）' % len(t3), '']
    if t3:
        lines += ['| 代码 | 名称 | 主信号 | 策略 | 综合 | 技术 | 基本面 | 剔除原因 |',
                  '|' + '---|' * 8]
        for s in t3:
            lines.append('| %s | %s | %s | %s | %s | %s | %s | %s |' % (
                s['ts_code'], s['stock_name'] or '', s['signal'] or '',
                '/'.join(t[0] for t in s['strategies'][:2]),
                _fx(s['comp']), _fx(s['tech_score']), _fx(s['fund_score']),
                reject_reason(s)))
        lines.append('')

    if any(s['fund_missing'] for s in sigs):
        lines += ['## 附：提示', '',
                  '- 个别标的无本期财务数据（多为次新股或缓存未覆盖），基本面按中性 50 计分，'
                  '结论需结合公告人工确认。', '']

    lines += ['## 风险提示', '',
              '- 本报告由本地缓存库数据规则化生成，只做**二次复核**，不重复策略本身的选股逻辑，'
              '也不构成投资建议。',
              '- 技术面基于日线（不复权原始行情）计算，除权除息日附近指标可能失真；'
              '财务数据为最近一期公告值，存在滞后。',
              '- 剔除名单仅代表未通过本复核口径，不代表个股基本面恶化。',
              '']

    # 末段：tushare_quant 每日复盘（20261003 用户要求，原文照录；无产出则跳过）
    _tq = load_tq_review(trade_date)
    if _tq:
        _tq_src, _tq_body = _tq
        lines += ['## 四、tushare_quant 每日复盘（大盘 · 主题 · ETF · 中长线池）', '',
                  '> 数据源：report_daily/%s（tushare_quant.py 当日产出，原文照录）' % _tq_src, '',
                  _tq_body, '']
    else:
        print('[信号复核] 未找到 tushare_quant 当日复盘（%s），跳过末段'
              % os.path.join(TQ_REPORT_DIR, 'Final_Self_%s.md' % trade_date))

    return '\n'.join(lines), '优选 %d / 观察 %d / 剔除 %d' % (len(t1), len(t2), len(t3))


# ──────────────────────────── 邮件推送 ────────────────────────────

_MAIL_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:0; background:#eef2f7; -webkit-text-size-adjust:100%;
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
       font-size:22px; line-height:1.8; color:#1f2937; }
.wrap { padding:14px; }
.hd { background:#0f766e; border-radius:14px; padding:18px 16px; margin-bottom:14px; color:#fff; }
.hd .t { font-size:26px; font-weight:700; line-height:1.45; }
.hd .s { font-size:18px; opacity:.92; margin-top:6px; }
.card { background:#fff; border-radius:14px; padding:18px 16px 8px; box-shadow:0 1px 4px rgba(15,23,42,.08); }
.ft { text-align:center; color:#94a3b8; font-size:17px; line-height:1.7; padding:14px 8px 4px; }
h1,h2,h3,h4 { margin:22px 0 12px; line-height:1.45; font-weight:700; color:#0f172a; }
h1 { font-size:26px; padding-bottom:10px; border-bottom:3px solid #0f766e; }
h2 { font-size:24px; padding-left:12px; border-left:7px solid #0f766e; }
h3 { font-size:22px; color:#334155; }
p { margin:0 0 14px; }
strong { color:#b91c1c; }
ul,ol { margin:0 0 14px; padding-left:1.35em; }
li { margin:7px 0; }
hr { border:0; border-top:1px dashed #cbd5e1; margin:22px 0; }
code { background:#f1f5f9; color:#be123c; border-radius:6px; padding:2px 7px; font-size:21px; }
.tbl { overflow-x:auto; -webkit-overflow-scrolling:touch; margin:0 0 16px;
       border:1px solid #e2e8f0; border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:21px; }
th { background:#0f766e; color:#fff; font-weight:600; text-align:left; padding:11px 10px;
     border-right:1px solid #14907f; white-space:nowrap; }
th:last-child { border-right:0; }
td { padding:11px 10px; border-top:1px solid #e8eef5; white-space:nowrap; }
tbody tr:nth-child(even) { background:#f8fafc; }
"""


def md_to_email_html(md_text: str, title: str, subtitle: str = '') -> str:
    import markdown2
    body = markdown2.markdown(
        md_text, extras=['tables', 'fenced-code-blocks', 'strike', 'task_list'])
    body = re.sub(r'<table>', '<div class="tbl"><table>', body).replace('</table>', '</table></div>')
    sub = '<div class="s">%s</div>' % subtitle if subtitle else ''
    gen = datetime.now().strftime('%Y-%m-%d %H:%M')
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<div class="wrap">\n'
        '<div class="hd"><div class="t">%s</div>%s</div>\n'
        '<div class="card">\n%s\n</div>\n'
        '<div class="ft">生成时间 %s<br>本邮件由选股信号复核程序自动推送，仅供研究参考，不构成投资建议</div>\n'
        '</div>\n</body>\n</html>' % (title, _MAIL_CSS, title, sub, body, gen))


def send_email(subject: str, html: str, to: str = MAIL_TO) -> bool:
    """Agent Mail CLI 发送 HTML 正文邮件；异常只打日志不抛出。"""
    if not os.path.exists(MAIL_CLI):
        print('[信号复核] 未找到 Agent Mail CLI: %s，跳过邮件推送' % MAIL_CLI)
        return False
    body_path = os.path.join(os.getcwd(), '_signal_review_mail.html')
    try:
        raw = html.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(html)
    except OSError as e:
        print('[信号复核] 邮件正文写入失败: %s' % e)
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', to, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print('[信号复核] 邮件推送成功: %s' % to)
            os.remove(body_path)
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print('[信号复核] 邮件推送失败: rc=%s %s（正文留存: %s）' % (r.returncode, detail, body_path))
        return False
    except Exception as e:
        print('[信号复核] 邮件推送异常: %s' % e)
        return False


# ──────────────────────────── 主流程 ────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='选股信号二次复核（技术面×基本面）+ 邮件推送')
    ap.add_argument('--date', default=None, help='选股日期 YYYYMMDD，默认当日（当日无信号则取最近一日）')
    ap.add_argument('--no-mail', action='store_true', help='只生成报告，不推送邮件')
    ap.add_argument('--dry-run', action='store_true', help='不发邮件，保留 HTML 到报告目录')
    ap.add_argument('--to', default=MAIL_TO, help='收件人，默认 %s' % MAIL_TO)
    args = ap.parse_args(argv)

    if not os.path.exists(PICK_DB):
        raise SystemExit('找不到选股库：%s' % PICK_DB)

    conn = sqlite3.connect(PICK_DB, timeout=30.0)
    try:
        trade_date, fallback = resolve_trade_date(conn, args.date)
    finally:
        conn.close()
    if fallback:
        print('[信号复核] %s 无新增信号，回退最近有信号日 %s'
              % (datetime.now().strftime('%Y%m%d'), trade_date))

    sigs, data_date = review(trade_date)
    if not sigs:
        print('[信号复核] %s 无新增选股信号，跳过报告与推送' % trade_date)
        return 0

    md_text, summary = build_markdown(trade_date, sigs, data_date)
    os.makedirs(REPORT_DIR, exist_ok=True)
    md_path = os.path.join(REPORT_DIR, 'signal_review_%s.md' % trade_date)
    html_path = os.path.join(REPORT_DIR, 'signal_review_%s.html' % trade_date)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_text)

    subject = '选股信号二次复核 · %s（%s）' % (trade_date, summary)
    html = md_to_email_html(md_text, subject, '技术面 × 基本面 二次筛选 · 本日新增信号复盘')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print('[信号复核] %s 信号 %d 只 → %s' % (trade_date, len(sigs), summary))
    print('[信号复核] 报告：%s' % md_path)

    if args.no_mail or args.dry_run:
        print('[信号复核] 已跳过邮件推送（%s）'
              % ('--no-mail' if args.no_mail else '--dry-run'))
        return 0
    return 0 if send_email(subject, html, args.to) else 1


if __name__ == '__main__':
    sys.exit(main())
