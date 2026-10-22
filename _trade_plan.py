# -*- coding: utf-8 -*-
"""
_trade_plan.py —— 每日 08:30「大盘推演」生成器（手机版双主题 HTML）

模板：用户提供《大盘推演1022_手机版.html》
      （Stone 量化工作矩阵 · 大盘：结论与操作 / 数据与依据 双 Part，
       一句话结论 + A/B 双路径 + 时间窗口 + 见底见顶 + 纪律 + 基准/开盘定位/
       定性/裁决线/关键价位分级，深色可切，邮件即正文）

数据：mystock 既有接口与缓存为主，公开行情源兜底 ——
      接口 lib/astock_data.py：a50_futures / tencent_kline / cls_telegraph /
        eastmoney_global_news / wallstreetcn_lives / cctv_news / macro_calendar /
        earnings_forecast / lockup_expiry / ipo_calendar / em_hot_rank
      公开源：新浪 hq.sinajs.cn（环球指数 + A 股实时成交额）、
              东方财富 push2his（指数日K成交额 / KOSPI 100.KS11）、
              腾讯 qt.gtimg.cn（实时成交额）
      规则引擎（全部可由日K复算）：
        裁决线 = 昨日最低；生死线 = 年内低点（不含昨日，昨日已破则取昨日低）
        中枢   = 昨收 ×(1+gap)，gap = 0.5×美股三指数均值 + 0.5×KOSPI涨跌，截尾±1.5%
        压力档 = 昨低 + k×(昨高−昨低)/4（模板口径：首档开盘上摸即到，末档=极限）
        支撑档 = 昨低 − k×(昨低−生死线)/4（生死线=裁决线时按 0.3% 步进下探）
        修复目标 = A 路第二档（2/4 处）；第二目标 = A 路第三档（3/4 处）
        ★★     = 档位与日线 MA5/MA10/MA20 距离 <0.4%（均线共振；首档强制 ★）

产物（与晨报/复盘邮件同目录）：
  C:/Users/kongx/WorkBuddy/main/trade-plan-YYYYMMDD.html   （主产物，邮件正文）
  C:/Users/kongx/WorkBuddy/main/trade-plan-YYYYMMDD.txt    （文本摘要）

用法：
  py -3 -X utf8 D:/mystock/_trade_plan.py            # 今天（8:30 定时跑）
  py -3 -X utf8 D:/mystock/_trade_plan.py 20261023   # 指定日期（回放/补跑）

设计原则：外部调用全部独立容错（单点失败只缺该模块并标「暂缺」）；
          盘前跑，日K一律取「计划日之前」的最后一个完整交易日。
"""
import datetime as dt
import html
import os
import re
import sys
import time
import traceback

import requests

sys.path.insert(0, r"D:/mystock/lib")
import astock_data as A  # noqa: E402

# ---------------------------------------------------------------- 常量
OUT_DIR = r"C:/Users/kongx/WorkBuddy/main"

# 模板双主角指数：名称 -> (腾讯码, 东财 secid)
INDEX_CODES = {
    "上证指数": ("sh000001", "1.000001"),
    "创业板指": ("sz399006", "0.399006"),
}

# 美股三指数：名称 -> (新浪码, 东财 secid)
US_CODES = {
    "道指": ("gb_dji", "100.DJIA"),
    "纳指": ("gb_ixic", "100.NDX"),
    "标普": ("gb_inx", "100.SPX"),
}
KOSPI_SECID = "100.KS11"

# 时间窗口（静态日程，±5 分钟）
WIN_SZ = [("09:45", "s2", "★★", "主窗"), ("10:00", "s1", "★", ""),
          ("10:25", "s1", "★", ""), ("10:45", "s2", "★★", "主窗"),
          ("11:05", "s2", "★★", "主窗"), ("13:20", "s1", "★", ""),
          ("14:55", "s1", "★", "")]
WIN_CY = [("09:40", "s2", "★★", ""), ("09:45", "s3", "★★★", "双指数共振，全天最重"),
          ("10:25", "s2", "★★", ""), ("11:00", "s2", "★★", ""),
          ("11:30", "s2", "★★", ""), ("13:45", "s2", "★★", "")]

# 隔夜要闻关键词打分（越高越可能影响 A 股当日）
KW_WEIGHT = {
    "A股": 6, "A 股": 6, "大盘": 5, "沪指": 6, "创业板": 4, "央行": 6, "降准": 7, "降息": 6,
    "证监会": 6, "关税": 6, "特朗普": 4, "美联储": 6, "鲍威尔": 5, "美联储议息": 7,
    "AI": 4, "算力": 5, "光模块": 5, "英伟达": 6, "OpenAI": 5, "半导体": 5, "芯片": 4,
    "存储": 5, "台积电": 5, "机器人": 4, "新能源": 3, "锂电": 4, "宁德时代": 6, "比亚迪": 4,
    "光伏": 4, "稀土": 4, "黄金": 4, "原油": 4, "地缘": 4, "中东": 4, "俄乌": 3,
    " IPO ": 3, "新股": 3, "解禁": 4, "减持": 4, "增持": 4, "回购": 3,
    "美元": 3, "人民币": 4, "汇率": 4, "美债": 5, "纳斯达克": 3, "标普": 3, "道琼斯": 3,
    "通胀": 5, "CPI": 5, "PPI": 4, "PMI": 5, "非农": 5, "GDP": 5, "社融": 5, "LPR": 5,
    "涨停": 4, "跌停": 4, "龙虎榜": 3, "北向": 4, "南向": 3, "两融": 3, "融资余额": 3,
}

TODAY_TAG = ""   # YYYYMMDD（计划日）
TODAY_CN = ""    # YYYY-MM-DD

X_ICON = ('<svg class="j-x" viewBox="0 0 24 24" aria-hidden="true">'
          '<circle cx="12" cy="12" r="11"/>'
          '<path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/></svg>')
V_ICON = ('<svg class="j-x" viewBox="0 0 24 24" aria-hidden="true">'
          '<circle cx="12" cy="12" r="11" style="fill:var(--up)"/>'
          '<path d="M8 12.5l2.8 2.8L16.5 9.5"/></svg>')


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 工具
def safe(fn, default=None, tag=""):
    try:
        return fn()
    except Exception as e:
        log(f"  [缺数据] {tag}: {type(e).__name__} {str(e)[:80]}")
        return default


