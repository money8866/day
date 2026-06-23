# -*- coding: utf-8 -*-
"""二波形态精选报告 PDF 生成"""
import os, sys, datetime

SCRIPT_DIR = r'C:\Users\kongx\.qclaw\skills\pdf\scripts'
sys.path.insert(0, SCRIPT_DIR)
from setup_chinese_pdf import setup_chinese_pdf

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                  Table, TableStyle, KeepTogether, HRFlowable)
from reportlab.lib.units import mm

cn_font, styles = setup_chinese_pdf()
OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'

COLOR_BG_HEADER  = colors.HexColor('#1a2a4a')
COLOR_BG_S       = colors.HexColor('#d4edda')
COLOR_BG_DEFAULT = colors.HexColor('#f1f3f5')
COLOR_ACCENT     = colors.HexColor('#0d47a1')
COLOR_DARK       = colors.HexColor('#1a1a2e')
COLOR_MID        = colors.HexColor('#4a4a6a')
COLOR_GREEN      = colors.HexColor('#155724')
COLOR_ORANGE     = colors.HexColor('#b35900')

W = A4[0]
MARGIN = 18 * mm


def ms(name, **kw):
    base = styles[name] if name in styles else styles['Normal']
    return ParagraphStyle('_' + name, parent=base, **kw)


def p(text, name='Normal', **kw):
    return Paragraph(text, ms(name, **kw))


def hr(thick=1.5, color=COLOR_ACCENT):
    return HRFlowable(width='100%', thickness=thick, color=color, spaceAfter=4)


def stat_card(label, value, unit='', color=COLOR_ACCENT):
    rows = [
        [p(label, '_stat_l', fontSize=8, textColor=COLOR_MID, alignment=TA_CENTER)],
        [p(str(value), '_stat_v', fontSize=18, textColor=color, fontName=cn_font, alignment=TA_CENTER)],
        [p(unit,       '_stat_u', fontSize=8, textColor=COLOR_MID, alignment=TA_CENTER)],
    ]
    tbl = Table(rows, colWidths=[46*mm])
    tbl.setStyle(TableStyle([
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return tbl


def summary_table(df):
    header_s = ms('_h', fontSize=8, textColor=colors.white, alignment=TA_CENTER)
    cell_s   = ms('_c', fontSize=8, alignment=TA_CENTER)
    headers = ['代码', '形态', '入场信号', '一波▲', '回调▼',
               '调整天数', 'RSI', '入场价', '止损', '目标', '盈亏比', '二波状态']

    rows = []
    for _, r in df.iterrows():
        w2_color = COLOR_GREEN if r.get('wave2_confirmed') else COLOR_MID
        w2_str = f"<b>+{r['wave2_gain']:.0f}%</b>" if r.get('wave2_confirmed') else '待确认'
        rows.append([
            p(str(r['ts_code']), '_cc'),
            p(str(r['pattern']), '_cc'),
            p(str(r.get('signal_desc','')), '_cc'),
            p(f"+{r['wave1_gain']:.1f}%", '_cc'),
            p(f"-{r['pullback_pct']:.1f}%", '_cc'),
            p(f"{r['adjust_days']}天", '_cc'),
            p(f"{r['rsi']:.0f}", '_cc'),
            p(f"{r['entry_price']:.2f}", '_cc'),
            p(f"{r['stop_loss']:.2f}", '_cc'),
            p(f"{r['target']:.2f}", '_cc'),
            p(f"{r['rr']:.1f}x", '_cc'),
            p(w2_str, '_cc2', textColor=w2_color, fontName=cn_font),
        ])

    col_w = [22*mm, 18*mm, 24*mm, 14*mm, 14*mm, 16*mm, 12*mm,
             18*mm, 16*mm, 16*mm, 16*mm, 20*mm]

    tbl = Table([[p(h, '_h') for h in headers]] + rows, colWidths=col_w)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_BG_HEADER),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, COLOR_BG_DEFAULT]),
        ('GRID',       (0, 0), (-1, -1), 0.3, colors.HexColor('#cccccc')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        # 二波确认高亮
        *[('BACKGROUND', (0, 1+i), (-1, 1+i), COLOR_BG_S)
          for i, confirmed in enumerate(df['wave2_confirmed']) if confirmed],
    ]))
    return tbl


