"""快速诊断：检查 pipeline 在哪个步骤卡住"""
import sys, os, time
sys.path.insert(0, r'd:\mystock\solo')

print(f"[{time.strftime('%H:%M:%S')}] 开始导入...")

t0 = time.time()
from market_regime_v3.__main__ import main
print(f"[{time.strftime('%H:%M:%S')}] __main__ 导入完成 ({time.time()-t0:.1f}s)")

t0 = time.time()
# 只初始化引擎（不运行）
from market_regime_v3.main import MarketRegimeV3, load_config
print(f"[{time.strftime('%H:%M:%S')}] main 导入完成 ({time.time()-t0:.1f}s)")

t0 = time.time()
engine = MarketRegimeV3()
print(f"[{time.strftime('%H:%M:%S')}] MarketRegimeV3 初始化完成 ({time.time()-t0:.1f}s)")

t0 = time.time()
engine.run(trade_date='20260724')
print(f"[{time.strftime('%H:%M:%S')}] run() 完成 ({time.time()-t0:.1f}s)")
