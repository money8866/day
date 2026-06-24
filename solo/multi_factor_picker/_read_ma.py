import os
fp = r'D:\mystock\solo\cache_backbone_tushare\market_analysis_20260623.txt'
# Try different encodings
for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
    try:
        with open(fp, 'r', encoding=enc) as f:
            content = f.read()
        print(f"=== {enc} ===")
        print(content[:2000])
        break
    except:
        continue
