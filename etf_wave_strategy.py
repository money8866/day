import datetime
import pandas as pd
import tushare as ts

# ==================== 配置区域 ====================
TS_TOKEN = "bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d"  # 替换为你的 Tushare token
ETF_CODE = "159531.SZ"  # 中证2000ETF (南方), 也可以换成 562000.SH (华泰柏瑞)
START_DATE = "20240101"  # 开始数据时间

# 策略参数
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0
MOM_PERIOD = 20
# =================================================


def get_etf_data(token, code, start_date):
    """从 Tushare 获取 ETF 日线数据"""
    ts.set_token(token)
    pro = ts.pro_api()

    # 获取日线行情 (Tushare 中 fund_daily 接口适用于 ETF)
    df = pro.fund_daily(
        ts_code=code,
        start_date=start_date,
        end_date=datetime.datetime.now().strftime("%Y%m%d"),
    )

    if df.empty:
        raise ValueError(
            "未能获取到数据，请检查Token是否正确、积分是否足够或代码是否输入正确。"
        )

    # 按照日期正序排列 (从远到近)
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 统一列名，适应策略脚本
    df = df.rename(
        columns={
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "vol": "volume",
        }
    )
    return df


def generate_next_day_signal(df):
    """计算策略核心指标并输出次日操作指令"""
    # 1. 计算 ATR
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["close"].shift(1)).abs()
    df["tr3"] = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(window=ATR_PERIOD).mean()

    # 2. 计算动能和跟踪止损线
    df["momentum"] = df["close"] - df["close"].shift(MOM_PERIOD)
    df["highest_close"] = df["close"].rolling(window=MOM_PERIOD).max()
    df["long_stop_line"] = df["highest_close"] - (df["atr"] * ATR_MULTIPLIER)

    # 3. 循环生成历史信号以确保持仓状态连续
    position = 0
    signals = []

    for idx, row in df.iterrows():
        current_close = row["close"]
        current_mom = row["momentum"]
        current_stop = row["long_stop_line"]
        highest_c = row["highest_close"]

        if pd.isna(current_mom) or pd.isna(current_stop):
            signals.append(0)
            continue

        if position == 0:
            # 买入条件：动能为正 且 价格接近或突破近期高点
            if current_mom > 0 and current_close > highest_c * 0.98:
                position = 1
        elif position == 1:
            # 卖出条件：跌破动态下轨 或 动能转负
            if current_close < current_stop or current_mom < 0:
                position = 0

        signals.append(position)

    df["signal"] = signals
    df["action"] = df["signal"].diff().fillna(0)

    return df


# ==================== 主程序运行 ====================
if __name__ == "__main__":
    print(f"正在从 Tushare 获取 {ETF_CODE} 的数据...")
    data = get_etf_data(TS_TOKEN, ETF_CODE, START_DATE)

    print("数据获取成功，正在计算最新波段信号...")
    result_df = generate_next_day_signal(data)

    # 提取最后一行（即今天收盘后的最新数据）
    today_status = result_df.iloc[-1]
    last_trade_date = today_status["trade_date"]

    # 判断持仓状态文本
    status_text = "【持仓中】" if today_status["signal"] == 1 else "【空仓观望】"

    print("\n" + "=" * 40)
    print("【中证2000 ETF 量化波段盯盘助手】")
    print(f"数据更新至截至日期: {last_trade_date}")
    print(f"今日收盘价: {today_status['close']}")
    print(f"当前策略动态止损线: {round(today_status['long_stop_line'], 3)}")
    print(f"当前持仓状态: {status_text}")
    print("=" * 40)

    # 判断明天的操作
    # 注意：action 是今天的信号减去昨天的信号。
    if today_status["action"] == 1:
        print(
            "[买入信号] 策略今天发出【买入】信号！请在明天（次日）开盘后找机会逢低买入。"
        )
    elif today_status["action"] == -1:
        print(
            "[卖出信号] 策略今天发出【卖出】信号！请在明天（次日）开盘后立即清仓卖出，规避风险。"
        )
    else:
        if today_status["signal"] == 1:
            print(
                "[持有] 没有新信号。策略目前处于持仓期，明天请【继续持有】。"
            )
        else:
            print(
                "[观望] 没有新信号。策略目前处于空仓期，明天请【继续空仓观望】。"
            )
    print("=" * 40)
