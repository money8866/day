# -*- coding: utf-8 -*-
"""验证 stk_factor_pro 新指标对二波形态的信号价值"""
import os, sys
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import tushare as ts
import pandas as pd
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 688629: 深度回调(一波+36.2%→回调-20.5%→二波+29.3%已确认)
df = pro.stk_factor_pro(ts_code='688629.SH', start_date='20260501', end_date='20260623')
bottom = df[df['trade_date']=='20260609'].iloc[0]  # 调整最低点
now = df.iloc[0]  # 最新

print('='*70)
print('688629.SH 深度回调关键指标对比: 底部(6/9) vs 最新(6/23)')
print('='*70)
print()

indicators = [
    ('RSI-6',       'rsi_qfq_6',    'RSI'),
    ('KDJ-J',       'kdj_qfq',      'KDJ'),
    ('CCI',         'cci_qfq',      'CCI'),
    ('MFI',         'mfi_qfq',      'MFI'),
    ('WR',          'wr_qfq',       'WR'),
    ('BIAS1',       'bias1_qfq',    'BIAS'),
    ('BIAS2',       'bias2_qfq',    'BIAS'),
    ('ATR',          'atr_qfq',      'ATR'),
    ('OBV',         'obv_qfq',      'OBV'),
    ('VR',          'vr_qfq',       'VR'),
    ('PSY',         'psy_qfq',      'PSY'),
    ('DMI-ADX',     'dmi_adx_qfq',  'DMI'),
    ('DMI-PDI',     'dmi_pdi_qfq',  'DMI'),
    ('DMI-MDI',     'dmi_mdi_qfq',  'DMI'),
    ('volume_ratio','volume_ratio', 'VOLR'),
    ('MACD-DIF',    'macd_dif_qfq', 'MACD'),
    ('MACD-DEA',    'macd_dea_qfq', 'MACD'),
]

for name, col, group in indicators:
    bv = float(bottom[col])
    nv = float(now[col])
    signal = ''
    # 判断底部信号
    if name == 'RSI-6' and bv < 30: signal = '<<超卖>>'
    elif name == 'RSI-6' and bv < 50: signal = '<偏低>'
    elif name == 'KDJ-J' and bv < 20: signal = '<<超卖>>'
    elif name == 'CCI' and bv < -100: signal = '<<超卖>>'
    elif name == 'MFI' and bv < 20: signal = '<<资金枯竭>>'
    elif name == 'WR' and bv > 80: signal = '<<超卖>>'
    elif name == 'BIAS1' and bv < -5: signal = '<<超卖>>'
    elif name == 'BIAS2' and bv < -10: signal = '<<超卖>>'
    elif name == 'VR' and bv < 70: signal = '<缩量>'
    elif name == 'PSY' and bv < 25: signal = '<<超卖>>'
    # 判断反转信号
    if name == 'MACD-DIF' and bv < nv: signal += ' DIF上升'
    print(f'{name:>14}: 底部={bv:>10.2f}  最新={nv:>10.2f}  {signal}')

# DMI趋势反转
pdi_b, mdi_b = float(bottom['dmi_pdi_qfq']), float(bottom['dmi_mdi_qfq'])
pdi_n, mdi_n = float(now['dmi_pdi_qfq']), float(now['dmi_mdi_qfq'])
print(f'\nDMI趋势: 底部 PDI({pdi_b:.1f}) vs MDI({mdi_b:.1f}) => {"空头" if pdi_b<mdi_b else "多头"}')
print(f'         最新 PDI({pdi_n:.1f}) vs MDI({mdi_n:.1f}) => {"空头" if pdi_n<mdi_n else "多头"}')
if pdi_b < mdi_b and pdi_n > mdi_n:
    print('  >>> PDI上穿MDI = 趋势反转确认!')

# 连涨连跌
print(f'\n连涨天数: 底部={float(bottom["updays"]):.0f}  最新={float(now["updays"]):.0f}')
print(f'连跌天数: 底部={float(bottom["downdays"]):.0f}  最新={float(now["downdays"]):.0f}')

