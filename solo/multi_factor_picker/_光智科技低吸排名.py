"""光智科技20260608低吸评分全市场排名分析"""
import pandas as pd
import glob
import os

cache_dir = r'D:\mystock\cache_daily'

print('=== 光智科技20260608低吸评分全市场排名 ===\n')

# 先获取光智科技的特征
target_file = f'{cache_dir}\\300489.SZ.csv'
df = pd.read_csv(target_file, encoding='utf-8')
df['trade_date'] = df['trade_date'].astype(str)
df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)

# 找到20260608
target_idx = df[df['trade_date'] == '20260608'].index[0]
target_row = df.loc[target_idx]

print('【光智科技低吸日特征】')
print(f'日期：20260608')
print(f'收盘价：{float(target_row["close"]):.2f}')
print(f'最低价：{float(target_row["low"]):.2f}')
print(f'涨幅：{float(target_row["pct_chg"]):+.2f}%')
print(f'成交量：{float(target_row["vol"])/1e4:.0f}万手')

# 计算低吸评分
# 找首波涨停
wave1_idx = df[df['trade_date'] == '20260528'].index[0]
wave1_close = float(df.loc[wave1_idx, 'close'])

pullback_low = float(target_row['low'])
pullback_ratio = pullback_low / wave1_close

print(f'\n首波收盘：{wave1_close:.2f}')
print(f'回踩比例：{pullback_ratio:.1%}')
print(f'低吸评分：')

# 计算评分
score = 0

# F1: 回踩位置（满分10分）
if 0.80 <= pullback_ratio <= 0.85:
    score += 10  # 最佳区间
    print(f'  F1回踩位置：+10分（最佳区间80-85%）')
elif 0.85 < pullback_ratio <= 0.90:
    score += 8
    print(f'  F1回踩位置：+8分（较深回踩）')
elif 0.90 < pullback_ratio <= 0.95:
    score += 6
    print(f'  F1回踩位置：+6分（浅回踩）')
else:
    score += 4
    print(f'  F1回踩位置：+4分（极浅回踩）')

# F2: 缩量程度
vol_ma5 = float(df.loc[target_idx-5:target_idx, 'vol'].mean())
vol_ratio = float(target_row['vol']) / vol_ma5

if vol_ratio < 0.5:
    score += 10
    print(f'  F2缩量程度：+10分（极度缩量{vol_ratio:.2f}）')
elif vol_ratio < 0.7:
    score += 8
    print(f'  F2缩量程度：+8分（明显缩量{vol_ratio:.2f}）')
elif vol_ratio < 1.0:
    score += 6
    print(f'  F2缩量程度：+6分（轻度缩量{vol_ratio:.2f}）')
else:
    score += 4
    print(f'  F2缩量程度：+4分（未缩量{vol_ratio:.2f}）')

# F3: RSI位置
recent_14 = df.loc[target_idx-13:target_idx, 'close']
gains = recent_14.diff()
gains = gains[gains > 0]
losses = -gains[gains < 0]
avg_gain = gains.mean() if len(gains) > 0 else 0
avg_loss = losses.mean() if len(losses) > 0 else 0.01
rs = avg_gain / avg_loss
rsi = 100 - (100 / (1 + rs))

if 35 <= rsi <= 45:
    score += 10
    print(f'  F3超卖程度：+10分（RSI={rsi:.1f}最佳区间）')
elif rsi < 35:
    score += 8
    print(f'  F3超卖程度：+8分（RSI={rsi:.1f}极度超卖）')
elif rsi < 50:
    score += 6
    print(f'  F3超卖程度：+6分（RSI={rsi:.1f}轻度超卖）')
else:
    score += 4
    print(f'  F3超卖程度：+4分（RSI={rsi:.1f}未超卖）')

# F4: 均线支撑
ma60 = float(df.loc[target_idx-60:target_idx, 'close'].mean())
ma120 = float(df.loc[target_idx-120:target_idx, 'close'].mean())

support_count = 0
if pullback_low >= ma60 * 0.98:
    support_count += 1
    score += 5
    print(f'  F4均线支撑：+5分（MA60支撑）')
if pullback_low >= ma120 * 0.98:
    support_count += 1
    score += 5
    print(f'  F4均线支撑：+5分（MA120支撑）')
    
print(f'\n总评分：{score}/40分')

# 现在全市场扫描
print('\n\n【全市场扫描低吸评分】\n')

all_files = glob.glob(f'{cache_dir}/*.csv')
results = []

