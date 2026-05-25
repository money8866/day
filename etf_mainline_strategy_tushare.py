"""
ETF主线轮动策略 - Tushare版 (收盘后运行)
策略: 1只持仓 + 60天调仓 + 20日动量 (夏普1.96)
ETF池: 37只行业ETF (全验证)
数据源: Tushare API
"""
from dotenv import load_dotenv
import os, datetime, pandas as pd, numpy as np, json, time
load_dotenv("config/.env") 
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
WECHAT_KEY = os.getenv("WECHAT_KEY")
import tushare as ts
import requests
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

STATE_FILE = os.path.join(os.path.dirname(__file__), "etf_mainline_state_tushare.json")
MOM_PERIOD = 20
REBAL_DAYS = 60
TOP_N = 1

ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '黄金': '518880', '沪深300': '510300', '创业板': '159915',
    '上证50': '510050','双创ETF':'588300','科创ETF':'588050',
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_etf_daily(ts_pro, codes, start_date="20200101"):
    """Batch fetch daily data for all ETFs"""
    all_data = {}
    code_list = list(codes.values())
    # Tushare fund_daily supports batch by ts_code
    for i in range(0, len(code_list), 10):
        batch = code_list[i:i+10]
        batch_str = ",".join(batch)
        try:
            df = ts_pro.fund_daily(ts_code=batch_str, start_date=start_date,
                                   fields="ts_code,trade_date,close")
            if df is not None and len(df) > 0:
                for code in batch:
                    sub = df[df["ts_code"] == code].copy()
                    if len(sub) == 0:
                        continue
                    sub["trade_date"] = pd.to_datetime(sub["trade_date"], format="%Y%m%d")
                    sub = sub.sort_values("trade_date").reset_index(drop=True)
                    all_data[code] = sub
            time.sleep(0.3)  # rate limit
        except Exception as e:
            print(f"  [WARN] fetch error: {e}")
            time.sleep(1)
    return all_data

def get_last_trade_date():

    now = datetime.datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260518" # for test

print("当前交易日:", TRADE_DATE)


# =========================
# 微信
# =========================
def send_wechat(msg, key):

    url = f"https://sctapi.ftqq.com/{key}.send"

    data = {
        "title": f"ETF每日分析 - {TRADE_DATE}",
        "desp": msg
    }

    requests.post(url, data=data)
    
