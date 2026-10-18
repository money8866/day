# -*- coding: utf-8 -*-
"""
每日盘后复盘驱动器：确定交易日 → 调用 solo/tushare_quant.py 生成 Final_Self 报告 → 产出摘要。

设计要点:
  1. token 只存在于 config/.env，而主脚本在模块顶层就用 os.getenv 读 TUSHARE_TOKEN，
     因此必须「先注入环境变量再子进程執行」，不能 import。
  2. 交易日由真实行情推断（腾讯上证综指最后一根日K），避免节假日/补班日算错。
  3. 生成结果交由上层（Agent）读取并发送，本脚本不碰邮件，职责单一。

用法:
  python _daily_review.py                      # 自动取最近交易日
  python _daily_review.py --date 20260930      # 指定交易日
  python _daily_review.py --timeout 1800       # 放宽生成超时
  python _daily_review.py --skip-gen           # 跳过生成，只重读已有日志输出摘要（调试/重发判断用）
输出: 结尾打印一行 JSON 摘要，供调用方解析。

流程: 生成 Final_Self 报告 → 追加「入场信号记分卡」（solo/_entry_tracker.py：
0仓位后何时能入场的三道闸每日跟踪，含与昨日对比）→ JSON 摘要带 entry_tracker 字段。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
SOLO = os.path.join(ROOT, "solo")
REPORT_DIR = os.path.join(ROOT, "report_daily")
ENV_FILE = os.path.join(ROOT, "config", ".env")
PY = r"C:/Users/kongx/AppData/Local/Python/bin/python.exe"   # 唯一装齐依赖的解释器


def load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def last_trade_date():
    """从上证综指日K推断最近交易日（腾讯源，无需 token）。"""
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import astock_data as A  # noqa
    k = A.tencent_kline("sh000001", count=3)
    dates = sorted(str(d) for d in k["date"].tolist())
    return dates[-1].replace("-", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="交易日 YYYYMMDD，默认自动推断")
    ap.add_argument("--timeout", type=int, default=1500, help="生成超时秒数")
    ap.add_argument("--allow-stale-date", action="store_true",
                    help="非交易日也照样生成（默认非交易日跳过，避免重复补发旧报告）")
    ap.add_argument("--skip-gen", action="store_true",
                    help="跳过生成，只重读已有日志输出摘要（调试/重发判断用）")
    a = ap.parse_args()

    today = datetime.now().strftime("%Y%m%d")
    date = a.date or last_trade_date()
    md = os.path.join(REPORT_DIR, f"Final_Self_{date}.md")
    html = os.path.join(REPORT_DIR, f"Final_Self_{date}.html")
    log = os.path.join(ROOT, f"_gen_{date}.log")

    # 节日/周末保护：腾讯 K 线最后一根不是今天 → 今天不是交易日
    if not a.date and date != today and not a.allow_stale_date:
        print(json.dumps({"ok": True, "skipped": True, "reason": "非交易日",
                          "today": today, "last_trade_date": date}, ensure_ascii=False))
        return 0

    env = dict(os.environ)
    env.update(load_env(ENV_FILE))
    if not env.get("TUSHARE_TOKEN"):
        print(json.dumps({"ok": False, "stage": "env", "error": "config/.env 缺少 TUSHARE_TOKEN"},
                         ensure_ascii=False))
        return 1
    # 抑制 tushare 访问用户目录
    env.setdefault("HOME", ROOT)

    if a.skip_gen:
        proc = type("P", (), {"returncode": 0})
        dur = 0.0
    else:
        # 前置依赖：tushare_quant 只「读取」market_analysis_{date}.txt（不重算），
        # 缺失时 Final_Self 的大盘仓位/趋势分会静默退化为默认值（position=0/ts=50）。
        # 此处做缺失保护：txt 不存在才补跑 market_analysis.py（存在则不重复跑，
        # 尊重 tushare_quant「避免重复运行 analyze_market」的设计）。
        # 注意：market_analysis.py 无 CLI 日期参数，靠内部 TRADE_DATE 回溯，
        # 与本品类一致的前提是「盘后运行」——盘前跑会错位到上一交易日。
        ma_txt = os.path.join(SOLO, "cache_backbone_tushare", f"market_analysis_{date}.txt")
        if not os.path.exists(ma_txt):
            print(f"[PreStep] market_analysis_{date}.txt 缺失，先补跑 market_analysis.py ...",
                  file=sys.stderr)
            ma_log = os.path.join(ROOT, f"_gen_ma_{date}.log")
            with open(ma_log, "w", encoding="utf-8", errors="replace") as lf:
                subprocess.run([PY, "market_analysis.py"], cwd=SOLO, env=env,
                               stdout=lf, stderr=subprocess.STDOUT, timeout=600)
        cmd = [PY, "tushare_quant.py", "--date", date]
        t0 = time.time()
        with open(log, "w", encoding="utf-8", errors="replace") as lf:
            proc = subprocess.run(cmd, cwd=SOLO, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                  timeout=a.timeout)
        dur = time.time() - t0

    # 生成脚本内部会自行调用 Agent Mail CLI 发送（收件人见 solo/tushare_quant.py 的
    # AGENT_MAIL_TO），这里只做结果确认，便于上层决定是否需要补发。
    log_txt = ""
    try:
        log_txt = open(log, encoding="utf-8", errors="replace").read()
    except Exception:
        pass
    mail_line = [l for l in log_txt.splitlines() if "Agent Mail 已发送" in l]
    att_line = [l for l in log_txt.splitlines() if "邮件附件已生成" in l]
    sync_fail = "报告上传失败" in log_txt or "报告同步未成功" in log_txt

    out = {
        "ok": False, "date": date, "duration_sec": round(dur, 1),
        "returncode": proc.returncode, "log": log,
        "mail_sent": bool(mail_line),
        "mail_to": (mail_line[-1].split("已发送:")[-1].strip().split("（")[0] if mail_line else None),
        "attachment": (att_line[-1].split(":")[-1].strip() if att_line else None),
        "remote_sync_failed": sync_fail,   # scp 到服务器的同步，失败不影响邮件
    }
    if os.path.exists(md):
        txt = open(md, encoding="utf-8", errors="replace").read()
        out.update({
            "ok": proc.returncode == 0 and len(txt) > 500,
            "md": md, "html": html if os.path.exists(html) else None,
            "size": len(txt), "mtime": datetime.fromtimestamp(os.path.getmtime(md)).isoformat(timespec="seconds"),
            "sections": txt.count("\n**") + txt.count("\n\n**"),
        })

    # ---- 入场信号记分卡（0仓位后何时能入场的每日跟踪；失败不影响主报告） ----
    entry_md = os.path.join(REPORT_DIR, f"entry_tracker_{date}.md")
    entry_json = os.path.join(REPORT_DIR, f"entry_tracker_{date}.json")
    try:
        subprocess.run([PY, os.path.join(SOLO, "_entry_tracker.py"), "--date", date, "--save"],
                       cwd=SOLO, env=env, capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
        if os.path.exists(entry_md):
            entry_txt = open(entry_md, encoding="utf-8", errors="replace").read()
            # 追加到 Final_Self 报告末尾（幂等：已有记分卡小节则替换旧内容）
            with open(md, "r+", encoding="utf-8") as f:
                base = f.read()
                marker = "\n\n==============================================\n📊 入场信号记分卡"
                if marker in base:
                    base = base[:base.index(marker)]
                f.seek(0)
                f.truncate()
                f.write(base.rstrip() + "\n\n" + entry_txt.strip() + "\n")
            # 摘要提取：三道闸状态（读 JSON 快照，避免字符串匹配脆弱）
            gates, conclusion = {}, None
            if os.path.exists(entry_json):
                card = json.load(open(entry_json, encoding="utf-8"))
                gates = {"gate1_breakdown": "clear" if card["gate1_clear"] else "locked",
                         "gate2_level1": "pass" if card["gate2_clear"] else "fail",
                         "gate3_level2": "pass" if card["gate3_clear"] else "fail"}
                conclusion = next((l.strip() for l in entry_txt.splitlines()
                                   if l.startswith("结论：")), None)
            out["entry_tracker"] = {"md": entry_md, "gates": gates, "conclusion": conclusion}
    except Exception as e:
        out["entry_tracker"] = {"error": str(e)}

    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
