# -*- coding: utf-8 -*-
"""
KPL 题材轮动监测模块（基于 tushare kpl_concept_cons）
═══════════════════════════════════════════════════════
数据层：独立 SQLite 快照库 d:\\mystock\\cache_daily\\kpl.db
  · kpl_snapshot: (trade_date, ts_code, con_code) 主键，记录题材-成分股映射/hot_num/desc
分析层：轮动监测报告
  · 题材热度总榜（当日 hot_num 排行 + 1日/5日变化）
  · 升温题材（热度陡峭上升 + 成分扩容方向）
  · 退潮警示（冲高回落 / 成分收缩）
  · 主线候选（连续高位 + 成分不断扩容）
  · 扩散方向（新进多个热点题材的成分股 = 资金可能切入的补涨方向）

用法:
  python kpl_monitor.py update             # 增量入库最近交易日（收盘后跑）
  python kpl_monitor.py backfill --days 60 # 历史回补近 N 个交易日
  python kpl_monitor.py report [DATE]      # 输出轮动报告（缺省取库中最新完整日），
                                           # 并归档到 report_daily/kpl_轮动_YYYYMMDD.txt
                                           # （加 --no-save 只打印不归档）
"""
import os
import sys
import time
import sqlite3
import argparse
import datetime
import pandas as pd

CACHE_DIR = r"D:\mystock\cache_daily"
DB_PATH = os.path.join(CACHE_DIR, "kpl.db")
PAGE_SIZE = 3000  # kpl_concept_cons 单次返回上限

try:
    from dotenv import load_dotenv
    _env = r"D:\mystock\config\.env"
    if not os.path.exists(_env):
        _env = r"D:\mystock\solo\.env"
    if os.path.exists(_env):
        load_dotenv(_env)
except Exception:
    pass
import tushare as ts
_pro = None


def get_pro():
    global _pro
    if _pro is None:
        _pro = ts.pro_api()
    return _pro


# ═══════════════════════════════════════════════
# 数据层：独立 kpl.db
# ═══════════════════════════════════════════════

def _conn():
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def init_db():
    with _conn() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS kpl_snapshot (
                "trade_date" TEXT NOT NULL,
                "ts_code"    TEXT NOT NULL,
                "name"       TEXT,
                "con_code"   TEXT NOT NULL,
                "con_name"   TEXT,
                "hot_num"    INTEGER,
                "desc"       TEXT,
                PRIMARY KEY ("trade_date", "ts_code", "con_code")
            )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kpl_date ON kpl_snapshot ("trade_date")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kpl_ts   ON kpl_snapshot ("ts_code", "trade_date")')
        conn.execute('''CREATE TABLE IF NOT EXISTS kpl_meta (
            "key" TEXT PRIMARY KEY, "value" TEXT)''')


def fetch_day(date, sleep_s=0.0):
    """分页拉全某交易日题材-成分快照（单页上限3000行）"""
    parts = []
    offset = 0
    while True:
        df = get_pro().kpl_concept_cons(**{
            "ts_code": "", "trade_date": date, "con_code": "",
            "limit": str(PAGE_SIZE), "offset": str(offset)
        }, fields=[
            "ts_code", "name", "con_name", "con_code",
            "trade_date", "desc", "hot_num",
        ])
        if df is None or df.empty:
            break
        parts.append(df)
        offset += len(df)
        if len(df) < PAGE_SIZE:
            break
        if sleep_s:
            time.sleep(sleep_s)
    if not parts:
        return None
    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates(subset=['trade_date', 'ts_code', 'con_code'])
    return out


def save_day(df):
    """整日快照入库（INSERT OR REPLACE，幂等）"""
    if df is None or df.empty:
        return 0
    df = df.copy()
    for col in ('name', 'con_name', 'desc'):
        if col in df.columns:
            df[col] = df[col].fillna('')
    if 'hot_num' in df.columns:
        df['hot_num'] = df['hot_num'].fillna(0).astype(int)
    cols = ['trade_date', 'ts_code', 'name', 'con_code', 'con_name', 'desc', 'hot_num']
    cols = [c for c in cols if c in df.columns]
    rows = [tuple(r) for r in df[cols].to_numpy()]
    with _conn() as conn:
        ph = ','.join(['?'] * len(cols))
        conn.executemany(
            f'INSERT OR REPLACE INTO kpl_snapshot ({",".join(cols)}) VALUES ({ph})', rows)
    return len(rows)


def update_day(date, silent=False):
    """拉取并入库单日快照"""
    t0 = time.time()
    df = fetch_day(date, sleep_s=0.1)
    if df is None or df.empty:
        if not silent:
            print(f'[kpl] {date} 无数据（非交易日/接口未更新）')
        return 0
    n = save_day(df)
    n_c = df['ts_code'].nunique()
    if not silent:
        print(f'[kpl] {date} 入库 {n} 行 / {n_c} 题材 / {time.time()-t0:.1f}s')
    return n


