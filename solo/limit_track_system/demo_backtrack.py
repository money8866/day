# -*- coding: utf-8 -*-
"""
历史回溯功能演示脚本
"""

import sys
import os
import sqlite3

# 使用相对路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limit_track_review import (
    init_sqlite_db,
    backtrack_history,
    query_history
)

print("="*80)
print("🚀 涨停跟踪系统 - 历史回溯功能演示")
print("="*80)
print()

# 1. 初始化数据库
print("【步骤1】初始化数据库...")
init_sqlite_db()
print()

# 2. 检查数据
print("【步骤2】检查现有数据...")
conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "limit_history.db"))
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM limit_stocks")
count = cursor.fetchone()[0]
print(f"  当前数据: {count} 条记录")

if count > 0:
    print("  ✓ 数据库已有数据")
    
    # 显示每日统计
    cursor.execute("""
        SELECT trade_date, COUNT(*) 
        FROM limit_stocks 
        GROUP BY trade_date 
        ORDER BY trade_date DESC
    """)
    
    print("\n  每日涨停数量:")
    for row in cursor.fetchall():
        print(f"    {row[0]}: {row[1]} 只")
    
    conn.close()
    
    print("\n  下一步建议:")
    print("    1. 查询高概率股票:")
    print("       python limit_track_review.py --query --min-prob 60")
    print()
    print("    2. 导出数据:")
    print("       python limit_track_review.py --query --export data.csv")
    print()
    print("    3. 验证数据库:")
    print("       python verify_db.py")
    print()
    
else:
    print("  ⚠️ 数据库为空，需要回溯数据")
    conn.close()
    
    print("\n【步骤3】开始回溯数据...")
    print("  (将回溯最近5个交易日作为演示)")
    print()
    
    backtrack_history(days=5, force_refresh=False)
    
    print("\n【步骤4】验证回溯结果...")
    verify_db()

print()
print("="*80)
print("✅ 演示完成！")
print("="*80)
print()
print("📚 详细使用说明:")
print("   完整指南: LIMIT_TRACK_HISTORY_BACKTRACK_GUIDE.md")
print("   快速参考: LIMIT_TRACK_HISTORY_QUICKREF.txt")
print()
print("🚀 下一步操作:")
print("   1. 运行完整回溯: python full_backtrack.py")
print("   2. 查询数据: python limit_track_review.py --query")
print("   3. 导出CSV: python limit_track_review.py --query --export data.csv")
print()
