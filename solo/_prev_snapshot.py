# -*- coding: utf-8 -*-
"""前日状态快照提取器 —— 为盘中连续计算提供基准

★ 为什么需要它
  盘中分析要做三件事，每件都需要「前一日」作为基准：
    1. 广度的 5 日均 / 20 日均  → 需要最近 20 个交易日的广度序列
    2. 状态机的投票链          → 需要昨日的 state / pending / vote_history
    3. 短线赚钱效应的相对量    → 需要**昨日涨停股名单**（算今日晋级率与强股表现）
  这些都不该在盘中重算：
    · 重算慢（每次扫 7.2M 行 daily_cache）
    · 口径可能与盘后不一致（盘后是收盘口径，盘中重算会混入未收盘数据）
  所以：**从盘后产出的报告与状态表里取前日数据**，一次提取、缓存复用。

数据来源（按优先级）
  ① state_regime 表（cache_backbone_tushare/market_regime_v10.db）
     → state / consecutive_days / pending_target / pending_days / node_type
  ② V10.0 盘后报告（market_regime_v10_YYYYMMDD.txt）
     → 广度、涨跌停、炸板率、晋级率、强股次日表现、成交额、指数状态
  ③ V10.1 盘后报告（market_regime_v101_YYYYMMDD.txt）
     → 四模块判定结果、仓位权限、主线
  ④ daily_cache（算昨日涨停股名单，与 V10.0 同口径：pct_chg ≥ 阈值×0.99）

产出：_bt_out/prev_snapshot_<date>.json
"""
import os
import re
import json
import glob
import sqlite3
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
STATE_DB = os.path.join(CACHE, 'market_regime_v10.db')
STOCK_DB = os.path.join(os.path.dirname(BASE), 'cache_daily', 'stock_data.db')
OUT = os.path.join(BASE, '_bt_out')

# 报告里可能出现的目录（盘后主流程 / 模块单独运行）
REPORT_DIRS = [CACHE, os.path.join(os.path.dirname(BASE), 'cache_daily')]


def _find_report(prefix, trade_date):
    """在候选目录里找**指定日期**的报告文件。

    ★ 不做「取最后一个」的兜底 —— 那会让 20260513 静默读到 20261030 的数据，
      口径完全错乱且极难察觉。找不到就返回 None，由调用方标记缺失。
    """
    name = f'{prefix}_{trade_date}.txt'
    for d in REPORT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def _num(text, default=None):
    """从文本里抽第一个数字"""
    if text is None:
        return default
    m = re.search(r'-?\d+\.?\d*', str(text))
    return float(m.group()) if m else default


