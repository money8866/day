# -*- coding: utf-8 -*-
"""复盘PDF生成器 v3 - 按真实HTML结构解析
用法: python _final_pdf_fixed.py [日期]  (不填自动找最新)
"""
import os, re, sys, datetime

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, HRFlowable)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER

FONT = 'Chinese'
for fp in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont(FONT, fp)); break
        except: pass

C = {
    '大盘':    colors.HexColor('#1a3a8a'),
    '主题':    colors.HexColor('#2c5f9e'),
    '强势股':  colors.HexColor('#e74c3c'),
    'ETF':     colors.HexColor('#27ae60'),
    '低吸':    colors.HexColor('#8e44ad'),
    '量能':    colors.HexColor('#e67e22'),
}

def S(name, **kw):
    b = dict(fontName=FONT, leading=14); b.update(kw)
    return ParagraphStyle(name, **b)

Ss = {
    'title':  S('T',  fontSize=20, alignment=TA_CENTER, textColor=C['大盘'], spaceAfter=6),
    'sub':    S('Su', fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor('#888'), spaceAfter=4),
    'h1':     S('H1', fontSize=13, textColor=C['大盘'], spaceBefore=14, spaceAfter=6),
    'h2':     S('H2', fontSize=11, textColor=C['主题'], spaceBefore=8,  spaceAfter=4),
    'body':   S('B',  fontSize=9.5, textColor=colors.HexColor('#222'), leading=15),
    'hl':     S('HL', fontSize=9.5, textColor=colors.HexColor('#c0392b'),
                backColor=colors.HexColor('#fff5f5'), leading=14, borderPadding=4),
    'footer': S('F',  fontSize=8, textColor=colors.HexColor('#aaa')),
}

def sp(h=0.3): return Spacer(1, h*cm)
def hr(c, t=2): return HRFlowable(width='100%', thickness=t, color=c, spaceAfter=3)
def h1(t, c): return [hr(c), Paragraph(t, Ss['h1'])]
def esc(t): return t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
def para(t): return Paragraph(esc(str(t).replace('\n','<br/>')), Ss['body'])

def strip_html(html):
    """HTML → 纯文本，保留换行"""
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&nbsp;',' ').replace('&lt;','<').replace('&gt;','>')
    return html

def extract_section(html, marker):
    """
    在html中找 marker（含 <strong> 标签），返回该段纯文本。
    截取范围：从 marker 所在行开头 到 下一个 top-level 章节标题 或 文件末尾。
    """
    # 找 <strong>marker</strong> 位置
    idx = -1
    for m in re.finditer(r'<strong>([^<]*)</strong>', html):
        if marker in m.group(1):
            idx = m.start(); break
    if idx == -1:
        return ''
    # 找到这一行（<p>或<li>或普通文本）的开始
    line_start = max(0, html.rfind('<p', 0, idx), html.rfind('<li', 0, idx),
                     html.rfind('\n', max(0, idx-200), idx))
    # 找下一个 top-level 章节（独立行、有数字标号或特殊标记）
    search_from = idx + 1
    # 用 findall + search 找下一个章节
    cand_pos = len(html)
    for m in re.finditer(r'<strong>【[^】]+】</strong>', html[search_from:]):
        if m.start() + search_from < cand_pos:
            cand_pos = m.start() + search_from
    for m in re.finditer(r'<strong>\d+[、.][^<]+</strong>', html[search_from:]):
        if m.start() + search_from < cand_pos:
            cand_pos = m.start() + search_from
    return strip_html(html[line_start:cand_pos]).strip()

def latest_html(base):
    fs = [(re.search(r'(\d{8})',f).group(1),f)
          for f in os.listdir(base) if re.match(r'Final_Self_\d{8}\.html$',f)]
    fs.sort(reverse=True)
    return os.path.join(base, fs[0][1]) if fs else None

def trow(data, cw, color, fs=8.5):
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),color), ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('ALIGN',(0,0),(-1,-1),'CENTER'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('FONTNAME',(0,0),(-1,-1),FONT), ('FONTSIZE',(0,0),(-1,-1),fs),
        ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
        ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#f8f9fa'),colors.HexColor('#eef1f5')]),
    ]))
    return t

