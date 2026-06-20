"""调试昊志机电(300503.SZ) AI新闻情绪分析"""
import sys, os, time
sys.path.insert(0, r'd:\mystock\solo')

# 强制重新设置日期（方便调试）
TRADE_DATE = '2026-06-20'

# 设置 token
os.environ['TUSHARE_TOKEN'] = 'b39a53d344e08d4d6f2d7b28e3c8c4a5b6c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2'

from tushare_quant import get_news_sentiment, pro

print("=" * 70)
print("🔍 昊志机电 (300503.SZ) AI新闻情绪分析调试")
print("=" * 70)

# 先确认 stock_basic
print("\n【1】基本信息确认")
try:
    df = pro.stock_basic(ts_code='300503.SZ', fields='ts_code,name,industry,market')
    if df is not None and not df.empty:
        print(f"  名称: {df.iloc[0]['name']}")
        print(f"  行业: {df.iloc[0]['industry']}")
        print(f"  市场: {df.iloc[0]['market']}")
    else:
        print("  ❌ 无基本信息")
except Exception as e:
    print(f"  ❌ {e}")

# 调用情绪分析
print("\n【2】调用 get_news_sentiment()")
try:
    score = get_news_sentiment(
        ts_code='300503.SZ',
        name='昊志机电',
        theme='机器人',
        theme_state='机器人概念'
    )
    print(f"\n  ✅ 最终情绪得分: {score}")
except Exception as e:
    import traceback
    print(f"  ❌ 异常: {e}")
    traceback.print_exc()

print(f"\n{'='*70}")
