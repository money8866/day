# -*- coding: utf-8 -*-
"""Server酱 微信推送"""
import requests

from .config import WECHAT_SCKEY
from .database import log_alert


def send_wechat(title: str, content: str) -> bool:
    if not WECHAT_SCKEY:
        print("未配置 WECHAT_SCKEY，跳过推送")
        return False
    url = f"https://sctapi.ftqq.com/{WECHAT_SCKEY}.send"
    try:
        resp = requests.post(
            url, data={"title": title, "desp": content}, timeout=10
        )
        ok = resp.status_code == 200
        if ok:
            print(f"✓ 微信推送成功: {title}")
        else:
            print(f"推送失败: {resp.text[:200]}")
        return ok
    except Exception as e:
        print(f"推送异常: {e}")
        return False


def push_review_report(trade_date: str, report: str):
    title = f"【主题复盘】{trade_date}"
    send_wechat(title, report)


def push_starter_alert(
    trade_date: str,
    theme_name: str,
    name: str,
    ts_code: str,
    pct_chg: float,
    price: float,
    alert_type: str = "启动预警",
):
    now_str = __import__("datetime").datetime.now().strftime("%H:%M:%S")
    title = f"【{alert_type}】{theme_name} · {name} {pct_chg:+.1f}%"
    content = f"""## {theme_name} 启动信号

⏰ **{now_str}**

### 标的
- **{name}** ({ts_code})
- 现价: **{price:.2f}** 元
- 涨幅: **{pct_chg:+.2f}%**

### 操作建议
> 该主题为今日主线候选，此股为**第1启动股**信号。
> 关注是否封板、板块跟风数量、指数配合。

⚠️ 仅供参考，控制仓位，严格止损
"""
    sent = send_wechat(title, content)
    log_alert(
        trade_date, alert_type, theme_name, ts_code, name,
        f"{name} {pct_chg:+.1f}%", sent=1 if sent else 0,
    )
    return sent


def push_daily_plan(trade_date: str, plan_text: str):
    title = f"【明日作战计划】{trade_date}"
    send_wechat(title, plan_text)