def g(pat, text):
    m = re.search(pat, text)
    return m.group(1).strip() if m else '—'

def generate(date=None):
    base = r'D:\mystock\report_daily'
    html_path = os.path.join(base, f'Final_Self_{date}.html') if date else latest_html(base)
    if not html_path or not os.path.exists(html_path):
        print('未找到HTML'); return
    m = re.search(r'(\d{8})', html_path); date_str = m.group(1)
    date_disp = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
    pdf_path = os.path.join(base, f'Final_Self_{date_str}.pdf')
    print(f'源: {html_path}')
    html = open(html_path, encoding='utf-8').read()
    story = []
    story.append(sp(0.5))
    story.append(Paragraph('每日复盘报告', Ss['title']))
    story.append(Paragraph(f'{date_disp} 收盘 | 数据来源：AI量化系统', Ss['sub']))
    story.append(hr(C['大盘'], 2)); story.append(sp(0.2))

    # ── 1. 大盘分析 ─────────────────────────────────────────────
    story += h1('一、大盘分析', C['大盘'])
    sec = extract_section(html, '大盘分析')
    # 提取数字指标
    idx   = g(r'上证指数[：:：]\s*([\d,.]+)', sec)
    amt   = g(r'成交额[：:：]\s*([\d,.万亿]+)', sec)
    ratio = g(r'涨跌比\s*(\d+%)', sec)
    mkt   = g(r'市场[：:：]\s*([^\n]+)', sec)
    earn  = g(r'赚钱[：:：]\s*([^\n]+)', sec)
    risk  = g(r'风险[：:：]\s*([^\n]+)', sec)
    rhythm= g(r'节奏[：:：]\s*([^\n]+)', sec)
    pos   = g(r'当前目标[：:：]\s*(\d+%)', sec)
    normal= g(r'正常区间[：:：]\s*([^\n]+)', sec)
    cap   = g(r'确认上限[：:：]\s*(\d+%)', sec)
    one   = g(r'[一-]?\s*句话[：:：]\s*([^\n]+)', sec)
    data = [['指标','数值','指标','数值'],
            ['上证指数',idx,'成交额',amt],
            ['涨跌比',ratio,'市场状态',mkt],
            ['赚钱效应',earn,'风险',risk],
            ['节奏',rhythm,'目标仓位',pos],
            ['正常区间',normal,'确认上限',cap]]
    story.append(trow(data,[3.2*cm,3.3*cm,3.2*cm,3.3*cm],C['大盘']))
    story.append(sp(0.15))
    if one and one != '—':
        story.append(Paragraph('💡 '+esc(one), Ss['hl']))

    # ── 2. 主题分析 ─────────────────────────────────────────────
    story += h1('二、今日主题分析', C['主题'])
    sec = extract_section(html, '主题分析')
    # 找核心主线N块
    ms = list(re.finditer(r'\*\*\s*核心主线(\d+)[：:：]\s*([^\n]+)', sec))
    for i, m in enumerate(ms):
        title = m.group(2).strip()
        start = m.start(); end = ms[i+1].start() if i+1 < len(ms) else len(sec)
        blk = sec[start:end]
        story.append(Paragraph(f'🔹 主线：{esc(title.rstrip("（("))}', Ss['h2']))
        # 子主题
        sub = re.search(r'最佳子主题[】]?[：:：]\s*([^\n]+)', blk)
        if sub: story.append(Paragraph('  子主题：'+esc(sub.group(1).strip()), Ss['body']))
        # 龙头 / 中军
        row = [['角色','标的','要点']]
        for role_lbl in ['龙头','中军']:
            rm = re.search(rf'\【{role_lbl}\】[^\n]+?([\d]{{6}}\.[A-Z]+\s*[^\n（(-]+)', blk)
            if rm:
                rest = blk[blk.find(rm.group(0)):]
                w = re.search(r'建议仓位\s*([\d.]+%)', rest)
                sig = re.search(r'匹配动作[：:：]\s*([^\n（(-]+)', rest)
                pt = f"仓位：{w.group(1)}" if w else "仓位：—"
                if sig: pt += f"｜{sig.group(1).strip()}"
                row.append([role_lbl, esc(rm.group(1).strip()), pt])
        if len(row) > 1:
            story.append(trow(row,[1.6*cm,4.0*cm,9.4*cm],C['主题'],8))
        story.append(sp(0.1))
    rot   = re.search(r'轮动主题[：:：]\s*([^\n*]+)', sec)
    avoid = re.search(r'避免杂毛[：:：]\s*([^\n*]+)', sec)
    if rot:   story.append(Paragraph('🔄 轮动：'+esc(rot.group(1).strip()), Ss['body']))
    if avoid: story.append(Paragraph('⛔ 避免：'+esc(avoid.group(1).strip()), Ss['body']))

    # ── 3. ETF ─────────────────────────────────────────────────
    story += h1('三、ETF操作建议', C['ETF'])
    sec = extract_section(html, 'ETF操作建议')
    if not sec:
        sec = extract_section(html, '【ETF操作建议】')
    sig = re.search(r'操作建议[：:：]\s*([^\n]+)', sec)
    if sig: story.append(Paragraph('操作建议：'+esc(sig.group(1).strip()[:200]), Ss['body']))
    etfs = re.findall(r'(\d+)\.\s*([^\n（(（]+?)\((\d{6})\)\s*动量[：:：]\s*([+\-]?[\d.]+%)', sec)
    if etfs:
        row = [['排名','ETF名称','代码','动量']]
        for rk,nm,code,mv in etfs:
            row.append([rk, esc(nm.strip()), code, mv])
        story.append(sp(0.1)); story.append(trow(row,[1.5*cm,7*cm,3*cm,3.5*cm],C['ETF'],8.5))

    # ── 4. 突破股池（精简版）─────────────────────────────────────
    story += h1('四、今日突破股池分析', C['强势股'])
    sec = extract_section(html, '今日突破股池分析')
    if not sec:
        sec = extract_section(html, '【今日突破股池分析】')
    entries = list(re.finditer(r'【第(\d+)名】([^\n（(（]+?)\s*\((\d{6}\.[A-Z]+)\)', sec))
    if entries:
        row = [['排名','名称','代码','整合评分','失败率','信号']]
        for m in entries:
            rk,nm,code = m.group(1),m.group(2),m.group(3)
            start = m.start()
            end = entries[entries.index(m)+1].start() if entries.index(m)+1 < len(entries) else len(sec)
            blk = sec[start:end]
            sc  = re.search(r'整合评分[：:：]\s*([\d.]+)', blk)
            fr  = re.search(r'失败[概率率][：:：]\s*([\d.]+%)', blk)
            sig = re.search(r'信号[=＝]\s*([A-Za-z0-9%（）() onPullback Buy]+)', blk)
            row.append([rk, esc(nm.strip()), code,
                        (sc.group(1)  if sc  else '—'),
                        (fr.group(1)  if fr  else '—'),
                        (sig.group(1).strip() if sig else '—')])
        story.append(trow(row,[1.1*cm,3.5*cm,2.2*cm,1.8*cm,1.5*cm,4.9*cm],C['强势股'],8))
        story.append(sp(0.1))
        for m in entries:
            rk,nm,code = m.group(1),m.group(2),m.group(3)
            start = m.start()
            end = entries[entries.index(m)+1].start() if entries.index(m)+1 < len(entries) else len(sec)
            blk = sec[start:end]
            sc  = re.search(r'整合评分[：:：]\s*([\d.]+)', blk)
            th  = re.search(r'所属主题[为：:：]\s*([^\n（(（]+)', blk)
            v5  = re.search(r'V5决策[：:：]\s*([^\n]+)', blk)
            line = f"• {esc(nm.strip())} {code}  "
            if sc:  line += f"评分{sc.group(1)}｜"
            if th:  line += f"{th.group(1).strip()}｜"
            if v5:
                v5t = v5.group(1).strip()
                buy = re.search(r'Buy(?: on Pullback)?\((\d+)%\)', v5t)
                sl  = re.search(r'止损[位]?\s*([\d.]+元)', v5t)
                if buy: line += f"Buy{buy.group(1)}%｜"
                if sl:  line += f"止损{sl.group(1)}｜"
            story.append(Paragraph(line.rstrip('｜'), Ss['body']))

    # ── 5. 中报 ─────────────────────────────────────────────────
    story += h1('五、中报优质股池买点', C['低吸'])
    sec = extract_section(html, '中报优质股池买点')
    if not sec:
        sec = extract_section(html, '【中报优质股池买点】')
    txt = re.sub(r'^五、.*','',sec,flags=re.DOTALL).strip() if sec else ''
    story.append(para(txt) if txt else Paragraph('今日无中报优质股池买点信号。', Ss['body']))

    # ── 6. 量能爆发 ─────────────────────────────────────────────
    story += h1('六、今日量能爆发+宽幅震荡池分析', C['量能'])
    sec = ''
    for k in ['今日量能爆发','量能爆发','宽幅震荡池','【今日量能爆发']:
        t = extract_section(html, k)
        if t: sec = t; break
    # 如果全匹配失败，用模糊搜索
    if not sec:
        m = re.search(r'<strong>([^<]*量能[^<]*)</strong>', html)
        if m: sec = extract_section(html, m.group(1).replace('【','').replace('】',''))
    # 强买：<strong>强买N：名称 (代码)</strong>...
    strong = list(re.finditer(r'强买(\d+)[：:：]\s*([^\n（(（]+?)\s*\((\d{6}\.[A-Z]+)\)', sec or ''))
    if strong:
        row = [['#','名称','代码','评分','主题/要点']]
        for m in strong:
            n,nm,code = m.group(1),m.group(2),m.group(3)
            start = m.start(); end = strong[strong.index(m)+1].start() if strong.index(m)+1 < len(strong) else len(sec)
            blk = (sec or '')[start:end]
            sc = re.search(r'评分\s*(\d+)', blk)
            pt = re.search(r'([^\n]+?)(?:。|$)', blk.replace(f'强买{n}：{nm} ({code})','',1))
            row.append([n, esc(nm.strip()), code,
                        (sc.group(1) if sc else '—'),
                        esc((pt.group(1).strip() if pt else blk[:60]).replace(nm,'').replace(code,'').strip()[:50])])
        story.append(Paragraph('🔥 强买信号：', Ss['h2']))
        story.append(trow(row,[0.8*cm,2.6*cm,2.2*cm,1.2*cm,8.2*cm],C['量能'],8))
        story.append(sp(0.1))
    # 观察
    obs = re.search(r'观察信号[：:：]\s*([\s\S]+?)$', sec or '', re.MULTILINE)
    if obs:
        story.append(Paragraph('👀 观察信号：', Ss['h2']))
        obs_lines = [l.strip() for l in obs.group(1).split('\n') if l.strip() and not l.strip().startswith('-')]
        for ln in obs_lines[:5]:
            ln = re.sub(r'^观察\d+[：:：]\s*','',ln).strip()
            if ln: story.append(Paragraph('· '+esc(ln[:80]), Ss['body']))

    story.append(sp(0.4))
    story.append(hr(C['大盘'], 1))
    story.append(Paragraph(
        f'免责声明：本报告仅供参考，不构成投资建议。市场有风险，投资需谨慎。'
        f'生成：{datetime.datetime.now():%Y-%m-%d %H:%M} | QClaw量化系统', Ss['footer']))

    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
        rightMargin=1.8*cm, leftMargin=1.8*cm, topMargin=1.8*cm, bottomMargin=1.8*cm)
    doc.build(story)
    sz = os.path.getsize(pdf_path)
    print(f'PDF: {pdf_path}  ({sz//1024} KB, {sz//1024//1024+.1:.1f} MB)')

if __name__ == '__main__':
    generate(sys.argv[1] if len(sys.argv) > 1 else None)
