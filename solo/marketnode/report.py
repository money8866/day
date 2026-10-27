# -*- coding: utf-8 -*-
"""每日 Markdown 报告生成 + 节点历史落盘。"""
import os

import pandas as pd


REASON_TEXT = {
    "ZT_LOW": "涨停家数低位",
    "ZT_DEEP_LOW": "涨停家数深冰(分位≤15)",
    "DT_SURGE": "跌停家数激增",
    "PROMO_WEAK": "连板晋级率低",
    "FEEDBACK_NEG": "昨日强势股反馈转负",
    "FEEDBACK_STRONG_NEG": "高位连板股次日大幅负反馈",
    "LADDER_SHRINK": "连板梯队收缩",
    "BREADTH_BAD": "市场广度恶化",
    "ZT_EXTREME": "涨停情绪极端",
    "DIVERGENCE": "高位分歧(炸板/晋级下降)",
    "CLIMAX_FADE": "高潮退潮",
    "ACCELERATE": "高潮加速",
    "ICE_REPAIR": "冰点修复",
    "DT_DECREASE": "跌停家数减少",
    "ZT_SPREAD": "涨停扩散",
    "BREADTH_RECOVER": "广度回升",
    "VOLUME_EXPAND": "成交额放大",
    "BREADTH_EXPAND": "上涨范围扩大",
    "MAINLINE_EXPAND": "主线扩散/主升确认",
}

NODE_NAME = {
    "N1": "情绪冰点", "N2": "冰点修复", "N3": "增量启动",
    "N4": "主线扩张/主升确认", "N5": "极度高潮", "N6": "退潮确认",
}

NEXT_ACTION = {
    "进攻": "进攻策略可启动",
    "观察": "等待确认",
    "防守": "暂停新增进攻仓位",
}


def _fmt(v, nd=1, suffix=""):
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.{nd}f}{suffix}"


def _reason_text(codes):
    return "、".join(REASON_TEXT.get(c, c) for c in codes)


def _d2(td):
    td = str(td or "")
    return f"{td[:4]}-{td[4:6]}-{td[6:8]}" if len(td) == 8 else (td or "—")


def dynamic_summary_parts(dyn, detect, cur_node, nt):
    """把 node_dynamics() 结果整理为纯文本四段，供 Markdown 报告与 tushare 数据块共用。"""
    days = int(dyn.get("days") or 0)
    prev_node = dyn.get("prev_node")

    path = dyn.get("path") or []
    trail = " → ".join(f"{str(d)[4:6]}-{str(d)[6:8]} {n or '无'}" for d, n in path)
    head = f"{cur_node} · 第 {days} 日" if nt else "无新节点（常态）"
    if prev_node and prev_node != nt:
        if nt:
            head += f"（切换，上一交易日为 {prev_node} {NODE_NAME.get(prev_node, '')}）"
        else:
            head += f"；上一交易日 {prev_node} {NODE_NAME.get(prev_node, '')} 于今日结束"
    elif prev_node and prev_node == nt:
        head += "（较上一交易日延续）"
    else:
        head += "（上一交易日亦无节点）"
    last_node, last_date = dyn.get("last_other_node"), dyn.get("last_other_date")
    if last_node and last_node not in (nt, prev_node):
        head += f"；上一节点 {last_node} {NODE_NAME.get(last_node, '')} 于 {_d2(last_date)} 结束"

    ps = dyn.get("prev_score")
    if nt and prev_node == nt and ps is not None:
        score_txt = f"{ps:.0f}→{detect['node_score']:.0f}（{detect['node_score'] - ps:+.0f}）"
    else:
        score_txt = f"{detect['node_score']:.0f}"

    return {
        "trail": trail,
        "head": head,
        "score_state": f"强度 {score_txt}｜状态 {dyn.get('state_text', '')}",
        "metrics": f"改善 {dyn.get('improved', 0)} 项 / 恶化 {dyn.get('worsened', 0)} 项"
                   f"（{dyn.get('metrics_text') or '—'}）",
        "summary": dyn.get("summary", ""),
    }


def _dynamic_lines(dyn, detect, cur_node, nt):
    """节点延续/切换动态：轨迹 + 今日节点 · 延续天数 + 强度状态 + 指标边际 + 解读。"""
    p = dynamic_summary_parts(dyn, detect, cur_node, nt)
    dyn_line = f"**节点动态：** {('轨迹 ' + p['trail'] + '｜') if p['trail'] else ''}{p['head']}"
    return [
        dyn_line,
        "",
        f"**强度状态：** {p['score_state']}",
        "",
        f"**指标边际：** {p['metrics']}",
        "",
        f"**动态解读：** {p['summary']}",
        "",
    ]


