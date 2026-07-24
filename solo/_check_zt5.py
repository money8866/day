import os, pandas as pd

# 检查每只CSV的最后日期和最近几天的数据
codes_to_check = ['002300.SZ', '603618.SH', '600973.SH', '301012.SZ', '000400.SZ', '600089.SH']
cache_dir = r'd:\mystock\cache_daily'
for code in codes_to_check:
    csv_path = os.path.join(cache_dir, f"{code}.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        df = df.sort_values('trade_date')
        print(f"\n{code} 最后5条:")
        print(df[['trade_date','close','pct_chg','vol']].tail(5).to_string())
        print(f"  最后日期: {df['trade_date'].iloc[-1]}")

# 检查主题CSV文件的 zt_count=4 是怎么算出来的
# 问题可能出在：zt_flag=1 的条件在 per_stock_features 中用的是 >=9.5
# 但 CSV 数据可能没有更新到最新，或者 zt_count 来自不同的日盘数据
# 让我看 theme_trend_sentiment_score.py 里的 per_stock_features