def http_get(url, params=None, retry=3, timeout=15, referer="https://quote.eastmoney.com/"):
    """带 trust_env 双通道重试的 GET（本机代理时通时不通）"""
    last = None
    for i in range(retry):
        for bypass in (True, False):
            s = requests.Session()
            s.trust_env = bypass
            try:
                r = s.get(url, params=params, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": referer}, timeout=timeout)
                if r.status_code == 200:
                    return r
            except Exception as e:
                last = e
                time.sleep(0.5)
        if i < retry - 1:
            time.sleep(1.0)
    raise last if last else RuntimeError("http_get failed")


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt(v, nd=2):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "-"


def cls_pct(v) -> str:
    return "pos" if v is not None and v >= 0 else "neg"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- 1 指数日K与技术位
def fetch_index(name: str, tx: str, secid: str) -> dict | None:
    """腾讯日K（实时）为主：昨收/昨高/昨低/涨跌幅、MA5/10/20/60、ATR14%、
    年内低点（不含最后一日）、5MIN 中轨、成交额（东财→腾讯实时→暂缺）"""
    df = safe(lambda: A.tencent_kline(tx, period="day", count=280), None,
              f"tencent_kline {tx}")
    if df is None or len(df) < 30:
        log(f"  [指数] {name} 日K不足")
        return None
    df = df[df["date"] < TODAY_CN].reset_index(drop=True)
    if len(df) < 30:
        log(f"  [指数] {name} 计划日前数据不足")
        return None
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    last, prev = df.iloc[-1], df.iloc[-2]

    ma20_series = c.rolling(20).mean()
    tr = pd_like_max(h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs())
    atr14 = float(tr.tail(14).mean() / c.iloc[-1] * 100)

    y0 = TODAY_CN[:4] + "-01-01"
    yl = lo[(df["date"] >= y0) & (df["date"] < last["date"])]
    year_low = float(yl.min()) if len(yl) else float(lo.iloc[:-1].min())

    st = {
        "name": name, "code": tx,
        "last_date": str(last["date"]),
        "prev_close": float(last["close"]),
        "prev_open": float(last["open"]),
        "prev_high": float(last["high"]),
        "prev_low": float(last["low"]),
        "prev_chg": float(last["close"] / prev["close"] - 1) * 100,
        "ma5": float(c.rolling(5).mean().iloc[-1]),
        "ma10": float(c.rolling(10).mean().iloc[-1]),
        "ma20": float(ma20_series.iloc[-1]),
        "ma20_prev": float(ma20_series.iloc[-2]),
        "atr14": atr14,
        "year_low": year_low,
        "amount": None, "amount_src": "暂缺",
        "mid5": None,
        "hi_time": None, "lo_time": None,
    }
    # 生死线：年内低点；昨日已破则取昨日低（刚破的线即新生死线）
    st["death"] = st["prev_low"] if st["prev_low"] <= year_low else year_low

    # 成交额：本地缓存 -> 东财日K -> 腾讯实时（校验现价=末根收盘才采用）
    st["amount"], st["amount_src"] = fetch_amount(secid, tx, st["prev_close"], st["last_date"])

    # 5MIN 中轨（m5 MA10，截至上一交易日收盘）+ 当日高/低点时（m5 覆盖验证）
    m5 = safe(lambda: A.tencent_kline(tx, period="m5", count=320), None, f"m5 {tx}")
    if m5 is not None and len(m5) >= 10:
        m5 = m5[m5["datetime"] < TODAY_CN]
        if len(m5) >= 10:
            st["mid5"] = float(m5["close"].astype(float).tail(10).mean())
            day5 = m5[m5["datetime"].astype(str).str.startswith(str(last["date"]))]
            if len(day5) >= 40:  # 覆盖全天（约48根）才采用
                st["hi_time"] = str(day5.loc[day5["high"].astype(float).idxmax(), "datetime"])[-5:]
                st["lo_time"] = str(day5.loc[day5["low"].astype(float).idxmin(), "datetime"])[-5:]
    return st


def pd_like_max(*series):
    import pandas as pd
    out = series[0]
    for s in series[1:]:
        out = out.combine(s, max)
    return out.fillna(0.0)


def fetch_amount(secid: str, tx: str, expect_close: float, date_str: str):
    """成交额（元）：本地缓存 -> 东财日K f57 -> 腾讯实时 f37（万元，校验现价=末根收盘）"""
    import json
    cache_p = os.path.join(r"D:/mystock", "amount_cache.json")
    cache = {}
    try:
        cache = json.load(open(cache_p, encoding="utf-8"))
    except Exception:
        pass
    ck = f"{tx}|{date_str}"
    if ck in cache:
        return float(cache[ck]), "缓存"
    # ① 东财日K
    try:
        p = {"secid": secid, "klt": "101", "fqt": "0", "end": "20500101", "lmt": "3",
             "fields1": "f1,f2,f3,f4,f5,f6",
             "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
             "ut": "b2884a393a59ad64002292a3e90d46a5"}
        for host in ("https://push2his.eastmoney.com", "https://push2delay.eastmoney.com"):
            try:
                r = http_get(host + "/api/qt/stock/kline/get", p, retry=1)
                kl = r.json()["data"]["klines"]
                best = None
                for row in kl:
                    f = row.split(",")
                    if f[0] < TODAY_CN and (best is None or f[0] > best[0]):
                        best = f
                # f[2]=f53 收盘：与日K末根收盘一致才认，防 secid 错配取到别家指数
                if best and abs(float(best[2]) - expect_close) < max(0.5, expect_close * 0.003):
                    amt = float(best[6])
                    cache[ck] = amt
                    try:
                        json.dump(cache, open(cache_p, "w", encoding="utf-8"))
                    except Exception:
                        pass
                    return amt, "东财日K"
                break
            except Exception:
                continue
    except Exception as e:
        log(f"  [成交额] 东财失败 {type(e).__name__}")
    # ② 腾讯实时（f37 万元）：现价与末根收盘一致才认
    try:
        r = http_get(f"https://qt.gtimg.cn/q={tx}", retry=1)
        r.encoding = "gbk"
        f = r.text.split("~")
        if len(f) > 37 and abs(float(f[3]) - expect_close) < 0.5:
            amt = float(f[37]) * 1e4
            cache[ck] = amt
            try:
                json.dump(cache, open(cache_p, "w", encoding="utf-8"))
            except Exception:
                pass
            return amt, "腾讯实时"
    except Exception as e:
        log(f"  [成交额] 腾讯失败 {type(e).__name__}")
    return None, "暂缺"


# ---------------------------------------------------------------- 2 外围
def fetch_external() -> dict:
    ex = {"us": {}, "kospi": None, "a50": None}
    # 美股：新浪优先（直接给涨跌%），东财兜底
    for name, (sina_c, em_sec) in US_CODES.items():
        pct = None
        try:
            r = http_get("https://hq.sinajs.cn/list=" + sina_c, retry=1,
                         referer="https://finance.sina.com.cn")
            r.encoding = "gbk"
            m = re.search(rf'hq_str_{sina_c}="([^"]*)"', r.text)
            if m:
                f = m.group(1).split(",")
                if len(f) > 2:
                    pct = float(f[2])
        except Exception:
            pass
        if pct is None:
            try:
                p = {"secid": em_sec, "fields1": "f1,f2,f3", "fields2": "f43,f60",
                     "ut": "b2884a393a59ad64002292a3e90d46a5"}
                d = http_get("https://push2.eastmoney.com/api/qt/stock/get", p, retry=1).json().get("data")
                if d and d.get("f60"):
                    pct = (float(d["f43"]) - float(d["f60"])) / float(d["f60"]) * 100
            except Exception:
                pass
        ex["us"][name] = pct
    # KOSPI：东财 100.KS11（今开 vs 前收）；取不到由调用方用 A50 兜底
    try:
        p = {"secid": KOSPI_SECID, "fields1": "f1,f2,f3", "fields2": "f43,f44,f45,f46,f60",
             "ut": "b2884a393a59ad64002292a3e90d46a5"}
        d = http_get("https://push2.eastmoney.com/api/qt/stock/get", p, retry=2).json().get("data")
        if d and d.get("f60"):
            prev = float(d["f60"]) / 100
            cur = float(d["f43"]) / 100 if d.get("f43") else None
            op = float(d["f46"]) / 100 if d.get("f46") else cur
            ex["kospi"] = {"prev": prev, "open": op, "cur": cur,
                           "chg": (op - prev) / prev * 100 if op else None}
        else:
            log("  [KOSPI] 东财返回空")
    except Exception as e:
        log(f"  [KOSPI] 失败 {type(e).__name__}（将由 A50 兜底合成）")
    # A50：mystock 接口
    a50 = safe(lambda: A.a50_futures(), None, "a50_futures")
    if a50 is not None and len(a50):
        r = a50.iloc[0].to_dict()
        try:
            last, pre = float(r.get("last")), float(r.get("pre_settle"))
            ex["a50"] = {"last": last, "pre": pre,
                         "chg": (last - pre) / pre * 100 if pre else None,
                         "night": dt.datetime.now().hour < 9 or dt.datetime.now().hour >= 16}
        except (TypeError, ValueError):
            pass
    return ex


# ---------------------------------------------------------------- 3 资讯与事件
def score_news(item: dict) -> tuple:
    title = (item.get("title") or item.get("Name") or "").strip()
    digest = (item.get("digest") or item.get("content") or item.get("brief") or "").strip()
    if not title and digest:
        title = digest.split("\n")[0][:40]
    digest = re.sub(r"\s+", " ", digest)
    s = 0
    for kw, w in KW_WEIGHT.items():
        if kw in f"{title} {digest}":
            s += w
    return s, title, digest


def fetch_news_events() -> tuple[dict, dict, list]:
    ex = {}
    log("  取财联社电报…")
    cls = safe(lambda: A.cls_telegraph(), None, "cls_telegraph")
    ex["cls"] = (cls.to_dict("records") if hasattr(cls, "to_dict") else list(cls or []))[:40]
    log("  取全球财经…")
    gn = safe(lambda: A.eastmoney_global_news(page_size=20), None, "eastmoney_global_news")
    ex["global"] = gn.to_dict("records") if hasattr(gn, "to_dict") else list(gn or [])
    log("  取华尔街见闻…")
    wsc = safe(lambda: A.wallstreetcn_lives(), None, "wallstreetcn_lives")
    ex["wscn"] = (wsc.to_dict("records") if hasattr(wsc, "to_dict") else list(wsc or []))[:30]
    log("  取央视新闻（昨晚）…")
    cctv_day = (dt.date.fromisoformat(TODAY_CN) - dt.timedelta(days=1)).isoformat()
    cctv = safe(lambda: A.cctv_news(cctv_day, with_content=False), None, "cctv_news")
    ex["cctv"] = (cctv.to_dict("records") if hasattr(cctv, "to_dict") else list(cctv or []))[:20]
    log("  取热榜…")
    hot = safe(lambda: A.em_hot_rank(), None, "em_hot_rank")
    ex["hot"] = (hot.to_dict("records") if hasattr(hot, "to_dict") else list(hot or []))[:10]

    ev = {}
    log("  取宏观日历…")
    ev["macro"] = safe(lambda: A.macro_calendar(TODAY_CN, TODAY_CN), None, "macro_calendar")
    log("  取财报…")
    ev["earnings"] = safe(lambda: A.earnings_forecast(limit=200), None, "earnings_forecast")
    log("  取新股…")
    ev["ipo"] = safe(lambda: A.ipo_calendar(), None, "ipo_calendar")

    pool = []
    for src in ("cls", "global", "wscn", "cctv"):
        for it in ex.get(src) or []:
            if isinstance(it, dict):
                pool.append(score_news(it))
    pool.sort(key=lambda x: -x[0])
    news = [t for t in pool if t[0] > 0][:8]
    return ex, ev, news


def events_lines(ev: dict) -> list[str]:
    """今日关注：宏观日历 + 财报 + 解禁 + 新股"""
    L = []
    mc = ev.get("macro")
    if mc is not None and len(mc):
        rows = mc.to_dict("records") if hasattr(mc, "to_dict") else mc
        for row in rows[:6]:
            t = str(row.get("time") or "")
            try:
                tt = dt.datetime.fromisoformat(t) + dt.timedelta(hours=8)
                t_show = tt.strftime("%H:%M")
            except Exception:
                t_show = t[-5:]
            title = str(row.get("title") or row.get("event") or "")
            imp = row.get("importance") or 0
            star = "★" * int(imp) if str(imp).isdigit() else ""
            extra = ""
            if row.get("actual") not in (None, "") and str(row.get("actual")) != "nan":
                extra = f" 实际{row.get('actual')}"
                if row.get("forecast") not in (None, "") and str(row.get("forecast")) != "nan":
                    extra += f"/预期{row.get('forecast')}"
            if title:
                L.append(f"宏观 {t_show} {star} {title}{extra}")
    ef = ev.get("earnings")
    if ef is not None and len(ef):
        rows = ef.to_dict("records") if hasattr(ef, "to_dict") else ef
        names = []
        for row in rows:
            if str(row.get("notice_date") or "").startswith(TODAY_CN):  # 公告日=今日
                nm = row.get("name") or row.get("code") or ""
                if nm and str(nm) not in names:
                    names.append(str(nm))
        if names:
            L.append(f"财报 {len(names)} 家披露预告：" + "、".join(names[:6]))
    lk = ev.get("lockup")
    rows_lk = []
    if isinstance(lk, dict):
        rows_lk = lk.get("upcoming") or lk.get("history") or []
    elif lk is not None:
        try:
            rows_lk = lk.to_dict("records") if hasattr(lk, "to_dict") else list(lk)
        except Exception:
            rows_lk = []
    if rows_lk:
        names = [str(r.get("name") or r.get("stock_name") or r.get("code") or "")
                 for r in rows_lk[:6]]
        names = [n for n in names if n]
        if names:
            L.append(f"解禁 {len(rows_lk)} 家：" + "、".join(names[:5]))
    ipo = ev.get("ipo")
    if ipo is not None and len(ipo):
        rows_ipo = ipo.to_dict("records") if hasattr(ipo, "to_dict") else ipo
        tdy = TODAY_CN.replace("-", "")
        today_ipo = [r for r in rows_ipo
                     if tdy in str(r.get("apply_date") or "").replace("-", "")
                     or tdy in str(r.get("ballot_date") or "").replace("-", "")
                     or tdy in str(r.get("pay_date") or "").replace("-", "")
                     or tdy in str(r.get("listing_date") or "").replace("-", "")]
        if today_ipo:
            nms = "、".join(str(r.get("name") or "") for r in today_ipo[:5])
            L.append(f"新股 {len(today_ipo)} 只（{nms}），注意开盘分流")
        else:
            L.append(f"新股日历 {len(rows_ipo)} 条（今日无申购/上市）")
    if not L:
        L.append("今日无重大事件日历（或接口暂缺），按常态节奏应对。")
    return L


# ---------------------------------------------------------------- 4 规则引擎
def near_ma(v: float, st: dict) -> bool:
    for ma in (st.get("ma5"), st.get("ma10"), st.get("ma20")):
        if ma and abs(v - ma) / ma < 0.004:
            return True
    return False


def level_engine(st: dict) -> dict:
    """由昨低/昨高/生死线生成 A/B 两路档位（模板口径，2026-10-22 对齐）：
    A 路 = 昨日低→昨日高 四等分（首档开盘上摸即到，末档=极限）；
    B 路 = 昨低→生死线 四等分（末两位固定为 生死线/极限），
           年内低点昨日已破（生死线=裁决线）时按 0.3% 步进下探"""
    pc, ph, pl, dl = (st["prev_close"], st["prev_high"], st["prev_low"], st["death"])
    # A 路（模板：四等分自昨日低起算；首档强制 ★）
    a_chips = []
    for k in (1, 2, 3, 4):
        v = pl + (ph - pl) * k / 4
        desc = "首档，开盘上摸即到" if k == 1 else ("极限（昨日全天高）" if k == 4 else "")
        a_chips.append({"v": v, "star": True if k == 1 else near_ma(v, st), "desc": desc})

    span = pl - dl
    if span > pl * 0.005:
        b_chips = [{"v": pl - span * k / 4, "star": False, "desc": ""} for k in (1, 2, 3)]
        b_chips.append({"v": dl, "star": False, "desc": "生死线"})
        b_chips.append({"v": dl - span * 0.25, "star": False, "desc": "极限"})
    else:  # 昨日已破年内低点：生死线与裁决线重合，按 0.3% 步进下探
        b_chips = [{"v": dl, "star": False,
                    "desc": "生死线（年内低点昨日已破，与裁决线重合）"}]
        b_chips += [{"v": pl * (1 - 0.003 * k), "star": False, "desc": ""} for k in (1, 2, 3)]
        b_chips.append({"v": pl * (1 - 0.013), "star": False, "desc": "极限"})
    return {"a": a_chips, "b": b_chips}


def climate_of(st: dict) -> str:
    """bull/bear/mid：收盘 vs MA20 且看 MA20 斜率"""
    above = st["prev_close"] >= st["ma20"]
    up = st["ma20"] >= st["ma20_prev"]
    if above and up:
        return "bull"
    if (not above) and (not up):
        return "bear"
    return "mid"


def build_plan(idx: dict, ex: dict, news: list, ev: dict) -> dict:
    sh, cy = idx["上证指数"], idx["创业板指"]
    us = ex["us"]
    us_vals = [v for v in us.values() if v is not None]
    us_avg = sum(us_vals) / len(us_vals) if us_vals else 0.0
    ks = ex.get("kospi") or {}
    a50 = ex.get("a50") or {}
    ks_chg = ks.get("chg")
    # gap 合成：0.5×美股三指数均值 + 0.5×KOSPI（缺失时退 0.5×美股 + 0.5×A50夜盘）
    if ks_chg is not None:
        ap_chg, ap_name = ks_chg, "KOSPI"
    elif a50.get("chg") is not None:
        ap_chg, ap_name = a50["chg"], "A50期指"
    else:
        ap_chg, ap_name = 0.0, "仅美股"
    gap = clamp(0.5 * us_avg + 0.5 * ap_chg, -1.5, 1.5)

    # 各指数中枢/区间/档位
    for st in (sh, cy):
        st["gap"] = gap
        st["mid"] = st["prev_close"] * (1 + gap / 100)
        band = clamp(st["atr14"] * 0.5, 0.3, 1.0) / 100
        st["band_lo"] = st["mid"] * (1 - band)
        st["band_hi"] = st["mid"] * (1 + band)
        st["lv"] = level_engine(st)
        st["climate"] = climate_of(st)

    dl_sh, dl_cy = sh["prev_low"], cy["prev_low"]
    # 修复目标锚定模板口径：第一目标 = A 路第二档（昨低→昨高 2/4 处，首档开盘即到不计），
    # 第二目标 = A 路第三档（3/4 处）
    t1_sh, t1_cy = sh["lv"]["a"][1]["v"], cy["lv"]["a"][1]["v"]
    t2_sh, t2_cy = sh["lv"]["a"][2]["v"], cy["lv"]["a"][2]["v"]

    # 收盘区间（模板口径：A 路首档→第三档；B 路极限→昨低，低–高排序）
    for st in (sh, cy):
        st["range_a"] = (int(st["lv"]["a"][0]["v"]), int(st["lv"]["a"][2]["v"]))
        st["range_b"] = tuple(sorted((int(st["lv"]["b"][-1]["v"]), int(st["prev_low"]))))

    # ---- 外围文案
    if us_avg > 0.3:
        us_word = "美股虽强" if all(v > 0 for v in us_vals) else "美股偏强"
    elif us_avg < -0.3:
        us_word = "美股拖累" if all(v < 0 for v in us_vals) else "美股偏弱"
    else:
        us_word = "美股平稳"
    if ap_name == "KOSPI":
        ks_word = "韩股拖累" if ap_chg < -0.3 else ("韩股带动" if ap_chg > 0.3 else "韩股平稳")
    else:
        ks_word = "A50夜盘拖累" if ap_chg < -0.3 else ("A50夜盘带动" if ap_chg > 0.3 else "A50平稳")
    ag = abs(gap)
    open_word = ("平开" if ag < 0.3 else ("小幅" + ("低开" if gap < 0 else "高开") if ag < 0.8
                else "大幅" + ("低开" if gap < 0 else "高开")))

    # ---- 定性
    c_sh, c_cy = sh["climate"], cy["climate"]
    both_bear = c_sh == "bear" and c_cy == "bear"
    both_bull = c_sh == "bull" and c_cy == "bull"
    dev_sh = (sh["prev_close"] / sh["ma20"] - 1) * 100
    dev_cy = (cy["prev_close"] / cy["ma20"] - 1) * 100
    ma20_down_sh = sh["ma20"] < sh["ma20_prev"]
    ma20_down_cy = cy["ma20"] < cy["ma20_prev"]

    def dist_txt(st, dev):
        side = "下方" if dev < 0 else "上方"
        trend = "下行" if st["ma20"] < st["ma20_prev"] else "上行"
        return f"{side} {abs(dev):.1f}%，MA20 {trend}"

    if both_bear:
        climate = f"底气候 ✗ 双指数仅卖（上证{dist_txt(sh, dev_sh)}，创业板{dist_txt(cy, dev_cy)}）"
        rules = ["→ 本轮所有反弹按减仓处理，不新增隔夜多头",
                 "→ 低点窗是反 T 回补时点，不是建仓买点"]
        tail = "任何反弹只做减仓，不做建仓"
        disc1 = "双底气候 ✗：只做反 T，不做正 T 开仓"
    elif both_bull:
        climate = f"底气候 ✓ 双指数仅买（上证{dist_txt(sh, dev_sh)}，创业板{dist_txt(cy, dev_cy)}）"
        rules = ["→ 趋势未破，回踩 5 日线不破可低吸，不追高",
                 "→ 冲高放量滞涨即为减仓点"]
        tail = "回踩可低吸，不追高"
        disc1 = "双底气候 ✓：可顺势低吸，不追高"
    else:
        icons = ("✗" if c_sh == "bear" else ("✓" if c_sh == "bull" else "◇"))
        iconc = ("✗" if c_cy == "bear" else ("✓" if c_cy == "bull" else "◇"))
        climate = f"底气候 {icons}{iconc} 多空分歧（上证{dist_txt(sh, dev_sh)}，创业板{dist_txt(cy, dev_cy)}）"
        rules = ["→ 只做站上 MA20 的一侧，另一侧仅反 T", "→ 无共振方向前不加总仓位"]
        tail = "只做强势一侧，不逆势"
        disc1 = "气候分歧：只做强势一侧，另一侧观望"

    # ---- 一句话结论
    prox_sh = abs(sh["mid"] - dl_sh) / dl_sh * 100
    prox_cy = abs(cy["mid"] - dl_cy) / dl_cy * 100
    if max(prox_sh, prox_cy) < 0.3:
        prox = "并直接贴在裁决线上"
    elif max(prox_sh, prox_cy) < 1.0:
        prox = "贴近裁决线"
    else:
        prox = f"开盘中枢距裁决线约 {max(prox_sh, prox_cy):.1f}%"
    # 跌破后的下看目标：生死线与裁决线重合时（年内低点昨日已破），改看 B 路第二档
    def break_target(st):
        if abs(st["death"] - st["prev_low"]) < 0.5 and len(st["lv"]["b"]) > 1:
            return st["lv"]["b"][1]["v"]
        return st["death"]
    bt_sh, bt_cy = break_target(sh), break_target(cy)
    merged = (abs(sh["death"] - dl_sh) < 0.5) or (abs(cy["death"] - dl_cy) < 0.5)
    brk = "一旦跌破（生死线与裁决线重合，无第二防线），则下看" if merged else "一旦跌破，则下看"
    concl = (f"{us_word}，{ks_word}，A 股{open_word}，{prox}——09:45 是全天方向裁决点。"
             f"守住 {dl_sh:.0f} / {dl_cy:.0f} 则弱势修复至 {t1_sh:.0f} / {t1_cy:.0f}；"
             f"{brk} {bt_sh:.0f} / {bt_cy:.0f}。{tail}。")

    # ---- 极端日与形态判读
    maxabs = max([abs(v) for v in us_vals] + [abs(ap_chg)])
    extreme = maxabs >= 2.5
    ap_desc = ("韩国股市低开走弱" if ap_name == "KOSPI" else "A50 夜盘走弱")
    ap_desc_up = ("韩国股市高开走强" if ap_name == "KOSPI" else "A50 夜盘走强")
    if us_avg > 0.3 and ap_chg < -0.3:
        shape = (f"美股全线走强，而{ap_desc}，A 股小幅低开。"
                 "外围拖累强于美股带动，开盘后更可能处于弱势，而非顺势上攻。")
    elif us_avg < -0.3 and ap_chg < -0.3:
        shape = "内外同步走弱，低开概率大，开盘后防进一步下探，不接第一刀。"
    elif us_avg > 0.3 and ap_chg > 0.3:
        shape = f"内外共振偏强（{ap_desc_up}），可小幅高开，但防高开低走，冲高滞涨即减。"
    elif us_avg < -0.3 and ap_chg > 0.3:
        shape = f"美股拖累但{ap_desc_up}，A 股或平开，按自身节奏区间应对。"
    else:
        shape = "外围指引不强，A 股按自身结构运行，重板块轻指数。"
    open_note = (f"极端日阈值{'已触发' if extreme else '未触发'}"
                 f"（最大值 {maxabs:.2f}% {'≥' if extreme else '<'} 2.5%）；"
                 f"形态判读：{shape}")

    # ---- 外盘 ext
    us_tone = ("（三指数齐涨）" if all(v > 0 for v in us_vals) else
               "（三指数齐跌）" if all(v < 0 for v in us_vals) else "（分化）") if us_vals else ""
    us_txt = "、".join(f"{k} {v:+.2f}%" for k, v in us.items() if v is not None)
    if not us_txt:
        us_txt = "暂缺"
    if ks.get("prev") and ks.get("open"):
        kospi_txt = (f"KOSPI 前收 {ks['prev']:.2f} → 今开 {ks['open']:.2f}"
                     f"（{ks['chg']:+.2f}%）")
    elif ap_name == "仅美股":
        kospi_txt = "KOSPI 暂缺"
    else:
        kospi_txt = f"KOSPI 暂缺（亚太开盘参考以 {ap_name} 代替）"
    a50 = ex.get("a50")
    if a50 and a50.get("chg") is not None:
        sess = "夜盘" if a50.get("night") else "最新"
        a50_txt = f"｜A50 期指{sess} {a50['chg']:+.2f}%"
    else:
        a50_txt = "｜A50 暂缺"
    ext_txt = f"{us_txt}{us_tone}｜{kospi_txt}{a50_txt}"

    # ---- 裁决线 note
    def gap_word(st, dl):
        d = abs(st["mid"] - dl)
        if d < 1:
            return "几乎重合"
        return f"差 {d:.0f} 点（{d / dl * 100:.2f}%）"

    prox_note = f"开盘中枢距裁决线：上证{gap_word(sh, dl_sh)}；创业板{gap_word(cy, dl_cy)}"
    if max(prox_sh, prox_cy) < 0.3:
        line_note = (f"两线均为 {sh['last_date'][5:].replace('-', '/')} 的日内低点"
                     f"（生死线为年内低点）。开盘即贴近裁决线（{prox_note}）→ 开盘首格就决定方向。")
    else:
        line_note = (f"两线均为 {sh['last_date'][5:].replace('-', '/')} 的日内低点"
                     f"（生死线为年内低点）。{prox_note} → 早盘先看中枢攻防。")

    plan = {
        "date_cn": TODAY_CN,
        "week": "一二三四五六日"[dt.date.fromisoformat(TODAY_CN).weekday()],
        "concl": concl,
        "gap": gap,
        "ext": ext_txt,
        "open_note": open_note,
        "climate": climate,
        "rules": rules,
        "line_note": line_note,
        "disc": [disc1,
                 "单票仓位 ≤ 2/3；14:30 后不开新组；收盘敞口归零",
                 f"触及即成交，成交后不可撤销；破生死线（{sh['death']:.2f} / {cy['death']:.2f}）"
                 f"则未成交挂单作废",
                 "09:45 窗口未给方向 → 全天按震荡对待，只做区间"],
        "bottom_sig": [f"09:45 窗口内出现低点，且 10:00 不再破（{dl_sh:.2f} / {dl_cy:.2f}）",
                       f"5MIN 中轨收复（上证 {fmt(sh['mid5'], 1)} / 创业板 {fmt(cy['mid5'], 1)}）",
                       "ΔMA5 负值连续 2 根收窄"],
        "top_sig": [f"上攻触及 {t1_sh:.1f} / {t1_cy:.1f} 后 ΔMA5 由正转负",
                    "5MIN 收阴且量能低于前 3 根均量",
                    f"触及 {t2_sh:.1f} / {t2_cy:.1f} 后滞涨"],
        "news": news,
        "events": events_lines(ev),
        "idx": idx,
    }
    return plan


# ---------------------------------------------------------------- 5 HTML 渲染（模板同款）
CSS = """
:root{
  --bg:#f3f5f8; --card:#ffffff; --line:#e6e9ef; --ink:#16181d; --ink2:#6a7280;
  --up:#0e9f6e; --upbg:#e9f8f2; --down:#dc2626; --downbg:#fdecec;
  --rise:#dc2626; --risebg:#fdecec; --fall:#0e9f6e; --fallbg:#e9f8f2;
  --head:#0d1526; --head2:#16233f; --shadow:0 1px 2px rgba(16,24,40,.05),0 4px 14px rgba(16,24,40,.06);
  --gold:#b98a44; --goldbg:#f8f0df;
}
[data-theme="dark"]{
  --bg:#0b0f17; --card:#121826; --line:#232c3f; --ink:#e7ecf5; --ink2:#8b95a7;
  --upbg:rgba(14,159,110,.12); --downbg:rgba(220,38,38,.12);
  --risebg:rgba(220,38,38,.12); --fallbg:rgba(14,159,110,.12);
  --goldbg:rgba(185,138,68,.14);
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.55}
.pos{color:var(--rise);font-weight:600}
.neg{color:var(--fall);font-weight:600}
.j-x{display:inline-block;width:16px;height:16px;vertical-align:-3px;margin-right:2px;flex:0 0 auto}
.j-x circle{fill:var(--down)}
.j-x path{fill:none;stroke:#fff;stroke-width:2.2;stroke-linecap:round}
.topbar{position:sticky;top:0;z-index:50;background:var(--head);color:#fff}
.topbar-inner{max-width:640px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;padding:12px 16px 10px}
.brand{font-size:14px;font-weight:800;letter-spacing:.02em}
.tbtn{border-radius:999px;padding:0 14px;font-size:12.5px;cursor:pointer;min-height:44px;border:1px solid rgba(255,255,255,.3);background:rgba(255,255,255,.08);color:#fff}
.stknav{max-width:640px;margin:0 auto;display:flex;gap:8px;overflow-x:auto;padding:0 16px 10px;scrollbar-width:none}
.stknav::-webkit-scbar{display:none}
.stknav a{flex:0 0 auto;color:#c8d4e8;background:rgba(255,255,255,.08);border-radius:999px;padding:0 16px;font-size:13px;min-height:44px;display:inline-flex;align-items:center;text-decoration:none}
.stknav a.on{background:#fff;color:#0d1526;font-weight:600}
main{max-width:640px;margin:0 auto;padding:12px 12px 40px}
@media (min-width:700px){main{max-width:760px}}
.part{margin-top:14px}
.phead{background:linear-gradient(135deg,var(--head),var(--head2));color:#fff;border-radius:18px;padding:16px 18px}
.pnum{font-size:11px;font-weight:800;color:#ffd76e;border:1px solid rgba(255,255,255,.3);border-radius:7px;padding:2px 8px}
.phead h2{font-size:19px;font-weight:800;margin-top:8px}
.pmeta{display:flex;gap:6px;flex-wrap:wrap;font-size:11.5px;color:#9fb0cc;margin-top:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);margin:12px 0;padding:16px}
.sec-ttl{font-size:13.5px;font-weight:800;color:var(--ink2);letter-spacing:.06em;margin-bottom:12px}
.sec-ttl::before{content:"";width:4px;height:14px;border-radius:2px;background:var(--ink);display:inline-block;margin-right:8px}
.concl{background:linear-gradient(135deg,#1b2437,#0d1526);color:#fff;border-radius:14px;padding:14px 16px}
.concl-ttl{font-size:12px;font-weight:800;color:#ffd76e;letter-spacing:.08em;margin-bottom:7px}
.concl-body{font-size:13.5px;line-height:1.65}
.concl-body .pos{color:#ff8a8a}
.concl-body .neg{color:#7fe0b2}
.route-trig{font-size:12px;color:var(--ink2);background:var(--line);border-radius:8px;padding:6px 10px;margin-bottom:12px}
.rt-stk{margin:10px 0}
.rt-name{font-size:12.5px;font-weight:800;margin-bottom:6px}
.rt-path{display:flex;flex-wrap:wrap;gap:6px}
.rt-chip{border:1px solid var(--line);border-radius:9px;padding:5px 9px;font-size:12px;font-weight:700;color:var(--ink);background:var(--card)}
.rt-chip.star{border-color:var(--gold);background:var(--goldbg);color:#8a5f1e}
[data-theme="dark"] .rt-chip.star{color:#ffd76e}
.rt-desc{display:block;font-size:10.5px;font-weight:500;color:var(--ink2);margin-top:2px}
.rt-range{margin-top:8px;font-size:12px;font-weight:700;color:var(--ink);background:var(--risebg);border-radius:8px;padding:5px 10px;display:inline-block}
.tw-stk{margin:10px 0}
.tw-row{display:flex;flex-wrap:wrap;gap:6px}
.tw-chip{border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:12px;color:var(--ink)}
.tw-chip.s1{font-weight:600}
.tw-chip.s2{border-color:var(--gold);background:var(--goldbg);font-weight:800;color:#8a5f1e}
[data-theme="dark"] .tw-chip.s2{color:#ffd76e}
.tw-chip.s3{background:linear-gradient(135deg,#1b2437,#0d1526);color:#ffd76e;font-weight:800}
.tw-note{display:block;font-size:10px;font-weight:500;color:var(--ink2);margin-top:2px}
.tw-res{margin-top:10px;font-size:12px;font-weight:700;color:#8a5f1e;background:var(--goldbg);border:1px solid rgba(180,83,9,.35);border-radius:10px;padding:9px 12px}
[data-theme="dark"] .tw-res{color:#ffd76e}
.sig-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.sig{border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.sig-b{border-top:3px solid var(--fall)}
.sig-t{border-top:3px solid var(--rise)}
.sig-ttl{font-size:12.5px;font-weight:800;color:var(--ink)}
.sig ul{margin:7px 0 0;padding-left:16px}
.sig li{font-size:12px;line-height:1.55;margin:5px 0}
.disc{display:flex;gap:9px;margin:8px 0;font-size:12.5px}
.disc .no{flex:0 0 auto;width:20px;height:20px;border-radius:6px;background:var(--head);color:#ffd76e;font-size:11px;font-weight:800;display:flex;align-items:center;justify-content:center;margin-top:1px}
.disc div{line-height:1.55}
.tb{width:100%;border-collapse:collapse;font-size:12.5px;table-layout:fixed}
.tb th{font-size:11px;color:var(--ink2);text-align:left;padding:5px 6px;border-bottom:1.5px solid var(--line)}
.tb td{padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:top;line-height:1.45}
.ext{margin-top:11px;font-size:12px;background:var(--upbg);border-radius:10px;padding:8px 11px;line-height:1.6}
.note{font-size:12px;color:#8a5f1e;background:var(--goldbg);border:1px solid rgba(180,83,9,.35);border-radius:10px;padding:9px 12px;margin-top:11px}
[data-theme="dark"] .note{color:#ffd76e}
.ql{font-size:12.5px;padding:8px 0;border-bottom:1px dashed var(--line);line-height:1.6}
.ql:last-child{border-bottom:none}
.foot{max-width:640px;margin:0 auto;padding:4px 16px 30px;font-size:11px;color:var(--ink2);line-height:1.7}
"""

SCRIPT = """
var nav = document.getElementById('stknav');
var links = Array.prototype.slice.call(nav.querySelectorAll('a'));
var sections = links.map(function(a){return document.querySelector(a.getAttribute('href'));});
function onScroll(){
  var pos = window.scrollY + 90, cur = 0;
  sections.forEach(function(sec,i){ if(sec && sec.offsetTop <= pos) cur = i; });
  links.forEach(function(a,i){a.classList.toggle('on', i===cur);});
}
window.addEventListener('scroll', onScroll, {passive:true});
onScroll();
var tbtn = document.getElementById('tbtn');
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  tbtn.textContent = (t==='dark') ? '浅色' : '深色';
  try{localStorage.setItem('t9r', t);}catch(e){}
}
var saved = null; try{saved = localStorage.getItem('t9r');}catch(e){}
applyTheme(saved === 'dark' ? 'dark' : 'light');
tbtn.addEventListener('click', function(){
  var cur = document.documentElement.getAttribute('data-theme');
  applyTheme(cur === 'dark' ? 'light' : 'dark');
});
"""


def rt_chip(c: dict) -> str:
    cls = "rt-chip star" if c.get("star") else "rt-chip"
    txt = f"{c['v']:.1f}" + (" ★★" if c.get("star") else "")
    desc = f'<span class="rt-desc">{esc(c["desc"])}</span>' if c.get("desc") else ""
    return f'<span class="{cls}">{txt}{desc}</span>'


def tw_chip(t: str, s: str, star: str, note: str) -> str:
    n = f'<span class="tw-note">{esc(note)}</span>' if note else ""
    return f'<span class="tw-chip {s}">{t} {star}{n}</span>'


def iconify(s: str) -> str:
    return s.replace("✗", X_ICON).replace("✓", V_ICON)


def level_color(v: float, ref: float) -> str:
    return "🟡" if abs(v - ref) / ref > 0.0035 else "🟢"


def render_html(p: dict) -> str:
    sh, cy = p["idx"]["上证指数"], p["idx"]["创业板指"]
    d_ref = sh["last_date"][5:].replace("-", "/")  # MM/DD

    # ---- Part 01
    concl = (f'<div class="card"><div class="concl"><div class="concl-ttl">一句话结论</div>'
             f'<div class="concl-body">{esc(p["concl"])}</div></div></div>')

    def route(tag: str, title: str, trig: str, rng_key: str) -> str:
        blocks = []
        for st, nm in ((sh, "上证"), (cy, "创业板")):
            chips = "".join(rt_chip(c) for c in st["lv"][tag])
            rng = st[rng_key]
            blocks.append(
                f'<div class="rt-stk"><div class="rt-name">{nm}</div>'
                f'<div class="rt-path">{chips}</div>'
                f'<div class="rt-range">收盘区间：{rng[0]} – {rng[1]}</div></div>')
        return (f'<div class="card"><div class="sec-ttl">{esc(title)}</div>'
                f'<div class="route-trig">{esc(trig)}</div>{"".join(blocks)}</div>')

    route_a = route("a", "A 路 · 守住裁决线 → 修复",
                    f"触发：守住裁决线（{sh['prev_low']:.2f} / {cy['prev_low']:.2f}），弱势修复",
                    "range_a")
    route_b = route("b", "B 路 · 跌破裁决线 → 延续",
                    f"触发：跌破裁决线（{sh['prev_low']:.2f} / {cy['prev_low']:.2f}），弱势延续",
                    "range_b")

    win_sz = "".join(tw_chip(*w) for w in WIN_SZ)
    win_cy = "".join(tw_chip(*w) for w in WIN_CY)
    windows = (f'<div class="card"><div class="sec-ttl">时间窗口（±5 分钟）</div>'
               f'<div class="tw-stk"><div class="rt-name">上证</div>'
               f'<div class="tw-row">{win_sz}</div></div>'
               f'<div class="tw-stk"><div class="rt-name">创业板</div>'
               f'<div class="tw-row">{win_cy}</div></div>'
               f'<div class="tw-res">共振点：09:45 — 两指数同时指向，是当日第一个也是最关键的方向裁决点。</div></div>')

    sig = (f'<div class="card"><div class="sec-ttl">见底 / 见顶信号</div><div class="sig-grid">'
           f'<div class="sig sig-b"><div class="sig-ttl">见底（B 路止跌，可回补）</div><ul>'
           + "".join(f"<li>{esc(x)}</li>" for x in p["bottom_sig"])
           + f'</ul></div><div class="sig sig-t"><div class="sig-ttl">见顶（A 路衰竭，减仓）</div><ul>'
           + "".join(f"<li>{esc(x)}</li>" for x in p["top_sig"])
           + "</ul></div></div></div>")

    disc = ('<div class="card"><div class="sec-ttl">纪律</div>'
            + "".join(f'<div class="disc"><span class="no">{i}</span>'
                      f'<div>{iconify(esc(x))}</div></div>'
                      for i, x in enumerate(p["disc"], 1)) + "</div>")

    part1 = (f'<section class="part" id="p1"><div class="phead"><span class="pnum">01</span>'
             f'<h2>结论与操作</h2><div class="pmeta">一句话结论 · 双路径推演 · 时间窗口 · '
             f'见底见顶 · 纪律</div></div>{concl}{route_a}{route_b}{windows}{sig}{disc}</section>')

    # ---- Part 02
    def amt_txt(st):
        return f"{st['amount']/1e8:.1f} 亿" if st.get("amount") else "暂缺"

    def hi_lo(st, key):
        v = st["prev_high"] if key == "prev_high" else st["prev_low"]
        t = st.get("hi_time") if key == "prev_high" else st.get("lo_time")
        return f"{v:.2f}" + (f"（{t}）" if t else "")

    base = (f'<div class="card"><div class="sec-ttl">基准（{d_ref} 收盘确认）</div>'
            f'<table class="tb"><colgroup><col style="width:20%"><col style="width:40%">'
            f'<col style="width:40%"></colgroup><thead><tr><th></th>'
            f'<th>上证 000001.SH</th><th>创业板 399006.SZ</th></tr></thead><tbody>'
            f'<tr><td>前收</td><td>{sh["prev_close"]:.2f}（<span class="{cls_pct(sh["prev_chg"])}">'
            f'{sh["prev_chg"]:+.2f}</span>%）</td><td>{cy["prev_close"]:.2f}（'
            f'<span class="{cls_pct(cy["prev_chg"])}">{cy["prev_chg"]:+.2f}</span>%）</td></tr>'
            f'<tr><td>当日高</td><td>{hi_lo(sh, "prev_high")}</td><td>{hi_lo(cy, "prev_high")}</td></tr>'
            f'<tr><td>当日低</td><td>{hi_lo(sh, "prev_low")}</td><td>{hi_lo(cy, "prev_low")}</td></tr>'
            f'<tr><td>成交额</td><td>{amt_txt(sh)}</td><td>{amt_txt(cy)}</td></tr>'
            f'</tbody></table><div class="ext">外盘：{esc(p["ext"])}</div></div>')

    open_tbl = ('<table class="tb"><colgroup><col style="width:20%"><col style="width:30%">'
                '<col style="width:50%"></colgroup><thead><tr><th></th><th>中枢</th>'
                '<th>核心区间</th></tr></thead><tbody>'
                + "".join(
                    f'<tr><td>{nm}</td><td>{st["mid"]:.0f}（<span class="{cls_pct(p["gap"])}">'
                    f'{p["gap"]:+.2f}</span>%）</td><td>{st["band_lo"]:.0f} – {st["band_hi"]:.0f}</td></tr>'
                    for st, nm in ((sh, "上证"), (cy, "创业板")))
                + "</tbody></table>")
    openpos = (f'<div class="card"><div class="sec-ttl">开盘定位</div>{open_tbl}'
               f'<div class="note">{esc(p["open_note"])}</div></div>')

    nature = ('<div class="card"><div class="sec-ttl">定性</div>'
              + "".join(f'<div class="ql">{x}</div>'
                        for x in [iconify(esc(p["climate"]))] + [esc(r) for r in p["rules"]])
              + "</div>")

    line_tbl = ('<table class="tb"><colgroup><col style="width:20%"><col style="width:40%">'
                '<col style="width:40%"></colgroup><thead><tr><th></th><th>裁决线</th>'
                '<th>生死线</th></tr></thead><tbody>'
                f'<tr><td>上证</td><td>{sh["prev_low"]:.2f}</td><td>{sh["death"]:.2f}</td></tr>'
                f'<tr><td>创业板</td><td>{cy["prev_low"]:.2f}</td><td>{cy["death"]:.2f}</td></tr>'
                f'</tbody></table>')
    line_card = (f'<div class="card"><div class="sec-ttl">裁决线（以 30MIN 收盘价为准）</div>'
                 f'{line_tbl}<div class="note">{esc(p["line_note"])}</div></div>')

    def level_table(st: dict) -> str:
        pc, pl = st["prev_close"], st["prev_low"]
        b = st["lv"]["b"]
        limit_v = b[-1]["v"]
        merged = abs(st["death"] - pl) < 0.01  # 生死线=裁决线（年内低点昨日已破）
        supports = sorted((c["v"] for c in b if c["v"] not in (st["death"], limit_v)),
                          reverse=True)
        rows = []
        for c in reversed(st["lv"]["a"]):  # 极限 → 首档
            if c["desc"].startswith("极限"):
                lab = "🔴 极限"
            elif c["desc"].startswith("首档"):
                lab = "🟢 首档"
            else:
                lab = level_color(c["v"], pc)
            rows.append(("压力", c["v"], lab))
        rows.append(("—", pl, "🔴 裁决线=生死线" if merged else "裁决线"))
        for v in supports:
            rows.append(("支撑", v, level_color(v, pl)))
        if not merged:
            rows.append(("支撑", st["death"], "🔴 生死线"))
        rows.append(("支撑", limit_v, "🔴 极限"))
        rows.sort(key=lambda r: -r[1])  # 稳定降序；同值时裁决线保持在生死线之上
        body = "".join(f'<tr><td>{a}</td><td>{v:.2f}</td><td>{c}</td></tr>'
                       for a, v, c in rows)
        return ('<table class="tb"><colgroup><col style="width:22%"><col style="width:36%">'
                '<col style="width:42%"></colgroup><thead><tr><th>档</th><th>价格</th>'
                f'<th>级别</th></tr></thead><tbody>{body}</tbody></table>')

    lv_cards = ""
    for st, nm in ((sh, "上证"), (cy, "创业板")):
        lv_cards += (f'<div class="card"><div class="sec-ttl">关键价位分级 · {nm}</div>'
                     f'{level_table(st)}</div>')

    if p["news"]:
        news_ql = "".join(
            f'<div class="ql">{i}. {esc(t)}｜{esc(d[:64])}</div>'
            for i, (_s, t, d) in enumerate(p["news"][:6], 1))
    else:
        news_ql = '<div class="ql">（接口暂缺或无命中）</div>'
    news_card = (f'<div class="card"><div class="sec-ttl">隔夜资讯精选（按 A 股相关度排序）</div>'
                 f'{news_ql}</div>')

    ev_ql = "".join(f'<div class="ql">{esc(x)}</div>' for x in p["events"])
    ev_card = f'<div class="card"><div class="sec-ttl">今日关注（事件日历）</div>{ev_ql}</div>'

    part2 = (f'<section class="part" id="p2"><div class="phead"><span class="pnum">02</span>'
             f'<h2>数据与依据</h2><div class="pmeta">基准 · 外盘 · 开盘定位 · 定性 · 裁决线 · '
             f'关键价位 · 资讯 · 今日关注</div></div>{base}{openpos}{nature}{line_card}'
             f'{lv_cards}{news_card}{ev_card}</section>')

    foot = (f"内容由 mystock _trade_plan.py 自动生成（{time.strftime('%Y-%m-%d %H:%M')}）｜ "
            f"数据来源：腾讯/新浪/东方财富行情 · mystock 资讯接口 ｜ 技术面判断，不构成投资建议 ｜ "
            f"关键价位为规则引擎按昨收/昨高昨低/年内低点/均线共振推算")

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1526">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230d1526'/%3E%3Cpath d='M7 22 L14 14 L19 18 L26 9' stroke='%23ffd76e' stroke-width='3' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<title>{p['date_cn']} 大盘推演 · Stone 量化工作矩阵</title>
<style>{CSS}</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner"><div class="brand">Stone 量化工作矩阵 · 大盘</div><button class="tbtn" id="tbtn">深色</button></div>
  <nav class="stknav" id="stknav"><a href="#p1">结论操作</a><a href="#p2">数据依据</a></nav>
</header>
<main>
{part1}
{part2}
</main>
<footer class="foot">{esc(foot)}</footer>
<script>{SCRIPT}</script>
</body>
</html>"""


# ---------------------------------------------------------------- 6 TXT 渲染
def render_txt(p: dict) -> str:
    sh, cy = p["idx"]["上证指数"], p["idx"]["创业板指"]
    L = []
    L.append(f"大盘推演 | {p['date_cn']}（周{p['week']}）")
    L.append("")
    L.append(f"【一句话结论】{p['concl']}")
    L.append("")
    L.append(f"【A路 · 守住裁决线 {sh['prev_low']:.2f}/{cy['prev_low']:.2f} → 修复】")
    for st, nm in ((sh, "上证"), (cy, "创业板")):
        chips = " ".join(f"{c['v']:.1f}{'★★' if c['star'] else ''}"
                         + (f"({c['desc']})" if c["desc"] else "") for c in st["lv"]["a"])
        L.append(f"  {nm}: {chips}")
        L.append(f"      收盘区间 {st['range_a'][0]} – {st['range_a'][1]}")
    L.append(f"【B路 · 跌破裁决线 → 延续】")
    for st, nm in ((sh, "上证"), (cy, "创业板")):
        chips = " ".join(f"{c['v']:.1f}" + (f"({c['desc']})" if c["desc"] else "")
                         for c in st["lv"]["b"])
        L.append(f"  {nm}: {chips}")
        L.append(f"      收盘区间 {st['range_b'][0]} – {st['range_b'][1]}")
    L.append("")
    L.append("【时间窗口】上证 09:45/10:45/11:05 主窗；创业板 09:45 双指数共振（全天最重）")
    L.append("【见底信号】" + "；".join(p["bottom_sig"]))
    L.append("【见顶信号】" + "；".join(p["top_sig"]))
    L.append("")
    L.append("【纪律】")
    for i, x in enumerate(p["disc"], 1):
        L.append(f"  {i}. {x}")
    L.append("")
    L.append(f"【外盘】{p['ext']}")
    L.append(f"【开盘定位】中枢：上证 {sh['mid']:.0f}（{p['gap']:+.2f}%）区间 "
             f"{sh['band_lo']:.0f}-{sh['band_hi']:.0f}；创业板 {cy['mid']:.0f}"
             f"（{p['gap']:+.2f}%）区间 "
             f"{cy['band_lo']:.0f}-{cy['band_hi']:.0f}")
    L.append(f"  {p['open_note']}")
    L.append(f"【定性】{p['climate']}")
    for r in p["rules"]:
        L.append(f"  {r}")
    L.append(f"【裁决线/生死线】上证 {sh['prev_low']:.2f} / {sh['death']:.2f}；"
             f"创业板 {cy['prev_low']:.2f} / {cy['death']:.2f}")
    L.append("")
    L.append("【隔夜资讯】")
    for i, (_s, t, d) in enumerate(p["news"][:6], 1):
        L.append(f"  {i}. {t}｜{(d or t)[:60]}")
    L.append("【今日关注】")
    for x in p["events"]:
        L.append(f"  {x}")
    L.append("")
    L.append("技术面判断，不构成投资建议。市场有风险，投资需谨慎。")
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程
def main():
    global TODAY_TAG, TODAY_CN
    day = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y%m%d")
    TODAY_TAG = day
    TODAY_CN = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    t0 = time.time()
    log(f"=== 大盘推演生成 {TODAY_CN} ===")

    log("== 指数技术位 ==")
    idx = {}
    for name, (tx, secid) in INDEX_CODES.items():
        try:
            st = fetch_index(name, tx, secid)
        except Exception as e:
            log(f"  [指数] {name} 失败 {type(e).__name__} {str(e)[:80]}")
            st = None
        if st:
            idx[name] = st
            log(f"  {name}: 昨收 {st['prev_close']:.2f} 昨低 {st['prev_low']:.2f} "
                f"生死线 {st['death']:.2f} MA20偏离 {(st['prev_close']/st['ma20']-1)*100:+.1f}% "
                f"成交额 {st['amount_src']}")
    if len(idx) < len(INDEX_CODES):
        log("  [严重] 指数数据不完整，仍按可用数据渲染")

    log("== 外围 ==")
    ex = fetch_external()
    us_txt = "、".join(f"{k} {v:+.2f}%" for k, v in ex["us"].items() if v is not None) or "暂缺"
    log(f"  美股：{us_txt}")
    if ex.get("kospi"):
        log(f"  KOSPI：前收 {ex['kospi']['prev']:.2f} 今开 {ex['kospi']['open']:.2f} "
            f"（{ex['kospi']['chg']:+.2f}%）")
    if ex.get("a50"):
        log(f"  A50：{ex['a50']['last']:.0f}（{ex['a50']['chg']:+.2f}%）")

    log("== 资讯与事件 ==")
    _ex, ev, news = fetch_news_events()
    log(f"  资讯命中 {len(news)} 条")

    if not idx:
        log("!! 无任何指数数据，退出")
        sys.exit(2)

    plan = build_plan(idx, ex, news, ev)
    htm = render_html(plan)
    txt = render_txt(plan)

    out_html = os.path.join(OUT_DIR, f"trade-plan-{day}.html")
    out_txt = os.path.join(OUT_DIR, f"trade-plan-{day}.txt")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(htm)
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write(txt)

    log(f"=== 完成 {time.time()-t0:.1f}s ===")
    log(f"产物：{out_html}")
    log(f"      {out_txt}")
    log(f"结论：{plan['concl']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
