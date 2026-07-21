import pandas as pd
df = pd.read_csv('../report_daily/bull_stocks_all.csv', encoding='utf-8-sig')
samples = df.head(20)
for _, r in samples.iterrows():
    kf = str(r['扣非净利润(亿)'])
    print(f"{r['name']:8s} 营收={r['营收同比']:>8.1f} 利润={r['利润同比']:>10.1f} Q1利润={r['Q1利润同比']:>8.1f} 毛利率={r['毛利率']:>6.1f} 研发={r['研发投入%']:>5.1f} 非经常={r['非经常损益%']:>6.1f} 扣非={kf:>8s}")