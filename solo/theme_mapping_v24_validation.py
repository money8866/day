# -*- coding: utf-8 -*-
"""
主题成分股 V2.4 抽样验证：分层随机盲抽 + 误杀/误纳入率检验

输入：report_daily/theme_mapping_v3_{DATE}.csv（V3 四级判定全量）
产物：
  1) theme_mapping_v24_validation_sample_{DATE}.csv     盲抽样本（无价格/热度/排名）
  2) theme_mapping_v24_validation_judgement_{DATE}.csv  人工判定回填表（模板）
  3) 若回填表已填写，则输出混淆矩阵 + 指标 + 五张表

回归模式（环境变量 VALIDATION_FIXED_SET=1）：
  固定人工判定真值集（已回填的 (主题,代码) 集合）不变，仅替换系统分类，
  用 theme_mapping_v3_{DATE}_baseline.csv 作修正前对照，输出前后四项指标对比。
  依据：报告 §七 优先修正顺序第 6 步「修正后重跑回归验证」。

本脚本不改任何 Heat 公式，不写回生产映射。
"""
import csv
import json
import os
import random
import collections

TRADE_DATE = '20260924'
RD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_daily')
SAMPLE_N = 20
RANDOM_SEED = 20260924

THEMES = ['AI算力', '智能驾驶', '军工', '电力', '消费']
CLASSES = ['CORE', 'CHAIN', 'UNCERTAIN', 'EXCLUDE']

TRUE_CORE, TRUE_CHAIN, TRUE_UNC, TRUE_EXCL = 'TRUE_CORE', 'TRUE_CHAIN', 'TRUE_UNC', 'TRUE_EXCL'
SYS_TO_TRUE = {'CORE': TRUE_CORE, 'CHAIN': TRUE_CHAIN,
               'UNCERTAIN': TRUE_UNC, 'EXCLUDE': TRUE_EXCL}
TRUE_ORDER = [TRUE_CORE, TRUE_CHAIN, TRUE_UNC, TRUE_EXCL]

SAMPLE_PATH = os.path.join(RD, f'theme_mapping_v24_validation_sample_{TRADE_DATE}.csv')
JUDGE_PATH = os.path.join(RD, f'theme_mapping_v24_validation_judgement_{TRADE_DATE}.csv')
AUDIT_PATH = os.path.join(RD, f'theme_mapping_v3_{TRADE_DATE}.csv')
BASELINE_PATH = os.path.join(RD, f'theme_mapping_v3_{TRADE_DATE}_baseline.csv')


def read_csv(path):
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, fieldnames, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def build_sample(audit):
    """分层随机盲抽：每主题 CORE/CHAIN/UNCERTAIN/EXCLUDE 各 ≤20。
    盲抽字段只有：代码 / 名称 / 主营文本 / 行业 / 主题 / 系统分类 / 系统证据。
    不含：价格、涨幅、热度、排名、回测收益、旧 HC 分类。"""
    rnd = random.Random(RANDOM_SEED)
    rows = []
    for theme in THEMES:
        for cls in CLASSES:
            grp = [a for a in audit if a['theme'] == theme and a['classification'] == cls]
            grp.sort(key=lambda x: x['ts_code'])   # 先排序保证可复现
            rnd.shuffle(grp)
            for a in grp[:SAMPLE_N]:
                rows.append({
                    '主题': theme,
                    '代码': a['ts_code'],
                    '名称': a['name'],
                    '主营文本': (a.get('mainbiz') or '').strip(),
                    '行业': a.get('industry', ''),
                    '产业链位置': '',          # 人工核验时填写
                    '系统分类': cls,
                    '系统证据': a.get('evidence', ''),
                })
    return rows


