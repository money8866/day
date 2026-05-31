# -*- coding: utf-8 -*-
"""
涨停二波交易系统 - 所有版本统一回测对比
"""

import os
import sys
import subprocess
import pandas as pd
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

versions = {
    'V1': {
        'name': '原始版',
        'desc': '止盈5%，止损3%，调整3-20天',
        'file': 'wave2_trade_system.py'
    },
    'V3': {
        'name': '中间版',
        'desc': '止盈6%，止损2.5%，平衡路线',
        'file': 'wave2_trade_system_v3.py'
    },
    'V5': {
        'name': '大盘+板块版',
        'desc': 'V1基础上+大盘情绪+主线板块',
        'file': 'wave2_trade_system_v5.py'
    }
}


def run_backtest(version_id, version_info, start_date, end_date):
    print(f"\n{'='*70}")
    print(f"📈 运行 {version_id} ({version_info['name']}) 回测...")
    print(f"{'='*70}")
    
    file_path = os.path.join(BASE_DIR, version_info['file'])
    
    try:
        cmd = [
            sys.executable, file_path,
            'backtest',
            '--start', start_date,
            '--end', end_date
        ]
        
        result = subprocess.run(
            cmd,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        
        output = result.stdout
        print(output)
        
        # 解析回测结果
        total_trades = None
        win_rate = None
        total_return = None
        avg_return = None
        
        for line in output.split('\n'):
            if '总交易' in line and '笔' in line:
                import re
                m = re.search(r'(\d+)笔', line)
                if m:
                    total_trades = int(m.group(1))
            elif '胜率' in line and '%' in line:
                import re
                m = re.search(r'(\d+\.?\d*)%', line)
                if m:
                    win_rate = float(m.group(1))
            elif '总收益率' in line and '%' in line:
                import re
                m = re.search(r'([-+]?\d+\.?\d*)%', line)
                if m:
                    total_return = float(m.group(1))
            elif '平均收益' in line and '%' in line:
                import re
                m = re.search(r'([-+]?\d+\.?\d*)%', line)
                if m:
                    avg_return = float(m.group(1))
        
        return {
            'version': version_id,
            'name': version_info['name'],
            'desc': version_info['desc'],
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_return': total_return,
            'avg_return': avg_return,
            'success': True
        }
    
    except Exception as e:
        print(f"❌ {version_id} 回测失败: {e}")
        return {
            'version': version_id,
            'name': version_info['name'],
            'desc': version_info['desc'],
            'total_trades': None,
            'win_rate': None,
            'total_return': None,
            'avg_return': None,
            'success': False,
            'error': str(e)
        }


def main():
    print("\n" + "="*80)
    print("📊 涨停二波交易系统 - 全版本回测对比")
    print("="*80)
    
    # 设置回测日期（两个月）
    end_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
    
    print(f"\n回测周期: {start_date} ~ {end_date}")
    
    results = []
    
    for version_id, version_info in versions.items():
        result = run_backtest(version_id, version_info, start_date, end_date)
        results.append(result)
    
    # 打印汇总对比
    print("\n" + "="*80)
    print("📋 回测结果汇总对比")
    print("="*80)
    
    summary_data = []
    
    for r in results:
        if r['success']:
            summary_data.append({
                '版本': r['version'],
                '名称': r['name'],
                '总交易': r['total_trades'],
                '胜率(%)': r['win_rate'],
                '总收益率(%)': r['total_return'],
                '平均单笔(%)': r['avg_return']
            })
    
    if summary_data:
        df = pd.DataFrame(summary_data)
        print(df.to_string(index=False))
        
        # 找出最优版本
        best_by_return = max(summary_data, key=lambda x: x['总收益率(%)'])
        best_by_win_rate = max(summary_data, key=lambda x: x['胜率(%)'])
        
        print(f"\n🏆 按总收益率最优: {best_by_return['版本']} ({best_by_return['名称']}) - {best_by_return['总收益率(%)']:.2f}%")
        print(f"🎯 按胜率最优: {best_by_win_rate['版本']} ({best_by_win_rate['名称']}) - {best_by_win_rate['胜率(%)']:.1f}%")
    
    print("\n" + "="*80)
    print("📁 文件位置")
    print("="*80)
    
    for version_id, version_info in versions.items():
        file_path = os.path.join(BASE_DIR, version_info['file'])
        print(f"  {version_id} ({version_info['name']}): {file_path}")


if __name__ == "__main__":
    main()
