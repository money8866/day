# -*- coding: utf-8 -*-
import os, sys, datetime
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyArrowPatch, Rectangle
import matplotlib.patches as mpatches

sys.stdout.reconfigure(encoding='utf-8')

# ── 数据获取 ──
import tushare as ts
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()
today = datetime.date.today().strftime('%Y%m%d')

df = pro.index_daily(ts_code='932000.CSI', start_date='20260501', end_date=today)
df = df.sort_values('trade_date').reset_index(drop=True)
df['date'] = pd.to_datetime(df['trade_date'])
df['close'] = df['close'].astype(float)
df['high'] = df['high'].astype(float)
df['low'] = df['low'].astype(float)
df['open'] = df['open'].astype(float)

# 过滤有效数据
df = df[df['close'] > 1000].reset_index(drop=True)
print(f"数据: {len(df)} 条  {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"最新: {df.iloc[-1]['date'].strftime('%Y-%m-%d')}  收={df.iloc[-1]['close']:.2f}  高={df.iloc[-1]['high']:.2f}  低={df.iloc[-1]['low']:.2f}")

# ── 波浪标注 ──
peak_idx = df['high'].idxmax()
peak_date = df.loc[peak_idx, 'date']
peak_price = df.loc[peak_idx, 'high']
peak_close = df.loc[peak_idx, 'close']

post_peak = df[df.index > peak_idx].copy()
a_end_idx = post_peak['low'].idxmin()
a_end_date = df.loc[a_end_idx, 'date']
a_end_price = df.loc[a_end_idx, 'low']

# A浪内部次高点
sub_df = df[(df.index > peak_idx) & (df.index < a_end_idx)]
sub_b_idx = sub_df['high'].idxmax() if len(sub_df) > 0 else None
sub_b_date = df.loc[sub_b_idx, 'date'] if sub_b_idx else None
sub_b_price = df.loc[sub_b_idx, 'high'] if sub_b_idx else None

# B浪/C浪起点
post_a = df[df.index > a_end_idx].copy()
b_c_idx = None
b_c_price = 0
for i, (idx, row) in enumerate(post_a.iterrows()):
    if i == 0: continue
    if row['high'] > a_end_price * 1.02:
        b_c_idx = idx
        b_c_price = row['high']
        break
if b_c_idx is None:
    b_c_idx = df.index[-1]
    b_c_price = df.iloc[-1]['close']
b_c_date = df.loc[b_c_idx, 'date']

current = df.iloc[-1]
current_date = current['date']
current_close = current['close']

a_total = peak_price - a_end_price
c_targets = {
    'C=A×0.618\n(乐观)': peak_price - a_total * 0.618,
    'C=A×0.786\n(合理)': peak_price - a_total * 0.786,
    'C=A×1.000\n(等长)': peak_price - a_total * 1.000,
    'C=A×1.382\n(悲观)': peak_price - a_total * 1.382,
}

print(f"\n关键价位:")
print(f"  A浪起点: {peak_date.strftime('%Y-%m-%d')} 高={peak_price:.2f}")
if sub_b_idx: print(f"  小B浪: {sub_b_date.strftime('%Y-%m-%d')} 高={sub_b_price:.2f}")
print(f"  A浪终点: {a_end_date.strftime('%Y-%m-%d')} 低={a_end_price:.2f}")
print(f"  B/C浪起点: {b_c_date.strftime('%Y-%m-%d')} 高={b_c_price:.2f}")
print(f"  当前: {current_date.strftime('%Y-%m-%d')} 收={current_close:.2f}")
for label, target in c_targets.items():
    print(f"  {label.strip()}: {target:.2f}")

# ── 绘图 ──
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12), gridspec_kw={'height_ratios': [3, 1]})
fig.patch.set_facecolor('#1a1a2e')
ax1.set_facecolor('#1a1a2e')
ax2.set_facecolor('#1a1a2e')

dates = mdates.date2num(df['date'])

# 阴阳烛
up = df['close'] >= df['open']
down = ~up
col_up, col_down = '#e74c3c', '#27ae60'
ax1.bar(dates[up], df['high'][up]-df['low'][up], bottom=df['low'][up], width=0.6, color='#666', linewidth=0.3)
ax1.bar(dates[up], df['close'][up]-df['open'][up], bottom=df['open'][up], width=0.6, color=col_up, linewidth=0.3)
ax1.bar(dates[down], df['high'][down]-df['low'][down], bottom=df['low'][down], width=0.6, color='#666', linewidth=0.3)
ax1.bar(dates[down], df['close'][down]-df['open'][down], bottom=df['open'][down], width=0.6, color=col_down, linewidth=0.3)

# ── 标注波浪 ──
def add_wave_label(ax, x, y, text, color, fontsize=11, va='bottom'):
    ax.annotate(text, xy=(x, y), xytext=(x, y + (30 if va=='bottom' else -30)),
                fontsize=fontsize, color=color, fontweight='bold',
                ha='center', va=va,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a1a2e', edgecolor=color, alpha=0.85),
                arrowprops=dict(arrowstyle='->', color=color, lw=1.2))

# A浪起点
add_wave_label(ax1, mdates.date2num(peak_date), peak_price, f'A浪起点\n{peak_date.strftime("%m/%d")}\n{peak_price:.0f}', '#f39c12', fontsize=10)

