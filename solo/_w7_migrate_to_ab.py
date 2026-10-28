# -*- coding: utf-8 -*-
"""迁移：新口径三类信号记录从 w7_hvt 迁到 w7_ab（20261028）。

背景：W7 引擎改造后，类型A（T0_PULLBACK_HOLD）/ 类型B（T0_REBREAK）与旧口径
（BREAKOUT_RETEST / RIGHT_BOTTOM / MIDLINE_HOLD / SHAKEOUT_HOLD / SQUAT_SHRINK）
形态与阈值完全不同，混在同一个 strategy_id='w7_hvt' 下无法做「统一复查」的归因分析。

本脚本：
  1) 把 w7_hvt 下 signal ∈ {T0_PULLBACK_HOLD, T0_REBREAK} 的记录复制到 w7_ab；
  2) 同步复制对应的 pick_tracking 行（保留 base_close / ret_* / status / hit_date，
     保证与原口径完全一致的跟踪结果，不重算）；
  3) 从 w7_hvt 中删除这些已迁走的行；
  4) 注册 strategy 表里的 w7_ab 名称；
  5) 校验迁移前后条数、字段一致性。

幂等：已迁走的行不会再存在于 w7_hvt，重复执行不会产生重复记录。
只读校验 + 单一事务写入，失败整体回滚。
"""
import json
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picks_db", "stock_picks.db")
OLD_SID = "w7_hvt"
NEW_SID = "w7_ab"
NEW_NAME = "W7 二波·新口径两类信号(T0锚定)"
NEW_STATES = ("T0_PULLBACK_HOLD", "T0_REBREAK")

PICKS_COLS = ["pick_date", "strategy_id", "ts_code", "stock_name", "close", "pct_chg",
              "signal", "action", "score", "rank_no", "industry", "reason",
              "stop_price", "target_price", "indicators", "details"]
TRACK_COLS = ["pick_date", "strategy_id", "ts_code", "base_close", "last_date", "last_close",
              "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "max_high", "max_low",
              "max_gain", "max_drawdown", "status", "hit_date"]


def main():
    if not os.path.exists(DB):
        print(f"[migrate] 找不到数据库: {DB}")
        return 1
    con = sqlite3.connect(DB, timeout=30.0)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        q = ",".join("?" * len(NEW_STATES))
        rows = con.execute(
            f"SELECT {','.join(PICKS_COLS)} FROM stock_pick "
            f"WHERE strategy_id=? AND signal IN ({q})", (OLD_SID,) + NEW_STATES).fetchall()
        print(f"[migrate] 待迁移 stock_pick 行数: {len(rows)}")
        if not rows:
            print("[migrate] 无需迁移，退出")
            return 0

        moved_keys = []
        for r in rows:
            moved_keys.append((r[0], r[2]))
            print(f"  - {r[0]} {r[2]} {r[3]} {r[6]} close={r[4]} score={r[8]}")

        # 复制 pick_tracking
        tr_rows = []
        for pd_, code in moved_keys:
            tr = con.execute(
                f"SELECT {','.join(TRACK_COLS)} FROM pick_tracking "
                "WHERE pick_date=? AND strategy_id=? AND ts_code=?", (pd_, OLD_SID, code)).fetchone()
            if tr:
                tr_rows.append(tr)

        # 注册 strategy
        con.execute("""
            INSERT INTO strategy (strategy_id, strategy_name, category, horizon,
                                  description, enabled, updated_at)
            VALUES (?, ?, 'A', 'short', ?, 1, datetime('now','localtime'))
            ON CONFLICT(strategy_id) DO UPDATE SET
                strategy_name = excluded.strategy_name,
                category = excluded.category,
                description = excluded.description,
                updated_at = datetime('now','localtime')
        """, [NEW_SID, NEW_NAME,
              "20261028 新口径两类信号：类型A=缩量回踩至T0开盘价上方出现止跌信号；"
              "类型B=回踩后突破T0日收盘价。与旧口径 w7_hvt 隔离，供统一复查。"])

        with con:
            for r in rows:
                vals = list(r)
                vals[1] = NEW_SID
                con.execute(
                    f"INSERT INTO stock_pick ({','.join(PICKS_COLS)}) "
                    f"VALUES ({','.join('?' * len(PICKS_COLS))}) "
                    f"ON CONFLICT(pick_date, strategy_id, ts_code) DO UPDATE SET "
                    "stock_name=excluded.stock_name, close=excluded.close, pct_chg=excluded.pct_chg, "
                    "signal=excluded.signal, action=excluded.action, score=excluded.score, "
                    "rank_no=excluded.rank_no, industry=excluded.industry, reason=excluded.reason, "
                    "stop_price=excluded.stop_price, target_price=excluded.target_price, "
                    "indicators=excluded.indicators, details=excluded.details",
                    vals)
            for tr in tr_rows:
                vals = list(tr)
                vals[1] = NEW_SID
                con.execute(
                    f"INSERT INTO pick_tracking ({','.join(TRACK_COLS)}) "
                    f"VALUES ({','.join('?' * len(TRACK_COLS))}) "
                    f"ON CONFLICT(pick_date, strategy_id, ts_code) DO UPDATE SET "
                    "base_close=excluded.base_close, last_date=excluded.last_date, "
                    "last_close=excluded.last_close, ret_1d=excluded.ret_1d, ret_3d=excluded.ret_3d, "
                    "ret_5d=excluded.ret_5d, ret_10d=excluded.ret_10d, ret_20d=excluded.ret_20d, "
                    "max_high=excluded.max_high, max_low=excluded.max_low, "
                    "max_gain=excluded.max_gain, max_drawdown=excluded.max_drawdown, "
                    "status=excluded.status, hit_date=excluded.hit_date, "
                    "updated_at=datetime('now','localtime')",
                    vals)
            # 删除 w7_hvt 下已迁走的行
            for pd_, code in moved_keys:
                con.execute("DELETE FROM stock_pick WHERE pick_date=? AND strategy_id=? AND ts_code=?",
                            (pd_, OLD_SID, code))
                con.execute("DELETE FROM pick_tracking WHERE pick_date=? AND strategy_id=? AND ts_code=?",
                            (pd_, OLD_SID, code))

        # 校验
        a = con.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id=?", (NEW_SID,)).fetchone()[0]
        b = con.execute("SELECT COUNT(*) FROM pick_tracking WHERE strategy_id=?", (NEW_SID,)).fetchone()[0]
        c = con.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id=? AND signal IN (%s)"
                        % q, (OLD_SID,) + NEW_STATES).fetchone()[0]
        old_total = con.execute("SELECT COUNT(*) FROM stock_pick WHERE strategy_id=?", (OLD_SID,)).fetchone()[0]
        print(f"[migrate] 校验: w7_ab stock_pick={a} pick_tracking={b} / "
              f"w7_hvt 残留新口径={c} / w7_hvt 旧口径总数={old_total}")
        if a == len(rows) and b == len(tr_rows) and c == 0:
            print("[migrate] 迁移成功")
            return 0
        print("[migrate] 校验不一致，请检查")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
