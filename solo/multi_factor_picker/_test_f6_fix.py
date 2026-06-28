"""快速测试F6修复是否生效"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
from trend_picker import get_daily_data
from trend_picker_v2_draft import score_technical_v2

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = get_daily_data(fetcher, '600498.SH', '20260101', '20260611')
score, detail = score_technical_v2(daily, is_wave2=False)

print(f'F6得分: {detail.get("F6", {}).get("score", "N/A")}')
print(f'F6详情: {detail.get("F6", {})}')
print(f'\n总分: {score}')
