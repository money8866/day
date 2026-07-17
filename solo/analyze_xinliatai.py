"""信立泰(002294.SZ)量能爆发形态分析
1. 拉取2026-07-15前后数据
2. 分析量能、均线、价格形态
3. 提取特征参数
4. 构建选股公式
5. 全市场回测
"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

CODE = "002294.SZ"
NAME = "信立泰"
START_DATE = "20260715"

print("=" * 70)
print(f"信立泰({CODE}) 量能爆发形态分析")
print("=" * 70)

df = tq.get_hist_data(CODE)
if df is None or len(df) < 60:
    print(f"❌ 数据不足: {df is None and 'None' or len(df)}")
    sys.exit(1)

df = df.copy()
if 'trade_date' in df.columns:
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date').reset_index(drop=True)

print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}, 共{len(df)}天")

# 找到20260715的位置
target_idx = None
for i, d in enumerate(df['trade_date'].values):
    if str(d) == START_DATE:
        target_idx = i
        break

if target_idx is None:
    print(f"⚠️ 未找到{START_DATE}，使用最近日期")
    target_idx = len(df) - 1

print(f"目标日{START_DATE}位置: idx={target_idx}, 数据共{len(df)}天")
print(f"目标日收盘价: {df['close'].iloc[target_idx]:.2f}")
print(f"目标日成交量: {df['vol'].iloc[target_idx]:.0f}")

# 分析目标日前后60天的形态
window = 60
start_i = max(0, target_idx - window + 1)
end_i = min(len(df), target_idx + 1)
seg = df.iloc[start_i:end_i].copy().reset_index(drop=True)

print(f"\n分析窗口: {seg['trade_date'].iloc[0]} ~ {seg['trade_date'].iloc[-1]}, 共{len(seg)}天")

# ============ 1. 量能分析 ============
print("\n" + "=" * 70)
print("【1. 量能分析】")
print("=" * 70)

vol_arr = seg['vol'].values.astype(float)
close_arr = seg['close'].values.astype(float)
high_arr = seg['high'].values.astype(float)
low_arr = seg['low'].values.astype(float)
pre_close_arr = seg['pre_close'].values.astype(float)

vol_ma5 = pd.Series(vol_arr).rolling(5, min_periods=1).mean().values
vol_ma10 = pd.Series(vol_arr).rolling(10, min_periods=1).mean().values
vol_ma20 = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values

print(f"窗口内最大量能: {np.max(vol_arr):.0f} (日期: {seg['trade_date'].iloc[np.argmax(vol_arr)]})")
print(f"窗口内平均量能: {np.mean(vol_arr):.0f}")
print(f"前20日均量(起涨前): {np.mean(vol_arr[:20]):.0f}")
print(f"后20日均量(起涨后): {np.mean(vol_arr[20:40]):.0f}")
print(f"量能放大倍数: {np.mean(vol_arr[20:40]) / np.mean(vol_arr[:20]):.2f}")

# 量比>2的天数
vol_ratio_vs_ma20 = vol_arr / np.maximum(vol_ma20, 1)
print(f"量比>1.5天数: {np.sum(vol_ratio_vs_ma20 > 1.5)}")
print(f"量比>2.0天数: {np.sum(vol_ratio_vs_ma20 > 2.0)}")
print(f"最大量比: {np.max(vol_ratio_vs_ma20):.2f}")

# ============ 2. 均线分析 ============
print("\n" + "=" * 70)
print("【2. 均线分析】")
print("=" * 70)

ma5 = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
ma10 = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
ma60 = pd.Series(close_arr).rolling(60, min_periods=1).mean().values

# MA20斜率
if len(seg) >= 11:
    ma20_now = ma20[-1]
    ma20_10ago = ma20[-11] if len(ma20) > 10 else ma20[0]
    ma20_chg_10d = (ma20_now / ma20_10ago - 1) * 100 if ma20_10ago > 0 else 0
    print(f"MA20近10日变化率: {ma20_chg_10d:+.2f}%")

if len(seg) >= 21:
    ma20_now = ma20[-1]
    ma20_20ago = ma20[-21] if len(ma20) > 20 else ma20[0]
    ma20_chg_20d = (ma20_now / ma20_20ago - 1) * 100 if ma20_20ago > 0 else 0
    print(f"MA20近20日变化率: {ma20_chg_20d:+.2f}%")

# 均线多头排列
last_ma5, last_ma10, last_ma20, last_ma60 = ma5[-1], ma10[-1], ma20[-1], ma60[-1]
print(f"最新MA5={last_ma5:.2f} MA10={last_ma10:.2f} MA20={last_ma20:.2f} MA60={last_ma60:.2f}")
print(f"均线多头排列(MA5>MA10>MA20>MA60): {last_ma5 > last_ma10 > last_ma20 > last_ma60}")

# 价格与MA20关系
last_close = close_arr[-1]
dist_ma20 = (last_close / last_ma20 - 1) * 100
print(f"最新收盘={last_close:.2f} 距MA20={dist_ma20:+.2f}%")

# ============ 3. 价格形态分析 ============
print("\n" + "=" * 70)
print("【3. 价格形态分析】")
print("=" * 70)

# 区间振幅
range_high = np.max(high_arr)
range_low = np.min(low_arr)
range_swing = (range_high / range_low - 1) * 100
price_change = (close_arr[-1] / close_arr[0] - 1) * 100
print(f"区间最高: {range_high:.2f} 最低: {range_low:.2f}")
print(f"区间振幅: {range_swing:.2f}%")
print(f"区间涨幅: {price_change:+.2f}%")

# 振幅
amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
avg_amplitude = np.mean(amplitude)
print(f"平均日振幅: {avg_amplitude:.2f}%")
print(f"振幅>5%天数: {np.sum(amplitude > 5)}")

# ============ 4. MACD状态 ============
print("\n" + "=" * 70)
print("【4. MACD状态】")
print("=" * 70)

exp12 = pd.Series(close_arr).ewm(span=12, adjust=False).mean().values
exp26 = pd.Series(close_arr).ewm(span=26, adjust=False).mean().values
dif = exp12 - exp26
dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
macd = (dif - dea) * 2

last_dif = dif[-1]
last_dea = dea[-1]
last_macd = macd[-1]
prev_macd = macd[-2] if len(macd) > 1 else 0

print(f"DIF={last_dif:.4f} DEA={last_dea:.4f} MACD={last_macd:.4f}")
if last_macd > 0 and prev_macd <= 0:
    macd_status = "刚刚红柱（金叉）"
elif last_macd > 0 and prev_macd > 0:
    if last_macd > prev_macd:
        macd_status = "红柱放大"
    else:
        macd_status = "红柱缩短"
elif last_macd < 0 and prev_macd < 0:
    if last_macd > prev_macd:
        macd_status = "绿柱缩短（即将金叉）"
    else:
        macd_status = "绿柱放大"
else:
    macd_status = "状态转换中"
print(f"MACD状态: {macd_status}")

# ============ 5. 关键特征提炼 ============
print("\n" + "=" * 70)
print("【5. 信立泰量能爆发形态关键特征】")
print("=" * 70)

# 5.1 起涨前的"地量"特征
pre_vol = np.mean(vol_arr[:20])  # 前20日均量
post_vol = np.mean(vol_arr[20:])  # 后续均量
vol_surge_ratio = post_vol / pre_vol
print(f"起涨前20日均量: {pre_vol:.0f}")
print(f"起涨后均量: {post_vol:.0f}")
print(f"量能放大倍数: {vol_surge_ratio:.2f}")

# 5.2 量价同步性
vol_change = (post_vol / pre_vol - 1) * 100
price_change_pct = price_change
print(f"量能增幅: {vol_change:+.2f}%")
print(f"价格涨幅: {price_change_pct:+.2f}%")
print(f"量价同步性(同向且量先放大): {vol_change > 50 and price_change_pct > 5}")

# 5.3 均线同步上升
ma20_slope_now = (ma20[-1] / ma20[-5] - 1) * 100 if len(ma20) > 5 and ma20[-5] > 0 else 0
ma20_slope_pre = (ma20[20] / ma20[5] - 1) * 100 if len(ma20) > 20 and ma20[5] > 0 else 0
print(f"MA20近5日斜率: {ma20_slope_now:+.2f}%")
print(f"MA20前段斜率(5~20日): {ma20_slope_pre:+.2f}%")
print(f"均线同步上升(前段<后段): {ma20_slope_now > ma20_slope_pre}")

# 5.4 量能持续性
vol_recent_5 = np.mean(vol_arr[-5:])
vol_recent_20 = np.mean(vol_arr[-20:])
vol_persistence = vol_recent_5 / vol_recent_20
print(f"近5日均量/近20日均量: {vol_persistence:.2f}")
print(f"量能持续性(>0.8): {vol_persistence > 0.8}")

# 5.5 量能爆发后的回踩
vol_peak_idx = np.argmax(vol_arr)
price_at_vol_peak = close_arr[vol_peak_idx]
price_now = close_arr[-1]
pullback_from_peak = (price_now / price_at_vol_peak - 1) * 100
print(f"量能峰值位置: 第{vol_peak_idx}天({seg['trade_date'].iloc[vol_peak_idx]})")
print(f"量能峰值时价格: {price_at_vol_peak:.2f}")
print(f"当前距量能峰值: {pullback_from_peak:+.2f}%")

# ============ 6. 形态可视化 ============
print("\n" + "=" * 70)
print("【6. 形态可视化（生成图表）】")
print("=" * 70)

fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                         gridspec_kw={'height_ratios': [2, 1, 1]})

# 价格+均线
ax1 = axes[0]
ax1.plot(close_arr, 'b-', linewidth=1.5, label='收盘价')
ax1.plot(ma5, 'y-', linewidth=1, label='MA5', alpha=0.7)
ax1.plot(ma10, 'g-', linewidth=1, label='MA10', alpha=0.7)
ax1.plot(ma20, 'r-', linewidth=1.5, label='MA20')
ax1.plot(ma60, 'purple', linewidth=1, label='MA60', alpha=0.7)
ax1.fill_between(range(len(close_arr)), low_arr, high_arr, alpha=0.1, color='blue')
ax1.set_title(f'{NAME}({CODE}) 量能爆发形态 - 价格与均线', fontsize=14)
ax1.legend(loc='upper left', fontsize=9)
ax1.grid(True, alpha=0.3)
ax1.axvline(x=len(close_arr) - 1, color='red', linestyle='--', alpha=0.5, label=f'目标日{START_DATE}')

# 标记量能峰值
ax1.scatter([vol_peak_idx], [high_arr[vol_peak_idx]], color='red', s=100, marker='v', zorder=5)
ax1.annotate(f'量能峰值\n{seg["trade_date"].iloc[vol_peak_idx]}',
             xy=(vol_peak_idx, high_arr[vol_peak_idx]),
             xytext=(vol_peak_idx, high_arr[vol_peak_idx] * 1.05),
             fontsize=9, color='red')

# 成交量
ax2 = axes[1]
colors = ['red' if c > p else 'green' for c, p in zip(close_arr, pre_close_arr)]
ax2.bar(range(len(vol_arr)), vol_arr, color=colors, alpha=0.6)
ax2.plot(vol_ma5, 'y-', linewidth=1, label='量能MA5')
ax2.plot(vol_ma20, 'r-', linewidth=1.5, label='量能MA20')
ax2.set_title('成交量', fontsize=12)
ax2.legend(loc='upper left', fontsize=9)
ax2.grid(True, alpha=0.3)
ax2.axvline(x=len(close_arr) - 1, color='red', linestyle='--', alpha=0.5)

# MACD
ax3 = axes[2]
ax3.bar(range(len(macd)), macd, color=['red' if m > 0 else 'green' for m in macd], alpha=0.6)
ax3.plot(dif, 'white', linewidth=1, label='DIF')
ax3.plot(dea, 'yellow', linewidth=1, label='DEA')
ax3.set_title('MACD', fontsize=12)
ax3.legend(loc='upper left', fontsize=9)
ax3.grid(True, alpha=0.3)
ax3.axhline(y=0, color='gray', linewidth=0.5)
ax3.axvline(x=len(close_arr) - 1, color='red', linestyle='--', alpha=0.5)

# X轴日期标签
date_labels = [str(d) for d in seg['trade_date'].values]
step = max(1, len(date_labels) // 10)
ax3.set_xticks(range(0, len(date_labels), step))
ax3.set_xticklabels(date_labels[::step], rotation=45, fontsize=8)

plt.tight_layout()
out_path = r"d:\mystock\cache_daily\XinLaoTai_Analysis.png"
plt.savefig(out_path, dpi=100, bbox_inches='tight')
print(f"✅ 图表已保存: {out_path}")
plt.close()

# ============ 7. 特征汇总 ============
print("\n" + "=" * 70)
print("【7. 信立泰形态特征汇总（用于构建公式）】")
print("=" * 70)
print(f"""
形态特征:
- 起涨前20日均量: {pre_vol:.0f}
- 起涨后均量: {post_vol:.0f}
- 量能放大倍数: {vol_surge_ratio:.2f}（建议阈值>=1.5）
- 区间振幅: {range_swing:.2f}%（建议>=20%）
- 区间涨幅: {price_change_pct:+.2f}%（建议>=5%）
- 平均日振幅: {avg_amplitude:.2f}%（建议>=3%）
- MA20近10日斜率: {ma20_slope_now:+.2f}%（建议>=0%走平或上行）
- 量比>1.5天数: {np.sum(vol_ratio_vs_ma20 > 1.5)}（建议>=3）
- 量能持续性: {vol_persistence:.2f}（建议>=0.8）
- MACD状态: {macd_status}
- 距MA20: {dist_ma20:+.2f}%
""")

# 保存特征数据
import json
features = {
    "code": CODE,
    "name": NAME,
    "target_date": START_DATE,
    "window_days": len(seg),
    "vol_pre_20d_avg": float(pre_vol),
    "vol_post_avg": float(post_vol),
    "vol_surge_ratio": float(vol_surge_ratio),
    "range_swing_pct": float(range_swing),
    "price_change_pct": float(price_change_pct),
    "avg_amplitude_pct": float(avg_amplitude),
    "ma20_slope_5d_pct": float(ma20_slope_now),
    "vol_ratio_gt15_count": int(np.sum(vol_ratio_vs_ma20 > 1.5)),
    "vol_persistence": float(vol_persistence),
    "macd_status": macd_status,
    "dist_ma20_pct": float(dist_ma20),
    "pullback_from_vol_peak_pct": float(pullback_from_peak),
    "close_at_target": float(close_arr[-1]),
    "vol_at_target": float(vol_arr[-1]),
}
feat_path = r"d:\mystock\cache_daily\XinLaoTai_Features.json"
with open(feat_path, "w", encoding="utf-8") as f:
    json.dump(features, f, ensure_ascii=False, indent=2)
print(f"✅ 特征已保存: {feat_path}")
