import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/double_score_top.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('DoubleScore范围:', df['DoubleScore'].min(), '-', df['DoubleScore'].max())
na_count = df['_否决'].notna().sum()
print('否决数:', na_count)
print()
print('龙头类型分布:', df['龙头类型'].value_counts().to_dict())
print()
print('主题分布TOP15:')
for t, c in df['主题'].value_counts().head(15).items():
    print(f'  {t}: {c}')
print()
print('估值空间分布: >=100%', len(df[df['估值空间%']>=100]), '  50-100%', len(df[(df['估值空间%']>=50)&(df['估值空间%']<100)]), '  <50%', len(df[df['估值空间%']<50]))
print()
col_list = list(df.columns)
print('列名:', col_list)
print()
# 检查核心逻辑标签
all_logics = set()
for v in df['核心逻辑'].dropna():
    for tag in str(v).split(' + '):
        all_logics.add(tag.strip())
print('核心逻辑标签:', sorted(all_logics))
