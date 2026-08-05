"""
ELD × 主题关联分析 — 微信推送脚本（PushPlus）
"""
import json
import csv
import os
import re
import requests
from collections import defaultdict

# ── 配置 ──
import sys
import glob

# 交易日：命令行参数 > 环境变量 > 最新CSV文件推断
TRADE_DATE = ""
if len(sys.argv) > 1:
    TRADE_DATE = sys.argv[1]
else:
    TRADE_DATE = os.environ.get("ELD_TARGET_DATE", "")
if not TRADE_DATE:
    csv_dir = r"D:\mystock\report_daily"
    files = glob.glob(os.path.join(csv_dir, "eld_report_*.csv"))
    if files:
        latest = max(files)
        TRADE_DATE = latest.split("eld_report_")[1].split(".")[0]
if not TRADE_DATE:
    from datetime import datetime
    TRADE_DATE = datetime.now().strftime("%Y%m%d")

ELD_CSV = rf"D:\mystock\report_daily\eld_report_{TRADE_DATE}.csv"
THEME_MAP = rf"D:\mystock\cache_daily\theme_stock_map_v2_{TRADE_DATE}.json"
V8_RESULT = rf"D:\mystock\solo\theme_alpha_v6\cache\theme_alpha_v6_result_v8_{TRADE_DATE}.json"
ENV_PATH = r"D:\mystock\config\.env"

# 读取PushPlus token
pushplus_token = ""
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("PUSHPLUS="):
                pushplus_token = line.split("=", 1)[1].strip()

