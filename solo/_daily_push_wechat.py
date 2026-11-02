# -*- coding: utf-8 -*-
"""
盘后自动分析 + 微信推送
=========================================
流程：
  1. 运行 market_analysis.py（V9.9 仓位 + V10.0 市场环境状态机）
  2. 读取落盘报告，压缩成微信可读纯文本
  3. 通过 wechat-clawbot-push MCP 推送到用户个人微信

用法：
  python _daily_push_wechat.py                # 自动取最近交易日
  python _daily_push_wechat.py --date 20261030
  python _daily_push_wechat.py --dry-run      # 只打印不推送

设计要点：
  · 不重新实现分析逻辑，全部复用 market_analysis.py 的落盘产物
  · 微信正文限长（超长截断并保留关键段），避免推送失败
  · 非交易日不推送，直接退出
"""
import os
import sys
import glob
import json
import time
import argparse
import subprocess
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')
# 备选目录：各模块单独运行时会把报告落到 D:\mystock\cache_daily
ALT_CACHE_DIR = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily')

# ★ WorkBuddy 默认 python 已是 3.14（AppData\Local\Python\bin），但为稳妥显式指定
PYTHON = r'C:\Users\kongx\AppData\Local\Python\bin\python.exe'
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

MAX_WEIXIN_CHARS = 3800      # 微信正文长度上限（保守值）


# ================ 交易日判断 ================
def is_trading_day(d: str) -> bool:
    """用 tushare 交易日历判断；失败时按「周一至周五」粗判并标注。"""
    try:
        sys.path.insert(0, BASE_DIR)
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(BASE_DIR), 'config', '.env'))
        import tushare as ts
        ts.set_token(os.getenv('TUSHARE_TOKEN'))
        pro = ts.pro_api()
        cal = pro.trade_cal(exchange='SSE',
                            start_date=d, end_date=d)
        if cal is None or cal.empty:
            return None          # UNKNOWN
        return bool(int(cal.iloc[0]['is_open']))
    except Exception:
        dt = datetime.datetime.strptime(d, '%Y%m%d')
        return dt.weekday() < 5   # 粗判


def resolve_trade_date(explicit: str = None) -> str:
    """确定要分析/推送的交易日。

    优先级：显式参数 > 今日（交易日且已收盘）> 最近一个已落盘报告的日期。

    ⚠️ 2026-11-02 事故教训：旧版无条件取「最新报告文件」的日期。11-02 15:50
    自动推送时，磁盘上最新的报告还是 10-30 的回填件 → 目标日被定成 10-30，
    分析照跑（落盘的是 11-02 报告），推送却读了 10-30 的旧报告，用户收到
    前一天的内容。且该逻辑对每个交易日都会 off-by-one（周二会重推周一的分析）。
    """
    if explicit:
        return explicit
    today = datetime.datetime.now().strftime('%Y%m%d')
    now_h = datetime.datetime.now().hour
    t = is_trading_day(today)
    if t and now_h >= 15:
        # 今天是交易日且已收盘 → 就分析今天（run_analysis 会落盘今天的报告）
        return today
    # 非交易日 / 盘中误触发 / 交易日历不可用 → 回退到最近报告文件的日期
    files = sorted(glob.glob(os.path.join(CACHE_DIR, 'market_regime_v10_*.txt')))
    if files:
        return os.path.basename(files[-1]).replace('market_regime_v10_', '').replace('.txt', '')
    return today


def _report_is_fresh(trade_date: str, since_ts: float) -> bool:
    """防呆：td 的报告文件是否在 since_ts 之后被写过。

    11-02 事故的第二种形态：目标日定错（如 10-30），分析却写成了别的日期
    （11-02）→ build_message  silently 读到旧报告推给用户。本检查在推送前
    确认「本次分析确实为 td 落了盘」，否则只发告警、不推内容。
    """
    names = [f'market_regime_v10_{trade_date}.txt', f'market_analysis_{trade_date}.txt']
    for d in (CACHE_DIR, ALT_CACHE_DIR):
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p) and os.path.getmtime(p) >= since_ts - 60:
                return True
    return False


