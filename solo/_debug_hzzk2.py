"""调试昊志机电 AI新闻情绪（绕过tushare_quant模块初始化）"""
import os, time, re, json, requests, tushare as ts
from datetime import datetime, timedelta

# ===== 强制设置token =====
os.environ['TUSHARE_TOKEN'] = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
ts.set_token(os.environ['TUSHARE_TOKEN'])
TRADE_DATE = '2026-06-20'
pro = ts.pro_api()

# ===== 导入必要的常量和函数 =====
STOCK_DATA_DIR = r'd:\mystock\data'
NEWS_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "news_cache")
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)

# ===== 完整复刻 get_news_sentiment 的数据采集逻辑 =====
code = '300503.SZ'
name = '昊志机电'
theme = '机器人'
theme_state = '机器人概念'

today = datetime.strptime(TRADE_DATE, '%Y-%m-%d')
week_ago = (today - timedelta(days=7)).strftime('%Y%m%d')

print("=" * 70)
print(f"🔍 昊志机电 (300503.SZ) AI新闻情绪数据采集调试")
print("=" * 70)

# ---- 1. 研报 ----
print("\n【1】report_rc (研报数据)")
try:
    df = pro.report_rc(ts_code=code, start_date=week_ago, end_date=TRADE_DATE.replace('-', ''),
                       fields='trade_date,title,report_type,institution,report_content')
    print(f"  返回: {len(df) if df is not None else 0} 条")
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            print(f"  [{r['trade_date']}] {r['title']} ({r['institution']})")
            content = str(r.get('report_content', ''))[:100]
            print(f"    摘要: {content}...")
except Exception as e:
    print(f"  ❌ {e}")

time.sleep(1)

# ---- 2. 调研 ----
print("\n【2】stk_surv (调研数据)")
try:
    df = pro.stk_surv(ts_code=code, start_date=week_ago, end_date=TRADE_DATE.replace('-', ''))
    print(f"  返回: {len(df) if df is not None else 0} 条")
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            print(f"  [{r.get('surv_date', '')}] {r.get('title', r.get('org_name', ''))}")
except Exception as e:
    print(f"  ❌ {e}")

time.sleep(1)

# ---- 3. 股票基本信息 ----
print("\n【3】stock_basic (基本信息)")
try:
    df = pro.stock_basic(ts_code=code, fields='ts_code,name,industry,market')
    if df is not None and not df.empty:
        print(f"  {df.iloc[0].to_dict()}")
except Exception as e:
    print(f"  ❌ {e}")

time.sleep(1)

# ---- 4. 近期行情 ----
print("\n【4】daily (近5日行情)")
try:
    df = pro.daily(ts_code=code, start_date=(today - timedelta(days=10)).strftime('%Y%m%d'),
                   end_date=TRADE_DATE.replace('-', ''))
    if df is not None and not df.empty:
        df = df.sort_values('trade_date')
        for _, r in df.tail(5).iterrows():
            print(f"  [{r['trade_date']}] 收={r['close']:.2f} 涨={r['pct_chg']:+.2f}% 量={r['vol']:.0f}")
except Exception as e:
    print(f"  ❌ {e}")

time.sleep(1)

# ---- 5. 热点/概念 ----
print("\n【5】concept (概念板块)")
try:
    # 用 concept_detail 而非 concept
    df = pro.concept_detail(code='300503.SZ') if hasattr(pro, 'concept_detail') else None
    if df is None or df.empty:
        # 尝试用指数成分
        df = pro.index_daily(ts_code='399967.SZ') if hasattr(pro, 'index_daily') else None
    print(f"  {len(df) if df is not None else 0} 条")
except Exception as e:
    print(f"  ❌ {e}")

time.sleep(1)

# ---- 6. 东财7×24快讯 ----
print("\n【6】东财7×24快讯")
try:
    import uuid
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {"client": "web", "biz": "web_724", "fastColumn": "102",
              "pageSize": "30", "sortEnd": "", "req_trace": str(uuid.uuid4())}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kuaixun.eastmoney.com/"}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    d = r.json()
    items = d.get("data", {}).get("fastNewsList", []) or []
    print(f"  总快讯: {len(items)} 条")
    # 过滤机器人相关
    keywords = ['机器人', '昊志', '300503', '自动化', '工业']
    filtered = [n for n in items if any(kw in n.get('title', '') for kw in keywords)]
    print(f"  过滤「机器人」相关: {len(filtered)} 条")
    for n in filtered[:5]:
        print(f"  [{n.get('showTime', '')}] {n.get('title', '')[:60]}")
except Exception as e:
    print(f"  ❌ {e}")

time.sleep(1)

# ---- 7. Bing新闻搜索 ----
print("\n【7】Bing新闻搜索")
try:
    search_url = "https://www.bing.com/news/search"
    params = {"q": f"{name} {theme} 2026", "setmkt": "zh-CN", "newser": "1"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(search_url, params=params, headers=headers, timeout=15)
    # 解析新闻
    titles = re.findall(r'<a[^>]+class="news-title"[^>]*>([^<]+)</a>', r.text)
    times = re.findall(r'<span[^>]+class="news-date"[^>]*>([^<]+)</span>', r.text)
    print(f"  Bing搜索「{name} {theme}」: 找到 {len(titles)} 条")
    for t in titles[:5]:
        clean = re.sub(r'<[^>]+>', '', t).strip()
        print(f"  - {clean[:80]}")
except Exception as e:
    print(f"  ❌ {e}")

print(f"\n{'='*70}")
print("✅ 数据采集调试完成")
