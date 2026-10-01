# -*- coding: utf-8 -*-
"""W7 旧逻辑历史回放（0915 大改前的引擎 = commit 1acb6b6~1）。

目的：对 20260825~20260916 的每个交易日，用「旧逻辑 + as-of 输入」重算当日买点，
与库里现有 w7_hvt 记录对比，量化 0915/0917 回填带来的选样偏差。

安全措施：
1) 拦截 sync_downstream —— 不写 stock_pick_db、不写 w7_today_action_*.json；
2) 报告 md 只写 _w7_replay_out\\ 临时目录，绝不覆盖 report_daily 下的历史报告；
3) load_ige_adj 改严格 as-of（快照日期 <= 回放日），修掉旧实现「回退到最新快照」的前视。

性能改造（等价改写，不改结果）：
   缓存库 22.4GB、内存吃紧，原 load_all 每日把全市场 K 线扫一遍并整体 concat 算均线
   （峰值 4.5G 且单日约 17 分钟）。改为：
     a. 全区间只扫一遍 RAW（不含均线）；
     b. 每个回放日 load_all 只做「按 universe(d) 取码 + 截断到 d」；
     c. bars() 在被调用时才对该股截断序列算前复权均线（锚点=该日，与原口径一致）。
   因均线为组内尾部滚动、锚点取序列末行，逐股计算与整体处理结果完全相同。
   用 20260904 生产日志（results=236 / 各状态与类型分布）做等价性校验。
"""
import argparse
import csv
import importlib.util
import os
import re
import sys
import time

SOLO = r"d:\mystock\solo"
OLD_ENGINE = os.path.join(SOLO, "_w7_old_engine.py")
OUT_DIR = os.path.join(SOLO, "_w7_replay_out")
ACTION_CSV = os.path.join(SOLO, "_w7_replay_action.csv")
FULL_CSV = os.path.join(SOLO, "_w7_replay_full.csv")
STATS_CSV = os.path.join(SOLO, "_w7_replay_stats.csv")

try:
    import psutil
except ImportError:
    psutil = None


def rss_mb():
    return psutil.Process().memory_info().rss / 1024 / 1024 if psutil else -1.0


