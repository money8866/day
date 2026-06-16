import sys
sys.path.insert(0, '.')
import pandas as pd

# 读取主题形态选股结果
df = pd.read_csv('cache_backbone_tushare/theme_pattern_stocks.csv')

# 筛选物理AI主题的补涨中军
phys_ai_buzhang = df[(df['theme_name'] == '物理AI') & (df['buy_type'] == '补涨中军')]

print('物理AI主题补涨中军:')
print('=' * 80)
print(phys_ai_buzhang[['code', 'name', 'theme_name', 'avg_amount_20', 'buzhang_score']])

# 检查太辰光是否在结果中
taichenguang = df[(df['code'] == '300570.SZ') | (df['name'] == '太辰光')]
print('\n\n太辰光在结果中的情况:')
if not taichenguang.empty:
    print(taichenguang)
else:
    print('太辰光不在结果中')

# 检查物理AI主题的所有股票
phys_ai_all = df[df['theme_name'] == '物理AI']
print(f'\n\n物理AI主题共 {len(phys_ai_all)} 只股票:')
print(phys_ai_all[['code', 'name', 'buy_type', 'avg_amount_20', 'buzhang_score']])