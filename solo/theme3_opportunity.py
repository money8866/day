#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
theme3_opportunity.py —— theme3 新机会主题识别 + 龙头/中军 + AI 日报
════════════════════════════════════════════════════════════

回答一个问题：**在今天的 theme3 体系里，哪些子主题是「新机会」？谁在领涨？**

输入（只读）：
    report_daily/theme3_heat_{date}.json            ← theme3_heat.py 产物（缺失时自动调用）
    cache_backbone_tushare/theme3_constituents_{date}.json
    cache_daily/stock_data.db → daily_cache         ← 当日 pct_chg / amount

三层分析（口径固定、可复现，不含主观权重调节）：

  ① 市场风格层（一级目录）
     把 14 个目录分为「价值防御 / 成长进攻」两组，比较三窗口 Heat 均值，
     差值 > +10 → 防御占优；< -10 → 成长占优；否则均衡。
     用途：新机会若属成长组、而当日风格偏防御，打 STYLE_CONFLICT 标记。

  ② 新机会评分（子主题，0~100）
        跃升分 25 = clamp(本周名次 − 今日名次, 0, 40) / 40 × 25
        结构分 35 = [涨幅P50(15) + 上涨占比(8) + 强涨占比(7)] 原始 0~30，按 35/30 缩放
        中期分 20 = 本月名次 ≤10→20 / ≤20→14 / ≤30→8 / 其他→0
        量能分 10 = 量能比 ≥1.0→10 / ≥0.9→7 / ≥0.8→4 / 其他→0
        未消耗分 10 = 今日涨停 0→10 / 1~2→7 / 3~4→3 / ≥5→0
      扣分：
        NO_ELASTICITY −20  上涨占比≥95% 且 强涨<5%（无弹性普涨，避险式撒钱）
        HIGH_POSITION −10  本月名次≤3 且 本月Heat≥90（已在中后期）
        样本门槛             20≤N<30 分数 ×0.9；N<20 不参与排名

      类型标签：
        NEW_START  跃升≥15 且 今日名次≤10 且 本月名次>10（低位新启动）
        RESUME     跃升≥15 且 今日名次≤10 且 本月名次≤10（强势再启动）
        ACCEL      跃升 5~14 且 今日≤10 且 本月≤15（趋势加速）
        OBSERVE    其他（仅观察）

  ③ 龙头 / 中军（对候选子主题）
      龙头：role=='龙头' 按今日涨幅取前2；无标签则取 链距≤1 且 今日上涨，
           按「今日涨幅分位×0.6 + 成交额分位×0.4」推举前2（标注「推举」）
      中军：role=='中军' 按总市值取前2（先排除已选龙头）；标签不足 2 只时
           用 链距≤1 池按市值补齐（补入的标注「推举」）；完全无标签则按市值推举
      资金聚焦：今日成交额占该主题内总成交额比例前3

输出：
    report_daily/theme3_opportunity_{date}.html   ← 邮件正文（内联样式）
    report_daily/theme3_opportunity_{date}.md
    report_daily/theme3_opportunity_{date}.json

邮件：
    沿用项目内既有通道 Agent Mail CLI（agently-cli），默认发往 MAIL_TO（stock1975@qq.com）。
    缺 CLI 或发送失败时只打印日志、留存正文，不影响文件产出。

用法：
    python theme3_opportunity.py                 # 最近一个交易日，生成并发送
    python theme3_opportunity.py 20261026        # 指定交易日
    python theme3_opportunity.py 20261026 --no-ai     # 跳过 AI，用规则文本
    python theme3_opportunity.py 20261026 --no-email  # 只生成不发送
    python theme3_opportunity.py --if-today           # 定时任务用：非当日数据则跳过（不发信）
    python theme3_opportunity.py --top 8         # 候选数量

