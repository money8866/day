"""
B浪策略CSV转PDF
"""
import os, csv, tushare as ts
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm

TS_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    FONT_NAME = 'SimHei'
except:
    FONT_NAME = 'Helvetica'

def get_names(codes):
    names = {}
    try:
        df = pro.stock_basic(ts_code=','.join(codes), fields='ts_code,name')
        if df is not None:
            for _, r in df.iterrows():
                names[r['ts_code']] = r['name']
    except:
        pass
    return names

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_125355_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_125355_qualified.pdf'
    
    # 读取CSV
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        data = list(csv.DictReader(f))
    
    print(f'读取到 {len(data)} 条记录')
    
    # 按股票代码排序
    data.sort(key=lambda x: (x['ts_code'], str(x.get('launch_date', ''))))
    
    # 获取股票名称
    names = get_names(list(set([r['ts_code'] for r in data])))
    
    # 生成PDF
    c = canvas.Canvas(pdf_file, pagesize=A4)
    w, h = A4
    
    lm, rm, tm, bm = 1.5*cm, 1.5*cm, 2*cm, 2*cm
    cols = [
        (lm, 1*cm, '排名'),
        (lm+1*cm, 2*cm, '代码'),
        (lm+3*cm, 2.5*cm, '名称'),
        (lm+5.5*cm, 2*cm, '信号日期'),
        (lm+7.5*cm, 1.5*cm, 'B浪评分'),
        (lm+9*cm, 1.5*cm, '信号类型'),
        (lm+10.5*cm, 1.5*cm, 'A浪%'),
        (lm+12*cm, 1.5*cm, 'B浪%'),
        (lm+13.5*cm, 1.5*cm, '未来1d%'),
    ]
    
    page_num = 1
    y = h - tm
    
    # 页眉
    c.setFont(FONT_NAME, 10)
    c.drawString(lm, h-1.2*cm, 'B浪策略合格股票池报告')
    c.setFont(FONT_NAME, 8)
    c.drawString(w-rm-4*cm, h-1.2*cm, f'股票数: {len(data)}')
    y -= 1*cm
    
    # 表头
    c.setFont(FONT_NAME, 8)
    for x, _, title in cols:
        c.drawString(x, y, title)
    y -= 0.5*cm
    
    # 数据行
    c.setFont(FONT_NAME, 7)
    for i, row in enumerate(data, 1):
        if y < bm + 0.5*cm:
            c.setFont(FONT_NAME, 8)
            c.drawString(w/2-1*cm, 1*cm, f'- {page_num} -')
            c.showPage()
            page_num += 1
            y = h - tm
            c.setFont(FONT_NAME, 10)
            c.drawString(lm, h-1.2*cm, f'B浪策略报告 - 第{page_num}页')
            y -= 1*cm
            c.setFont(FONT_NAME, 8)
            for x, _, title in cols:
                c.drawString(x, y, title)
            y -= 0.5*cm
            c.setFont(FONT_NAME, 7)
        
        ts_code = row.get('ts_code', '')
        c.drawString(cols[0][0], y, str(i))
        c.drawString(cols[1][0], y, ts_code[:10])
        c.drawString(cols[2][0], y, names.get(ts_code, ts_code)[:8])
        c.drawString(cols[3][0], y, str(row.get('launch_date', '')))
        c.drawString(cols[4][0], y, str(row.get('bwave_score', ''))[:6])
        c.drawString(cols[5][0], y, str(row.get('signal_type', ''))[:8])
        c.drawString(cols[6][0], y, str(row.get('a_gain', ''))[:6])
        c.drawString(cols[7][0], y, str(row.get('b_drop', ''))[:6])
        c.drawString(cols[8][0], y, str(row.get('return_1d', ''))[:6])
        y -= 0.4*cm
    
    c.setFont(FONT_NAME, 8)
    c.drawString(w/2-1*cm, 1*cm, f'- {page_num} -')
    c.save()
    
    print(f'✓ PDF生成成功: {pdf_file}')

if __name__ == '__main__':
    main()
