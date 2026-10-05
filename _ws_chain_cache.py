# -*- coding: utf-8 -*-
"""
westock 主题/成份链路本地缓存工具

把 westock CLI 的「产业链主题 -> 节点 -> 成份股」与「概念板块 -> 成份股」
两套链路下载到本地 SQLite + CSV，支持增量更新。

用法:
  python _ws_chain_cache.py --mode chain             # 缓存全部产业链主题图谱
  python _ws_chain_cache.py --mode chain --limit 5   # 先试跑 5 个主题
  python _ws_chain_cache.py --mode concept           # 缓存全市场概念板块成份股
  python _ws_chain_cache.py --mode all --workers 6
  python _ws_chain_cache.py --mode chain --force     # 忽略已缓存，重抓
  python _ws_chain_cache.py --date 2026-10-01        # 抓历史快照

产物目录: D:/mystock/data/westock_cache/
  westock.db                 SQLite 主库
  csv/topics.csv             产业链主题清单
  csv/chain_stock_node.csv   主题-节点-个股关系展开表
  csv/concept_sectors.csv    概念板块清单
  csv/concept_stocks.csv     概念板块-成份股
  raw/chain/*.md             每条链路的原始 Markdown（审计/重解析用）
"""
import argparse
import csv
import os
import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

OUT_DIR = os.path.join("D:/mystock", "data", "westock_cache")
RAW_DIR = os.path.join(OUT_DIR, "raw", "chain")
CSV_DIR = os.path.join(OUT_DIR, "csv")
DB_PATH = os.path.join(OUT_DIR, "westock.db")

WS = "westock"
ROW_RE = re.compile(r"^\s*\|")


