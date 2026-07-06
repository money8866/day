# -*- coding: utf-8 -*-
"""
分析final文件中股票的BullScore
"""
import os
import sys

# 添加bull scorer路径
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# 尝试导入tushare
try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except:
    TUSHARE_AVAILABLE = False

def load_stocks():
    """加载股票列表"""
    stock_file = r'D:\mystock\report_daily\final_stocks.txt'
    
    with open(stock_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    source_file = lines[0].strip()
    mtime = lines[1].strip()
    count = int(lines[2].strip())
    stocks = [l.strip() for l in lines[3:] if l.strip()]
    
    print('=' * 60)
    print('从 %s 加载 %d 只股票' % (os.path.basename(source_file), len(stocks)))
    print('=' * 60)
    
    return stocks, source_file

def get_stock_data(ts_code, days=250):
    """获取股票数据"""
    if not TUSHARE_AVAILABLE:
        return None
    
    pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
    
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            return df
    except:
        pass
    
    return None

def calculate_bull_score_simple(ts_code):
    """技术面+趋势综合评分（适配技术信号股）"""
    try:
        if not TUSHARE_AVAILABLE:
            return None, None
        
        pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
        
        # 获取日线数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=300)).strftime('%Y%m%d')
        
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or len(df) < 60:
            return None, None
        
        df = df.sort_values('trade_date').reset_index(drop=True)
        df['pct_chg'] = df['pct_chg'].fillna(0)
        df['close'] = df['close'].astype(float)
        
        close = df['close']
        vol = df['vol'].astype(float)
        pct_chg = df['pct_chg']
        
        # === 技术面评分 (80%权重) ===
        
        # 1. 趋势因子 (25%)
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        
        trend_score = 0
        if ma5.iloc[-1] > ma20.iloc[-1]:
            trend_score += 25
        if ma20.iloc[-1] > ma60.iloc[-1]:
            trend_score += 25
        if close.iloc[-1] > ma5.iloc[-1]:
            trend_score += 10
        if close.iloc[-1] > ma20.iloc[-1]:
            trend_score += 10
        # 三均线多头排列加分
        if ma5.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]:
            trend_score += 10
        
        # 2. 动量因子 (25%)
        mom_20 = (close.iloc[-1] / close.iloc[-20] - 1) * 100 if len(df) >= 20 else 0
        mom_60 = (close.iloc[-1] / close.iloc[-60] - 1) * 100 if len(df) >= 60 else 0
        
        mom_score = 0
        if mom_20 > 30:
            mom_score = 100
        elif mom_20 > 20:
            mom_score = 75 + (mom_20 - 20) * 2.5
        elif mom_20 > 10:
            mom_score = 50 + (mom_20 - 10) * 2.5
        elif mom_20 > 0:
            mom_score = 25 + mom_20 * 2.5
        else:
            mom_score = max(0, 20 + mom_20)
        
        # 3. RSI因子 (15%)
        def calc_rsi(prices, period=6):
            delta = prices.diff()
            gain = delta.where(delta > 0, 0).rolling(period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            return rsi.iloc[-1]
        
        rsi = calc_rsi(close, 6)
        
        if 45 <= rsi <= 60:
            rsi_score = 100  # 最佳区间
        elif 40 <= rsi < 45:
            rsi_score = 85   # 偏低
        elif 60 < rsi <= 70:
            rsi_score = 80  # 略高但可接受
        elif rsi < 40:
            rsi_score = 70  # 超卖
        else:
            rsi_score = 50  # 超买
        
        # 4. 成交量因子 (15%)
        avg_vol_20 = vol.tail(20).mean()
        avg_vol_5 = vol.tail(5).mean()
        vol_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1
        
        if 1.5 <= vol_ratio <= 2.5:
            vol_score = 100  # 温和放量最佳
        elif 1.2 <= vol_ratio < 1.5:
            vol_score = 85
        elif 2.5 < vol_ratio <= 3.5:
            vol_score = 75  # 放量过大
        elif vol_ratio >= 3.5:
            vol_score = 50  # 巨量警惕
        else:
            vol_score = 60  # 缩量
        
        # 5. 相对强度因子 (10%)
        # 比较20日涨幅与行业平均（用全部股票平均替代）
        rel_strength = mom_20  # 简化：直接用动量
        if rel_strength > 20:
            rel_score = 100
        elif rel_strength > 10:
            rel_score = 75 + (rel_strength - 10) * 2.5
        elif rel_strength > 0:
            rel_score = 50 + rel_strength * 2.5
        else:
            rel_score = 40
        
        # 技术总分 (标准化到100)
        tech_total = (
            trend_score * 0.25 +
            mom_score * 0.25 +
            rsi_score * 0.20 +
            vol_score * 0.15 +
            rel_score * 0.15
        )
        
        # === 基本面评分 (20%权重) ===
        fundamental_score = 50  # 默认中等
        try:
            df_fina = pro.fina_indicator(ts_code=ts_code, start_date=start_date, limit=4)
            if df_fina is not None and len(df_fina) > 0:
                latest = df_fina.iloc[0]
                
                # ROE评分 (满分30)
                roe = latest.get('roe')
                if roe and not pd.isna(roe):
                    if roe > 20:
                        roe_s = 30
                    elif roe > 15:
                        roe_s = 25
                    elif roe > 10:
                        roe_s = 18
                    elif roe > 5:
                        roe_s = 10
                    else:
                        roe_s = 0
                else:
                    roe_s = 10
                
                # 净利润增速 (满分40)
                yoy = latest.get('yoyoy')
                if yoy and not pd.isna(yoy):
                    if yoy > 50:
                        yoy_s = 40
                    elif yoy > 30:
                        yoy_s = 35
                    elif yoy > 15:
                        yoy_s = 25
                    elif yoy > 0:
                        yoy_s = 15
                    else:
                        yoy_s = 5
                else:
                    yoy_s = 15
                
                # 毛利率 (满分30)
                gross = latest.get('gross_profit_rate')
                if gross and not pd.isna(gross):
                    if gross > 40:
                        gross_s = 30
                    elif gross > 25:
                        gross_s = 22
                    elif gross > 15:
                        gross_s = 15
                    else:
                        gross_s = 5
                else:
                    gross_s = 10
                
                fundamental_score = roe_s + yoy_s + gross_s
                fundamental_score = min(100, fundamental_score)  # 上限100
        except:
            pass
        
        # === 最终总分 ===
        # 技术面80% + 基本面20%，映射到0-100
        raw_total = tech_total * 0.8 + fundamental_score * 0.2
        final_score = min(100, raw_total)
        
        # 信号判断
        if final_score >= 80:
            signal = '强烈推荐'
        elif final_score >= 70:
            signal = '推荐'
        elif final_score >= 60:
            signal = '关注'
        elif final_score >= 50:
            signal = '中性'
        else:
            signal = '观望'
        
        return round(final_score, 1), signal
        
    except Exception as e:
        print('  计算 %s 失败: %s' % (ts_code, str(e)))
        return None, None

def get_stock_name(ts_code):
    """获取股票名称"""
    if not TUSHARE_AVAILABLE:
        return ts_code
    
    # 从代码提取交易所和代码
    market = 1 if ts_code.endswith('.SZ') or ts_code.endswith('.BJ') else 0
    code = ts_code.split('.')[0]
    
    try:
        pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
        
        # 获取基本信息
        if ts_code.startswith('6'):
            exch = 'SSE'
        elif ts_code.startswith('00') or ts_code.startswith('30'):
            exch = 'SZSE'
        elif ts_code.startswith('8') or ts_code.startswith('4'):
            exch = 'BSE'
        else:
            exch = None
        
        if exch:
            df_basic = pro.stock_basic(exchange=exch, ts_code=ts_code, fields='ts_code,name')
            if df_basic is not None and len(df_basic) > 0:
                name = df_basic.iloc[0].get('name')
                if pd.notna(name) and name:
                    return str(name)
    except:
        pass
    
    return ts_code

def generate_report(stocks, scores):
    """生成文本报告"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    report = []
    report.append('# 📊 Final文件股票BullScore分析')
    report.append('')
    report.append('**日期**: %s' % today)
    report.append('')
    
    # 统计
    valid_scores = [s for s in scores if s[2] is not None]
    report.append('## 📈 统计概览')
    report.append('')
    report.append('- 总股票数: %d' % len(stocks))
    report.append('- 成功分析: %d' % len(valid_scores))
    report.append('')
    
    if valid_scores:
        score_values = [s[2] for s in valid_scores]
        avg_score = sum(score_values) / len(score_values)
        
        strong = [s for s in valid_scores if s[2] >= 80]
        recommend = [s for s in valid_scores if 70 <= s[2] < 80]
        watch = [s for s in valid_scores if 60 <= s[2] < 70]
        wait = [s for s in valid_scores if s[2] < 60]
        
        report.append('- 平均评分: %.1f' % avg_score)
        report.append('- 强烈推荐(≥80): %d只' % len(strong))
        report.append('- 推荐(70-80): %d只' % len(recommend))
        report.append('- 关注(60-70): %d只' % len(watch))
        report.append('- 观望(<60): %d只' % len(wait))
        report.append('')
        
        # TOP推荐
        if strong:
            report.append('## 🔥 强烈推荐 (≥80分)')
            report.append('')
            for name, code, score, signal in sorted(strong, key=lambda x: x[2], reverse=True):
                report.append('- **%s** (%s): %.1f分 [%s]' % (name, code, score, signal))
            report.append('')
        
        if recommend:
            report.append('## ✅ 推荐 (70-80分)')
            report.append('')
            for name, code, score, signal in sorted(recommend, key=lambda x: x[2], reverse=True):
                report.append('- **%s** (%s): %.1f分 [%s]' % (name, code, score, signal))
            report.append('')
        
        if watch:
            report.append('## 👀 关注 (60-70分)')
            report.append('')
            for name, code, score, signal in sorted(watch, key=lambda x: x[2], reverse=True)[:10]:
                report.append('- **%s** (%s): %.1f分 [%s]' % (name, code, score, signal))
            if len(watch) > 10:
                report.append('- ... 还有%d只' % (len(watch) - 10))
            report.append('')
        
        if wait:
            report.append('## ⚠️ 观望 (<60分)')
            report.append('')
            for name, code, score, signal in sorted(wait, key=lambda x: x[2], reverse=True)[:5]:
                report.append('- **%s** (%s): %.1f分 [%s]' % (name, code, score, signal))
            if len(wait) > 5:
                report.append('- ... 还有%d只' % (len(wait) - 5))
            report.append('')
    
    return '\n'.join(report)

def save_report(report_text, today_str):
    """保存报告"""
    output_dir = r'D:\mystock\report_daily'
    
    # 保存markdown
    md_file = os.path.join(output_dir, 'final_analysis_%s.md' % today_str)
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print('报告已保存:', md_file)
    return md_file

def pdf_report(report_text, output_path):
    """生成PDF"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import cm
        from reportlab.lib.enums import TA_LEFT
        
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=1.5*cm,
            bottomMargin=1.5*cm
        )
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=12,
        )
        h2_style = ParagraphStyle(
            'H2Style',
            parent=styles['Heading2'],
            fontSize=13,
            spaceAfter=8,
            spaceBefore=10,
        )
        normal_style = ParagraphStyle(
            'NormalText',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
        )
        
        content = []
        
        # 逐行处理
        lines = report_text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                content.append(Spacer(1, 0.3*cm))
                continue
            
            # 处理markdown格式
            # 移除emoji避免乱码
            line = line.replace('🔥', '').replace('✅', '').replace('👀', '').replace('⚠️', '')
            
            if line.startswith('# '):
                content.append(Paragraph(line[2:], title_style))
            elif line.startswith('## '):
                content.append(Paragraph(line[3:], h2_style))
            else:
                # 移除残留的markdown格式
                line = line.replace('**', '').replace('*', '').replace('- ', '')
                content.append(Paragraph(line, normal_style))
        
        doc.build(content)
        print('PDF已生成:', output_path)
        return True
        
    except Exception as e:
        print('PDF生成失败:', str(e))
        import traceback
        traceback.print_exc()
        return False

