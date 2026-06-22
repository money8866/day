"""验证筹码分布缓存逻辑：首次读→API下载；再次读→命中缓存"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_chip_distribution, CACHE_DIR

ts_code = '000970.SZ'
trade_date = '20260618'
current_price = 14.94

print(f"实际 CACHE_DIR = {CACHE_DIR}")

# 先清理测试缓存
cleaned = []
for f in ['chip_000970.SZ_20260618_chips.csv', 'chip_000970.SZ_20260618_perf.csv']:
    path = os.path.join(CACHE_DIR, f)
    if os.path.exists(path):
        os.remove(path)
        cleaned.append(f)
if cleaned:
    print(f"已清理测试缓存: {len(cleaned)} 个文件")
else:
    print("初始状态：无缓存")

# 第1次：无缓存
print(f"\n{'='*60}")
print("第 1 次运行（应走 API）")
print(f"{'='*60}")
t0 = time.time()
r1 = get_chip_distribution(ts_code, trade_date, current_price)
t1 = time.time()
print(f"耗时: {t1-t0:.2f} 秒")
print(f"上方套牢盘: {r1.get('above_chips_pct', -1)}%")
print(f"平均成本: {r1.get('avg_cost', 0)}")
print(f"最近压力位: {r1.get('nearest_pressure', 0)}")

# 检查缓存文件
chips_file = os.path.join(CACHE_DIR, 'chip_000970.SZ_20260618_chips.csv')
perf_file = os.path.join(CACHE_DIR, 'chip_000970.SZ_20260618_perf.csv')
print(f"\n缓存文件检查:")
print(f"  cyq_chips: {'存在 '+str(os.path.getsize(chips_file))+' 字节' if os.path.exists(chips_file) else '未生成'}")
print(f"  cyq_perf: {'存在 '+str(os.path.getsize(perf_file))+' 字节' if os.path.exists(perf_file) else '未生成'}")

# 第2次：命中缓存
print(f"\n{'='*60}")
print("第 2 次运行（应命中缓存，速度更快）")
print(f"{'='*60}")
t2 = time.time()
r2 = get_chip_distribution(ts_code, trade_date, current_price)
t3 = time.time()
print(f"耗时: {t3-t2:.3f} 秒（应显著小于 {t1-t0:.2f} 秒）")
print(f"上方套牢盘: {r2.get('above_chips_pct', -1)}%")
print(f"平均成本: {r2.get('avg_cost', 0)}")

# 数据一致性校验
print(f"\n{'='*60}")
print("数据一致性校验（两次结果应完全一致）")
print(f"{'='*60}")
same = r1.get('above_chips_pct', -1) == r2.get('above_chips_pct', -1) and r1.get('avg_cost', 0) == r2.get('avg_cost', 0)
print(f"{'✓ PASS（缓存正常工作）' if same else '✗ FAIL'}")
print(f"速度对比: 第1次 {t1-t0:.2f}s，第2次 {t3-t2:.3f}s（加速 {(t1-t0)/max(t3-t2,0.001):.0f}x）")

# 第3次：再跑主流程里的其他股票来估算总时间节省
print(f"\n{'='*60}")
print("扩展测试：模拟 60 只股票的时间")
print(f"{'='*60}")
total_hit = 0.0
# 直接测5轮读缓存
for i in range(5):
    _ = get_chip_distribution(ts_code, trade_date, current_price)
t_avg = (time.time() - t3) / 5
print(f"命中缓存平均耗时: {t_avg*1000:.1f} 毫秒/只")
print(f"60 只股票估计: 缓存 {t_avg*60:.1f} 秒 vs 无缓存 {(t1-t0)*60:.0f} 秒，节省约 {(t1-t0)*60 - t_avg*60:.0f} 秒")
