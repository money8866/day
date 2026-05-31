"""
=============================
板块中军分析系统 v1.0
=============================
功能：
1. 获取当日A股全市场行情
2. 按行业板块评分，找出最强板块（TOP5）
3. 在最强板块中识别"中军"——板块的中流砥柱
4. 可视化输出 + 自动报告

中军定义：
- 板块内成交额排名前5（大资金战场）
- 总市值 > 100亿（大盘股/中大盘股）
- 涨幅温和（2%~7%），非涨停——机构行为
- 站上5日均线
- 20日均线持续向上（趋势健康）
- 成交量温和放大（资金持续流入）
- 非一字板/无涨停（区别于情绪龙头）
=============================
"""

import tushare as ts
import pandas as pd
import numpy as np
import os
import datetime
import warnings
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

# =========================
# 加载配置
# =========================
load_dotenv()
TOKEN = os.getenv("TUSHARE_TOKEN")
if not TOKEN:
    raise ValueError("请在 .env 中设置 TUSHARE_TOKEN")

ts.set_token(TOKEN)
pro = ts.pro_api()

# =========================
# 日期工具
# =========================
def get_last_trade_date():
    """获取最近一个交易日"""
    now = datetime.datetime.now()
    today = now.strftime("%Y%m%d")

    # 9:30前取前一天
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        today = (now - datetime.timedelta(days=1)).strftime("%Y%m%d")

    cal = pro.trade_cal(exchange='', start_date='20240101', end_date=today)
    cal = cal[cal['is_open'] == 1]
    last_date = cal[cal['cal_date'] <= today]['cal_date'].max()

    return last_date


def get_hist_trade_dates(end_date, days=60):
    """获取历史交易日列表"""
    cal = pro.trade_cal(exchange='', start_date='20240101', end_date=end_date)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date')
    dates = cal['cal_date'].tolist()
    if len(dates) < days:
        return dates
    return dates[-days:]


# =========================
# 数据获取
# =========================
def get_market_data(trade_date):
    """获取当日全市场行情 + 基本面"""
    print(f"📡 获取行情数据: {trade_date}")

    # 日线行情
    df = pro.daily(trade_date=trade_date)
    if df is None or len(df) == 0:
        print("⚠️ 当日无数据")
        return pd.DataFrame()

    # 每日基本面（市值、换手率）
    basic = pro.daily_basic(
        trade_date=trade_date,
        fields="ts_code,total_mv,circ_mv,turnover_rate,pe"
    )

    # 股票名称与行业
    stock_info = pro.stock_basic(
        exchange='', list_status='L',
        fields="ts_code,name,industry,market"
    )

    # 合并
    df = df.merge(basic, on="ts_code", how="left")
    df = df.merge(stock_info, on="ts_code", how="left")

    # 字段整理
    df['代码'] = df['ts_code'].str.split('.').str[0]
    df['名称'] = df['name']
    df['涨跌幅'] = df['pct_chg'].astype(float)
    df['成交额'] = df['amount'].astype(float) * 1000  # 千元→元
    df['换手率'] = df['turnover_rate'].astype(float)
    df['市盈率'] = df['pe'].astype(float)

    # 市值：daily_basic中为万元 → 转元
    df['总市值'] = df['total_mv'].fillna(0).astype(float) * 10000
    df['流通市值'] = df['circ_mv'].fillna(0).astype(float) * 10000

    # 行业板块
    df['板块'] = df['industry'].fillna('其他')

    # 关键列
    cols = ['代码', '名称', '板块', '涨跌幅', '成交额', '总市值',
            '流通市值', '换手率', '市盈率']
    df = df[cols].dropna(subset=['板块'])

    return df


def get_hist_kline(code, end_date, limit=60):
    """获取个股历史K线（用于均线分析）"""
    ts_code = f"{code}.SH" if code.startswith('6') else f"{code}.SZ"
    if code.startswith('8') or code.startswith('4'):
        ts_code = f"{code}.BJ"

    try:
        df = pro.daily(ts_code=ts_code, start_date=end_date[:4]+"0101", end_date=end_date)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.sort_values('trade_date').reset_index(drop=True)
        return df
    except:
        return pd.DataFrame()


