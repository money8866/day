import pandas as pd

t5 = pd.read_csv(r'd:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.csv', low_memory=False)
fu = pd.read_csv(r'd:\mystock\solo\sli\output\sli_full_20260901.csv', low_memory=False)
for df, tag, cols in ((t5, 'top5', ['Purity', 'Dominance', 'SLI_V2', '龙头类型', '生命周期']),
                      (fu, 'full', ['roe_dt', 'ocf_to_profit', 'netprofit_margin', 'g1', 'g2', 'g3', 'pe_ttm'])):
    for c in cols:
        if c not in df.columns:
            print(f'[{tag}] {c}: MISSING')
            continue
        s = pd.to_numeric(df[c], errors='coerce')
        if s.notna().sum() == 0:
            print(f'[{tag}] {c}: all NaN (categorical?) uniq={df[c].dropna().unique()[:6]}')
            continue
        print(f'[{tag}] {c}: n={s.notna().sum()} min={s.min():.2f} p25={s.quantile(.25):.2f} '
              f'med={s.median():.2f} p75={s.quantile(.75):.2f} p95={s.quantile(.95):.2f} max={s.max():.2f}')
print('top5 龙头类型:', t5['龙头类型'].value_counts(dropna=False).to_dict())
print('top5 生命周期:', t5['生命周期'].value_counts(dropna=False).to_dict())
print('full 生命周期:', fu['lifecycle'].value_counts(dropna=False).to_dict())