# ===== 算法优化分析 =====
print('\n' + '='*70)
print('算法优化方向分析')
print('='*70)

# 1. ATR-based止损 vs 固定百分比止损
atr_bottom = float(bottom['atr_qfq'])
close_bottom = float(bottom['close'])
atr_stop = close_bottom - 2 * atr_bottom  # ATR止损: 2倍ATR
fixed_stop_5pct = close_bottom * 0.95
print(f'\n1. ATR动态止损 vs 固定-5%止损:')
print(f'   底部收盘: {close_bottom:.2f}')
print(f'   ATR(14): {atr_bottom:.2f}')
print(f'   2*ATR止损: {atr_stop:.2f} (距离-{(close_bottom-atr_stop)/close_bottom*100:.1f}%)')
print(f'   固定-5%止损: {fixed_stop_5pct:.2f}')
print(f'   => ATR止损{"更宽松" if atr_stop < fixed_stop_5pct else "更严格"}，适应个股波动性')

# 2. MFI资金流量确认底部
mfi_series = df['mfi_qfq'].values
rsi_series = df['rsi_qfq_6'].values
close_series = df['close'].values
# 底部背离检测：价格新低但MFI没新低
mfi_bottom_idx = 9  # 6/9
for i in range(mfi_bottom_idx-1, -1, -1):
    if close_series[i] < close_series[mfi_bottom_idx] and mfi_series[i] > mfi_series[mfi_bottom_idx]:
        print(f'\n2. MFI底背离检测: YES! 价格{i}更低但MFI更高 => 资金未出逃')
        break
else:
    print(f'\n2. MFI底背离检测: 未检测到(后续无更低价格)')

# RSI底背离
for i in range(mfi_bottom_idx-1, -1, -1):
    if close_series[i] < close_series[mfi_bottom_idx] and rsi_series[i] > rsi_series[mfi_bottom_idx]:
        print(f'   RSI底背离检测: YES! 价格{i}更低但RSI更高 => 动能恢复')
        break
else:
    print(f'   RSI底背离检测: 未检测到(后续无更低价格)')

# 3. DMI趋势强度
adx_series = df['dmi_adx_qfq'].values
print(f'\n3. DMI-ADX趋势强度:')
print(f'   一波顶部(5/25): ADX={float(df.iloc[20]["dmi_adx_qfq"]):.1f}')
print(f'   调整底部(6/9): ADX={float(bottom["dmi_adx_qfq"]):.1f}')
print(f'   当前: ADX={float(now["dmi_adx_qfq"]):.1f}')
print(f'   => ADX>25为强趋势，底部ADX低=趋势弱化，反转后ADX回升确认二波')

# 4. volume_ratio量比信号
vr_bottom = df['volume_ratio'].iloc[9]
print(f'\n4. 量比信号:')
print(f'   底部(6/9)量比: {vr_bottom:.2f}')
print(f'   反弹(6/10)量比: {df["volume_ratio"].iloc[8]:.2f}')
print(f'   => 底部缩量+反弹放量 = 经典二波启动信号')

# 5. BIAS乖离率
bias1_bottom = float(bottom['bias1_qfq'])
bias2_bottom = float(bottom['bias2_qfq'])
print(f'\n5. BIAS乖离率:')
print(f'   底部BIAS1(6日): {bias1_bottom:.2f}%')
print(f'   底部BIAS2(12日): {bias2_bottom:.2f}%')
print(f'   => BIAS1<-5%或BIAS2<-10%为极端超卖，回归均值概率极高')

# 6. 薛斯通道 xsii
xsii_cols = [c for c in df.columns if 'xsii' in c]
if xsii_cols:
    print(f'\n6. 薛斯通道:')
    for c in xsii_cols:
        print(f'   底部 {c}: {float(bottom[c]):.2f}  最新 {float(now[c]):.2f}')