# ================ 运行分析 ================
def run_analysis(trade_date: str, timeout: int = 1800) -> tuple:
    """运行 market_analysis.py，返回 (成功?, stdout 尾部)"""
    cmd = [PYTHON, os.path.join(BASE_DIR, 'market_analysis.py')]
    print(f"[推送] 运行分析: {' '.join(cmd)}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           cwd=BASE_DIR, timeout=timeout)
        ok = p.returncode == 0
        return ok, (p.stdout or '')[-2000:]
    except subprocess.TimeoutExpired:
        return False, '分析超时'
    except Exception as e:
        return False, f'分析异常: {e}'


# ================ 报告解析与压缩 ================
def _read(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


def _grab(text: str, label: str, stop: str = None) -> str:
    """从文本里抓取以 label 开头的一行（去掉缩进）"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(label):
            v = s[len(label):].strip()
            if stop and v.endswith(stop):
                v = v[:-len(stop)]
            return v
    return ''


def _section(text: str, start: str, ends: tuple) -> str:
    """抽取 [start, ends) 之间的段落"""
    i = text.find(start)
    if i < 0:
        return ''
    j = len(text)
    for e in ends:
        k = text.find(e, i + len(start))
        if 0 <= k < j:
            j = k
    return text[i:j]


def _grab_block(text: str, start: str, end: str, max_lines: int = 4) -> list:
    """抽取 [start, end) 之间的非空行（最多 max_lines 行）。

    用于把 V10.1 报告里的「当前主线」小节压进微信消息。
    """
    if not text:
        return []
    i = text.find(start)
    if i < 0:
        return []
    j = text.find(end, i + len(start))
    if j < 0:
        j = len(text)
    out = []
    for ln in text[i + len(start):j].splitlines():
        ln = ln.strip()
        if ln:
            out.append(ln)
        if len(out) >= max_lines:
            break
    return out


def build_message(trade_date: str) -> str:
    """把 V10.0 + V10.1 + V9.9 报告压缩成微信消息"""
    # ★ 报告可能落在两个目录：
    #   solo/cache_backbone_tushare —— market_analysis.py 主流程输出
    #   D:\mystock\cache_daily      —— 各模块单独运行时输出
    #   两个都找，取先命中的。
    def _first(paths):
        for p in paths:
            t = _read(p)
            if t:
                return t
        return ''

    v10 = _first([
        os.path.join(CACHE_DIR, f'market_regime_v10_{trade_date}.txt'),
        os.path.join(ALT_CACHE_DIR, f'market_regime_v10_{trade_date}.txt'),
    ])
    v101 = _first([
        os.path.join(CACHE_DIR, f'market_regime_v101_{trade_date}.txt'),
        os.path.join(ALT_CACHE_DIR, f'market_regime_v101_{trade_date}.txt'),
    ])
    v99 = _first([
        os.path.join(CACHE_DIR, f'market_analysis_{trade_date}.txt'),
        os.path.join(ALT_CACHE_DIR, f'market_analysis_{trade_date}.txt'),
        os.path.join(CACHE_DIR, f'position_engine_v9_{trade_date}.txt'),
        os.path.join(ALT_CACHE_DIR, f'position_engine_v9_{trade_date}.txt'),
    ])

    if not v10 and not v99:
        return (f"【盘后分析 · {trade_date}】\n\n"
                f"⚠️ 未找到当日报告文件，脚本可能未成功运行。\n"
                f"（目录：{CACHE_DIR} / {ALT_CACHE_DIR}）")

    d = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    L = []
    L.append(f"📊 盘后分析 · {d}")
    L.append("")

    # ---------- V10.0 市场环境 ----------
    if v10:
        node = _grab(v10, '节点类型：')
        state = _grab(v10, '当前市场状态：')
        days = _grab(v10, '状态持续时间：')
        conf = _grab(v10, '状态置信度：')
        L.append("【市场环境】")
        L.append(f"节点：{node or '无'}")
        L.append(f"状态：{state or '未知'}"
                 + (f"（{days}）" if days else "")
                 + (f"｜置信度{conf}" if conf else ""))
        L.append("")

        # 核心证据（最多3条）
        ev = _section(v10, '核心证据：', ('不确定因素：', '状态判定依据：', '【市场广度与情绪】'))
        if ev:
            items = [l.strip().lstrip('1234567890. ').strip()
                     for l in ev.splitlines()
                     if l.strip() and l.strip()[0].isdigit()]
            for i, it in enumerate(items[:3], 1):
                L.append(f"{i}. {it}")
            L.append("")

        # 广度与情绪
        br = _grab(v10, '上涨家数占比：')
        zt = _grab(v10, '涨停家数：')
        dt = _grab(v10, '跌停家数：')
        zb = _grab(v10, '炸板率：')
        pct = _grab(v10, '历史分位：')
        L.append("【广度与情绪】")
        if br:
            # pct 原文自带「（近250日口径）」，这里只取数值部分避免括号嵌套
            pv = pct.split('（')[0].strip() if pct else ''
            L.append(f"上涨占比：{br}" + (f"｜历史分位 {pv}" if pv else ""))
        if zt or dt:
            L.append(f"涨停 {zt}　跌停 {dt}")
        if zb:
            L.append(f"炸板率 {zb}")
        L.append("")

        # 量价（必须限定在【量价判断】小节内抓取：'较前一交易日'这个标签在
        # 【市场广度与情绪】里也有一个，全文抓会把广度的 +68.9 个百分点
        # 误当成成交额环比推给用户）
        lp = _section(v10, '【量价判断】',
                      ('【指数趋势与位置分位】', '【仓位建议】', '【明日观察重点】'))
        amt = _grab(lp, '成交额：')
        vs1 = _grab(lp, '较前一交易日：')
        vs5 = _grab(lp, '相对5日均额：')
        pat = _grab(lp, '量价形态：')
        L.append("【量价】")
        if amt:
            L.append(f"成交额 {amt}")
        if vs1 or vs5:
            L.append(f"较前日 {vs1}　vs5日均 {vs5}")
        if pat:
            L.append(f"形态：{pat}")
        L.append("")

        # 仓位建议（只取目标仓位与动作）
        tgt = _grab(v10, '建议目标仓位：')
        probe = _grab(v10, '当前是否允许主动试探：')
        action = _grab(v10, '建议动作：')
        L.append("【仓位建议】")
        L.append(f"目标仓位：{tgt or '按现有策略'}")
        if action:
            L.append(f"动作：{action}")
        if probe:
            L.append(f"允许主动试探：{probe}")
        L.append("")

        # 一句话结论
        tail = _grab(v10, '▎')
        if tail:
            L.append("💡 " + tail)

    # ---------- V10.1：当前主线 + 仓位权限（ETF 主线口径）----------
    if v101:
        ml_lines = _grab_block(v101, '【当前主线】', '【仓位权限】')
        auth_max = _grab(v101, '当前市场允许的最高仓位：')
        auth_res = _grab(v101, '满仓授权：')
        state = _grab(v101, '当前状态：')
        if ml_lines or auth_max:
            L.append("")
            L.append("【主线与仓位权限】(V10.1)")
            if state:
                L.append(f"状态：{state}")
            if auth_max:
                L.append(f"仓位权限上限：{auth_max}")
            for ln in (ml_lines or []):
                ln = ln.strip()
                if ln:
                    L.append(ln)
            if auth_res:
                L.append(f"满仓授权：{auth_res}")

    msg = '\n'.join(L)
    if len(msg) > MAX_WEIXIN_CHARS:
        msg = msg[:MAX_WEIXIN_CHARS] + "\n…（内容过长已截断）"
    return msg


# ================ 推送 ================
# wechat-clawbot-push MCP 服务端脚本自带 CLI 调试模式（--test TEXT），
# 直接调它即可推送，脚本不依赖 WorkBuddy 会话在场。
PUSH_SCRIPT = os.path.join(
    os.path.expanduser('~'), '.workbuddy', 'skills', 'wechat-clawbot-push',
    'scripts', 'wechat_clawbot_push_mcp.py')
PUSH_PYTHON = r'C://Users//kongx//.workbuddy//binaries//python//envs//default//Scripts//python.exe'


def check_token_health() -> str:
    """推送前做 token 保活自检。

    wechat-clawbot-push 的 context_token 有效期约 7 天（刷新阈值），
    长假静置可能过期 → 推送静默失败。
    keepalive 在临近失效时会主动往微信推一条续期预警，
    把「突然失效」变成「提前几天预警」。token 正常时不发消息。
    """
    if not os.path.exists(PUSH_SCRIPT):
        return 'skip'
    py = PUSH_PYTHON if os.path.exists(PUSH_PYTHON) else sys.executable
    try:
        p = subprocess.run([py, PUSH_SCRIPT, '--keepalive'],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=120)
        out = ((p.stdout or '') + (p.stderr or '')).strip()
        print(f"[推送] token 自检: {out}")
        return out
    except Exception as e:
        print(f"[推送] token 自检失败（不阻断推送）: {e}")
        return 'error'


def push_to_wechat(text: str) -> bool:
    """推送文本到用户个人微信。返回是否成功。"""
    if not os.path.exists(PUSH_SCRIPT):
        print(f"[推送] ✗ 找不到推送脚本: {PUSH_SCRIPT}")
        return False
    py = PUSH_PYTHON if os.path.exists(PUSH_PYTHON) else sys.executable
    try:
        p = subprocess.run([py, PUSH_SCRIPT, '--test', text],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=120)
        out = (p.stdout or '') + (p.stderr or '')
        print(out.strip())
        # 注意：不能简单匹配 'HTTP 200'——失败时 detail 也是 "HTTP 200 | ... | prepare failed"，
        # 曾因误判成功（2026-11-02 事故）。只认成功标记。
        return ('发送成功' in out) or ('已主动推送' in out)
    except subprocess.TimeoutExpired:
        print('[推送] ✗ 推送超时（120s）')
        return False
    except Exception as e:
        print(f'[推送] ✗ 推送异常: {e}')
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='交易日 YYYYMMDD，默认取最近交易日')
    ap.add_argument('--dry-run', action='store_true', help='只生成内容，不推送、不运行分析')
    ap.add_argument('--no-run', action='store_true', help='不重跑分析，直接用已有报告')
    args = ap.parse_args()

    td = resolve_trade_date(args.date)

    # 交易日检查
    trading = is_trading_day(td)
    if trading is False:
        print(f"[推送] {td} 非交易日，跳过")
        return
    if trading is None:
        print(f"[推送] {td} 交易日历不可用，按交易日继续")

    if not args.dry_run and not args.no_run:
        run_started = time.time()
        ok, tail = run_analysis(td)
        print(f"[推送] 分析{'成功' if ok else '失败'}")
        if not ok:
            print(tail)
            # 失败也推送一条告警
            msg = (f"⚠️ 盘后分析失败 · {td}\n\n"
                   f"market_analysis.py 执行异常，详见运行日志。\n\n"
                   f"（错误摘要）\n{tail[-500:]}")
            _do_push(msg)
            return
        # 防呆：确认本次分析确实为 td 落了盘，否则中止推送（防止重推旧日期内容）
        if not _report_is_fresh(td, run_started):
            print(f"[推送] ✗ 未找到本次为 {td} 生成的报告，疑似分析写成了其他日期，中止推送")
            msg = (f"⚠️ 盘后分析日期异常 · {td}\n\n"
                   f"分析已执行，但未找到本次为 {td} 新写的报告文件，"
                   f"疑似实际写成了其他日期。已中止推送，请人工检查。")
            _do_push(msg)
            return

    msg = build_message(td)
    if args.dry_run:
        print(msg)
        return
    _do_push(msg)


def _do_push(msg: str):
    """实际推送到微信。推送前先做 token 保活自检。"""
    check_token_health()
    print("[推送] 发送到微信…")
    ok = push_to_wechat(msg)
    print(f"[推送] {'✓ 推送成功' if ok else '✗ 推送失败'}")
    if not ok:
        print("[推送] 内容已打印在上方，可手动转发")
    return ok


if __name__ == '__main__':
    main()
