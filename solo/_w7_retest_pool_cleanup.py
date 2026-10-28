# -*- coding: utf-8 -*-
"""整理 w7_retest 相关 strategy_id：
  1) 删掉 v1 回测池 w7_retest_bt（无 age 下限，已证伪）
  2) v2 回测池 w7_retest_v2 → 重命名 w7_retest_backtest（保留作验证记录）
  3) 实盘池 w7_retest 清空（旧的 6 条是无 age 门槛版本）
  4) strategy 表同步：回测池 enabled=0（不进网页展示）
"""
import sqlite3

DB = r"D:\mystock\solo\picks_db\stock_picks.db"
con = sqlite3.connect(DB)
cur = con.cursor()

def counts():
    for sid in ("w7_retest", "w7_retest_bt", "w7_retest_v2", "w7_retest_backtest"):
        n = cur.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id=?", (sid,)).fetchone()[0]
        m = cur.execute("SELECT COUNT(*) FROM pick_tracking WHERE strategy_id=?", (sid,)).fetchone()[0]
        print(f"  {sid:24s} stock_pick={n:4d}  pick_tracking={m:4d}")

print("=== 整理前 ===")
counts()

# 1) 删 v1 回测池
n1 = cur.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id='w7_retest_bt'").fetchone()[0]
cur.execute("DELETE FROM pick_tracking WHERE strategy_id='w7_retest_bt'")
cur.execute("DELETE FROM stock_pick WHERE strategy_id='w7_retest_bt'")
cur.execute("DELETE FROM strategy WHERE strategy_id='w7_retest_bt'")
print(f"\n[1] 删除 w7_retest_bt: {n1} 条")

# 2) v2 回测池改名
n2 = cur.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id='w7_retest_v2'").fetchone()[0]
cur.execute("UPDATE stock_pick SET strategy_id='w7_retest_backtest' WHERE strategy_id='w7_retest_v2'")
cur.execute("UPDATE pick_tracking SET strategy_id='w7_retest_backtest' WHERE strategy_id='w7_retest_v2'")
cur.execute("UPDATE strategy SET strategy_id='w7_retest_backtest', enabled=0, "
            "strategy_name='W7 逆势缩量回踩低吸·前瞻回测样本(age>=8)' "
            "WHERE strategy_id='w7_retest_v2'")
print(f"[2] w7_retest_v2 → w7_retest_backtest: {n2} 条 (enabled=0，不进网页)")

# 3) 清空实盘池
n3 = cur.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id='w7_retest'").fetchone()[0]
cur.execute("DELETE FROM pick_tracking WHERE strategy_id='w7_retest'")
cur.execute("DELETE FROM stock_pick WHERE strategy_id='w7_retest'")
print(f"[3] 清空 w7_retest 实盘池: {n3} 条 (将由 v2 规则重跑最近交易日)")

con.commit()
print("\n=== 整理后 ===")
counts()
con.close()
print("\n完成")
