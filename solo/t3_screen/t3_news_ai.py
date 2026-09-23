# -*- coding: utf-8 -*-
"""T+3 二筛 · 资讯/公告层（东财公告 + DeepSeek 打分）

设计边界（与主程序一致）：
  - 只读外部公开接口，不写任何数据库，不 import 任何原策略模块
  - 严禁编造：公告取自东方财富公告接口；模型自由文本不允许进入结论
  - 任何环节失败（无 key / 接口失败 / 模型超时 / JSON 解析失败）一律回落
    DATA_MISSING + 中性分，绝不阻塞主流程

调用链：
    t3_screen_build.py 逐票算完机械分后 -> run_news_ai() -> 回填 news 10 分
"""
import os
import json
import time

import requests

ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
DS_URL = "https://api.deepseek.com/v1/chat/completions"

FLAG_VOCAB = ["减持", "解禁", "立案", "监管问询", "业绩预亏", "终止重组", "诉讼", "违规",
              "退市风险", "商誉减值", "高比例质押", "定增", "回购", "增持", "业绩预增",
              "中标", "重大合同", "股权激励"]

SYSTEM_PROMPT = u"""你是 A 股 T+3 短周期交易的风险核验员。
你只能依据用户 JSON 中给出的【公告清单】与【行情/财务数据】判断，
严禁编造任何未提供的新闻、传闻、事件或数字，不得用你自己的记忆补充信息。

对输入中的每一只股票输出三项：
- score：0~10 的资讯/公告分
- flags：短标签数组，只能从给定词表中选
- summary：不超过 40 字的一句话，且必须能在给定材料里找到依据

打分口径（严格执行，不得自行放宽）：
1) 公告清单为空 → score=5.0，flags=[]，summary="无近期公告"
2) 只有例行/中性公告（H股公告、公司章程、会议通知、正常定期报告等）→ score=5.0
3) 利好类（回购、增持、中标、重大合同、业绩预增、股权激励）→ score=6.5~8.5
4) 风险类（减持、解禁、立案、监管问询、业绩预亏、终止重组、诉讼、违规、退市风险、
   商誉减值、高比例质押）→ score=0~3.5，且 flags 必须列出对应风险标签
5) 利好与风险并存 → score=4.0~5.5，flags 列出负面项
6) 材料不足以判断 → score=5.0；不得因为"没有消息"就给高分或低分

flags 只能使用以下词表：
%s

只输出 JSON 对象，格式：
{"results":[{"code":"600000.SH","score":5.0,"flags":[],"summary":"无近期公告"}]}
不要输出解释文字、不要 markdown 代码块、不要多余字段。
""" % json.dumps(FLAG_VOCAB, ensure_ascii=False)


def load_env_file(path):
    """从 .env 读取键值（不覆盖已存在的环境变量）；失败静默"""
    if not path or not os.path.exists(path):
        return
    try:
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def fetch_announcements(code, n, log):
    """东方财富公告接口，返回最近公告列表（按 ann_days 过滤）"""
    num = code.split(".")[0]
    try:
        r = requests.get(
            ANN_URL,
            params={"sr": -1, "page_size": n["ann_page_size"], "page_index": 1,
                    "ann_type": "A", "client_source": "web", "stock_list": num},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
            timeout=n["ann_timeout"],
        )
        r.raise_for_status()
        rows = (r.json().get("data") or {}).get("list") or []
    except Exception as e:
        log.warning("公告获取失败 %s: %s", code, repr(e)[:120])
        return None

    out = []
    for it in rows:
        d = (it.get("notice_date") or "")[:10].replace("-", "")
        title = (it.get("title") or "").strip()
        if not title:
            continue
        if n["ann_days"] and d and d < _cutoff(n["ann_days"]):
            continue
        cols = [c.get("column_name") for c in (it.get("columns") or []) if c.get("column_name")]
        out.append({"date": d, "title": title, "type": "/".join(cols[:2])})
    return out


def _cutoff(days):
    import datetime as dt
    return (dt.date.today() - dt.timedelta(days=int(days))).strftime("%Y%m%d")


def build_card(ctx, anns):
    """把内部指标压成给模型的最小卡片，避免无关字段干扰判断"""
    g, p = ctx["g"], ctx.get("plan") or {}
    card = {
        "code": ctx["code"], "name": ctx["name"],
        "industry": ctx["industry"] or "",
        "strategies": [s["strategy_id"] for s in ctx["strategies"]],
        "price": {"ref": _r(g["ref_px"], 2), "chg_pct": _r(g["chg_pct"], 2), "gap_pct": _r(g["gap"], 2)},
        "tech": {"dist_ma20_pct": _r(g["dist_ma20"], 2), "dist_h20_pct": _r(g["dist_h20"], 2),
                 "pos60_pct": _r(g["pos"], 0), "vol_ratio": _r(g["vr"], 2),
                 "turnover_rate": _r(g["turnover"], 2), "rsi": _r(g["rsi"], 0)},
        "fina": {"pe_ttm": _r(g["pe"], 2), "pb": _r(g["pb"], 2), "netprofit_yoy": _r(g["npyoy"], 1),
                 "or_yoy": _r(g["oryoy"], 1), "roe": _r(g["roe"], 1), "gpm": _r(g["gpm"], 1),
                 "ocf_to_or": _r(g["ocf"], 3), "end_date": g["end_date"] or ""},
        "mech_flags": ctx["flags"],
        "announcements": anns or [],
    }
    if p:
        card["plan"] = {"entry": [round(p["entry_low"], 2), round(p["entry_high"], 2)],
                        "stop": round(p["stop"], 2) if p.get("stop") else None,
                        "rr": _r(p.get("rr"), 2)}
    return card