def main():

    today = datetime.datetime.strptime(TRADE_DATE, "%Y%m%d")
    result_message = ""

    print("=" * 60)
    print("  ETF主线轮动策略 Tushare版 (1只+60天+20日动量)")
    print("=" * 60)

    result_message += f"  ETF主线轮动策略(1只+60天+20日动量)\n"

    # Build ts_code mapping: 512480 -> 512480.SH, 159995 -> 159995.SZ
    codes_ts = {}
    for name, code in ETF_POOL.items():
        if code.startswith("5") or code.startswith("6"):
            codes_ts[code] = f"{code}.SH"
        else:
            codes_ts[code] = f"{code}.SZ"

    # Fetch data one by one
    print("  正在获取Tushare数据...")
    all_data = {}
    for name, code in ETF_POOL.items():
        ts_code = codes_ts[code]
        try:
            df = pro.fund_daily(ts_code=ts_code,
                                start_date=(today - datetime.timedelta(days=120)).strftime("%Y%m%d"),
                                fields="ts_code,trade_date,close")
            if df is not None and len(df) > 0:
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
                all_data[code] = df
            time.sleep(0.25)
        except Exception as e:
            print(f"  [WARN] {name}({ts_code}) error: {e}")
            time.sleep(0.5)

    skipped = [name for name, code in ETF_POOL.items() if code not in all_data]
    if skipped:
        print(f"  [WARN] 缺失数据: {', '.join(skipped)}")

    max_date = max(df["trade_date"].max() for df in all_data.values())
    gap = (today - max_date).days
    print(f"  数据截止: {max_date.strftime('%Y-%m-%d')} (距今{gap}天)")

    # Calculate momentum
    # Build code->name lookup from ETF_POOL (values are codes, keys are names)
    code_to_name = {v: k for k, v in ETF_POOL.items()}

    rankings = []
    for code, df in all_data.items():
        if len(df) <= MOM_PERIOD:
            continue
        mom = df["close"].pct_change(MOM_PERIOD).iloc[-1] * 100
        if pd.isna(mom):
            continue
        latest = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) >= 2 else latest
        day_chg = (latest - prev) / prev * 100
        rankings.append({
            "code": code,
            "name": code_to_name.get(code, code),
            "close": latest,
            "momentum_20d": round(mom, 2),
            "day_chg": round(day_chg, 2),
        })

    rankings.sort(key=lambda x: x["momentum_20d"], reverse=True)

    print(f"\n  --- 动量排名 TOP 10 (20日涨幅) ---")
    for i, r in enumerate(rankings[:10]):
        print(f"  {i+1:>2}. {r['name']:<8s} {r['code']}  动量:{r['momentum_20d']:>+7.2f}%  "
              f"收盘:{r['close']:.3f}  日涨:{r['day_chg']:>+5.2f}%\n")

    result_message += f"  ---动量排名 TOP 5 ---\n"
    for i, r in enumerate(rankings[:5]):
        result_message += f"  {i+1:>2}. {r['name']:<8s} {r['code']}  动量:{r['momentum_20d']:>+7.2f}%  收盘:{r['close']:.3f}  日涨:{r['day_chg']:>+5.2f}%\n"

    # Count trade days
    def count_trade_days(start_str, end_date):
        ref = all_data.get("512880")
        if ref is None:
            return 0
        start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d")
        mask = (ref["trade_date"] > start_dt) & (ref["trade_date"] <= end_date)
        return len(ref[mask])

    # State
    state = load_state()
    need_rebalance = False
    days_since = 0


    if state is None:
        need_rebalance = True
        print(f"\n  [首次运行] 初始化策略...")
    else:
        days_since = count_trade_days(state["last_rebalance_date"], today)
        print(f"\n  当前持仓: {state['holding_name']} ({state['holding_code']})")
        result_message += f"**当前持仓:{state['holding_name']} ({state['holding_code']})**\n"
        
        print(f"  买入日期: {state['last_rebalance_date']}")
        result_message += f"买入日期 {state['last_rebalance_date']}\n"
        
        print(f"  买入价格: {state['buy_price']:.3f}")
        result_message += f"买入价格 {state['buy_price']:.3f}\n"
        
        print(f"  已过交易日: {days_since}/{REBAL_DAYS}")
        result_message += f"已过交易日 {days_since}/{REBAL_DAYS}\n"

        hc = state.get("holding_code")
        if hc and hc in all_data:
            latest = all_data[hc]["close"].iloc[-1]
            pnl = (latest - state["buy_price"]) / state["buy_price"] * 100
            print(f"  当前价格: {latest:.3f}  持仓收益: {pnl:+.2f}%")
            result_message += f"  持仓收益 {pnl:+.2f}%" 
        if days_since >= REBAL_DAYS:
            need_rebalance = True

    if need_rebalance:
        target = rankings[0]
        print(f"\n  {'='*40}")
        result_message += f"{'='*40}\n"
        
        print(f"  [调仓信号] 需要调仓!")
        result_message += f"[调仓信号] 需要调仓!\n"
        
        print(f"  目标: {target['name']} ({target['code']})")
        result_message += f"目标 {target['name']} ({target['code']})\n"
        
        print(f"  动量: {target['momentum_20d']:+.2f}%")
        result_message += f"动量 {target['momentum_20d']:+.2f}%\n"
        
        print(f"  现价: {target['close']:.3f}")
        result_message += f"现价 {target['close']:.3f}\n"
        
        if state and state.get("holding_code"):
            old = state["holding_code"]
            if old in all_data:
                old_close = all_data[old]["close"].iloc[-1]
                old_pnl = (old_close - state["buy_price"]) / state["buy_price"] * 100
                print(f"  卖出: {state['holding_name']}  收益: {old_pnl:+.2f}%\n")
                result_message += f"卖出 {state['holding_name']}  收益 {old_pnl:+.2f}%\n"

        new_state = {
            "last_rebalance_date": max_date.strftime("%Y-%m-%d"),
            "holding_code": target["code"],
            "holding_name": target["name"],
            "buy_price": target["close"],
            "momentum_at_buy": target["momentum_20d"],
            "rebalance_count": (state.get("rebalance_count", 0) + 1) if state else 1,
        }
        save_state(new_state)
        result_message += f"状态已更新! 累计第{new_state['rebalance_count']}次调仓\n"
        print(f"状态已更新! 累计第{new_state['rebalance_count']}次调仓")
    else:
        remain = REBAL_DAYS - days_since
        print(f"\n  距下次调仓还有 {remain} 个交易日")
        result_message += f"\n  距下次调仓还有 {remain} 个交易日\n"
        
        next_top = rankings[0]
        if next_top["code"] != state.get("holding_code"):
            result_message += f"[提示] 当前动量第一: {next_top['name']} ({next_top['momentum_20d']:+.2f}%)\n"
            print(f"  [提示] 当前动量第一: {next_top['name']} ({next_top['momentum_20d']:+.2f}%)")
            print(f"  与持仓不同，下次调仓将切换")
            result_message += f"与持仓不同，下次调仓将切换\n"   

    print(f"\n  --- 动量垫底 5 ---")
    for i, r in enumerate(rankings[-5:]):
        print(f"  {len(rankings)-4+i:>2}. {r['name']:<8s} {r['code']}  动量:{r['momentum_20d']:>+7.2f}%")
        
    print(f"\n  {'='*60}")
    
    send_wechat(
        result_message.replace("\n", "\n\n"),
        os.getenv("WECHAT_SCKEY")
    )   


if __name__ == "__main__":
    main()
