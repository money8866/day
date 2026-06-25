#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
二波形态信号 → 主题筛选器
对 wave2_pattern_scanner.py 输出的信号列表,使用非一日游确认主题 + 主题成份股映射进行交叉筛选。

逻辑:
1. 从 theme_trend_sentiment_score 的数据库读取非一日游确认主题
2. 从 build_theme_stock_map 输出的 JSON 读取主题-成份股映射
3. 对每只信号股票,查其所属主题,标记是否属于非一日游确认主题
4. 输出带主题标注的筛选结果(CSV + PDF)

使用方式:
  python wave2_theme_filter.py --input output/wave2_pattern_bull_stocks_20260624.json [--pdf] [--today]
"""
import os
import sys
import json
import argparse
import sqlite3
from datetime import datetime
from reportlab.lib.units import mm
from collections import defaultdict

# Windows GBK 控制台修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)  # D:\mystock\solo -> D:\mystock

# 非一日游数据库(在solo目录下)
THEME_DB = os.path.join(PARENT_DIR, "cache_backbone_tushare", "theme_trend_sentiment.db")
# 主题-成份股映射(在mystock根目录下)
MYSTOCK_DIR = os.path.dirname(PARENT_DIR)  # D:\mystock
THEME_MAP_PATH = os.path.join(MYSTOCK_DIR, "cache_daily", "theme_stock_map_latest.json")
# 输出目录
OUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUT_DIR, exist_ok=True)


def load_non_daytrip_themes(db_path=None, ndays=20):
    """读取非一日游确认主题列表

    条件:composite_score >= 60 AND sentiment_score >= 65 AND zt_count >= 2
    且当前连续活跃天数 >= 1

    返回: dict {theme_name: {composite, sentiment, zt_count, confirmed_days, cycle_phase, leader}}
    """
    if db_path is None:
        db_path = THEME_DB

    if not os.path.exists(db_path):
        print(f"[Warning] 主题数据库不存在: {db_path}")
        return {}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 获取最近交易日
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC")
    all_dates = [row[0] for row in cur.fetchall()]
    if not all_dates:
        conn.close()
        return {}

    trade_date = all_dates[0]
    recent_dates = list(reversed(all_dates[:ndays]))  # 从旧到新

    # 读取历史数据
    placeholders = ','.join(['?' for _ in recent_dates])
    cur.execute(f"""
        SELECT trade_date, theme, composite_score, trend_score, sentiment_score,
               zt_count, leader_name, leader_score
        FROM theme_scores
        WHERE trade_date IN ({placeholders})
        ORDER BY theme, trade_date
    """, recent_dates)

    rows = cur.fetchall()
    conn.close()

    # 按主题分组
    theme_hist = defaultdict(list)
    for r in rows:
        theme_hist[r[1]].append({
            "trade_date": r[0],
            "composite": r[2] or 0,
            "trend": r[3] or 0,
            "sentiment": r[4] or 0,
            "zt_count": r[5] or 0,
            "leader": r[6] or "",
        })

    # 判断非一日游
    confirmed = {}
    for theme, hist in theme_hist.items():
        # 判断每天是否确认
        for day in hist:
            day["is_confirmed"] = (
                day["composite"] >= 60 and
                day["sentiment"] >= 65 and
                day["zt_count"] >= 2
            )

        # 当前连续确认天数
        current_streak = 0
        for day in reversed(hist):
            if day["is_confirmed"]:
                current_streak += 1
            else:
                break

        # 历史最长连续
        max_streak = 0
        tmp = 0
        for day in hist:
            if day["is_confirmed"]:
                tmp += 1
                max_streak = max(max_streak, tmp)
            else:
                tmp = 0

        # 周期阶段
        if current_streak == 0:
            if max_streak >= 2:
                cycle_phase = "休眠等待"
            else:
                cycle_phase = "未激活"
        elif current_streak <= 2:
            cycle_phase = "启动确认"
        elif current_streak <= 5:
            cycle_phase = "中期延续"
        else:
            cycle_phase = "中期延续"

        latest = hist[-1]

        # 非一日游分类:
        #   confirmed_active = 当前连续确认 >= 1(正在活跃)
        #   confirmed_dormant = 休眠等待(历史曾活跃但当前不活跃)
        # 两者都算非一日游,但在输出中标注区分
        if current_streak >= 1 or max_streak >= 2:
            confirmed[theme] = {
                "composite": latest["composite"],
                "sentiment": latest["sentiment"],
                "zt_count": latest["zt_count"],
                "confirmed_days": current_streak,
                "max_active_days": max_streak,
                "cycle_phase": cycle_phase,
                "leader": latest["leader"],
                "is_currently_active": current_streak >= 1,  # 当前正在活跃
            }

    return confirmed


def load_theme_stock_map(map_path=None):
    """加载主题-成份股映射

    返回: (stock_themes, theme_stocks)
      stock_themes: {ts_code: [theme1, theme2, ...]}
      theme_stocks: {theme_name: {ts_code: {name, score, chain_distance, ...}}}
    """
    if map_path is None:
        map_path = THEME_MAP_PATH

    if not os.path.exists(map_path):
        print(f"[Warning] 主题映射文件不存在: {map_path}")
        return {}, {}

    with open(map_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 反向映射:stock -> themes
    stock_themes = {}
    for code, info in data.get("stocks", {}).items():
        stock_themes[code] = info.get("themes", [])

    # 正向映射:theme -> stocks
    theme_stocks = {}
    for theme_name, stock_list in data.get("themes", {}).items():
        stocks = {}
        for s in stock_list:
            stocks[s["code"]] = {
                "name": s.get("name", ""),
                "score": s.get("score", 0),
                "chain_distance": s.get("chain_distance", 2),
                "via": s.get("via", ""),
                "industry_match": s.get("industry_match", False),
            }
        theme_stocks[theme_name] = stocks

    print(f"[加载] 主题映射: {len(theme_stocks)} 个主题, {len(stock_themes)} 只个股")
    return stock_themes, theme_stocks


def filter_signals(signals, non_daytrip_themes, stock_themes, theme_stocks):
    """对信号列表进行主题筛选

    每只信号股票标注:
    - themes: 所属主题列表
    - non_daytrip_themes: 所属非一日游确认主题列表
    - theme_rank: 在所属非一日游主题中的成份股排名
    - best_theme: 综合分最高的非一日游主题
    - best_theme_score: 非一日游主题综合分
    - is_confirmed_theme: 是否属于非一日游确认主题(Bool)
    """
    results = []

    for sig in signals:
        code = sig.get("ts_code", "")

        # 获取股票所属主题
        all_themes = stock_themes.get(code, [])

        # 筛选非一日游确认主题
        nd_themes = [t for t in all_themes if t in non_daytrip_themes]

        # 找综合分最高的非一日游主题
        best_theme = ""
        best_composite = 0
        best_rank = 0
        best_chain_dist = 99

        for t in nd_themes:
            tinfo = non_daytrip_themes[t]
            if tinfo["composite"] > best_composite:
                best_composite = tinfo["composite"]
                best_theme = t
                # 查在主题中的排名
                tstocks = theme_stocks.get(t, {})
                if code in tstocks:
                    # 按 score 排序
                    sorted_stocks = sorted(tstocks.items(), key=lambda x: -x[1].get("score", 0))
                    for rank, (c, _) in enumerate(sorted_stocks, 1):
                        if c == code:
                            best_rank = rank
                            best_chain_dist = tstocks[code].get("chain_distance", 2)
                            break

        # 构建标注
        enriched = dict(sig)
        enriched["themes"] = "; ".join(all_themes[:5])  # 最多5个主题
        enriched["non_daytrip_themes"] = "; ".join(nd_themes)
        enriched["best_theme"] = best_theme
        enriched["best_theme_composite"] = best_composite
        enriched["best_theme_rank"] = best_rank
        enriched["best_chain_distance"] = best_chain_dist
        enriched["is_confirmed_theme"] = len(nd_themes) > 0
        enriched["is_currently_active_theme"] = any(
            non_daytrip_themes[t].get("is_currently_active", False) for t in nd_themes
        )

        # 非一日游主题的周期阶段
        if best_theme and best_theme in non_daytrip_themes:
            enriched["theme_phase"] = non_daytrip_themes[best_theme]["cycle_phase"]
            enriched["theme_leader"] = non_daytrip_themes[best_theme]["leader"]
            enriched["theme_confirmed_days"] = non_daytrip_themes[best_theme]["confirmed_days"]
        else:
            enriched["theme_phase"] = ""
            enriched["theme_leader"] = ""
            enriched["theme_confirmed_days"] = 0

        results.append(enriched)

    return results


def _add_market_overview_to_pdf(elements, font_name, styles):
    """从market_analysis.db读取昨日大盘概览,插入PDF"""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer
    import sqlite3, os

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'cache_backbone_tushare', 'market_analysis.db')
    db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute('SELECT * FROM overall_analysis ORDER BY trade_date DESC LIMIT 1')
        oa = cur.fetchone()
        cur.execute('SELECT * FROM limit_stats ORDER BY trade_date DESC LIMIT 1')
        ls = cur.fetchone()
        conn.close()

        if not oa:
            return

        trade_date = oa['trade_date']
        market_status = oa['market_status'] or ''
        position = oa['total_position'] or ''
        index_trend = f"{oa['index_trend']:.0f}" if oa['index_trend'] else ''
        theme_trend = f"{oa['theme_trend']:.0f}" if oa['theme_trend'] else ''
        trend_score = f"{oa['trend_score']:.1f}" if oa['trend_score'] else ''

        zt = ls['zt_count'] if ls else '?'
        dt = ls['dt_count'] if ls else '?'
        up = ls['up_count'] if ls else '?'
        down = ls['down_count'] if ls else '?'

        ov_line1 = f"数据{trade_date} | {market_status} | 趋势分{trend_score}(指数{index_trend}/主题{theme_trend}) | 仓位{position}%"
        ov_line2 = f"涨停{zt} 跌停{dt} | 上涨{up} 下跌{down}"
        ov_text = f'📊 大盘概览：{ov_line1}\n{ov_line2}'

        ov_style = ParagraphStyle('OV', parent=styles['Normal'],
            fontName=font_name, fontSize=8, alignment=0,
            textColor=colors.HexColor('#2c3e50'),
            borderWidth=1, borderColor=colors.HexColor('#3498db'),
            borderPadding=8, backColor=colors.HexColor('#ebf5fb'),
            spaceAfter=4*mm)
        elements.append(Paragraph(ov_text, ov_style))
        elements.append(Spacer(1, 8*mm))
    except Exception as e:
        pass


def generate_pdf_report(results, non_daytrip_themes, output_path):
    """生成带主题筛选的PDF报告"""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        print("[Error] reportlab 未安装,无法生成PDF")
        return

    # 注册中文字体
    font_paths = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    cn_font = "Helvetica"
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('CNFont', fp))
                cn_font = 'CNFont'
                break
            except Exception:
                continue

    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=20, rightMargin=20,
                            topMargin=15, bottomMargin=15)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CNTitle', parent=styles['Title'],
                                  fontName=cn_font, fontSize=14, spaceAfter=6)
    subtitle_style = ParagraphStyle('CNSub', parent=styles['Normal'],
                                     fontName=cn_font, fontSize=9, spaceAfter=4)
    normal_style = ParagraphStyle('CNNormal', parent=styles['Normal'],
                                   fontName=cn_font, fontSize=7)

    elements = []

    # 标题
    trade_date = results[0].get("entry_date", "") if results else ""
    confirmed_count = sum(1 for r in results if r.get("is_confirmed_theme"))
    total_count = len(results)

    elements.append(Paragraph(
        f"二波形态精选 × 非一日游主题筛选报告", title_style))
    elements.append(Paragraph(
        f"数据日期: {trade_date} | 总信号: {total_count}只 | 非一日游命中: {confirmed_count}只 | "
        f"非一日游主题: {len(non_daytrip_themes)}个", subtitle_style))

    # ── 昨日大盘概览 ──────────────────────
    _add_market_overview_to_pdf(elements, cn_font, styles)

    # ── 今日精选 TOP3 ──────────────────────
    # 按四种形态分组筛选TOP3
    main_sideways = [r for r in results
                     if r['pattern'] == '强势横盘' and r['ts_code'].startswith(('600', '601', '603', '605', '000', '002'))]
    gem_deep = [r for r in results
                if r['pattern'] == '深度回调' and r['ts_code'].startswith(('688', '300', '301'))]
    gem_volume = [r for r in results
                  if r['pattern'] == '放量回调' and r['ts_code'].startswith(('688', '300', '301'))]
    gem_vshape = [r for r in results
                  if r['pattern'] == 'V型急跌' and r['ts_code'].startswith(('688', '300', '301'))]
    main_sideways = sorted(main_sideways, key=lambda x: x.get('score', 0), reverse=True)[:3]
    gem_deep = sorted(gem_deep, key=lambda x: x.get('score', 0), reverse=True)[:3]
    gem_volume = sorted(gem_volume, key=lambda x: x.get('score', 0), reverse=True)[:3]
    gem_vshape = sorted(gem_vshape, key=lambda x: x.get('score', 0), reverse=True)[:3]

    if main_sideways or gem_deep or gem_volume or gem_vshape:
        pick_title_style = ParagraphStyle('PICK_T', parent=styles['Normal'],
            fontName=cn_font, fontSize=12, alignment=0, spaceAfter=2*mm,
            textColor=colors.HexColor('#1a5276'))
        pick_style = ParagraphStyle('PICK', parent=styles['Normal'],
            fontName=cn_font, fontSize=8.5, alignment=0, spaceAfter=1*mm,
            textColor=colors.HexColor('#2c3e50'), leading=12)
        pick_highlight = ParagraphStyle('PICK_H', parent=styles['Normal'],
            fontName=cn_font, fontSize=9, alignment=0, spaceAfter=1*mm,
            textColor=colors.HexColor('#c0392b'), leading=12)

        elements.append(Paragraph('⭐ 今日精选', pick_title_style))

        if main_sideways:
            elements.append(Paragraph('【主板强势横盘 TOP3】(成功率98.6%, 盈亏比19.9x, 评分+5)', pick_highlight))
            for i, r in enumerate(main_sideways, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"RSI={r['rsi']:.0f}  主题:{r.get('best_theme','')}",
                    pick_style))

        if gem_deep:
            elements.append(Paragraph('【双创深度回调 TOP3】(成功率88.2%, 盈亏比12.2x, 评分-2)', pick_highlight))
            for i, r in enumerate(gem_deep, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"RSI={r['rsi']:.0f}  主题:{r.get('best_theme','')}",
                    pick_style))

        if gem_volume:
            elements.append(Paragraph('【双创放量回调 TOP3】(成功率91.2%, 盈亏比14.5x)', pick_highlight))
            for i, r in enumerate(gem_volume, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"RSI={r['rsi']:.0f}  主题:{r.get('best_theme','')}",
                    pick_style))

        if gem_vshape:
            elements.append(Paragraph('【双创V型急跌 TOP3】(成功率97.2%, 盈亏比16.1x, 评分+8)', pick_highlight))
            for i, r in enumerate(gem_vshape, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"RSI={r['rsi']:.0f}  主题:{r.get('best_theme','')}",
                    pick_style))

        elements.append(Spacer(1, 4*mm))

    # 非一日游主题一览
    if non_daytrip_themes:
        elements.append(Spacer(1, 6))
        theme_data = [["主题", "综合分", "情绪分", "涨停", "连续天数", "周期阶段", "龙头"]]
        for t, info in sorted(non_daytrip_themes.items(), key=lambda x: -x[1]["composite"]):
            theme_data.append([
                t, f"{info['composite']:.0f}", f"{info['sentiment']:.0f}",
                str(info["zt_count"]), str(info["confirmed_days"]),
                info["cycle_phase"], info["leader"]
            ])

        theme_table = Table(theme_data, colWidths=[80, 40, 40, 30, 45, 55, 70])
        theme_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), cn_font),
            ('FONTSIZE', (0, 0), (-1, -1), 6.5),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2F5496')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F2F2F2')]),
        ]))
        elements.append(theme_table)

    elements.append(Spacer(1, 8))

    # 筛选结果:按主题活跃度分三层
    # 第一层:当前活跃非一日游主题命中的信号
    active_results = [r for r in results if r.get("is_currently_active_theme")]
    # 第二层:休眠等待非一日游主题命中的信号
    dormant_results = [r for r in results if r.get("is_confirmed_theme") and not r.get("is_currently_active_theme")]
    # 第三层:未命中
    unconfirmed_results = [r for r in results if not r.get("is_confirmed_theme")]

    # 按主题综合分 × 二波评分 排序
    active_results.sort(key=lambda x: -x.get("score", 0))
    dormant_results.sort(key=lambda x: -x.get("score", 0))

    # 表头定义(三层共用)
    hdr = ["代码", "名称", "形态", "评分", "最佳主题", "主题分", "周期", "龙头", "一波%", "回调%", "RSI"]
    col_w = [50, 40, 30, 25, 50, 28, 35, 35, 28, 30, 22]

    # 第一层:当前活跃
    if active_results:
        elements.append(Paragraph("★ 非一日游当前活跃主题命中(重点推荐)", subtitle_style))

        rows = [hdr]
        for r in active_results[:30]:
            rows.append([
                r.get("ts_code", "").replace(".SH","").replace(".SZ",""),
                r.get("name", ""),
                r.get("pattern", "")[:4],
                str(r.get("score", 0)),
                r.get("best_theme", "")[:8],
                f"{r.get('best_theme_composite', 0):.0f}",
                r.get("theme_phase", "")[:6],
                r.get("theme_leader", "")[:6],
                f"+{r.get('wave1_gain', 0):.0f}%",
                f"-{r.get('pullback_pct', 0):.1f}%",
                f"{r.get('rsi', 0):.0f}",
            ])

        col_w = [50, 40, 30, 25, 50, 28, 35, 35, 28, 30, 22]
        tbl = Table(rows, colWidths=col_w)
        tbl.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), cn_font),
            ('FONTSIZE', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#C00000')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
            ('ALIGN', (3, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FFF2F2')]),
        ]))
        elements.append(tbl)

    # 第二层:休眠等待
    if dormant_results:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(f"◇ 非一日游休眠主题命中(近期曾活跃,共{len(dormant_results)}只)", subtitle_style))

        rows_d = [hdr]
        for r in dormant_results[:20]:
            rows_d.append([
                r.get("ts_code", "").replace(".SH","").replace(".SZ",""),
                r.get("name", ""),
                r.get("pattern", "")[:4],
                str(r.get("score", 0)),
                r.get("best_theme", "")[:8],
                f"{r.get('best_theme_composite', 0):.0f}",
                r.get("theme_phase", "")[:6],
                r.get("theme_leader", "")[:6],
                f"+{r.get('wave1_gain', 0):.0f}%",
                f"-{r.get('pullback_pct', 0):.1f}%",
                f"{r.get('rsi', 0):.0f}",
            ])

        tbl_d = Table(rows_d, colWidths=col_w)
        tbl_d.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), cn_font),
            ('FONTSIZE', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#BF8F00')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
            ('ALIGN', (3, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FFFFF0')]),
        ]))
        elements.append(tbl_d)

    # 未命中非一日游主题的信号
    if unconfirmed_results:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(f"其他信号(非一日游主题未确认,共{len(unconfirmed_results)}只)", subtitle_style))

        hdr2 = ["代码", "名称", "形态", "评分", "所属主题", "一波%", "回调%", "RSI"]
        rows2 = [hdr2]
        unconfirmed_results.sort(key=lambda x: -x.get("score", 0))
        for r in unconfirmed_results[:20]:
            rows2.append([
                r.get("ts_code", "").replace(".SH","").replace(".SZ",""),
                r.get("name", ""),
                r.get("pattern", "")[:4],
                str(r.get("score", 0)),
                r.get("themes", "")[:16],
                f"+{r.get('wave1_gain', 0):.0f}%",
                f"-{r.get('pullback_pct', 0):.1f}%",
                f"{r.get('rsi', 0):.0f}",
            ])

        col_w2 = [50, 40, 30, 25, 90, 28, 30, 22]
        tbl2 = Table(rows2, colWidths=col_w2)
        tbl2.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), cn_font),
            ('FONTSIZE', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#808080')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
            ('ALIGN', (3, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
        ]))
        elements.append(tbl2)

    doc.build(elements)
    print(f"[PDF] 已生成: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="二波形态 × 非一日游主题筛选器")
    parser.add_argument("--input", required=True, help="wave2扫描结果JSON文件路径")
    parser.add_argument("--pdf", action="store_true", help="生成PDF报告")
    parser.add_argument("--today", action="store_true", help="仅保留当日信号")
    parser.add_argument("--min-score", type=int, default=0, help="最低共振评分过滤")
    args = parser.parse_args()

    # 1. 加载信号
    input_path = args.input
    if not os.path.isabs(input_path):
        input_path = os.path.join(BASE_DIR, input_path)

    with open(input_path, 'r', encoding='utf-8-sig') as f:
        signals = json.load(f)

    print(f"[加载] 信号: {len(signals)} 只")

    # today 模式过滤
    if args.today:
        from datetime import date
        today_str = date.today().strftime('%Y%m%d')
        signals = [s for s in signals if s.get("entry_date", "") == today_str]
        print(f"[过滤] 今日信号: {len(signals)} 只")

    # 评分过滤
    if args.min_score > 0:
        signals = [s for s in signals if s.get("score", 0) >= args.min_score]
        print(f"[过滤] 评分>={args.min_score}: {len(signals)} 只")

    # 2. 加载非一日游确认主题
    non_daytrip = load_non_daytrip_themes()
    print(f"[加载] 非一日游确认主题: {len(non_daytrip)} 个")
    for t, info in sorted(non_daytrip.items(), key=lambda x: -x[1]["composite"]):
        print(f"  {t}: 综合={info['composite']:.0f} 情绪={info['sentiment']:.0f} "
              f"涨停={info['zt_count']}家 连续{info['confirmed_days']}天 [{info['cycle_phase']}] 龙头={info['leader']}")

    # 3. 加载主题-成份股映射
    stock_themes, theme_stocks = load_theme_stock_map()

    # 4. 筛选
    results = filter_signals(signals, non_daytrip, stock_themes, theme_stocks)

    # 5. 输出统计
    confirmed = [r for r in results if r.get("is_confirmed_theme")]
    active = [r for r in results if r.get("is_currently_active_theme")]
    dormant = [r for r in results if r.get("is_confirmed_theme") and not r.get("is_currently_active_theme")]
    print(f"\n{'='*80}")
    print(f"二波形态 × 非一日游主题筛选结果")
    print(f"{'='*80}")
    print(f"总信号: {len(results)} 只 | 活跃主题命中: {len(active)} 只 | 休眠主题命中: {len(dormant)} 只 | 未命中: {len(results)-len(confirmed)} 只")

    if active:
        print(f"\n★ 非一日游活跃主题命中(重点推荐)")
        print(f"{'代码':<12}{'名称':<10}{'形态':<8}{'评分':<5}{'最佳主题':<14}{'主题分':<6}{'周期':<10}{'龙头':<10}")
        print("-" * 80)
        active.sort(key=lambda x: -x.get("score", 0))
        for r in active[:20]:
            print(f"{r['ts_code']:<12}{r.get('name',''):<10}{r['pattern'][:4]:<8}"
                  f"{r['score']:<5}{r.get('best_theme',''):<14}"
                  f"{r.get('best_theme_composite',0):<6.0f}"
                  f"{r.get('theme_phase',''):<10}{r.get('theme_leader',''):<10}")

    if dormant:
        print(f"\n◇ 非一日游休眠主题命中(近期曾活跃)")
        print(f"{'代码':<12}{'名称':<10}{'形态':<8}{'评分':<5}{'最佳主题':<14}{'主题分':<6}{'周期':<10}")
        print("-" * 80)
        dormant.sort(key=lambda x: -x.get("score", 0))
        for r in dormant[:15]:
            print(f"{r['ts_code']:<12}{r.get('name',''):<10}{r['pattern'][:4]:<8}"
                  f"{r['score']:<5}{r.get('best_theme',''):<14}"
                  f"{r.get('best_theme_composite',0):<6.0f}"
                  f"{r.get('theme_phase',''):<10}")

    # 6. 保存JSON
    trade_date = results[0].get("entry_date", "unknown") if results else "unknown"
    out_json = os.path.join(OUT_DIR, f"wave2_theme_filtered_{trade_date}.json")
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] {out_json}")

    # 7. 保存CSV
    import pandas as pd
    df = pd.DataFrame(results)
    # 选择关键列
    cols = ["ts_code", "name", "pattern", "score", "best_theme", "best_theme_composite",
            "theme_phase", "theme_leader", "theme_confirmed_days", "best_theme_rank",
            "best_chain_distance", "is_confirmed_theme", "is_currently_active_theme",
            "wave1_gain", "pullback_pct",
            "adjust_days", "rsi", "entry_price", "stop_loss", "target", "entry_date",
            "non_daytrip_themes", "themes"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]

    out_csv = os.path.join(OUT_DIR, f"wave2_theme_filtered_{trade_date}.csv")
    df.to_csv(out_csv, index=False, encoding='utf-8-sig')
    print(f"[保存] {out_csv}")

    # 8. 生成PDF
    if args.pdf:
        out_pdf = os.path.join(OUT_DIR, f"wave2_theme_filtered_{trade_date}.pdf")
        generate_pdf_report(results, non_daytrip, out_pdf)
        return out_pdf

    return out_json


if __name__ == "__main__":
    result = main()
    print(f"\n[完成] 输出: {result}")
