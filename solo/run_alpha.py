"""运行Alpha引擎完整Pipeline"""
import sys, os, time
sys.path.insert(0, r'd:\mystock\solo')

os.environ['PYTHONUNBUFFERED'] = '1'

t0 = time.time()
print(f"[{time.strftime('%H:%M:%S')}] 初始化引擎...")
from market_regime_v3.main import MarketRegimeV3
engine = MarketRegimeV3()
print(f"[{time.strftime('%H:%M:%S')}] 初始化完成 ({time.time()-t0:.1f}s)")

t1 = time.time()
print(f"[{time.strftime('%H:%M:%S')}] 运行Pipeline...")
result = engine.run(trade_date='20260727')
print(f"[{time.strftime('%H:%M:%S')}] Pipeline完成 ({time.time()-t1:.1f}s)")
print(f"  总耗时: {time.time()-t0:.1f}s")
