# -*- coding: utf-8 -*-
"""
潜龙五维 · PRIMARY BUY 每日微信推送
=========================================
1. 读取当日 f120_signal_{T}.csv，筛出 verdict == 'PRIMARY BUY' 的信号
2. 调 DeepSeek 把信号精炼为移动端可执行分析（严格基于字段，禁止编造）
3. 落盘 sli/output/f120_指令_{T}.md
4. PushPlus 推送到微信

用法:
  python sli/f120_push_wechat.py                # 推今日(自动定位最近信号日=今天才推)
  python sli/f120_push_wechat.py --date 20260904   # 指定市场日(仍会推送)
  python sli/f120_push_wechat.py --dry-run 20260904  # 只生成不推送(测试)
"""
import os, sys, argparse, datetime
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
sys.path.insert(0, BASE)

from dotenv import load_dotenv
import requests
load_dotenv(r"d:\mystock\config\.env")

PUSHPLUS_TOKEN = os.getenv("PUSHPLUS")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MAX_PUSH_CHARS = 9000  # PushPlus 超长降级线


def call_deepseek(prompt: str, system: str) -> str:
    """调用 DeepSeek 精炼（失败返回空串，由调用方降级）"""
    if not DEEPSEEK_API_KEY:
        print("⚠ 未配置 DEEPSEEK_API_KEY，跳过 AI 精炼")
        return ""
    try:
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1600,
        }
        resp = requests.post(DEEPSEEK_URL, headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }, json=data, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"⚠ DeepSeek 调用失败: {e}")
        return ""


SYSTEM_PROMPT = (
    "你是A股中长线趋势执行助理，负责把『潜龙五维(F120 V1.1)』PRIMARY BUY 信号整理成可直接执行的手机简报。\n"
    "硬性规则：\n"
    "- 只能使用下方提供的数据，禁止编造任何价格、新闻、财务数字或公司信息；\n"
    "- 字段语义：F120/F/E/P/T/V=0-100评分不是股价；现价/入场区/止损/目标/触发价才是真实价格(元)，原样引用，禁止自算或改动；\n"
    "- setup 含义：DEEP_PULLBACK=深回调、FIRST_PULLBACK=首次浅回调、BREAKOUT_RETEST=突破回踩；trigger 是买点触发条件；\n"
    "- EV/P到目标/P到止损/胜率/建议仓位 直接引用，不解释成预测保证；\n"
    "- 输出用移动端友好 Markdown：禁止首行缩进，禁止使用 #/##/### 等标题语法（渲染字体过大），所有小标题一律用 **加粗** 表示，不用连续全角波浪线装饰。\n"
    "输出结构(总长尽量 ≤1200 字)：\n"
    "1.【今日 PRIMARY BUY】一句话：共 N 只 + 一句总览(基于reason字段的GATE全过描述)；\n"
    "2. 逐只单独成段，固定格式：\n"
    "   ● 代码 名称｜F120 XX.X｜XX_PULLBACK\n"
    "   现价 X.XX｜入场区 a~b｜止损 X.XX｜目标 X.XX｜R:R X.X\n"
    "   EV 期望+X%｜胜率 XX%｜P到目标 XX%｜建议仓 X.X%\n"
    "   操作：触发=「trigger原文」，入场区外不追；收盘跌破止损 X.XX 离场；\n"
    "3.【纪律】一行(竞价高开>3% WAIT/入场溢价>5%放弃当日执行)。\n"
    "若用户提供的数据中没有任何 PRIMARY BUY，则只需一句话说明即可，不要编造标的。"
)


def row_brief(r: pd.Series) -> str:
    """单只信号的紧凑卡片(喂给 AI 的结构化文本)"""
    return (
        f"代码 {r['ts_code']} {r['name']} | verdict {r['verdict']} | grade {r.get('grade','')} | "
        f"setup {r['setup']} | F120 {r['F120']:.1f} | 现价 {r['cur']} | 入场区 {r['zone_lo']}~{r['zone_hi']} | "
        f"止损 {r['stop']} | 目标 {r['target']} | ceiling {r.get('ceiling','')} | RR {r['rr']} | "
        f"trigger={r['trigger']} | reason={r['reason']} | "
        f"EV {r['ev']:+.1%} | P10/P90 {r['ev_p10']:+.0%}/{r['ev_p90']:+.0%} | "
        f"胜率 {r['raw_win']:.0%} | P到目标 {r['p_target']:.0%} | P到止损 {r['p_stop']:.0%} | "
        f"期望持有 {r['exp_days']:.0f}日 | 入场溢价 {r['entry_premium']:+.1%} | 建议仓位 {r['pos']*100:.1f}%"
    )


