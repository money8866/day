"""
ETF主线轮动策略 - 生产版
策略: 1只持仓 + 60天调仓 + 20日动量 (夏普1.96)
ETF池: 37只行业ETF (全验证)
数据源: 通达信本地日线
"""
import os, datetime, pandas as pd, numpy as np, json

TDX_PATH = r"C:\new_tdx\vipdoc"
STATE_FILE = os.path.join(os.path.dirname(__file__), "etf_mainline_state.json")
MOM_PERIOD = 20
REBAL_DAYS = 60
TOP_N = 1

ETF_POOL = {
    '半导体': ('512480', 'sh'),
    '芯片': ('159995', 'sz'),
    '半导体设备': ('159516', 'sz'),
    '人工智能': ('159819', 'sz'),
    '软件': ('515230', 'sh'),
    '通信': ('515880', 'sh'),
    '消费电子': ('159732', 'sz'),
    '金融科技': ('159851', 'sz'),
    '游戏': ('159869', 'sz'),
    '新能源': ('516160', 'sh'),
    '光伏': ('515790', 'sh'),
    '储能': ('159566', 'sz'),
    '电池': ('159755', 'sz'),
    '新能源车': ('515030', 'sh'),
    '创新药': ('159992', 'sz'),
    '医疗器械': ('159883', 'sz'),
    '医药': ('512010', 'sh'),
    '军工': ('512660', 'sh'),
    '航空航天': ('159227', 'sz'),
    '机器人': ('562500', 'sh'),
    '有色金属': ('516650', 'sh'),
    '化工': ('159870', 'sz'),
    '煤炭': ('515220', 'sh'),
    '钢铁': ('515210', 'sh'),
    '电力': ('159611', 'sz'),
    '电网设备': ('561380', 'sh'),
    '消费': ('159928', 'sz'),
    '食品饮料': ('159736', 'sz'),
    '酒': ('512690', 'sh'),
    '家电': ('159996', 'sz'),
    '证券': ('512880', 'sh'),
    '银行': ('512800', 'sh'),
    '红利': ('515180', 'sh'),
    '黄金': ('518880', 'sh'),
    '沪深300': ('510300', 'sh'),
    '创业板': ('159915', 'sz'),
    '上证50': ('510050', 'sh'),
}


def parse_tdx(filepath):
    if not os.path.exists(filepath):
        return None
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = int.from_bytes(chunk[0:4], "little")
            close = int.from_bytes(chunk[16:20], "little") / 1000  # ETF price divisor
            dt = datetime.datetime.strptime(str(date_int), "%Y%m%d")
            data.append({"date": dt, "close": close})
    if not data:
        return None
    return pd.DataFrame(data).sort_values("date").reset_index(drop=True)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def count_trade_days(start_str, end_date):
    """Count actual trading days between two dates using a reference ETF"""
    ref_code, ref_market = "512880", "sh"
    ref_path = os.path.join(TDX_PATH, ref_market, "lday", f"{ref_market}{ref_code}.day")
    ref_df = parse_tdx(ref_path)
    if ref_df is None:
        return 0
    start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d")
    mask = (ref_df["date"] > start_dt) & (ref_df["date"] <= end_date)
    return len(ref_df[mask])


