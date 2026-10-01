import numpy as np, pandas as pd, importlib.util, sys
spec=importlib.util.spec_from_file_location('m','01_build_panel.py'); m=importlib.util.module_from_spec(spec)
sys.argv=['x']; spec.loader.exec_module(m)
rng=np.random.default_rng(1)
x=np.abs(rng.normal(size=600))+1
p=m.roll_pct(x,250,60)
s=pd.Series(x).rolling(250,min_periods=60).apply(lambda v:(v<=v[-1]).mean(),raw=True).values
print('pct clean maxdiff',np.nanmax(np.abs(p-s[59:]+0*p[59:])) if False else np.nanmax(np.abs(p-s)))
for w in (5,20,60):
    a=m.rmean_prev(x,w); b=pd.Series(x).rolling(w).mean().shift(1).values
    print('meanprev',w,np.nanmax(np.abs(a-b)))