def trading_days(end_date, n):
    """返回截止 end_date 的最近 n 个交易日（升序）"""
    end = pd.Timestamp(str(end_date))
    start = (end - pd.Timedelta(days=n * 3 + 10)).strftime('%Y%m%d')
    cal = get_pro().trade_cal(exchange='SSE', start_date=start, end_date=str(end_date))
    if cal is None or cal.empty:
        return []
    days = sorted(cal.loc[cal['is_open'] == 1, 'cal_date'].astype(str).tolist())
    return days[-n:]


def backfill(n_days=60):
    """回补最近 n 个交易日快照"""
    end = trading_days(str(datetime.date.today()).replace('-', ''), 1)
    if not end:
        end = [str(datetime.date.today()).replace('-', '')]
    days = trading_days(end[-1], n_days)
    init_db()
    # 跳过已入库完整日
    have = set(day for day in days if count_day(day) > 0)
    todo = [d for d in days if d not in have]
    print(f'[kpl] 目标 {len(days)} 交易日，已入库 {len(have)}，待拉取 {len(todo)}：{todo[0]}~{todo[-1]}')
    ok = fail = 0
    for i, d in enumerate(todo, 1):
        try:
            n = update_day(d)
            if n:
                ok += 1
            else:
                fail += 1  # 无数据交易日
        except Exception as e:
            print(f'[kpl] {d} ERR {e!r}')
            fail += 1
            time.sleep(2)
        if i % 10 == 0:
            print(f'[kpl] 回补进度 {i}/{len(todo)}')
    print(f'[kpl] 回补完成：成功 {ok} 日，无数据/失败 {fail} 日')


def count_day(date):
    with _conn() as conn:
        row = conn.execute(
            'SELECT COUNT(*) FROM kpl_snapshot WHERE trade_date = ?', (str(date),)).fetchone()
    return row[0] if row else 0


def stored_dates():
    with _conn() as conn:
        rows = conn.execute(
            'SELECT trade_date, COUNT(*) FROM kpl_snapshot GROUP BY trade_date ORDER BY trade_date').fetchall()
    return rows


# ═══════════════════════════════════════════════
# 分析层：轮动监测报告
# ═══════════════════════════════════════════════

# 报告阈值（可调）
HOT_MIN = 2000          # 参与升温判定的题材最低热度（过滤噪声小题材）
UP_CHG = 50             # 1日热度增幅>=50% 视为陡峭升温
UP_5D = 30              # 相对5日均值增幅>=30% 亦可视为升温
COOL_CHG = -30          # 1日热度回落<=-30% 视为冲高回落
MAIN_STREAK = 3         # 高位持续至少N日
TOP_N = 15

LINE = '━' * 62
LINE2 = '─' * 62


def _agg_day(date):
    """某交易日题材聚合：hot / name / 成分集合"""
    with _conn() as conn:
        df = pd.read_sql_query(
            'SELECT ts_code, name, con_code, hot_num FROM kpl_snapshot WHERE trade_date = ?',
            conn, params=(str(date),))
    if df.empty:
        return None
    g = {}
    for code, name, con, hot in df.to_numpy():
        a = g.get(code)
        if a is None:
            g[code] = {'name': name, 'hot': int(hot or 0), 'members': set()}
        a = g[code]
        a['members'].add(con)
        if int(hot or 0) > a['hot']:
            a['hot'] = int(hot or 0)
    return g


def load_recent(dates):
    """返回 {date: {ts_code: agg}}，dates 升序"""
    out = {}
    for d in dates:
        agg = _agg_day(d)
        if agg:
            out[d] = agg
    return out


# 参与对比分析的“完整日”最低题材数（低于此视为接口数据不全日，避免失真）
MIN_CONCEPTS = 100


def _day_concept_count(date):
    with _conn() as conn:
        row = conn.execute(
            'SELECT COUNT(DISTINCT ts_code) FROM kpl_snapshot WHERE trade_date = ?',
            (str(date),)).fetchone()
    return row[0] if row else 0


def report(target_date=None, verbose=True, save=True):
    res = analyze_day(target_date, verbose=verbose)
    if res is None:
        print('[kpl] 无可分析的完整日（先执行 update / backfill）')
        return None
    if verbose:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            _render(res)
        text = buf.getvalue()
        sys.stdout.write(text)
        if save:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
            os.makedirs(out_dir, exist_ok=True)
            fname = os.path.join(out_dir, f'kpl_轮动_{res["today"]}.txt')
            with open(fname, 'w', encoding='utf-8') as f:
                f.write(text)
            print(f'[已归档] {fname}')
    return res


