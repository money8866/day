"""
回溯过去N个交易日分析
用法: python backtrack_days.py [天数]
默认回溯5个交易日
"""
import sys
import os
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv('d:/mystock/config/.env')
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

def get_recent_trade_dates(n=5):
    """获取过去N个交易日（从今天往前推）"""
    today = datetime.now()
    dates = []
    current = today
    
    while len(dates) < n:
        current = current - timedelta(days=1)
        # 跳过周末（周六=5, 周日=6）
        if current.weekday() >= 5:
            continue
        dates.append(current.strftime('%Y%m%d'))
    
    # 按时间升序排列
    dates.reverse()
    return dates

def main():
    # 获取回溯天数
    days = 5
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except:
            pass
    
    print(f"\n{'='*60}")
    print(f"  回溯过去 {days} 个交易日分析")
    print(f"{'='*60}\n")
    
    # 获取交易日列表
    trade_dates = get_recent_trade_dates(days)
    print(f"回溯日期: {trade_dates}\n")
    
    # 导入主程序
    import tushare_quant as tq
    
    # 逐日运行
    for d in trade_dates:
        print(f"\n{'='*60}")
        print(f"  开始处理: {d}")
        print(f"{'='*60}\n")
        try:
            tq.run(target_date=d)
        except Exception as e:
            print(f"[错误] {d} 处理失败: {e}")
    
    print(f"\n{'='*60}")
    print(f"  回溯完成，共处理 {len(trade_dates)} 个交易日")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()
