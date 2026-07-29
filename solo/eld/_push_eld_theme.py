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

lines.append(f"> 熊市 | {len(eld_stocks)-len(unmapped)}/50映射主题")

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

# ── 三、操作策略 ──
lines.append("")
lines.append("**操作策略**")
lines.append("")
lines.append("熊市+26/28主题退潮，仓位≤2成")
lines.append("入场：缩量回踩MA20(量比<0.6)+观望")
lines.append("止损：收盘跌破MA10")
lines.append("优先关注：金域医学、东方铁塔、全志科技")

# ── 四、派发风险 ──
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
