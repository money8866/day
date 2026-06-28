"""
ETF主线轮动策略 - Tushare版 (收盘后运行)
策略: 多因子动量评分 (动量+量能+波动率+相对强弱)
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

BENCHMARK_CODE = '510300'


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_last_trade_date():
    now = datetime.datetime.now()
    if now.hour < 15:
        query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)


def send_wechat(msg, key):
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": f"ETF每日分析 - {TRADE_DATE}",
        "desp": msg
    }
    requests.post(url, data=data)


def send_pushplus(msg, token):
    """通过 PushPlus 推送微信消息（支持markdown）"""
    if not token:
        return
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": f"ETF每日分析 - {TRADE_DATE}",
        "content": msg,
        "template": "markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get("code") == 200:
            print("✅ PushPlus 已发送")
        else:
            print(f"⚠️ PushPlus 发送失败: {result.get('msg', '未知错误')}")
    except Exception as e:
        print(f"⚠️ PushPlus 异常: {e}")


def normalize_score(series):
    """将序列归一化到0-100分"""
    if len(series) < 2:
        return pd.Series([50] * len(series), index=series.index)
    min_val = series.min()
    max_val = series.max()
    if max_val == min_val:
        return pd.Series([50] * len(series), index=series.index)
    return (series - min_val) / (max_val - min_val) * 100


def calculate_multi_factor_score(df, benchmark_df, mom_period=20):
    """
    计算多因子综合评分
    因子1: 20日动量 (40%)
    因子2: 量能配合 (25%) - 近期量能是否放大
    因子3: 风险调整收益 (20%) - 动量/波动率
    因子4: 相对强弱 (15%) - 相对于沪深300的表现
    """
    if len(df) < mom_period + 5:
        return None

    close = df['close']

    mom_20d = close.pct_change(mom_period).iloc[-1] * 100

    vol = df.get('vol', None)
    if vol is None or len(vol) < mom_period:
        vol_score = 50
    else:
        recent_vol_avg = vol.tail(5).mean()
        hist_vol_avg = vol.tail(mom_period).mean()
        vol_ratio = recent_vol_avg / (hist_vol_avg + 1e-6)
        vol_score = min(vol_ratio * 50, 100)

    daily_returns = close.pct_change().dropna()
    if len(daily_returns) >= mom_period:
        volatility = daily_returns.tail(mom_period).std() * np.sqrt(252) * 100
        if volatility > 0:
            risk_adj_score = min(mom_20d / volatility * 10, 100)
        else:
            risk_adj_score = 50
    else:
        risk_adj_score = 50

    if len(benchmark_df) >= mom_period + 1:
        bm_return = benchmark_df['close'].pct_change(mom_period).iloc[-1] * 100
        relative_strength = mom_20d - bm_return
        rel_score = 50 + relative_strength
        rel_score = max(0, min(100, rel_score))
    else:
        rel_score = 50

    total_score = (
        mom_20d * 0.40 +
        vol_score * 0.25 +
        risk_adj_score * 0.20 +
        rel_score * 0.15
    )

    return {
        'momentum': round(mom_20d, 2),
        'vol_score': round(vol_score, 2),
        'risk_adj': round(risk_adj_score, 2),
        'rel_strength': round(rel_score, 2),
        'total_score': round(total_score, 2)
    }


def main():
    today = datetime.datetime.strptime(TRADE_DATE, "%Y%m%d")
    result_message = ""

    print("=" * 60)
    print("  ETF主线轮动策略 Tushare版 (多因子动量评分)")
    print("=" * 60)

    result_message += f"  ETF主线轮动策略(多因子动量评分)\n"
    result_message += f"  因子权重: 动量40% + 量能25% + 风险调整20% + 相对强弱15%\n\n"

    codes_ts = {}
    for name, code in ETF_POOL.items():
        if code.startswith("5") or code.startswith("6"):
            codes_ts[code] = f"{code}.SH"
        else:
            codes_ts[code] = f"{code}.SZ"

    print("  正在获取Tushare数据...")
    all_data = {}
    for name, code in ETF_POOL.items():
        ts_code = codes_ts[code]
        try:
            df = pro.fund_daily(ts_code=ts_code,
                                start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                fields="ts_code,trade_date,close")
            if df is not None and len(df) > 0:
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
                all_data[code] = df
            time.sleep(0.25)
        except Exception as e:
            print(f"  [WARN] {name}({ts_code}) error: {e}")
            time.sleep(0.5)

    bm_ts = "510300.SH"
    try:
        benchmark_df = pro.fund_daily(ts_code=bm_ts,
                                       start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                       fields="ts_code,trade_date,close")
        if benchmark_df is not None and len(benchmark_df) > 0:
            benchmark_df["trade_date"] = pd.to_datetime(benchmark_df["trade_date"], format="%Y%m%d")
            benchmark_df = benchmark_df.sort_values("trade_date").reset_index(drop=True)
        else:
            benchmark_df = None
    except Exception as e:
        print(f"  [WARN] 沪深300数据获取失败: {e}")
        benchmark_df = None

    skipped = [name for name, code in ETF_POOL.items() if code not in all_data]
    if skipped:
        print(f"  [WARN] 缺失数据: {', '.join(skipped)}")

    max_date = max(df["trade_date"].max() for df in all_data.values())
    gap = (today - max_date).days
    print(f"  数据截止: {max_date.strftime('%Y-%m-%d')} (距今{gap}天)")

    code_to_name = {v: k for k, v in ETF_POOL.items()}

    rankings = []
    for code, df in all_data.items():
        bm_for_etf = benchmark_df if benchmark_df is not None else None
        factors = calculate_multi_factor_score(df, bm_for_etf, MOM_PERIOD)
        if factors is None:
            continue

        latest = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) >= 2 else latest
        day_chg = (latest - prev) / prev * 100

        rankings.append({
            "code": code,
            "name": code_to_name.get(code, code),
            "close": latest,
            "day_chg": round(day_chg, 2),
            **factors
        })

    rankings.sort(key=lambda x: x['total_score'], reverse=True)

    print(f"\n  --- 多因子综合评分 TOP 10 ---")
    print(f"  {'序号':>2} {'名称':<8} {'代码':<8} {'综合分':>6} {'动量':>7} {'量能':>6} {'风险':>6} {'相对':>6}")
    print(f"  {'-'*60}")

    for i, r in enumerate(rankings[:10]):
        print(f"  {i+1:>2}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f} "
              f"{r['momentum']:>+7.2f}% {r['vol_score']:>6.1f} {r['risk_adj']:>6.1f} {r['rel_strength']:>6.1f}")

    result_message += f"  ---多因子评分 TOP 5 ---\n"
    for i, r in enumerate(rankings[:5]):
        result_message += f"  {i+1}. {r['name']}({r['code']}) 综合分:{r['total_score']:.1f} 动量:{r['momentum']:+.2f}%\n"

    def count_trade_days(start_str, end_date):
        ref = all_data.get("512880")
        if ref is None:
            return 0
        start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d")
        mask = (ref["trade_date"] > start_dt) & (ref["trade_date"] <= end_date)
        return len(ref[mask])

    state = load_state()
    need_rebalance = False
    days_since = 0

    if state is None:
        need_rebalance = True
        print(f"\n  [首次运行] 初始化策略...")
    else:
        days_since = count_trade_days(state["last_rebalance_date"], today)
        print(f"\n  当前持仓: {state['holding_name']} ({state['holding_code']})")
        result_message += f"\n**当前持仓:{state['holding_name']} ({state['holding_code']})**\n"

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

        print(f"  综合评分: {target['total_score']:.1f}")
        result_message += f"综合评分 {target['total_score']:.1f}\n"

        print(f"  动量: {target['momentum']:+.2f}% | 量能: {target['vol_score']:.1f} | 风险调整: {target['risk_adj']:.1f}")
        result_message += f"动量:{target['momentum']:+.2f}% 量能:{target['vol_score']:.1f} 风险调整:{target['risk_adj']:.1f}\n"

        print(f"  现价: {target['close']:.3f}")
        result_message += f"现价 {target['close']:.3f}\n"

        if state and state.get("holding_code"):
            old = state["holding_code"]
            if old in all_data:
                old_close = all_data[old]["close"].iloc[-1]
                old_pnl = (old_close - state["buy_price"]) / state["buy_price"] * 100
                print(f"  卖出: {state['holding_name']}  收益: {old_pnl:+.2f}%")
                result_message += f"卖出 {state['holding_name']}  收益 {old_pnl:+.2f}%\n"

        new_state = {
            "last_rebalance_date": max_date.strftime("%Y-%m-%d"),
            "holding_code": target["code"],
            "holding_name": target["name"],
            "buy_price": target["close"],
            "score_at_buy": target['total_score'],
            "momentum_at_buy": target['momentum'],
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
            result_message += f"[提示] 当前评分第一: {next_top['name']} ({next_top['total_score']:.1f})\n"
            print(f"  [提示] 当前评分第一: {next_top['name']} ({next_top['total_score']:.1f})")
            print(f"  与持仓不同，下次调仓将切换")
            result_message += f"与持仓不同，下次调仓将切换\n"

    print(f"\n  --- 评分垫底 5 ---")
    for i, r in enumerate(rankings[-5:]):
        print(f"  {len(rankings)-4+i:>2}. {r['name']:<8} {r['code']:<8} {r['total_score']:>6.1f}")

    print(f"\n  {'='*60}")

    send_wechat(
        result_message.replace("\n", "\n\n"),
        os.getenv("WECHAT_SCKEY")
    )

    send_pushplus(result_message, os.getenv("PUSHPLUS"))


if __name__ == "__main__":
    main()