本程序只做机会识别与观察条件，不输出买卖建议、仓位、目标价。
"""

import os
import sys
import json
import subprocess
from datetime import datetime

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONST_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
DB_PATH = r'd:\mystock\cache_daily\stock_data.db'
ENV_PATH = r'd:\mystock\config\.env'

# ── 邮件推送（沿用项目内 Agent Mail CLI 约定）──
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

# ── 市场风格分组（可按需修改，改这里即改风格判断）──
DEFENSIVE_CATS = {'消费', '金融', '资源', '化工'}
GROWTH_CATS = {'AI', '半导体', '物理AI', '人形机器人', '商业航天',
               '低空经济', '新能源汽车', '新能源', '军工'}
NEUTRAL_CATS = {'生物医药'}      # 攻守混合，不参与风格均值，也不触发 STYLE_CONFLICT
STYLE_GAP = 10.0

# ── 评分参数 ──
# 跃升 25 / 结构 35：结构（当日强度）权重高于跃升（边际变化），避免「冷门一日反抽」压过「持续走强」
W_JUMP, W_STRUCT, W_MID, W_VOL, W_FRESH = 25, 35, 20, 10, 10
JUMP_CAP = 40
MIN_N = 20                 # 参与评分的最小成员数
FULL_N = 30                # 低于此值分数打 9 折
PENALTY_NO_ELASTIC = 20
PENALTY_HIGH_POS = 10
LEADER_TOP, ZHONGJUN_TOP, MONEY_TOP = 2, 2, 3

os.makedirs(REPORT_DIR, exist_ok=True)


# ═════════════════════ 基础工具 ═════════════════════

def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def f(v, d=1, sign=False):
    if v is None:
        return '—'
    try:
        return f'{v:+.{d}f}' if sign else f'{v:.{d}f}'
    except (TypeError, ValueError):
        return '—'


def pct(v, d=0):
    return '—' if v is None else f'{v * 100:.{d}f}%'


def rank_of(rec, wk):
    r = (rec.get(wk) or {}).get('rank')
    return r if r else 999


def heat_of(rec, wk):
    return (rec.get(wk) or {}).get('heat')


# ═════════════════════ 数据装载 ═════════════════════

def resolve_trade_date(arg=None):
    if arg:
        return str(arg)
    files = [fn[len('theme3_constituents_'):-len('.json')]
             for fn in os.listdir(CONST_DIR)
             if fn.startswith('theme3_constituents_') and fn.endswith('.json')]
    files = [d for d in files if len(d) == 8 and d.isdigit()]
    if not files:
        raise SystemExit('未找到 theme3_constituents_*.json，请先运行 theme3_constituents_v2.py')
    return max(files)


def ensure_heat(trade_date):
    path = os.path.join(REPORT_DIR, f'theme3_heat_{trade_date}.json')
    if os.path.exists(path):
        return path
    print(f'[Heat] 缺少 {os.path.basename(path)}，先调用 theme3_heat.py ...')
    r = subprocess.run([sys.executable, '-X', 'utf8', 'theme3_heat.py', trade_date],
                       cwd=BASE_DIR, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode != 0 or not os.path.exists(path):
        raise SystemExit(f'theme3_heat.py 执行失败：\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
    return path


def load_heat(trade_date):
    with open(ensure_heat(trade_date), encoding='utf-8') as f:
        return json.load(f)


def load_constituents(trade_date):
    path = os.path.join(CONST_DIR, f'theme3_constituents_{trade_date}.json')
    if not os.path.exists(path):
        raise SystemExit(f'成分股文件不存在：{path}')
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = {}
    for t in raw.get('themes', []):
        name = t.get('theme_name') or ''
        stocks = [dict(s) for s in (t.get('stocks') or []) if s.get('ts_code')]
        out[name] = {'top_category': t.get('top_category') or '', 'stocks': stocks}
    return out


def load_today_px(trade_date):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            'SELECT ts_code, pct_chg, amount FROM daily_cache WHERE trade_date=?',
            (trade_date,)).fetchall()
    finally:
        conn.close()
    return {r[0]: {'pct': r[1], 'amt': r[2]} for r in rows}


# ═════════════════════ ① 市场风格 ═════════════════════

def style_layer(heat):
    cats = heat['categories']

    def avg(group, wk):
        v = [heat_of(c, wk) for c in cats
             if c['theme'] in group and heat_of(c, wk) is not None]
        return round(sum(v) / len(v), 1) if v else None

    def avg_rank(group, wk):
        v = [rank_of(c, wk) for c in cats
             if c['theme'] in group and (c.get(wk) or {}).get('rank')]
        return round(sum(v) / len(v), 1) if v else None

    out = {}
    for wk in ('TODAY', 'WEEK', 'MONTH'):
        d_h, g_h = avg(DEFENSIVE_CATS, wk), avg(GROWTH_CATS, wk)
        out[wk] = {'defensive_heat': d_h, 'growth_heat': g_h,
                   'defensive_rank': avg_rank(DEFENSIVE_CATS, wk),
                   'growth_rank': avg_rank(GROWTH_CATS, wk),
                   'gap': None if (d_h is None or g_h is None) else round(d_h - g_h, 1)}

    gap = out['TODAY']['gap']
    if gap is None:
        verdict, tone = '数据不足，无法判断风格', 'neutral'
    elif gap > STYLE_GAP:
        verdict, tone = f'价值防御占优（防御组今日 Heat 均值领先 {gap:.1f}）', 'defensive'
    elif gap < -STYLE_GAP:
        verdict, tone = f'成长进攻占优（成长组今日 Heat 均值领先 {-gap:.1f}）', 'growth'
    else:
        verdict, tone = f'风格均衡（两组差距仅 {gap:.1f}）', 'neutral'
    out['verdict'] = verdict
    out['tone'] = tone
    return out


# ═════════════════════ ② 新机会评分 ═════════════════════

def _struct_score(t):
    s = 0
    p50 = t.get('p50')
    if p50 is not None:
        s += 10 if p50 > 0 else 0
        s += 5 if p50 >= 0.5 else 0
    up = t.get('up_ratio')
    if up is not None:
        s += 6 if up >= 0.60 else 0
        s += 2 if up >= 0.70 else 0
    st = t.get('strong_ratio')
    if st is not None:
        s += 4 if st >= 0.08 else 0
        s += 3 if st >= 0.15 else 0
    return s


def _mid_score(month_rank):
    if month_rank <= 10:
        return 20
    if month_rank <= 20:
        return 14
    if month_rank <= 30:
        return 8
    return 0


def _vol_score(act):
    if act is None:
        return 0
    if act >= 1.0:
        return 10
    if act >= 0.9:
        return 7
    if act >= 0.8:
        return 4
    return 0


def _fresh_score(limit_up):
    n = limit_up or 0
    if n == 0:
        return 10
    if n <= 2:
        return 7
    if n <= 4:
        return 3
    return 0


def score_candidates(heat):
    cat_rank = {c['theme']: rank_of(c, 'TODAY') for c in heat['categories']}
    out = []
    for r in heat['subtopics']:
        t, w, m = r['TODAY'], r['WEEK'], r['MONTH']
        if not t.get('rank'):
            continue
        n = t.get('n') or 0
        if n < MIN_N:
            continue

        jump = (w['rank'] - t['rank']) if w.get('rank') else 0
        s_jump = min(max(jump, 0), JUMP_CAP) / JUMP_CAP * W_JUMP
        s_struct = round(_struct_score(t) / 30 * W_STRUCT, 1)   # _struct_score 原始 0~30，按权重缩放
        s_mid = _mid_score(rank_of(r, 'MONTH'))
        s_vol = _vol_score(t.get('activity'))
        s_fresh = _fresh_score(t.get('limit_up_n'))

        penalty, risks = 0, []
        up, st = t.get('up_ratio'), t.get('strong_ratio')
        if up is not None and st is not None and up >= 0.95 and st < 0.05:
            penalty += PENALTY_NO_ELASTIC
            risks.append('NO_ELASTICITY')
        if rank_of(r, 'MONTH') <= 3 and (heat_of(r, 'MONTH') or 0) >= 90:
            penalty += PENALTY_HIGH_POS
            risks.append('HIGH_POSITION')
        if (t.get('activity') or 0) < 0.85:
            risks.append('VOLUME_SHRINK')
        if (w.get('p50') is not None) and w['p50'] < 0:
            risks.append('RECLAIM_DAY1')
        if cat_rank.get(r['top_category'], 999) > 8:
            risks.append('ISOLATED')
        if n < FULL_N:
            risks.append('SMALL_SAMPLE')
        if r.get('quality_flag') == 'WARN':
            risks.append('MAPPING_WARN')
        if r['top_category'] in GROWTH_CATS and heat.get('style_tone') == 'defensive':
            risks.append('STYLE_CONFLICT')

        score = s_jump + s_struct + s_mid + s_vol + s_fresh - penalty
        if n < FULL_N:
            score *= 0.9
        score = round(score, 1)

        if jump >= 15 and t['rank'] <= 10:
            kind = 'RESUME' if rank_of(r, 'MONTH') <= 10 else 'NEW_START'
        elif jump >= 5 and t['rank'] <= 10 and rank_of(r, 'MONTH') <= 15:
            kind = 'ACCEL'
        else:
            kind = 'OBSERVE'

        out.append({
            'theme': r['theme'], 'top_category': r['top_category'],
            'n': n, 'score': score, 'kind': kind, 'jump': jump,
            'today_rank': t['rank'], 'week_rank': w.get('rank'), 'month_rank': m.get('rank'),
            'today_heat': t.get('heat'), 'week_heat': w.get('heat'), 'month_heat': m.get('heat'),
            'p50': t.get('p50'), 'p75': t.get('p75'),
            'up_ratio': t.get('up_ratio'), 'strong_ratio': t.get('strong_ratio'),
            'activity': t.get('activity'), 'limit_up_n': t.get('limit_up_n') or 0,
            'week_p50': w.get('p50'), 'week_up_ratio': w.get('up_ratio'),
            'month_p50': m.get('p50'),
            'quality_flag': r.get('quality_flag'),
            'risks': risks, 'parts': {'jump': round(s_jump, 1), 'struct': s_struct,
                                      'mid': s_mid, 'vol': s_vol, 'fresh': s_fresh,
                                      'penalty': penalty},
        })
    out.sort(key=lambda x: (-x['score'], x['today_rank'], x['theme']))
    return out


# ═════════════════════ ③ 龙头 / 中军 ═════════════════════

def _ranked(vals):
    """返回 0~1 分位（并列取平均）"""
    m = len(vals)
    if m <= 1:
        return [0.5] * m
    order = sorted(range(m), key=lambda i: vals[i])
    pos = [0.0] * m
    i = 0
    while i < m:
        j = i
        while j + 1 < m and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 / (m - 1)
        for k in range(i, j + 1):
            pos[order[k]] = avg
        i = j + 1
    return pos


def _stock_view(s, px):
    p = px.get(s['ts_code']) or {}
    return {'code': s['ts_code'], 'name': s.get('name') or '',
            'role': s.get('role') or '—',
            'chain_distance': s.get('chain_distance'),
            'today': p.get('pct'), 'amount': p.get('amt'),
            'd5': s.get('change_5d_pct'), 'd20': s.get('change_20d_pct'),
            'mv': s.get('total_mv_wan'), 'score': s.get('score'),
            'via': s.get('via')}


def pick_leaders(theme, cons, px):
    info = cons.get(theme)
    if not info:
        return {'leaders': [], 'zhongjun': [], 'money': [], 'leader_mode': 'NO_DATA'}
    rows = [_stock_view(s, px) for s in info['stocks']]
    rows = [r for r in rows if r['today'] is not None]
    if not rows:
        return {'leaders': [], 'zhongjun': [], 'money': [], 'leader_mode': 'NO_DATA'}

    # 龙头：有 role 标签直接用；否则按「链距优先 + 涨幅 + 成交额」推举
    tagged = [r for r in rows if r['role'] == '龙头']
    if tagged:
        leaders = sorted(tagged, key=lambda r: (-(r['today'] or -99), r['name']))[:LEADER_TOP]
        leader_mode = 'TAGGED'
    else:
        core = [r for r in rows if r['chain_distance'] == 0 and (r['today'] or 0) > 0]
        if len(core) < LEADER_TOP:
            core = [r for r in rows if (r['chain_distance'] or 0) <= 1 and (r['today'] or 0) > 0]
        if not core:
            core = [r for r in rows if (r['today'] or 0) > 0] or rows
        pct_s = _ranked([r['today'] or 0 for r in core])
        amt_s = _ranked([r['amount'] or 0 for r in core])
        for i, r in enumerate(core):
            r['_ls'] = pct_s[i] * 0.6 + amt_s[i] * 0.4
        leaders = sorted(core, key=lambda r: (-r['_ls'], r['name']))[:LEADER_TOP]
        leader_mode = 'INFERRED'

    used = {r['code'] for r in leaders}

    # 中军：有 role 标签按市值；否则排除龙头后按市值推举（中军 = 体量大、承接资金）
    tagged_z = [r for r in rows if r['role'] == '中军' and r['code'] not in used]
    if tagged_z:
        zhongjun = sorted(tagged_z, key=lambda r: (-(r['mv'] or 0), r['name']))[:ZHONGJUN_TOP]
        zj_mode = 'TAGGED'
        if len(zhongjun) < ZHONGJUN_TOP:
            used_z = used | {r['code'] for r in zhongjun}
            extra = [r for r in rows if r['code'] not in used_z and (r['chain_distance'] or 0) <= 1]
            extra.sort(key=lambda r: (-(r['mv'] or 0), r['name']))
            for r in extra[:ZHONGJUN_TOP - len(zhongjun)]:
                r['inferred'] = True
                zhongjun.append(r)
            zj_mode = 'MIXED'
    else:
        pool = [r for r in rows if r['code'] not in used and (r['chain_distance'] or 0) <= 1]
        if not pool:
            pool = [r for r in rows if r['code'] not in used] or rows
        zhongjun = sorted(pool, key=lambda r: (-(r['mv'] or 0), r['name']))[:ZHONGJUN_TOP]
        zj_mode = 'INFERRED'

    total_amt = sum((r['amount'] or 0) for r in rows) or 1.0
    money = sorted(rows, key=lambda r: (-(r['amount'] or 0), r['name']))[:MONEY_TOP]
    money = [dict(r, share=round((r['amount'] or 0) / total_amt * 100, 1)) for r in money]

    for r in leaders:
        r['inferred'] = leader_mode == 'INFERRED'
    for r in zhongjun:
        if zj_mode == 'INFERRED':
            r['inferred'] = True
        else:
            r.setdefault('inferred', False)
    return {'leaders': leaders, 'zhongjun': zhongjun, 'money': money,
            'leader_mode': leader_mode, 'zj_mode': zj_mode}


# ═════════════════════ AI 提炼 ═════════════════════

KIND_CN = {'NEW_START': '低位新启动', 'RESUME': '强势再启动',
           'ACCEL': '趋势加速', 'OBSERVE': '观察'}


def _cand_line(c):
    return (f"{c['theme']}｜{c['top_category']}｜评分{c['score']}｜{KIND_CN[c['kind']]}｜"
            f"N={c['n']}｜今日#{c['today_rank']}(Heat {f(c['today_heat'])})｜"
            f"本周#{c['week_rank']}({f(c['week_heat'])})｜本月#{c['month_rank']}({f(c['month_heat'])})｜"
            f"跃升{c['jump']:+d}｜P50 {f(c['p50'], 2, True)}%｜P75 {f(c['p75'], 2, True)}%｜"
            f"上涨{pct(c['up_ratio'])}｜强涨{pct(c['strong_ratio'])}｜量能{f(c['activity'], 2)}｜"
            f"涨停{c['limit_up_n']}｜本周P50 {f(c['week_p50'], 2, True)}%｜"
            f"风险[{','.join(c['risks']) or '无'}]")


def _pick_line(p):
    def one(r, extra=''):
        return (f"{r['code']} {r['name']} 今{f(r['today'], 2, True)}% "
                f"5日{f(r['d5'], 2, True)}% 20日{f(r['d20'], 2, True)}%{extra}")
    ls = '；'.join(one(r, '（推举）' if r.get('inferred') else '') for r in p['leaders']) or '无'
    zs = ('；'.join(one(r, '（推举）' if r.get('inferred') else '') for r in p['zhongjun'])
          or '无')
    ms = '；'.join(f"{r['name']} {r['share']}%" for r in p['money']) or '无'
    return ls, zs, ms


def build_prompt(payload):
    st = payload['style']
    L = []
    L.append(f"你是一位A股主题研究员。以下是 {payload['trade_date']} 收盘后的量化扫描结果，"
             "全部为客观指标，请据此写一份主题机会日报。")
    L.append("")
    L.append("【硬性要求】")
    L.append("1. 只能引用下面给出的数字，禁止编造任何数据、新闻、政策或外部事件。")
    L.append("2. 不给出买入/卖出/仓位/目标价建议，只做机会识别、强弱判断与观察条件。")
    L.append("3. 输出 Markdown，不用表格；每段 2~4 句，短句为主，手机可读。")
    L.append("4. 必须解释「为什么是新机会」，并明确「什么信号确认、什么信号证伪」。")
    L.append("5. 不要复述全部数据，做提炼与因果解释。")
    L.append("")
    L.append('=' * 56)
    L.append("【市场风格】")
    for wk, nm in (('TODAY', '今日'), ('WEEK', '本周'), ('MONTH', '本月')):
        s = st[wk]
        L.append(f"{nm}: 防御组Heat均值 {f(s['defensive_heat'])} vs 成长组 {f(s['growth_heat'])}"
                 f"（差 {f(s['gap'], 1)}）")
    L.append(f"判定: {st['verdict']}")
    L.append("")
    L.append("【一级目录全景（今日Heat降序）】")
    for c in sorted(payload['categories'], key=lambda x: x['today_rank']):
        L.append(f"{c['theme']}: 今日#{c['today_rank']} Heat {f(c['today_heat'])} P50 {f(c['today_p50'], 2, True)}% "
                 f"上涨{pct(c['today_up'])} 强涨{pct(c['today_strong'])} 量能{f(c['today_activity'], 2)} "
                 f"涨停{c['today_limit_up']}｜本周#{c['week_rank']} 本月#{c['month_rank']}")
    L.append("")
    L.append("【新机会候选（已按评分排序）】")
    for i, c in enumerate(payload['candidates'], 1):
        L.append(f"{i}. {_cand_line(c)}")
        ls, zs, ms = _pick_line(c['picks'])
        L.append(f"   龙头: {ls}")
        L.append(f"   中军: {zs}")
        L.append(f"   资金聚焦: {ms}")
        L.append(f"   评分构成: {c['parts']}")
    L.append("")
    L.append("【今日子主题 TOP10】")
    for r in payload['top10_today']:
        L.append(f"#{r['today_rank']} {r['theme']}（{r['top_category']}）Heat {f(r['today_heat'])} "
                 f"P50 {f(r['p50'], 2, True)}% 上涨{pct(r['up_ratio'])} 强涨{pct(r['strong_ratio'])} "
                 f"量能{f(r['activity'], 2)} 涨停{r['limit_up_n']}")
    L.append("")
    L.append("【本周子主题 TOP10】")
    for r in payload['top10_week']:
        L.append(f"#{r['week_rank']} {r['theme']} Heat {f(r['week_heat'])} 今日#{r['today_rank']} "
                 f"本月#{r['month_rank']}")
    L.append("")
    L.append("【本月子主题 TOP10】")
    for r in payload['top10_month']:
        L.append(f"#{r['month_rank']} {r['theme']} Heat {f(r['month_heat'])} 今日#{r['today_rank']} "
                 f"本周#{r['week_rank']}")
    L.append("")
    L.append('=' * 56)
    L.append("【输出结构（严格照此四节，不额外加节）】")
    L.append("### 一句话结论")
    L.append("用一句话说清今天最该关注的方向和它的性质（新启动/趋势加速/已是尾声），不超过60字。")
    L.append("### 市场风格")
    L.append("解释今天的风格位置与切换含义，说明这对新机会意味着什么。")
    L.append("### 新机会（逐条，只写评分前 5 名）")
    L.append("每条以「主题名（目录）｜类型｜评分」开头，然后写：①为什么是机会（引用跃升、P50、上涨占比、量能、本月位置）"
             "②龙头与中军分别是谁、强弱如何、是否推举（无标签时靠涨幅+资金推举）"
             "③确认信号 ④证伪信号。")
    L.append("### 风险与回避")
    L.append("指出候选里的主要风险标签含义，以及哪些上榜主题属于「看似强实则不可持续」，说明理由。")
    L.append("")
    L.append("输出语言：中文。")
    return '\n'.join(L)


def call_deepseek(prompt, timeout=180):
    import urllib.request
    key = os.getenv('DEEPSEEK_API_KEY')
    if not key:
        return '', 'DEEPSEEK_API_KEY 未配置'
    model = os.getenv('THEME3_AI_MODEL', 'deepseek-v4-flash')
    body = json.dumps({
        'model': model,
        'messages': [
            {'role': 'system',
             'content': '你是A股主题研究员，严格基于给定量化数据写日报，绝不编造数据或事件，'
                        '不给买卖建议、仓位或目标价。'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.2,
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://api.deepseek.com/chat/completions', data=body,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        return data['choices'][0]['message']['content'], ''
    except Exception as e:
        return '', f'DeepSeek 调用失败：{e}'


def fallback_text(payload):
    """AI 不可用时的确定性文本"""
    st = payload['style']
    L = ['### 一句话结论',
         f'今日风格{st["verdict"]}，评分最高的新方向是 '
         f'{payload["candidates"][0]["theme"] if payload["candidates"] else "（无）"}。',
         '', '### 市场风格', st['verdict'] + '。', '', '### 新机会']
    for c in payload['candidates'][:5]:
        ls, zs, ms = _pick_line(c['picks'])
        L.append(f'- **{c["theme"]}（{c["top_category"]}）｜{KIND_CN[c["kind"]]}｜{c["score"]}分**：'
                 f'今日#{c["today_rank"]}（本周#{c["week_rank"]}，跃升{c["jump"]:+d}），'
                 f'P50 {f(c["p50"], 2, True)}%、上涨{pct(c["up_ratio"])}、强涨{pct(c["strong_ratio"])}、'
                 f'量能{f(c["activity"], 2)}、涨停{c["limit_up_n"]}，本月#{c["month_rank"]}。'
                 f'龙头：{ls}。中军：{zs}。资金聚焦：{ms}。')
    L += ['', '### 风险与回避']
    for c in payload['candidates'][:5]:
        if c['risks']:
            L.append(f'- {c["theme"]}：{", ".join(c["risks"])}')
    L.append('')
    L.append('> 本段由规则模板生成（AI 提炼未启用或调用失败）。')
    return '\n'.join(L)


# ═════════════════════ 渲染 ═════════════════════

def md_to_html(md):
    import re
    out, in_ul = [], False
    for raw in md.split('\n'):
        line = raw.rstrip()
        if not line.strip():
            if in_ul:
                out.append('</ul>')
                in_ul = False
            continue
        if line.startswith('### '):
            if in_ul:
                out.append('</ul>')
                in_ul = False
            out.append(f'<h3 style="font-size:24px;line-height:1.5;margin:22px 0 8px;color:#111827">{_inline(line[4:])}</h3>')
        elif line.startswith('## '):
            if in_ul:
                out.append('</ul>')
                in_ul = False
            out.append(f'<h2 style="font-size:26px;line-height:1.5;margin:24px 0 10px;color:#111827">{_inline(line[3:])}</h2>')
        elif line.lstrip().startswith(('- ', '* ')):
            if not in_ul:
                out.append('<ul>')
                in_ul = True
            out.append(f'<li style="margin:5px 0">{_inline(line.lstrip()[2:])}</li>')
        elif line.startswith('> '):
            out.append(f'<blockquote style="margin:10px 0;padding-left:12px;border-left:4px solid #d1d5db;color:#374151">{_inline(line[2:])}</blockquote>')
        else:
            if in_ul:
                out.append('</ul>')
                in_ul = False
            out.append(f'<p style="margin:0 0 12px">{_inline(line)}</p>')
    if in_ul:
        out.append('</ul>')
    return '\n'.join(out)


def _inline(t):
    import html
    import re
    t = html.escape(t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    return t


COLOR_UP, COLOR_DN, COLOR_MUTED = '#c0392b', '#1e7e34', '#6b7280'


def _delta(v, d=2, suffix='%'):
    if v is None:
        return f'<span style="color:{COLOR_MUTED}">—</span>'
    c = COLOR_UP if v > 0 else (COLOR_DN if v < 0 else COLOR_MUTED)
    return f'<span style="color:{c}">{v:+.{d}f}{suffix}</span>'


def _gap_color(v):
    """风格差值着色：正=防御占优(绿)，负=成长占优(红)，未达阈值则灰。"""
    if v is None:
        return COLOR_MUTED
    if abs(v) < STYLE_GAP:
        return COLOR_MUTED
    return COLOR_DN if v > 0 else COLOR_UP


RISK_CN = {
    'NO_ELASTICITY': '无弹性普涨（涨幅均匀、缺领涨股）',
    'HIGH_POSITION': '已在中后期（本月前3且热度≥90）',
    'VOLUME_SHRINK': '量能未放大',
    'RECLAIM_DAY1': '5日仍为负（仅反包首日）',
    'ISOLATED': '板块孤岛（所属目录今日偏弱）',
    'SMALL_SAMPLE': '样本偏小',
    'MAPPING_WARN': '成分映射质量偏低',
    'STYLE_CONFLICT': '与当日风格相反',
}
KIND_COLOR = {'NEW_START': '#c0392b', 'RESUME': '#9a3412',
              'ACCEL': '#b26a00', 'OBSERVE': '#6b7280'}


def _stock_table(rows, kind):
    if not rows:
        return '<div style="color:#6b7280;font-size:22px">无</div>'
    body = []
    for r in rows:
        tag = '推举' if r.get('inferred') else (r['role'] if r['role'] != '—' else '—')
        body.append(
            '<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:22px">'
            f'<span style="font-family:Consolas,monospace;color:#374151">{r["code"]}</span> '
            f'<strong>{r["name"]}</strong></td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:18px;color:#6b7280">{tag}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:22px;text-align:right">{_delta(r["today"])}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:22px;text-align:right">{_delta(r["d5"])}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:22px;text-align:right">{_delta(r["d20"])}</td>'
            '</tr>')
    head = ('<tr style="background:#f5f6f7">'
            '<th style="padding:5px 8px;text-align:left;font-size:18px;color:#6b7280">代码 / 名称</th>'
            '<th style="padding:5px 8px;text-align:left;font-size:18px;color:#6b7280">角色</th>'
            '<th style="padding:5px 8px;text-align:right;font-size:18px;color:#6b7280">今日</th>'
            '<th style="padding:5px 8px;text-align:right;font-size:18px;color:#6b7280">5日</th>'
            '<th style="padding:5px 8px;text-align:right;font-size:18px;color:#6b7280">20日</th></tr>')
    return ('<div style="overflow-x:auto">'
            '<table style="width:100%;border-collapse:collapse;margin:6px 0 12px">'
            + head + ''.join(body) + '</table></div>')


def _cand_card(c, idx):
    p = c['picks']
    pt = c['parts']
    color = KIND_COLOR[c['kind']]
    risks = '、'.join(RISK_CN.get(x, x) for x in c['risks']) or '无明显风险标记'
    ls = _pick_line(p)[0]
    zs = _pick_line(p)[1]
    part_txt = (f"跃升{pt['jump']} + 结构{pt['struct']} + 中期{pt['mid']} "
                f"+ 量能{pt['vol']} + 未消耗{pt['fresh']}"
                + (f" − 扣分{pt['penalty']}" if pt['penalty'] else ''))
    ZJ_MODE = {'TAGGED': '按标签', 'MIXED': '标签不足按市值补齐', 'INFERRED': '按市值推举'}
    zj_label = ZJ_MODE.get(p.get('zj_mode'), '按市值推举')
    return f'''
<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:0 0 14px;background:#fff">
  <table style="width:100%;border-collapse:collapse">
    <tr>
      <td style="font-size:22px;font-weight:700;color:#111;padding:0">
        {idx}. {c['theme']}
        <span style="font-size:18px;font-weight:400;color:#6b7280;margin-left:6px">{c['top_category']}</span>
      </td>
      <td style="text-align:right;white-space:nowrap;padding:0">
        <span style="background:{color};color:#fff;border-radius:4px;padding:2px 7px;font-size:18px">{KIND_CN[c['kind']]}</span>
        <span style="margin-left:8px;font-weight:700;color:{color}">{c['score']}分</span>
      </td>
    </tr>
  </table>
  <div style="margin:10px 0 8px;font-size:22px;color:#374151;line-height:1.9">
    今日 <strong>#{c['today_rank']}</strong>（Heat {f(c['today_heat'])}） ·
    本周 <strong>#{c['week_rank']}</strong>（{f(c['week_heat'])}） ·
    本月 <strong>#{c['month_rank']}</strong>（{f(c['month_heat'])}）
    <span style="color:{COLOR_UP if c['jump'] > 0 else COLOR_MUTED};font-weight:700">跃升 {c['jump']:+d} 位</span>
    <br>
    涨幅P50 {_delta(c['p50'])} · P75 {_delta(c['p75'])} ·
    上涨{pct(c['up_ratio'])} · 强涨{pct(c['strong_ratio'])} ·
    量能 {f(c['activity'], 2)} · 涨停 {c['limit_up_n']} · 成员 {c['n']}
    <br>
    <span style="color:#6b7280;font-size:18px">评分构成：{part_txt}</span>
  </div>
  <div style="font-size:18px;font-weight:700;color:#374151;margin-top:8px">龙头 <span style="font-weight:400;color:#6b7280">（{'按标签' if p['leader_mode'] == 'TAGGED' else '无龙头标签，按涨幅+资金推举'}）</span></div>
  {_stock_table(p['leaders'], 'leader')}
  <div style="font-size:18px;font-weight:700;color:#374151">中军 <span style="font-weight:400;color:#6b7280">（{zj_label}）</span></div>
  {_stock_table(p['zhongjun'], 'zj')}
  <div style="font-size:18px;font-weight:700;color:#374151;margin-bottom:4px">资金聚焦（占主题成交额）</div>
  <div style="font-size:22px;color:#374151;line-height:1.9">{' · '.join(f"{r['name']} {r['share']}%" for r in p['money']) or '无'}</div>
  <div style="margin-top:10px;padding-top:8px;border-top:1px dashed #e5e7eb;font-size:22px;color:#9a3412">
    风险：{risks}
  </div>
</div>'''


def render_html(payload, ai_md):
    st = payload['style']
    d = payload['trade_date']
    ds = f'{d[:4]}-{d[4:6]}-{d[6:]}'

    rows = []
    for c in sorted(payload['categories'], key=lambda x: x['today_rank']):
        rows.append(
            '<tr>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px">{c["theme"]}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:center">{c["today_rank"]}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:right;font-weight:700">{f(c["today_heat"])}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:right">{_delta(c["today_p50"])}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:right;color:#6b7280">{pct(c["today_up"])}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:right;color:#6b7280">{pct(c["today_strong"])}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:right;color:#6b7280">{f(c["today_activity"], 2)}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:center;color:#6b7280">{c["week_rank"]}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:17px;text-align:center;color:#6b7280">{c["month_rank"]}</td>'
            '</tr>')

    cards = ''.join(_cand_card(c, i) for i, c in enumerate(payload['candidates'], 1))
    ai_html = md_to_html(ai_md)

    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>theme3 新机会日报 {ds}</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#111">
<div style="max-width:760px;margin:0 auto;background:#f3f4f6;padding:16px">

  <div style="background:linear-gradient(135deg,#111827,#374151);border-radius:12px;padding:20px 22px;color:#fff">
    <div style="font-size:18px;letter-spacing:2px;opacity:.75">THEME3 OPPORTUNITY · 每日主题机会日报</div>
    <div style="font-size:28px;font-weight:700;margin-top:6px">{ds}</div>
    <div style="margin-top:10px;font-size:22px;line-height:1.6">
      子主题 {payload['n_subtopics']} 个 · 一级目录 {payload['n_categories']} 个 ·
      覆盖个股 {payload['n_stocks']} 只 · 候选池 {len(payload['candidates'])} 个
    </div>
    <div style="margin-top:12px;display:inline-block;background:rgba(255,255,255,.14);border-radius:6px;padding:6px 10px;font-size:22px">
      风格判定：<strong style="color:#fff">{st['verdict']}</strong>
    </div>
  </div>

  <div style="background:#fff;border-radius:12px;padding:18px 20px;margin-top:14px">
    <div style="font-size:24px;font-weight:700;color:#111;margin-bottom:8px">市场风格（Heat 均值对比）</div>
    <table style="width:100%;border-collapse:collapse;font-size:22px">
      <tr style="color:#6b7280;font-size:18px">
        <td style="padding:4px 0">窗口</td>
        <td style="text-align:right">价值防御组</td>
        <td style="text-align:right">成长进攻组</td>
        <td style="text-align:right">差值</td>
      </tr>
      {''.join(f'<tr><td style="padding:5px 0;color:#374151">{nm}</td>'
               f'<td style="text-align:right">{f(st[wk]["defensive_heat"])}</td>'
               f'<td style="text-align:right">{f(st[wk]["growth_heat"])}</td>'
               f'<td style="text-align:right;font-weight:700;color:{_gap_color(st[wk]["gap"])}">{f(st[wk]["gap"], 1, True)}</td></tr>'
               for wk, nm in (('TODAY', '今日'), ('WEEK', '本周'), ('MONTH', '本月')))}
    </table>
    <div style="margin-top:8px;font-size:18px;color:#6b7280">
      防御组 = {'、'.join(sorted(DEFENSIVE_CATS))}；成长组 = {'、'.join(sorted(GROWTH_CATS))}；
      中性组 = {'、'.join(sorted(NEUTRAL_CATS))}（攻守混合，不参与风格均值）。
      差值 &gt; +{STYLE_GAP:.0f} 判防御占优，&lt; −{STYLE_GAP:.0f} 判成长占优。
    </div>
  </div>

  <div style="background:#fff;border-radius:12px;padding:18px 20px;margin-top:14px">
    <div style="font-size:24px;font-weight:700;color:#111;margin-bottom:4px">AI 研判</div>
    <div style="height:3px;width:40px;background:#111827;border-radius:2px;margin-bottom:12px"></div>
    <div style="font-size:22px;line-height:1.85;color:#1f2937">{ai_html}</div>
  </div>

  <div style="margin-top:18px">
    <div style="font-size:24px;font-weight:700;color:#111;padding-left:2px">新机会候选（按评分）</div>
    <div style="font-size:18px;color:#6b7280;margin:4px 0 12px;padding-left:2px">
      评分 = 跃升{W_JUMP} + 结构{W_STRUCT} + 中期{W_MID} + 量能{W_VOL} + 未消耗{W_FRESH} − 扣分；样本 N&lt;{MIN_N} 不参与，{MIN_N}≤N&lt;{FULL_N} 打 9 折
    </div>
    {cards}
  </div>

  <div style="background:#fff;border-radius:12px;padding:18px 20px;margin-top:6px">
    <div style="font-size:24px;font-weight:700;margin-bottom:10px">一级目录全景</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;min-width:520px">
      <tr style="background:#f9fafb;color:#6b7280;font-size:16px">
        <th style="padding:6px 8px;text-align:left">目录</th>
        <th style="padding:6px 8px">今序</th>
        <th style="padding:6px 8px;text-align:right">Heat</th>
        <th style="padding:6px 8px;text-align:right">P50</th>
        <th style="padding:6px 8px;text-align:right">上涨</th>
        <th style="padding:6px 8px;text-align:right">强涨</th>
        <th style="padding:6px 8px;text-align:right">量能</th>
        <th style="padding:6px 8px">本周</th>
        <th style="padding:6px 8px">本月</th>
      </tr>
      {''.join(rows)}
    </table>
    </div>
  </div>

  <div style="font-size:16px;color:#9ca3af;line-height:1.8;padding:14px 4px 4px">
    数据来源：theme3_constituents_{d}.json（成分股映射）+ cache_daily/stock_data.db（daily_cache 日线）。<br>
    热度口径对齐 theme_heat_v22 / V2.4：HeatRaw = 60%涨幅分位 + 30%广度分位 + 10%量能分位，再按 Rel=min(1,√(N/50)) 收缩；
    Price = 70%×P50 + 30%×P75（成员收益 clip ±20%）；Breadth = 70%上涨 + 30%强涨(≥3%)；Activity = 今日÷过去20日均额（单股 clip 0.5~3.0，取中位数）。<br>
    龙头/中军：优先使用成分股 role 标签，无标签时按「链距≤1 + 今日涨幅 + 成交额」推举并标注。<br>
    本报告仅做机会识别与观察条件，不构成买卖建议、仓位或目标价。
  </div>
</div></body></html>'''


def render_markdown(payload, ai_md):
    d = payload['trade_date']
    st = payload['style']
    L = [f'# theme3 新机会日报 {d}', '']
    L.append(f"**风格判定**：{st['verdict']}")
    L.append('')
    for wk, nm in (('TODAY', '今日'), ('WEEK', '本周'), ('MONTH', '本月')):
        s = st[wk]
        L.append(f"- {nm}：防御组 {f(s['defensive_heat'])} / 成长组 {f(s['growth_heat'])}"
                 f"（差 {f(s['gap'], 1, True)}）")
    L += ['', '---', '', '## AI 研判', '', ai_md, '', '---', '', '## 新机会候选', '']
    for i, c in enumerate(payload['candidates'], 1):
        ls, zs, ms = _pick_line(c['picks'])
        L.append(f"### {i}. {c['theme']}（{c['top_category']}）｜{KIND_CN[c['kind']]}｜{c['score']}分")
        L.append(f"- 今日 #{c['today_rank']}（Heat {f(c['today_heat'])}）／本周 #{c['week_rank']}"
                 f"（{f(c['week_heat'])}）／本月 #{c['month_rank']}（{f(c['month_heat'])}）｜跃升 {c['jump']:+d}")
        L.append(f"- P50 {f(c['p50'], 2, True)}%／P75 {f(c['p75'], 2, True)}%／上涨 {pct(c['up_ratio'])}"
                 f"／强涨 {pct(c['strong_ratio'])}／量能 {f(c['activity'], 2)}／涨停 {c['limit_up_n']}／成员 {c['n']}")
        L.append(f"- 龙头：{ls}")
        L.append(f"- 中军：{zs}")
        L.append(f"- 资金聚焦：{ms}")
        L.append(f"- 风险：{'、'.join(RISK_CN.get(x, x) for x in c['risks']) or '无'}")
        L.append('')
    L += ['---', '', '## 一级目录全景', '']
    L.append('| 目录 | 今名 | Heat | P50 | 上涨 | 强涨 | 量能 | 本周 | 本月 |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for c in sorted(payload['categories'], key=lambda x: x['today_rank']):
        L.append(f"| {c['theme']} | {c['today_rank']} | {f(c['today_heat'])} | {f(c['today_p50'], 2, True)}% "
                 f"| {pct(c['today_up'])} | {pct(c['today_strong'])} | {f(c['today_activity'], 2)} "
                 f"| {c['week_rank']} | {c['month_rank']} |")
    L += ['', '> 本报告仅做机会识别与观察条件，不构成买卖建议。']
    return '\n'.join(L)


# ═════════════════════ 组装 ═════════════════════

def build_payload(trade_date, top):
    heat = load_heat(trade_date)
    cons = load_constituents(trade_date)
    px = load_today_px(trade_date)

    style = style_layer(heat)
    heat['style_tone'] = style['tone']
    scored = score_candidates(heat)

    cands = []
    for c in scored[:top]:
        picks = pick_leaders(c['theme'], cons, px)
        cands.append(dict(c, picks=picks))

    cats = []
    for c in heat['categories']:
        t, w, m = c['TODAY'], c['WEEK'], c['MONTH']
        cats.append({
            'theme': c['theme'], 'n_members': c['n_members'],
            'today_rank': t['rank'], 'today_heat': t.get('heat'),
            'today_p50': t.get('p50'), 'today_up': t.get('up_ratio'),
            'today_strong': t.get('strong_ratio'), 'today_activity': t.get('activity'),
            'today_limit_up': t.get('limit_up_n') or 0,
            'week_rank': w.get('rank'), 'month_rank': m.get('rank'),
        })

    def top_by(recs, wk, k=10):
        v = [r for r in recs if (r.get(wk) or {}).get('rank')]
        v.sort(key=lambda r: r[wk]['rank'])
        out = []
        for r in v[:k]:
            t, w, m = r['TODAY'], r['WEEK'], r['MONTH']
            out.append({'theme': r['theme'], 'top_category': r['top_category'],
                        'today_rank': t.get('rank'), 'today_heat': t.get('heat'),
                        'week_rank': w.get('rank'), 'week_heat': w.get('heat'),
                        'month_rank': m.get('rank'), 'month_heat': m.get('heat'),
                        'p50': t.get('p50'), 'up_ratio': t.get('up_ratio'),
                        'strong_ratio': t.get('strong_ratio'),
                        'activity': t.get('activity'), 'limit_up_n': t.get('limit_up_n') or 0})
        return out

    subs = heat['subtopics']
    return {
        'trade_date': trade_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'n_subtopics': heat['summary']['n_subtopics'],
        'n_categories': heat['summary']['n_categories'],
        'n_stocks': heat['summary']['n_stocks'],
        'style': style,
        'categories': cats,
        'candidates': cands,
        'top10_today': top_by(subs, 'TODAY'),
        'top10_week': top_by(subs, 'WEEK'),
        'top10_month': top_by(subs, 'MONTH'),
    }


def send_email(html_path, subject, to=MAIL_TO):
    """用 Agent Mail CLI（agently-cli）发送 HTML 日报，与项目内 hve_v1/push.py 等保持一致。

    CLI 的 --body-file 必须落在当前工作目录内且 ≤1MB，故正文先写 BASE_DIR 下的临时
    html 文件，发送成功即删除（失败则留存并打印路径）。异常只打日志不抛出。
    """
    if not os.path.exists(MAIL_CLI):
        print(f'[邮件] 未找到 Agent Mail CLI: {MAIL_CLI}，跳过发送')
        return False

    body_path = os.path.join(BASE_DIR, '_theme3_mail_body.html')
    try:
        with open(html_path, encoding='utf-8') as f:
            body = f.read()
        raw = body.encode('utf-8')
        if len(raw) > 1000 * 1024:
            body = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(body)
    except Exception as e:
        print(f'[邮件] 正文写入失败: {e}')
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', to, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180, cwd=BASE_DIR)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print(f'[邮件] 已发送 → {to}')
            try:
                os.remove(body_path)
            except OSError:
                pass
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print(f'[邮件] 发送失败: rc={r.returncode} {detail}（正文留存: {body_path}）')
        return False
    except Exception as e:
        print(f'[邮件] 发送异常: {e}')
        return False


def main():
    args = [a for a in sys.argv[1:]]
    no_ai = '--no-ai' in args
    no_email = '--no-email' in args
    if_today = '--if-today' in args
    args = [a for a in args if a not in ('--no-ai', '--no-email', '--if-today')]
    top = 6
    if '--top' in args:
        i = args.index('--top')
        try:
            top = int(args[i + 1])
        except (IndexError, ValueError):
            pass
        del args[i:i + 2]
    date_arg = args[0] if args else None

    load_env()
    trade_date = resolve_trade_date(date_arg)
    if if_today:
        today = datetime.now().strftime('%Y%m%d')
        if trade_date != today:
            print(f'[跳过] 最新交易日 {trade_date} != 今日 {today}（非交易日或数据未就绪），'
                  '不生成、不发信')
            return

    print('=' * 72)
    print('theme3_opportunity: 新机会主题识别 + 龙头/中军 + AI 日报')
    print(f'交易日: {trade_date}')
    print('=' * 72)

    payload = build_payload(trade_date, top)

    print(f"[风格] {payload['style']['verdict']}")
    print(f"[候选] {len(payload['candidates'])} 个")
    for i, c in enumerate(payload['candidates'], 1):
        print(f"  {i}. {c['theme']:<14} {KIND_CN[c['kind']]:<5} {c['score']:>6}分  "
              f"今#{c['today_rank']}(本周#{c['week_rank']}, 跃升{c['jump']:+d})  "
              f"N={c['n']}  {'/'.join(c['risks']) or '-'}")
        ls, zs, _ = _pick_line(c['picks'])
        print(f"      龙头: {ls}")
        print(f"      中军: {zs}")

    ai_md, err = '', ''
    if not no_ai:
        print('\n[AI] 调用 DeepSeek 提炼表述 ...')
        ai_md, err = call_deepseek(build_prompt(payload))
        if err:
            print(f'[AI] {err}，回退到规则文本')
    if not ai_md:
        ai_md = fallback_text(payload)
        payload['ai_source'] = 'rule'
    else:
        payload['ai_source'] = 'deepseek'
        print(f'[AI] 完成（{len(ai_md)} 字）')

    html = render_html(payload, ai_md)
    md = render_markdown(payload, ai_md)

    hp = os.path.join(REPORT_DIR, f'theme3_opportunity_{trade_date}.html')
    mp = os.path.join(REPORT_DIR, f'theme3_opportunity_{trade_date}.md')
    jp = os.path.join(REPORT_DIR, f'theme3_opportunity_{trade_date}.json')
    with open(hp, 'w', encoding='utf-8') as f:
        f.write(html)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(md)
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(dict(payload, ai_text=ai_md), f, ensure_ascii=False, indent=2)

    print(f'\n✅ HTML: {hp}')
    print(f'✅ MD  : {mp}')
    print(f'✅ JSON: {jp}')

    if not no_email:
        subject = f'[主题机会日报] {trade_date}｜{payload["style"]["verdict"]}'
        send_email(hp, subject)


if __name__ == '__main__':
    main()