def generate_daily_report(detect, feat, cfg):
    """detect = engine.detect_date(date) 的结果字典。"""
    td = detect["trade_date"]
    r = feat.loc[td]
    nt = detect["node_type"]
    cur_node = f"{nt} {NODE_NAME[nt]}" if nt else "无新节点"

    lines = []
    lines.append("# A股大盘节点日报")
    lines.append("")
    lines.append(f"**交易日期：** {td[:4]}-{td[4:6]}-{td[6:8]}")
    lines.append("")
    lines.append(f"**当前节点：** {cur_node}")
    lines.append("")
    lines.append(f"**当前状态：** {detect['market_state']}")
    lines.append("")
    lines.append(f"**情绪子状态：** {detect['sentiment_state']}")
    lines.append("")
    lines.append(f"**节点强度：** {detect['node_score']:.0f}")
    lines.append("")
    lines.append(f"**置信度：** {detect['confidence']}")
    lines.append("")
    dyn = detect.get("dynamics") or {}
    if dyn:
        lines.extend(_dynamic_lines(dyn, detect, cur_node, nt))
    lines.append("**关键证据：**")
    lines.append("")
    # 涨停/跌停及连板梯队
    lines.append(f"- 涨停 {int(r['zt_count'])} 家（首板 {int(r['board1'])} / 二板 {int(r['board2'])} / 三板 {int(r['board3'])} / 四板+ {int(r['board4p'])}），最高 {int(r['max_board'])} 连板；跌停 {int(r['dt_count'])} 家。")
    # 炸板率与昨日强势股反馈
    lines.append(f"- 炸板率 {_fmt(r.get('zhaban_rate'), 1, '%')}；昨日涨停今日均涨幅 {_fmt(r.get('zt_y_pcnt'), 2, '%')}（正收益 {_fmt(r.get('zt_y_pos'), 0, '%')}），昨日连板今日均涨幅 {_fmt(r.get('lb_y_pcnt'), 2, '%')}。")
    # 市场广度及成交额
    lines.append(f"- 上涨 {int(r['up_count'])} / 下跌 {int(r['down_count'])}（上涨占比 {_fmt(r.get('up_ratio'), 1, '%')}），站上MA20占比 {_fmt(r.get('above_ma20_ratio'), 1, '%')}；成交额 {_fmt(r.get('amount_yi'), 0, '亿')}（为20日均值 {_fmt(r.get('amount_ratio'), 2, '倍')}）。")
    # 指数趋势
    lines.append(f"- 上证指数 {_fmt(r.get('index_pct'), 2, '%')}，中证1000 {_fmt(r.get('small_pct'), 2, '%')}，沪深300 {_fmt(r.get('large_pct'), 2, '%')}（小盘相对大盘 {_fmt(r.get('small_large_diff'), 2, '%')}）。")
    lines.append("")
    # 判断理由
    reason = _reason_text(detect["reason_codes"]) if detect["reason_codes"] else "无显著节点信号，情绪与广度处于常态"
    if nt:
        node_name = NODE_NAME[nt]
        # reason 首项常与节点名同义，去重避免「冰点修复——冰点修复、…」
        parts = [p for p in reason.split("、") if p != node_name]
        reason_disp = "、".join(parts) if parts else reason
        reason_txt = f"{node_name}——{reason_disp}，短线情绪温度 {detect['node_score']:.0f}。"
    else:
        reason_txt = "短线情绪与市场广度均未触发节点阈值，处于常态。"
    lines.append(f"**判断理由：** {reason_txt}")
    lines.append("")
    lines.append(f"**下一步：** {NEXT_ACTION[detect['market_state']]}")
    lines.append("")
    inval = detect.get("invalidation_conditions") or "—"
    lines.append(f"**失效条件：** {inval}")
    lines.append("")
    if detect.get("missing_fields"):
        lines.append(f"> 提示：以下字段缺失，置信度已相应下调：{', '.join(detect['missing_fields'])}")
        lines.append("")
    return "\n".join(lines)


