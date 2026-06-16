import requests
import time

# 测试新浪批量接口
print("=== 新浪批量接口测试 ===")

# 构造批量代码列表（上证+深证+北交所部分股票）
sh_codes = [f"sh{i:06d}" for i in range(1, 100)]  # sh000001 - sh000099
sz_codes = [f"sz{i:06d}" for i in range(1, 100)]  # sz000001 - sz000099
bj_codes = [f"bj83{i:04d}" for i in range(1, 50)]  # bj830001 - bj830049

# 测试不同批次大小
batch_sizes = [50, 100, 150, 200]

for batch_size in batch_sizes:
    codes = sh_codes[:batch_size]
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    
    start = time.time()
    try:
        r = requests.get(url, headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0"
        }, timeout=10)
        r.encoding = "gbk"
        lines = r.text.strip().split('\n')
        success = sum(1 for line in lines if '=' in line and '""' not in line)
        elapsed = time.time() - start
        
        print(f"批次{batch_size}只: 耗时{elapsed:.2f}秒, 成功{success}/{len(lines)}条")
        if lines:
            print(f"  第一条: {lines[0][:80]}")
            print(f"  最后一条: {lines[-1][:80]}")
    except Exception as e:
        print(f"批次{batch_size}只: 失败 - {e}")

# 测试深证和北交所
print("\n=== 深证代码测试 ===")
url_sz = f"https://hq.sinajs.cn/list={','.join(sz_codes[:50])}"
try:
    r = requests.get(url_sz, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=10)
    r.encoding = "gbk"
    lines = r.text.strip().split('\n')
    print(f"深证50只: {len(lines)}条")
    print(f"  第一条: {lines[0][:80]}")
except Exception as e:
    print(f"深证失败: {e}")

print("\n=== 北交所代码测试 ===")
url_bj = f"https://hq.sinajs.cn/list={','.join(bj_codes[:30])}"
try:
    r = requests.get(url_bj, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=10)
    r.encoding = "gbk"
    lines = r.text.strip().split('\n')
    print(f"北交所30只: {len(lines)}条")
    print(f"  第一条: {lines[0][:80]}")
except Exception as e:
    print(f"北交所失败: {e}")

# 测试解析逻辑
print("\n=== 解析示例 ===")
url_test = "https://hq.sinajs.cn/list=sh000001,sh000300,sz399001,sh600519"
r = requests.get(url_test, headers={
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0"
}, timeout=10)
r.encoding = "gbk"
lines = r.text.strip().split('\n')
for line in lines:
    if '=' in line and '""' not in line:
        parts = line.split('"')
        if len(parts) > 1:
            data = parts[1].split(',')
            if len(data) >= 4:
                name = data[0]
                last_close = float(data[2])
                price = float(data[3])
                pct = (price - last_close) / last_close * 100
                print(f"{name}: 现价{price:.2f} 涨{pct:+.2f}%")