def parse_v10_report(path):
    """解析 V10.0 盘后报告"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        txt = f.read()
    out = {'_file': path}

    def grab(pat, cast=None):
        m = re.search(pat, txt)
        if not m:
            return None
        v = m.group(1).strip()
        if cast:
            try:
                return cast(v)
            except Exception:
                return None
        return v

    out['state'] = grab(r'当前市场状态：\s*(\S+)\s')
    out['state_full'] = grab(r'当前市场状态：(.+)')
    out['state_days'] = grab(r'状态持续时间：\s*(\d+)', int)
    out['node_type'] = grab(r'节点类型：(\S+)')
    out['confidence'] = grab(r'状态置信度：\s*(\S+)')
    out['breadth_ratio'] = grab(r'上涨家数占比：\s*([\d.]+)%', float)
    out['breadth_percentile'] = grab(r'历史分位：\s*([\d.]+)%', float)
    out['zt_count'] = grab(r'涨停家数：\s*(\d+)', int)
    out['dt_count'] = grab(r'跌停家数：\s*(\d+)', int)
    out['dt_prev'] = grab(r'跌停家数：\s*\d+（前日\s*(\d+)）', int)
    out['broken_rate'] = grab(r'炸板率：\s*([\d.]+)%', float)
    out['promotion_rate'] = grab(r'连板晋级率：\s*([\d.]+)%', float)
    out['strong_mean'] = grab(r'昨日涨停股次日表现：平均\s*([-\d.]+)%', float)
    out['strong_up_ratio'] = grab(r'昨日涨停股次日表现：平均\s*[-\d.]+%，上涨比例\s*([\d.]+)%', float)
    out['strong_count'] = grab(r'上涨比例\s*[\d.]+%（\s*(\d+)\s*只）', int)
    out['amount'] = grab(r'成交额：\s*(\d+)\s*亿', float)
    out['amount_vs_prev'] = grab(r'较前一交易日：\s*([-\d.]+)%', float)
    out['amount_vs_ma5'] = grab(r'相对5日均额：\s*([-\d.]+)%', float)
    out['amount_vs_ma20'] = grab(r'相对20日均额：\s*([-\d.]+)%', float)
    out['smallcap_vs_large'] = grab(r'小盘相对大盘：\s*([-\d.]+)\s*个百分点', float)
    out['target_position'] = grab(r'建议目标仓位：\s*(\d+)%', int)
    # 指数表：站上 MA20/MA60 的指数个数
    idx_rows = re.findall(r'^\s{2}(\S+?)\s+([\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$',
                          txt, re.M)
    if idx_rows:
        out['index_rows'] = [{'name': r[0], 'close': float(r[1]), 'pct': float(r[2]),
                              'ma20': float(r[3]), 'ma60': float(r[4]),
                              'ret5': float(r[5]), 'ret20': float(r[6]),
                              'pos250': float(r[7]), 'dd_pos': float(r[8])} for r in idx_rows]
    return out


def parse_v101_report(path):
    """解析 V10.1 盘后报告"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        txt = f.read()
    out = {'_file': path}
    out['state'] = grab = None
    m = re.search(r'当前状态：\s*(\S+)', txt)
    out['state'] = m.group(1) if m else None
    m = re.search(r'状态持续时间：\s*(\d+)', txt)
    out['state_days'] = int(m.group(1)) if m else None
    m = re.search(r'当前市场允许的最高仓位：\s*(\d+)%', txt)
    out['authority_max'] = int(m.group(1)) if m else None
    m = re.search(r'当前目标仓位（建议）：\s*(\d+)%', txt)
    out['suggested_position'] = int(m.group(1)) if m else None
    m = re.search(r'满仓授权：(\S+)', txt)
    out['full_auth'] = m.group(1) if m else None
    # 四模块
    mods = {}
    for mm in re.finditer(r'\[(✓通过|✗未通过)\]\s*([^\s（(]+)', txt):
        mods[mm.group(2)] = (mm.group(1) == '✓通过')
    out['modules_passed'] = mods
    # 主线
    m = re.search(r'【当前主线】[^\n]*\n([^\n]+)', txt)
    if m:
        out['mainline_text'] = m.group(1).strip().lstrip('★ ').strip()
    else:
        m2 = re.search(r'当前主线：([^\n]+)', txt)
        out['mainline_text'] = m2.group(1).strip() if m2 else None
    m = re.search(r'组合风险预算：\s*([\d.]+)%\s*/\s*([\d.]+)%', txt)
    if m:
        out['portfolio_risk_pct'] = float(m.group(1))
        out['risk_budget_pct'] = float(m.group(2))
    return out


def load_state_row(trade_date):
    """从 state_regime 表读该日状态行"""
    if not os.path.exists(STATE_DB):
        return {}
    conn = sqlite3.connect(f'file:{STATE_DB}?mode=ro', uri=True)
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(regime_state)')]
        row = conn.execute(
            'SELECT * FROM regime_state WHERE trade_date=?', (trade_date,)).fetchone()
        if not row:
            return {}
        d = dict(zip(cols, row))
        # vote_history 是 JSON 字符串
        for k in ('reasons', 'triggers', 'vote_history'):
            if k in d and isinstance(d[k], str) and d[k]:
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        return d
    finally:
        conn.close()


def breadth_series(n=25):
    """从 daily_cache 取最近 n 个交易日的广度序列（收盘口径）"""
    conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)
    try:
        dates = [r[0] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>=? ORDER BY trade_date',
            ('20260101',))]
        dates = dates[-n:]
        out = []
        for d in dates:
            u = conn.execute('SELECT COUNT(*) FROM daily_cache WHERE trade_date=? AND pct_chg>0',
                             (d,)).fetchone()[0]
            dn = conn.execute('SELECT COUNT(*) FROM daily_cache WHERE trade_date=? AND pct_chg<0',
                              (d,)).fetchone()[0]
            out.append({'d': d, 'ratio': (u / (u + dn) * 100) if (u + dn) else 0.0,
                        'up': u, 'down': dn})
        return out
    finally:
        conn.close()


