import requests
import time
import json

def get_a_stock_codes():
    """获取全市场A股代码列表（sh/sz/bj）"""
    codes = []
    
    # 上证A股：600xxx, 601xxx, 603xxx, 605xxx, 608xxx, 609xxx
    for pre in ['600', '601', '603', '605', '608', '609']:
        for i in range(0, 1000):
            codes.append(f"sh{pre}{i:03d}")
    
    # 深证主板：000xxx, 001xxx
    for pre in ['000', '001']:
        for i in range(0, 1000):
            codes.append(f"sz{pre}{i:03d}")
    
    # 深证中小板：002xxx
    for i in range(0, 1000):
        codes.append(f"sz002{i:03d}")
    
    # 创业板：300xxx, 301xxx
    for pre in ['300', '301']:
        for i in range(0, 1000):
            codes.append(f"sz{pre}{i:03d}")
    
    # 北交所：83xxx, 87xxx, 88xxx
    for pre in ['83', '87', '88']:
        for i in range(0, 1000):
            codes.append(f"bj{pre}{i:03d}")
    
    return codes


def fetch_full_market_stats_sina(batch_size=99, timeout=15):
    """
    使用新浪批量接口获取全市场涨跌停统计
    返回: {total, zt_count, dt_count, up_count, down_count, up_ratio, down_ratio}
    """
    codes = get_a_stock_codes()
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }
    
    total = 0
    zt_count = 0  # 涨停 >=9.5%
    dt_count = 0  # 跌停 <=-9.5%
    up_count = 0  # 上涨 >0%
    down_count = 0  # 下跌 <0%
    success_count = 0
    
    start_total = time.time()
    batch_count = (len(codes) + batch_size - 1) // batch_size
    
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        url = f"https://hq.sinajs.cn/list={','.join(batch)}"
        
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.encoding = "gbk"
            lines = r.text.strip().split('\n')
            
            for line in lines:
                if '=' not in line or '""' in line:
                    continue
                
                parts = line.split('"')
                if len(parts) < 2:
                    continue
                
                data = parts[1].split(',')
                if len(data) < 4:
                    continue
                
                try:
                    name = data[0]
                    last_close = float(data[2])
                    price = float(data[3])
                    
                    if last_close <= 0 or price <= 0:
                        continue
                    
                    pct = (price - last_close) / last_close * 100
                    total += 1
                    success_count += 1
                    
                    if pct >= 9.5:
                        zt_count += 1
                    elif pct <= -9.5:
                        dt_count += 1
                    if pct > 0:
                        up_count += 1
                    elif pct < 0:
                        down_count += 1
                    
                except (ValueError, IndexError):
                    continue
            
            # 进度显示
            progress = ((i + batch_size) / len(codes)) * 100
            if int(progress) % 10 == 0 and progress > 0:
                elapsed = time.time() - start_total
                print(f"  进度: {progress:.0f}% 已获取: {total}只 耗时: {elapsed:.1f}秒")
            
            # 控制频率
            time.sleep(0.1)
            
        except Exception as e:
            print(f"  批次{i//batch_size}失败: {e}")
            continue
    
    total_time = time.time() - start_total
    print(f"\n完成: 总股票{total}只 耗时{total_time:.1f}秒")
    
    if total > 0:
        return {
            'total': total,
            'zt_count': zt_count,
            'dt_count': dt_count,
            'up_count': up_count,
            'down_count': down_count,
            'up_ratio': round(up_count / total * 100, 1),
            'down_ratio': round(down_count / total * 100, 1),
            'success_count': success_count,
            'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'elapsed_ms': int(total_time * 1000)
        }
    return None


# 测试
if __name__ == "__main__":
    print("=== 获取全市场A股涨跌停统计 ===")
    result = fetch_full_market_stats_sina(batch_size=99)
    
    if result:
        print("\n=== 统计结果 ===")
        print(f"全市场A股: {result['total']}只")
        print(f"涨停: {result['zt_count']}只")
        print(f"跌停: {result['dt_count']}只")
        print(f"上涨: {result['up_count']}只 ({result['up_ratio']}%)")
        print(f"下跌: {result['down_count']}只 ({result['down_ratio']}%)")
        print(f"耗时: {result['elapsed_ms']}ms")
        print(f"更新时间: {result['updated']}")
        
        # 保存缓存
        with open('d:/mystock/solo/cache_daily/full_market_stats.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("\n✅ 缓存已保存")
    else:
        print("❌ 获取失败")
