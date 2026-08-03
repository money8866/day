import sqlite3, json
CACHE_DIR = r'D:\mystock\cache_daily'
db = sqlite3.connect(f'{CACHE_DIR}/tail_signal_tracker.db')
rows = db.execute('''
    SELECT ts_code, name, theme, signal, total_score, attack_score, structure_score, 
           position_score, theme_score, tech_score, trap_penalty, pct_chg, detail_json
    FROM tail_signal_tracker 
    WHERE signal_date = '20260803'
    ORDER BY total_score DESC
''').fetchall()
db.close()

print(f'=== 「猎尾」尾盘突袭信号 [20260803] 完整列表 ===')
print(f'共 {len(rows)} 只候选\n')
header = f"{'排名':<4} {'代码':<12} {'名称':<10} {'主题':<14} {'总分':>4} {'攻击':>4} {'结构':>4} {'位置':>4} {'共振':>4} {'技术':>4} {'诱多':>4} {'信号':<6} {'涨幅':>7} {'层级':<6}"
print(header)
print('-' * 110)

for i, (code, name, theme, signal, total, attack, structure, pos, 
        theme_sc, tech, trap, pct, detail_json) in enumerate(rows, 1):
    detail = json.loads(detail_json) if detail_json else {}
    layer = detail.get('layer', '')
    layer_tag = {'leader': '龙头', 'middle': '中军'}.get(layer, '')
    emoji = {'强买入': '✅', '买入': '🟢', '关注': '👀'}.get(signal, '')
    trap_str = f'-{trap}' if trap > 0 else '0'
    print(f'{i:<4} {code:<12} {name:<10} {theme:<14} {total:>4} {attack:>4} {structure:>4} {pos:>4} {theme_sc:>4} {tech:>4} {trap_str:>4} {emoji:<6} {pct:>+6.1f}% {layer_tag:<6}')

print('-' * 110)

# 统计
strong = sum(1 for r in rows if r[3] == '强买入')
buy = sum(1 for r in rows if r[3] == '买入')
watch = sum(1 for r in rows if r[3] == '关注')
leader = sum(1 for r in rows if json.loads(r[12] or '{}').get('layer') == 'leader')
middle = sum(1 for r in rows if json.loads(r[12] or '{}').get('layer') == 'middle')
print(f'\n📊 统计: 强买入{strong}  买入{buy}  关注{watch}')
print(f'🏆 龙头{leader}只  中军{middle}只')

# 按主题统计
from collections import Counter
theme_cnt = Counter(r[2] for r in rows)
print(f'\n📊 按主题分布:')
for t, c in theme_cnt.most_common():
    ld = sum(1 for r in rows if r[2] == t and json.loads(r[12] or '{}').get('layer') == 'leader')
    md = sum(1 for r in rows if r[2] == t and json.loads(r[12] or '{}').get('layer') == 'middle')
    ld_str = f' 龙头{ld}' if ld else ''
    md_str = f' 中军{md}' if md else ''
    print(f'  {t:<14} {c:>3}只{ld_str}{md_str}')