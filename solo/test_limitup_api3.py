import requests
import time
import json

# 东方财富 数据中心 - 涨跌停统计数据（正确报表名）
print("=== 东方财富 涨跌停统计（数据中心）===")
try:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_STOCK_LIMIT_STATISTIC",
        "columns": "TRADE_DATE,LIMIT_UP_COUNT,LIMIT_DOWN_COUNT,LIMIT_UP_BAN_COUNT,FINISH_COUNT,FINISH_RATIO",
        "pageNumber": 1,
        "pageSize": 5,
        "sortTypes": -1,
        "sortColumns": "TRADE_DATE",
        "source": "WEB",
        "client": "WEB"
    }
    r = requests.get(url, params=params, headers={
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data = r.json()
    print(f"状态: success={data.get('success')}, code={data.get('code')}")
    if data.get("result") and data["result"].get("data"):
        for item in data["result"]["data"]:
            print(f"  {item['TRADE_DATE']} 涨停{item['LIMIT_UP_COUNT']} 跌停{item['LIMIT_DOWN_COUNT']} 炸板{item.get('LIMIT_UP_BAN_COUNT',0)}")
    else:
        print(f"返回: {str(data)[:400]}")
except Exception as e:
    print(f"失败: {e}")

# 尝试东方财富行情中心 涨停统计接口
print("\n=== 东方财富 行情中心 涨停池（正确filter语法）===")
try:
    url2 = "https://push2.eastmoney.com/api/qt/clist/get"
    # 使用正确的filter格式：f3为涨幅字段
    params2 = {
        "pn": 1, "pz": 10, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14",
        "filters": "f3>=9.5",
        "_": int(time.time() * 1000)
    }
    r2 = requests.get(url2, params=params2, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data2 = r2.json()
    total_zt = data2.get("data", {}).get("total", "N/A")
    print(f"涨停数(过滤f3>=9.5): {total_zt}")
    if data2.get("data", {}).get("diff"):
        for item in list(data2["data"]["diff"])[:3]:
            print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
    else:
        print(f"返回: {str(data2)[:300]}")
except Exception as e:
    print(f"失败: {e}")

# 东方财富 昨日涨停今日未封(炸板)
print("\n=== 东方财富 昨日涨停今日炸板 ===")
try:
    url3 = "https://push2.eastmoney.com/api/qt/clist/get"
    params3 = {
        "pn": 1, "pz": 10, "po": 0, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14,f62",  # f62=昨收价
        "filters": "f3>=0",
        "_": int(time.time() * 1000)
    }
    r3 = requests.get(url3, params=params3, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data3 = r3.json()
    total = data3.get("data", {}).get("total", "N/A")
    print(f"返回总数: {total}")
    if data3.get("data", {}).get("diff"):
        for item in list(data3["data"]["diff"])[:5]:
            print(f"  {item.get('f14','?')} 涨幅{item.get('f3','?')}% 昨收{item.get('f62','?')}")
    else:
        print(f"返回: {str(data3)[:300]}")
except Exception as e:
    print(f"失败: {e}")

# 腾讯 涨停统计
print("\n=== 腾讯 涨停统计 ===")
try:
    # 尝试不同的腾讯接口
    urls = [
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktData/ztCount",
        "https://web.ifzq.gtimg.cn/appstock/app/ztcount/get",
        "https://qt.gtimg.cn/q=sh000001",
    ]
    for u in urls:
        try:
            r = requests.get(u, headers={
                "Referer": "https://finance.qq.com/",
                "User-Agent": "Mozilla/5.0"
            }, timeout=5)
            print(f"{u}: {r.text[:200]}")
        except:
            pass
except Exception as e:
    print(f"失败: {e}")

# 同花顺 涨停数据
print("\n=== 同花顺 涨停数据 ===")
try:
    url_ths = "https://data.10jqka.com.cn/funds/ztb/"
    r_ths = requests.get(url_ths, headers={
        "Referer": "https://data.10jqka.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    r_ths.encoding = "utf-8"
    # 找涨停数
    import re
    zt_match = re.search(r'涨停(\d+)家', r_ths.text)
    dt_match = re.search(r'跌停(\d+)家', r_ths.text)
    print(f"涨停: {zt_match.group(1) if zt_match else 'N/A'}")
    print(f"跌停: {dt_match.group(1) if dt_match else 'N/A'}")
except Exception as e:
    print(f"失败: {e}")