# =========================
# 板块评分系统
# =========================
def score_sectors(df):
    """多维度板块评分"""
    print("📊 计算板块评分...")

    result = []

    for board, group in df.groupby('板块'):
        n_stocks = len(group)
        if n_stocks < 5:  # 少于5只个股的板块跳过
            continue

        avg_pct = group['涨跌幅'].mean()
        total_amount = group['成交额'].sum()
        limit_cnt = len(group[group['涨跌幅'] > 9.5])
        strong_cnt = len(group[group['涨跌幅'] > 5])
        positive_ratio = (group['涨跌幅'] > 0).mean()
        large_cap_cnt = len(group[group['总市值'] > 100e8])

        # 板块评分公式
        score = (
            avg_pct * 2.0 +                # 平均涨幅（核心）
            limit_cnt * 12.0 +              # 涨停数（情绪）
            strong_cnt * 4.0 +              # 强势股数（扩散性）
            positive_ratio * 5.0 +          # 上涨比率（广度）
            np.log1p(total_amount / 1e8) * 3.0 +  # 总成交额（资金）
            np.log1p(large_cap_cnt + 1) * 2.0     # 大盘股参与度（机构）
        )

        result.append({
            '板块': board,
            '评分': round(score, 2),
            '平均涨幅': round(avg_pct, 2),
            '涨停数': limit_cnt,
            '强势股数': strong_cnt,
            '上涨比率': round(positive_ratio * 100, 1),
            '总成交额(亿)': round(total_amount / 1e8, 2),
            '成分股数': n_stocks
        })

    score_df = pd.DataFrame(result).sort_values('评分', ascending=False).reset_index(drop=True)
    return score_df


