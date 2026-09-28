#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V2.4-HC 主题人工确认层（Human Confirmation）
════════════════════════════════════════════════════════════

只回答三个问题（§一）：
    Q1 这个主题定义本身是否成立？
    Q2 这个主题的成分股是否真的属于这个主题？
    Q3 当前 Heat 是否具有「主题意义」？

严格约束（§十九 禁止事项）：
    · 不重新计算 Heat、不改 Price/Breadth/Activity 权重、不改排名
    · 不因主题热门 / 过去表现好 / 单只股票上涨而加分或倒推归属
    · 不用行业分类直接代替主题分类，不用新闻热度代替股票广度
    · 人工判断只落盘到 theme_heat_v24_hc_*，绝不写回 theme_heat_v24_* 原始量化数据

数据来源（只读取，不新增下载）：
    report_daily/theme_heat_v24_{date}.csv        ← V2.4 机器排名与全部指标
    cache_daily/theme_stock_map_v2_{date}.json    ← 成分股映射
    theme_kg_v3/theme_kg_v3/config/theme_config.json
    cache_daily/stock_company_mainbiz.json
    cache_daily/stock_data.db（daily_cache，个股日线，用于贡献度与边缘抽样）

成分股四分类（§四，在 V2.4 三层之上细分）：
    CORE      = 人工名单 或 主营命中 core_keywords / industry_keywords
    RELATED   = 主营命中 keywords / product / brand / concept / industry_chains
    POLLUTION = 无正面主营证据，且（命中排除词 / 仅概念标签 / 仅靠宽口径行业被拉入）
    WEAK      = 无正面主营证据，但所属行业属于该主题「特征行业」，或主营文本缺失

用法：
    python theme_heat_v24_hc.py              # 最近一个交易日
    python theme_heat_v24_hc.py 20260923     # 指定交易日
