#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主题-个股匹配质量审计：via 分布、行业失配率、多主题归属、宽口径板块排查

用法：python diag_theme_match_audit.py [trade_date]
"""
import sys
import os
import json
from collections import Counter, defaultdict

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_PATH = os.path.join(BASE_DIR, 'report_daily', 'theme_stock_map_latest_v2.json')
CFG_CANDS = [
    os.path.join(BASE_DIR, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json'),
    os.path.join(BASE_DIR, 'theme_kg_v3', 'config', 'theme_config.json'),
]

FORCE_VIA = {'leader_company', 'core_company', 'manual_override'}


def theme_industry_terms(c):
    return [t for t in (c.get('sw_industry_match', []) + c.get('cx_industry_match', [])) if t]


def ind_related(ind, terms):
    if not ind:
        return None
    return any((t in ind) or (ind in t) for t in terms)


def main():
    m = json.load(open(MAP_PATH, encoding='utf-8'))
    cfg = None
    for p in CFG_CANDS:
        if os.path.exists(p):
            cfg = json.load(open(p, encoding='utf-8'))
            break
    themes = m.get('themes', {})
    stocks = m.get('stocks', {})
    total_members = sum(len(v) for v in themes.values())
    print('=' * 78)
    print('主题-个股匹配质量审计  映射主题数:%d  成员总数:%d  stocks索引:%d'
          % (len(themes), total_members, len(stocks)))
    print('=' * 78)

    print('\n[1] 全局 via 分布')
    gd = Counter(s.get('via', '') for v in themes.values() for s in v)
    for k, n in gd.most_common():
        print('  %-22s %5d  %5.1f%%' % (k, n, n * 100.0 / total_members))

    print('\n[2] 行业失配审计（成员 tushare 行业与主题行业词表无交集；leader/core/manual 豁免）')
    rows = []
    for tname, members in themes.items():
        c = (cfg or {}).get(tname) or {}
        c2 = None
        if not c:
            for k, v in (cfg or {}).items():
                if isinstance(v, dict) and v.get('name_cn', '') == tname:
                    c2 = v
                    break
        c = c or c2 or {}
        terms = theme_industry_terms(c)
        mism, checked = [], 0
        for s in members:
            if s.get('via') in FORCE_VIA or s.get('irs_layer') == 'core':
                continue
            checked += 1
            ind = s.get('industry', '') or stocks.get(s.get('code', ''), {}).get('industry', '')
            if not ind_related(ind, terms):
                mism.append((s.get('name', ''), ind, s.get('via', ''), s.get('irs_score', 0)))
        rate = len(mism) * 100.0 / checked if checked else 0.0
        ind_cnt = Counter(x[1] for x in mism if x[1])
        rows.append((rate, tname, len(members), checked, len(mism), ind_cnt, mism))
    rows.sort(reverse=True)
    print('  %-8s %6s %6s %6s %8s  %s' % ('主题', '成员', '核验', '失配', '失配率', '失配行业TOP3'))
    for rate, tname, size, checked, nbad, ind_cnt, mism in rows:
        top3 = '、'.join('%s×%d' % (k or '无标签', v) for k, v in ind_cnt.most_common(3))
        print('  %-8s %6d %6d %6d %7.1f%%  %s' % (tname, size, checked, nbad, rate, top3))

    print('\n[3] 失配率最高 5 主题的可疑成员示例（按 IRS 升序，IRS 低=匹配证据最弱）')
    for rate, tname, size, checked, nbad, ind_cnt, mism in rows[:5]:
        if not mism:
            continue
        print('  ── %s（失配 %d/%d）' % (tname, nbad, checked))
        for name, ind, via, irs in sorted(mism, key=lambda x: x[3])[:8]:
            print('     %-8s 行业=%-8s via=%-20s IRS=%s' % (name, ind or '—', via, irs))

    print('\n[4] 多主题归属（stocks.themes 数组长度>1）')
    multi = {code: st.get('themes', []) for code, st in stocks.items() if len(st.get('themes', [])) > 1}
    print('  多主题个股: %d / %d (%.1f%%)' % (len(multi), len(stocks), len(multi) * 100.0 / max(len(stocks), 1)))
    mcnt = Counter(t for v in multi.values() for t in v)
    print('  涉及主题频次TOP8:', '、'.join('%s×%d' % kv for kv in mcnt.most_common(8)))

    print('\n[5] 特例通道定位')
    for tname, members in themes.items():
        for s in members:
            if s.get('via') == 'manual_override':
                print('  manual_override:', tname, s.get('code'), s.get('name'))
    cf = Counter()
    for tname, members in themes.items():
        for s in members:
            if s.get('via') == 'concept_fallback':
                cf[tname] += 1
    print('  concept_fallback 分布:', json.dumps(dict(cf), ensure_ascii=False))

    print('\n[6] 宽口径板块核验：钢铁·金属制品通道成员的行业构成')
    steel = themes.get('钢铁', [])
    bucket = defaultdict(list)
    for s in steel:
        if s.get('via') == 'dc_industry_board' and (s.get('industry', '') or '') not in ('普钢', '特钢', '钢加工', '钢铁', '冶钢原料'):
            bucket[s.get('industry', '') or '无标签'].append(s.get('name', ''))
    for ind, names in sorted(bucket.items(), key=lambda x: -len(x[1]))[:8]:
        print('  %-8s %2d 只: %s' % (ind, len(names), '、'.join(names[:10])))
    print('\n完成。')


if __name__ == '__main__':
    main()
