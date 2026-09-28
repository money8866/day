# -*- coding: utf-8 -*-
"""临时脚本：主题成份股人工确认 HC V1.0 数据底座（只读，不改任何原始数据）

五状态：CORE / RELATED / WEAK / BAD / UNKNOWN
污染率 = (WEAK + BAD) / (N - UNKNOWN)
"""
import os
import sys
import csv
import json
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import theme_heat_v22 as v24
import theme_heat_v24_hc as hc
import pandas as pd

TRADE_DATE = sys.argv[1] if len(sys.argv) > 1 else '20260924'
REPORT_DIR = v24.REPORT_DIR
PREFIX = os.path.join(REPORT_DIR, f'theme_mapping_hc_v1_{TRADE_DATE}')

# 人工预置的「公认核心公司」探针（用于找漏配，不做自动加入）
PROBE = {
    '消费': ['600519.SH', '000858.SZ', '000568.SZ', '002304.SZ', '600809.SH', '603288.SH',
             '605499.SH', '600887.SZ', '000651.SZ', '000333.SZ', '600690.SH', '601888.SH',
             '603345.SH', '000895.SZ', '603288.SH', '600600.SH', '000596.SZ'],
    'AI算力': ['300308.SZ', '300502.SZ', '002281.SZ', '300394.SZ', '002463.SZ', '300476.SZ',
               '002837.SZ', '300442.SZ', '603019.SH', '000977.SZ', '688041.SH', '688256.SH',
               '300474.SZ', '002185.SZ', '688220.SH'],
    '智能驾驶': ['002920.SZ', '601689.SH', '002050.SZ', '688326.SH', '002405.SZ', '300496.SZ',
                 '603596.SH', '002906.SZ', '688208.SH'],
    '军工': ['600760.SH', '000768.SZ', '600893.SH', '002179.SZ', '600372.SH', '688122.SH',
             '600150.SH', '000738.SZ', '600038.SH', '002013.SZ'],
    '机器人': ['300124.SZ', '002472.SZ', '688017.SH', '002527.SZ', '688169.SH', '603486.SH',
               '300024.SZ', '002031.SZ'],
    '信创': ['688111.SH', '600588.SH', '002410.SZ', '600271.SH', '000034.SZ', '603019.SH',
             '688041.SH', '002439.SZ'],
    '创新药': ['600276.SH', '688180.SH', '300347.SZ', '603259.SH', '688235.SH', '002422.SZ',
               '600196.SH', '300558.SZ'],
    '半导体': ['688981.SH', '002371.SZ', '603501.SH', '688012.SH', '002049.SZ', '688008.SH'],
}


def classify5(code, via, txt, cfg, industry, char_ind):
    if cfg is None:
        return 'UNKNOWN', '无对应主题配置'
    ov = hc.HC_OVERRIDES.get(code)
    if ov and ov[0] == cfg.get('name_cn'):
        return ov[1], f'人工复核修正｜{ov[2]}'
    manual = set(cfg.get('leaders') or []) | set(cfg.get('core_stocks') or [])
    if via == 'manual_override':
        return 'CORE', '人工补漏映射（manual_override）'
    if code in manual:
        return 'CORE', f'人工确认名单（{via}）'
    if not txt:
        return 'UNKNOWN', '无主营文本（数据缺口，需人工复核）'
    layer, why = v24.classify_member(code, via, txt, cfg)
    if layer == 'CORE':
        return 'CORE', why
    if layer == 'NORMAL':
        return 'RELATED', why
    if '排除词' in why:
        return 'BAD', why
    if via in hc.CONCEPT_SOURCES:
        return 'WEAK', f'{via}｜仅概念标签来源，主营无证据'
    if industry in char_ind:
        return 'WEAK', f'{via}｜行业「{industry}」属主题特征行业'
    return 'BAD', f'{via}｜仅因宽口径行业「{industry or "未知"}」被纳入'


def quality(valid_k, bad_k, unknown_r):
    if bad_k >= 0.30 or valid_k < 0.40:
        return 'BAD'
    if bad_k <= 0.05 and valid_k >= 0.70 and unknown_r <= 0.30:
        return 'OK'
    return 'WARN'


