import sys, os, subprocess, glob, time
sys.path.insert(0, '.')
import pandas as pd

# 5-6月的关键交易日
dates = ['20260506','20260509','20260514','20260520','20260526',
         '20260601','20260605','20260610','20260616','20260620','20260626']

all_signals = []
for dt in dates:
    print(f'\n[{dt}] 扫描开始...', flush=True)
    result = subprocess.run(
        ['python', 'wave2_pattern_scanner.py',
         '--csv', '../report_daily/bull_stocks_qualified.csv',
         '--date', dt, '--today'],
        capture_output=True, text=True, timeout=600
    )
    
    csv_files = sorted(glob.glob('output/wave2_pattern_*.csv'), key=os.path.getmtime)
    if csv_files:
        latest = csv_files[-1]
        df_new = pd.read_csv(latest)
        sideways = df_new[df_new['pattern'] == '强势横盘']
        print(f'  {len(sideways)}只强势横盘信号', flush=True)
        
        for _, r in sideways.iterrows():
            all_signals.append({
                'scan_date': dt, 'name': r['name'], 'ts_code': r['ts_code'],
                'score': r['score'], 'wave1_gain': r['wave1_gain'],
                'pullback_pct': r['pullback_pct'], 'rsi': r.get('rsi', 50),
                'vol_ratio': r.get('vol_ratio', 1.0),
                'details': str(r.get('score_details', '')),
                'entry_price': r.get('entry_price', 0),
            })
    
    # 为避免输出文件重名，重命名
    for f in csv_files:
        if 'wave2_pattern' in f:
            os.rename(f, f.replace('wave2_pattern', f'wave2_backtest_{dt}'))
            break

rdf = pd.DataFrame(all_signals)
rdf.to_csv('output/sideways_signals_may_june.csv', index=False, encoding='utf-8-sig')
print(f'\n{"="*50}')
print(f'完成！共扫描{len(dates)}天，发现{len(rdf)}个强势横盘信号')
for d in sorted(rdf['scan_date'].unique()):
    cnt = len(rdf[rdf['scan_date'] == d])
    print(f'  {d}: {cnt}只')
