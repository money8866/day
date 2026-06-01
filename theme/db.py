import sqlite3
import os
from config import DB_PATH, PORTFOLIO_DB


# ───── 本地数据库（评分/轮动/龙头）─────

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_conn()
    try:
        # 检测旧版遗留表(有theme_code列的旧表) → 只清理一次
        old_cols = conn.execute("PRAGMA table_info(theme_scores)").fetchall()
        old_col_names = {c[1] for c in old_cols}
        if "theme_code" in old_col_names:
            conn.executescript("""
                DROP TABLE IF EXISTS theme_scores;
                DROP TABLE IF EXISTS theme_rotation;
                DROP TABLE IF EXISTS theme_leaders;
                DROP TABLE IF EXISTS themes;
                DROP TABLE IF EXISTS theme_cons;
            """)
            print("  已清理旧版表结构(theme_code→theme_name)")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS theme_scores (
                trade_date TEXT,
                theme_name TEXT,
                score REAL,
                avg_pct REAL,
                limit_ratio REAL,
                up_ratio REAL,
                amount REAL,
                leader_premium REAL,
                height_score REAL,
                PRIMARY KEY(trade_date, theme_name)
            );

            CREATE TABLE IF NOT EXISTS theme_rotation (
                trade_date TEXT,
                rank INTEGER,
                theme_name TEXT,
                score REAL
            );

            CREATE TABLE IF NOT EXISTS theme_leaders (
                trade_date TEXT,
                theme_name TEXT,
                leader TEXT,
                core TEXT,
                supplement TEXT,
                PRIMARY KEY(trade_date, theme_name)
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ───── 外部数据库（题材 + 成份股）─────

def get_portfolio_conn():
    return sqlite3.connect(PORTFOLIO_DB)

def load_all_themes():
    """从 theme_portfolio.db 读取全部题材列表"""
    conn = get_portfolio_conn()
    try:
        rows = conn.execute("SELECT theme_name, industry, keywords FROM themes").fetchall()
        return rows  # [(theme_name, industry, keywords), ...]
    finally:
        conn.close()

def get_theme_stock_codes(theme_name):
    """从 theme_portfolio.db 读取某个题材的所有成份股代码"""
    conn = get_portfolio_conn()
    try:
        rows = conn.execute(
            "SELECT ts_code FROM portfolio WHERE theme_name=?", (theme_name,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()

def get_theme_stocks_full(theme_name):
    """读取成份股完整信息"""
    conn = get_portfolio_conn()
    try:
        rows = conn.execute(
            "SELECT ts_code, name, layer, mcap, amount FROM portfolio WHERE theme_name=?",
            (theme_name,)
        ).fetchall()
        return rows
    finally:
        conn.close()

def get_all_stock_codes():
    """读取所有成份股代码（去重），用于批量预加载日线"""
    conn = get_portfolio_conn()
    try:
        rows = conn.execute("SELECT DISTINCT ts_code FROM portfolio").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ───── theme_scores ─────

def save_theme_score(trade_date, theme_name, score, avg_pct, limit_ratio, up_ratio, amount, leader_premium, height_score):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO theme_scores
               (trade_date, theme_name, score, avg_pct, limit_ratio, up_ratio, amount, leader_premium, height_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_date, theme_name, score, avg_pct, limit_ratio, up_ratio, amount, leader_premium, height_score)
        )
        conn.commit()
    finally:
        conn.close()

def load_theme_scores(trade_date):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT theme_name, score, avg_pct, limit_ratio, up_ratio, amount, leader_premium, height_score "
            "FROM theme_scores WHERE trade_date=? ORDER BY score DESC", (trade_date,)
        ).fetchall()
        return rows
    finally:
        conn.close()

def load_recent_scores(theme_name, lookback=10):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT trade_date, score FROM theme_scores WHERE theme_name=? ORDER BY trade_date DESC LIMIT ?",
            (theme_name, lookback)
        ).fetchall()
        return rows
    finally:
        conn.close()


# ───── theme_rotation ─────

def save_rotation(trade_date, ranks):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM theme_rotation WHERE trade_date=?", (trade_date,))
        conn.executemany(
            "INSERT INTO theme_rotation (trade_date, rank, theme_name, score) VALUES (?, ?, ?, ?)",
            [(trade_date, r["rank"], r["theme_name"], r["score"]) for r in ranks]
        )
        conn.commit()
    finally:
        conn.close()

def load_rotation_history():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT trade_date, rank, theme_name, score FROM theme_rotation ORDER BY trade_date"
        ).fetchall()
        return rows
    finally:
        conn.close()


# ───── theme_leaders ─────

def save_leader(trade_date, theme_name, leader, core, supplement):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO theme_leaders (trade_date, theme_name, leader, core, supplement) VALUES (?, ?, ?, ?, ?)",
            (trade_date, theme_name, leader, core, supplement)
        )
        conn.commit()
    finally:
        conn.close()


# ───── 写入TOP10板块及个股到theme_portfolio.db ─────

def save_top10_to_portfolio_db(trade_date, scored_themes, stages, leaders):
    """将TOP10板块和个股写入theme_portfolio.db"""
    conn = sqlite3.connect(PORTFOLIO_DB)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_top10 (
                trade_date TEXT,
                rank INTEGER,
                theme_name TEXT,
                stage TEXT,
                score REAL,
                leader_code TEXT,
                leader_name TEXT,
                core_code TEXT,
                core_name TEXT,
                supp_codes TEXT,
                supp_names TEXT,
                PRIMARY KEY(trade_date, rank)
            );
        """)

        # 删除当日旧数据
        conn.execute("DELETE FROM daily_top10 WHERE trade_date=?", (trade_date,))

        # 构建 leaders 查询map
        leader_map = {l["theme_name"]: l for l in leaders}
        stage_map = {s["theme_name"]: s["stage"] for s in stages}

        top10 = sorted(scored_themes, key=lambda x: x["score"], reverse=True)[:10]
        rows = []
        for rank, item in enumerate(top10, 1):
            theme_name = item["theme_name"]
            ldr = leader_map.get(theme_name, {})
            stage = stage_map.get(theme_name, "震荡")
            supp_codes_str = ",".join(ldr.get("supp_codes", []))
            rows.append((
                trade_date, rank, theme_name, stage, round(item["score"], 2),
                ldr.get("leader_code", ""), ldr.get("leader", ""),
                ldr.get("core_code", ""), ldr.get("core", ""),
                supp_codes_str, ldr.get("supplement", "")
            ))

        conn.executemany(
            "INSERT INTO daily_top10 (trade_date, rank, theme_name, stage, score, leader_code, leader_name, core_code, core_name, supp_codes, supp_names) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        conn.commit()
        print(f"  ✓ TOP10已写入{os.path.basename(PORTFOLIO_DB)}.daily_top10")
    except Exception as e:
        print(f"  ✗ 写入daily_top10失败: {e}")
    finally:
        conn.close()

def load_leaders(trade_date):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT theme_name, leader, core, supplement FROM theme_leaders WHERE trade_date=?",
            (trade_date,)
        ).fetchall()
        return rows
    finally:
        conn.close()
