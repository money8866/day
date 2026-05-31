# -*- coding: utf-8 -*-
"""
完整历史回溯脚本 - 回溯过去20个交易日的涨停数据
"""

import sys
import os

# 添加项目路径（使用相对路径）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limit_track_review import backtrack_history

if __name__ == "__main__":
    print("="*80)
    print("🚀 开始完整历史回溯")
    print("="*80)
    print()
    
    # 回溯过去20个交易日
    backtrack_history(days=20, force_refresh=False)
    
    print()
    print("="*80)
    print("✅ 完整历史回溯完成！")
    print("="*80)
    print()
    print("📊 数据已保存到 SQLite 数据库")
    print("📍 数据库位置: cache/limit_history.db")
    print()
    print("💡 后续使用:")
    print("   查询数据: python limit_track_review.py --query")
    print("   导出CSV: python limit_track_review.py --query --export data.csv")
    print("   指定日期: python limit_track_review.py --query --start-date 20260501 --end-date 20260529")
    print()