def main():
    today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Load all ETF data
    print("=" * 60)
    print("  ETF主线轮动策略 (1只+60天+20日动量)")
    print("=" * 60)

    etf_data = {}
    etf_names = {}
    etf_codes_inv = {}  # code -> short name
    skipped = []

    for name, (code, market) in ETF_POOL.items():
        filepath = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
        df = parse_tdx(filepath)
        if df is not None and len(df) > MOM_PERIOD:
            etf_data[code] = df
            etf_names[code] = name
            etf_codes_inv[code] = name
        else:
            skipped.append(name)

    if skipped:
        print(f"  [WARN] 缺失数据: {', '.join(skipped)}")

    # Check if data is up to date (within 3 business days)
    max_date = max(df["date"].max() for df in etf_data.values())
    gap = (today - max_date).days
    print(f"  数据截止: {max_date.strftime('%Y-%m-%d')} (距今{gap}天)")
    if gap > 5:
        print("  [WARN] 数据可能过期，请确认通达信已更新!")

    # Calculate momentum for all ETFs
    rankings = []
    for code, df in etf_data.items():
        mom = df["close"].pct_change(MOM_PERIOD).iloc[-1] * 100 if len(df) > MOM_PERIOD else None
        if mom is not None and pd.notna(mom):
            latest_close = df["close"].iloc[-1]
            prev_close = df["close"].iloc[-2] if len(df) >= 2 else latest_close
            day_chg = (latest_close - prev_close) / prev_close * 100
            rankings.append({
                "code": code,
                "name": etf_names[code],
                "close": latest_close,
                "momentum_20d": round(mom, 2),
                "day_chg": round(day_chg, 2),
            })

    rankings.sort(key=lambda x: x["momentum_20d"], reverse=True)

    print(f"\n  --- 动量排名 TOP 10 (20日涨幅) ---")
    for i, r in enumerate(rankings[:10]):
        print(f"  {i+1:>2}. {r['name']:<8s} {r['code']}  动量:{r['momentum_20d']:>+7.2f}%  "
              f"收盘:{r['close']:.3f}  日涨:{r['day_chg']:>+5.2f}%")

    # Current state
    state = load_state()
    need_rebalance = False
    days_since = 0

    if state is None:
        # First run - initialize
        need_rebalance = True
        print(f"\n  [首次运行] 初始化策略...")
    else:
        days_since = count_trade_days(state["last_rebalance_date"], today)
        print(f"\n  当前持仓: {state['holding_name']} ({state['holding_code']})")
        print(f"  买入日期: {state['last_rebalance_date']}")
        print(f"  买入价格: {state['buy_price']:.3f}")
        print(f"  已过交易日: {days_since}/{REBAL_DAYS}")
        if state.get("holding_code"):
            hc = state["holding_code"]
            if hc in etf_data:
                latest = etf_data[hc]["close"].iloc[-1]
                pnl = (latest - state["buy_price"]) / state["buy_price"] * 100
                print(f"  当前价格: {latest:.3f}  持仓收益: {pnl:+.2f}%")
        if days_since >= REBAL_DAYS:
            need_rebalance = True

    # Rebalance logic
    if need_rebalance:
        target = rankings[0]
        print(f"\n  {'='*40}")
        print(f"  [调仓信号] 需要调仓!")
        print(f"  目标: {target['name']} ({target['code']})")
        print(f"  动量: {target['momentum_20d']:+.2f}%")
        print(f"  现价: {target['close']:.3f}")

        if state and state.get("holding_code"):
            old = state["holding_code"]
            if old in etf_data:
                old_close = etf_data[old]["close"].iloc[-1]
                old_pnl = (old_close - state["buy_price"]) / state["buy_price"] * 100
                print(f"  卖出: {state['holding_name']}  收益: {old_pnl:+.2f}%")

        new_state = {
            "last_rebalance_date": max_date.strftime("%Y-%m-%d"),
            "holding_code": target["code"],
            "holding_name": target["name"],
            "buy_price": target["close"],
            "momentum_at_buy": target["momentum_20d"],
            "rebalance_count": (state.get("rebalance_count", 0) + 1) if state else 1,
        }
        save_state(new_state)
        print(f"  状态已更新! 累计第{new_state['rebalance_count']}次调仓")
    else:
        remain = REBAL_DAYS - days_since
        print(f"\n  距下次调仓还有 {remain} 个交易日")
        next_top = rankings[0]
        if next_top["code"] != state.get("holding_code"):
            print(f"  [提示] 当前动量第一: {next_top['name']} ({next_top['momentum_20d']:+.2f}%)")
            print(f"  与持仓不同，下次调仓将切换")

    # Bottom 5 warning
    print(f"\n  --- 动量垫底 5 ---")
    for i, r in enumerate(rankings[-5:]):
        print(f"  {len(rankings)-4+i:>2}. {r['name']:<8s} {r['code']}  动量:{r['momentum_20d']:>+7.2f}%")

    print(f"\n  {'='*60}")


if __name__ == "__main__":
    main()