def _r(v, nd):
    return round(float(v), nd) if isinstance(v, (int, float)) else None


def call_deepseek(api_key, n, cards, log):
    """单批调用；返回 {code: {...}}，失败返回 {}"""
    body = {
        "model": n["model"],
        "temperature": n["temperature"],
        "max_tokens": n["max_tokens"],
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"stocks": cards}, ensure_ascii=False)},
        ],
    }
    try:
        r = requests.post(DS_URL, headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer %s" % api_key},
                          json=body, timeout=n["timeout"])
        if r.status_code != 200:
            log.warning("DeepSeek 返回 %s: %s", r.status_code, r.text[:200])
            return {}
        txt = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("DeepSeek 调用失败: %s", repr(e)[:160])
        return {}

    obj = _parse_json(txt)
    if obj is None:
        log.warning("DeepSeek 返回非 JSON，本批回落 DATA_MISSING：%s", txt[:160])
        return {}
    out = {}
    for it in obj.get("results") or []:
        code = str(it.get("code") or "").strip().upper()
        try:
            score = float(it.get("score"))
        except Exception:
            continue
        if not code:
            continue
        score = min(max(score, 0.0), float(n["max"]))
        flags = [f for f in (it.get("flags") or []) if f in FLAG_VOCAB]
        out[code] = {"score": round(score, 2), "flags": flags,
                     "summary": str(it.get("summary") or "").strip()[:80]}
    return out


def _parse_json(txt):
    """容忍模型偶发的代码块包裹 / 前后废话"""
    if not txt:
        return None
    s = txt.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    for cand in (s, s[s.find("{"):s.rfind("}") + 1] if "{" in s and "}" in s else ""):
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None


def run_news_ai(ctxs, cfg, log):
    """主入口：ctxs -> {code: news结果}

    ctxs 每项需含 code/name/industry/g/strategies/flags/plan（见 build_card）
    """
    n = cfg["news"]
    missing = {"status": n["status_missing"], "score": n["missing_default_score"],
               "flags": [], "summary": "", "items": [], "model": None}
    if not n.get("enabled") or not ctxs:
        return {}

    load_env_file(n.get("env_file"))
    api_key = os.getenv(n["api_key_env"])
    if not api_key:
        log.warning("未配置 %s，资讯层回落 DATA_MISSING（中性 %.1f 分）",
                    n["api_key_env"], n["missing_default_score"])
        return {}

    # ---- 1) 逐票取真实公告 ----
    cards, status = [], {}
    for c in ctxs:
        anns = fetch_announcements(c["code"], n, log)
        if anns is None:
            status[c["code"]] = "FETCH_FAIL"
            anns = []
        elif not anns:
            status[c["code"]] = n["status_no_ann"]
        cards.append(build_card(c, anns))
        time.sleep(n["ann_sleep"])

    got = sum(1 for v in status.values() if v == n["status_no_ann"])
    log.info("资讯层 | 公告获取完成 %d 只（其中无近期公告 %d 只）| 模型 %s",
             len(cards), got, n["model"])

    # ---- 2) 分批调用模型 ----
    results, fail_batches = {}, 0
    bs = max(1, int(n["batch_size"]))
    for i in range(0, len(cards), bs):
        batch = cards[i:i + bs]
        res = call_deepseek(api_key, n, batch, log)
        if not res:
            fail_batches += 1
        results.update(res)
        if i + bs < len(cards):
            time.sleep(n["api_sleep"])

    # ---- 3) 合并（模型缺漏的按 DATA_MISSING 兜底）----
    news_map = {}
    for c in cards:
        code = c["code"]
        hit = results.get(code)
        if hit:
            news_map[code] = {"status": n["status_ai"], "score": hit["score"],
                              "flags": hit["flags"], "summary": hit["summary"],
                              "items": c["announcements"], "model": n["model"]}
        else:
            st = status.get(code) or n["status_missing"]
            if st == "FETCH_FAIL":
                st = n["status_missing"]
            news_map[code] = dict(missing, status=st, items=c["announcements"], model=n["model"])
    log.info("资讯层 | 模型打分成功 %d/%d 只（失败批次 %d）", len(results), len(cards), fail_batches)
    return news_map
