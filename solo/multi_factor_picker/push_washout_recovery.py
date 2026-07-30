# -*- coding: utf-8 -*-
"""
洗盘修复专题 — 每日微信推送 (PushPlus)
=========================================
读取最新 enhanced_timing_bull_all 报告，推送调整充分的二波潜力股到微信
"""
import os, sys, re
import pandas as pd
import requests
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv('d:/mystock/config/.env')

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
PUSHPLUS_TOKEN = os.getenv('PUSHPLUS')
PUSHPLUS_URL = 'https://www.pushplus.plus/send'


def find_latest_report() -> str:
    """找最新的 enhanced_timing_bull_all CSV"""
    files = [f for f in os.listdir(REPORT_DIR) if f.startswith('enhanced_timing_bull_all_') and f.endswith('.csv')]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(REPORT_DIR, files[0])


def format_cash(val) -> str:
    """格式化金额显示"""
    try:
        v = float(val)
        if v >= 10000:
            return f'{v/10000:.0f}万亿'
        elif v >= 1000:
            return f'{v:.0f}'
        return f'{v:.2f}'
    except:
        return '-'


def build_wechat_msg(df: pd.DataFrame, trade_date: str) -> str:
    """
    构建微信推送的 Markdown 消息
    - S/A级中洗盘修复分>=80的股票
    - 洗盘修复专题前15只
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    lines.append(f'# 中报预增股择时算法 — 洗盘修复专题')
    lines.append(f'报告日期: {trade_date} | 推送时间: {now}')
    lines.append('')
    lines.append('> **说明**: 洗盘修复分=洗盘形态完整度(满分100)，排名仅代表形态标准程度。')
    lines.append('> 综合评级(S/A/B)看的是趋势/动量/量价等7个因子综合得分，才是真正的强弱排序。')
    lines.append('> 所以修复分高但评级低的(如100分A级)，说明刚启动不久、低吸安全；修复分略低但评级高的(如S级)，说明趋势已确立、确定性更强。')
    lines.append('')

    # ─── 精选标的 (S/A级且洗盘修复分>=80, 无兑现冲击) ───
    elite = df[
        (df['修正后胜率分级'].isin(['S', 'A'])) &
        (df['洗盘修复分'] >= 80) &
        (df['兑现冲击过滤'].str.contains('✅', na=False))
    ].sort_values('洗盘修复分', ascending=False)

    if len(elite) > 0:
        lines.append('## 精选标的 (S/A + 洗盘修复分≥80 + 无兑现冲击)')
        lines.append('')
        lines.append('| 股票 | 评级 | 洗盘修复分 | 主题 | 现价 | 止损 | 决策 |')
        lines.append('|------|:----:|:--------:|------|:---:|:---:|------|')
        for _, r in elite.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            decision = str(r['交易决策'])[:20]
            stop_loss = f"{r['ATR动态止损价']:.2f}" if pd.notna(r.get('ATR动态止损价')) else '-'
            price = f"{r['现价']:.2f}" if pd.notna(r.get('现价')) else '-'
            theme = str(r.get('主题', '')) if pd.notna(r.get('主题')) else '-'
            lines.append(f"| {name} | {r['修正后胜率分级']} | {r['洗盘修复分']:.0f} | {theme} | {price} | {stop_loss} | {decision} |")
        lines.append('')

    # ─── 洗盘修复专题前15 ───
    wr_top = df[df['洗盘修复分'] >= 80].sort_values('洗盘修复分', ascending=False).head(15)
    if len(wr_top) > 0:
        lines.append('')
        lines.append('## 洗盘修复专题 TOP 15')
        lines.append('')
        lines.append('| 股票 | 修复分 | 评级 | 主题 | 标签 |')
        lines.append('|------|:------:|:----:|------|:----:|')
        for _, r in wr_top.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            tag = str(r.get('洗盘修复标签', '')) if pd.notna(r.get('洗盘修复标签')) else ''
            theme = str(r.get('主题', '')) if pd.notna(r.get('主题')) else '-'
            lines.append(f"| {name} | {r['洗盘修复分']:.0f} | {r['修正后胜率分级']} | {theme} | {tag} |")
        lines.append('')

    # ─── 统计信息 ───
    total_wr = (df['洗盘修复分'] >= 80).sum()
    total_elite = len(elite)
    lines.append('')
    lines.append('---')
    lines.append(f'- 洗盘修复分≥80: {total_wr} 只')
    lines.append(f'- 精选S/A级标的: {total_elite} 只')
    lines.append(f'- 选股范围: {len(df)} 只')
    lines.append('')

    return '\n'.join(lines)


def push_to_wechat(msg: str, title: str = None) -> bool:
    """通过 PushPlus 推送到微信"""
    if not PUSHPLUS_TOKEN:
        print('错误: 未设置 PUSHPLUS 环境变量')
        return False

    if not title:
        title = f'洗盘修复专题 — {datetime.now().strftime("%Y%m%d")}'

    try:
        resp = requests.post(PUSHPLUS_URL, json={
            'token': PUSHPLUS_TOKEN,
            'title': title,
            'content': msg,
            'template': 'markdown',
        }, timeout=15)
        result = resp.json()
        if result.get('code') == 200:
            print(f'推送成功: {result.get("msg", "")}')
            return True
        else:
            print(f'推送失败: code={result.get("code")} msg={result.get("msg")}')
            return False
    except Exception as e:
        print(f'推送异常: {e}')
        return False


def main():
    report_path = find_latest_report()
    if not report_path:
        print('未找到增强择时报告')
        return

    print(f'读取报告: {report_path}')

    df = pd.read_csv(report_path, encoding='utf-8-sig')

    # 从文件名提取交易日期
    match = re.search(r'enhanced_timing_bull_all_(\d{8})\.csv', os.path.basename(report_path))
    trade_date = match.group(1) if match else '未知'

    # 构建消息
    msg = build_wechat_msg(df, trade_date)
    print(msg[:500] + '...' if len(msg) > 500 else msg)

    # 推送
    success = push_to_wechat(msg, title=f'中报预增股择时算法 — 洗盘修复专题 {trade_date}')
    if success:
        print(f'微信推送完成: {trade_date}')
    else:
        print('微信推送失败')


if __name__ == '__main__':
    main()
