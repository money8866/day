import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bwave_strategy import load_qualified_pool, get_data, detect_all_awaves, detect_bwave, detect_bwave_relaxed, check_launch_signal, detect_bwave_divergence, calc_bwave_score, calc_divergence_score, make_result_base, OUTPUT_DIR
import pandas as pd
from datetime import datetime

codes = load_qualified_pool('qualified')[:50]
total = len(codes)
all_results = []
diag = {'total': 0, 'a_wave': 0, 'scanned': 0, 'signals': 0, 'launch': 0, 'divergence': 0}

for i, ts_code in enumerate(codes):
    df_full = get_data(ts_code)
    if df_full is None or len(df_full) < 250:
        continue
    diag['total'] += 1

    awaves = detect_all_awaves(df_full)
    if not awaves:
        continue
    diag['a_wave'] += 1

    for awave_full in awaves:
        a_end_idx = awave_full['end_idx']
        a_duration = awave_full['duration']
        bt_start = a_end_idx + int(a_duration * 0.6)
        bt_end = min(a_end_idx + a_duration * 3 + 10, len(df_full) - 20)
        if bt_start >= bt_end:
            continue
        diag['scanned'] += 1
        triggered_signals = set()

        for day_idx in range(bt_start, bt_end):
            df_slice = df_full.iloc[:day_idx + 1].reset_index(drop=True)
            awave = awave_full
            sig = None; score = None; bwave_used = None; signal_type = None

            bwave = detect_bwave(df_slice, awave)
            if bwave:
                launch = check_launch_signal(df_slice, awave, bwave)
                if launch:
                    s = calc_bwave_score(awave, bwave, launch)
                    if s['total'] >= 55:
                        sig = launch; score = s; bwave_used = bwave; signal_type = '启动'; diag['launch'] += 1

            if not sig:
                bwave_r = detect_bwave_relaxed(df_slice, awave) if not bwave else None
                if bwave_r:
                    launch = check_launch_signal(df_slice, awave, bwave_r)
                    if launch:
                        s = calc_bwave_score(awave, bwave_r, launch)
                        if s['total'] >= 50:
                            sig = launch; score = s; bwave_used = bwave_r; signal_type = '启动'; diag['launch'] += 1

            if not sig and bwave:
                div = detect_bwave_divergence(df_slice, awave, bwave)
                if div:
                    s = calc_divergence_score(awave, bwave, div)
                    if s['total'] >= 50:
                        sig = div; score = s; bwave_used = bwave; signal_type = '底背离'; diag['divergence'] += 1

            if not sig:
                if not bwave: bwave_r = detect_bwave_relaxed(df_slice, awave)
                else: bwave_r = bwave
                if bwave_r:
                    div = detect_bwave_divergence(df_slice, awave, bwave_r)
                    if div:
                        s = calc_divergence_score(awave, bwave_r, div)
                        if s['total'] >= 50:
                            sig = div; score = s; bwave_used = bwave_r; signal_type = '底背离'; diag['divergence'] += 1

            if not sig: continue
            sig_key = (signal_type, sig['launch_date'])
            if sig_key in triggered_signals: continue
            triggered_signals.add(sig_key)

            sig_idx = sig['launch_idx']
            entry_price = df_full.iloc[sig_idx]['close']
            rets = {}
            for w in [1, 5, 10, 20]:
                fi = min(sig_idx + w, len(df_full) - 1)
                rets[w] = round((df_full.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0

            tags = []
            if sig.get('bottom_signal_date'): tags.append('见底' + sig['bottom_signal_date'])
            if sig.get('rsi_golden_date'): tags.append('RSI金叉' + sig['rsi_golden_date'])
            if sig.get('macd_golden_date'): tags.append('MACD金叉' + sig['macd_golden_date'])

            all_results.append({
                **make_result_base(ts_code, df_full.iloc[-1]['trade_date'], awave, bwave_used, sig, score, rets),
                'signal_type': signal_type, 'signal_tags': ','.join(tags) if tags else '',
                'backtest_date': str(df_full.iloc[day_idx]['trade_date']),
            })
            diag['signals'] += 1

    if (i + 1) % 10 == 0:
        print(f"  [{i+1}/{total}] {len(all_results)} signals", flush=True)

print(f"\nA浪: {diag['a_wave']}  扫描: {diag['scanned']}  启动: {diag['launch']}  底背离: {diag['divergence']}  信号: {diag['signals']}", flush=True)

if all_results:
    df_bt = pd.DataFrame(all_results)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUTPUT_DIR, f'bwave_backtest_50_{timestamp}.csv')
    df_bt.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'CSV: {csv_path}', flush=True)
    
    for sig_type in ['启动', '底背离']:
        sub = df_bt[df_bt['signal_type'] == sig_type]
        if sub.empty: continue
        print(f'\n--- {sig_type} ({len(sub)}个) ---', flush=True)
        for w in [1, 5, 10, 20]:
            col = f'return_{w}d'
            if col in sub.columns:
                r = sub[col].dropna()
                if len(r) > 0:
                    wins = r[r > 0]
                    print(f'  +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>4.0f}%  中位={r.median():>6.2f}%  亏>15%={(r<-15).sum()}  赚>15%={(r>15).sum()}', flush=True)
    
    for tag_name in ['见底', 'RSI金叉', 'MACD金叉']:
        sub = df_bt[df_bt['signal_tags'].str.contains(tag_name, na=False)]
        if sub.empty: continue
        print(f'\n--- {tag_name} ({len(sub)}个) ---', flush=True)
        for w in [5, 10]:
            col = f'return_{w}d'
            if col in sub.columns:
                r = sub[col].dropna()
                if len(r) > 0:
                    wins = r[r > 0]
                    print(f'  +{w:>2}d: 均={r.mean():>6.2f}%  胜率={len(wins)/len(r)*100:>4.0f}%', flush=True)