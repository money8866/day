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

# 项目根目录加入 sys.path（脚本以 python eld\_push_eld_theme.py 直运行，需能 import eld.*）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 交易日：命令行参数 > 环境变量 > 最新CSV文件推断（仅接受 YYYYMMDD 格式，忽略 --xx 标志）
import re
TRADE_DATE = ""
for _a in sys.argv[1:]:
    if re.fullmatch(r"\d{8}", _a):
        TRADE_DATE = _a
        break
if not TRADE_DATE:
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

# 主题关联（仅统计未映射数，用于头部显示）
stock_to_themes = defaultdict(list)
for tname, stocks in ts_map.get("themes", {}).items():
    for s in stocks:
        code = s.get("code", "")
        if code:
            stock_to_themes[code].append(tname)

unmapped = [s for s in eld_stocks if not stock_to_themes.get(s["ts_code"], [])]

# ── 构建推送消息（最佳精简模式） ──
# 结构: 市场状态 → 今日可操作 → 个股排行TOP10 → 低吸买点 → 风险提示 → 操作策略
lines = []
lines.append(f"# ELD × 主题关联 — {TRADE_DATE}")
lines.append("")

# 信号翻译
_SIGNAL_CN = {"BUY": "买入", "WATCH": "观望", "IGNORE": "忽略", "NONE": "无"}

def _sig_cn(s: str) -> str:
    return _SIGNAL_CN.get(s, s)

# 市场状态（回测第一变量：大盘<MA20 弱市时 BUY 已整体降级）
def _market_weak() -> bool:
    try:
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            with open(ENV_PATH) as f:
                for line in f:
                    if line.startswith("TUSHARE_TOKEN="):
                        token = line.split("=", 1)[1].strip()
        if not token:
            return False
        from eld.config import get_config
        from eld.cache import EldCache
        from eld.datasource import EldDataSource
        from eld.earnings_buy_point import _get_market_ma20_below
        _cfg = get_config()
        ds = EldDataSource(token, EldCache(_cfg.cache))
        below = _get_market_ma20_below(ds)
        return bool(below)
    except Exception:
        return False

_market_weak_flag = _market_weak()
mkt_str = "⚠️弱市（大盘<MA20），今日以观望为主" if _market_weak_flag else "✅市场正常（大盘≥MA20）"
mapped_n = len(eld_stocks) - len(unmapped)
lines.append(f"> {mapped_n}/50映射主题 ｜ {mkt_str}")
lines.append("")

# ── 一、今日可操作（BUY 信号） ──
_buys = [s for s in eld_stocks
         if s.get('earnings_buy_signal', '') == 'BUY'
         and s['institution_state'] != '派发'
         and str(s.get('is_sell_on_news', '')).lower() != 'true']
lines.append("**🎯今日可操作**")
if _buys:
    for s in sorted(_buys, key=lambda x: -float(x['final_score_v2']))[:10]:
        inst = s["institution_state"]
        v2 = round(float(s['final_score_v2']))
        bpt = s.get('buy_point_type', '')
        bpt_cn = {"VCP_PULLBACK": "回踩企稳", "MA20_BOUNCE": "MA20支撑", "MA10_BOUNCE": "MA10支撑",
                  "BREAKOUT": "突破", "TREND_FOLLOW": "趋势", "CHASE_HIGH": "⚠️追高"}.get(bpt, bpt)
        q = round(float(s.get('buy_quality_score', 0) or 0))
        bias = float(s.get('bias_pct', 0) or 0)
        ref_price = float(s.get("reference_buy_price", 0))
        stop_loss = float(s.get("stop_loss_price", 0))
        price_str = f" 参考价{ref_price:.2f} 止损{stop_loss:.2f}" if ref_price > 0 else ""
        lines.append(f" - {s['name']} {bpt_cn}:{q}分 V2:{v2} {inst} 乖离{bias:+.1f}%{price_str}")
else:
    if _market_weak_flag:
        lines.append(" - 无（弱市整体降级，等待大盘站上MA20再看）")
    else:
        lines.append(" - 今日无BUY信号，关注低吸买点段")
