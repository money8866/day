"""全市场100-1000亿市值扫描 - 二波形态"""
import pandas as pd
import sys
import time
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

# 初始化Tushare
import tushare as ts
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api(token)

print('=== 全市场100-1000亿市值扫描 ===')
print('扫描日期: 2026-06-11\n')

# 1. 获取股票池
print('【步骤1】筛选100-1000亿市值股票...')
daily_basic = pro.daily_basic(trade_date='20260611', fields='ts_code,circ_mv')
if len(daily_basic) == 0:
    # 尝试上一个交易日
    daily_basic = pro.daily_basic(trade_date='20260610', fields='ts_code,circ_mv')

# 筛选100-1000亿
pool = daily_basic[(daily_basic['circ_mv'] >= 100) & (daily_basic['circ_mv'] <= 1000)]
pool_list = pool['ts_code'].tolist()
print(f'符合条件的股票: {len(pool_list)}只\n')

# 2. 获取股票名称
stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))

# 3. 扫描二波信号
print('【步骤2】扫描二波信号...\n')

results = []
errors = []

for i, ts_code in enumerate(pool_list):
    try:
        # 从本地缓存读取日线数据
        import os
        cache_file = f'D:\\mystock\\cache_daily\\{ts_code}.csv'
        
        if not os.path.exists(cache_file):
            continue
        
        daily = pd.read_csv(cache_file, encoding='utf-8')
        daily['trade_date'] = daily['trade_date'].astype(str)
        daily = daily.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        # 找到6月11日
        target_date = '20260611'
        target_row = daily[daily['trade_date'] == target_date]
        
        if len(target_row) == 0:
            continue
        
        target_idx = target_row.index[0]
        today_pct = float(daily.loc[target_idx, 'pct_chg'])
        
        # 只关注涨幅≥5%的股票
        if today_pct < 5:
            continue
        
        # 二波检测
        lookback = 60
        start_idx = max(0, target_idx - lookback)
        recent = daily.loc[start_idx:target_idx].copy()
        
        # 排除最近5天
        recent_ex5 = recent.iloc[:-5]
        
        # 找首波涨停
        limit_up = recent_ex5[recent_ex5['pct_chg'] >= 9.4]
        
        if len(limit_up) > 0:
            wave1_idx = limit_up['pct_chg'].idxmax()
            wave1_row = daily.loc[wave1_idx]
            wave1_close = float(wave1_row['close'])
            wave1_date = wave1_row['trade_date']
            
            # 回踩最低点
            after_wave1 = daily.loc[wave1_idx+1:target_idx]
            
            if len(after_wave1) > 0:
                pullback_low = float(after_wave1['low'].min())
                pullback_ratio = pullback_low / wave1_close
                
                # 二波判断
                latest_close = float(daily.loc[target_idx, 'close'])
                
                is_wave2 = (
                    today_pct >= 5 and
                    latest_close >= wave1_close * 0.98 and
                    pullback_ratio >= 0.80
                )
                
                # 技术面评分
                tech_score = 0.0
                
                if is_wave2:
                    # F6换手率（从daily_basic获取）
                    turnover_rate = 10.0  # 默认值
                    
                    try:
                        db = pro.daily_basic(ts_code=ts_code, trade_date=target_date, fields='turnover_rate')
                        if len(db) > 0:
                            turnover_rate = float(db.iloc[0]['turnover_rate'])
                    except:
                        pass
                    
                    if turnover_rate >= 8:
                        tech_score += 2.0
                    elif turnover_rate >= 5:
                        tech_score += 1.5
                    else:
                        tech_score += 1.0
                    
                    # F8成交量
                    tech_score += 1.0
                    
                    # WAVE2二波
                    if today_pct >= 9.4:
                        tech_score += 3.0
                    else:
                        tech_score += 2.0
                
                # 记录结果
                results.append({
                    'code': ts_code,
                    'name': name_map.get(ts_code, ts_code[:6]),
                    'industry': industry_map.get(ts_code, ''),
                    'mv': float(pool[pool['ts_code'] == ts_code]['circ_mv'].iloc[0]),
                    'pct': today_pct,
                    'tech': tech_score,
                    'wave2': is_wave2,
                    'wave1_date': wave1_date,
                    'pullback_ratio': pullback_ratio,
                    'turnover': turnover_rate,
                })
                
                status = '✓二波' if is_wave2 else '启动'
                print(f'{status} {name_map.get(ts_code, ts_code[:6]):<10} {today_pct:+5.1f}% 技术分{tech_score:.1f} 市值{float(pool[pool["ts_code"] == ts_code]["circ_mv"].iloc[0]):.0f}亿')
        
        time.sleep(0.01)
        
    except Exception as e:
        errors.append((ts_code, str(e)))
    
    if (i+1) % 100 == 0:
        print(f'已扫描 {i+1}/{len(pool_list)}...')

print('\n' + '='*70)
print(f'扫描完成：{len(pool_list)}只 → 发现{len(results)}只信号')
print('='*70)

if results:
    results.sort(key=lambda x: x['tech'], reverse=True)
    
    print(f'\nTOP50信号股票：\n')
    print(f'{"排名":<4} {"股票":<12} {"行业":<8} {"市值":<8} {"涨幅":<8} {"技术分":<8} {"首波日期":<10} {"回踩比例":<10} {"换手率":<10} {"二波"}')
    print('-'*100)
    
    for i, r in enumerate(results[:50], 1):
        wave2_mark = '✓' if r['wave2'] else '✗'
        print(f'{i:<4} {r["name"]:<12} {r["industry"]:<8} {r["mv"]:<8.0f} {r["pct"]:+6.1f}%  {r["tech"]:<8.1f} {r["wave1_date"]:<10} {r["pullback_ratio"]:<10.1%} {r["turnover"]:<10.1f}% {wave2_mark}')
    
    # 统计
    wave2_count = sum(1 for r in results if r['wave2'])
    print(f'\n二波确认信号: {wave2_count}只')
    print(f'启动信号: {len(results) - wave2_count}只')
    
    # 行业分布
    print(f'\n\n【行业分布】')
    industry_dist = {}
    for r in results:
        ind = r['industry'] or '未知'
        if ind not in industry_dist:
            industry_dist[ind] = 0
        industry_dist[ind] += 1
    
    for ind, count in sorted(industry_dist.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f'{ind}: {count}只')
    
    # 保存结果
    result_df = pd.DataFrame(results)
    result_df.to_csv(r'D:\mystock\solo\multi_factor_picker\全市场扫描_20260611.csv', index=False, encoding='utf-8-sig')
    print(f'\n结果已保存至 全市场扫描_20260611.csv')

if errors:
    print(f'\n错误统计: {len(errors)}只')
    for code, err in errors[:5]:
        print(f'  {code}: {err}')
