# -*- coding: utf-8 -*-
"""每日新闻 AI 总结（早 8:00 推送「前一日」资讯）

数据源：服务器上 news_web 的落盘归档 /var/www/StockPick/news/data/news.db
    news_item    —— 四个资讯源（财联社 / 东财7×24 / 华尔街见闻 / 央视新闻联播）
    hot_snapshot —— 同花顺热榜（日榜 ths_day，含题材归类）

流程：ssh 导出前一日数据 → 拼 prompt → DeepSeek 总结 → 22px HTML → Agent Mail 推送

用法：
    python daily_news_digest.py                    # 总结「昨天」，发送邮件
    python daily_news_digest.py --date 2026-10-02  # 指定日期
    python daily_news_digest.py --dry              # 只生成不发送（HTML 留在本地供预览）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import requests

try:                                              # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 服务器归档库 ────────────────────────────────────────────────────────
SSH_KEY = r"C:\Users\kongx\.ssh\id_rsa"
SSH_HOST = "root@115.159.221.46"
REMOTE_DB = "/var/www/StockPick/news/data/news.db"

# ── 模型 ───────────────────────────────────────────────────────────────
ENV_FILE = "d:/mystock/config/.env"
DS_URL = "https://api.deepseek.com/chat/completions"
DS_MODEL = "deepseek-v4-flash"

# ── 邮件 ───────────────────────────────────────────────────────────────
MAIL_CLI = r"C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd"
MAIL_TO = "stock1975@qq.com"

OUT_DIR = os.path.join(BASE_DIR, "news_digest_out")
MAX_ITEMS = 800          # 单次喂给模型的最大资讯条数（超出按时间均匀抽稀）
BODY_CUT = 70            # 每条正文截断字数

SOURCE_CN = {"cls": "财联社", "em": "东财7×24", "wscn": "华尔街见闻", "cctv": "新闻联播"}

SYS_PROMPT = (
    "你是A股资深策略分析师，负责把一天的财经资讯压缩成一份可执行的投研早报。"
    "严格基于用户提供的资讯内容进行分析，绝不编造任何未出现的新闻、数据、公司、事件或"
    "游资动向。提到的股票、公司、板块必须来自用户提供的资讯或热榜名单，名称与代码严格"
    "照抄，不得改写或臆造。信息不足时直接写「信息不足」，不要脑补。输出中文 Markdown。"
)


# ══════════════════════════════════════════════════════════════════════
# 取数（ssh + sqlite3 -json）
# ══════════════════════════════════════════════════════════════════════
def remote_json(sql: str) -> list[dict]:
    """把 SQL 用 base64 送到服务器执行，返回 sqlite3 -json 的结果。"""
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    remote = "echo %s | base64 -d | sqlite3 -json %s" % (b64, REMOTE_DB)
    try:
        r = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=15", SSH_HOST, remote],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=180)
    except Exception as e:
        print("[!] ssh 调用失败: %s" % e)
        return []
    if r.returncode != 0:
        print("[!] 远端执行失败: %s" % (r.stderr or "").strip()[:300])
        return []
    out = (r.stdout or "").strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except Exception as e:
        print("[!] 解析 JSON 失败: %s / %s" % (e, out[:200]))
        return []


def fetch_news(date: str) -> list[dict]:
    """某自然日的全部资讯，按时间升序。"""
    return remote_json(
        "SELECT ts, source, title, text FROM news_item "
        "WHERE substr(ts,1,10)='%s' ORDER BY ts" % date)


def fetch_hot(date: str) -> tuple[str, list[dict]]:
    """日榜：取 <= date 的最近一个 bucket（周末/节假日自动回退到最近交易日）。"""
    got = remote_json("SELECT MAX(bucket) AS b FROM hot_snapshot "
                      "WHERE source='ths_day' AND bucket<='%s'" % date)
    bucket = (got[0].get("b") if got else None) or ""
    if not bucket:
        return "", []
    rows = remote_json(
        "SELECT rank, code, name, heat, pct, rank_chg, concepts, tag FROM hot_snapshot "
        "WHERE source='ths_day' AND bucket='%s' ORDER BY rank" % bucket)
    return bucket, rows


# ══════════════════════════════════════════════════════════════════════
# 组装 prompt
# ══════════════════════════════════════════════════════════════════════
def fmt_heat(v) -> str:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return "-"
    if v >= 1e8:
        return "%.2f亿" % (v / 1e8)
    if v >= 1e4:
        return "%d万" % round(v / 1e4)
    return str(int(v))


def thin(rows: list, limit: int) -> list:
    """超过上限时按时间均匀抽稀，保证全天覆盖而不是砍掉某一段。"""
    if len(rows) <= limit:
        return rows
    step = len(rows) / float(limit)
    return [rows[int(i * step)] for i in range(limit)]


def concept_groups(rows: list[dict], min_n: int = 2) -> list[dict]:
    m: dict = {}
    for x in rows:
        try:
            cs = json.loads(x.get("concepts") or "[]")
        except Exception:
            cs = []
        for c in cs:
            if not c:
                continue
            o = m.setdefault(c, {"n": 0, "heat": 0.0})
            o["n"] += 1
            try:
                o["heat"] += float(x.get("heat") or 0)
            except (TypeError, ValueError):
                pass
    out = [{"name": k, "n": v["n"], "heat": v["heat"]}
           for k, v in m.items() if v["n"] >= min_n]
    out.sort(key=lambda o: (-o["n"], -o["heat"]))
    return out


def build_prompt(date: str, news: list[dict], hot_bucket: str, hot: list[dict]) -> str:
    used = thin(news, MAX_ITEMS)

    lines = []
    for x in used:
        src = SOURCE_CN.get(x.get("source") or "", x.get("source") or "")
        t = (x.get("ts") or "")[11:16]
        title = (x.get("title") or "").strip()
        body = " ".join((x.get("text") or "").split())
        if body and body != title:
            body = body[:BODY_CUT]
        else:
            body = ""
        lines.append("[%s][%s] %s%s" % (t, src, title, ("｜" + body) if body else ""))

    hot_lines = []
    for x in hot[:60]:
        pct = x.get("pct")
        try:
            pct_s = "%+.2f%%" % float(pct) if pct is not None else "-"
        except (TypeError, ValueError):
            pct_s = "-"
        try:
            cs = json.loads(x.get("concepts") or "[]")
        except Exception:
            cs = []
        hot_lines.append("%s. %s(%s) 人气%s 涨跌%s %s %s" % (
            x.get("rank"), x.get("name"), x.get("code"), fmt_heat(x.get("heat")),
            pct_s, ("概念[" + "/".join(cs) + "]") if cs else "",
            ("标签[" + str(x.get("tag")) + "]") if x.get("tag") else ""))

    grp = concept_groups(hot)
    grp_s = "；".join("%s %d只/%s" % (o["name"], o["n"], fmt_heat(o["heat"])) for o in grp[:30])

    note = ""
    if len(news) > len(used):
        note = "\n（当日共 %d 条，为控制长度已按时间均匀抽稀至 %d 条）" % (len(news), len(used))

    return """以下是 A股 【%(date)s】 全天抓取的财经快讯（共 %(n)d 条），以及同花顺热榜日榜（榜单日期 %(bucket)s）。