# ── 读取数据 ──
eld_stocks = []
with open(ELD_CSV, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if int(row["rank"]) > 50:
            break
        eld_stocks.append(row)

with open(THEME_MAP, encoding="utf-8") as f:
    ts_map = json.load(f)

stock_to_themes = defaultdict(list)
for tname, stocks in ts_map.get("themes", {}).items():
    for s in stocks:
        code = s.get("code", "")
        if code:
            stock_to_themes[code].append(tname)

with open(V8_RESULT, encoding="utf-8") as f:
    v8_themes = json.load(f)
v8_lookup = {t["主题"]: t for t in v8_themes}

# ── 关联主题 ──
theme_hits = defaultdict(list)
unmapped = []
for s in eld_stocks:
    code = s["ts_code"]
    themes = stock_to_themes.get(code, [])
    if themes:
        for t in themes:
            theme_hits[t].append(s)
    else:
        unmapped.append(s)

sorted_themes = sorted(theme_hits.items(), key=lambda x: -len(x[1]))

# ── 构建推送消息 ──
lines = []
lines.append(f"# ELD × 主题关联 — {TRADE_DATE}")
lines.append("")
# 信号翻译
_SIGNAL_CN = {"BUY": "买入", "WATCH": "观望", "IGNORE": "忽略", "NONE": "无"}

def _sig_cn(s: str) -> str:
    return _SIGNAL_CN.get(s, s)

lines.append(f"> {len(eld_stocks)-len(unmapped)}/50映射主题")

# ── 一、核心关注 ──
absorbers = [s for s in eld_stocks if s["institution_state"] == "吸筹"]
winners = sorted(eld_stocks, key=lambda x: -float(x["final_score_v2"]))[:5]

if absorbers:
    lines.append("")
    lines.append("**机构吸筹**")
    for s in absorbers:
        code = s["ts_code"]
        themes = stock_to_themes.get(code, [])
        theme_str = ",".join(themes[:2]) if themes else "-"
        v2 = round(float(s['final_score_v2']))
        gap = round(float(s['expectation_gap_v2']))
        lines.append(f" - {s['name']} V2:{v2} 预期差{gap} {_sig_cn(s['earnings_buy_signal'])}")

lines.append("")
lines.append("**V2评分前5**")
for s in winners:
    inst = s["institution_state"]
    icon = "🟢" if inst == "吸筹" else "🟡" if inst == "洗盘" else "🔴"
    v2 = round(float(s['final_score_v2']))
    lines.append(f" - {s['name']} V2:{v2} {icon}{inst} {_sig_cn(s['earnings_buy_signal'])}")

# ── 二、个股排行 ──
lines.append("")
lines.append("**个股排行**")
lines.append("")
for i, s in enumerate(eld_stocks[:20], 1):
    inst = s["institution_state"]
    icon = "🟢" if inst == "吸筹" else "🟡" if inst == "洗盘" else "🔴"
    v2 = round(float(s['final_score_v2']))
    pct = round(float(s["forecast_pct"]))
    lines.append(f"{i}.{s['name']} V2:{v2} 预增{pct}% {icon} {_sig_cn(s['earnings_buy_signal'])}")

# ── 三、个股回踩企稳（独立于大盘信号，新增） ──
_pullback = [s for s in eld_stocks
             if float(s.get("stock_pullback_score", 0) or 0) >= 60
             and s['institution_state'] != '派发']
if _pullback:
    lines.append("")
    lines.append("**🎯个股回踩企稳（缩量企稳买点，独立于大盘）**")
    for s in sorted(_pullback, key=lambda x: -float(x.get("stock_pullback_score", 0) or 0))[:10]:
        inst = s["institution_state"]
        v2 = round(float(s['final_score_v2']))
        pb_score = round(float(s.get("stock_pullback_score", 0) or 0))
        reason = s.get("stock_pullback_reason", "")
        price_str = ""
        ref_price = float(s.get("reference_buy_price", 0))
        stop_loss = float(s.get("stop_loss_price", 0))
        if ref_price > 0:
            price_str = f" 参考价{ref_price:.2f} 止损{stop_loss:.2f}"
        lines.append(f" - {s['name']} 回踩:{pb_score}分 V2:{v2} {inst}{price_str}")
        if reason:
            lines.append(f"    ↳ {reason}")

# ── 四、次日可买 ──
_buyable = [s for s in eld_stocks
            if s.get("next_day_buyable", "").lower() == "true"
            and s['institution_state'] != '派发']
if _buyable:
    lines.append("")
    lines.append("**🎯次日可买（回调中低吸机会）**")
    for s in _buyable[:10]:
        inst = s["institution_state"]
        v2 = round(float(s['final_score_v2']))
        ref_price = float(s.get("reference_buy_price", 0))
        stop_loss = float(s.get("stop_loss_price", 0))
        price_str = f" 参考价{ref_price:.2f} 止损{stop_loss:.2f}" if ref_price > 0 else ""
        lines.append(f" - {s['name']} V2:{v2} {inst}{price_str}")

# ── 五、操作策略 ──
lines.append("")
lines.append("**操作策略**")
lines.append("")
lines.append("入场：次日可买标的 - 回调中低吸，参考价附近建仓")
lines.append("止损：收盘跌破止损价或MA10")
# 优先关注：次日可买 > BUY > WATCH，过滤派发+利好兑现
_focus_buy = []
_focus_watch = []
for s in eld_stocks[:10]:
    sig = s.get('earnings_buy_signal', 'IGNORE')
    inst = s.get('institution_state', '')
    sell_news = str(s.get('is_sell_on_news', '')).lower() == 'true'
    next_day = str(s.get('next_day_buyable', '')).lower() == 'true'
    if sig == 'IGNORE' or '派发' in inst or sell_news:
        continue
    name = s['name']
    if next_day or sig == 'BUY':
        _focus_buy.append(name)
    elif sig == 'WATCH':
        _focus_watch.append(name)
if _focus_buy:
    lines.append(f"优先关注（可操作）：{'、'.join(_focus_buy[:5])}")
elif _focus_watch:
    lines.append(f"优先关注（观察中）：{'、'.join(_focus_watch[:5])}")

# ── 六、派发风险 ──
distributors = [s for s in eld_stocks if s["institution_state"] == "派发"]
if distributors:
    lines.append("")
    lines.append("**派发风险**")
    for s in distributors:
        lines.append(f" - {s['name']} V2{s['final_score_v2']}")

# 合并消息
msg = "\n".join(lines)

# ── PushPlus 推送 ──
def push(msg_title: str = ""):
    if not pushplus_token:
        print("⚠️ PushPlus token 为空，跳过推送")
        return
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": pushplus_token,
        "title": msg_title or f"ELD×主题关联日报 {TRADE_DATE}",
        "content": msg,
        "template": "markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        result = resp.json()
        if result.get("code") == 200:
            print(f"✅ PushPlus 推送成功: {result.get('msg', '')}")
        else:
            print(f"⚠️ PushPlus 推送失败: {result}")
    except Exception as e:
        print(f"❌ PushPlus 请求异常: {e}")

# 同时保存本地
out_path = rf"D:\mystock\report_daily\eld_themes_push_{TRADE_DATE}.md"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(msg)
print(f"✅ 本地已保存: {out_path}")

def main():
    push()

if __name__ == "__main__":
    main()
