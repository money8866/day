# -*- coding: utf-8 -*-
"""运行完整量化分析系统的辅助脚本"""
import subprocess
import sys
import os

def run_script(script_name):
    """运行单个Python脚本"""
    print(f"\n{'='*80}")
    print(f"正在运行: {script_name}")
    print('='*80)
    result = subprocess.run([sys.executable, script_name], 
                          capture_output=True, 
                          text=True,
                          cwd=os.path.dirname(os.path.abspath(__file__)))
    if result.returncode != 0:
        print(f"错误: {script_name} 执行失败")
        print(result.stderr)
        return False
    else:
        print(result.stdout)
        return True

def main():
    scripts = [
        'theme_trend_sentiment_score.py',
        'market_analysis.py',
        'theme_pattern_stock_picker.py',
        'etf_quant_theme.py',
        'tushare_quant.py',
        'daily_analysis_summarizer.py'
    ]
    
    for i, script in enumerate(scripts, 1):
        print(f"\n[{i}/6] 正在运行 {script}...")
        if not run_script(script):
            print(f"\n流程中断: {script} 执行失败")
            sys.exit(1)
    
    print("\n" + "="*80)
    print("全流程完成!")
    print("="*80)

if __name__ == "__main__":
    main()
