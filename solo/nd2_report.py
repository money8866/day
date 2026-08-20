# -*- coding: utf-8 -*-
"""
「猎尾V5」报告输出模块
控制台完整明细 + 微信推送格式
"""

from datetime import datetime

PATTERN_CN = {
    'PULLBACK_GAP': '强势基因回调低吸',
    'BREAKOUT_TAIL': '平台突破尾盘',
    'STEALTH_ACCUMULATION': '隐蔽吸筹',
    'OTHER': '其他',
}

GRADE_EMOJI = {'S': '🔴', 'A': '🟢', 'B': '👀', 'REJECT': '❌'}


def format_console_report(signals, top_n=10):
    """控制台输出完整报告(用户指定格式)"""
    if not signals:
        return ''
    now = datetime.now().strftime('%H:%M:%S')
    lines = []
    lines.append('═' * 70)
    lines.append(f'🎯 猎尾V5 NEXT-DAY ALPHA [{now}] 共{len(signals)}只候选')
    lines.append('═' * 70)
    for i, s in enumerate(signals[:top_n], 1):
        d = s.get('detail', {})
        tf = d.get('tailflow', {})
        g = s.get('grade', 'B')
        emoji = GRADE_EMOJI.get(g, '')
        lines.append(f"\n{i}. {s.get('name','')}({s['ts_code']}) [{s.get('theme','')}] {emoji}{g}级 {PATTERN_CN.get(s.get('pattern',''), '?')}")
        lines.append(f"   FinalScore {s['final_score']} | rank {s.get('rank_score',0):.3f} | 涨{s.get('pct_chg',0):+.1f}%")
        lines.append(f"   评分: 趋势{s['trend_structure']}/15 形态{s['pattern_quality']}/15 尾流{s['tail_flow']}/25 "
                     f"基因{s['strong_gene']}/10 ND2 {s['nd2_potential']}/15 主题{s['theme_alpha']}/12 市场{s['market_alpha']}/8 "
                     f"加分+{s['bonus']} 风险-{s['risk_penalty']}")
        p_up = s.get('p_up_2', 0)
        p_close = s.get('p_close_2', 0)
        p_dd = s.get('p_dd_2', 0)
        n_sample = s.get('sample_size', 0)
        conf = s.get('probability_confidence', 0)
        lines.append(f"   概率: P(高≥2%)={p_up:.0%} P(收≥2%)={p_close:.0%} P(破-2%)={p_dd:.0%} "
                     f"置信{conf:.1f} 样本{n_sample}")
        lines.append(f"   尾盘: 量比{tf.get('tail_volume_ratio','-')} 涨{tf.get('tail_return',0):+.1f}% "
                     f"收盘位{tf.get('close_position',0):.0%} 买压{tf.get('buy_pressure_proxy','-')}")
        risk_d = d.get('risk', {})
        if risk_d:
            risk_keys = [k for k in risk_d if k.startswith('risk_') or 'trap' in k or 'distribution' in k]
            if risk_keys:
                lines.append(f"   风险: {'; '.join(str(risk_d[k]) for k in risk_keys[:3])}")
    lines.append('\n' + '═' * 70)
    return '\n'.join(lines)


def format_wechat_message(signals, max_n=5):
    """微信推送格式(S/A级)"""
    sa = [s for s in signals if s.get('grade') in ('S', 'A')]
    if not sa:
        return None
    now = datetime.now().strftime('%H:%M')
    lines = [f"🎯 猎尾V5次日Alpha [{now}] S/A级{len(sa)}只"]
    for s in sa[:max_n]:
        g = s.get('grade')
        p_up = s.get('p_up_2', 0)
        tf = s.get('tail_flow', 0)
        nd2 = s.get('nd2_potential', 0)
        risk = s.get('risk_penalty', 0)
        pat = PATTERN_CN.get(s.get('pattern', ''), '')
        lines.append(
            f"{g}级 {s.get('name','')}({s['ts_code']}) {s['final_score']}分 {pat}\n"
            f"  P(次日高≥2%)={p_up:.0%} 尾流{tf}/25 ND2 {nd2}/15 风险-{risk}\n"
            f"  涨{s.get('pct_chg',0):+.1f}% 量比{s.get('detail',{}).get('tailflow',{}).get('tail_volume_ratio','-')}\n"
            f"  买入参考: 14:50~14:57 现价{s.get('price',0):.2f} 止损-2%"
        )
    return '\n'.join(lines)


def format_wechat_market(market_info):
    """市场环境行"""
    if not market_info:
        return ''
    return (f"市场: {market_info.get('status','')} 乘数{market_info.get('multiplier',1):.2f} "
            f"趋势分{market_info.get('trend_score',0):.0f}")