def load_signal_csv(T: str):
    """读取 f120_signal_{T}.csv, 若不存在返回 None"""
    p = os.path.join(OUT, f"f120_signal_{T}.csv")
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, dtype={"ts_code": str})


def extract_market_state(T: str) -> str:
    """从 f120_signal_{T}.md 头部块引用取市场状态"""
    p = os.path.join(OUT, f"f120_signal_{T}.md")
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f.read().splitlines()[:6]:
                if "市场状态" in ln:
                    return ln.strip().lstrip("> ").split("｜")[0]
    except Exception:
        pass
    return "状态未知"


def push_to_wechat(msg: str, title: str) -> bool:
    """PushPlus 推送(markdown)，超长自动降级"""
    if not PUSHPLUS_TOKEN:
        print("错误: 未设置 PUSHPLUS 环境变量")
        return False
    if len(msg) > MAX_PUSH_CHARS:
        msg = msg[:MAX_PUSH_CHARS] + "\n…(内容超长已截断)"
    url = "https://www.pushplus.plus/send"
    try:
        resp = requests.post(url, json={
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": msg,
            "template": "markdown",
        }, timeout=30)
        result = resp.json()
        if result.get("code") == 200:
            print(f"✅ 推送成功: {result.get('msg', '')}")
            return True
        print(f"⚠ 推送失败: code={result.get('code')} msg={result.get('msg')} data={result.get('data','')}")
        return False
    except Exception as e:
        print(f"⚠ 推送异常: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="潜龙五维 PRIMARY BUY 微信推送")
    ap.add_argument("--date", default=None, help="市场日 YYYYMMDD")
    ap.add_argument("--dry-run", action="store_true", help="只生成不推送")
    args = ap.parse_args()

    today = datetime.date.today().strftime("%Y%m%d")
    T = args.date or today

    df = load_signal_csv(T)
    if df is None:
        print(f"[skip] 无 {T} 信号文件，非交易日或行情未刷新 → 静默退出")
        return 0

    pri = df[df["verdict"] == "PRIMARY BUY"] if len(df) else df
    mkt = extract_market_state(T)

    lines = []
    A = lines.append
    A(f"**潜龙五维 · PRIMARY BUY 信号（市场日 {T}｜{mkt}）**")
    A("")
    A(f"全口径档(verdict=PRIMARY BUY)共 **{len(pri)}** 只。\n")

    if pri.empty:
        A("今日无 PRIMARY BUY 档信号（宁缺毋滥），无全仓口径买入动作。")
    else:
        brief = "\n\n".join(row_brief(r) for _, r in pri.iterrows())
        if args.dry_run:
            ai = ""
        else:
            ai = call_deepseek(
                f"市场日 {T} 的 PRIMARY BUY 信号数据如下：\n\n{brief}",
                SYSTEM_PROMPT)
        if ai:
            A(ai)
        else:
            # AI 不可用 → 降级为原样数据卡
            A("（AI 精炼不可用，以下为信号原始数据）\n")
            for _, r in pri.iterrows():
                A(f"**{r['ts_code']} {r['name']}**\n")
                A(f"- F120 {r['F120']:.1f}｜setup {r['setup']}｜现价 {r['cur']}｜入场区 "
                  f"{r['zone_lo']}~{r['zone_hi']}｜止损 {r['stop']}｜目标 {r['target']}｜RR {r['rr']}")
                A(f"- EV {r['ev']:+.1%}｜胜率 {r['raw_win']:.0%}｜P到目标 {r['p_target']:.0%}｜"
                  f"期望持有 {r['exp_days']:.0f}日｜建议仓 {r['pos']*100:.1f}%")
                A(f"- 触发：{r['trigger']}")
                A(f"- reason：{r['reason']}\n")
        A("---")
        A(f"*潜龙五维 F120 V1.1 · {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} 自动推送*")

    msg = "\n".join(lines)
    save = os.path.join(OUT, f"f120_指令_{T}.md")
    with open(save, "w", encoding="utf-8") as f:
        f.write(msg)
    print(f"✅ 指令已保存: {save}")

    if args.dry_run:
        print("(--dry-run 跳过推送)")
        print(msg)
        return 0
    push_to_wechat(msg, title=f"潜龙五维 PRIMARY BUY {T}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
