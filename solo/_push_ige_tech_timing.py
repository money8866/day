# -*- coding: utf-8 -*-
"""IGE 优秀股技术面择时 手机版要点推送（PushPlus）
数据源：ige/output/ige_v12_tech_timing_<date>.csv（价格/均线只引用 CSV 数值）
用法：
    python _push_ige_tech_timing.py --date 20260924   # 指定日期
    python _push_ige_tech_timing.py                   # 默认最新 CSV
输出：PushPlus 推送微信 + 存档 report_daily/ige_tech_timing_指令_<date>.md
"""
import glob
import os
import sys

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv('d:/mystock/config/.env')

OUTDIR = r"d:\mystock\solo\ige\output"
ARCHIVE_DIR = r"d:\mystock\solo\report_daily"
PUSHPLUS_TOKEN = os.getenv('PUSHPLUS')

TIER_META = {
    '主升/强势': '持有；回踩MA5/MA10分批低吸，不追阳',
    '缩量回踩低吸区': '低吸带MA60~MA20；缩量企稳分批，收盘破MA60离场',
    '强势整理': '回踩MA10低吸；收盘破MA20离场',
    '强势-乖离过大': '不追高；回踩MA10/MA20分批，破MA10减',
    '贴MA60-待突破': '放量收复MA60转右侧；缩量回踩MA20不破可低吸',
}
AVOID_TIERS = ['破位-急跌', '下跌中-勿接刀', 'MA60下-下行整理', '贴MA60-防破位']
WATCH_DISCIPLINE = '统一纪律：反弹MA10不过先减，未收复关键均线前不加仓不抢反弹。'
AVOID_DISCIPLINE = '统一纪律：不接刀；2日不创新低且收复MA10/MA20再评估。'


def f2(x) -> str:
    try:
        v = float(x)
        return '-' if pd.isna(v) else f'{v:.2f}'
    except (TypeError, ValueError):
        return '-'


def push_to_wechat(msg: str, title: str) -> bool:
    if not PUSHPLUS_TOKEN:
        print('错误: 未设置 PUSHPLUS 环境变量')
        return False
    try:
        resp = requests.post('https://www.pushplus.plus/send', json={
            'token': PUSHPLUS_TOKEN,
            'title': title,
            'content': msg,
            'template': 'html',
        }, timeout=30)
        result = resp.json()
        if result.get('code') == 200:
            print(f'✅ 推送成功: {result.get("msg", "")}')
            return True
        print(f'⚠️ 推送失败: code={result.get("code")} msg={result.get("msg")}')
        return False
    except Exception as e:
        print(f'⚠️ 推送异常: {e}')
        return False


def find_csv(date: str) -> str:
    if date:
        return os.path.join(OUTDIR, f'ige_v12_tech_timing_{date}.csv')
    cands = sorted(glob.glob(os.path.join(OUTDIR, 'ige_v12_tech_timing_*.csv')))
    return cands[-1] if cands else ''


def stock_line(r) -> str:
    phase = str(r['phase'])
    base = f"{r['name']} {f2(r['close'])}"
    if phase == '主升/强势':
        return f"{base}｜MA5 {f2(r['ma5'])} MA10 {f2(r['ma10'])}"
    if phase == '缩量回踩低吸区':
        return f"{base}｜MA60 {f2(r['ma60'])} MA20 {f2(r['ma20'])}"
    if phase in ('强势整理', '强势-乖离过大'):
        return f"{base}｜MA10 {f2(r['ma10'])} MA20 {f2(r['ma20'])}"
    if phase == '贴MA60-待突破':
        return f"{base}｜MA20 {f2(r['ma20'])} MA60 {f2(r['ma60'])}"
    return base


def build_lines(df: pd.DataFrame, date: str) -> list:
    lines = [f'IGE优秀股技术面择时 {date}', f'池共{len(df)}只（t120_rocket_core）', '═ 可操作档 ═']
    rest = df[~df['phase'].isin(TIER_META) & ~df['phase'].isin(AVOID_TIERS)]
    for tier, action in TIER_META.items():
        sub = df[df['phase'] == tier]
        if sub.empty:
            continue
        lines.append(f'◆ {tier} {len(sub)}只（{action}）')
        lines.extend(stock_line(r) for _, r in sub.iterrows())
    if not rest.empty:
        vc = rest['phase'].value_counts()
        detail = '/'.join(f'{k}{v}' for k, v in vc.items())
        lines.append(f'── 观察档 {len(rest)}只 ──')
        lines.append(detail)
        lines.append(WATCH_DISCIPLINE)
    av = df[df['phase'].isin(AVOID_TIERS)]
    if not av.empty:
        vc = av['phase'].value_counts()
        detail = '/'.join(f'{k}{v}' for k, v in vc.items())
        lines.append(f'── 规避/破位档 {len(av)}只 ──')
        lines.append(detail)
        lines.append(AVOID_DISCIPLINE)
    return lines


def main():
    date = ''
    if '--date' in sys.argv:
        date = sys.argv[sys.argv.index('--date') + 1]
    path = find_csv(date)
    if not path or not os.path.exists(path):
        print('未找到 ige_v12_tech_timing CSV，跳过推送')
        return
    date = os.path.basename(path)[len('ige_v12_tech_timing_'):-len('.csv')]
    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        print('CSV 为空，跳过推送')
        return
    print(f'读取: {os.path.basename(path)} 共{len(df)}只')

    lines = build_lines(df, date)
    body = '\n'.join(lines)
    print(f'正文 {len(body)} 字')
    if len(body) > 1200:
        print('⚠️ 正文超1200字，未推送（请检查档位数量）')
        return

    html = '<br>'.join(lines)
    title = f'IGE优秀股技术面择时 {date}'
    ok = push_to_wechat(html, title)

    archive = os.path.join(ARCHIVE_DIR, f'ige_tech_timing_指令_{date}.md')
    with open(archive, 'w', encoding='utf-8') as f:
        f.write(body + '\n')
    print(f'✅ 指令已保存: {archive}')
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
