# -*- coding: utf-8 -*-
"""
KPL 题材轮动 → 成分股筛选（分题材分组输出）
═══════════════════════════════════════════════════
输入：kpl_monitor.analyze_day 识别出的“有价值题材”
      = ①热度总榜TOP15 ∪ ②升温 ∪ ③主线候选
流程：对每个题材的成分股做 卫生过滤(硬剔除) → 量价健康度打分 → 分题材 Top 清单
口径（对齐交易偏好）：
  · 只做双创/主板，剔除 ST/退市、次新(K线<120)、北交所
  · 总市值≥80亿（低于即剔除）；80-150亿弹性最大、双创加分
  · 只做非涨停：当日涨幅≥9% 剔除；近5日≥45% / 近10日≥60% 连续大涨透支剔除
  · 5日线<10日线不惩罚（视为二波/回调候选）；缩量调整加分；MA20走平/向上加分
  · 距60日高点适度回撤(10-30%)加分，贴近20日新高不加分
用法：
  python kpl_picker.py [DATE]      # 缺省取库中最新完整日
"""
import os
import sys
import sqlite3
import datetime
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kpl_monitor as km
import stock_cache as sc

# 硬性阈值
MV_MIN = 800000            # 总市值下限(万元)=80亿
RET_LIMIT_UP = 9.0         # 当日涨幅≥9% 视为涨停/近涨停，剔除
CHG5_DROP = 45.0           # 近5日涨幅≥45% 剔除（连板透支）
CHG10_DROP = 60.0          # 近10日涨幅≥60% 剔除
RET_DROP_DEEP = -9.0       # 当日跌幅≤-9% 破位剔除
MIN_BARS = 120             # K线数量下限（剔除次新/数据不足）
SCORE_FLOOR = 55           # 展示下限：健康度≥此分才进入候选
TOP_K = 8                  # 每个题材最多输出几只
LOOKBACK_DAYS = 210        # 拉取自然日窗口（约140根K线）

LINE = '━' * 62
LINE2 = '─' * 62


def _is_tradable_board(code):
    """沪深可交易板块：主板/创业板/科创板；跳过北交所等"""
    return code.endswith('.SH') and code.startswith(('60', '68')) or \
        code.endswith('.SZ') and code.startswith(('00', '30'))


def _load_day_members(date):
    """当日全部 题材→成分 set + 成分名称 map"""
    conn = sqlite3.connect(km.DB_PATH)
    df = pd.read_sql_query(
        'SELECT ts_code, con_code, con_name FROM kpl_snapshot WHERE trade_date = ?',
        conn, params=(str(date),))
    conn.close()
    if df.empty:
        return {}, {}
    theme_mem = {}
    name_map = {}
    for tc, cc, cn in df.to_numpy():
        theme_mem.setdefault(tc, set()).add(cc)
        name_map.setdefault(cc, cn)
    return theme_mem, name_map


def _load_basic_map(date):
    """当日总市值map(万元)；缓存缺失时尝试自动补齐"""
    df = sc.get_daily_basic_by_date(date)
    if df is None or df.empty:
        try:
            df = sc.daily_basic_market(date, silent=True)
        except Exception:
            df = None
    if df is None or df.empty:
        return {}
    df = df.drop_duplicates('ts_code')
    return dict(zip(df['ts_code'], df['total_mv']))


def _load_circ_map(date):
    """当日流通市值map(万元)，供换手率代理使用"""
    df = sc.get_daily_basic_by_date(date)
    if df is None or df.empty:
        return {}
    if 'circ_mv' not in df.columns:
        return {}
    df = df.drop_duplicates('ts_code')
    return dict(zip(df['ts_code'], df['circ_mv']))


def _pull_bars(code, end_date):
    """取个股截至 end_date 的日线（原始价），返回升序 DataFrame 或 None"""
    start = (pd.Timestamp(str(end_date)) - pd.Timedelta(days=LOOKBACK_DAYS)).strftime('%Y%m%d')
    try:
        df = sc.get_daily_cache(code, start, str(end_date))
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def _stock_metrics(code, name, df, mv):
    """计算个股量价健康度。返回 (metrics_dict, drop_reason)
    若命中硬性剔除返回 (None, 原因)；否则返回 (metrics, None)
    """
    if len(df) < MIN_BARS:
        return None, '次新/数据不足'
    if not _is_tradable_board(code):
        return None, '北交所等非沪深'
    if 'ST' in str(name).upper() or '退' in str(name):
        return None, 'ST/退市股'
    close = df['close'].to_numpy(dtype=float)
    high = df['high'].to_numpy(dtype=float)
    vol = df['vol'].to_numpy(dtype=float)
    n = len(close)
    cur_close = close[-1]
    prev_close = close[-2]
    ret_today = (cur_close / prev_close - 1) * 100
    if pd.isna(ret_today):
        return None, '行情缺失'
    if ret_today >= RET_LIMIT_UP:
        return None, f'涨停/近涨停({ret_today:+.1f}%)'
    if ret_today <= RET_DROP_DEEP:
        return None, f'破位大跌({ret_today:+.1f}%)'
    if mv is not None and mv < MV_MIN:
        return None, f'市值{mv/10000:.0f}亿<80亿'
    ma5 = float(np.mean(close[-5:]))
    ma10 = float(np.mean(close[-10:]))
    ma20 = float(np.mean(close[-20:]))
    ma20_prev = float(np.mean(close[-30:-10]))  # 10个交易日前MA20
    ma20_slope = (ma20 / ma20_prev - 1) * 100 if ma20_prev > 0 else 0.0
    chg5 = (cur_close / close[-6] - 1) * 100
    chg10 = (cur_close / close[-11] - 1) * 100
    if chg5 >= CHG5_DROP:
        return None, f'近5日涨幅{chg5:.0f}%透支'
    if chg10 >= CHG10_DROP:
        return None, f'近10日涨幅{chg10:.0f}%透支'
    dist_ma20 = (cur_close / ma20 - 1) * 100 if ma20 > 0 else 0.0
    high60 = float(np.max(high[-min(60, n):]))
    dist_high60 = (cur_close / high60 - 1) * 100 if high60 > 0 else 0.0  # ≤0为回撤
    vol20 = float(np.mean(vol[-20:]))
    vol5 = float(np.mean(vol[-5:]))
    vr_5_20 = vol5 / vol20 if vol20 > 0 else 1.0
    return {
        'code': code, 'close': cur_close, 'ret_today': ret_today,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma20_slope': ma20_slope,
        'chg5': chg5, 'chg10': chg10, 'dist_ma20': dist_ma20,
        'dist_high60': dist_high60, 'vr_5_20': vr_5_20, 'mv': mv,
        'cross': '二波' if ma5 < ma10 else '多头',
    }, None


