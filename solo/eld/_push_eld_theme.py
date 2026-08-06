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

# ── 分级工具（Buy Score） ──
_LEVEL_ICON = {"推荐买": "✅", "观察": "👀", "等回踩": "⏳", "禁止追高": "❌"}

def _buy_score_of(s) -> float:
    try:
        return float(s.get("buy_score", 0) or 0)
    except (TypeError, ValueError):
        return 0.0

def _buy_level_of(s) -> str:
    lv = str(s.get("buy_score_level", "") or "")
    if lv in _LEVEL_ICON:
        return lv
    bs = _buy_score_of(s)
    return "推荐买" if bs > 80 else "观察" if bs >= 60 else "等回踩" if bs >= 40 else "禁止追高"

def _bpt_cn(bpt: str) -> str:
    return {"VCP_PULLBACK": "回踩企稳", "MA20_BOUNCE": "MA20支撑", "MA10_BOUNCE": "MA10支撑",
            "BREAKOUT": "突破", "TREND_FOLLOW": "趋势", "CHASE_HIGH": "追高"}.get(bpt, bpt)

def _price_str(s) -> str:
    ref_price = float(s.get("reference_buy_price", 0) or 0)
    stop_loss = float(s.get("stop_loss_price", 0) or 0)
    return f" 参考价{ref_price:.2f} 止损{stop_loss:.2f}" if ref_price > 0 else ""

def _is_chase(s) -> bool:
    return (s.get('buy_point_type', '') == 'CHASE_HIGH') or (float(s.get('bias_pct', 0) or 0) > 15)

def _not_operable(s) -> bool:
    """排除：机构派发 / 利好兑现"""
    return s['institution_state'] == '派发' or str(s.get('is_sell_on_news', '')).lower() == 'true'

# ── 一、今日研究价值 TOP10（V2 排序 · 回答"谁最值得研究"） ──
lines.append("**📚 今日研究价值 TOP10（V2·谁最值得研究）**")
_chase_cnt = 0
for i, s in enumerate(eld_stocks[:10], 1):
    inst = s["institution_state"]
    icon = "🟢" if inst == "吸筹" else "🟡" if inst == "洗盘" else "🔴"
    v2 = round(float(s['final_score_v2']))
    pct = round(float(s["forecast_pct"]))
    buy = _buy_score_of(s)
    lv_icon = _LEVEL_ICON.get(_buy_level_of(s), "")
    if _is_chase(s):
        _chase_cnt += 1
        tag = f"⚠️追高({float(s.get('bias_pct', 0) or 0):.0f}%)"
    else:
        tag = _sig_cn(s['earnings_buy_signal'])
    lines.append(f"{i}.{s['name']} V2:{v2} 预增{pct}% {icon}{inst} {tag} Buy:{buy:.0f}{lv_icon}")
if _chase_cnt > 0:
    lines.append("")
    lines.append(f"> ⚠️ {_chase_cnt}只标注'追高'（乖离>15%高位/连板）：研究第一≠买入第一，仅供跟踪不可追买")
lines.append("")

# ── 二、今日可操作 TOP10（Buy Score 排序 · 回答"谁今天值得买"） ──
# 可操作 = Buy≥60(推荐买/观察) + V2≥55(研究达标, 与BUY信号v2_min_for_buy一致) + 非派发/追高/利好兑现
_tradeable = [s for s in eld_stocks
              if _buy_score_of(s) >= 60
              and float(s.get('final_score_v2', 0) or 0) >= 55
              and not _not_operable(s)
              and not _is_chase(s)]
_wait_pull = [s for s in eld_stocks
              if 40 <= _buy_score_of(s) < 60
              and float(s.get('final_score_v2', 0) or 0) >= 55
              and not _not_operable(s)
              and not _is_chase(s)]
lines.append("**🛒 今日可操作 TOP10（Buy≥60 且 V2≥55）**")
if _tradeable:
    if _market_weak_flag:
        lines.append("> 弱市（大盘<MA20）：买点已整体降级，仅作观察名单，待大盘企稳再介入")
    for s in sorted(_tradeable, key=lambda x: -_buy_score_of(x))[:10]:
        buy = _buy_score_of(s)
        lv = _buy_level_of(s)
        lv_icon = _LEVEL_ICON.get(lv, "")
        v2 = round(float(s['final_score_v2']))
        inst = s["institution_state"]
        q = round(float(s.get('buy_quality_score', 0) or 0))
        qual = f" {_bpt_cn(s.get('buy_point_type', ''))}:{q}" if q > 0 else ""
        bias = float(s.get('bias_pct', 0) or 0)
        lines.append(f" - {s['name']} Buy:{buy:.0f}{lv_icon}({lv}) V2:{v2} {inst}{qual} 乖离{bias:+.1f}%{_price_str(s)}")
elif _wait_pull:
    if _market_weak_flag:
        lines.append(" - 弱市无推荐买，以下等回踩（Buy40-60 且 V2≥55）：")
    else:
        lines.append(" - 今日无推荐买（Buy<60 或 V2<55），等回踩候选：")
    for s in sorted(_wait_pull, key=lambda x: -_buy_score_of(x))[:5]:
        lines.append(f"   · {s['name']} Buy:{_buy_score_of(s):.0f}{_LEVEL_ICON.get(_buy_level_of(s), '')}({_buy_level_of(s)}) V2:{round(float(s['final_score_v2']))} {s['institution_state']}")
else:
    if _market_weak_flag:
        lines.append(" - 无（弱市整体降级，等待大盘站上MA20再看）")
    else:
        lines.append(" - 今日无 Buy≥60 可操作标的，等回踩或关注低吸买点段")
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

# ── 五、操作策略（Buy Score 驱动） ──
lines.append("**操作策略**")
lines.append("")
lines.append("入场：今日可操作榜 Buy≥60 标的 - 回调低吸，参考价附近分批建仓")
lines.append("止损：收盘跌破止损价或MA10")
# 优先关注：Buy≥60 可买 > Buy40-60 等回踩（均叠加 V2≥55，过滤派发+利好兑现+追高）
def _v2_of(s) -> float:
    try:
        return float(s.get('final_score_v2', 0) or 0)
    except (TypeError, ValueError):
        return 0.0

_focus_buy = [s for s in eld_stocks
              if _buy_score_of(s) >= 60 and _v2_of(s) >= 55
              and not _not_operable(s) and not _is_chase(s)]
_focus_wait = [s for s in eld_stocks
               if 40 <= _buy_score_of(s) < 60 and _v2_of(s) >= 55
               and not _not_operable(s) and not _is_chase(s)]
if _focus_buy:
    _focus_buy.sort(key=lambda x: -_buy_score_of(x))
    _txt = "、".join(f"{s['name']}(Buy{_buy_score_of(s):.0f})" for s in _focus_buy[:5])
    lines.append(f"优先关注（Buy≥60 可买）：{_txt}")
elif _focus_wait:
    _focus_wait.sort(key=lambda x: -_buy_score_of(x))
    _txt = "、".join(f"{s['name']}(Buy{_buy_score_of(s):.0f})" for s in _focus_wait[:5])
    lines.append(f"优先关注（Buy40-60 等回踩）：{_txt}")
if _market_weak_flag:
    lines.append("")
    lines.append("⚠️ 大盘<MA20 弱市提醒：买点信号已整体降级，重点等企稳信号")

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
