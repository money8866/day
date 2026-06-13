#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 realtime_theme_monitor.py 中的 fetch_full_market_stats 函数"""
import re

file_path = r"D:\mystock\solo\realtime_theme_monitor.py"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 找到函数定义开始位置
func_start = content.find("    # ── 10.5")
if func_start == -1:
    print("未找到函数定义")
    exit(1)

# 找到函数结束位置（下一个函数定义或类方法）
func_end = content.find("\n    # ── 10.6", func_start)
if func_end == -1:
    func_end = content.find("\n    def ", func_start + 100)
if func_end == -1:
    print("未找到函数结束位置")
    exit(1)

# 新的函数实现（使用新浪市场总貌接口）
new_func = '''    # ── 10.5 新浪全市场涨跌停统计(后台任务) ──
    def fetch_full_market_stats_sina(self):
        """
        使用新浪市场总貌接口获取全市场涨跌停统计(约3秒)
        返回: {total, zt_count, dt_count, up_count, down_count, up_ratio, down_ratio}
        """
        import threading

        def _fetch():
            try:
                import requests
                import time
                import json
                import re

                headers = {
                    "Referer": "https://finance.sina.com.cn/",
                    "User-Agent": "Mozilla/5.0"
                }

                # 新浪市场总貌接口
                url = "http://vip.stock.finance.sina.com.cn/q/view/newMarketsDataAll.php"
                
                try:
                    r = requests.get(url, headers=headers, timeout=10)
                    text = r.text.strip()
                    
                    if text:
                        # 解析JSON数据（格式: jsonData(...)）
                        json_match = re.search(r'\\((.*)\\)', text)
                        if json_match:
                            market_data = json.loads(json_match.group(1))
                            
                            total = int(market_data.get('total', 0))
                            up_count = int(market_data.get('up', 0))
                            down_count = int(market_data.get('down', 0))
                            zt_count = int(market_data.get('zt', 0))
                            dt_count = int(market_data.get('dt', 0))
                            
                            if total > 0:
                                up_ratio = round(up_count / total * 100, 1)
                                down_ratio = round(down_count / total * 100, 1)
                                
                                self.full_market_stats = {
                                    'total': total,
                                    'zt_count': zt_count,
                                    'dt_count': dt_count,
                                    'up_count': up_count,
                                    'down_count': down_count,
                                    'up_ratio': up_ratio,
                                    'down_ratio': down_ratio,
                                    'updated': time.strftime('%Y-%m-%d %H:%M:%S')
                                }
                                print(f"📊 全市场统计更新: 涨停{zt_count} 跌停{dt_count} 上涨{up_ratio}% 下跌{down_ratio}%")
                                
                                # 保存缓存
                                import os
                                cache_file = os.path.join(CACHE_DIR, 'cache_daily', 'full_market_stats.json')
                                try:
                                    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                                    with open(cache_file, 'w', encoding='utf-8') as f:
                                        json.dump(self.full_market_stats, f, ensure_ascii=False)
                                except:
                                    pass
                            else:
                                print(f"⚠ 全市场统计获取失败: total=0")
                        else:
                            print(f"⚠ 全市场统计解析失败: 无法提取JSON")
                    else:
                        print(f"⚠ 全市场统计获取失败: 返回为空")
                except Exception as e:
                    print(f"⚠ 新浪市场总貌接口失败: {e}")
                    
                    # 备用方案: 使用东方财富涨跌停板接口
                    try:
                        print("   尝试备用方案: 东方财富涨跌停接口...")
                        zt_url = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23+f:8&fields=f12,f14,f2,f3"
                        dt_url = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23+f:4&fields=f12,f14,f2,f3"
                        
                        zt_count = 0
                        dt_count = 0
                        
                        r_zt = requests.get(zt_url, headers=headers, timeout=10)
                        data_zt = r_zt.json()
                        if data_zt.get('data') and data_zt['data'].get('total'):
                            zt_count = int(data_zt['data']['total'])
                        
                        r_dt = requests.get(dt_url, headers=headers, timeout=10)
                        data_dt = r_dt.json()
                        if data_dt.get('data') and data_dt['data'].get('total'):
                            dt_count = int(data_dt['data']['total'])
                        
                        if zt_count > 0 or dt_count > 0:
                            self.full_market_stats = {
                                'total': 0,
                                'zt_count': zt_count,
                                'dt_count': dt_count,
                                'up_count': 0,
                                'down_count': 0,
                                'up_ratio': 0,
                                'down_ratio': 0,
                                'updated': time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                            print(f"📊 全市场统计更新(备用): 涨停{zt_count} 跌停{dt_count}")
                    except Exception as e2:
                        print(f"⚠ 备用方案也失败: {e2}")
            except Exception as e:
                print(f"⚠ 全市场统计获取失败: {e}")

        # 在后台线程运行
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        return "后台获取中..."

'''

# 替换函数
content = content[:func_start] + new_func + content[func_end:]

# 同时修改函数调用（把 fetch_full_market_stats_eastmoney 改为 fetch_full_market_stats_sina）
content = content.replace('self.fetch_full_market_stats_eastmoney()', 'self.fetch_full_market_stats_sina()')

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ 函数修复完成")
print("   函数名: fetch_full_market_stats_sina")
print("   数据源: 新浪市场总貌接口（备用：东方财富涨跌停接口）")
