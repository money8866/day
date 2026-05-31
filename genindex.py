#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日复盘报告索引生成器
自动扫描各复盘报告目录，生成统一的HTML索引页面
"""

import os
import sys
from datetime import datetime, timedelta

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

REPORT_PATTERNS = [
    {
        'name': 'AI主线ETF日报',
        'prefix': 'AI_ETF_Report_',
        'icon': '&#128202;'
    },
    {
        'name': '每日复盘',
        'prefix': 'Final_Self_',
        'icon': '&#128200;'
    },
]

def get_trade_dates(n_days=20):
    """获取最近N个交易日"""
    today = datetime.now()
    dates = []
    for i in range(n_days * 3):
        date = today - timedelta(days=i)
        if date.weekday() < 5:
            dates.append(date.strftime('%Y%m%d'))
        if len(dates) >= n_days:
            break
    return dates

def scan_reports(trade_dates):
    """扫描report_daily目录的报告文件"""
    if not os.path.exists(REPORT_DIR):
        return {}

    all_reports = {date: [] for date in trade_dates}

    for filename in os.listdir(REPORT_DIR):
        if not filename.endswith('.html'):
            continue

        for config in REPORT_PATTERNS:
            prefix = config['prefix']
            if filename.startswith(prefix) and filename.endswith('.html'):
                date_str = filename[len(prefix):-5]
                if date_str in all_reports:
                    all_reports[date_str].append({
                        'name': config['name'],
                        'icon': config['icon'],
                        'file': os.path.join(REPORT_DIR, filename)
                    })
                break

    return all_reports

def generate_index_html():
    """生成索引HTML页面"""
    trade_dates = get_trade_dates(20)
    all_reports = scan_reports(trade_dates)

    report_rows = ""
    for date in trade_dates:
        reports = all_reports.get(date, [])
        if not reports:
            continue

        report_items = ""
        for r in reports:
            rel_path = os.path.relpath(r['file'], BASE_DIR)
            report_items += f'<a href="{rel_path}" class="report-link">{r["icon"]} {r["name"]}</a> '

        report_rows += f"""
        <tr>
            <td class="date">{date}</td>
            <td>{report_items}</td>
            <td class="time">{datetime.strptime(date, '%Y%m%d').strftime('%Y-%m-%d')}</td>
        </tr>"""

    active_days = len([d for d in trade_dates if all_reports.get(d)])
    total_reports = sum(len(r) for r in all_reports.values())

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日复盘报告索引</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #0f1419;
            color: #e7e9ea;
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            background: linear-gradient(135deg, #1a1f25 0%, #2d333b 100%);
            padding: 40px;
            border-radius: 16px;
            margin-bottom: 30px;
            text-align: center;
        }}
        .header h1 {{ color: #4ade80; font-size: 32px; margin-bottom: 10px; }}
        .header .subtitle {{ color: #8b949e; font-size: 16px; }}
        .stats {{
            display: flex;
            justify-content: center;
            gap: 40px;
            margin-top: 20px;
        }}
        .stat {{
            text-align: center;
        }}
        .stat-value {{ font-size: 28px; font-weight: bold; color: #58a6ff; }}
        .stat-label {{ color: #6e7681; font-size: 14px; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #161b22;
            border-radius: 12px;
            overflow: hidden;
        }}
        th, td {{
            padding: 16px 20px;
            text-align: left;
            border-bottom: 1px solid #30363d;
        }}
        th {{
            background: #1c2128;
            color: #8b949e;
            font-weight: 500;
            font-size: 14px;
        }}
        tr:hover {{ background: #1c2128; }}
        .date {{
            color: #58a6ff;
            font-weight: 500;
            white-space: nowrap;
        }}
        .time {{ color: #6e7681; font-size: 14px; white-space: nowrap; }}
        .report-link {{
            display: inline-block;
            background: #238636;
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 13px;
            margin: 2px 4px;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .report-link:hover {{ background: #2ea043; }}
        .footer {{
            text-align: center;
            color: #6e7681;
            margin-top: 30px;
            padding: 20px;
            font-size: 13px;
        }}
        .update-time {{ color: #4ade80; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 每日复盘报告索引</h1>
            <div class="subtitle">整合A股各维度复盘分析报告</div>
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">{active_days}</div>
                    <div class="stat-label">已生成报告天数</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{total_reports}</div>
                    <div class="stat-label">报告总数</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{len(REPORT_PATTERNS)}</div>
                    <div class="stat-label">报告类型</div>
                </div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>交易日期</th>
                    <th>报告链接</th>
                    <th>日期</th>
                </tr>
            </thead>
            <tbody>
                {report_rows if report_rows else '<tr><td colspan="3" style="text-align:center;color:#6e7681;">暂无报告数据</td></tr>'}
            </tbody>
        </table>

        <div class="footer">
            <p>报告索引自动生成</p>
            <p>更新时间: <span class="update-time">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span></p>
        </div>
    </div>
</body>
</html>"""

    index_file = os.path.join(REPORT_DIR, "index.html")
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(index_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"索引页面已生成: {index_file}")
    return index_file

if __name__ == '__main__':
    generate_index_html()