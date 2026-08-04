"""临时测试 RallyPullbackEngine - 详细调试"""
import sys, os, yaml, pandas as pd

# 正确的路径设置
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'inst_pullback_v2'))

import stock_cache as sc
from data.loader import DataLoader

td = sc.get_effective_date()
print(f'trade_date: {td}')

loader = DataLoader()
loader.trade_date = td

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
rp_cfg = config.get('rally_pullback', {})
print(f'rally_pullback keys: {list(rp_cfg.keys())}')

lookback = rp_cfg.get('rally', {}).get('lookback', 60) + 60
start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback + 20)).strftime('%Y%m%d')
print(f'start_date: {start_date}')

code = '300750.SZ'
df = loader.load_stk_factor(code, start_date, td, silent=False)
print(f'df: type={type(df)}, empty={df is None or df.empty}')
if df is not None and not df.empty:
    print(f'len={len(df)}, close[-1]={df["close_qfq"].iloc[-1]}')

# Now test engine
from market_regime_v3.engines.rally_pullback_engine import RallyPullbackEngine
engine = RallyPullbackEngine(config)
r = engine.detect(code, td)
if r is None:
    print(f'Engine returned None for {code}')
elif r.is_qualified:
    print(f'QUALIFIED: {r.name} total={r.total_score:.0f}')
else:
    print(f'NOT QUALIFIED: {r.name} amp={r.rally_amplitude*100:.1f}% lu={r.rally_limit_up_count} vol={r.rally_vol_expansion:.1f} dd={r.drawdown_from_high*100:.1f}%')
print('完成')