def build_summary(filled, jmap):
    """混淆矩阵 + 误纳入/误剔除率 + 诊断。filled 需含 主题/代码/系统分类。"""
    summary = {}
    for theme in THEMES:
        sub = [r for r in filled if r['主题'] == theme]
        if not sub:
            continue
        mat = collections.Counter()
        for r in sub:
            mat[(r['系统分类'], jmap[(theme, r['代码'])]['人工分类'])] += 1

        sys_final = [r for r in sub if r['系统分类'] in ('CORE', 'CHAIN')]
        sys_core = [r for r in sub if r['系统分类'] == 'CORE']
        sys_excl = [r for r in sub if r['系统分类'] == 'EXCLUDE']

        fi = sum(1 for r in sys_final
                 if jmap[(theme, r['代码'])]['人工分类'] in (TRUE_UNC, TRUE_EXCL))
        fe = sum(1 for r in sys_excl
                 if jmap[(theme, r['代码'])]['人工分类'] in (TRUE_CORE, TRUE_CHAIN))
        cp = sum(1 for r in sys_core
                 if jmap[(theme, r['代码'])]['人工分类'] == TRUE_CORE)
        fp = sum(1 for r in sys_final
                 if jmap[(theme, r['代码'])]['人工分类'] in (TRUE_CORE, TRUE_CHAIN))

        fi_rate = fi * 100.0 / len(sys_final) if sys_final else 0.0
        fe_rate = fe * 100.0 / len(sys_excl) if sys_excl else 0.0
        core_prec = cp * 100.0 / len(sys_core) if sys_core else 0.0
        final_prec = fp * 100.0 / len(sys_final) if sys_final else 0.0

        # 诊断（规格 §十四，仅诊断不改规则；EXCLUDE 层为空时误剔除率不可测 → 记为 N/A）
        fe_na = not sys_excl
        if fe_na:
            diag = 'N/A(无EXCLUDE样本)' if not fi_rate else (
                'TOO_BROAD' if fi_rate >= 25 else 'BALANCED(仅误纳入可测)')
        elif fe_rate >= 25 or fe_rate > fi_rate * 1.5:
            diag = 'TOO_STRICT'
        elif fi_rate >= 25 or fi_rate > fe_rate * 1.5:
            diag = 'TOO_BROAD'
        elif fi_rate < 20 and fe_rate < 20:
            diag = 'BALANCED'
        else:
            diag = 'MIXED'   # 双向都超 20% 但未触发 STRICT/BROAD 判定，不得误标 BALANCED

        summary[theme] = {
            'n_sample': len(sub), 'n_core': len(sys_core), 'n_final': len(sys_final),
            'n_excl': len(sys_excl),
            'false_inclusion': fi, 'false_exclusion': fe,
            'fi_rate': round(fi_rate, 1), 'fe_rate': round(fe_rate, 1),
            'core_precision': round(core_prec, 1), 'final_precision': round(final_prec, 1),
            'diag': diag,
            'matrix': {k: {t: mat[(k, t)] for t in TRUE_ORDER} for k in SYS_TO_TRUE},
        }
    return summary


def print_summary(summary, title):
    print('\n' + '=' * 68)
    print(title)
    print('=' * 68)
    for theme in THEMES:
        if theme not in summary:
            continue
        m = summary[theme]
        print(f"\n{theme}  样本 {m['n_sample']}（CORE {m['n_core']}/CHAIN "
              f"{m['n_final'] - m['n_core']}/EXCL {m['n_excl']}）")
        print(f"  Final Precision {m['final_precision']}%  CORE Precision {m['core_precision']}%")
        print(f"  误纳入率 {m['fi_rate']}%（{m['false_inclusion']}/{m['n_final']}）  "
              f"误剔除率 {m['fe_rate']}%（{m['false_exclusion']}/{m['n_excl']}）  -> {m['diag']}")
        print('        ' + ''.join(f'{t:>12}' for t in TRUE_ORDER))
        for k in ['CORE', 'CHAIN', 'UNCERTAIN', 'EXCLUDE']:
            print(f'  {k:<8}' + ''.join(f"{m['matrix'][k][t]:>12}" for t in TRUE_ORDER))


