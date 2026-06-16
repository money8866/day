import requests
import time

# 1. 东方财富 实时涨停池
print("=== 东方财富 涨停池 ===")
try:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 20, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14",
        "_": int(time.time() * 1000)
    }
    r = requests.get(url, params=params, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data = r.json()
    if data.get("data") and data["data"].get("diff"):
        total = data["data"]["total"]
        print(f"涨停数: {total}")
        for item in list(data["data"]["diff"])[:3]:
            print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
    else:
        print(f"返回: {str(data)[:200]}")
except Exception as e:
    print(f"失败: {e}")

# 2. 东方财富 实时跌停池
print("\n=== 东方财富 跌停池 ===")
try:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 20, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14",
        "_": int(time.time() * 1000)
    }
    # fs参数不同：fid=f3改为跌停排序，取涨幅<= -9.5
    params2 = {
        "pn": 1, "pz": 20, "po": 0, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f4,f5,f6",
        "_": int(time.time() * 1000)
    }
    r2 = requests.get(url, params=params2, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data2 = r2.json()
    if data2.get("data") and data2["data"].get("diff"):
        total2 = data2["data"]["total"]
        print(f"跌停数: {total2}")
        for item in list(data2["data"]["diff"])[:3]:
            print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
    else:
        print(f"返回: {str(data2)[:200]}")
except Exception as e:
    print(f"失败: {e}")

# 3. 新浪 全市场股票接口
print("\n=== 新浪 全市场行情（含涨跌停统计）===")
try:
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {
        "page": 1, "num": 5, "sort": "changepercent", "asc": 0,
        "node": "hs_a",  # 沪深A股
        "symbol": "", "_s_r_a": "page"
    }
    r3 = requests.get(url, params=params, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    r3.encoding = "utf-8"
    text = r3.text
    print(f"新浪A股前5: {text[:300]}")
except Exception as e:
    print(f"失败: {e}")

# 4. 腾讯 全市场涨跌停统计
print("\n=== 腾讯 今日涨停/跌停统计 ===")
try:
    # 涨停
    url_zt = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktData/mktedStat"
    params_zt = {"type": "zt", "date": ""}
    r_zt = requests.get(url_zt, params=params_zt, headers={
        "Referer": "https://finance.qq.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    r_zt.encoding = "utf-8"
    print(f"涨停统计: {r_zt.text[:200]}")
except Exception as e:
    print(f"失败: {e}")

# 5. 东方财富 涨跌停统计数据
print("\n=== 东方财富 涨跌停数量统计 ===")
try:
    url_stat = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params_stat = {
        "reportName": "RPT_STOCK_LIMIT_UP_STATISTIC",
        "columns": "TRADE_DATE,LIMIT_UP_COUNT,LIMIT_DOWN_COUNT,RAISE_COUNT,FALL_COUNT",
        "pageNumber": 1,
        "pageSize": 1,
        "sortTypes": -1,
        "sortColumns": "TRADE_DATE",
        "source": "WEB",
        "client": "WEB"
    }
    r_stat = requests.get(url_stat, params=params_stat, headers={
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    print(f"涨跌停统计: {r_stat.text[:400]}")
except Exception as e:
    print(f"失败: {e}")