def load_old_engine():
    spec = importlib.util.spec_from_file_location("w7old", OLD_ENGINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w7old"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_raw(mod, reader, codes, end_date, chunk=300):
    """全区间只扫一遍：返回 {code: 原始K线(无均线)}。"""
    import pandas as pd

    frames, t0 = {}, time.time()
    code_list = [c for c in codes]
    nbatch = (len(code_list) + chunk - 1) // chunk
    for bi, start in enumerate(range(0, len(code_list), chunk), 1):
        batch = code_list[start:start + chunk]
        ph = ",".join(["?"] * len(batch))
        q = (mod._BARS_SELECT +
             f" WHERE d.trade_date>=? AND d.trade_date<=? AND d.ts_code IN ({ph})"
             " ORDER BY d.ts_code, d.trade_date")
        part = pd.read_sql_query(q, reader.conn, params=[mod.DATA_START, end_date] + batch)
        part = part.dropna(subset=["close", "high", "low", "vol"])
        for col in part.columns:
            if col not in ("ts_code", "trade_date") and not pd.api.types.is_numeric_dtype(part[col]):
                part[col] = pd.to_numeric(part[col], errors="coerce")
        for code, g in part.groupby("ts_code", sort=False):
            frames[code] = g.reset_index(drop=True)
        del part
        print("    [raw] 批次 %d/%d 代码=%d 行=%d RSS=%.0fMB 耗时=%.1fs"
              % (bi, nbatch, len(frames), sum(len(f) for f in frames.values()),
                 rss_mb(), time.time() - t0), flush=True)
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="")
    ap.add_argument("--single", default="")
    args = ap.parse_args()

    if args.single:
        dates = [args.single]
    elif args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        import sqlite3
        con = sqlite3.connect(os.path.join(r"D:\mystock\cache_daily", "stock_data.db"))
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
            ("20260825", "20260916"))]
        con.close()
    dates = sorted(dates)
    last = dates[-1]

    mod = load_old_engine()

    # ---- 一次性扫盘：取 17 个交易日 universe 的并集 ----
    r0 = mod.CacheReader()
    code_set = set()
    for d in dates:
        u = r0.universe(d)
        if not u.empty:
            code_set |= set(u["ts_code"].astype(str))
    print("[replay] 回放 %d 日，universe 并集 %d 只，一次性加载区间 %s~%s"
          % (len(dates), len(code_set), mod.DATA_START, last), flush=True)
    RAW = load_raw(mod, r0, sorted(code_set), last)
    r0.close()
    print("[replay] RAW 就绪 %d 只，RSS=%.0fMB" % (len(RAW), rss_mb()), flush=True)

    # ---- 替换 load_all：按 universe(d) 取码 + 截断到 d ----
    def load_all_patched(self, end_date, codes=None, min_date=None, chunk=500, verbose=False):
        out = {}
        for c in (codes or []):
            f = RAW.get(str(c))
            if f is None or f.empty:
                continue
            sub = f[f.trade_date <= end_date]
            if not sub.empty:
                out[str(c)] = sub.reset_index(drop=True)
        self.frames = out
        return len(out)

    mod.CacheReader.load_all = load_all_patched

    # ---- bars() 增加懒算均线（锚点=序列末行=回放日，与原口径一致）----
    def bars_patched(self, ts_code, end_date):
        import pandas as pd
        df = self.frames.get(ts_code)
        if df is None or df.empty:
            return pd.DataFrame()
        sub = df[df.trade_date <= end_date]
        if sub.empty:
            return pd.DataFrame()
        sub = sub.reset_index(drop=True)
        return mod._fill_ma_columns(sub)

    mod.CacheReader.bars = bars_patched

    mod.load_ige_adj = make_asof_ige(mod)

    os.makedirs(OUT_DIR, exist_ok=True)
    captured, stats_rows = {}, []
    orig_markdown = mod.markdown

    def md_wrap(results, date):
        captured[date] = list(results)
        return orig_markdown(results, date)

    mod.markdown = md_wrap
    mod.sync_downstream = lambda date, results, output: None   # 不落库、不写 JSON

    for d in dates:
        sys.argv = ["w7old", "--date", d,
                    "--output", os.path.join(OUT_DIR, "w7_second_wave_%s.md" % d)]
        t0 = time.time()
        print("\n" + "=" * 72, flush=True)
        print("[replay] %s 开始 RSS=%.0fMB" % (d, rss_mb()), flush=True)
        try:
            mod.main()
        except Exception as exc:
            print("[replay] %s 失败: %r" % (d, exc), flush=True)
            continue
        el = time.time() - t0
        res = captured.get(d, [])
        acts = [x for x in res if x["state"] in mod.ACTION_BUY_STATES]
        st, ty = {}, {}
        for x in res:
            st[x["state"]] = st.get(x["state"], 0) + 1
            ty[x["type"]] = ty.get(x["type"], 0) + 1
        stats_rows.append({"date": d, "results": len(res), "actionable": len(acts),
                           "sec": round(el, 1), "states": st, "types": ty})
        print("[replay] %s 完成 候选=%d 当日买点=%d 耗时=%.1fs RSS=%.0fMB"
              % (d, len(res), len(acts), el, rss_mb()), flush=True)

    full_rows, act_rows = [], []
    for d in dates:
        res = captured.get(d, [])
        acts = [x for x in res if x["state"] in mod.ACTION_BUY_STATES]
        for x in res:
            full_rows.append({
                "date": d, "ts_code": x["code"], "name": x["name"], "state": x["state"],
                "type": x["type"], "level": x["level"], "score": round(float(x["score"]), 2),
                "close": x["close"], "entry": x["entry"], "volr": x["volr"],
                "ige_adj": x.get("ige_adj"), "buy": x.get("buy"), "event_date": x.get("event_date"),
            })
        for i, x in enumerate(sorted(acts, key=lambda y: (-(y.get("ige_adj") or -1.0), -(y["score"] or 0))), 1):
            act_rows.append({
                "date": d, "rank": i, "ts_code": x["code"], "name": x["name"], "state": x["state"],
                "type": x["type"], "level": x["level"], "score": round(float(x["score"]), 2),
                "close": x["close"], "entry": x["entry"], "volr": x["volr"],
                "ige_adj": x.get("ige_adj"), "buy": x.get("buy"),
            })

    def dump(path, rows, header):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    dump(FULL_CSV, full_rows,
         ["date", "ts_code", "name", "state", "type", "level", "score", "close", "entry", "volr",
          "ige_adj", "buy", "event_date"])
    dump(ACTION_CSV, act_rows,
         ["date", "rank", "ts_code", "name", "state", "type", "level", "score", "close", "entry",
          "volr", "ige_adj", "buy"])
    with open(STATS_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "results", "actionable", "sec", "types", "states"])
        w.writeheader()
        for r in stats_rows:
            w.writerow({"date": r["date"], "results": r["results"], "actionable": r["actionable"],
                        "sec": r["sec"],
                        "types": " ".join("%s=%d" % kv for kv in sorted(r["types"].items())),
                        "states": " ".join("%s=%d" % kv for kv in sorted(r["states"].items()))})
    print("\n[replay] 全量候选 %d 行 -> %s" % (len(full_rows), FULL_CSV), flush=True)
    print("[replay] 当日买点 %d 行 -> %s" % (len(act_rows), ACTION_CSV), flush=True)
    print("[replay] 逐日统计 -> %s" % STATS_CSV, flush=True)


def make_asof_ige(mod):
    """严格 as-of 版 load_ige_adj：只取 快照日期 <= asof 的最近一次；无则视为无 IGE。"""
    import pandas as pd

    def _asof_ige(asof=""):
        d = mod.IGE_OUT_DIR
        if not os.path.isdir(d):
            return {}, ""
        files = sorted(f for f in os.listdir(d) if re.fullmatch(r"ige_full_\d{8}\.csv", f))
        if not files:
            return {}, ""
        cands = [f for f in files if (not asof) or f[9:17] <= asof]
        if not cands:
            print("    [ige] 无 <=%s 的快照 → 本次不注入 IGE" % asof, flush=True)
            return {}, ""
        chosen = cands[-1]
        snap = chosen[9:17]
        if asof and snap != asof:
            print("    [ige] as-of 回退: %s → %s" % (asof, snap), flush=True)
        df = pd.read_csv(os.path.join(d, chosen), encoding="utf-8-sig", dtype={"code": str})
        need = [c for c in ("code", "ige_adj", "ige_mix", "sw_l1", "sw_l3") if c in df.columns]
        if "code" not in need or "ige_adj" not in need:
            return {}, ""
        info = {}
        for rec in df[need].to_dict("records"):
            code = str(rec.get("code") or "").strip()
            if not code:
                continue
            info[code] = {
                "ige_adj": mod.finite(rec.get("ige_adj"), None),
                "ige_mix": mod.finite(rec.get("ige_mix"), None),
                "sw_l1": "" if pd.isna(rec.get("sw_l1")) else str(rec["sw_l1"]),
                "sw_l3": "" if pd.isna(rec.get("sw_l3")) else str(rec["sw_l3"]),
            }
        print("    [ige] as-of 快照=%s 覆盖=%d" % (snap, len(info)), flush=True)
        return info, snap

    return _asof_ige


if __name__ == "__main__":
    main()