def run_fixed_set(audit, jmap, judge_rows):
    """回归模式：真值集固定，仅替换系统分类，对比修正前后四项指标。"""
    base_map = {}
    if os.path.exists(BASELINE_PATH):
        base_map = {(a['theme'], a['ts_code']): a['classification']
                    for a in read_csv(BASELINE_PATH)}
    new_map = {(a['theme'], a['ts_code']): a for a in audit}

    filled_new, filled_old, cmp_rows = [], [], []
    for r in judge_rows:
        key = (r['主题'], r['代码'])
        if not r['人工分类'].strip() or key not in new_map:
            continue
        a = new_map[key]
        filled_new.append({'主题': r['主题'], '代码': r['代码'], '系统分类': a['classification']})
        old = base_map.get(key, '')
        if old:
            filled_old.append({'主题': r['主题'], '代码': r['代码'], '系统分类': old})
        cmp_rows.append({
            '主题': r['主题'], '代码': r['代码'], '名称': r['名称'],
            '人工分类': r['人工分类'], '修正前系统分类': old or 'NA',
            '修正后系统分类': a['classification'], '是否变化': 'Y' if old and old != a['classification'] else '',
            '修正后证据类型': a.get('evidence_type', ''), '修正后证据': a.get('evidence', ''),
            '修正后置信度': a.get('confidence', ''),
        })

    cmp_path = os.path.join(RD, f'theme_mapping_v24_regression_{TRADE_DATE}.csv')
    write_csv(cmp_path, list(cmp_rows[0].keys()), cmp_rows)
    print(f'\n[回归明细] {cmp_path}（{len(cmp_rows)} 条）')

    s_new = build_summary(filled_new, jmap)
    s_old = build_summary(filled_old, jmap) if filled_old else {}
    print_summary(s_old, '修正前（baseline 规则，同一真值集）')
    print_summary(s_new, '修正后（当前规则，同一真值集）')

    print('\n' + '=' * 68)
    print('修正前后对照（同一人工判定真值集）')
    print('=' * 68)
    print(f"{'主题':<8}{'指标':<16}{'修正前':>10}{'修正后':>10}")
    for theme in THEMES:
        if theme not in s_new:
            continue
        n, o = s_new[theme], s_old.get(theme, {})
        for label, key in [('最终N', 'n_final'), ('CORE Precision', 'core_precision'),
                           ('Final Precision', 'final_precision'), ('误纳入率', 'fi_rate'),
                           ('误剔除率', 'fe_rate')]:
            print(f"{theme:<8}{label:<16}{str(o.get(key, 'NA')):>10}{str(n[key]):>10}")
        print(f"{'':<8}{'诊断':<16}{str(o.get('diag', 'NA')):>10}{str(n['diag']):>10}")

    json_path = os.path.join(RD, f'theme_mapping_v24_regression_{TRADE_DATE}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'trade_date': TRADE_DATE, 'mode': 'FIXED_TRUTH_SET',
                   'baseline': s_old, 'current': s_new}, f, ensure_ascii=False, indent=2)
    print(f'\n-> {json_path}')


def main():
    audit = read_csv(AUDIT_PATH)
    print(f'载入 V3 审计全量 {len(audit)} 条')

    judge_rows = read_csv(JUDGE_PATH) if os.path.exists(JUDGE_PATH) else []
    jmap = {}
    for r in judge_rows:
        if r['人工分类'].strip():
            jmap[(r['主题'], r['代码'])] = r

    # ---------------- 回归模式：真值集固定，仅替换系统分类
    if os.environ.get('VALIDATION_FIXED_SET') == '1':
        print(f'[回归模式] 人工判定真值集 {len(jmap)} 条，修正前对照 '
              f'{os.path.basename(BASELINE_PATH)}')
        run_fixed_set(audit, jmap, judge_rows)
        return

    # ---------------- 1) 盲抽样本
    sample = build_sample(audit)
    write_csv(SAMPLE_PATH, list(sample[0].keys()), sample)
    print(f'\n[盲抽样本] {SAMPLE_PATH}（{len(sample)} 条）')
    dist = collections.Counter((r['主题'], r['系统分类']) for r in sample)
    for theme in THEMES:
        s = ' '.join(f'{c}={dist[(theme, c)]}' for c in CLASSES)
        print(f'  {theme}: {s}')

    # ---------------- 2) 判定回填模板
    if not os.path.exists(JUDGE_PATH):
        tmpl = [{'主题': r['主题'], '代码': r['代码'], '名称': r['名称'],
                 '人工分类': '', '证据等级': '', '人工理由': ''} for r in sample]
        write_csv(JUDGE_PATH, ['主题', '代码', '名称', '人工分类', '证据等级', '人工理由'], tmpl)
        print(f'\n[判定模板] {JUDGE_PATH}（{len(tmpl)} 条，待人工填写）')
        return

    # ---------------- 3) 有回填 -> 计算
    filled = [r for r in sample if (r['主题'], r['代码']) in jmap]
    print(f'\n[判定] 已回填 {len(filled)}/{len(sample)} 条')
    if not filled:
        print('  回填表为空，先填写人工分类（TRUE_CORE/TRUE_CHAIN/TRUE_UNC/TRUE_EXCL）')
        return

    summary = build_summary(filled, jmap)
    print_summary(summary, '抽样验证结果（分层盲抽）')

    json_path = os.path.join(RD, f'theme_mapping_v24_validation_{TRADE_DATE}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f'\n-> {json_path}')


if __name__ == '__main__':
    main()
