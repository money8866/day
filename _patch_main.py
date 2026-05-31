import sys, os
# Read the file
path = r'D:\mystock\etf_quant.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find main() and replace
idx = content.index("def main():")
end_idx = content.index("if __name__")

old_main = content[idx:end_idx]

new_main = r'''def main():

    print("=" * 60)

    print("AI主线ETF系统 v5.0（持仓延续版）")

    print("=" * 60)

    init_style_table()
    init_portfolio_table()

    # =====================================================
    # 加载昨日持仓
    # =====================================================
    portfolio_df = load_portfolio()
    if not portfolio_df.empty:
        print(f"\n📋 当前持仓: {len(portfolio_df)} 只")
        for _, p in portfolio_df.iterrows():
            print(f"  - {p['industry']}({p['ts_code']}): 买入价{p['buy_price']}({p['buy_date']})")
    else:
        print("\n📋 当前无持仓")

    # 加载昨日报告摘要
    last_report_summary = load_last_report()
    if last_report_summary:
        print(f"\n📖 昨日报告已加载 ({len(last_report_summary)}字)")

    # 加载历史快照
    history_snap_df = load_daily_snapshot(days=5)
    if not history_snap_df.empty:
        print(f"\n📊 历史快照: {history_snap_df['trade_date'].nunique()}天")
    
    # =====================================================
    # 指数
    # =====================================================
    index_df = get_index_data()

    index_df = calc_indicators(index_df)

    # =========================板块分析
    sector_df = block.analyze_hot_sectors()

    # =========================
    # 市场情绪
    # =========================
    emotion_result = emotion.analyze_market_emotion(
        sector_df
    )

    emotion_text = ""

    if emotion_result:

        emotion_text = str(emotion_result)

    print(emotion_text)

    # 提取情绪分数
    emotion_score = 50
    if isinstance(emotion_result, dict):
        emotion_score = emotion_result.get('情绪分', 50)
    elif isinstance(emotion_result, str):
        import re
        m = re.search(r'情绪[分：:]+(\d+)', emotion_result)
        if m:
            emotion_score = int(m.group(1))

    if not sector_df.empty:

        print("\n========== 最强主线板块 ==========\n")

        top_sector = sector_df.head(20)

        print(top_sector)

    else:

        top_sector = pd.DataFrame()

    sector_text = ""
    if not top_sector.empty:

        sector_text = top_sector.to_string(index=False)

    sector_df_his = block.load_history()
    sector_text_his = sector_df_his.to_string(index=False)

    # =====================================================
    # 市场风险
    # =====================================================
    risk_state, position = market_risk(index_df)

    print("市场状态:", risk_state)

    print("建议仓位:", position)

    position_pct = round(position * 100)

    all_result = []

    # =====================================================
    # ETF分析
    # =====================================================
    for industry, ts_code in ETF_POOL.items():

        print(f"\n分析 {industry}")

        df = get_etf_data(ts_code)

        if df is None:

            continue

        if len(df) < 60:

            continue

        df = calc_indicators(df)

        latest = df.iloc[-1]

        # =================================================
        # 评分
        # =================================================
        score, rs = etf_score(

            df,

            industry,

            index_df
        )

        # =================================================
        # 波段
        # =================================================
        stage, rise = wave_stage(df)

        # =================================================
        # 信号
        # =================================================
        signal = buy_signal(df)

        level = signal_level(df)

        all_result.append({

            '行业': industry,

            'ETF': ts_code,

            '收盘价': round(
                latest['close'],
                2
            ),
            '涨跌幅': round(
                latest['pct_chg'],
                2
            ),

            '成交额': round(
                latest['amount'] / 1e8,
                2
            ),
            'RS强度': rs,

            '5日涨幅': round(
                latest['pct5'],
                2
            ),

            '10日涨幅': round(
                latest['pct10'],
                2
            ),

            '20日涨幅': round(
                latest['pct20'],
                2
            ),

            '波段阶段': stage,

            '波段涨幅': round(
                rise,
                2
            ),

            'AI情绪': ai_sentiment(
                industry
            ),

            '信号': signal,

            '等级': level,

            '总评分': score
        })

    # =====================================================
    # DataFrame
    # =====================================================
    result_df = pd.DataFrame(all_result)
    print(result_df)
    result_df = result_df.sort_values(
        '总评分',
        ascending=False
    )

    # =====================================================
    # 更新持仓价格 & 持仓分析
    # =====================================================
    update_portfolio_prices(result_df)
    portfolio_df = load_portfolio()
    portfolio_text = analyze_portfolio(result_df, portfolio_df)

    # 保存今日快照
    save_daily_snapshot(result_df, position_pct, emotion_score)

    # =====================================================
    # 市场风格
    # =====================================================
    style_df = market_style(result_df)

    # =====================================================
    # 输出
    # =====================================================
    print("\n")

    print("=" * 60)

    print("ETF主线排名")

    print("=" * 60)

    print(result_df)

    print("\n")

    print("=" * 60)

    print("市场风格")

    print("=" * 60)

    print(style_df)

    if portfolio_text:
        print("\n📋 持仓分析:")
        print(portfolio_text)

    # =====================================================
    # AI日报（增强版：含持仓跟踪+延续性分析）
    # =====================================================
    print("\nAI日报生成中...\n")

    report = deepseek_report(

        result_df,

        style_df,

        risk_state,
        emotion_text, sector_text, sector_text_his,
        portfolio_text=portfolio_text,
        last_report_summary=last_report_summary,
        history_snap_df=history_snap_df
    )

    # =====================================================
    # 保存
    # =====================================================
    report_file = save_report(report)

    print("\n")

    print("=" * 60)

    print("AI主线ETF日报")

    print("=" * 60)

    print(report)

    print("\n报告已保存:", report_file)

    # =====================================================
    # 手机推送
    # =====================================================
    send_report(report)

    print("\n系统运行完成")

'''

content = content[:idx] + new_main + content[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("OK - main() replaced")
