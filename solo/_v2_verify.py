"""V2.0 验收核对（G1~G15 可自动判定项）——只读，不改任何数据"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(BASE, '_v2_verify.log'), 'w', encoding='utf-8')
sys.stdout = LOG

CSV = os.path.join(BASE, 'report_daily', 'theme_heat_v24_20260924.csv')
MAP = os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json')
MEM = os.path.join(BASE, 'theme_membership_v2.json')

import pandas as pd

df = pd.read_csv(CSV, dtype={'theme': str}).fillna('')
mraw = json.load(open(MAP, encoding='utf-8'))
themes = mraw['themes']
mem = json.load(open(MEM, encoding='utf-8'))

print('=== G12 其他主题成分数不变（CSV n_map vs theme_stock_map_v2）===')
bad = []
for t, recs in themes.items():
    row = df[df['theme'] == t]
    if row.empty:
        bad.append((t, 'CSV 缺行', len(recs)))
        continue
    got = int(row.iloc[0]['n_map'])
    if got != len(recs):
        bad.append((t, got, len(recs)))
print('主题数', len(themes), '｜不一致', len(bad))
for b in bad:
    print('   ', b)
print('n_stocks(源映射)', mraw.get('n_stocks'), 'n_themes', mraw.get('n_themes'))

print('')
print('=== G1 旧「AI算力」一级主题不存在 ===')
hit = [t for t in df['theme'] if 'AI算力' in str(t)]
print('CSV 中匹配 "AI算力" 的主题：', hit or '无')

print('')
print('=== G6/G7/G8/G9 五主题四级规模与准入 ===')
AI5 = ('CPO', '液冷', 'AI服务器', '算力运营', 'AI应用')
for t in AI5:
    r = df[df['theme'] == t]
    if r.empty:
        print(t, 'CSV 缺行')
        continue
    r = r.iloc[0]
    mv = mem['themes'][t]
    same = (int(r['core_n']) == mv['core_n'] and int(r['extension_n']) == mv['extension_n']
            and int(r['related_n']) == mv['related_n'])
    print(f"{t:<8} CORE {r['core_n']:>4}  EXT {r['extension_n']:>4}  REL {r['related_n']:>4}"
          f"  SR {float(r['sample_reliability']):.4f}  MQ {float(r['membership_quality']):.4f}"
          f"  FLAG {r['sample_flag']:<20} 与 membership_v2 一致={same}")

print('')
print('=== G10/G11 AdjustedHeat 与 RawHeat 同时输出且受低样本惩罚 ===')
for t in AI5:
    r = df[df['theme'] == t]
    if r.empty:
        continue
    r = r.iloc[0]
    print(f"{t:<8} month: RawHeat {r['month_heat_raw']:>6} -> AdjHeat {r['month_adjusted_heat']:>6}"
          f"  (线上 heat {r['month_heat']:>6} / rank {r['month_rank']:>3} / rank_adj {r['month_rank_adj']:>3})"
          f"  乘数 {float(r['sample_reliability']) * float(r['membership_quality']):.4f}")

print('')
print('=== G13 污染检查（全 36 主题）===')
rows = df[df['in_universe'] == True] if 'in_universe' in df.columns else df
print('  被 ≥5 个主题同时纳入的成员数（cross_pollution 合计）：',
      int(pd.to_numeric(df['cross_pollution'], errors='coerce').fillna(0).sum()))
print('  membership_v2 pollution_check：',
      json.dumps(mem['pollution_check'], ensure_ascii=False))
print('  MappingStatus 分布：', df['mapping_status'].value_counts().to_dict())

print('')
print('=== G2 旧 81 只迁移表完备性 ===')
print('  迁移记录', len(mem['old81_migration']), '/ 81 =',
      f"{len(mem['old81_migration']) / 81 * 100:.0f}%")
from collections import Counter
print('  mapping_action 分布：',
      dict(Counter(m['mapping_action'] for m in mem['old81_migration'])))
print('  v2_tier 分布：', dict(Counter(m['v2_tier'] for m in mem['old81_migration'])))

print('')
print('=== G14 AI服务器是否人为补员（列全部成员）===')
mv = mem['themes']['AI服务器']
for k, cs in (('CORE', mv['cores']), ('EXTENSION', mv['extensions']), ('RELATED', mv['relateds'])):
    nm = {r['ts_code']: r['name'] for r in mem['records']}
    print(f'  {k} ({len(cs)})：', '、'.join(f'{nm.get(c, c)}' for c in cs))
print('  sample_reliability', mv['sample_reliability'], 'sample_flag', mv['sample_flag'])

print('')
print('=== §29 算力运营成员构成 ===')
mv = mem['themes']['算力运营']
nm = {r['ts_code']: r['name'] for r in mem['records']}
for k, cs in (('CORE', mv['cores']), ('EXTENSION', mv['extensions']), ('RELATED', mv['relateds'])):
    print(f'  {k} ({len(cs)})：', '、'.join(f'{nm.get(c, c)}' for c in cs))
print('  sample_reliability', mv['sample_reliability'], 'sample_flag', mv['sample_flag'])
print('  sample_flag 规则 -> CORE<5 LOW_SAMPLE / CORE<3 INSUFFICIENT_SAMPLE')

print('')
print('=== §30 朗威股份专项 ===')
for r in mem['records']:
    if r['ts_code'] == '301202.SZ':
        print('  PRIMARY', r['primary_theme'], '| Tier', r['tier'],
              '| Score', r['theme_membership_score'], '| AMBIGUOUS', r['is_ambiguous'])
        print('  SECONDARY', r['secondary_theme'])
        for c in r['candidates']:
            print('   候选', c['theme'], c['score'], c['tier'])
        print('  依据', r['business_basis'])

LOG.close()