【输出结构，严格照此组织】
## 一、今日要点速览
3~6 条，每条一句话，覆盖当天最重要的信息。

## 二、分类要点
分四个小节：### 宏观与政策 / ### 产业与行业 / ### 公司与个股 / ### 海外与大宗。
每节用短句列点，信息不足就写「信息不足」。

## 三、影响判断
用 Markdown 表格输出，四列：| 资讯 | 方向 | 受影响板块/个股 | 逻辑 |
方向只填 利多 / 利空 / 中性。涉及个股必须写「名称(代码)」，且只能来自下面给出的资讯或热榜名单。
只列真正有明确指向的 5~12 条，不要凑数。

## 四、情绪与主线研判
结合当日热榜的题材归类回答：
1) 市场主线题材是什么（点名题材与代表个股）；
2) 题材集中度如何（是否有单一主线抱团）；
3) 情绪位置（升温 / 高潮 / 分歧 / 退潮），依据是什么；
4) 次日值得关注的方向，以及需要警惕的地方。
每句话都要有资讯或热榜数据支撑，禁止空话。

【当日资讯（按时间升序）】%(note)s
%(news)s

【当日同花顺热榜 Top60】
%(hot)s

【当日热榜题材归类（上榜≥2只）】
%(grp)s
""" % {"date": date, "n": len(news), "bucket": hot_bucket or "-", "note": note,
       "news": "\n".join(lines) or "（当日无资讯）",
       "hot": "\n".join(hot_lines) or "（无热榜数据）",
       "grp": grp_s or "（无）"}


# ══════════════════════════════════════════════════════════════════════
# 模型 + 邮件
# ══════════════════════════════════════════════════════════════════════
def ask_deepseek(prompt: str) -> str:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 为空（检查 %s）" % ENV_FILE)
    r = requests.post(DS_URL,
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json"},
                      json={"model": DS_MODEL, "temperature": 0.1,
                            "messages": [{"role": "system", "content": SYS_PROMPT},
                                         {"role": "user", "content": prompt}]},
                      timeout=600)
    if r.status_code != 200:
        raise RuntimeError("DeepSeek 返回 %s: %s" % (r.status_code, r.text[:300]))
    return r.json()["choices"][0]["message"]["content"]


MAIL_CSS = """
body{margin:0;padding:0;background:#f5f6f8}
.wrap{max-width:760px;margin:0 auto;padding:18px 16px 32px;color:#1f2430;
  font-size:22px;line-height:1.75;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:26px;margin:6px 0 4px}
.sub{color:#6b7480;font-size:18px;margin:0 0 16px}
h2{font-size:23px;margin:26px 0 10px;padding-left:10px;border-left:5px solid #2f6fd0}
h3{font-size:22px;margin:18px 0 8px;color:#2f6fd0}
p{margin:10px 0}
ul,ol{margin:8px 0;padding-left:1.5em}
li{margin:6px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:20px}
th,td{border:1px solid #dfe3ea;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#eef3fb}
code{background:#f0f2f5;padding:1px 5px;border-radius:4px;font-size:19px}
hr{border:none;border-top:1px solid #e3e7ee;margin:22px 0}
"""


def md_to_mail_html(md: str, title: str, subtitle: str) -> str:
    import markdown2
    body = markdown2.markdown(md, extras=["tables"])
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body>"
            "<div class=\"wrap\" style=\"font-size:22px;line-height:1.75\">"
            "<h1>%s</h1><div class=\"sub\">%s</div>%s</div></body></html>"
            % (title, MAIL_CSS, title, subtitle, body))


def send_mail(subject: str, html: str) -> bool:
    if not os.path.exists(MAIL_CLI):
        print("[!] 未找到 Agent Mail CLI: %s" % MAIL_CLI)
        return False
    path = os.path.join(os.getcwd(), "_news_digest_body.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    cmd = [MAIL_CLI, "message", "+send", "--to", MAIL_TO, "--subject", subject,
           "--body-file", os.path.basename(path), "--body-format", "html", "--confirmed"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=180)
    except Exception as e:
        print("[!] 邮件调用异常: %s（正文留存 %s）" % (e, path))
        return False
    if r.returncode == 0 and '"ok": true' in (r.stdout or ""):
        print("[OK] 邮件已发送: %s" % MAIL_TO)
        os.remove(path)
        return True
    detail = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")[:300]
    print("[!] 发送失败 rc=%s %s（正文留存 %s）" % (r.returncode, detail, path))
    return False


# ══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="每日新闻 AI 总结（邮件日报）")
    ap.add_argument("--date", help="总结哪一天（YYYY-MM-DD），默认昨天")
    ap.add_argument("--dry", action="store_true", help="只生成不发送")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    os.chdir(OUT_DIR)                       # agently-cli 的 --body-file 需在 cwd 内

    date = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print("=== 新闻日报 %s ===" % date)

    news = fetch_news(date)
    bucket, hot = fetch_hot(date)
    print("资讯 %d 条；热榜日榜 bucket=%s，%d 只" % (len(news), bucket or "-", len(hot)))
    if not news and not hot:
        print("[!] 该日期没有任何数据（检查服务器归档或换 --date），退出")
        return 1

    prompt = build_prompt(date, news, bucket, hot)
    print("prompt 长度 %d 字" % len(prompt))

    print("调用 DeepSeek %s ..." % DS_MODEL)
    md = ask_deepseek(prompt)
    if not md.strip():
        print("[!] 模型返回为空，退出")
        return 1
    print("模型输出 %d 字" % len(md))

    subject = "A股新闻日报 - %s" % date
    subtitle = "前一日资讯 AI 总结 · 财联社/东财/华尔街见闻/央视 · 含热榜题材归类"
    html = md_to_mail_html(md, subject, subtitle)

    raw_path = os.path.join(OUT_DIR, "NewsDigest_%s.md" % date)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(md)
    print("正文已存: %s" % raw_path)

    if args.dry:
        prev = os.path.join(OUT_DIR, "NewsDigest_%s.html" % date)
        with open(prev, "w", encoding="utf-8") as f:
            f.write(html)
        print("[dry] 未发送，HTML 预览: %s" % prev)
        return 0

    return 0 if send_mail(subject, html) else 1


if __name__ == "__main__":
    sys.exit(main())