def analyze_day(target_date=None, verbose=False):
    """返回某完整交易日的题材分析（供 report 与个股筛选复用）

    Returns:
        dict(today, prev_day, rows, p75) 或 None
        rows 每行含 hot/chg1/vs5/member/add/drop/add_names/streak/net3，
        及分区标记 is_top15(热度总榜)/is_warm(升温)/is_main(主线候选)/is_cool(退潮)
    """
    init_db()
    days = [d for d, _ in stored_dates()]
    if not days:
        return None
    target_date = str(target_date or days[-1])
    if target_date not in days:
        target_date = days[-1]
    # 数据不全日（题材数明显偏少）仅保留可正常分析的最晚完整日
    if _day_concept_count(target_date) < MIN_CONCEPTS:
        full = [d for d in days if _day_concept_count(d) >= MIN_CONCEPTS]
        if not full:
            return None
        target_date = full[-1]
        if verbose:
            print(f'[kpl] 目标日数据不全，改用最近完整日 {target_date}')
    complete = [d for d in days if _day_concept_count(d) >= MIN_CONCEPTS]
    idx = complete.index(target_date)
    win = complete[max(0, idx - 5):idx + 1]          # 近6个完整交易日
    prev_day = complete[idx - 1] if idx > 0 else None
    data = load_recent(win)
    cur = data[target_date]
    rows, p75 = build_rows(cur, data, target_date, prev_day)
    # 分区标记
    hot_arr = pd.Series([a['hot'] for a in cur.values()])
    p75 = float(hot_arr.quantile(0.75)) if len(hot_arr) else 0.0
    top_codes = {r['code'] for r in sorted(rows, key=lambda r: -r['hot'])[:TOP_N]}
    for r in rows:
        r['is_top15'] = r['code'] in top_codes
        r['is_warm'] = r['hot'] >= HOT_MIN and (
            (r['chg1'] is not None and r['chg1'] >= UP_CHG) or
            (r['vs5'] is not None and r['vs5'] >= UP_5D))
        r['is_main'] = r['hot'] >= p75 and r['streak'] >= MAIN_STREAK and r['net3'] > 0
        r['is_cool'] = r['hot'] >= HOT_MIN and (
            (r['chg1'] is not None and r['chg1'] <= COOL_CHG) or
            (r['drop'] >= 10 and r['chg1'] is not None and r['chg1'] < 0))
    return {'today': target_date, 'prev_day': prev_day, 'rows': rows, 'p75': p75}


def build_rows(cur, data, today, prev_day):
    """题材热力/成分变动统计（无打印副作用），返回 (rows, p75)"""
    p75 = float(pd.Series([a['hot'] for a in cur.values()]).quantile(0.75)) if cur else 0.0
    prev_rows = data[prev_day] if prev_day in data else None
    rows = []
    for code, a in cur.items():
        hot = a['hot']
        prev = prev_rows.get(code) if prev_rows else None
        prev_hot = prev['hot'] if prev else None
        vals5 = [data[d][code]['hot'] for d in data
                 if d != today and code in data[d]]
        avg5 = (sum(vals5) / len(vals5)) if vals5 else None
        rows.append({
            'code': code, 'name': a['name'], 'hot': hot,
            'chg1': ((hot - prev_hot) / prev_hot * 100) if prev_hot else None,
            'vs5': ((hot - avg5) / avg5 * 100) if avg5 else None, 'avg5': avg5,
            'member': len(a['members']),
            'add': len(a['members'] - (prev['members'] if prev else set())),
            'drop': len((prev['members'] if prev else set()) - a['members']),
            'add_names': sorted(a['members'] - (prev['members'] if prev else set())),
        })
    ordered = sorted(data)

    def streak(code):
        n = 0
        for d in reversed(ordered):
            v = data[d].get(code)
            if v is None:
                break
            if n == 0 or v['hot'] >= cur[code]['hot'] * 0.7:
                n += 1
            else:
                break
        return n

    def net_add3(code):
        total = 0
        for i in range(1, len(ordered)):
            pre = data[ordered[i - 1]].get(code)
            cc = data[ordered[i]].get(code)
            if pre and cc:
                total += len(cc['members'] - pre['members'])
        return total

    for r in rows:
        r['streak'] = streak(r['code'])
        r['net3'] = net_add3(r['code'])
    return rows, p75