def _score(m):
    """量价健康度打分(0-100)，全维度可直接解释"""
    s = 0.0
    reason = []
    # A 交易形态(0-40)
    if -4 <= m['dist_ma20'] <= 6:
        base, tag = 24, 'MA20附近整理/回踩'
    elif m['dist_ma20'] > 6:
        base, tag = 14, '偏离MA20过高'
    else:
        base, tag = 10, '跌破MA20'
    s += base
    if m['ma20_slope'] > 0.5:
        s += 8
        tag += '·MA20向上'
    elif m['ma20_slope'] > -0.5:
        s += 4
        tag += '·MA20走平'
    else:
        s -= 6
        tag += '·MA20下行'
    if m['cross'] == '二波':
        s += 6
        tag += '·5<10日均线(二波候选)'
    if 3 < m['ret_today'] <= 7:
        s += 6
        tag += '·中小阳启动'
    elif -9 <= m['ret_today'] <= -3:
        s += 4
        tag += '·回踩低吸'
    reason.append(tag)
    # B 量能(0-20)
    vr = m['vr_5_20']
    if vr < 0.7:
        s += 20
        reason.append('显著缩量调整')
    elif vr < 0.95:
        s += 15
        reason.append('缩量整理')
    elif vr <= 1.3:
        s += 8
        reason.append('量能平稳')
    elif vr <= 2.0:
        s += 10
        reason.append('温和放量')
    else:
        s -= 5
        reason.append('放量过猛')
    # C 板块偏好(0-15)：双创优先
    if m['code'].startswith(('688', '300', '301')):
        s += 15
        reason.append('双创')
    else:
        s += 6
        reason.append('主板')
    # D 市值弹性(0-15)
    mv = m['mv']
    if mv is not None:
        yi = mv / 10000
        if yi < 150:
            s += 15
        elif yi < 300:
            s += 12
        elif yi < 800:
            s += 8
        else:
            s += 5
        reason.append(f'市值{yi:.0f}亿')
    else:
        reason.append('市值缺失')
    # E 位置安全(0-10)：适度回撤>贴新高（20日新高不加分）
    dh = m['dist_high60']
    if -30 <= dh <= -10:
        s += 10
        reason.append('距60日高回撤充分')
    elif -10 < dh <= -3:
        s += 7
        reason.append('温和回撤')
    elif -3 < dh <= 0:
        s += 3
        reason.append('贴近60日高')
    elif dh > 0:
        s += 3
        reason.append('创新高')
    else:
        s += 8
        reason.append('深度回撤')
    return max(0.0, min(100.0, s)), '·'.join(reason)