def main():
    members, map_path, _ = v24.load_theme_members(TRADE_DATE)
    config = v24.load_theme_config_v3()
    mainbiz = v24.load_mainbiz()
    t24 = {}
    for _, r in pd.read_csv(os.path.join(REPORT_DIR, f'theme_heat_v24_{TRADE_DATE}.csv')).iterrows():
        t24[str(r['theme'])] = r

    stats, mrows = {}, []
    for t, recs in members.items():
        cfg = config.get(t)
        lay = {c: {'layer': v24.classify_member(c, rec['via'], mainbiz.get(c, ''), cfg)[0]}
               for c, rec in recs.items()} if cfg else {}
        char_ind = hc.characteristic_industries(lay, recs) if cfg else set()
        cls, whys = {}, {}
        for c, rec in recs.items():
            k, w = classify5(c, rec['via'], mainbiz.get(c, ''), cfg,
                             rec.get('industry') or '', char_ind)
            cls[c], whys[c] = k, w
            mrows.append({'theme': t, 'code': c, 'name': rec['name'],
                          'industry': rec.get('industry') or '', 'via': rec['via'],
                          'cls': k, 'why': w, 'has_text': 1 if mainbiz.get(c) else 0,
                          'mainbiz': (mainbiz.get(c) or '')[:120]})
        n = len(recs)
        cnt = collections.Counter(cls.values())
        known = n - cnt['UNKNOWN']
        valid_k = (cnt['CORE'] + cnt['RELATED']) / known if known else 0.0
        bad_k = cnt['BAD'] / known if known else 0.0
        weak_k = cnt['WEAK'] / known if known else 0.0
        unk_r = cnt['UNKNOWN'] / n if n else 0.0
        pol = (cnt['WEAK'] + cnt['BAD']) / known * 100 if known else 0.0
        top_rank = min([x for x in (t24.get(t)['today_rank'] if t in t24 else 9999,
                                    t24.get(t)['week_rank'] if t in t24 else 9999,
                                    t24.get(t)['month_rank'] if t in t24 else 9999)
                        if pd.notna(x)] or [9999])
        top = max(0.0, (11 - top_rank) / 10) if top_rank <= 10 else 0.0
        anom = 1.0 if (n >= 300 or (t in t24 and bool(t24[t].get('count_anomaly')))) else 0.0
        score = 35 * bad_k + 25 * weak_k + 15 * unk_r + 15 * top + 10 * anom
        stats[t] = {
            'theme': t, 'n': n, 'core': cnt['CORE'], 'related': cnt['RELATED'],
            'weak': cnt['WEAK'], 'bad': cnt['BAD'], 'unknown': cnt['UNKNOWN'],
            'core_r': cnt['CORE'] / n * 100 if n else 0, 'related_r': cnt['RELATED'] / n * 100 if n else 0,
            'weak_r': cnt['WEAK'] / n * 100 if n else 0, 'bad_r': cnt['BAD'] / n * 100 if n else 0,
            'unknown_r': unk_r * 100, 'poll': pol,
            'quality': quality(valid_k, bad_k, unk_r),
            'valid_k': valid_k * 100, 'bad_k': bad_k * 100, 'weak_k': weak_k * 100,
            'char_ind': sorted(x for x in char_ind if x),
            'weak_text': sum(1 for c, k in cls.items() if k == 'WEAK' and mainbiz.get(c)),
            'cls': cls, 'whys': whys, 'score': score,
            'v24_mq': t24[t]['mapping_quality'] if t in t24 else None,
            'v24_n': t24[t]['today_n'] if t in t24 else None,
        }

    # summary csv
    with open(PREFIX + '_summary.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['theme', 'N', 'CORE', 'RELATED', 'WEAK', 'BAD', 'UNKNOWN', 'KNOWN',
                    'CORE_RATIO', 'RELATED_RATIO', 'WEAK_RATIO', 'BAD_RATIO', 'UNKNOWN_RATIO',
                    '污染率(WEAK+BAD)/KNOWN', 'Quality_V1', '修复优先级分', 'V24_MappingQuality'])
        for t, s in sorted(stats.items(), key=lambda x: -x[1]['score']):
            w.writerow([t, s['n'], s['core'], s['related'], s['weak'], s['bad'], s['unknown'],
                        s['n'] - s['unknown'], f"{s['core_r']:.1f}", f"{s['related_r']:.1f}",
                        f"{s['weak_r']:.1f}", f"{s['bad_r']:.1f}", f"{s['unknown_r']:.1f}",
                        f"{s['poll']:.1f}", s['quality'], f"{s['score']:.1f}", s['v24_mq']])
    with open(PREFIX + '_members.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['theme', 'code', 'name', 'industry', 'via',
                                          'cls', 'why', 'has_text', 'mainbiz'])
        w.writeheader()
        w.writerows(mrows)

    tot = collections.Counter()
    for s in stats.values():
        for k in ('core', 'related', 'weak', 'bad', 'unknown'):
            tot[k] += s[k]
    print('=' * 78)
    print(f'[HC V1] {TRADE_DATE} 全局 CORE {tot["core"]} / RELATED {tot["related"]} / '
          f'WEAK {tot["weak"]} / BAD {tot["bad"]} / UNKNOWN {tot["unknown"]} = {sum(tot.values())}')
    print(f'[HC V1] 已知成员 {sum(tot.values()) - tot["unknown"]}，污染率 '
          f'{(tot["weak"] + tot["bad"]) / (sum(tot.values()) - tot["unknown"]) * 100:.1f}%')
    print('[HC V1] Quality: ' + str(dict(collections.Counter(s['quality'] for s in stats.values()))))
    print('-' * 78)
    print(f'{"主题":<8}{"N":>5}{"CORE":>6}{"REL":>5}{"WEAK":>6}{"BAD":>6}{"UNK":>6}'
          f'{"有效%":>7}{"污染%":>7}{"UNK%":>6}{"优先分":>7}  {"Q":<5}{"V24MQ":>6}')
    for t, s in sorted(stats.items(), key=lambda x: -x[1]['score']):
        print(f'{t:<8}{s["n"]:>5}{s["core"]:>6}{s["related"]:>5}{s["weak"]:>6}{s["bad"]:>6}'
              f'{s["unknown"]:>6}{s["valid_k"]:>7.1f}{s["poll"]:>7.1f}{s["unknown_r"]:>6.1f}'
              f'{s["score"]:>7.1f}  {s["quality"]:<5}{str(s["v24_mq"]):>6}')

    print('-' * 78)
    print('[修复优先级 TOP10]')
    for i, (t, s) in enumerate(sorted(stats.items(), key=lambda x: -x[1]['score'])[:10], 1):
        print(f'  {i}. {t:<8} 优先分 {s["score"]:>5.1f}｜BAD {s["bad"]}/{s["n"]}({s["bad_r"]:.0f}%)'
              f'｜WEAK {s["weak"]}/{s["n"]}({s["weak_r"]:.0f}%)｜UNK {s["unknown"]}({s["unknown_r"]:.0f}%)'
              f'｜污染 {s["poll"]:.0f}%')

    # 漏配探针
    print('-' * 78)
    print('[漏配探针：公认核心公司是否在池内]')
    miss = []
    for t, codes in PROBE.items():
        pool = members.get(t, {})
        for c in dict.fromkeys(codes):
            if c in pool:
                k = stats[t]['cls'][c]
                if k != 'CORE':
                    miss.append((t, c, pool[c]['name'], f'在池内但={k}'))
            else:
                miss.append((t, c, '', '不在池内'))
    for t, c, nm, s in miss:
        print(f'  {t:<8} {c} {nm:<8} {s}')
    with open(PREFIX + '_probe.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['theme', 'code', 'name', 'problem'])
        w.writerows(miss)

    # BAD 明细
    with open(PREFIX + '_remove_candidates.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['theme', 'code', 'name', 'industry', 'via', 'cls', 'why'])
        for r in mrows:
            if r['cls'] in ('BAD',):
                w.writerow([r['theme'], r['code'], r['name'], r['industry'], r['via'],
                            r['cls'], r['why']])
    with open(PREFIX + '_review_candidates.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['theme', 'code', 'name', 'industry', 'via', 'cls', 'why'])
        for r in mrows:
            if r['cls'] in ('WEAK', 'UNKNOWN'):
                w.writerow([r['theme'], r['code'], r['name'], r['industry'], r['via'],
                            r['cls'], r['why']])

    print('-' * 78)
    for t, s in sorted(stats.items(), key=lambda x: -x[1]['bad'])[:12]:
        bads = [r for r in mrows if r['theme'] == t and r['cls'] == 'BAD']
        if bads:
            print(f'[BAD] {t}（{len(bads)}）: ' + '、'.join(f'{r["name"]}({r["industry"]})' for r in bads[:60]))
    print('-' * 78)
    for t, s in sorted(stats.items(), key=lambda x: -x[1]['unknown'])[:10]:
        us = [r for r in mrows if r['theme'] == t and r['cls'] == 'UNKNOWN']
        if us:
            print(f'[UNKNOWN] {t}（{len(us)}）: ' + '、'.join(r['name'] for r in us[:60]))
    print('-' * 78)
    for t, s in sorted(stats.items(), key=lambda x: -x[1]['weak'])[:12]:
        ws = [r for r in mrows if r['theme'] == t and r['cls'] == 'WEAK']
        if ws:
            print(f'[WEAK] {t}（{len(ws)}）: ' + '、'.join(f'{r["name"]}({r["industry"]})' for r in ws[:60]))

    # 贡献度归因
    codes = sorted({r['code'] for r in mrows})
    rets = hc.member_returns(codes, TRADE_DATE)
    print('-' * 78)
    print('[TOP10 贡献度归因：有效成员中涨幅前 6]')
    for wk, col in (('TODAY', 'today_rank'), ('WEEK', 'week_rank'), ('MONTH', 'month_rank')):
        print(f'── {wk}')
        rows = [(t, r) for t, r in t24.items() if pd.notna(r[col])]
        rows.sort(key=lambda x: x[1][col])
        for t, r in rows[:10]:
            s = stats.get(t)
            if not s:
                continue
            valid = [c for c, k in s['cls'].items() if k in ('CORE', 'RELATED')]
            ser = rets[wk]
            got = []
            for c in valid:
                x = hc._mret(rets, wk, c)
                if x is not None:
                    got.append((x, c))
            got.sort(reverse=True)
            nm = {r['code']: r['name'] for r in mrows if r['theme'] == t}
            s2 = ' '.join(f'{nm.get(c, c)}{"(C)" if s["cls"][c] == "CORE" else "(R)"}{x:+.1f}' for x, c in got[:6])
            print(f'  #{int(r[col])} {t:<8} 有效成员 {len(valid):>3}（有收益 {len(got):>3}）｜{s2}')


if __name__ == '__main__':
    main()