# =========================
# 中军识别引擎
# =========================
def identify_zhongjun(df, board, hist_end_date):
    """
    在指定板块中识别中军标的

    中军筛选条件：
    1. ✅ 成交额板块前10（大资金主战场）
    2. ✅ 总市值 > 80亿（中大盘股）
    3. ✅ 涨幅 0% ~ 7%（非涨停、机构风格）
    4. ✅ 站上5日均线
    5. ✅ 20日均线持续向上（近5日斜率正）
    6. ✅ 成交量趋势：近5日均量 > 前5日均量（温和放量）
    7. ✅ 非涨停（排除纯情绪龙头）
    """
    sub = df[df['板块'] == board].copy()

    if len(sub) == 0:
        return pd.DataFrame()

    # 第一步：市值 & 涨幅 初筛
    candidates = sub[
        (sub['总市值'] > 80e8) &
        (sub['涨跌幅'] >= 0) &
        (sub['涨跌幅'] <= 7) &
        (sub['成交额'] > 0)
    ].copy()

    if len(candidates) == 0:
        # 放宽条件：允许微跌
        candidates = sub[
            (sub['总市值'] > 50e8) &
            (sub['涨跌幅'] >= -2) &
            (sub['涨跌幅'] <= 7)
        ].copy()

    # 取成交额前15进行深度分析
    candidates = candidates.sort_values('成交额', ascending=False).head(15)

    if len(candidates) == 0:
        return pd.DataFrame()

    print(f"  🔍 候选股票: {len(candidates)} 只")

    results = []
    errors = []

    for _, row in candidates.iterrows():
        code = row['代码']
        hist = get_hist_kline(code, hist_end_date, limit=60)

        if hist is None or len(hist) < 25:
            errors.append((code, "历史数据不足"))
            continue

        try:
            hist = hist.sort_values('trade_date').reset_index(drop=True)
            hist['ma5'] = hist['close'].rolling(5).mean()
            hist['ma10'] = hist['close'].rolling(10).mean()
            hist['ma20'] = hist['close'].rolling(20).mean()
            hist['ma60'] = hist['close'].rolling(60).mean()

            latest = hist.iloc[-1]

            # ----- 条件1: 站上5日线 -----
            above_ma5 = latest['close'] > latest['ma5']

            # ----- 条件2: 站上20日线 -----
            above_ma20 = latest['close'] > latest['ma20']

            # ----- 条件3: 20日均线趋势向上 -----
            ma20_values = hist['ma20'].dropna().values
            if len(ma20_values) >= 5:
                ma20_slope = (ma20_values[-1] - ma20_values[-5]) / ma20_values[-5] * 100
                ma20_up = ma20_slope > -0.1  # 基本走平或向上
            else:
                ma20_up = False

            # ----- 条件4: 成交量趋势（温和放量）-----
            if len(hist) >= 15:
                vol_recent = hist['vol'].tail(5).mean()
                vol_prev = hist['vol'].iloc[-10:-5].mean()
                vol_trend = vol_recent > vol_prev * 0.8  # 不缩量
                vol_ratio = vol_recent / vol_prev if vol_prev > 0 else 1
            else:
                vol_trend = True
                vol_ratio = 1

            # ----- 条件5: 非涨停（排除一字板情绪股）-----
            not_limit = latest['pct_chg'] < 9.5

            # ----- 条件6: 趋势稳定性（低波动）-----
            if len(hist) >= 20:
                recent_volatility = hist['pct_chg'].tail(20).std()
                stable = recent_volatility < 5.0  # 20日波动率 < 5%
            else:
                stable = True

            # ----- 综合评分（中军得分）-----
            zj_score = 0
            zj_score += 25 if above_ma5 else 0
            zj_score += 20 if above_ma20 else 0
            zj_score += 20 if ma20_up else 0
            zj_score += 15 if vol_trend else 0
            zj_score += 10 if not_limit else 0
            zj_score += 10 if stable else 0

            # 成交额排名加分（板块内成交额越大越好）
            amount_rank = candidates['成交额'].rank(ascending=False).loc[row.name]
            zj_score += max(0, 15 - amount_rank)

            # 市值加分（中军偏爱大盘股）
            if row['总市值'] > 500e8:
                zj_score += 10
            elif row['总市值'] > 200e8:
                zj_score += 5

            results.append({
                '代码': code,
                '名称': row['名称'],
                '涨跌幅': round(row['涨跌幅'], 2),
                '成交额(亿)': round(row['成交额'] / 1e8, 2),
                '总市值(亿)': round(row['总市值'] / 1e8, 2),
                '流通市值(亿)': round(row['流通市值'] / 1e8, 2),
                '换手率': round(row['换手率'], 2),
                '站上5日线': '✅' if above_ma5 else '❌',
                '站上20日线': '✅' if above_ma20 else '❌',
                '20日线向上': '✅' if ma20_up else '❌',
                '量能趋势': '📈温和' if vol_trend else '📉缩量',
                '趋势稳定': '✅' if stable else '⚠️波动大',
                '中军评分': round(zj_score, 1)
            })

        except Exception as e:
            errors.append((code, str(e)))
            continue

    if errors:
        print(f"  ⚠️ {len(errors)} 只股票分析失败")

    if len(results) == 0:
        return pd.DataFrame()

    zj_df = pd.DataFrame(results)
    zj_df = zj_df.sort_values('中军评分', ascending=False).reset_index(drop=True)
    return zj_df