# 小B
if sub_b_idx:
    add_wave_label(ax1, mdates.date2num(sub_b_date), sub_b_price, f'小B\n{sub_b_date.strftime("%m/%d")}\n{sub_b_price:.0f}', '#e67e22', fontsize=9, va='top')

# A浪终点
add_wave_label(ax1, mdates.date2num(a_end_date), a_end_price, f'A浪终点\n{a_end_date.strftime("%m/%d")}\n{a_end_price:.0f}\n(-{a_total/peak_price*100:.1f}%)', '#e74c3c', fontsize=10)

# B/C浪起点
add_wave_label(ax1, mdates.date2num(b_c_date), b_c_price, f'B浪/C起点\n{b_c_date.strftime("%m/%d")}\n{b_c_price:.0f}', '#9b59b6', fontsize=10)

# C浪目标线
colors_t = ['#3498db', '#2ecc71', '#f39c12', '#e74c3c']
for (label, target), col in zip(c_targets.items(), colors_t):
    ax1.axhline(y=target, color=col, linestyle='--', linewidth=1.2, alpha=0.8)
    ax1.text(dates[-1], target, f'  {target:.0f}  {label.replace(chr(10), " ")}', color=col, fontsize=8.5, va='center')

# 波浪箭头
arrow_style = dict(connectionstyle='arc3,rad=0.0', arrowstyle='->', color='#aaa', lw=1.2)
# A浪大箭头
ax1.annotate('', xy=(mdates.date2num(a_end_date), a_end_price+50),
             xytext=(mdates.date2num(peak_date), peak_price-50),
             arrowprops=dict(arrowstyle='<->', color='#f39c12', lw=1.5, linestyle='dashed'))
ax1.text((mdates.date2num(peak_date)+mdates.date2num(a_end_date))/2, (peak_price+a_end_price)/2,
         f'A浪\n-{a_total/peak_price*100:.1f}%\n({peak_price:.0f}→{a_end_price:.0f})',
         color='#f39c12', fontsize=9, ha='center', va='center',
         bbox=dict(boxstyle='round', facecolor='#1a1a2e', edgecolor='#f39c12', alpha=0.8))

# B/C浪箭头
if b_c_idx != a_end_idx:
    ax1.annotate('', xy=(mdates.date2num(b_c_date), b_c_price+50),
                 xytext=(mdates.date2num(a_end_date), a_end_price+50),
                 arrowprops=dict(arrowstyle='->', color='#9b59b6', lw=1.5))
    ax1.text((mdates.date2num(a_end_date)+mdates.date2num(b_c_date))/2, (a_end_price+b_c_price)/2+60,
             f'B浪反弹\n+{(b_c_price-a_end_price)/a_end_price*100:.1f}%',
             color='#9b59b6', fontsize=9, ha='center', va='center',
             bbox=dict(boxstyle='round', facecolor='#1a1a2e', edgecolor='#9b59b6', alpha=0.8))

# 当前价线
ax1.axvline(x=dates[-1], color='#e74c3c', linestyle='-', linewidth=1, alpha=0.5)
ax1.text(dates[-1], current_close+30, f'  今日 {current_close:.0f}', color='#e74c3c', fontsize=9, va='bottom')

# 标题
ax1.set_title('中证2000（CSI 2000）走势分析  |  2026-05-13至今\nABC三浪结构', 
              color='white', fontsize=14, fontweight='bold', pad=12)
ax1.set_ylabel('指数点位', color='white', fontsize=11)
ax1.tick_params(colors='white')
ax1.spines['top'].set_color('#333')
ax1.spines['bottom'].set_color('#333')
ax1.spines['left'].set_color('#333')
ax1.spines['right'].set_color('#333')
ax1.xaxis.label.set_color('white')
ax1.grid(True, alpha=0.15, color='#555')

# MA线
for ma_n, color_ma in [(5, '#ff6b6b'), (20, '#ffd93d')]:
    ma = df['close'].rolling(ma_n).mean()
    ax1.plot(dates, ma, color=color_ma, linewidth=1.2, alpha=0.7, label=f'MA{ma_n}')

ax1.legend(loc='upper right', labelcolor='white', facecolor='#1a1a2e', edgecolor='#333', fontsize=9)

# ── 成交量 ──
colors_vol = np.where(df['close'] >= df['open'], col_up, col_down)
ax2.bar(dates, df['vol']/1e4, color=colors_vol, width=0.6, alpha=0.7)
ax2.set_ylabel('成交量(万手)', color='white', fontsize=10)
ax2.tick_params(colors='white', labelsize=8)
ax2.spines['top'].set_color('#333')
ax2.spines['bottom'].set_color('#333')
ax2.spines['left'].set_color('#333')
ax2.spines['right'].set_color('#333')
ax2.grid(True, alpha=0.15, color='#555')
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

# 波浪标注在成交量上
ax2.annotate('A浪', xy=(mdates.date2num(a_end_date), 0), 
             xytext=(mdates.date2num(peak_date), df['vol'].max()/1e4*0.5),
             arrowprops=dict(arrowstyle='<->', color='#f39c12', lw=1.2), 
             color='#f39c12', fontsize=9, ha='center')

plt.tight_layout()
out_path = r'D:\mystock\report_daily\csi2000_wave_analysis.png'
plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()
print(f"\n✅ K线图已保存: {out_path}")
print(f"   分辨率: 150dpi")