def write_report(detect, feat, cfg):
    md = generate_daily_report(detect, feat, cfg)
    out_dir = cfg["data"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"market_node_daily_{detect['trade_date']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path, md


def save_node_history(hist, meta, cfg):
    out_dir = cfg["data"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "node_history.csv")
    meta_path = os.path.join(out_dir, "node_meta.json")
    hist.to_csv(csv_path, index=False, encoding="utf-8-sig")
    meta.to_json(meta_path, orient="records", force_ascii=False, indent=2)
    return csv_path, meta_path


# ---------------------------------------------------------------------------
# 邮件推送（22px 大字卡片式 HTML，兼容 QQ 邮箱）
# ---------------------------------------------------------------------------

def md_to_email_html(md_text, footer="MarketNode V1 规则引擎自动生成 · 非投资建议"):
    """把日报 Markdown 渲染为卡片式 HTML：正文 22px、标题蓝底、小节左侧蓝色竖条。"""
    import html as _html
    import re

    blocks = []
    title = "A股大盘节点日报"
    date_txt = node_txt = state_txt = ""
    for raw in md_text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("# "):
            title = s[2:].strip()
            continue
        m = re.match(r"^\*\*(.+?)：\*\*\s*(.*)$", s)
        if m:
            label, value = m.group(1).strip(), m.group(2).strip()
            if value:
                blocks.append(("kv", label, value))
                if label == "交易日期":
                    date_txt = value
                elif label == "当前节点":
                    node_txt = value
                elif label == "当前状态":
                    state_txt = value
            else:
                blocks.append(("h2", label))
            continue
        if s.startswith("- "):
            blocks.append(("li", s[2:].strip()))
        elif s.startswith(">"):
            blocks.append(("p", s.lstrip("> ").strip()))
        else:
            blocks.append(("p", s))

    sub = " · ".join(x for x in (date_txt, node_txt, state_txt) if x)
    CARD = "background:#ffffff;border-radius:14px;padding:24px 22px;margin-top:16px;font-size:22px;line-height:1.8;color:#1a1a1a;"
    H2 = ("font-size:22px;line-height:1.5;margin:24px 0 10px;padding-left:10px;"
          "border-left:5px solid #1e5eff;color:#0a2f7a;")
    ROW = "font-size:22px;line-height:1.8;color:#1a1a1a;word-break:break-word;"
    K = "color:#5a6472;"
    V = "font-weight:700;color:#0a2f7a;"

    body = []
    h2_seen = False
    for b in blocks:
        if b[0] == "kv":
            body.append(f'<div style="{ROW}"><span style="{K}">{_html.escape(b[1])}：</span>'
                        f'<span style="{V}">{_html.escape(b[2])}</span></div>')
        elif b[0] == "h2":
            style = H2 if h2_seen else H2 + "margin-top:0;"
            h2_seen = True
            body.append(f'<h2 style="{style}">{_html.escape(b[1])}</h2>')
        elif b[0] == "li":
            body.append(f'<div style="{ROW}">•&nbsp;{_html.escape(b[1])}</div>')
        else:
            body.append(f'<p style="{ROW}margin:0 0 8px;">{_html.escape(b[1])}</p>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:0; background:#eef2f7; }}
  .card, .card p, .card div, .card span, .card li {{ font-size:22px !important; line-height:1.8; }}
</style></head>
<body>
<div style="margin:0;padding:16px 10px;background:#eef2f7;">
  <div style="max-width:680px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#1e5eff 0%,#0a2f7a 100%);border-radius:14px;padding:26px 22px;color:#ffffff;">
      <div style="font-size:22px;font-weight:700;line-height:1.5;color:#ffffff;">{_html.escape(title)}</div>
      <div style="font-size:22px;line-height:1.6;margin-top:10px;color:#ffffff;">{_html.escape(sub)}</div>
    </div>
    <div class="card" style="{CARD}">
      {''.join(body)}
    </div>
    <div style="text-align:center;color:#8a94a6;font-size:16px;line-height:1.6;padding:18px 8px 6px;">{_html.escape(footer)}</div>
  </div>
</div>
</body></html>"""


def send_report_mail(md_path, cfg, subject=None, to_addr=None):
    """用 Agent Mail CLI 把日报发到指定邮箱（fail-soft，失败只打印错误）。

    正文为 22px 卡片式 HTML（老花眼友好）；临时 HTML 发送后即删除。
    """
    import re
    import shutil
    import subprocess

    mail_cfg = cfg.get("mail", {})
    to_addr = to_addr or mail_cfg.get("to") or "stock1975@qq.com"
    with open(md_path, "r", encoding="utf-8") as f:
        md = f.read()

    if not subject:
        m_date = re.search(r"\*\*交易日期：\*\*\s*(\S+)", md)
        m_node = re.search(r"\*\*当前节点：\*\*\s*(.+)", md)
        m_state = re.search(r"\*\*当前状态：\*\*\s*(.+)", md)
        parts = ["A股大盘节点日报"]
        for m in (m_date, m_node, m_state):
            if m:
                parts.append(m.group(1).strip())
        subject = "｜".join(parts)

    out_dir = cfg["data"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    body_path = os.path.join(out_dir, "_mail_body.html")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(md_to_email_html(md))

    exe = shutil.which("agently-cli") or "agently-cli"
    # CLI 要求 --body-file 为相对路径：以 output 目录为工作目录，传文件名
    body_rel = "./" + os.path.basename(body_path)
    cmd = [exe, "message", "+send", "--to", to_addr, "--subject", subject,
           "--body-file", body_rel, "--confirmed"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=out_dir)
        if r.returncode == 0:
            print(f"[mail] 节点日报已发送 -> {to_addr}（主题：{subject}）")
            return True
        # Windows 下 agently-cli 多为 .cmd，直接执行失败时用 cmd /c 重试一次
        if r.returncode != 0 and exe.lower().endswith((".cmd", ".bat")):
            r = subprocess.run(["cmd", "/c", exe] + cmd[1:], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180, cwd=out_dir)
            if r.returncode == 0:
                print(f"[mail] 节点日报已发送 -> {to_addr}（主题：{subject}）")
                return True
        print(f"[mail] 发送失败(exit {r.returncode}): "
              f"{((r.stdout or '') + (r.stderr or '')).strip()[:300]}")
        return False
    except Exception as e:
        print(f"[mail] 发送异常（fail-soft，报告已正常落盘）: {e}")
        return False
    finally:
        try:
            os.remove(body_path)
        except OSError:
            pass