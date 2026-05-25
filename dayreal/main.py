import time
from datetime import datetime
from config import Config
from tdx_data import TdxData
from warning_system import WarningSystem
from technical_analysis import calculate_ma, analyze_concept_intraday, calculate_change_percent
from position_optimizer import FourFactorPositionManager, TradingTimeHelper
import requests
import os
from dotenv import load_dotenv

load_dotenv()


def should_analyze_at_time(check_time, analyzed_time, target_hour, target_minute):
    """
    判断是否在指定时间点进行分析

    Args:
        check_time: 当前时间 (datetime)
        analyzed_time: 最后一次分析时间 (datetime, 可为None)
        target_hour: 目标小时
        target_minute: 目标分钟

    Returns:
        (bool, str): (是否应该分析, 时间描述)
    """
    if check_time.hour == target_hour and abs(check_time.minute - target_minute) <= 2:
        if analyzed_time is None:
            return True, f"{target_hour:02d}:{target_minute:02d}"
        if analyzed_time.hour != target_hour or analyzed_time.minute != target_minute:
            return True, f"{target_hour:02d}:{target_minute:02d}"
    return False, ""


def send_analysis_to_server(title, message):
    sckey = os.getenv("WECHAT_SCKEY")
    if not sckey:
        print("警告: 未配置 WECHAT_SCKEY 环境变量")
        return

    url = f"https://sctapi.ftqq.com/{sckey}.send"
    data = {
        "title": title,
        "desp": message
    }

    try:
        requests.post(url, data=data, timeout=10)
        print(f"[Server酱] 定时分析推送成功")
    except Exception as e:
        print(f"[Server酱] 推送失败: {e}")


