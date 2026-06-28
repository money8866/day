"""
盘后选股 — 每日复盘
用法: python daily_pick.py [日期]
默认日期 = 今天
"""
import pandas as pd, os, sys
from datetime import date, timedelta

OUTPUT = r'D:\mystock\solo\trend_feature_output'

def find_latest_csv(prefix: str):
    fs = sorted([f for f in os.listdir(OUTPUT) if f.startswith(prefix) and f.endswith('.csv')], reverse=True)
    return os.path.join(OUTPUT, fs[0]) if fs else None

def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime('%Y%m%d')
    print(f"{'='*65}")
    print(f"  盘后选股  {today}")
    print(f"{'='*65}")
    
    sources = [
        ('trend_system_v2', '趋势启动', 'signal_score', ['pct_chg','vol_ratio','above_ma20_pct','above_ma60_pct']),
        ('trend_system_v2', '二波启动', 'signal_score', ['pct_chg','vol_ratio','above_ma20_pct']),
        ('entry_precision', '精准入场', 'entry_score', ['pct_chg','vol_ratio','above_ma20_pct','above_ma60_pct']),
        ('consolidation_breakout', '震荡突破', 'signal_score', ['pct_chg','vol_ratio','above_ma20_pct']),
    ]
    
    all_rows = []
    seen = set()
    
    for prefix, label, score_col, cols in sources:
        path = find_latest_csv(prefix)
        if not path:
            continue
        df = pd.read_csv(path)
        df['signal_date'] = df['signal_date'].astype(str)
        day_df = df[df['signal_date'] == today]
        
        for _, r in day_df.iterrows():
            key = (r['ts_code'], label)
            if key in seen:
                continue
            seen.add(key)
            row = {
                '代码': r['ts_code'],
                '类型': label,
                '评分': r.get(score_col, 0),
                '涨幅%': r.get('pct_chg', 0),
                '次日%': r.get('return_1d', ''),
                '量比': r.get('vol_ratio', 0),
                '距MA20%': r.get('above_ma20_pct', 0),
                '波段': r.get('consecutive_up', ''),
            }
            if 'above_ma60_pct' in r and 'above_ma60_pct' in cols:
                row['距MA60%'] = r.get('above_ma60_pct', 0)
            else:
                row['距MA60%'] = ''
            all_rows.append(row)
    
    if not all_rows:
        print(f"\n  {today} 无信号\n")
        return
    
    out = pd.DataFrame(all_rows).sort_values('评分', ascending=False).reset_index(drop=True)
    
    # 统计
    print(f"\n  信号数: {len(out)}")
    print(f"  趋势启动: {len(out[out['类型']=='趋势启动'])}")
    print(f"  二波启动: {len(out[out['类型']=='二波启动'])}")
    print(f"  精准入场: {len(out[out['类型']=='精准入场'])}")
    print(f"  震荡突破: {len(out[out['类型']=='震荡突破'])}")
    print()
    
    # 展示
    print(f"  {'代码':<12} {'类型':<8} {'评分':>4} {'涨幅%':>5} {'次日%':>5} {'量比':>4} {'波段':>4} {'距MA20%':>6} {'距MA60%':>6}")
    print(f"  {'-'*65}")
    for _, r in out.iterrows():
        ma60 = f"{r['距MA60%']:.1f}%" if r['距MA60%'] != '' else '  -'
        band = f"D{r['波段']}" if r['波段'] != '' else '  -'
        nxt = f"{r['次日%']:.2f}%" if r['次日%'] != '' else '   -'
        print(f"  {r['代码']:<12} {r['类型']:<8} {r['评分']:>4.0f} {r['涨幅%']:>4.1f}% {nxt:>5} {r['量比']:>4.2f} {band:>4} {r['距MA20%']:>5.1f}% {ma60}")
    
    # 每日关注推荐（70分以上重点标注）
    print(f"\n  每日关注推荐:")
    top = out[out['评分'] >= 60].head(10)
    if len(top) == 0:
        top = out.head(5)
    for _, r in top.iterrows():
        stars = '★★★★★' if r['评分'] >= 80 else '★★★★' if r['评分'] >= 70 else '★★★'
        print(f"    {r['代码']:<12} {r['类型']:<8} {stars} 评分={r['评分']:.0f}")
    
    # 保存
    csv_name = f"daily_pick_{today}.csv"
    csv_path = os.path.join(OUTPUT, csv_name)
    out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n  CSV已保存: {csv_path}")

if __name__ == '__main__':
    main()
