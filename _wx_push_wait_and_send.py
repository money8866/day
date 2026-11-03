# -*- coding: utf-8 -*-
"""等待用户在微信给 ClawBot 发一条消息 → 刷新 context_token → 立即推送 V10.7 结论。

iLink 的主动下发要求「近期活跃会话」：用户必须先主动给 bot 发消息。
本脚本后台轮询（最多 ~6 分钟），拿到新消息后立即发送，全程只发一次（失败不热重试）。
"""
import importlib.util, json, os, sys, time

BRIDGE = os.path.expanduser("~/.workbuddy/skills/wechat-clawbot-push/scripts/wechat_clawbot_push_mcp.py")
spec = importlib.util.spec_from_file_location("wxbridge", BRIDGE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SUMMARY = r"D:\mystock\report_daily\market_regime_v107_summary_20261103.json"

TEXT = """【A股市场状态 V10.7 · 2026-11-03】

市场状态：S4 卖压衰减
卖压衰减确认：FAIL
反弹确认：FAIL
结构性机会：PASS

最强行业：食品饮料（连续强态 25 日）
行业内部广度：当日 64.8% / 20日 55.7%（剔除第一权重股后 20日仍 +13.79%）
量价形态：放量下跌（成交额环比 -4.46%，相对5日均 +4.98%，指数 -2.14%，上涨占比 38.91%）

【仓位】
目标仓位 20%｜建议区间 10%~20%｜硬上限 20%
主动试探权限：不允许｜满仓授权：FULL_DENIED

【切换关键条件】
升级：①上涨占比连续2日改善且≥45% ②跌停家数≤前日70% ③涨停/跌停比≥2.0且高于前日 ④成交额与指数方向匹配 ⑤主线行业持续性赚钱效应成立
降级：①跌停家数较前日放大≥2倍或≥95只 ②广度跌破25% ③核心指数下跌且成交额较20日均放大 ④小盘显著弱于大盘

说明：结论按 V10.7 增量层修订（V10.1 决策记录同步保留，可对比）。卖压衰减确认 FAIL 的原因：跌停 10→24 反弹、下跌家数 818→3309 且放量下跌，即卖压未见衰减，S4 是基线判定而非已确认状态。结构性机会 PASS 只在 20% 硬上限内给出，不突破市场状态权限。

— mystock V10.7"""


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(os.path.join(os.path.dirname(__file__), "_wx_push_run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log("等待用户在微信给 ClawBot 发任意一条消息（最长 6 分钟）…")
    deadline = time.time() + 360
    acquired = False
    while time.time() < deadline:
        ok, detail = m.acquire_token_once()
        if ok:
            log("拿到新 token：%s" % detail)
            acquired = True
            break
        log("轮询：%s" % detail)
        time.sleep(3)
    if not acquired:
        log("超时未收到用户消息，未发送任何内容。")
        return 2
    info = m.token_age_info()
    log("新鲜度：basis=%s age_h=%s session_active=%s" % (
        info.get("basis"), info.get("age_hours"), info.get("session_active")))
    ok, code, detail = m.do_send(TEXT)
    log("推送结果：ok=%s code=%s detail=%s" % (ok, code, detail))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