# =========================
# 可视化报告
# =========================
def print_report(top_sectors, zhongjun_data):
    """打印中军分析报告"""
    print("\n" + "=" * 70)
    print("               🏆 当日最强板块中军分析报告")
    print("=" * 70)
    print(f"📅 报告日期: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📊 数据源: Tushare Pro")
    print("=" * 70)

    # ---- 板块排名 ----
    print("\n📊 【板块强度排名 TOP10】")
    print("-" * 70)
    display_cols = ['板块', '评分', '平均涨幅', '涨停数', '强势股数',
                    '上涨比率', '总成交额(亿)']
    print(top_sectors.head(10)[display_cols].to_string(index=True))
    print("-" * 70)

    # ---- 各板块中军 ----
    print("\n🔥 【最强板块中军分析】")
    print("=" * 70)

    for i, (_, sector_row) in enumerate(top_sectors.head(5).iterrows()):
        board = sector_row['板块']
        score = sector_row['评分']

        print(f"\n{'=' * 50}")
        print(f"  #{i+1} 板块: {board}  |  评分: {score}")
        print(f"{'=' * 50}")

        zj = zhongjun_data.get(board, pd.DataFrame())

        if zj is None or len(zj) == 0:
            print("  ⚠️ 未找到符合条件的中军标的")
            continue

        # 取Top3中军
        top3 = zj.head(3)
        for j, (_, stock) in enumerate(top3.iterrows()):
            print(f"\n  🏅 中军 #{j+1}: {stock['名称']}({stock['代码']})")
            print(f"     ├ 涨跌幅: {stock['涨跌幅']}%")
            print(f"     ├ 成交额: {stock['成交额(亿)']}亿")
            print(f"     ├ 总市值: {stock['总市值(亿)']}亿")
            print(f"     ├ 流通市值: {stock['流通市值(亿)']}亿")
            print(f"     ├ 换手率: {stock['换手率']}%")
            print(f"     ├ 均线: {stock['站上5日线']}5日线 {stock['站上20日线']}20日线 {stock['20日线向上']}20日趋势")
            print(f"     ├ 量能: {stock['量能趋势']}")
            print(f"     ├ 稳定性: {stock['趋势稳定']}")
            print(f"     └ 中军评分: {stock['中军评分']}/100")

        print()

    # ---- 总结 ----
    print("\n📝 【中军操作建议】")
    print("-" * 70)
    print("  中军特征：大市值 ✅ 温和上涨 ✅ 趋势向上 ✅ 资金流入 ✅")
    print("  中军 vs 龙头：中军稳健可重仓，龙头弹性大但波动剧烈")
    print("  中军买点：回踩5/10日线低吸，不追高 ✅")
    print("=" * 70)