for i, file in enumerate(all_files):
    try:
        ts_code = os.path.basename(file).replace('.csv', '')
        if not (ts_code.startswith('6') or ts_code.startswith('0') or ts_code.startswith('3')):
            continue
        
        df2 = pd.read_csv(file, encoding='utf-8')
        df2['trade_date'] = df2['trade_date'].astype(str)
        df2 = df2.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        # 找到20260608
        target_row2 = df2[df2['trade_date'] == '20260608']
        if len(target_row2) == 0:
            continue
        
        target_idx2 = target_row2.index[0]
        
        # 必须是上涨的股票
        if float(target_row2.iloc[0]['pct_chg']) < 0:
            continue
        
        # 找最近60天内的首波涨停（排除最近5天）
        lookback_start = max(0, target_idx2 - 60)
        lookback_end = max(0, target_idx2 - 5)
        recent = df2.loc[lookback_start:lookback_end]
        
        limit_up = recent[recent['pct_chg'] >= 9.4]
        if len(limit_up) == 0:
            continue
        
        # 找最大涨幅的涨停
        wave1_idx2 = limit_up['pct_chg'].idxmax()
        wave1_close2 = float(df2.loc[wave1_idx2, 'close'])
        
        pullback_low2 = float(target_row2.iloc[0]['low'])
        pullback_ratio2 = pullback_low2 / wave1_close2
        
        # 只考虑回踩比例在75-100%区间的
        if not (0.75 <= pullback_ratio2 <= 1.0):
            continue
        
        # 计算评分
        score2 = 0
        
        # F1: 回踩位置
        if 0.80 <= pullback_ratio2 <= 0.85:
            score2 += 10
        elif 0.85 < pullback_ratio2 <= 0.90:
            score2 += 8
        elif 0.90 < pullback_ratio2 <= 0.95:
            score2 += 6
        else:
            score2 += 4
        
        # F2: 缩量
        vol_ma5_2 = float(df2.loc[target_idx2-5:target_idx2, 'vol'].mean())
        vol_ratio2 = float(target_row2.iloc[0]['vol']) / vol_ma5_2 if vol_ma5_2 > 0 else 1
        
        if vol_ratio2 < 0.5:
            score2 += 10
        elif vol_ratio2 < 0.7:
            score2 += 8
        elif vol_ratio2 < 1.0:
            score2 += 6
        else:
            score2 += 4
        
        # F3: RSI（简化计算）
        recent_14_2 = df2.loc[target_idx2-13:target_idx2, 'close']
        if len(recent_14_2) == 14:
            gains2 = recent_14_2.diff()
            gains2 = gains2[gains2 > 0]
            losses2 = -gains2[gains2 < 0]
            avg_gain2 = gains2.mean() if len(gains2) > 0 else 0
            avg_loss2 = losses2.mean() if len(losses2) > 0 else 0.01
            rs2 = avg_gain2 / avg_loss2
            rsi2 = 100 - (100 / (1 + rs2))
        else:
            rsi2 = 50
        
        if 35 <= rsi2 <= 45:
            score2 += 10
        elif rsi2 < 35:
            score2 += 8
        elif rsi2 < 50:
            score2 += 6
        else:
            score2 += 4
        
        # F4: 均线支撑
        if target_idx2 >= 60:
            ma60_2 = float(df2.loc[target_idx2-60:target_idx2, 'close'].mean())
            if pullback_low2 >= ma60_2 * 0.98:
                score2 += 5
        if target_idx2 >= 120:
            ma120_2 = float(df2.loc[target_idx2-120:target_idx2, 'close'].mean())
            if pullback_low2 >= ma120_2 * 0.98:
                score2 += 5
        
        if score2 >= 20:  # 只记录评分>=20的
            results.append({
                'code': ts_code,
                'score': score2,
                'pullback': pullback_ratio2,
                'vol_ratio': vol_ratio2,
            })
    
    except:
        continue
    
    if (i+1) % 500 == 0:
        print(f'已扫描 {i+1}/{len(all_files)}...')

print(f'\n扫描完成，发现{len(results)}只低吸信号')

# 排序
results.sort(key=lambda x: x['score'], reverse=True)

# 找光智科技排名
rank = 1
for r in results:
    if r['code'] == '300489.SZ':
        break
    rank += 1

print(f'\n【光智科技排名】')
print(f'排名：第{rank}名 / {len(results)}只')
print(f'评分：{score}/40分')
print(f'领先比例：{(1-rank/len(results))*100:.1f}%')

# 显示TOP20
print(f'\n\n【TOP20低吸信号】\n')
print(f'{"排名":<6} {"代码":<12} {"评分":<8} {"回踩比例":<10} {"量比":<10}')
print('-' * 50)
for i, r in enumerate(results[:20], 1):
    mark = '✓' if r['code'] == '300489.SZ' else ''
    print(f'{i:<6} {r["code"]:<12} {r["score"]:<8.0f} {r["pullback"]:<10.1%} {r["vol_ratio"]:<10.2f} {mark}')