def main():
    print("="*70)
    print("股票盘中预警系统 - 四因子仓位管理版")
    print("="*70)

    config = Config()
    tdx = TdxData(config.config)
    warning = WarningSystem(config)
    position_manager = FourFactorPositionManager(max_position=1.0)

    last_analysis_time = None
    base_position, position_notes = config.load_base_position()

    concepts = config.load_concepts()
    individual_stocks = config.load_stocks()
    stock_check_interval = config.get('warning.stock_check_interval', 10)
    concept_check_interval = config.get('warning.concept_check_interval', 60)

    print(f"\n配置信息:")
    print(f"概念板块文件: {config.get('files.concepts_csv', 'concepts.csv')}")
    print(f"概念板块: {concepts}")
    print(f"个股文件: {config.get('files.stocks_csv', 'stocks.csv')}")
    print(f"个股: {individual_stocks}")
    print(f"仓位配置文件: {config.get('files.position_csv', 'position_config.csv')}")
    print(f"盘后建议基础仓位: {base_position * 100:.1f}% ({position_notes})")
    print(f"个股检查间隔: {stock_check_interval}秒")
    print(f"板块检查间隔: {concept_check_interval}秒")
    print(f"\n四因子分析时间点: 10:00, 11:30, 13:30, 14:30")
    print(f"\n开始连接通达信服务器...")

    if not tdx.connect():
        print("连接失败，程序退出")
        return

    print("连接成功！\n")

    stock_list = []
    ma5_data = {}
    concept_stock_map = {}

    for concept in concepts:
        print(f"正在获取概念板块 [{concept}] 的股票列表...")
        concept_stocks = tdx.get_concept_stocks(concept)
        if concept_stocks:
            concept_stock_map[concept] = concept_stocks
            stock_list.extend(concept_stocks)
            print(f"  找到 {len(concept_stocks)} 只股票")
        else:
            print(f"  未找到该板块股票")

    stock_name_map = {}
    
    for stock_item in individual_stocks:
        code = stock_item['code']
        name = stock_item.get('name', '')
        market = 1 if code.startswith('6') else 0
        stock_list.append((market, code))
        stock_name_map[code] = name

    stock_list = list(set(stock_list))
    print(f"\n总共监控 {len(stock_list)} 只股票\n")

    print("正在获取历史数据计算5日均线...")
    for market, code in stock_list:
        klines = tdx.get_history_kline(market, code, category=9, count=20)
        if klines:
            ma5 = calculate_ma(klines, 5)
            if ma5:
                ma5_data[(market, code)] = ma5
    print(f"已获取 {len(ma5_data)} 只股票的5日均线数据\n")

    print("开始监控... (按 Ctrl+C 停止)\n")

    last_stock_check = datetime.now()
    last_concept_check = datetime.now()
    base_sleep_interval = 2
    
    last_warning_time = {}  # 记录上次预警时间，实现10分钟重复限制

    try:
        while True:
            now = datetime.now()

            if not (9 <= now.hour < 15) or (now.hour == 11 and now.minute > 30) or (now.hour == 12):
                time.sleep(base_sleep_interval)
                continue

            need_check_stocks = (now - last_stock_check).total_seconds() >= stock_check_interval
            need_check_concepts = (now - last_concept_check).total_seconds() >= concept_check_interval

            need_time_analysis = False
            analysis_time_desc = ""

            time_points = [(10, 0), (11, 30), (13, 30), (14, 30)]
            for hour, minute in time_points:
                should_do, desc = should_analyze_at_time(now, last_analysis_time, hour, minute)
                if should_do:
                    need_time_analysis = True
                    analysis_time_desc = desc
                    break

            if need_check_stocks or need_check_concepts or need_time_analysis:
                quotes = tdx.get_stock_quotes(stock_list)
                if not quotes:
                    time.sleep(base_sleep_interval)
                    continue

                quote_map = {q.get('code', ''): q for q in quotes}

                if need_time_analysis:
                    print("\n" + "="*70)
                    print(f"【{analysis_time_desc}】 定时四因子仓位分析")
                    print("="*70)

                    quote_data = []
                    for q in quotes:
                        change_pct = calculate_change_percent(q)
                        quote_data.append({
                            "code": q.get("code", ""),
                            "name": q.get("name", ""),
                            "price": q.get("price", 0),
                            "change_pct": change_pct
                        })

                    sentiment = position_manager.sentiment_analyzer.analyze_opening_30min(quote_data)
                    sentiment_score = sentiment["sentiment_score"]

                    concept_analysis = None
                    if concepts:
                        first_concept = concepts[0]
                        first_concept_stocks = concept_stock_map.get(first_concept, [])
                        concept_quotes = []
                        for market, code in first_concept_stocks:
                            if code in quote_map:
                                concept_quotes.append(quote_map[code])
                        if concept_quotes:
                            concept_analysis = analyze_concept_intraday(concept_quotes, first_concept)

                    index_change = tdx.calculate_index_change()

                    drawdown = 0.0

                    position_result = position_manager.get_position_suggestion(
                        base_position=base_position,
                        sentiment_score=sentiment_score,
                        concept_analysis=concept_analysis,
                        index_change=index_change,
                        drawdown=drawdown
                    )

                    print(f"\n【市场情绪】")
                    print(f"  情绪评分: {sentiment_score} ({sentiment['market_type']})")
                    print(f"  上涨: {sentiment['up_count']} 只  下跌: {sentiment['down_count']} 只")
                    print(f"  涨停: {sentiment['limit_up_count']} 只  跌停: {sentiment['limit_down_count']} 只")
                    print(f"  平均涨幅: {sentiment['avg_change']:.2f}%")

                    print(f"\n【四因子分解】")
                    factors = position_result['factors']
                    print(f"  情绪系数: {factors['sentiment_factor']:.2f}")
                    print(f"  主线系数: {factors['mainline_factor']:.2f}")
                    print(f"  指数系数: {factors['index_factor']:.2f}")
                    print(f"  回撤系数: {factors['drawdown_factor']:.2f}")

                    print(f"\n【仓位计算】")
                    print(f"  公式: 总仓位上限 = 情绪 × 主线 × 指数 × 回撤 × 最大仓位")
                    print(f"  盘后建议基础仓位: {base_position * 100:.1f}%")
                    print(f"  四因子计算仓位上限: {position_result['position_limit'] * 100:.1f}%")
                    print(f"  ★ 最终建议仓位: {position_result['final_position'] * 100:.1f}%")

                    print(f"\n【操作建议】")
                    print(f"  {position_result['suggestion']}")
                    print("="*70 + "\n")

                    server_title = f"【四因子仓位】{analysis_time_desc} - {position_result['final_position']*100:.0f}%"
                    server_msg = (
                        f"【{analysis_time_desc}】 定时四因子仓位分析\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"【市场情绪】\n"
                        f"  情绪评分: {sentiment_score} ({sentiment['market_type']})\n"
                        f"  上涨: {sentiment['up_count']} 只 | 下跌: {sentiment['down_count']} 只\n"
                        f"  涨停: {sentiment['limit_up_count']} 只 | 跌停: {sentiment['limit_down_count']} 只\n"
                        f"  平均涨幅: {sentiment['avg_change']:.2f}%\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"【四因子分解】\n"
                        f"  情绪系数: {factors['sentiment_factor']:.2f}\n"
                        f"  主线系数: {factors['mainline_factor']:.2f}\n"
                        f"  指数系数: {factors['index_factor']:.2f}\n"
                        f"  回撤系数: {factors['drawdown_factor']:.2f}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"【仓位计算】\n"
                        f"  盘后建议基础仓位: {base_position * 100:.1f}%\n"
                        f"  四因子计算仓位上限: {position_result['position_limit'] * 100:.1f}%\n"
                        f"  ★ 最终建议仓位: {position_result['final_position'] * 100:.1f}%\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"【操作建议】\n"
                        f"  {position_result['suggestion']}"
                    )
                    send_analysis_to_server(server_title, server_msg)

                    last_analysis_time = now

                if need_check_concepts:
                    for concept in concepts:
                        concept_stocks = concept_stock_map.get(concept, [])
                        if not concept_stocks:
                            continue

                        concept_quotes = []
                        for market, code in concept_stocks:
                            if code in quote_map:
                                concept_quotes.append(quote_map[code])

                        if concept_quotes:
                            analysis = analyze_concept_intraday(concept_quotes, concept)
                            if analysis:
                                concept_warning = warning.check_concept_breakout(analysis)
                                if concept_warning:
                                    warning.notify(concept_warning)

                    last_concept_check = now

                if need_check_stocks:
                    round_warnings = []
                    
                    for quote in quotes:
                        code = quote.get('code', '')
                        market = 1 if code.startswith('6') else 0
                        key = (market, code)

                        if key in ma5_data:
                            warning_info = warning.check_stock(quote, ma5_data[key])
                            if warning_info:
                                warning_info['name'] = stock_name_map.get(code, '')
                                
                                # 检查10分钟内是否已经预警过
                                last_time = last_warning_time.get(code)
                                if last_time is None or (now - last_time).total_seconds() >= 1800:
                                    round_warnings.append(warning_info)
                                    last_warning_time[code] = now
                    
                    # 合并一轮轮巡的预警结果，只发一条微信消息
                    if round_warnings:
                        warning.notify_batch(round_warnings)
                    
                    last_stock_check = now

            time.sleep(base_sleep_interval)

    except KeyboardInterrupt:
        print("\n\n停止监控")
    finally:
        tdx.disconnect()
        print("已断开连接")


if __name__ == "__main__":
    main()
