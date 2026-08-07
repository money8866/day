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
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'


def call_deepseek(prompt: str, use_flash: bool = True, system: str = None) -> str:
    """调用 DeepSeek 对精选标的做二次分析（严格基于给定数据，禁止编造）"""
    if not DEEPSEEK_API_KEY:
        print('⚠️ 未配置 DEEPSEEK_API_KEY，跳过 AI 二次分析')
        return ''
    try:
        model = 'deepseek-v4-flash' if use_flash else 'deepseek-v4-pro'
        if system is None:
            system = (
                '你是A股顶级短线投资分析师，严格基于用户提供的数据进行分析，'
                '绝不编造任何数据、新闻、游资动向或外部事件。'
                '股票名称和代码必须严格引用用户提供的数据，不得自行修改或臆造。'
                '输出要求：精炼务实，从"哪些最值得关注/各自看点/风险与止损纪律"三个角度给出结论，'
                '每只股票不超过2句话，末尾给出一句总操作提示。'
            )
        data = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.2,
        }
        resp = requests.post(DEEPSEEK_URL, headers={
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json',
        }, json=data, timeout=120)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'⚠️ DeepSeek 调用失败: {e}')
        return ''


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
    - 仅保留精选标的: S/A级 + 洗盘修复分>=80 + 无兑现冲击
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
    # 排序: 结构增强分(洗盘修复形态+买点质量+动量融合)优先
    sort_col = '结构增强分' if '结构增强分' in df.columns else '洗盘修复分'
    elite = df[
        (df['修正后胜率分级'].isin(['S', 'A'])) &
        (df['洗盘修复分'] >= 80) &
        (df['兑现冲击过滤'].str.contains('✅', na=False))
    ].sort_values(sort_col, ascending=False)

    if len(elite) > 0:
        lines.append('## 精选标的 (S/A + 洗盘修复分≥80 + 无兑现冲击)')
        lines.append('')
        lines.append('| 股票 | 评级 | 洗盘修复分 | 结构增强 | 主题 | 现价 | 止损 | 决策 |')
        lines.append('|------|:----:|:--------:|:------:|------|:---:|:---:|------|')
        for _, r in elite.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            decision = str(r['交易决策'])[:20]
            stop_loss = f"{r['ATR动态止损价']:.2f}" if pd.notna(r.get('ATR动态止损价')) else '-'
            price = f"{r['现价']:.2f}" if pd.notna(r.get('现价')) else '-'
            theme = str(r.get('主题', '')) if pd.notna(r.get('主题')) else '-'
            boost = f"{r['结构增强分']:.0f}" if '结构增强分' in df.columns else '-'
            lines.append(f"| {name} | {r['修正后胜率分级']} | {r['洗盘修复分']:.0f} | {boost} | {theme} | {price} | {stop_loss} | {decision} |")
        lines.append('')

        # ─── DeepSeek 二次分析 ───
        ai_text = _ai_analyze_elite(elite)
        if ai_text:
            lines.append('## 🤖 AI 二次分析 (DeepSeek)')
            lines.append('')
            lines.append(ai_text)
            lines.append('')

    return '\n'.join(lines)


def _ai_analyze_elite(elite: pd.DataFrame) -> str:
    """以顶级超短线交易员视角对精选标的做二次筛选与排序"""
    # 最多分析前10只，避免 token 过长
    top = elite.head(10)
    stock_lines = []
    for _, r in top.iterrows():
        code = str(r['代码']).replace('.SZ', '').replace('.SH', '')
        quant = r.get('量化择时分', 0)
        corrected = r.get('修正后评分', 0)
        boost = r.get('结构增强分', 0)
        buy_point = r.get('推荐买点类型', '')
        theme = r.get('主题', '') if pd.notna(r.get('主题', '')) else '-'
        # 超短线维度: 价格相对VWAP/MA20/筹码峰的位置 + 回踩确认 + 止盈空间 + 大盘状态
        price = r['现价']
        vwap = r.get('VWAP', 0) if pd.notna(r.get('VWAP', 0)) else 0
        ma20 = r.get('MA20', 0) if pd.notna(r.get('MA20', 0)) else 0
        peak = r.get('筹码峰顶', 0) if pd.notna(r.get('筹码峰顶', 0)) else 0
        conc = r.get('筹码集中度%', 0) if pd.notna(r.get('筹码集中度%', 0)) else 0
        pullback = r.get('回踩确认', '') if pd.notna(r.get('回踩确认', '')) else '-'
        target = r.get('ATR跟踪止盈价', 0) if pd.notna(r.get('ATR跟踪止盈价', 0)) else 0
        market = r.get('大盘状态', '') if pd.notna(r.get('大盘状态', '')) else '-'
        rise_gap = (price / vwap - 1) * 100 if vwap else 0       # 现价乖离VWAP
        ma20_gap = (price / ma20 - 1) * 100 if ma20 else 0       # 现价乖离MA20
        upside = (target / price - 1) * 100 if price and target else 0  # 至止盈位空间
        stock_lines.append(
            f"- {r['名称']}({code}) 评级{r['修正后胜率分级']} 量化{quant:.1f} 修复{r['洗盘修复分']:.0f} "
            f"增强{boost:.1f} 主题[{theme}] 现价{price:.2f} 乖离VWAP{rise_gap:+.1f}% 乖离MA20{ma20_gap:+.1f}% "
            f"筹码峰顶{peak:.2f}(集中度{conc:.0f}%) 回踩[{pullback}] 买点[{buy_point}] 止损{r['ATR动态止损价']:.2f} "
            f"止盈{r.get('ATR跟踪止盈价', 0):.2f}(空间{upside:+.1f}%) 大盘[{market}] 决策[{r['交易决策']}]"
        )
    system = (
        '你是A股顶级超短线交易员（隔日/3-5日波段打法），极其严格、纪律优先、胜率至上。'
        '严格基于用户提供的量化数据做二次筛选，绝不编造数据、新闻或消息面。'
        '股票名称和代码必须严格引用用户数据。'
        '输出要求（Markdown格式）：'
        '1.【核心持仓】给出最有把握的1-3只，说明超短线逻辑（买点结构+赔率+大盘环境配合度）；'
        '2.【可参与】次级标的及介入条件；'
        '3.【剔除/回避】明确剔除哪些，理由必须是数据上的（乖离过大、突破未确认、空间不足、大盘弱等）；'
        '4. 每只标注 T+1 关注要点与纪律（止损位、不及预期即走）。'
        '结论要敢排敢砍，宁缺毋滥，禁止含混的"都可以关注"。'
    )
    prompt = (
        "以下为今日中报预增股池洗盘修复专题精选标的（S/A级、洗盘修复分≥80、无兑现冲击），"
        "均为系统初筛结果：\n"
        + "\n".join(stock_lines)
        + "\n\n请以顶级超短线交易员视角进行二次筛选与排序。"
        "重点考察：乖离VWAP/MA20是否透支、筹码峰是否突破、回踩确认是否成立、"
        "到止盈位空间是否足够（赔率）、大盘状态是否支持、买点类型质量。"
    )
    return call_deepseek(prompt, system=system)


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