def pattern_legend():
    items = [
        ('⭐⭐⭐⭐⭐', '强势横盘（沪深300 98.6%）',
         '一波拉升>20%后，回调<10%，调整<15天，量能萎缩。'
         '入场信号：RSI<50+缩量，或MACD金叉+MA20上方。止损-3%，目标+30%。'),
        ('⭐⭐⭐⭐', '深度回调（双创板 92.0%）',
         '一波拉升>20%后，深度回调>20%，调整>10天。'
         '入场信号：RSI<30超卖，或量能萎缩+RSI<50。止损-5%，目标+25%。'),
    ]
    rows = []
    for star, name, desc in items:
        rows.append([
            p(star, '_sl', fontSize=14, alignment=TA_CENTER),
            p(name, '_nl', fontSize=10, fontName=cn_font, textColor=COLOR_ACCENT),
            p(desc, '_dl', fontSize=8, leading=12),
        ])
    tbl = Table(rows, colWidths=[18*mm, 38*mm, None])
    tbl.setStyle(TableStyle([
        ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [COLOR_BG_S, colors.HexColor('#fff3cd')]),
        ('GRID',       (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return tbl


def build_pdf(df, out_path):
    today = datetime.date.today()
    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)

    total     = len(df)
    confirmed = int(df['wave2_confirmed'].sum()) if 'wave2_confirmed' in df.columns else 0
    sideways  = int((df['pattern'] == '强势横盘').sum())
    deep      = int((df['pattern'] == '深度回调').sum())
    avg_rr    = df['rr'].mean() if 'rr' in df.columns else 0
    avg_gain  = df['wave1_gain'].mean() if 'wave1_gain' in df.columns else 0

    story = []

    # 标题
    story.append(Spacer(1, 3*mm))
    story.append(p('二波形态精选报告', '_tt',
                   fontSize=20, textColor=COLOR_DARK,
                   fontName=cn_font, alignment=TA_CENTER, spaceAfter=2))
    story.append(p(f'强势横盘 · 深度回调 | {today.strftime("%Y-%m-%d")}',
                   '_sub', fontSize=10, textColor=COLOR_MID, alignment=TA_CENTER))
    story.append(hr(thick=2, color=COLOR_ACCENT))
    story.append(Spacer(1, 3*mm))

    # 统计卡片
    stat_row = [
        stat_card('精选数量', total, '只'),
        stat_card('已二波确认', confirmed, '只', COLOR_GREEN),
        stat_card('强势横盘', sideways, '只', COLOR_ACCENT),
        stat_card('深度回调', deep, '只', COLOR_ORANGE),
        stat_card('平均盈亏比', f'{avg_rr:.1f}', 'x', COLOR_ORANGE),
    ]
    stat_tbl = Table([stat_row], colWidths=[46*mm]*5, rowHeights=[22*mm])
    stat_tbl.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#dddddd')),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8f9fa')),
    ]))
    story.append(stat_tbl)
    story.append(Spacer(1, 5*mm))

    # 形态说明
    story.append(p('形态说明', '_sec', fontSize=13, textColor=COLOR_ACCENT,
                   spaceBefore=2, spaceAfter=2))
    story.append(hr(thick=1.5, color=COLOR_ACCENT))
    story.append(pattern_legend())
    story.append(Spacer(1, 5*mm))

    # 信号表
    story.append(p(f'精选信号（共 {total} 只）', '_sec',
                   fontSize=13, textColor=COLOR_ACCENT,
                   spaceBefore=2, spaceAfter=2))
    story.append(hr(thick=1.5, color=COLOR_ACCENT))
    story.append(summary_table(df))
    story.append(Spacer(1, 5*mm))

    # 操作建议
    story.append(p('操作建议', '_sec', fontSize=13, textColor=COLOR_ACCENT,
                   spaceBefore=2, spaceAfter=2))
    story.append(hr(thick=1.5, color=COLOR_ACCENT))
    rec_rows = []
    for _, r in df.iterrows():
        pat = r['pattern']
        sl_pct = '3%' if pat == '强势横盘' else '5%'
        tgt_pct = '30%' if pat == '强势横盘' else '25%'
        rec = (f"买入价{r['entry_price']:.2f}，止损{r['stop_loss']:.2f}（-{sl_pct}），"
               f"目标{r['target']:.2f}（+{tgt_pct}），盈亏比{r['rr']:.1f}x。"
               f"二波{'已确认+' + str(r['wave2_gain']) + '%' if r.get('wave2_confirmed') else '待确认（持有观察）'}。")
        rec_rows.append([
            p(str(r['ts_code']), '_rc', fontSize=9, fontName=cn_font),
            p(rec, '_rd', fontSize=8, leading=12),
        ])
    rec_tbl = Table(rec_rows, colWidths=[24*mm, None])
    rec_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, COLOR_BG_DEFAULT]),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#dddddd')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        *[('BACKGROUND', (0, 1+i), (-1, 1+i), COLOR_BG_S)
          for i, confirmed in enumerate(df['wave2_confirmed']) if confirmed],
    ]))
    story.append(rec_tbl)
    story.append(Spacer(1, 5*mm))

    # 风险提示
    story.append(p('风险提示', '_sec', fontSize=13, textColor=COLOR_ACCENT,
                   spaceBefore=2, spaceAfter=2))
    story.append(hr(thick=1.5, color=COLOR_ACCENT))
    story.append(p(
        '1. 本报告仅供技术分析参考，不构成投资建议，盈亏自负。'
        '  2. 止损纪律：强势横盘止损-3%，深度回调止损-5%，跌破即出。'
        '  3. 单只仓位≤10%，组合持仓不超过5只，分散风险。'
        '  4. RSI超卖信号优先用于双创板；沪深300/主板优先MACD金叉+MA20信号。'
        '  5. 二波确认（持有后20日涨幅>10%）是最强信号，建议加仓。',
        '_note', fontSize=8, leading=13, textColor=colors.HexColor('#666666')
    ))
    story.append(Spacer(1, 3*mm))
    story.append(p(
        f'报告生成: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | '
        f'数据: Tushare | 模型: 二波形态精选 v1.0 | 回测成功率不代表实盘',
        '_foot', fontSize=7, textColor=colors.HexColor('#aaaaaa'), alignment=TA_CENTER
    ))

    doc.build(story)
    print(f'PDF已生成: {out_path}')