def pick(date=None, save=True):
    res = km.analyze_day(date)
    if res is None:
        print('[kpl] 无可分析的完整日（先执行 update / backfill）')
        return
    today = res['today']
    rows = res['rows']
    # 有价值题材池：热度总榜TOP15 ∪ 升温 ∪ 主线候选（退潮警示不入池）
    pool = [r for r in rows if r['is_top15'] or r['is_warm'] or r['is_main']]
    # 排序：主线候选 > 热度总榜 > 升温（组内按hot降序）
    pool.sort(key=lambda r: (
        (0 if r['is_main'] else 1 if r['is_top15'] else 2), -r['hot']))
    theme_mem, name_map = _load_day_members(today)
    mv_map = _load_basic_map(today)

    lines = []
    out = print

    def say(*a):
        s = ' '.join(str(x) for x in a)
        print(s)
        lines.append(s)

    say(LINE)
    say('  KPL 题材成分股筛选（分题材分组）')
    say(f'  交易日 {today}   有价值题材 {len(pool)} 个   生成于 {datetime.datetime.now():%Y-%m-%d %H:%M}')
    say(LINE)

    # 统计计数
    stat = {'total': 0, 'drop': 0, 'pass': 0, 'reasons': {}}
    cache = {}

    def metrics_of(code):
        if code not in cache:
            df = _pull_bars(code, today)
            cache[code] = (None, '行情缺失') if df is None else _stock_metrics(code, name_map.get(code, ''), df, mv_map.get(code))
        return cache[code]

    shown_themes = 0
    for r in pool:
        members = theme_mem.get(r['code'])
        if not members:
            continue
        tags = []
        if r['is_main']:
            tags.append('主线候选')
        if r['is_top15']:
            tags.append('热度榜')
        if r['is_warm']:
            tags.append('升温')
        tag_str = '/'.join(tags)
        res_list = []
        for cc in sorted(members):
            stat['total'] += 1
            m, reason = metrics_of(cc)
            if m is None:
                stat['drop'] += 1
                stat['reasons'][reason] = stat['reasons'].get(reason, 0) + 1
                continue
            m['name'] = name_map.get(cc, cc)
            sc_, why = _score(m)
            m['score'] = sc_
            m['why'] = why
            if sc_ >= SCORE_FLOOR:
                stat['pass'] += 1
                res_list.append(m)
        res_list.sort(key=lambda x: (-x['score'], x['ret_today']))
        if not res_list:
            continue
        shown_themes += 1
        say('')
        say(f'◤ {r["name"]}({r["code"]})  [{tag_str}]  hot={r["hot"]}   成分{len(members)}只 → 通过筛选 {len(res_list)}只')
        say(LINE2)
        for m in res_list[:TOP_K]:
            say(f'  {m["score"]:3.0f}分  {m["name"]}({m["code"]})  收盘{m["close"]:.2f} '
                f'当日{m["ret_today"]:+.1f}% 5日{m["chg5"]:+.0f}% MA20距{m["dist_ma20"]:+.0f}% '
                f'回撤{m["dist_high60"]:.0f}% 量比{m["vr_5_20"]:.2f}')
            say(f'       理由：{m["why"]}')
    say('')
    say(LINE)
    say(f'汇总：候选成分共 {stat["total"]} 只次，剔除 {stat["drop"]}，达标展示 {stat["pass"]}，有产出题材 {shown_themes}/{len(pool)}')
    top_why = sorted(stat['reasons'].items(), key=lambda kv: -kv[1])[:6]
    say('主要剔除原因：' + '，'.join(f'{k}×{v}' for k, v in top_why))
    say('口径：只做非涨停；市值≥80亿；双创优先；缩量回踩/MA20上二波候选加分；近20日新高不加分。')
    say(LINE)

    if save:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f'kpl_筛选_{today}.txt')
        with open(fname, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print(f'[已归档] {fname}')


def engine(date=None, save=True, top_per_theme=4, max_cand=60):
    """═ V1.1 引擎：热榜召回 → 量价/结构/延续性/确认/交易条件 ═
    核心原则：热度只召回不买入；无 PriceConfirmation 不得 PRIMARY BUY；
    强度高但 Extension 极高不追高；突破/回踩/支撑/止损四价分离；
    Quality Rank 找强股，Trade Rank 定买什么。
    """
    res = km.analyze_day(date)
    if res is None:
        print('[kpl] 无可分析的完整日（先执行 update / backfill）')
        return
    today = res['today']
    rows = res['rows']
    pool = [r for r in rows if r['is_top15'] or r['is_warm'] or r['is_main']]
    pool.sort(key=lambda r: ((0 if r['is_main'] else 1 if r['is_top15'] else 2), -r['hot']))
    theme_mem, name_map = _load_day_members(today)
    mv_map = _load_basic_map(today)
    circ_map = _load_circ_map(today)
    cand = _gather_engine_candidates(pool, theme_mem, name_map, mv_map, today,
                                     top_per_theme=top_per_theme, max_cand=max_cand)
    lines = []

    def say(*a):
        s = ' '.join(str(x) for x in a)
        print(s)
        lines.append(s)

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    say(LINE)
    say('  开盘啦热榜 → 个股量价二次筛选引擎 V1.1')
    say(f'  交易日 {today}   候选 {len(cand)} 只（{len(pool)} 个有价值题材×top{top_per_theme} 去重）  生成于 {now}')
    say('  热度召回 → 硬门控 → STQ/VPQ/结构/延续 → Extension → 价格确认 → Entry/Risk → 决策（决策可被规则逐条解释）')
    say(LINE)

    # ── 第一层：候选初筛与硬门控 ──
    recs = []
    stat = {'送入': len(cand)}
    for m in cand:
        em, why = _engine_metrics(m['code'], today, mv_map.get(m['code']))
        if em is None:
            stat[why] = stat.get(why, 0) + 1
            continue
        rec = _engine_decide(m, em, circ_map.get(m['code']))
        if rec is None:
            stat['硬门控(破位/D级结构)'] = stat.get('硬门控(破位/D级结构)', 0) + 1
            continue
        recs.append(rec)
    # HotRank：候选内按最强题材热度排序（仅用于优先级展示与同质排序）
    recs.sort(key=lambda x: -x['max_hot'])
    for i, r in enumerate(recs, 1):
        r['hot_rank'] = i

    # ── 评分（V1.1 全套：STQ/延续/价格确认/完备性/Entry/双排序）──
    for r in recs:
        _score_v11(r)
        r['qscore'] = 0.35 * r['STQ'] + 0.25 * r['vpq'] + 0.20 * r['T1'] + 0.20 * r['T3']
        r['tscore'] = (0.25 * r['T1'] + 0.20 * r['ENTRY'] + 0.15 * r['T3'] + 0.15 * r['STQ'] +
                       0.10 * r['vpq'] + 0.15 * (100 if r['conf'] else 0))
    # 题材核心：同一最强题材内 qscore 前2视为核心成员
    _mark_theme_core(recs)
    for r in recs:
        r['decision'], r['dec_info'] = _final_decision_v11(r)
    # Quality Rank / Trade Rank 分离
    quality_rank = sorted([r for r in recs if r['decision'] != 'AVOID'],
                          key=lambda x: -x['qscore'])
    for i, r in enumerate(quality_rank, 1):
        r['quality_rank'] = i
    prio = {'PRIMARY BUY': 0, 'CONDITIONAL_BREAKOUT': 1, 'CONDITIONAL_RETEST': 1,
            'WAIT_BREAKOUT': 2, 'WAIT_RETEST': 2, 'WAIT_CONFIRMATION': 2,
            'WAIT_HIGH_EXTENSION': 3, 'WATCH_HIGH_EXTENSION': 4, 'WATCH_WEAK_STRUCTURE': 4,
            'WATCH_LOW_STQ': 4, 'AVOID': 9}
    recs.sort(key=lambda x: (prio.get(x['decision'], 5), -x['tscore']))
    top10 = [r for r in recs if x_rankable(r['decision'])][:10]
    for i, r in enumerate(top10, 1):
        r['trade_rank'] = i

    say(f'  分层统计：送入 {stat["送入"]} 只 → 硬门控通过 {len(recs)} 只参与全维评分')
    dparts = [f'{k}={v}' for k, v in sorted(stat.items()) if k != '送入']
    if dparts:
        say('  淘汰原因：' + '、'.join(dparts))
    # TOP10 主表
    say('')
    say('══════ TOP 10（TradeRank 优先于 QualityRank）══════')
    say(f'{"#":<3}{"代码":<11}{"名称":<9}{"热榜":>5}{"STQ":>5}{"VPQ":>5}{"T1":>4}{"T3":>4}'
        f'  {"结构":<11}{"扩展":<6}{"Entry":>6}{"质排":>5}{"交排":>5}  决策')
    say('─' * 108)
    for i, r in enumerate(top10, 1):
        say(f'{i:<3}{r["code"]:<11}{r["name"]:<9}{r["hot_rank"]:>5}{r["STQ"]:>5.0f}{r["vpq"]:>5.0f}'
            f'{r["T1"]:>4.0f}{r["T3"]:>4.0f}  {r["struct_c"]:<11}{r["ext_lvl"]:<6}'
            f'{r["ENTRY"]:>6.0f}{r.get("quality_rank", 0):>5}{r.get("trade_rank", 0):>5}  {r["decision"]}')
    say('')
    # 每只 TOP 详细输出
    say('══════ TOP 详细（价格完全分离：突破/回踩/支撑/止损）══════')
    for r in top10:
        say('─' * 70)
        say(f'  {r["name"]}  {r["code"]}')
        say(f'  题材：{r["themes_short"]}（{len(r["themes"])}个，主线题材{getattr(r, "main_cnt", 0)}个）')
        tt = f'{r["turn_today"]:.2f}%' if r.get('turn_today') is not None else '—'
        say(f'  HotRank：{r["hot_rank"]}   最强题材热度{r["max_hot"]:.0f}   题材核心：{"是" if r.get("is_core") else "否"}'
            f'   共振：{"多题材" if len(r["themes"]) >= 2 else "单一"}')
        say(f'  STQ={r["STQ"]:.0f}  VPQ={r["vpq"]:.0f}({r["vp_grade"]})  T1={r["T1"]:.0f}  T3={r["T3"]:.0f}({r["cont_state"]})')
        say(f'  结构={r["struct_c"]}({r["grade"]})  趋势={r["trend"]}  量价状态={r["vp_state"]}  扩展={r["ext_lvl"]}(危{r["ext_val"]:.0f})')
        say(f'  PriceConfirmation={r["conf_tag"]}')
        say(f'  BREAKOUT_TRIGGER={r["bo_trigger"]:.2f}  RETEST_ENTRY={r["retest_entry"]:.2f}  SUPPORT={r["support"]:.2f}  STOP={r["stop"]:.2f}  R/R={r["rr"]:.2f}')
        say(f'  SignalCompleteness={r["completeness"]:.0f}/100   QualityRank={r.get("quality_rank", "—")}   TradeRank={r.get("trade_rank", "—")}   Decision={r["decision"]}')
        say(f'  解读：{r["dec_info"]["one_line"]}')
        if not r['conf']:
            say(f'  等待：{r["dec_info"]["wait_what"]}')
    say('')
    # 执行清单
    say('══════ 最终执行清单 ══════')
    say('【今日最值得买】PRIMARY BUY')
    for r in [x for x in recs if x['decision'] == 'PRIMARY BUY']:
        say(f'  ◆ {r["name"]}({r["code"]}) STQ{r["STQ"]:.0f} VPQ{r["vpq"]:.0f} T1{r["T1"]:.0f} '
            f'{r["struct_c"]} | {r["dec_info"]["one_line"]}')
    say('【回踩最值得买】PRIMARY/确认回踩 与 CONDITIONAL_RETEST')
    for r in [x for x in recs if x['decision'] == 'CONDITIONAL_RETEST']:
        say(f'  ◇ {r["name"]}({r["code"]}) STQ{r["STQ"]:.0f} 回踩位≈{r["retest_entry"]:.2f} 支撑{r["support"]:.2f} 止损{r["stop"]:.2f}')
        say(f'      {r["dec_info"]["wait_what"]}')
    say('【突破确认后可以买】WAIT_BREAKOUT 与 CONDITIONAL_BREAKOUT')
    for r in [x for x in recs if x['decision'] in ('WAIT_BREAKOUT', 'CONDITIONAL_BREAKOUT')]:
        say(f'  ○ {r["name"]}({r["code"]}) STQ{r["STQ"]:.0f} 突破触发≈{r["bo_trigger"]:.2f} 现价{r["em"]["close"]:.2f}'
            f' | {r["dec_info"]["wait_what"]}')
    say('【强但不能追】WATCH_HIGH_EXTENSION / WAIT_HIGH_EXTENSION')
    for r in [x for x in recs if x['decision'] in ('WATCH_HIGH_EXTENSION', 'WAIT_HIGH_EXTENSION')][:15]:
        say(f'  × {r["name"]}({r["code"]}) STQ{r["STQ"]:.0f} 扩展{r["ext_lvl"]}(危{r["ext_val"]:.0f}) {r["decision"]} | {r["dec_info"]["one_line"]}')
    say('【弱结构】WATCH_WEAK_STRUCTURE / WATCH_LOW_STQ')
    for r in [x for x in recs if x['decision'] in ('WATCH_WEAK_STRUCTURE', 'WATCH_LOW_STQ')][:15]:
        say(f'  × {r["name"]}({r["code"]}) STQ{r["STQ"]:.0f} {r["struct_c"]}({r["grade"]}) {r["decision"]} | {r["dec_info"]["one_line"]}')
    say('【禁止交易】AVOID')
    for r in [x for x in recs if x['decision'] == 'AVOID'][:15]:
        say(f'  ✕ {r["name"]}({r["code"]}) STQ{r["STQ"]:.0f} {r["decision"]} | {r["dec_info"]["one_line"]}')
    say('')
    say('口径：候选来自KPL有价值题材；原始价K线(未除权)；无 Confirmation 不 PRIMARY；扩展极高不追；Quality 找强股，Trade 定买什么。')
    say(LINE)
    if save:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f'kpl_引擎V1.1_{today}.txt')
        with open(fname, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print(f'[已归档] {fname}')


def x_rankable(decision):
    return decision not in ('WATCH_HIGH_EXTENSION', 'WATCH_WEAK_STRUCTURE', 'WATCH_LOW_STQ', 'AVOID')


def _mark_theme_core(recs):
    """同一最强题材内 qscore 前2 标记为核心成员"""
    from collections import defaultdict
    by_theme = defaultdict(list)
    for r in recs:
        for tcode in r.get('theme_codes', []):
            by_theme[tcode].append(r)
    for lst in by_theme.values():
        lst.sort(key=lambda x: -x['qscore'])
        for j, r in enumerate(lst[:2], 1):
            if r.get('is_core') is None:
                r['is_core'] = (r.get('is_core') or 0) + (1 if j == 1 else 0.5)
    for r in recs:
        if r.get('is_core') is None:
            r['is_core'] = False


# ══════════════ V1.1 评分/延续/确认/完备性/决策 ══════════════
# 结构规范名：BREAKOUT / PULLBACK / TREND_CONTINUATION / BASE / RECOVERY / DISTRIBUTION / BREAKDOWN
_STRUCT_CANON = {'BREAKOUT': 'BREAKOUT', 'BREAKOUT_RETEST': 'PULLBACK', 'PULLBACK': 'PULLBACK',
                 'TREND_CONTINUATION': 'TREND_CONT', 'ACCUMULATION': 'BASE',
                 'RANGE_BREAK_PREPARATION': 'BASE', 'RANGE': 'BASE', 'TREND_RECOVERY': 'RECOVERY',
                 'HIGH_LEVEL_EXPANSION': 'HIGH_LEVEL', 'OVEREXTENSION': 'HIGH_LEVEL'}


def _path_of(struct_c):
    if struct_c == 'BREAKOUT':
        return 'BREAKOUT'
    if struct_c in ('PULLBACK', 'BASE', 'RECOVERY'):
        return 'RETEST'
    if struct_c == 'TREND_CONT':
        return 'CONTINUATION'
    return 'WEAK'


def _score_v11(r):
    """V1.1 全维评分：STQ(新六维)/T1/T3延续/价格确认/完备性/Entry"""
    em = r['em']
    cur = em['close']
    r['struct_c'] = _STRUCT_CANON.get(r['structure'], r['structure'])
    r['path'] = _path_of(r['struct_c'])
    # ── STQ 六维：Trend20 Structure20 Momentum15 RS15 Position15 Theme15（不含热度）──
    tq = {'TREND_UP': 90, 'TREND_RECOVERY': 78, 'TREND_WEAK': 45}.get(r['trend'], 50)
    if em['ma20_slope'] >= 0.5:
        tq += 5
    tq = min(100, tq)
    ps = 78 if r['grade'] == 'A' else 92 if r['grade'] == 'S' else 55
    if r['struct_c'] in ('BREAKOUT', 'PULLBACK') and r['grade'] == 'S':
        ps = 94
    mnt = 55
    if em['chg5'] >= 6:
        mnt = 80 + min(15, em['chg5'])
    elif em['chg5'] > 0:
        mnt = 72
    elif em['chg5'] > -3:
        mnt = 60
    else:
        mnt = 45
    if em['ups'] >= 3:
        mnt += 8
    mnt = min(100, mnt)
    r60 = em['chg60']
    rs = 92 if em['dist_hh60'] > -3 else 85 if r60 > 25 else 76 if r60 > 10 else 62 if r60 > 0 else 45
    dh = em['dist_hh60']
    pos = 92 if dh > -2 else 84 if dh > -10 else 72 if dh > -20 else 60 if dh > -35 else 42
    n_theme = len(r['themes'])
    theme_align = 62 if n_theme == 1 else 82 if n_theme == 2 else 92
    if r.get('main_cnt', 0) >= 1:
        theme_align = min(100, theme_align + 12)
    r['STQ'] = 0.20 * tq + 0.20 * ps + 0.15 * mnt + 0.15 * rs + 0.15 * pos + 0.15 * theme_align
    # ── 价格确认 & 四价分离 ──
    sup, stop, trig = r['support'], r['stop'], r['trigger']
    bo_trig = max(em['hh20_ex'] * 1.005, cur)          # 突破确认价
    retest_entry = max(sup, em['ma'][20] if em['ma'][20] <= cur else sup)
    r['bo_trigger'], r['retest_entry'] = bo_trig, retest_entry
    r['rr'] = max(em['hh60'], cur * 1.12) - cur
    r['rr'] = r['rr'] / max(cur - stop, 1e-9)
    conf = _confirmation(r, em, bo_trig, retest_entry, sup)
    r.update(conf)
    # ── Entry 六维（不重复计 Trend/Structure）──
    d_zone = max(cur / retest_entry - 1, 0) * 100
    loc = 95 if d_zone <= 0.5 else 85 if d_zone <= 2 else 70 if d_zone <= 5 else 50 if d_zone <= 10 else 35
    if conf['conf'] and r['path'] == 'BREAKOUT':
        loc = 95
    d_trig = max(cur / bo_trig - 1, 0) * 100
    e_trig = 88 if d_trig <= 1 else 75 if d_trig <= 3 else 60 if d_trig <= 8 else 45
    if r['path'] == 'RETEST':
        retq = 95 if (d_zone <= 2.5 and em['vol1'] < 1.0 and r['vpq'] >= 75) else \
               75 if (d_zone <= 2.5 and em['vol1'] < 1.2) else 55 if d_zone <= 6 else 35
    elif r['path'] == 'BREAKOUT':
        retq = 90 if (conf['conf'] and em['clv'] >= 0.7) else 70 if cur > em['hh20_ex'] else 55
    elif r['path'] == 'CONTINUATION':
        retq = 82 if (r['vpq'] >= 75 and em['clv'] >= 0.5) else 62
    else:
        retq = 50
    rr_score = min(100, 30 + r['rr'] * 22)
    c_score = 100 if conf['conf'] else 35
    e_ext = max(0, 100 - r['ext_val'] * 0.9)
    r['ENTRY'] = (0.25 * loc + 0.15 * e_trig + 0.15 * retq + 0.15 * rr_score +
                  0.15 * c_score + 0.15 * e_ext)
    # ── T1 / T3 延续 ──
    _continuation(r, em, tq, rs, pos)
    # ── SignalCompleteness ──
    items = [r['trend'] in ('TREND_UP', 'TREND_RECOVERY'),
             r['grade'] in ('S', 'A'),
             r['vpq'] >= 75,
             r['vp_state'] not in ('放量下跌', '放量滞涨', '无效放量', '缩量阴跌'),
             r['T1'] >= 75,
             r['T3'] >= 70,
             r['ext_lvl'] in ('LOW', 'MEDIUM'),
             pos >= 70 and em['dist_ma20'] <= 12,
             all(x > 0 for x in (r['bo_trigger'], r['retest_entry'], sup, stop)),
             r['rr'] >= 1.5]
    r['completeness'] = sum(items) / len(items) * 100


def _confirmation(r, em, bo_trig, retest_entry, sup):
    """第八层 Price Confirmation：按路径分别确认"""
    path, cur = r['path'], em['close']
    vr, clv = em['vol1'], em['clv']
    if path == 'BREAKOUT':
        ok = (cur > bo_trig * 0.998 and vr >= 1.15 and clv >= 0.70 and
              em['upper_shadow'] < 3.5 and r['ext_lvl'] in ('LOW', 'MEDIUM'))
        tag = 'CONFIRMED_BREAKOUT' if ok else 'WAIT_BREAKOUT'
        why = ('放量越过突破触发且强收盘' if ok else
               '价格在触发附近但量/CLV/上影未达突破确认标准')
        return {'conf': ok, 'conf_tag': tag, 'conf_why': why}
    if path == 'RETEST':
        d_zone = max(cur / retest_entry - 1, 0) * 100
        ok = (d_zone <= 2.5 and vr < 1.05 and cur > sup and
              (em['ret_today'] >= -1.8 or clv >= 0.45) and r['ext_lvl'] in ('LOW', 'MEDIUM'))
        tag = 'CONFIRMED_RETEST' if ok else 'WAIT_RETEST'
        why = ('价格进入回踩区且缩量止跌、支撑未破' if ok else
               '回踩未到位/未缩量/支撑存疑，等待止跌信号')
        return {'conf': ok, 'conf_tag': tag, 'conf_why': why}
    if path == 'CONTINUATION':
        ok = (r['vpq'] >= 75 and r['ext_lvl'] in ('LOW', 'MEDIUM') and cur > em['ma'][10] and
              r['vp_state'] not in ('放量滞涨', '无效放量') and r['rr'] >= 1.2)
        tag = 'CONFIRMED_CONT' if ok else 'WAIT_CONTINUATION'
        why = ('趋势完整且量价健康、风险收益合理' if ok else '趋势在但量价/位置需进一步确认')
        return {'conf': ok, 'conf_tag': tag, 'conf_why': why}
    return {'conf': False, 'conf_tag': 'WAIT_WEAK', 'conf_why': '弱结构路径无确认条件'}


def _continuation(r, em, tq, rs, pos):
    """第六层 T+1/T+3 延续评分：StructurePersistence25 VPQ20 Trend15 RS15 Position10 Ext反10 Hot5"""
    base_persist = {'BREAKOUT': 92, 'PULLBACK': 86, 'TREND_CONT': 84, 'BASE': 72,
                    'RECOVERY': 76, 'HIGH_LEVEL': 42}.get(r['struct_c'], 60)
    if r['grade'] == 'S' and r['struct_c'] in ('BREAKOUT', 'PULLBACK', 'TREND_CONT'):
        base_persist += 4
    if cur_ok := (em['close'] >= r['support']):
        base_persist += 3
    if r['vp_danger']:
        base_persist -= 30
    base_persist = max(10, min(100, base_persist))
    hot_mom = 45
    c1 = r.get('max_chg1', 0)
    if c1 >= 60:
        hot_mom = 95
    elif c1 >= 25:
        hot_mom = 85
    elif c1 >= 10:
        hot_mom = 72
    elif c1 > 0:
        hot_mom = 60
    t1 = (0.25 * base_persist + 0.20 * r['vpq'] + 0.15 * tq + 0.15 * rs + 0.10 * pos +
          0.10 * max(0, 100 - r['ext_val'] * 0.9) + 0.05 * hot_mom)
    t3 = t1
    if r['ext_lvl'] in ('HIGH', 'EXTREME'):
        t3 -= 10
    if r['vp_state'] in ('放量下跌', '放量滞涨', '无效放量'):
        t3 -= 8
    if em['close'] < r['support']:
        t3 -= 8
    if r['vp_state'] == '放量下跌':
        t3 -= 6
    if r['struct_c'] == 'BREAKOUT' and r['conf']:
        t3 += 5
    t3 = max(0, min(100, t3))
    r['T1'], r['T3'] = round(t1, 1), round(t3, 1)
    r['cont_state'] = ('A+延续' if t3 >= 85 else 'A延续' if t3 >= 75 else
                       'B延续' if t3 >= 65 else 'C弱续')


def _final_decision_v11(r):
    """第二十三层决策：可逐条解释的规则门控，禁止先算总分再强行解释"""
    em = r['em']
    info = {'one_line': '', 'wait_what': ''}
    liq_fail = em['amount'] < 1.5e8 or (em['mv'] is not None and em['mv'] < MV_MIN)
    # AVOID 硬条件
    if liq_fail:
        info['one_line'] = '流动性不足（成交额/市值不达标），禁止交易'
        return 'AVOID', info
    if r['vp_state'] == '放量下跌':
        info['one_line'] = '放量下杀，资金出逃'
        return 'AVOID', info
    if r['trend'] in ('BREAKDOWN',) or (r['trend'] == 'TREND_WEAK' and em['close'] < em['ma'][20] * 0.98):
        info['one_line'] = '破位/趋势恶化，禁止交易'
        return 'AVOID', info
    # Extension 禁追高（先于分数）
    if r['ext_lvl'] == 'EXTREME':
        if r['vpq'] >= 65 and r['grade'] in ('S', 'A'):
            info['one_line'] = f'扩展极高(危{r["ext_val"]:.0f})但质量尚可，仅观察等消化'
            info['wait_what'] = '等回踩至 RETEST_ENTRY 或 MA20 再评估'
            return 'WATCH_HIGH_EXTENSION', info
        info['one_line'] = f'极端扩张(危{r["ext_val"]:.0f})，禁止主动买入'
        return 'AVOID', info
    if r['ext_lvl'] == 'HIGH':
        info['one_line'] = f'强度存在但距MA20过远(危{r["ext_val"]:.0f})，禁止追高'
        info['wait_what'] = f'等回踩至 {r["retest_entry"]:.2f}~{r["support"]:.2f} 且缩量后再评估'
        return ('WAIT_HIGH_EXTENSION' if r['STQ'] >= 75 and r['T1'] >= 70 else 'WATCH_HIGH_EXTENSION'), info
    # 结构弱（C 级）早退
    if r['grade'] == 'C' or r['struct_c'] == 'HIGH_LEVEL':
        info['one_line'] = f'结构偏弱/高位扩张({r["struct_c"]} C级)，暂不进入交易候选'
        return 'WATCH_WEAK_STRUCTURE', info
    if r['STQ'] < 70:
        info['one_line'] = f'短线质量不足(STQ{r["STQ"]:.0f})'
        return 'WATCH_LOW_STQ', info
    # PRIMARY 十条件
    ok_primary = (r['STQ'] >= 80 and r['vpq'] >= 75 and r['T1'] >= 75 and r['T3'] >= 70 and
                  r['conf'] and r['ext_lvl'] in ('LOW', 'MEDIUM') and r['grade'] in ('S', 'A') and
                  r['completeness'] >= 80 and r['rr'] >= 1.8)
    if ok_primary:
        why = f'{r["conf_tag"]}确认，延续与买点俱佳'
        info['one_line'] = why
        return 'PRIMARY BUY', info
    # 质量好但确认不足 / 买点未熟
    strong = r['STQ'] >= 80 and r['vpq'] >= 75 and r['T1'] >= 75
    if strong and not r['conf']:
        if r['path'] == 'BREAKOUT':
            info['one_line'] = '突破方向质量好，但量/CLV未达突破确认'
            info['wait_what'] = f'等收盘站上 {r["bo_trigger"]:.2f} 且放量≥1.15倍、CLV≥0.70'
            return 'CONDITIONAL_BREAKOUT', info
        if r['path'] == 'RETEST':
            info['one_line'] = '回踩标的质量好，但回踩/止跌未确认'
            info['wait_what'] = f'等价格进入 {r["retest_entry"]:.2f} 附近且缩量止跌'
            return 'CONDITIONAL_RETEST', info
        info['one_line'] = '延续候选质量好，等待量价进一步确认'
        info['wait_what'] = '等放量站回关键均线或CLV转强'
        return 'WAIT_CONFIRMATION', info
    # 一般等待（质量中上、条件未满）
    wait_tag = {'BREAKOUT': 'WAIT_BREAKOUT', 'RETEST': 'WAIT_RETEST'}.get(r['path'], 'WAIT_CONFIRMATION')
    missing = []
    if r['STQ'] < 75:
        missing.append(f'STQ{r["STQ"]:.0f}')
    if r['vpq'] < 75:
        missing.append(f'VPQ{r["vpq"]:.0f}')
    if r['T1'] < 75:
        missing.append(f'T1{r["T1"]:.0f}')
    if r['rr'] < 1.5:
        missing.append(f'R/R{r["rr"]:.1f}')
    info['one_line'] = f'质量中上但条件未满（{"、".join(missing) if missing else "等待确认"}）'
    info['wait_what'] = (f'等突破：收盘站上 {r["bo_trigger"]:.2f} 放量' if r['path'] == 'BREAKOUT'
                         else f'等回踩：进入 {r["retest_entry"]:.2f} 附近缩量止跌')
    return wait_tag, info


# ══════════════════════════ V1.0 引擎内部 ══════════════════════════

def _gather_engine_candidates(pool, theme_mem, name_map, mv_map, today,
                              top_per_theme=4, max_cand=60):
    """从题材通过池收集候选股：每题材取 top_per_theme，按题材热度全局裁剪 max_cand"""
    from collections import defaultdict
    stat_by_theme = defaultdict(list)
    cache = {}
    for r in pool:
        members = theme_mem.get(r['code'])
        if not members:
            continue
        lst = []
        for cc in sorted(members):
            if cc not in cache:
                df = _pull_bars(cc, today)
                cache[cc] = df
            df = cache[cc]
            if df is None:
                continue
            m0, why0 = _stock_metrics(cc, name_map.get(cc, ''), df, mv_map.get(cc)) if len(df) >= MIN_BARS else (None, '行情不足')
            if m0 is None:
                continue
            sc0, _ = _score(m0)
            m0['name'] = name_map.get(cc, cc)
            m0['score'] = sc0
            if sc0 >= SCORE_FLOOR:
                lst.append(m0)
        lst.sort(key=lambda x: (-x['score'], x['ret_today']))
        for m in lst[:top_per_theme]:
            stat_by_theme[r['code']].append((r, m))
    # 汇总每只股票：题材集合、最高题材热度、主线题材数、题材热度日增幅
    stock = {}
    for tcode, items in stat_by_theme.items():
        for r, m in items:
            cc = m['code']
            d = stock.setdefault(cc, {'code': cc, 'name': m['name'], 'max_hot': 0, 'themes': [],
                                      'pick_best': 0, 'main_cnt': 0, 'max_chg1': 0})
            d['themes'].append((r['name'], r['code'], r['hot'],
                                bool(r.get('is_main')), r.get('chg1')))
            d['max_hot'] = max(d['max_hot'], r['hot'])
            d['pick_best'] = max(d['pick_best'], m['score'])
            if r.get('is_main'):
                d['main_cnt'] += 1
            if r.get('chg1') is not None:
                d['max_chg1'] = max(d['max_chg1'], r['chg1'])
    cands = sorted(stock.values(), key=lambda x: -x['max_hot'])[:max_cand]
    for c in cands:
        c['themes'].sort(key=lambda t: -t[2])
        c['themes_short'] = '、'.join(t[0] for t in c['themes'][:3])
        c['theme_codes'] = [t[1] for t in c['themes']]
    return cands


def _engine_metrics(code, today, mv):
    """扩展量价指标（供引擎分层使用）"""
    df = _pull_bars(code, today)
    if df is None or len(df) < 120:
        return None, '行情不足'
    if str(df['trade_date'].iloc[-1]) != str(today):
        return None, '当日无交易'
    close = df['close'].to_numpy(dtype=float)
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    open_ = df['open'].to_numpy(dtype=float)
    vol = df['vol'].to_numpy(dtype=float)
    amount = df.get('amount', pd.Series(np.nan)).to_numpy(dtype=float)
    n = len(close)
    prev_c = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = float(np.mean(tr[-14:]))
    cur, prev = close[-1], close[-2]
    ret_today = (cur / prev - 1) * 100
    if np.isnan(ret_today) or ret_today >= 9.0 or ret_today <= -9.5:
        return None, f'涨停/大跌剔除({ret_today:+.1f}%)'
    ma = {w: float(np.mean(close[-w:])) for w in (5, 10, 20, 60, 120)}
    ma5_slope = (ma[5] / (np.mean(close[-8:-3])) - 1) * 100 if n > 8 else 0
    ma20_slope = (ma[20] / (np.mean(close[-30:-10])) - 1) * 100 if n > 30 else 0
    hh20 = float(np.max(high[-20:]))
    hh20_ex = float(np.max(high[-21:-1])) if n > 21 else hh20
    ll20_prev = float(np.min(low[-21:-1])) if n > 21 else float(np.min(low[-20:]))
    hh60 = float(np.max(high[-60:]))
    ll60 = float(np.min(low[-60:]))
    chg5 = (cur / close[-6] - 1) * 100
    chg10 = (cur / close[-11] - 1) * 100
    chg20 = (cur / close[-21] - 1) * 100
    chg60 = (cur / close[-61] - 1) * 100 if n > 61 else (cur / close[0] - 1) * 100
    dist_ma20 = (cur / ma[20] - 1) * 100
    dist_ma60 = (cur / ma[60] - 1) * 100
    dist_hh20 = (cur / hh20 - 1) * 100
    dist_hh60 = (cur / hh60 - 1) * 100
    dist_ll60 = (cur / ll60 - 1) * 100
    vol1 = vol[-1] / max(float(np.mean(vol[-6:-1])), 1e-9)          # 当日量/前5日均量
    vr_5_20 = float(np.mean(vol[-5:])) / max(float(np.mean(vol[-20:])), 1e-9)
    clv = (cur - low[-1]) / max(high[-1] - low[-1], 1e-9)
    upper_shadow = (high[-1] - max(open_[-1], cur)) / max(prev, 1e-9) * 100
    close_pos = (cur - low[-1]) / max(high[-1] - low[-1], 1e-9)
    # 连续阳线 / 连涨
    ups = 0
    for k in range(n - 1, 0, -1):
        if close[k] >= close[k - 1]:
            ups += 1
        else:
            break
    # 近5日内是否已创突破（今日之前收盘破前20高）
    crossed5 = n > 26 and float(np.max(close[-6:-1])) > hh20_ex
    # 近5日连续回落天数
    downs = 0
    for k in range(n - 1, 0, -1):
        if close[k] < close[k - 1]:
            downs += 1
        else:
            break
    amount_last = amount[-1] * 1000 if not np.isnan(amount[-1]) else cur * vol[-1] * 100
    em = {
        'code': code, 'close': cur, 'open': open_[-1], 'high': high[-1], 'low': low[-1],
        'ret_today': ret_today, 'atr': atr, 'atr_pct': atr / cur * 100,
        'ma': ma, 'ma5_slope': ma5_slope, 'ma20_slope': ma20_slope,
        'hh20': hh20, 'hh20_ex': hh20_ex, 'll20_prev': ll20_prev,
        'hh60': hh60, 'll60': ll60, 'chg5': chg5, 'chg10': chg10, 'chg20': chg20, 'chg60': chg60,
        'dist_ma20': dist_ma20, 'dist_ma60': dist_ma60,
        'dist_hh20': dist_hh20, 'dist_hh60': dist_hh60, 'dist_ll60': dist_ll60,
        'vol1': vol1, 'vr_5_20': vr_5_20, 'clv': clv,
        'upper_shadow': upper_shadow, 'close_pos': close_pos,
        'ups': ups, 'downs': downs, 'crossed5': crossed5,
        'amount': amount_last, 'mv': mv,
        # 原始序列（VPQ 需要历史量/额/CLV 分位）
        'arr': {'close': close, 'high': high, 'low': low, 'open': open_,
                'vol': vol, 'amount': amount, 'ret_today': ret_today},
    }
    return em, None


def _classify_trend(em):
    """层2 价格趋势门控 → (TREND标签, 是否BREAKDOWN)"""
    ma = em['ma']
    if em['close'] < em['ll20_prev'] and em['close'] < ma[20] and em['vol1'] > 1.2:
        return 'BREAKDOWN', True
    if em['close'] < ma[60] * 0.96:
        return 'BREAKDOWN', True
    if ma[5] > ma[10] > ma[20] and em['ma20_slope'] > 0:
        return 'TREND_UP', False
    if em['close'] >= ma[20] and (ma[5] > ma[10] or em['ma5_slope'] > 0.2):
        return 'TREND_RECOVERY', False
    return 'TREND_WEAK', False


# ══════════════════ VPQ 量价质量增强模块 ══════════════════
# 五维各20%：Volume Trend / Price Efficiency / CLV / Turnover Quality / Expansion
# 顺序原则：Volume → Price Response → CLV → Trend → Structure
# 六状态：高质量上涨/健康上涨/缩量回踩/无效放量/放量滞涨/放量下跌/缩量阴跌
_VP_STATE_SHORT = {'高质量上涨': '强量价', '健康上涨': '健康上涨', '缩量回踩': '缩量回踩',
                   '无效放量': '无效放量', '放量滞涨': '放量滞涨', '放量下跌': '放量下跌',
                   '缩量阴跌': '缩量阴跌'}


def _percentile_rank(x, window):
    """x[-1] 在最近 window 序列中的百分位(0-100)"""
    base = x[-min(window, len(x)):]
    return float((base <= x[-1]).mean() * 100)


def _vpq(em, structure, trend, circ_mv):
    """VPQ 量价质量评分 → dict(五维分/vpq/分级/状态/解释)
    “成交量是否真正支持价格继续向上”——禁止 放量=好 / 缩量=好 的简单假设
    """
    a = em['arr']
    close = a['close']
    vol = np.asarray(a['vol'], dtype=float)
    amount = np.asarray(a['amount'], dtype=float)
    high = a['high']
    low = a['low']
    open_ = a['open']
    n = len(close)
    cur = close[-1]
    ret = em['ret_today']
    ma20 = em['ma'][20]
    up = ret > 0
    vr = em['vol1']                                  # 当日量/前5日均量
    vol_perc = _percentile_rank(vol, 60)
    # 换手率代理：amount(元) / 流通市值(元)，历史序列按当日流通市值近似
    has_turn = bool(circ_mv)
    if has_turn:
        circ_y = circ_mv * 1e4
        amount_y = np.where(np.isnan(amount), close * vol * 100.0, amount * 1000.0)
        turn_s = amount_y / circ_y * 100.0
        turn_today = float(turn_s[-1])
        turn_perc = _percentile_rank(turn_s, 60)
    else:
        turn_today, turn_perc = None, None
    # CLV
    rng = high[-1] - low[-1]
    clv = 0.5 if rng <= 1e-9 else (cur - low[-1]) / rng
    if clv >= 0.80:
        clv_band, clv_score = '强收盘', 100
    elif clv >= 0.65:
        clv_band, clv_score = '偏强', 85
    elif clv >= 0.40:
        clv_band, clv_score = '中性', 65
    elif clv >= 0.20:
        clv_band, clv_score = '偏弱', 42
    else:
        clv_band, clv_score = '冲高回落', 20
    # 量价效率 = |涨跌| / max(量比-1, 0.1)
    eff = abs(ret) / max(vr - 1, 0.1)
    if eff >= 60:
        eff_band, eff_score = 'HIGH', 95
    elif eff >= 15:
        eff_band, eff_score = 'GOOD', 85
    elif eff >= 5:
        eff_band, eff_score = 'MED', 68
    elif eff >= 2:
        eff_band, eff_score = 'LOW', 55
    else:
        eff_band, eff_score = '极低', 30          # 大量放量价格几乎不动
    # ① Volume Trend：量能是否处健康区间并配合趋势（非“量大=好”）
    if not has_turn:
        vt = 70
    elif vol_perc >= 95 and ret <= -0.5:
        vt = 20                                   # 天量出货
    elif vol_perc >= 95 and clv < 0.6:
        vt = 38                                   # 天量滞涨
    elif vol_perc >= 95:
        vt = 80
    elif em['vr_5_20'] < 0.8:
        vt = 88 if cur >= ma20 else 45            # 持续缩量但守住MA20=惜售
    elif em['vr_5_20'] <= 1.3:
        vt = 82
    elif em['vr_5_20'] <= 2.0:
        vt = 70
    else:
        vt = 50
    # ② Turnover Quality：换手活跃度与涨跌匹配
    if not has_turn:
        tq = 70
    elif turn_today > 25 and ret <= -0.5:
        tq = 20
    elif turn_today > 25:
        tq = 55
    elif turn_today >= 8:
        tq = 88 if up else 42
    elif turn_today >= 3:
        tq = 80
    elif turn_today >= 1:
        tq = 85
    else:
        tq = 70
    if turn_perc is not None and turn_perc >= 95 and ret < 0:
        tq -= 15
    tq = max(0, min(100, tq))
    # 状态判定（Volume → Price Response → CLV → Trend → Structure）
    upper_sh = em['upper_shadow']
    blow = vr > 2.0 and upper_sh > 3.0 and (high[-1] - max(open_[-1], cur)) / max(close[-2], 1e-9) > 0.03
    if up and vr >= 1.15:
        if blow or clv < 0.5:
            state = '放量滞涨'
        elif clv < 0.6:
            state = '无效放量'                     # 资金增但价格没吃住
        else:
            state = '高质量上涨'
    elif up and 0.85 <= vr < 1.15 and clv >= 0.5:
        state = '健康上涨'
    elif up:
        state = '量能中性'
    elif vr > 1.3:
        state = '放量下跌'
    elif vr < 1.0:
        if cur >= ma20 and structure in ('PULLBACK', 'BREAKOUT_RETEST', 'TREND_CONTINUATION'):
            state = '缩量回踩'
        elif cur < ma20:
            state = '缩量阴跌'
        else:
            state = '缩量整理'
    else:
        state = '量能中性'
    # ③ Expansion Quality（第五维核心）
    exp_score = {'高质量上涨': 92, '健康上涨': 86, '缩量回踩': 90, '量能中性': 70,
                 '无效放量': 38, '放量滞涨': 28, '放量下跌': 15, '缩量阴跌': 50,
                 '缩量整理': 72}.get(state, 60)
    vpq = (vt + eff_score + clv_score + tq + exp_score) / 5.0
    # 特殊结构加减分
    if structure == 'BREAKOUT' and vr >= 1.2 and clv >= 0.8:
        vpq += 10
    if trend == 'TREND_UP' and state == '缩量回踩' and cur >= ma20:
        vpq += 8
    if structure == 'BREAKOUT_RETEST' and vr < 1.0:
        vpq += 6
    if em['ups'] >= 3 and em['vr_5_20'] <= 1.4:
        vpq += 5
    if state == '放量下跌':
        vpq -= 10
    if vr >= 2.0 and upper_sh >= 4.0:
        vpq -= 8                                       # 巨量长上影
    if vr >= 2.0 and abs(ret) <= 0.8:
        vpq -= 8                                       # 暴量不涨
    if vr >= 1.8 and ret > 0 and upper_sh >= 4.0:
        vpq -= 6                                       # 放量上涨后滞涨
    if structure == 'BREAKOUT' and vr >= 3.5:
        vpq -= 5                                       # 突破量极端
    vpq = max(0.0, min(100.0, vpq))
    grade = 'A+' if vpq >= 85 else 'A' if vpq >= 75 else 'B' if vpq >= 65 else 'C' if vpq >= 50 else 'D'
    # 解释文本
    if state == '高质量上涨':
        interp = '资金增量推动价格有效上行（高效量价）'
    elif state == '健康上涨':
        interp = '温和量能配合上行，未见失控'
    elif state == '缩量回踩':
        interp = '健康缩量调整，未现资金撤退证据'
    elif state == '无效放量':
        interp = '量增价不涨，资金未转化为向上结果'
    elif state == '放量滞涨':
        interp = '放量但收盘疲弱/长上影，警惕高位派发'
    elif state == '放量下跌':
        interp = '放量下杀，资金出逃迹象'
    elif state == '缩量阴跌':
        interp = '缩量阴跌，承接不足需回避'
    else:
        interp = '量能中性，方向待价格确认'
    danger = state in ('放量下跌', '放量滞涨')
    return {'vp': _VP_STATE_SHORT.get(state, state), 'vp_state': state, 'vp_danger': danger,
            'vol_state': state, 'clv_band': clv_band, 'eff_band': eff_band,
            'vp_vol': round(vt, 1), 'vp_eff': round(eff_score, 1), 'vp_clv': round(clv_score, 1),
            'vp_turn': round(tq, 1), 'vp_exp': round(exp_score, 1),
            'vol_perc': round(vol_perc, 1), 'turn_today': round(turn_today, 2) if has_turn else None,
            'turn_perc': round(turn_perc, 1) if has_turn else None,
            'vpq': round(vpq, 1), 'vp_grade': grade, 'vp_why': interp}


def _classify_structure(em, trend, vp):
    """层4 结构识别 → (结构名, 等级 S/A/C/D)"""
    cur, ma = em['close'], em['ma']
    ret = em['ret_today']
    # 高位放量滞涨/回落 → TOPPING
    if em['chg60'] > 30 and em['dist_hh60'] > -8 and ret < -2 and \
            em['upper_shadow'] > 3 and em['vol1'] > 1.6:
        return 'TOPPING', 'D'
    # 连续2日放量下跌且贴近前高 → DISTRIBUTION
    if em['downs'] >= 2 and ret < -1 and em['vr_5_20'] > 1.2 and em['dist_hh60'] > -8:
        return 'DISTRIBUTION', 'D'
    # 跌破 → BREAKDOWN
    if trend == 'BREAKDOWN':
        return 'BREAKDOWN', 'D'
    if trend == 'TREND_WEAK' and ret < -3:
        return 'BREAKDOWN', 'D'
    # 突破：今日放量越过前20日平台高
    if cur > em['hh20_ex'] and em['vol1'] >= 1.1:
        return 'BREAKOUT', 'S'
    # 前5日已破平台，现价回踩平台附近且缩量 → 回踩确认
    if em['crossed5'] and 0 <= cur / em['hh20_ex'] - 1 <= 0.025 and em['vol1'] < 1.0:
        return 'BREAKOUT_RETEST', 'S'
    # 沿均线强势上行
    if trend == 'TREND_UP' and cur > ma[10] and em['dist_hh60'] > -4 and em['vol1'] <= 1.8:
        return 'TREND_CONTINUATION', 'S'
    # 前期上涨后的回调（自60日高点回撤5-25%，未破MA20，缩量）
    if em['dist_hh60'] <= -5 and em['dist_hh60'] >= -30 and cur >= ma[20]:
        if em['vr_5_20'] < 1.0:
            return 'PULLBACK', 'S'
        return 'PULLBACK', 'A'
    # 高位扩展/超涨
    if em['dist_ma20'] > 15 or em['chg20'] > 35:
        return 'HIGH_LEVEL_EXPANSION', 'C'
    # 蓄势/区间
    if trend == 'TREND_RECOVERY':
        return 'TREND_RECOVERY', 'A'
    if -8 <= em['chg20'] <= 15 and abs(em['ma20_slope']) < 0.8 and em['dist_hh60'] > -12:
        return 'ACCUMULATION', 'A'
    if cur > em['hh20_ex']:
        return 'RANGE_BREAK_PREPARATION', 'A'
    if em['dist_ma20'] > 25:
        return 'OVEREXTENSION', 'C'
    return 'RANGE', 'C'


def _classify_extension(em):
    """层5 Extension → (等级, 危险度0-100)"""
    d20 = em['dist_ma20']
    v = 0
    if em['chg5'] >= 22 or em['ups'] >= 5 or (em['vol1'] > 2.2 and em['ret_today'] > 3) or d20 > 25:
        lvl = 'EXTREME'
        v = 92 + min(8, d20)
    elif d20 > 12 or em['chg5'] >= 14:
        lvl = 'HIGH'
        v = 72 + min(18, d20)
    elif d20 > 4 or em['chg5'] >= 7:
        lvl = 'MEDIUM'
        v = 40 + d20 * 2.2
    else:
        lvl = 'LOW'
        v = max(8, d20 * 3)
    return lvl, float(min(100, v))


def _calc_support_stop_trigger(em, structure):
    """层7 辅助：支撑/止损/触发价（按结构定制）"""
    cur, ma = em['close'], em['ma']
    below = [x for x in (ma[5], ma[10], ma[20]) if x <= cur]
    support = max(below + [em['ll20_prev']]) if below else em['ll20_prev']
    if structure in ('BREAKOUT_RETEST', 'PULLBACK'):
        support = max(support, em['hh20_ex']) if em['hh20_ex'] < cur else support
    stop = support - max(em['atr'] * 1.0, cur * 0.02)
    if structure in ('BREAKOUT',):
        trigger = max(em['hh20_ex'] * 1.005, cur)
        trigger = min(trigger, cur)  # 已越过触发位，现价即触发
    elif structure in ('BREAKOUT_RETEST',):
        trigger = em['hh20_ex'] * 1.005
    else:
        trigger = max(support, cur * 0.985)
    return support, stop, trigger


def _engine_decide(cand, em, circ_mv):
    """对单只股票做趋势/量价(VPQ)/结构/Extension 初筛，命中硬性淘汰返回 None"""
    trend, bd = _classify_trend(em)
    if bd:
        return None
    structure, grade = _classify_structure(em, trend, None)
    if grade == 'D':
        return None
    ext_lvl, ext_val = _classify_extension(em)
    support, stop, trigger = _calc_support_stop_trigger(em, structure)
    q = _vpq(em, structure, trend, circ_mv)
    rec = dict(cand)
    rec.update(q)
    rec.update({
        'em': em, 'trend': trend,
        'structure': structure, 'grade': grade, 'ext_lvl': ext_lvl, 'ext_val': ext_val,
        'support': support, 'stop': stop, 'trigger': trigger,
    })
    return rec


if __name__ == '__main__':
    args = sys.argv[1:]
    if args and args[0] == 'engine':
        engine(args[1] if len(args) > 1 else None)
    else:
        pick(args[0] if args else None)