# ---------- 基础执行 ----------
def run(args, retries=2, timeout=90):
    """调用 westock CLI，返回 stdout 文本。"""
    for attempt in range(retries + 1):
        try:
            p = subprocess.run([WS] + args, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if attempt == retries:
                return None, "timeout"
            time.sleep(1.5)
            continue
        out = p.stdout.decode("utf-8", errors="replace")
        err = p.stderr.decode("utf-8", errors="replace")
        if p.returncode == 0 and out.strip():
            return out, None
        msg = (err or out or "").strip()[:200]
        if attempt == retries:
            return None, msg or f"exit={p.returncode}"
        time.sleep(1.0 + attempt)
    return None, "unknown"


def md_rows(text):
    """把 Markdown 表格解析成 (表头, 行列表)。"""
    header, rows = None, []
    for line in (text or "").splitlines():
        if not ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        if header is None:
            header = cells
        else:
            rows.append(cells)
    return header or [], rows


# ---------- 数据库 ----------
def init_db(conn):
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS chain_topics (
            theme_code TEXT PRIMARY KEY, theme_name TEXT, snapshot TEXT, rows INTEGER
        );
        CREATE TABLE IF NOT EXISTS chain_stock_node (
            theme_code TEXT, theme_name TEXT, stock_code TEXT, stock_name TEXT,
            node TEXT, positions TEXT, is_theme_node INTEGER, snapshot TEXT
        );
        CREATE TABLE IF NOT EXISTS concept_sectors (
            code TEXT, name TEXT, change_pct TEXT, turnover TEXT, turnover_rate TEXT,
            up_count TEXT, leader TEXT, snapshot TEXT
        );
        CREATE TABLE IF NOT EXISTS concept_stocks (
            sector_code TEXT, sector_name TEXT, stock_code TEXT, stock_name TEXT, snapshot TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_csn_stock ON chain_stock_node(stock_code);
        CREATE INDEX IF NOT EXISTS idx_csn_theme ON chain_stock_node(theme_code);
        CREATE INDEX IF NOT EXISTS idx_cs_sector ON concept_stocks(sector_code);
        CREATE INDEX IF NOT EXISTS idx_cs_stock ON concept_stocks(stock_code);
        """
    )
    conn.commit()


def set_meta(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, v))
    conn.commit()


# ---------- 产业链 ----------
def fetch_topic_list(date=None):
    args = ["industry-chain", "list"]
    if date:
        args += ["--date", date]
    out, err = run(args)
    if out is None:
        raise SystemExit(f"[错误] industry-chain list 失败: {err}")
    _, rows = md_rows(out)
    topics = []
    for cells in rows:
        if len(cells) >= 2 and cells[0] and cells[1]:
            topics.append({"name": cells[0], "code": cells[1]})
    return topics


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name)[:80]


def parse_graph(theme, text):
    """解析产业链图谱：返回 [(stock_code, stock_name, node, positions, is_theme_node)]"""
    header, rows = md_rows(text)
    if not header:
        return []
    try:
        i_code, i_name = header.index("股票代码"), header.index("股票名称")
        i_node, i_pos = header.index("所属节点/主题"), header.index("产业链位置")
    except ValueError:
        return []
    recs, cur = [], None
    for cells in rows:
        def g(i):
            return cells[i] if i < len(cells) else ""
        if g(i_code):
            cur = (g(i_code), g(i_name))
            node, pos = g(i_node), g(i_pos)
        elif cur is None:
            continue
        else:
            node, pos = g(i_node), g(i_pos)
        if not node:
            continue
        recs.append((cur[0], cur[1], node, pos, 1 if "主题" in node else 0))
    return recs


def task_chain(topic, date, conn, snap, force, stats):
    tname, tcode = topic["name"], topic["code"]
    raw_path = os.path.join(RAW_DIR, f"{safe_name(tname)}.md")
    if not force and os.path.exists(raw_path):
        with open(raw_path, "r", encoding="utf-8") as f:
            text = f.read()
        return tname, "cached", len(parse_graph(tname, text)), text

    args = ["industry-chain", "graph", tname]
    if date:
        args += ["--date", date]
    out, err = run(args)
    if out is None:
        return tname, f"failed:{err}", 0, None
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(out)
    recs = parse_graph(tname, out)
    return tname, "ok", len(recs), out


def build_chain(conn, date, limit, workers, force):
    snap = date or datetime.now().strftime("%Y-%m-%d")
    topics = fetch_topic_list(date)
    if limit:
        topics = topics[:limit]
    print(f"[产业链] 待抓主题 {len(topics)} 个 (workers={workers}, snapshot={snap})")

    done = {n for (n,) in conn.execute(
        "SELECT DISTINCT theme_name FROM chain_stock_node WHERE snapshot=?", (snap,))}
    todo = [t for t in topics if force or t["name"] not in done]
    print(f"[产业链] 需抓取 {len(todo)} 个，命中当日缓存 {len(topics) - len(todo)} 个")

    os.makedirs(RAW_DIR, exist_ok=True)
    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(task_chain, t, date, conn, snap, force, None): t for t in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            tname, status, n, text = fut.result()
            if status in ("ok", "cached") and text:
                code = next(t["code"] for t in topics if t["name"] == tname)
                conn.execute(
                    "INSERT OR REPLACE INTO chain_topics VALUES(?,?,?,?)", (code, tname, snap, n))
                recs = parse_graph(tname, text)
                conn.executemany(
                    "INSERT INTO chain_stock_node VALUES(?,?,?,?,?,?,?,?)",
                    [(code, tname, r[0], r[1], r[2], r[3], r[4], snap) for r in recs])
                conn.commit()
                ok += 1
            else:
                fail += 1
            if i % 10 == 0 or i == len(todo):
                print(f"  进度 {i}/{len(todo)}  成功{ok} 失败{fail}  用时{time.time()-t0:.0f}s")
    total = conn.execute("SELECT COUNT(*) FROM chain_stock_node WHERE snapshot=?", (snap,)).fetchone()[0]
    print(f"[产业链] 完成：主题 {ok} 个，主题-节点-个股关系 {total} 条，失败 {fail}")


# ---------- 概念板块成份 ----------
def build_concept(conn, date, limit, workers, force):
    snap = date or datetime.now().strftime("%Y-%m-%d")
    args = ["sector", "ranking", "--kind", "concept"]
    if date:
        args += ["--date", date]
    out, err = run(args)
    if out is None:
        raise SystemExit(f"[错误] sector ranking 失败: {err}")
    header, rows = md_rows(out)
    idx = {h: i for i, h in enumerate(header)}
    sectors = []
    for cells in rows:
        def g(k):
            i = idx.get(k)
            return cells[i] if i is not None and i < len(cells) else ""
        if g("code") and g("name"):
            sectors.append((g("code"), g("name"), g("changePct"), g("turnover"),
                            g("turnoverRate"), g("upCount"), g("leader")))
    if limit:
        sectors = sectors[:limit]
    print(f"[概念板块] 板块 {len(sectors)} 个")

    done = {c for (c,) in conn.execute(
        "SELECT DISTINCT sector_code FROM concept_stocks WHERE snapshot=?", (snap,))}
    todo = [s for s in sectors if force or s[0] not in done]
    print(f"[概念板块] 需抓取 {len(todo)} 个，命中当日缓存 {len(sectors) - len(todo)} 个")

    conn.executemany(
        "INSERT INTO concept_sectors SELECT ?,?,?,?,?,?,?,? WHERE NOT EXISTS"
        "(SELECT 1 FROM concept_sectors WHERE code=? AND snapshot=?)",
        [(*s, snap, s[0], snap) for s in sectors])
    conn.commit()

    def task_one(sector):
        code, name = sector[0], sector[1]
        o, e = run(["sector", "constituent", code] + (["--date", date] if date else []))
        if o is None:
            return code, name, None, e
        _, r = md_rows(o)
        return code, name, [(code, name, c[0], c[1] if len(c) > 1 else "", snap)
                            for c in r if c and c[0]], None

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(task_one, s) for s in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            code, name, recs, e = fut.result()
            if recs is None:
                fail += 1
                print(f"  失败 {name}({code}): {e}")
                continue
            conn.executemany("INSERT INTO concept_stocks VALUES(?,?,?,?,?)", recs)
            ok += 1
            if i % 20 == 0 or i == len(todo):
                conn.commit()
                print(f"  进度 {i}/{len(todo)}  成功{ok} 失败{fail}  用时{time.time()-t0:.0f}s")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM concept_stocks WHERE snapshot=?", (snap,)).fetchone()[0]
    print(f"[概念板块] 完成：板块 {ok} 个，板块-成份股关系 {total} 条，失败 {fail}")


# ---------- 导出 CSV ----------
def export(conn, snap):
    os.makedirs(CSV_DIR, exist_ok=True)
    jobs = {
        "topics.csv": "SELECT theme_code, theme_name, snapshot, rows FROM chain_topics ORDER BY theme_name",
        "chain_stock_node.csv": ("SELECT theme_code, theme_name, stock_code, stock_name, node, "
                                 "positions, is_theme_node, snapshot FROM chain_stock_node "
                                 "ORDER BY theme_name, stock_code"),
        "concept_sectors.csv": "SELECT * FROM concept_sectors ORDER BY name",
        "concept_stocks.csv": "SELECT * FROM concept_stocks ORDER BY sector_code, stock_code",
    }
    for fn, sql in jobs.items():
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        with open(os.path.join(CSV_DIR, fn), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(cur)
        n = conn.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
        print(f"[导出] {fn}: {n} 行")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["chain", "concept", "all"], default="chain")
    ap.add_argument("--date", default=None, help="历史快照日期 YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 个主题/板块（试跑用）")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="忽略本地缓存重新抓取")
    ap.add_argument("--no-export", action="store_true")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    set_meta(conn, "last_run", datetime.now().isoformat(timespec="seconds"))
    snap = a.date or datetime.now().strftime("%Y-%m-%d")

    try:
        if a.mode in ("chain", "all"):
            build_chain(conn, a.date, a.limit, a.workers, a.force)
        if a.mode in ("concept", "all"):
            build_concept(conn, a.date, a.limit, a.workers, a.force)
    finally:
        if not a.no_export:
            export(conn, snap)
        n1 = conn.execute("SELECT COUNT(*) FROM chain_stock_node").fetchone()[0]
        n2 = conn.execute("SELECT COUNT(*) FROM concept_stocks").fetchone()[0]
        print(f"\n数据库：{DB_PATH}\n  链路关系 {n1} 条 / 概念成份 {n2} 条")
        conn.close()


if __name__ == "__main__":
    main()
