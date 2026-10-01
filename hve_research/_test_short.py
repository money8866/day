import numpy as np, pandas as pd, importlib.util, sys, traceback
spec=importlib.util.spec_from_file_location('m','01_build_panel.py'); m=importlib.util.module_from_spec(spec); sys.argv=['x']; spec.loader.exec_module(m)
rng=np.random.default_rng(3)
for n in [23,39,60,61,120]:
    df=pd.DataFrame({'ts_code':['000001.SZ']*n,'trade_date':np.arange(n),
        'open':np.abs(rng.normal(10,1,n)),'high':np.abs(rng.normal(11,1,n)),'low':np.abs(rng.normal(9,1,n)),
        'close':np.abs(rng.normal(10,1,n)),'pre_close':np.abs(rng.normal(10,1,n)),'pct_chg':rng.normal(0,2,n),
        'vol':np.abs(rng.normal(1e5,1e4,n)),'amount':np.abs(rng.normal(1e8,1e7,n)),
        'turnover_rate':np.abs(rng.normal(2,1,n)),'total_mv':np.full(n,1e5),'circ_mv':np.full(n,8e4)})
    try:
        m.compute_stock(df); print('ok',n)
    except Exception as e:
        print('FAIL',n,e)
