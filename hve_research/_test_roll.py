import numpy as np, pandas as pd, importlib.util, sys
spec=importlib.util.spec_from_file_location('m','01_build_panel.py'); m=importlib.util.module_from_spec(spec)
sys.argv=['x']; spec.loader.exec_module(m)
rng=np.random.default_rng(0)
x=rng.normal(size=300); x[::37]=np.nan
for w in (5,20,60):
    a=m.rmean_incl(x,w); b=pd.Series(x).rolling(w).mean().values
    print('mean',w,'maxdiff',np.nanmax(np.abs(a-b)))
    a=m.rstd_incl(x,w); b=pd.Series(x).rolling(w).std(ddof=1).values
    print('std',w,'maxdiff',np.nanmax(np.abs(a-b)))
    a=m.rmax_prev(x,w); b=pd.Series(x).rolling(w).max().shift(1).values
    print('maxprev',w,'maxdiff',np.nanmax(np.abs(a-b)))
    a=m.rmin_prev(x,w); b=pd.Series(x).rolling(w).min().shift(1).values
    print('minprev',w,'maxdiff',np.nanmax(np.abs(a-b)))
    a=m.rmean_prev(x,w); b=pd.Series(x).rolling(w).mean().shift(1).values
    print('meanprev',w,'maxdiff',np.nanmax(np.abs(a-b)))
p=m.roll_pct(x,250,60)
s=pd.Series(x).rolling(250,min_periods=60).apply(lambda v: (v<=v[-1]).mean(),raw=True).values
print('pct maxdiff',np.nanmax(np.abs(p-s)))