def limit_up_stocks(trade_date):
    """取某日涨停股名单（与 V10.0 同口径：pct_chg ≥ 阈值×0.99 且 < 阈值+0.1）"""
    conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)
    try:
        df = None
        try:
            import pandas as pd
            df = pd.read_sql_query(
                'SELECT ts_code, pct_chg FROM daily_cache WHERE trade_date=?', conn,
                params=(trade_date,))
        except Exception:
            pass
        if df is None or df.empty:
            return []
        try:
            from market_regime_v10 import _limit_thresholds as _v10thr
        except Exception:
            def _v10thr(code):
                code = str(code)
                if code.startswith(('688', '300', '301', '8', '92')):
                    return 20.0
                return 10.0
        thr = df['ts_code'].map(_v10thr)
        m = (df['pct_chg'] >= thr * 0.99) & (df['pct_chg'] < thr + 0.1)
        return df[m]['ts_code'].tolist()
    finally:
        conn.close()


def compute_from_cache(trade_date):
    """从 daily_cache 直接算某日的核心指标（V10.0 同口径）。

    ★ 用途：当该日的 V10.0 盘后报告缺失时降级使用，
      保证「任何日期都能出前日快照」，不因报告缺失而断掉盘中连续计算。
      口径与 V10.0 一致：
        · 广度     = pct_chg>0 家数 / (上涨+下跌)
        · 涨跌停   = pct_chg ≥ 阈值×0.99 且 < 阈值+0.1
        · 昨日涨停股次日表现 = 用前一日涨停名单算今日平均涨幅与上涨比例
        · 连板晋级率 = 昨日涨停中今日继续涨停的比例
    """
    import pandas as pd
    conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)
    try:
        df = pd.read_sql_query(
            'SELECT ts_code, pct_chg, amount FROM daily_cache WHERE trade_date=?',
            conn, params=(trade_date,))
        if df.empty:
            return {}
        prev_date = conn.execute(
            'SELECT MAX(trade_date) FROM daily_cache WHERE trade_date<?',
            (trade_date,)).fetchone()[0]
        pdf = pd.DataFrame()
        if prev_date:
            pdf = pd.read_sql_query(
                'SELECT ts_code, pct_chg FROM daily_cache WHERE trade_date=?',
                conn, params=(prev_date,))
    finally:
        conn.close()

    # ★ 复用 V10.0 的 _limit_thresholds，避免口径漂移
    #   （V10.0 对 8xx/92x 开头用 20%，本模块早期版本用 10%，
    #     导致跌停数差 1 只 —— 920169.BJ 被误判为跌停）
    try:
        from market_regime_v10 import _limit_thresholds as _v10thr
    except Exception:
        def _v10thr(code):
            code = str(code)
            if code.startswith(('688', '300', '301', '8', '92')):
                return 20.0
            return 10.0
    def _thr(codes):
        return codes.map(_v10thr)

    up = int((df.pct_chg > 0).sum()); dn = int((df.pct_chg < 0).sum())
    thr = _thr(df.ts_code)
    zt_mask = (df.pct_chg >= thr * 0.99) & (df.pct_chg < thr + 0.1)
    dt_mask = (df.pct_chg <= -thr * 0.99) & (df.pct_chg > -thr - 0.1)
    out = {
        'breadth_ratio': round(up / (up + dn) * 100, 1) if (up + dn) else None,
        'zt_count': int(zt_mask.sum()), 'dt_count': int(dt_mask.sum()),
        'amount': round(float(df.amount.sum()) / 1e5, 0),
        '_source': 'daily_cache（报告缺失时的降级口径）',
    }
    # 昨日涨停股今日表现 + 晋级率
    if not pdf.empty:
        p_thr = _thr(pdf.ts_code)
        y_zt = set(pdf.loc[(pdf.pct_chg >= p_thr*0.99) & (pdf.pct_chg < p_thr+0.1), 'ts_code'])
        if y_zt:
            sub = df[df.ts_code.isin(y_zt)]
            if len(sub):
                n_thr = _thr(sub.ts_code)
                pcts = sub.pct_chg.values
                cont = int(((pcts >= n_thr.values*0.99) & (pcts < n_thr.values+0.1)).sum())
                out['strong_mean'] = round(float(pcts.mean()), 2)
                out['strong_up_ratio'] = round(float((pcts > 0).mean() * 100), 1)
                out['strong_count'] = len(sub)
                out['promotion_rate'] = round(cont / len(y_zt) * 100, 1)
    return out