def _render(res):
    rows = res['rows']
    today, prev_day, p75 = res['today'], res['prev_day'], res['p75']

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    print(LINE)
    print('  KPL 题材轮动监测报告')
    print(f'  交易日 {today}' + (f'（对比 {prev_day}）' if prev_day else '（库中无前一交易日，变化率为空）') + f'  生成于 {now}')
    print(LINE)

    # ① 热度总榜
    top = sorted(rows, key=lambda r: -r['hot'])[:TOP_N]
    print(f'① 题材热度总榜 TOP{TOP_N}（hot_num 降序）')
    print(LINE2)
    print(f'{"名次":<4}{"题材":<12}{"hot":>7}{"1日%":>8}{"vs5日%":>8}{"成分":>5}{"增/减":>7}')
    for i, r in enumerate(top, 1):
        c1 = f'{r["chg1"]:+.0f}' if r['chg1'] is not None else '—'
        c5 = f'{r["vs5"]:+.0f}' if r['vs5'] is not None else '—'
        print(f'{i:<4}{r["name"]:<12}{r["hot"]:>7}{c1:>8}{c5:>8}{r["member"]:>5}{r["add"]:>+5}/{r["drop"]:<2}')

    # ② 升温题材
    warm = [r for r in rows if r['is_warm']]
    warm.sort(key=lambda r: -max(r['chg1'] or 0, r['vs5'] or 0))
    print(f'\n② 升温题材（hot≥{HOT_MIN}，1日≥{UP_CHG}% 或 vs5日均值≥{UP_5D}%）共 {len(warm)} 个')
    print(LINE2)
    for r in warm[:12]:
        c1 = f'{r["chg1"]:+.0f}%' if r['chg1'] is not None else '—'
        c5 = f'{r["vs5"]:+.0f}%' if r['vs5'] is not None else '—'
        exp = f' 新进{len(r["add_names"])}只'
        print(f'  {r["name"]}({r["code"]}) hot {r["hot"]}  1日 {c1}  vs5日 {c5}  成分{r["member"]}{exp}')

    # ③ 主线候选（高位持续 + 成分扩容）
    mains = [r for r in rows if r['is_main']]
    mains.sort(key=lambda r: (-r['streak'], -r['hot']))
    print(f'\n③ 主线候选（hot≥当日P75[{p75:.0f}]，连续≥{MAIN_STREAK}日高位，近3日成分净增）共 {len(mains)} 个')
    print(LINE2)
    for r in mains[:10]:
        print(f'  {r["name"]}({r["code"]}) hot {r["hot"]}  持续{r["streak"]}日  近3日净增成分{r["net3"]}  今日成分{r["member"]}')

    # ④ 退潮警示
    cool = [r for r in rows if r['is_cool']]
    cool.sort(key=lambda r: r['chg1'] or 0)
    print(f'\n④ 退潮警示（1日回落≤{COOL_CHG}%，或单日成分收缩≥10只）共 {len(cool)} 个')
    print(LINE2)
    for r in cool[:10]:
        c1 = f'{r["chg1"]:+.0f}%' if r['chg1'] is not None else '—'
        print(f'  {r["name"]}({r["code"]}) hot {r["hot"]}  1日 {c1}  成分 {r["member"]}（-{r["drop"]}）')

    # ⑤ 扩散方向：新进多个热点题材的成分股
    if warm or mains:
        hot_codes = {r['code'] for r in warm[:10]} | {r['code'] for r in mains[:10]}
        from collections import defaultdict
        spread = defaultdict(list)
        for r in rows:
            if r['code'] in hot_codes:
                for s in r['add_names']:
                    spread[s].append(r['name'])
        top_spread = sorted(spread.items(), key=lambda kv: -len(kv[1]))[:20]
        print(f'\n⑤ 扩散方向 TOP20（今日新进 ≥2 个升温/主线题材的成分股）')
        print(LINE2)
        shown = 0
        for stock, names in top_spread:
            if len(names) < 2:
                break
            shown += 1
            print(f'  {stock:<12}{"、".join(names)}')
        if not shown:
            print('  （无同时进入多个热点题材的新增成分股）')
    print(LINE)
    print('口径说明：1日%为相对上一交易日 hot 变化；vs5日%为相对前5个已入库交易日均值；')
    print('“持续N日”回溯最近连续不显著降温的交易日数；“近3日净增”为前3日新增成分股累计。')


# ═══════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='KPL 题材轮动监测')
    ap.add_argument('cmd', choices=['update', 'backfill', 'report'], nargs='?', default='report')
    ap.add_argument('date', nargs='?', default=None, help='目标交易日 YYYYMMDD')
    ap.add_argument('--days', type=int, default=60, help='backfill 回补交易日数')
    ap.add_argument('--no-save', action='store_true', help='report 不归档 txt（默认归档到 report_daily/）')
    args = ap.parse_args()

    if args.cmd == 'update':
        init_db()
        today = trading_days(str(datetime.date.today()).replace('-', ''), 1)
        target = args.date or (today[-1] if today else str(datetime.date.today()).replace('-', ''))
        update_day(str(target))
    elif args.cmd == 'backfill':
        backfill(args.days)
    else:
        report(args.date, save=not args.no_save)


if __name__ == '__main__':
    main()