if __name__ == '__main__':
    # 从测试扫描结果构建今日精选
    data = [
        {'ts_code':'688041.SH','pattern':'强势横盘','signal_desc':'MACD金叉+MA20上方',
         'wave1_gain':24.5,'pullback_pct':3.5,'adjust_days':1,'rsi':62.8,
         'vol_ratio':0.6,'entry_price':252.55,'stop_loss':244.97,'target':328.32,
         'rr':10.0,'wave2_gain':23.7,'wave2_confirmed':True,'confidence':'⭐⭐⭐⭐⭐'},
        {'ts_code':'688787.SH','pattern':'强势横盘','signal_desc':'RSI<50+缩量',
         'wave1_gain':27.0,'pullback_pct':7.0,'adjust_days':4,'rsi':41.0,
         'vol_ratio':0.7,'entry_price':148.48,'stop_loss':144.03,'target':193.02,
         'rr':10.0,'wave2_gain':0.0,'wave2_confirmed':False,'confidence':'⭐⭐⭐⭐'},
        {'ts_code':'688981.SH','pattern':'强势横盘','signal_desc':'RSI<50+缩量',
         'wave1_gain':26.3,'pullback_pct':8.9,'adjust_days':13,'rsi':30.8,
         'vol_ratio':0.75,'entry_price':121.92,'stop_loss':118.26,'target':158.50,
         'rr':10.0,'wave2_gain':0.0,'wave2_confirmed':False,'confidence':'⭐⭐⭐⭐'},
        {'ts_code':'688629.SH','pattern':'深度回调','signal_desc':'RSI<30超卖',
         'wave1_gain':36.2,'pullback_pct':20.5,'adjust_days':11,'rsi':28.8,
         'vol_ratio':0.65,'entry_price':102.10,'stop_loss':96.99,'target':127.62,
         'rr':10.2,'wave2_gain':29.3,'wave2_confirmed':True,'confidence':'⭐⭐⭐⭐⭐'},
        {'ts_code':'603163.SH','pattern':'深度回调','signal_desc':'量能萎缩+RSI<50',
         'wave1_gain':30.8,'pullback_pct':24.5,'adjust_days':13,'rsi':32.8,
         'vol_ratio':0.68,'entry_price':96.85,'stop_loss':92.01,'target':121.06,
         'rr':5.0,'wave2_gain':0.0,'wave2_confirmed':False,'confidence':'⭐⭐⭐⭐'},
        {'ts_code':'603993.SH','pattern':'强势横盘','signal_desc':'RSI<50+缩量',
         'wave1_gain':24.6,'pullback_pct':6.8,'adjust_days':5,'rsi':48.9,
         'vol_ratio':0.72,'entry_price':19.49,'stop_loss':18.91,'target':25.34,
         'rr':10.0,'wave2_gain':0.0,'wave2_confirmed':False,'confidence':'⭐⭐⭐⭐'},
        {'ts_code':'002192.SZ','pattern':'强势横盘','signal_desc':'RSI<50+缩量',
         'wave1_gain':22.7,'pullback_pct':6.6,'adjust_days':5,'rsi':46.4,
         'vol_ratio':0.70,'entry_price':84.90,'stop_loss':82.35,'target':110.37,
         'rr':10.0,'wave2_gain':0.0,'wave2_confirmed':False,'confidence':'⭐⭐⭐⭐'},
        {'ts_code':'301128.SZ','pattern':'深度回调','signal_desc':'量能萎缩+RSI<50',
         'wave1_gain':24.7,'pullback_pct':39.3,'adjust_days':16,'rsi':33.4,
         'vol_ratio':0.62,'entry_price':123.78,'stop_loss':117.59,'target':154.72,
         'rr':5.0,'wave2_gain':0.0,'wave2_confirmed':False,'confidence':'⭐⭐⭐⭐'},
    ]
    df = pd.DataFrame(data)
    df = df.sort_values(['wave2_confirmed','wave2_gain','rr'], ascending=[False,False,False]).reset_index(drop=True)

    today_str = datetime.date.today().strftime('%Y%m%d')
    out = os.path.join(OUT_DIR, f'二波形态精选报告_{today_str}.pdf')
    build_pdf(df, out)