def main():
    print('=' * 60)
    print('Final文件股票BullScore分析')
    print('=' * 60)
    
    # 加载股票
    stocks, source_file = load_stocks()
    
    if not stocks:
        print('没有找到股票')
        return
    
    # 分析每只股票
    print()
    print('开始分析...')
    scores = []
    
    for i, ts_code in enumerate(stocks):
        if (i + 1) % 10 == 0:
            print('已分析 %d/%d' % (i + 1, len(stocks)))
        
        try:
            name = get_stock_name(ts_code)
            score, signal = calculate_bull_score_simple(ts_code)
            scores.append((name, ts_code, score, signal))
            
            if score is not None:
                print('  %s (%s): %.1f分 [%s]' % (name, ts_code, score, signal))
            else:
                print('  %s (%s): 分析失败' % (name, ts_code))
            
        except Exception as e:
            print('  %s 分析异常: %s' % (ts_code, str(e)))
            scores.append((ts_code, ts_code, None, None))
    
    # 生成报告
    print()
    print('=' * 60)
    print('生成报告...')
    
    today_str = datetime.now().strftime('%Y%m%d')
    report_text = generate_report(stocks, scores)
    
    # 保存markdown
    md_file = save_report(report_text, today_str)
    
    # 生成PDF
    pdf_file = r'D:\mystock\report_daily\final_analysis_%s.pdf' % today_str
    pdf_success = pdf_report(report_text, pdf_file)
    
    print()
    print('=' * 60)
    print('完成!')
    print('=' * 60)
    
    return pdf_file if pdf_success else None

if __name__ == '__main__':
    result = main()
    if result:
        print()
        print('PDF路径:', result)
