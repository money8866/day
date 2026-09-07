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


if __name__ == '__main__':
    date = sys.argv[1] if len(sys.argv) > 1 else None
    pick(date)
