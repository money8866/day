# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— 每日实盘 CLI（§29 每日报告 + 可选邮件推送）

用法：
  python -m position_gate.daily                 # 输出最新一日的 Gate 报告
  python -m position_gate.daily 20260930        # 指定日期
  python -m position_gate.daily --push          # 额外推送邮件(HTML/22px)
  python -m position_gate.daily --rebuild       # 忽略缓存，重建特征
"""
import argparse
import os
import subprocess
import sys

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from position_gate import config as C            # noqa: E402
from position_gate import etf_plan as EP         # noqa: E402
from position_gate import report as RP           # noqa: E402
from position_gate.data import GateData          # noqa: E402

MAIL_TO = 'stock1975@qq.com'


def load_gate(rebuild: bool = False) -> pd.DataFrame:
    p = os.path.join(C.CACHE_DIR, 'gate.pkl')
    if rebuild or not os.path.exists(p):
        gate, _ = RP.build_gate(GateData(), verbose=True)
        return gate
    try:
        return pd.read_pickle(p)
    except ModuleNotFoundError as e:
        print(f'[daily] 旧 gate 缓存依赖缺失({e})，自动重建 ...')
        gate, _ = RP.build_gate(GateData(), verbose=True)
        return gate
    except Exception as e:
        print(f'[daily] gate 缓存读取失败({e})，自动重建 ...')
        gate, _ = RP.build_gate(GateData(), verbose=True)
        return gate


def to_html(text: str, date: str) -> str:
    """把 §29 纯文本报告转成邮件 HTML（22px 正文，兼顾手机与老花阅读）"""
    body = (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
    head = ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body,p,div,td,li{font-size:22px;line-height:1.7;}'
            'pre{font-size:22px;line-height:1.7;white-space:pre-wrap;word-break:break-all;'
            'font-family:inherit;margin:0;}</style></head><body>')
    return (f'{head}<div style="font-size:22px;line-height:1.7;">'
            f'<pre style="font-size:22px;line-height:1.7;white-space:pre-wrap;">{body}</pre>'
            f'</div></body></html>')


def push_mail(text: str, date: str) -> int:
    """经 Agent Mail CLI 推送（HTML 正文）→ 默认 stock1975@qq.com"""
    body_path = os.path.join(BASE_DIR, f'_pg_daily_{date}.html')
    subj = f'【中证2000风险暴露】{date}'
    with open(body_path, 'w', encoding='utf-8') as f:
        f.write(to_html(text, date))
    cmd = ['agently-cli.cmd', 'message', '+send', '--to', MAIL_TO,
           '--subject', subj, '--body-file', body_path, '--confirmed']
    try:
        r = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=180)
        print(r.stdout or '', r.stderr or '')
        return r.returncode
    except Exception as e:
        print(f'[daily] 邮件推送失败: {e}')
        return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description='CSI2000 Position Gate 每日报告')
    ap.add_argument('date', nargs='?', default=None, help='交易日 YYYYMMDD，默认最新')
    ap.add_argument('--push', action='store_true', help='推送邮件')
    ap.add_argument('--rebuild', action='store_true', help='忽略缓存重建特征')
    ap.add_argument('--save-daily', action='store_true', help='同时写出每日 CSV')
    ap.add_argument('--etf-code', default=C.DEFAULT_ETF_CODE, help='中证2000ETF代码，默认 %(default)s')
    ap.add_argument('--account-size', type=float, default=None, help='账户总资产；提供后换算目标份额')
    ap.add_argument('--current-shares', type=int, default=None, help='当前 ETF 持有份额')
    ap.add_argument('--current-position-pct', type=float, default=None, help='当前 ETF 仓位百分比')
    ap.add_argument('--save-plan', action='store_true', help='写出 ETF 次日计划 JSON')
    a = ap.parse_args(argv)

    gate = load_gate(rebuild=a.rebuild)
    text = RP.daily_brief(gate, a.date)
    plan = EP.build_plan(gate, date=a.date, etf_code=a.etf_code,
                         account_size=a.account_size,
                         current_shares=a.current_shares,
                         current_position_pct=a.current_position_pct)
    plan_text = EP.format_plan(plan)
    text = f'{text}\n\n{plan_text}'
    print(text)
    if a.save_daily:
        RP._csv(RP.gate_daily(gate), C.OUT_FILES[0])
    if a.save_plan:
        p = EP.save_plan(plan)
        print(f'\n[daily] ETF计划已写出: {p}')
    if a.push:
        d = a.date or str(gate.index[-1])
        return push_mail(text, d)
    return 0


if __name__ == '__main__':
    sys.exit(main())
