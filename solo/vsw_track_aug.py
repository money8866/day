# -*- coding: utf-8 -*-
"""VSW V2 跟踪分析：对 2026年8月已收盘交易日逐一跑选股，汇总 TOP3 与最终结论"""
import sys
import io
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import volume_surge_select as vsw

DATES = ['20260803', '20260804', '20260805', '20260806', '20260807',
         '20260810', '20260811', '20260812', '20260813', '20260814',
         '20260817', '20260818', '20260819', '20260820']


def verdict_of(res):
    """最终结论：S/A/B 级（B 级需次日确认）可开仓"""
    buyable = [x for x in res[:6]
               if x.get('Eligible') and x.get('Rating') in ('S', 'A', 'B') and not x.get('ForbidTOP')]
    if buyable:
        tag = '（B级·次日确认）' if buyable[0].get('Rating') == 'B' else ''
        return '可开仓' + tag, buyable[0]['名称']
    return '空仓观望', (res[0]['名称'] if res else '-')


def fmt_stock(s):
    theme = s.get('所属主题', '') or '无主题'
    stage = s.get('非一日游阶段', '') or ''
    return (f"{s['名称']}({s['代码']}) FES={s.get('FinalEntryScore', '-'):.1f} {s.get('Rating', 'C')} "
            f"ET={s.get('EntryTimingScore', 0):.0f}({s.get('EntryTimingGrade', 'C')}) "
            f"T1Risk={s.get('T1Risk', '-')} 距MA20={s.get('距MA20', 0):+.1f}% "
            f"5日={s.get('5日涨幅', 0):+.1f}% 主题={theme}{('|' + stage) if stage else ''} "
            f"[{s.get('_v2_label', '')}]")


lines = []
lines.append('# VSW V2 跟踪分析 — 2026年8月交易日')
lines.append('')
lines.append(f'> 生成时间: {datetime.datetime.now():%Y-%m-%d %H:%M} | 模型: V2.0 次日新开仓优先 FinalEntryScore')
lines.append('')
lines.append('| 交易日 | 结论 | TOP1 | TOP2 | TOP3 |')
lines.append('|---|---|---|---|---|')
for d in DATES:
    try:
        res = vsw.run(target_date=d, with_chip=True)
    except Exception as e:
        lines.append(f'| {d} | ERROR | {e} | | |')
        print(f'[{d}] ERROR {e}', flush=True)
        continue
    v, best = verdict_of(res)
    cells = [f"**{v}**", best]
    for s in res[:3]:
        cells.append(fmt_stock(s))
    lines.append('| ' + d + ' | ' + ' | '.join(cells) + ' |')
    print(f'[{d}] {v} | TOP1={best}', flush=True)

lines.append('')
lines.append('## 说明')
lines.append('- FES=FinalEntryScore；ET=EntryTiming(分级)；T1Risk=次日高开低走风险')
lines.append('- 结论：可开仓=存在 S/A/B 级且通过资格过滤（B 级需次日确认）；空仓观望=无高胜率可买标的')
lines.append('- 完整逐日报告见 report_daily/volume_surge_YYYYMMDD.md')

out = r'd:\mystock\report_daily\vsw_track_202608.md'
with open(out, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print(f'\n✅ 跟踪汇总已保存: {out}')