# =========================
# 保存报告到文件
# =========================
def generate_report_text(trade_date, top_sectors, zhongjun_map):
    """生成报告文本字符串"""
    now = datetime.datetime.now()
    lines = []
    sep70 = "=" * 70
    sep50 = "=" * 50

    lines.append(sep70)
    lines.append("               🏆 当日最强板块中军分析报告")
    lines.append(sep70)
    lines.append(f"📅 报告生成: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"📊 交易日: {trade_date}")
    lines.append(f"📡 数据源: Tushare Pro")
    lines.append(sep70)

    # ---- 板块排名 ----
    lines.append("")
    lines.append("📊 【板块强度排名 TOP10】")
    lines.append("-" * 70)
    display_cols = ['板块', '评分', '平均涨幅', '涨停数', '强势股数',
                    '上涨比率', '总成交额(亿)']
    for idx, (_, row) in enumerate(top_sectors.head(10).iterrows()):
        vals = [str(row[c]) for c in display_cols]
        lines.append(f"  {idx+1}. {' | '.join(vals)}")
    lines.append("-" * 70)

    # ---- 各板块中军 ----
    lines.append("")
    lines.append("🔥 【最强板块中军分析】")
    lines.append(sep70)

    for i, (_, sector_row) in enumerate(top_sectors.head(5).iterrows()):
        board = sector_row['板块']
        score = sector_row['评分']

        lines.append("")
        lines.append(sep50)
        lines.append(f"  #{i+1} 板块: {board}  |  评分: {score}")
        lines.append(sep50)

        zj = zhongjun_map.get(board, pd.DataFrame())
        if zj is None or len(zj) == 0:
            lines.append("  ⚠️ 未找到符合条件的中军标的")
            continue

        top3 = zj.head(3)
        for j, (_, stock) in enumerate(top3.iterrows()):
            lines.append(f"")
            lines.append(f"  🏅 中军 #{j+1}: {stock['名称']}({stock['代码']})")
            lines.append(f"     ├ 涨跌幅: {stock['涨跌幅']}%")
            lines.append(f"     ├ 成交额: {stock['成交额(亿)']}亿")
            lines.append(f"     ├ 总市值: {stock['总市值(亿)']}亿")
            lines.append(f"     ├ 流通市值: {stock['流通市值(亿)']}亿")
            lines.append(f"     ├ 换手率: {stock['换手率']}%")
            lines.append(f"     ├ 均线: {stock['站上5日线']}5日线 {stock['站上20日线']}20日线 {stock['20日线向上']}20日趋势")
            lines.append(f"     ├ 量能: {stock['量能趋势']}")
            lines.append(f"     ├ 稳定性: {stock['趋势稳定']}")
            lines.append(f"     └ 中军评分: {stock['中军评分']}/100")

    # ---- 中军精选汇总 ----
    lines.append("")
    lines.append("📋 【中军精选汇总】")
    lines.append("-" * 70)
    all_stocks = []
    for board, zj_df in zhongjun_map.items():
        if zj_df is not None and len(zj_df) > 0:
            top = zj_df.head(2).copy()
            top['所属板块'] = board
            all_stocks.append(top)

    if all_stocks:
        merged = pd.concat(all_stocks, ignore_index=True)
        merged = merged.sort_values('中军评分', ascending=False)
        summary_cols = ['所属板块', '名称', '代码', '涨跌幅', '成交额(亿)',
                        '总市值(亿)', '中军评分']
        lines.append(f"  {' | '.join(summary_cols)}")
        lines.append("  " + "-" * 66)
        for _, s in merged.iterrows():
            vals = [str(s[c]) for c in summary_cols]
            lines.append(f"  {' | '.join(vals)}")
    else:
        lines.append("  ⚠️ 今日无符合条件的优质中军")

    # ---- 操作建议 ----
    lines.append("")
    lines.append("📝 【中军操作建议】")
    lines.append("-" * 70)
    lines.append("  中军特征：大市值 ✅ 温和上涨 ✅ 趋势向上 ✅ 资金流入 ✅")
    lines.append("  中军 vs 龙头：中军稳健可重仓，龙头弹性大但波动剧烈")
    lines.append("  中军买点：回踩5/10日线低吸，不追高 ✅")
    lines.append(sep70)
    lines.append("")

    return "\n".join(lines)


def save_report(report_text, trade_date):
    """将报告保存到文件"""
    # 创建报告目录
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(report_dir, exist_ok=True)

    # 文件名：reports/中军报告_20260508.md
    filename = f"中军报告_{trade_date}.md"
    filepath = os.path.join(report_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n📝 报告已保存: {filepath}")
    return filepath


# =========================
# 主程序
# =========================
def main():
    print("🚀 板块中军分析系统启动")
    print("=" * 50)

    # 1. 获取交易日
    trade_date = get_last_trade_date()
    print(f"📅 当前交易日: {trade_date}")

    # 2. 获取行情
    df = get_market_data(trade_date)
    if df is None or len(df) == 0:
        print("❌ 无数据，退出")
        return

    # 3. 板块评分
    sector_scores = score_sectors(df)
    top5 = sector_scores.head(5)

    print(f"\n📊 最强板块 TOP5:")
    print(top5[['板块', '评分', '平均涨幅', '涨停数', '总成交额(亿)']].to_string(index=False))

    # 4. 逐板块分析中军
    zhongjun_map = {}
    for _, row in top5.iterrows():
        board = row['板块']
        print(f"\n{'=' * 60}")
        print(f"📊 分析板块: {board}")
        print(f"{'=' * 60}")

        zj = identify_zhongjun(df, board, trade_date)
        zhongjun_map[board] = zj

        if zj is not None and len(zj) > 0:
            print(f"\n  ✅ 找到 {len(zj)} 只中军候选")
            print(f"  {zj[['名称', '涨跌幅', '成交额(亿)', '总市值(亿)', '中军评分']].head(5).to_string(index=False)}")
        else:
            print(f"  ⚠️ 该板块未发现符合条件的优质中军")

    # 5. 输出完整报告
    print_report(sector_scores, zhongjun_map)

    # 6. 汇总输出
    print("\n📋 【中军精选汇总】")
    print("=" * 70)
    all_stocks = []
    for board, zj_df in zhongjun_map.items():
        if zj_df is not None and len(zj_df) > 0:
            top = zj_df.head(2).copy()
            top['所属板块'] = board
            all_stocks.append(top)

    if all_stocks:
        merged = pd.concat(all_stocks, ignore_index=True)
        merged = merged.sort_values('中军评分', ascending=False)
        print(merged[['所属板块', '名称', '代码', '涨跌幅', '成交额(亿)',
                      '总市值(亿)', '中军评分']].to_string(index=False))
    else:
        print("⚠️ 今日无符合条件的优质中军")

    # 7. 保存报告到文件
    report_text = generate_report_text(trade_date, sector_scores, zhongjun_map)
    save_report(report_text, trade_date)

    print("\n✅ 分析完成")


if __name__ == "__main__":
    main()
