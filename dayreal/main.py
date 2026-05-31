import time
from datetime import datetime
from config import Config
from tdx_data import TdxData
from warning_system import WarningSystem
from technical_analysis import calculate_change_percent, detect_bullish_engulfing, detect_new_high
from position_optimizer import FourFactorPositionManager, TradingTimeHelper
import requests
import os
from dotenv import load_dotenv
from sector_analyzer import get_sector_market_sentiment, calculate_composite_sentiment

load_dotenv("../config/.env")


def should_analyze_at_time(check_time, analyzed_time, target_hour, target_minute):
    """
    判断是否在指定时间点进行分析（每天每个时间点只执行一次）
    
    Args:
        check_time: 当前时间 (datetime)
        analyzed_time: 最后一次分析时间 (datetime, 可为None)
        target_hour: 目标小时
        target_minute: 目标分钟
    
    Returns:
        (bool, str): (是否应该分析, 时间描述)
    """
    # 检查是否在目标时间点前后2分钟内
    if check_time.hour == target_hour and abs(check_time.minute - target_minute) <= 2:
        if analyzed_time is None:
            return True, f"{target_hour:02d}:{target_minute:02d}"
        # 检查是否已经在当天的这个时间点执行过分析
        if analyzed_time.date() != check_time.date() or \
           analyzed_time.hour != target_hour or analyzed_time.minute != target_minute:
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
    print("股票盘中预警系统 - 阳包阴/创新高监测版")
    print("="*70)

    config = Config()
    tdx = TdxData(config.config)
    warning = WarningSystem(config)
    position_manager = FourFactorPositionManager(max_position=1.0)

    last_analysis_time = None
    base_position, position_notes = config.load_base_position()

    individual_stocks = config.load_stocks()
    stock_check_interval = config.get('warning.stock_check_interval', 10)

    print(f"\n配置信息:")
    print(f"个股文件: {config.get('files.stocks_csv', 'stocks.csv')}")
    print(f"个股: {individual_stocks}")
    print(f"仓位配置文件: {config.get('files.position_csv', 'position_config.csv')}")
    print(f"盘后建议基础仓位: {base_position * 100:.1f}% ({position_notes})")
    print(f"个股检查间隔: {stock_check_interval}秒")
    print(f"\n四因子分析时间点: 10:00, 11:30, 13:30, 14:30")
    print(f"\n开始连接通达信服务器...")

    if not tdx.connect():
        print("连接失败，程序退出")
        return

    print("连接成功！\n")

    stock_list = []
    stock_name_map = {}
    stock_kline_data = {}

    for stock_item in individual_stocks:
        code = stock_item['code']
        name = stock_item.get('name', '')
        market = 1 if code.startswith('6') else 0
        stock_list.append((market, code))
        stock_name_map[code] = name

    stock_list = list(set(stock_list))
    print(f"\n总共监控 {len(stock_list)} 只股票\n")

    print("正在获取历史数据...")
    for market, code in stock_list:
        klines = tdx.get_history_kline(market, code, category=9, count=60)
        print(f"  {stock_name_map[code]} ({code}) 历史K线: {len(klines)}条")
        if klines:
            stock_kline_data[(market, code)] = klines
    print(f"已获取 {len(stock_kline_data)} 只股票的历史数据\n")

    print("开始监控... (按 Ctrl+C 停止)\n")

    last_stock_check = datetime.now()
    base_sleep_interval = 2
    
    last_warning_time = {}
    notified_patterns = set()

    initial_time = datetime.now()
    if 9 <= initial_time.hour < 15 and not (initial_time.hour == 11 and initial_time.minute > 30) and initial_time.hour != 12:
        print("【初始化】检测到当前在交易时间段内，立即执行一次大盘分析...")
        # 使用四大指数计算市场情绪（而不是20只自选股）
        index_quotes = tdx.get_index_quotes()
        if index_quotes:
            quote_data = []
            for q in index_quotes:
                change_pct = calculate_change_percent(q)
                quote_data.append({
                    "code": q.get("code", ""),
                    "name": q.get("name", ""),
                    "price": q.get("price", 0),
                    "change_pct": change_pct
                })

            # 计算指数情绪
            sentiment = position_manager.sentiment_analyzer.analyze_opening_30min(quote_data)
            index_sentiment_score = sentiment["sentiment_score"]
            
            # 计算板块情绪（使用本地缓存）
            sector_sentiment = get_sector_market_sentiment()
            
            # 计算综合情绪评分
            composite_score = calculate_composite_sentiment(index_sentiment_score, sector_sentiment)
            sentiment_score = int(composite_score)  # 转换为整数
            
            index_change = tdx.calculate_index_change()
            drawdown = 0.0

            position_result = position_manager.get_position_suggestion(
                base_position=base_position,
                sentiment_score=sentiment_score,
                concept_analysis=None,
                index_change=index_change,
                drawdown=drawdown
            )

            print("\n" + "="*70)
            print(f"【初始化】 首次大盘分析")
            print("="*70)
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

            server_title = f"【初始化】首次分析 - {position_result['final_position']*100:.0f}%"
            server_msg = (
                f"【初始化】首次大盘分析\n"

                f"【市场情绪】\n"
                f"  情绪评分: {sentiment_score} ({sentiment['market_type']})\n"
                f"  上涨: {sentiment['up_count']} 只 | 下跌: {sentiment['down_count']} 只\n"
                f"  涨停: {sentiment['limit_up_count']} 只 | 跌停: {sentiment['limit_down_count']} 只\n"
                f"  平均涨幅: {sentiment['avg_change']:.2f}%\n"
                f"【四因子分解】\n"
                f"  情绪系数: {factors['sentiment_factor']:.2f}\n"
                f"  主线系数: {factors['mainline_factor']:.2f}\n"
                f"  指数系数: {factors['index_factor']:.2f}\n"
                f"  回撤系数: {factors['drawdown_factor']:.2f}\n"
                f"【仓位计算】\n"
                f"  盘后建议基础仓位: {base_position * 100:.1f}%\n"
                f"  四因子计算仓位上限: {position_result['position_limit'] * 100:.1f}%\n"
                f"  ★ 最终建议仓位: {position_result['final_position'] * 100:.1f}%\n"
                f"【操作建议】\n"
                f"  {position_result['suggestion']}"
            )
            send_analysis_to_server(server_title, server_msg.replace("\n", "\n\n"))

    try:
        while True:
            now = datetime.now()

            if not (9 <= now.hour < 15) or (now.hour == 11 and now.minute > 30) or (now.hour == 12):
                time.sleep(base_sleep_interval)
                continue

            need_check_stocks = (now - last_stock_check).total_seconds() >= stock_check_interval

            need_time_analysis = False
            analysis_time_desc = ""

            time_points = [(10, 0), (11, 30), (13, 30), (14, 30)]
            for hour, minute in time_points:
                
                should_do, desc = should_analyze_at_time(now, last_analysis_time, hour, minute)
                if should_do:
                    need_time_analysis = True
                    analysis_time_desc = desc
                    break

            if need_check_stocks or need_time_analysis:
                quotes = tdx.get_stock_quotes(stock_list)
                if not quotes:
                    time.sleep(base_sleep_interval)
                    continue

                quote_map = {q.get('code', ''): q for q in quotes}

                if need_time_analysis:
                    print("\n" + "="*70)
                    print(f"【{analysis_time_desc}】 定时四因子仓位分析")
                    print("="*70)

                    # 计算指数情绪
                    index_quotes = tdx.get_index_quotes()
                    quote_data = []
                    if index_quotes:
                        for q in index_quotes:
                            change_pct = calculate_change_percent(q)
                            quote_data.append({
                                "code": q.get("code", ""),
                                "name": q.get("name", ""),
                                "price": q.get("price", 0),
                                "change_pct": change_pct
                            })

                    index_sentiment = position_manager.sentiment_analyzer.analyze_opening_30min(quote_data)
                    index_sentiment_score = index_sentiment["sentiment_score"]
                    
                    # 计算板块情绪（使用本地缓存）
                    sector_sentiment = get_sector_market_sentiment()
                    
                    # 计算综合情绪评分
                    composite_score = calculate_composite_sentiment(index_sentiment_score, sector_sentiment)
                    sentiment_score = int(composite_score)  # 转换为整数

                    index_change = tdx.calculate_index_change()
                    drawdown = 0.0

                    position_result = position_manager.get_position_suggestion(
                        base_position=base_position,
                        sentiment_score=sentiment_score,
                        concept_analysis=None,
                        index_change=index_change,
                        drawdown=drawdown
                    )

                    print(f"\n【市场情绪】")
                    print(f"  情绪评分: {sentiment_score} ({index_sentiment['market_type']})")
                    if sector_sentiment:
                        print(f"  (指数:{index_sentiment_score} | 板块:{sector_sentiment['sentiment_score']:.1f})")
                    else:
                        print(f"  (指数:{index_sentiment_score} | 板块:无数据)")
                    print(f"  上涨: {index_sentiment['up_count']} 只  下跌: {index_sentiment['down_count']} 只")
                    print(f"  涨停: {index_sentiment['limit_up_count']} 只  跌停: {index_sentiment['limit_down_count']} 只")
                    print(f"  平均涨幅: {index_sentiment['avg_change']:.2f}%")

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
                        
                        f"#【市场情绪】\n"
                        f"  情绪评分: {sentiment_score} ({index_sentiment['market_type']})\n"
                        f"  (指数:{index_sentiment_score} | 板块:{sector_sentiment['sentiment_score']:.1f})\n" if sector_sentiment else f"  (指数:{index_sentiment_score} | 板块:无)\n"
                        f"  上涨: {index_sentiment['up_count']} 只 | 下跌: {index_sentiment['down_count']} 只\n"
                        f"  涨停: {index_sentiment['limit_up_count']} 只 | 跌停: {index_sentiment['limit_down_count']} 只\n"
                        f"  平均涨幅: {index_sentiment['avg_change']:.2f}%\n"
                        
                        f"#【四因子分解】\n"
                        f"  情绪系数: {factors['sentiment_factor']:.2f}\n"
                        f"  主线系数: {factors['mainline_factor']:.2f}\n"
                        f"  指数系数: {factors['index_factor']:.2f}\n"
                        f"  回撤系数: {factors['drawdown_factor']:.2f}\n"
                        
                        f"#【仓位计算】\n"
                        #f"  盘后建议基础仓位: {base_position * 100:.1f}%\n"
                        f"  四因子计算仓位上限: {position_result['position_limit'] * 100:.1f}%\n"
                        f"  ★ 最终建议仓位: {position_result['final_position'] * 100:.1f}%\n"
                        
                        f"#【操作建议】\n"
                        f"  {position_result['suggestion']}"
                    )
                    send_analysis_to_server(server_title, server_msg.replace("\n", "\n\n"))

                    last_analysis_time = now

                if need_check_stocks:
                    round_warnings = []
                    
                    for quote in quotes:
                        code = quote.get('code', '')
                        market = 1 if code.startswith('6') else 0
                        key = (market, code)
                        current_price = quote.get('price', 0)

                        if key in stock_kline_data:
                            klines = stock_kline_data[key]
                            
                            engulfing_detected, engulfing_info = detect_bullish_engulfing(klines)
                            new_high_detected, new_high_info = detect_new_high(klines, days=60)
                            
                            warning_type = None
                            warning_details = None
                            
                            if engulfing_detected:
                                warning_type = "阳包阴"
                                warning_details = engulfing_info
                            elif new_high_detected:
                                warning_type = "日线创新高"
                                warning_details = new_high_info
                            
                            if warning_type:
                                pattern_key = f"\n\n**{code}_{warning_type}"
                                if pattern_key in notified_patterns:
                                    continue
                                
                                last_time = last_warning_time.get(code)
                                if last_time is None or (now - last_time).total_seconds() >= 1800:
                                    notified_patterns.add(pattern_key)
                                    warning_info = {
                                        'code': code,
                                        'name': stock_name_map.get(code, ''),
                                        'price': current_price,
                                        'type': warning_type,
                                        'details': warning_details,
                                        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    }
                                    round_warnings.append(warning_info)
                                    last_warning_time[code] = now
                    
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