def build(trade_date=None, use_cache=True, before_date=None):
    """构建前日快照。

    trade_date   缺省 = 最新有报告的交易日
    before_date  若给出，则只取**该日期之前**的交易日（盘中用）
                 ★ 盘中必须传 before_date=今天，否则盘后刚跑完、
                   今天的报告已存在 → 快照会指向今天自己，
                   造成「昨日涨停名单 = 今日涨停名单」→ 晋级率恒为 100%、
                   昨日涨停股今日表现恒为 +11%（实测 20261102）。
    """
    os.makedirs(OUT, exist_ok=True)

    if trade_date is None:
        # 找最新**有完整报告**（V10.0 + V10.1）的交易日
        cands = set()
        for d in REPORT_DIRS:
            for p in glob.glob(os.path.join(d, 'market_regime_v10_*.txt')):
                m = re.search(r'(\d{8})', os.path.basename(p))
                if m and _find_report('market_regime_v101', m.group(1)):
                    cands.add(m.group(1))
        if before_date:
            cands = {c for c in cands if c < str(before_date)}
        if not cands:
            raise RuntimeError('找不到任何完整的 V10.0+V10.1 盘后报告')
        trade_date = sorted(cands)[-1]

    cache_path = os.path.join(OUT, f'prev_snapshot_{trade_date}.json')
    if use_cache and os.path.exists(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            return json.load(f)

    snap = {'trade_date': trade_date, 'built_at': datetime.datetime.now().isoformat(timespec='seconds')}

    p10 = _find_report('market_regime_v10', trade_date)
    p101 = _find_report('market_regime_v101', trade_date)
    snap['v10'] = parse_v10_report(p10)
    snap['v101'] = parse_v101_report(p101)
    snap['state_row'] = load_state_row(trade_date)
    # 报告缺失 → 从 daily_cache 降级计算，保证任何日期都能出快照
    if not p10:
        snap['v10_fallback'] = compute_from_cache(trade_date)
        fb = snap['v10_fallback']
        snap['v10'].update({k: v for k, v in fb.items() if not k.startswith('_')})
        snap['note'] = f'⚠ {trade_date} 缺 V10.0 报告，部分指标由 daily_cache 重算'
    snap['report_available'] = {
        'v10': bool(p10), 'v101': bool(p101),
        'v10_path': p10, 'v101_path': p101,
    }
    if not p10:
        snap['note'] = f'⚠ 缺少 {trade_date} 的 V10.0 盘后报告，'
    if not p101:
        snap['note'] = (snap.get('note', '') +
                        f'⚠ 缺少 {trade_date} 的 V10.1 盘后报告').strip()

    # 广度序列（含 trade_date 当日）
    snap['breadth_series'] = breadth_series(25)

    # 昨日涨停股名单 —— 用于盘中算晋级率与强股次日表现
    zt = limit_up_stocks(trade_date)
    snap['limit_up_list'] = zt
    snap['limit_up_count'] = len(zt)

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    return snap


def summarize(snap):
    """打印摘要"""
    print('=' * 70)
    print(f"  前日快照 · {snap['trade_date']}")
    print('=' * 70)
    v10 = snap.get('v10') or {}
    v101 = snap.get('v101') or {}
    st = snap.get('state_row') or {}
    print(f"  状态 {v10.get('state_full') or st.get('state')}  "
          f"节点 {v10.get('node_type')}  持续 {v10.get('state_days')} 日  "
          f"置信 {v10.get('confidence')}")
    print(f"  广度 {v10.get('breadth_ratio')}%（分位 {v10.get('breadth_percentile')}%）")
    print(f"  涨停 {v10.get('zt_count')}  跌停 {v10.get('dt_count')}（前日 {v10.get('dt_prev')}）")
    print(f"  炸板率 {v10.get('broken_rate')}%  晋级率 {v10.get('promotion_rate')}%")
    print(f"  强股次日 均值 {v10.get('strong_mean')}% / 上涨比 {v10.get('strong_up_ratio')}%"
          f"（{v10.get('strong_count')} 只）")
    print(f"  成交额 {v10.get('amount')} 亿")
    print(f"  建议仓位 {v10.get('target_position')}%  权限上限 {v101.get('authority_max')}%")
    print(f"  四模块: {v101.get('modules_passed')}")
    print(f"  主线: {v101.get('mainline_text')}")
    print(f"  涨停名单 {snap.get('limit_up_count')} 只")
    bs = snap.get('breadth_series') or []
    if bs:
        print(f"  广度序列(近{len(bs)}日): " + ' '.join(f"{x['ratio']:.0f}" for x in bs))
    if st:
        print(f"  state_row: state={st.get('state')} pending=({st.get('pending_target')},{st.get('pending_days')}) "
              f"votes={st.get('vote_history')}")
    print()


if __name__ == '__main__':
    import sys
    td = sys.argv[1] if len(sys.argv) > 1 else None
    s = build(td)
    summarize(s)
    print(f'已缓存: {os.path.join(OUT, f"prev_snapshot_{s['trade_date']}.json")}')