lines.append("")

# ── 二、个股排行 TOP10（V2 降序，含机构状态+信号） ──
lines.append("**📊个股排行 TOP10**")
lines.append("")
for i, s in enumerate(eld_stocks[:10], 1):
    inst = s["institution_state"]
    icon = "🟢" if inst == "吸筹" else "🟡" if inst == "洗盘" else "🔴"
    v2 = round(float(s['final_score_v2']))
    pct = round(float(s["forecast_pct"]))
    lines.append(f"{i}.{s['name']} V2:{v2} 预增{pct}% {icon}{inst} {_sig_cn(s['earnings_buy_signal'])}")
lines.append("")

# ── 三、低吸买点（质量≥80 或 回踩企稳≥60，合并去重） ──
_good_points = [s for s in eld_stocks
                if s.get('buy_point_type', '') in ('VCP_PULLBACK', 'MA20_BOUNCE', 'MA10_BOUNCE', 'BREAKOUT')
                and float(s.get('buy_quality_score', 0) or 0) >= 80
                and float(s.get('final_score_v2', 0) or 0) >= 55
                and s['institution_state'] != '派发'
                and str(s.get('is_sell_on_news', '')).lower() != 'true']
_pullback = [s for s in eld_stocks
             if float(s.get("stock_pullback_score", 0) or 0) >= 60
             and s['institution_state'] != '派发']
_low_buy = {s['ts_code']: s for s in _good_points + _pullback}.values()
if _low_buy:
    lines.append("**🏆低吸买点（质量≥80 或 回踩企稳≥60）**")
    if _market_weak_flag:
        lines.append("> 弱市（大盘<MA20）暂缓介入，等待大盘企稳后按此名单操作")
    for s in sorted(_low_buy, key=lambda x: -max(float(x.get('buy_quality_score', 0) or 0),
                                                float(x.get("stock_pullback_score", 0) or 0)))[:8]:
        inst = s["institution_state"]
        v2 = round(float(s['final_score_v2']))
        q = round(float(s.get('buy_quality_score', 0) or 0))
        pb = round(float(s.get("stock_pullback_score", 0) or 0))
        bpt = s.get('buy_point_type', '')
        bpt_cn = {"VCP_PULLBACK": "回踩企稳", "MA20_BOUNCE": "MA20支撑", "MA10_BOUNCE": "MA10支撑",
                  "BREAKOUT": "突破"}.get(bpt, bpt)
        bias = float(s.get('bias_pct', 0) or 0)
        ref_price = float(s.get("reference_buy_price", 0))
        stop_loss = float(s.get("stop_loss_price", 0))
        price_str = f" 参考价{ref_price:.2f} 止损{stop_loss:.2f}" if ref_price > 0 else ""
        qual = f"{bpt_cn}:{q}分" if q >= 80 else f"回踩:{pb}分"
        lines.append(f" - {s['name']} {qual} V2:{v2} {inst} 乖离{bias:+.1f}%{price_str}")
    lines.append("")

# ── 四、风险提示（派发前5） ──
distributors = [s for s in eld_stocks if s["institution_state"] == "派发"]
if distributors:
    lines.append("**⚠️风险提示（派发）**")
    for s in distributors[:5]:
        v2 = round(float(s['final_score_v2']))
        lines.append(f" - {s['name']} V2:{v2} {_sig_cn(s['earnings_buy_signal'])}")
    lines.append("")

# ── 五、操作策略 ──
lines.append("**操作策略**")
lines.append("")
lines.append("入场：可操作/低吸买点标的 - 回调低吸，参考价附近分批建仓")
lines.append("止损：收盘跌破止损价或MA10")
# 优先关注：BUY > 次日可买 > WATCH（过滤派发+利好兑现）
_focus_buy = []
_focus_watch = []
for s in eld_stocks:
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
if _market_weak_flag:
    lines.append("")
    lines.append("⚠️ 大盘<MA20 弱市提醒：本日买点信号已整体降级，重点等企稳信号")

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