"""

import os
import sys
import json
import random
import collections

# Windows GBK 控制台
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.dirname(BASE_DIR))

import numpy as np
import pandas as pd

import theme_heat_v22 as v24

REPORT_DIR = v24.REPORT_DIR
CACHE_DIR = v24.CACHE_DIR

# ── §七 小样本：HC 门槛比 V2.4（N<10）更严 ──
HC_LOW_SAMPLE = 20
# ── §八 广度状态阈值 ──
HC_HEAT_HIGH = 75.0          # Heat ≥ 75 视为「热度高」
HC_BREADTH_HIGH = 45.0       # Breadth（合成值，%）≥ 45 视为「扩散正常」
HC_PRICE_STRONG = 2.0        # 窗口涨幅 ≥ +2% 视为「价格强」
# ── §六 主题纯度阈值 ──
HC_VALID_PURE, HC_POLL_PURE = 80.0, 10.0        # PURE
HC_VALID_OK, HC_POLL_OK = 65.0, 20.0            # ACCEPTABLE
# ── §十二 ERROR4 行业代替主题 ──
HC_INDUSTRY_PROXY = 15.0
# ── §三 抽样只数 ──
HC_SAMPLE = 5
CONCEPT_SOURCES = {'concept_fallback', 'ths_concept'}   # 仅概念标签来源
# §十四 Level 1 人工复核记录：以年报/半年报主营为准，修正「关键词缺失 / 排除词误伤」导致的机器误判。
# 格式 {code: (主题, 人工分层, 依据)}；只作用于本 HC 层输出，绝不写回 V2.4 产物或 theme_config.json（§十九 #10）。
HC_OVERRIDES = {
    '603127.SH': ('创新药', 'RELATED',
                  '年报主营为「药物非临床安全性评价服务」（CRO），属创新药研发服务链，'
                  '机器因关键词缺「临床前」误判为 POLLUTION'),
    '000402.SZ': ('地产链', 'RELATED',
                  '年报主营含「土地开发、房产开发」，属地产链，排除词「租赁」不适用'),
}
SEP_FULL = v24.SEP_FULL
SEP_THIN = v24.SEP_THIN
_pad, _f = v24._pad, v24._f


# ═════════════════════════ 成分股四分类（§四 / §十四）═════════════════════════

def characteristic_industries(layers, members):
    """主题「特征行业」= 该主题 CORE 成员中占比 ≥15% 的行业（§十五 行业≠主题 的判据基础）

    CORE 不足 5 只时退化为 CORE+RELATED 的行业分布。纯数据驱动、可复现。
    """
    ind = collections.Counter(members[c].get('industry') or '' for c, v in layers.items()
                              if v['layer'] == 'CORE')
    if sum(ind.values()) < 5:
        ind = collections.Counter(members[c].get('industry') or '' for c, v in layers.items()
                                  if v['layer'] in ('CORE', 'RELATED'))
    n = sum(ind.values())
    if n == 0:
        return set()
    return {k for k, v in ind.items() if k and v / n >= 0.15}


def hc_classify(code, via, txt, cfg, industry, char_ind):
    """§四 四分类：在 V2.4 三层之上细分出 POLLUTION

    POLLUTION 判定（§十五 行业≠主题、§十二 ERROR4 行业代替主题、§十九 #9）：
        · 命中 exclude_keywords
        · 来源仅为概念标签（concept_fallback / ths_concept）
        · 无正面主营证据，且所属行业不属于该主题特征行业 → 仅靠宽口径行业被拉入
    无主营文本（数据缺口）不判 POLLUTION，保留 WEAK 并标注，等人工确认（§十四 Level 1 缺失）。
    """
    if cfg is None:
        return 'WEAK', '无对应主题配置（数据缺口）'
    ov = HC_OVERRIDES.get(code)
    if ov and ov[0] == cfg.get('name_cn'):
        return ov[1], f'人工复核修正（§十四 Level 1）｜{ov[2]}'
    layer, why = v24.classify_member(code, via, txt, cfg)
    if layer == 'CORE':
        return 'CORE', why
    if layer == 'NORMAL':
        return 'RELATED', why
    if not txt:
        return 'WEAK', f'{why}（数据缺口，需人工确认）'
    if '排除词' in why:
        return 'POLLUTION', why
    if via in CONCEPT_SOURCES:
        return 'POLLUTION', f'{via}｜仅有概念标签，无主营证据'
    if industry in char_ind:
        return 'WEAK', f'{via}｜行业「{industry}」属主题特征行业，但主营无直接证据'
    return 'POLLUTION', f'{via}｜仅因宽口径行业「{industry or "未知"}」被纳入（行业代替主题）'


def build_hc_purity(theme_members, config, mainbiz):
    """§五 主题纯度：CORE / RELATED / WEAK / POLLUTION 比例（全量口径，N=映射成员总数）"""
    out = {}
    for t, recs in theme_members.items():
        cfg = config.get(t)
        v24layers = {}
        for c, rec in recs.items():
            l, _ = ('CORE', '') if cfg is None else v24.classify_member(c, rec['via'], mainbiz.get(c, ''), cfg)
            v24layers[c] = {'layer': l}
        char_ind = characteristic_industries(v24layers, recs) if cfg is not None else set()
        layers = {}
        for c, rec in recs.items():
            cls, why = hc_classify(c, rec['via'], mainbiz.get(c, ''), cfg,
                                   rec.get('industry') or '', char_ind)
            layers[c] = {'cls': cls, 'why': why, 'name': rec['name'],
                         'via': rec['via'], 'industry': rec.get('industry') or ''}
        cnt = collections.Counter(v['cls'] for v in layers.values())
        total = len(recs) or 1
        core_r = cnt['CORE'] / total * 100.0
        rel_r = cnt['RELATED'] / total * 100.0
        weak_r = cnt['WEAK'] / total * 100.0
        poll_r = cnt['POLLUTION'] / total * 100.0
        valid_r = core_r + rel_r
        if valid_r >= HC_VALID_PURE and poll_r <= HC_POLL_PURE:
            level = 'PURE'
        elif valid_r >= HC_VALID_OK and poll_r <= HC_POLL_OK:
            level = 'ACCEPTABLE'
        else:
            level = 'CONTAMINATED'
        out[t] = {'total': len(recs), 'layers': layers, 'char_ind': char_ind,
                  'counts': dict(cnt), 'core_ratio': core_r, 'related_ratio': rel_r,
                  'weak_ratio': weak_r, 'pollution_ratio': poll_r, 'valid_ratio': valid_r,
                  'purity_level': level}
    return out


# ═════════════════════════ 状态判定（§八~§十二）═════════════════════════

def breadth_state(heat, breadth, price):
    """§八 广度真实性：BROAD / NARROW / DIVERGENCE / BROAD_WEAK（+NEUTRAL 兜底）"""
    hot = heat is not None and heat >= HC_HEAT_HIGH
    bhot = breadth is not None and breadth >= HC_BREADTH_HIGH
    strong = price is not None and price >= HC_PRICE_STRONG
    if hot and bhot:
        return 'BROAD'
    if hot and not bhot:
        return 'NARROW'
    if strong and not bhot:
        return 'DIVERGENCE'
    if bhot and not strong:
        return 'BROAD_WEAK'
    return 'NEUTRAL'


def sample_flag(n):
    """§七 N<20 → LOW_SAMPLE_REVIEW"""
    return 'LOW_SAMPLE_REVIEW' if (n is not None and n < HC_LOW_SAMPLE) else ''


def today_state(p, wk_state):
    """§九 今日人工确认状态"""
    if p['purity_level'] in ('INVALID', 'CONTAMINATED'):
        return 'INVALID_THEME'
    if p['today_n'] < HC_LOW_SAMPLE:
        return 'HOT_BUT_SMALL'
    if wk_state in ('NARROW', 'DIVERGENCE'):
        return 'HOT_BUT_NARROW'
    if wk_state == 'BROAD':
        return 'CONFIRMED_TODAY'
    return 'VALID_BUT_NOT_BROAD'


def week_state(p):
    """§十 本周人工确认状态"""
    if p['purity_level'] in ('INVALID', 'CONTAMINATED'):
        return 'WEEK_INVALID'
    if p['week_price'] is not None and p['week_breadth'] < HC_BREADTH_HIGH:
        return 'WEEK_NARROW'
    if p['week_n'] < HC_LOW_SAMPLE:
        return 'WEEK_EVENT_DRIVEN'
    if p['week_rank'] <= 10 and p['month_rank'] > 10:
        return 'WEEK_REBOUND'
    return 'WEEK_CONFIRMED'


def month_state(p):
    """§十一 本月人工确认状态"""
    if p['purity_level'] in ('INVALID', 'CONTAMINATED'):
        return 'MONTH_INVALID'
    if p['month_n'] < HC_LOW_SAMPLE:
        return 'MONTH_EVENT_DRIVEN'
    if p['month_breadth'] < HC_BREADTH_HIGH:
        return 'MONTH_LEADER_DRIVEN'
    if p['month_rank'] <= 10 and (p['week_rank'] > 10 or p['today_rank'] > 10):
        return 'MONTH_DECAYING'
    return 'MONTH_CONFIRMED'


def machine_errors(p):
    """§十二 机器排名错误主动标记（ERROR1~ERROR5）"""
    e = []
    if p['today_rank'] == 1 and p['today_n'] < HC_LOW_SAMPLE:
        e.append('ERROR1_小样本第一')
    if p['today_rank'] == 1 and p['today_breadth'] < HC_BREADTH_HIGH:
        e.append('ERROR2_低Breadth第一')
    if p['pollution_ratio'] > HC_POLL_OK:
        e.append('ERROR3_概念污染')
    if p['pollution_ratio'] > HC_INDUSTRY_PROXY:
        e.append('ERROR4_行业代替主题')
    mislead = [v['name'] for v in p['layers'].values()
               if v['cls'] == 'POLLUTION'
               and any(w in v['name'] for w in ('科技', '智能', '电子', '数字', '新材料', '信息', '网络'))]
    if mislead:
        e.append('ERROR5_名称误导(' + '、'.join(mislead[:3]) + ')')
    return e


# ═════════════════════════ 成分股抽样（§三）═════════════════════════

def _mret(rets, wk, code):
    s = rets.get(wk)
    if s is None or code not in s.index:
        return None
    x = s.get(code)
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def pick_samples(p, rets):
    """§三 三组各 5 只：涨幅贡献 Top5 / 随机 5 / 边缘 5（关联度最低）"""
    layers = p['layers']
    codes = list(layers)
    # 1) 涨幅贡献 Top5（当日涨幅降序；§三 首选贡献度指标）
    top = sorted(codes, key=lambda c: (_mret(rets, 'TODAY', c) is not None,
                                       _mret(rets, 'TODAY', c) or -999), reverse=True)[:HC_SAMPLE]
    # 2) 随机 5（固定种子，保证同一交易日可复现）
    rnd = random.Random(f"HC|{p['theme']}")
    rand = rnd.sample(codes, min(HC_SAMPLE, len(codes)))
    # 3) 边缘 5：优先 POLLUTION → WEAK，再取低涨幅
    edge_pool = ([c for c in codes if layers[c]['cls'] == 'POLLUTION']
                 + [c for c in codes if layers[c]['cls'] == 'WEAK'])
    if not edge_pool:
        edge_pool = sorted(codes, key=lambda c: (_mret(rets, 'TODAY', c) if _mret(rets, 'TODAY', c) is not None else 0))
    edge = edge_pool[:HC_SAMPLE]
    return {'top': top, 'random': rand, 'edge': edge}


# ═════════════════════════ 数据装配 ═════════════════════════

def load_v24_table(trade_date):
    """读取 V2.4 机器产物；缺失则先运行 V2.4（不改其任何数值）"""
    path = os.path.join(REPORT_DIR, f'theme_heat_v24_{trade_date}.csv')
    if not os.path.exists(path):
        print(f'[HC] 未找到 V2.4 产物，先运行 V2.4 生成：{os.path.basename(path)}')
        v24.run(trade_date)
    df = pd.read_csv(path)
    return {str(r['theme']): r for _, r in df.iterrows()}


def member_returns(codes, trade_date):
    from stock_cache import get_recent_trade_dates
    dates = [str(d) for d in get_recent_trade_dates(n=v24.HIST_DAYS, end_date=trade_date)]
    df = v24.fetch_kline(sorted(codes), dates[0], dates[-1])
    P, C, _ = v24.build_matrices(df, dates)
    n = len(dates)
    out = {'TODAY': P.iloc[-1] if n else pd.Series(dtype=float)}
    for wk, w in (('WEEK', v24.WIN_WEEK), ('MONTH', v24.WIN_MONTH)):
        out[wk] = (C.iloc[-1] / C.iloc[n - w - 1] - 1.0) * 100.0 if n > w else pd.Series(dtype=float)
    return out


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


def build(trade_date):
    theme_members, map_path, _ = v24.load_theme_members(trade_date)
    config = v24.load_theme_config_v3()
    mainbiz = v24.load_mainbiz()
    table = load_v24_table(trade_date)
    purity = build_hc_purity(theme_members, config, mainbiz)
    codes = sorted({c for m in theme_members.values() for c in m})
    rets = member_returns(codes, trade_date)

    rows = []
    for t, p in purity.items():
        r = table.get(t)
        row = {'theme': t, 'purity': p, 'layers': p['layers']}
        for wk in ('TODAY', 'WEEK', 'MONTH'):
            pre = wk.lower()
            row[f'{pre}_rank'] = _num(r.get(f'{pre}_rank')) if r is not None else None
            row[f'{pre}_heat'] = _num(r.get(f'{pre}_heat')) if r is not None else None
            row[f'{pre}_n'] = _num(r.get(f'{pre}_n')) if r is not None else None
            row[f'{pre}_breadth'] = _num(r.get(f'{pre}_breadth')) if r is not None else None
            row[f'{pre}_price'] = _num(r.get(f'{pre}_price')) if r is not None else None
            row[f'{pre}_up_ratio'] = _num(r.get(f'{pre}_up_ratio')) if r is not None else None
            row[f'{pre}_activity'] = _num(r.get(f'{pre}_activity')) if r is not None else None
            row[f'{pre}_reliability'] = _num(r.get(f'{pre}_reliability')) if r is not None else None
            row[f'{pre}_flags'] = str(r.get(f'{pre}_flags') or '') if r is not None else ''
        rows.append(row)
        p['today_rank'] = row['today_rank'] if row['today_rank'] is not None else 9999
        p['week_rank'] = row['week_rank'] if row['week_rank'] is not None else 9999
        p['month_rank'] = row['month_rank'] if row['month_rank'] is not None else 9999
        for wk in ('today', 'week', 'month'):
            n = row[f'{wk}_n']
            p[f'{wk}_n'] = 0 if n is None else int(n)
            b = row[f'{wk}_breadth']
            p[f'{wk}_breadth'] = 0.0 if b is None else float(b)
            p[f'{wk}_heat'] = row[f'{wk}_heat']
            p[f'{wk}_price'] = row[f'{wk}_price']
        p['theme'] = t
        p['today_state'] = breadth_state(p['today_heat'], p['today_breadth'], p['today_price'])
        p['week_state'] = breadth_state(p['week_heat'], p['week_breadth'], p['week_price'])
        p['month_state'] = breadth_state(p['month_heat'], p['month_breadth'], p['month_price'])
        p['today_verdict'] = today_state(p, p['today_state'])
        p['week_verdict'] = week_state(p)
        p['month_verdict'] = month_state(p)
        p['errors'] = machine_errors(p)
        p['low_sample'] = [wk.upper() for wk in ('today', 'week', 'month')
                           if 0 < p[f'{wk}_n'] < HC_LOW_SAMPLE]
        p['samples'] = pick_samples(p, rets)
    return {'trade_date': trade_date, 'map_path': map_path, 'purity': purity,
            'rows': rows, 'rets': rets, 'n_themes': len(theme_members)}


# ═════════════════════════ 报告（§二十）═════════════════════════

def hc_ranked(purity, wk, topn=3):
    """§十七 人工确认后的候选序：先按机器 Rank 取序，再剔除纯度/样本/扩散不合格者

    §十九 严禁为了结果好看重排：本函数只做「过滤」，不改变任何主题的相对顺序。
    """
    key = f'{wk.lower()}_rank'
    seq = sorted([p for p in purity.values() if p[key] < 9000], key=lambda p: p[key])
    head = seq[:max(topn * 3, 10)]
    ok = [p for p in head
          if p['purity_level'] in ('PURE', 'ACCEPTABLE')
          and p[f'{wk.lower()}_n'] >= HC_LOW_SAMPLE
          and p[f'{wk.lower()}_state'] not in ('NARROW', 'DIVERGENCE')]
    return seq, ok


def build_report(d):
    purity, trade_date = d['purity'], d['trade_date']
    L = []
    L.append(SEP_FULL)
    L.append('V2.4-HC 主题人工确认')
    L.append(f'交易日：{trade_date}')
    L.append(SEP_FULL)
    L.append('* 本层不重算 Heat、不改权重、不改机器排名；只做成分股验证 + 广度验证 + 样本可靠性验证。')
    L.append(f'* 四分类：CORE / RELATED / WEAK / POLLUTION；纯度分级：PURE / ACCEPTABLE / CONTAMINATED。')
    L.append(f'* 小样本门槛 N<{HC_LOW_SAMPLE} → LOW_SAMPLE_REVIEW；允许输出「暂无经人工确认的最强主题」。')
    L.append('')
    L.append('【主题定义自检（Q1）】')
    L.append(f'  本交易日机器覆盖主题 {d["n_themes"]} 个；其中纯度 PURE '
             f'{sum(1 for p in purity.values() if p["purity_level"]=="PURE")}、ACCEPTABLE '
             f'{sum(1 for p in purity.values() if p["purity_level"]=="ACCEPTABLE")}、'
             f'CONTAMINATED {sum(1 for p in purity.values() if p["purity_level"]=="CONTAMINATED")}。')
    L.append('  主题定义本身不清晰 / 多产业混合者，按 §六 判 INVALID，列入文末「需要人工进一步确认」。')
    L.append('')

    for label in ('TODAY', 'WEEK', 'MONTH'):
        L.append(f'【{label}】')
        seq, ok = hc_ranked(purity, label)
        mech = seq[0] if seq else None
        L.append(f"机器第一：{mech['theme'] if mech else '—'}")
        if not ok:
            L.append('人工确认：暂无经人工确认的明确最强主题（§十八 允许无结果）')
            L.append(f"  原因：机器前 {min(len(seq),6)} 名均存在纯度不足 / N<{HC_LOW_SAMPLE} / "
                     f"扩散不足（NARROW、DIVERGENCE）中至少一项。")
        else:
            L.append(f"人工确认：{ok[0]['theme']}")
            L.append(f"  原因：{_reason(ok[0], label)}")
        # 三个候选位：优先人工确认序列，不足则回落机器序列并标注「未通过人工确认」
        cands = ok + [m for m in seq if m not in ok]
        for i in (1, 2):
            p = cands[i] if i < len(cands) else None
            tag = '二三'[i - 1]
            if p is None:
                L.append(f'第{tag}：—')
                continue
            L.append(f'第{tag}：{p["theme"]}' + ('' if i < len(ok) else '（未通过人工确认）'))
            L.append(f'  状态：{_verdict(p, label)}')
        L.append('')

    # ── §二十【成分股质量检查】：机器候选第一 + 人工确认第一 ──
    L.append('【成分股质量检查】')
    focus = []
    for label in ('TODAY', 'WEEK', 'MONTH'):
        seq, ok = hc_ranked(purity, label)
        for p in ([seq[0]] if seq else []) + ([ok[0]] if ok else []):
            if p is not None and p['theme'] not in [x['theme'] for x in focus]:
                focus.append(p)
    for p in focus:
        L.append(SEP_THIN)
        L.append(f"主题：{p['theme']}　N（映射成员）：{p['total']}")
        L.append(f"CORE：{_f(p['core_ratio'],1)}%　RELATED：{_f(p['related_ratio'],1)}%　"
                 f"WEAK：{_f(p['weak_ratio'],1)}%　POLLUTION：{_f(p['pollution_ratio'],1)}%")
        L.append(f"结论：{p['purity_level']}（VALID {_f(p['valid_ratio'],1)}%）"
                 f"{'｜LOW_SAMPLE_REVIEW ' + '/'.join(p['low_sample']) if p['low_sample'] else ''}")
        if p['errors']:
            L.append('机器错误标记：' + '｜'.join(p['errors']))
        top_gain = _top_pollution(p, d['rets'])
        L.append('主要污染股票（按当日涨幅排序，涨得越多越具误导性）：')
        for i, (nm, cd, r, why) in enumerate(top_gain[:3], 1):
            L.append(f'  {i}. {nm} {cd}　当日 {_f(r,2)}%　{why}')
        weaks = [v for v in p['layers'].values() if v['cls'] == 'WEAK']
        L.append(f'需要人工进一步确认（WEAK {len(weaks)} 只，取前 3）：')
        for i, v in enumerate(weaks[:3], 1):
            L.append(f'  {i}. {v["name"]}　{v["why"]}')
        L.append('成分股抽样（§三 每组 5 只）：')
        for group, tag in (('top', '涨幅贡献Top5'), ('random', '随机5'), ('edge', '边缘5')):
            L.append(f'  [{tag}]')
            for c in p['samples'][group]:
                v = p['layers'][c]
                L.append(f"    {_pad(v['name'],10)} {c}　{_pad(v['cls'],10)}"
                         f"当日 {_f(_mret(d['rets'],'TODAY',c),2)}%　{v['why']}")
    L.append('')

    # ── §十六 最终人工确认表 ──
    L.append('【最终人工确认表】')
    cols = (('主题', 12, '<'), ('今日Heat', 10, '>'), ('周Heat', 9, '>'), ('月Heat', 9, '>'),
            ('N', 6, '>'), ('纯度', 15, '<'), ('今日状态', 16, '<'), ('周状态', 17, '<'),
            ('月状态', 19, '<'), ('人工结论', 0, '<'))

    def _row(vals):
        return ' '.join(v if w == 0 else _pad(str(v), w, a) for (_, w, a), v in zip(cols, vals))

    L.append(_row([c[0] for c in cols]))
    for p in sorted(purity.values(), key=lambda x: x['today_rank']):
        if p['today_rank'] == 9999 and p['week_rank'] == 9999 and p['month_rank'] == 9999:
            continue
        L.append(_row([p['theme'], _f(p['today_heat'], 0), _f(p['week_heat'], 0),
                       _f(p['month_heat'], 0), p['today_n'] or '—', p['purity_level'],
                       p['today_state'], p['week_state'], p['month_state'],
                       _final_verdict(p)]))
    L.append('')

    L.append('【最终人工确认】')
    for label in ('TODAY', 'WEEK', 'MONTH'):
        seq, ok = hc_ranked(purity, label)
        L.append(f"{label}：{ok[0]['theme'] if ok else '暂无经人工确认的主题'}")
    L.append('')
    L.append('最重要观察：')
    L.extend('  ' + s for s in _observations(purity))
    L.append('机器排名与人工判断存在明显偏差：')
    L.extend('  ' + s for s in _deviations(purity))
    L.append(SEP_FULL)
    return {'text': '\n'.join(L), 'trade_date': trade_date}


def _verdict(p, label):
    return {'TODAY': p['today_verdict'], 'WEEK': p['week_verdict'], 'MONTH': p['month_verdict']}[label]


def _reason(p, label):
    parts = []
    if p['low_sample']:
        parts.append(f"N={p[f'{label.lower()}_n']}（<{HC_LOW_SAMPLE}）样本极小，不足以证明形成广泛主题行情")
    if p['purity_level'] == 'PURE':
        parts.append(f"成分纯度 {_f(p['valid_ratio'],0)}%（POLLUTION {_f(p['pollution_ratio'],0)}%）")
    else:
        parts.append(f"成分纯度 {p['purity_level']}（VALID {_f(p['valid_ratio'],0)}%）")
    parts.append(f"{label} 广度 {_f(p[f'{label.lower()}_breadth'],1)}%（{_verdict(p, label)}）")
    return '；'.join(parts) + '。'


def _top_pollution(p, rets):
    out = []
    for c, v in p['layers'].items():
        if v['cls'] != 'POLLUTION':
            continue
        r = _mret(rets, 'TODAY', c)
        out.append((v['name'], c, r, v['why']))
    out.sort(key=lambda x: -(x[2] if x[2] is not None else -999))
    return out


def _final_verdict(p):
    """§七 N<20 必须自动标注 LOW_SAMPLE_REVIEW；§六 污染者直接 INVALID"""
    if p['purity_level'] == 'CONTAMINATED':
        base = 'INVALID（成分污染）'
    elif p['purity_level'] == 'PURE' and 'CONFIRMED' in (p['today_verdict'], p['week_verdict'],
                                                         p['month_verdict']):
        base = 'CONFIRMED'
    elif 'NARROW' in (p['today_verdict'], p['week_verdict'], p['month_verdict']):
        base = 'VALID_BUT_NARROW'
    else:
        base = 'VALID'
    if 'TODAY' in p['low_sample']:
        return f'LOW_SAMPLE_REVIEW({base})'
    return base


def _observations(purity):
    ps = list(purity.values())
    n_bad = sum(1 for p in ps if p['purity_level'] == 'CONTAMINATED')
    top_t = sorted([p for p in ps if p['today_rank'] < 9000], key=lambda p: p['today_rank'])[:10]
    bad_top = [p['theme'] for p in top_t if p['purity_level'] == 'CONTAMINATED']
    out = [f'机器 TODAY TOP10 中有 {len(bad_top)} 个主题成分为 CONTAMINATED：'
           + '、'.join(bad_top) + '。',
           f'全部 {len(ps)} 个主题中 CONTAMINATED {n_bad} 个：'
           f'映射池被宽口径行业板块稀释是系统性现象，不是个别错误（§十五 行业≠主题）。']
    smalls = [p['theme'] for p in ps if p['low_sample']]
    if smalls:
        out.append(f'样本不足 {HC_LOW_SAMPLE} 的主题：{"、".join(smalls)}，'
                   f'其 Heat 高不代表形成广泛主题行情（§七）。')
    return out


def _deviations(purity):
    out = []
    for label in ('TODAY', 'WEEK', 'MONTH'):
        seq, ok = hc_ranked(purity, label)
        if not seq:
            continue
        m = seq[0]
        if not ok or ok[0]['theme'] != m['theme']:
            reason = ('成分为 ' + m['purity_level']) if m['purity_level'] == 'CONTAMINATED' else (
                f"N={m[label.lower()+'_n']}<{HC_LOW_SAMPLE}") if m['low_sample'] else (
                '扩散不足（' + _verdict(m, label) + '）')
            out.append(f'{label}：机器第一 {m["theme"]}（Heat {_f(m[label.lower()+"_heat"],1)}）'
                       f'未通过人工确认 → {reason}；'
                       f'人工确认第一为 {ok[0]["theme"] if ok else "暂无"}。')
    if not out:
        out.append('暂无：三个窗口的机器第一均通过人工确认。')
    return out


def save_outputs(d, report):
    os.makedirs(REPORT_DIR, exist_ok=True)
    base = os.path.join(REPORT_DIR, f'theme_heat_v24_hc_{d["trade_date"]}')
    with open(base + '.md', 'w', encoding='utf-8') as f:
        f.write(report['text'] + '\n')
    rows = []
    for p in sorted(d['purity'].values(), key=lambda x: x['today_rank']):
        rows.append({
            'theme': p['theme'],
            'today_heat': p['today_heat'], 'week_heat': p['week_heat'],
            'month_heat': p['month_heat'],
            'today_rank': None if p['today_rank'] == 9999 else p['today_rank'],
            'week_rank': None if p['week_rank'] == 9999 else p['week_rank'],
            'month_rank': None if p['month_rank'] == 9999 else p['month_rank'],
            'n_map': p['total'],
            'n_core': p['counts'].get('CORE', 0), 'n_related': p['counts'].get('RELATED', 0),
            'n_weak': p['counts'].get('WEAK', 0), 'n_pollution': p['counts'].get('POLLUTION', 0),
            'core_ratio': round(p['core_ratio'], 2), 'related_ratio': round(p['related_ratio'], 2),
            'weak_ratio': round(p['weak_ratio'], 2),
            'pollution_ratio': round(p['pollution_ratio'], 2),
            'valid_ratio': round(p['valid_ratio'], 2), 'purity_level': p['purity_level'],
            'today_n': p['today_n'], 'week_n': p['week_n'], 'month_n': p['month_n'],
            'today_state': p['today_state'], 'week_state': p['week_state'],
            'month_state': p['month_state'],
            'today_verdict': p['today_verdict'], 'week_verdict': p['week_verdict'],
            'month_verdict': p['month_verdict'],
            'low_sample_windows': '|'.join(p['low_sample']),
            'machine_errors': '|'.join(p['errors']),
            'final_verdict': _final_verdict(p),
        })
    pd.DataFrame(rows).to_csv(base + '.csv', index=False, encoding='utf-8-sig')
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n[保存] {os.path.basename(base)}.md / .csv / .json")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    trade_date = v24.resolve_trade_date(arg)
    d = build(trade_date)
    report = build_report(d)
    save_outputs(d, report)
    print(report['text'])


if __name__ == '__main__':
    main()
