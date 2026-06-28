import pandas as pd, os
d = r'D:\mystock\solo\trend_feature_output'
f = sorted([x for x in os.listdir(d) if 'qualified' in x and x.endswith('.csv')], reverse=True)[0]
df = pd.read_csv(os.path.join(d, f))
df['entry_date'] = df['entry_date'].astype(str)
df = df[(df['entry_date'] >= '20260501') & (df['entry_date'] <= '20260625')].reset_index(drop=True)
main = df[~df['ts_code'].str.startswith(('300','301','688','920'))].copy()
dual = df[df['ts_code'].str.startswith(('300','301','688'))].copy()
print(f"总计: {len(df)}  | 主板: {len(main)}  | 双创: {len(dual)}\n")
for label, sub in [('全部', df), ('主板', main), ('双创', dual)]:
    if len(sub) == 0: continue
    print(f"{'='*55}"); print(f"{label} - {len(sub)} 个信号"); print(f"{'='*55}")
    for w in [1,5,10,20]:
        r = sub[f'return_{w}d'].dropna(); wins = r[r>0]; negs = r[r<0]
        print(f"  +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>5.1f}%  亏损比例={len(negs)/len(r)*100:>5.1f}%  亏>15%={(r<-15).sum()}  最大={r.max():>6.2f}%  最小={r.min():>6.2f}%")
    print()
