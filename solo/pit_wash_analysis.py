"""
ETF板块挖坑洗盘(Pit Wash)分析 - 假跌破/诱空洗盘模型
============================================================
模型: Liquidity Sweep & Pit Wash (微观结构)
1. 坑底特征: 价格下穿关键支撑(前低/MA20), 触发止损流动性后3-5日内快速收复, V型拉回
2. 量价动能: 出坑阳线放量(>1.3倍5日均量), MACD零轴下金叉/红柱放大/底背离
3. 突破确认: 上方生命线MA60/MA120, 未破MA60=右侧观察期, 站稳MA60=主升确认期

数据源: Tushare API (复用 etf_mainline_strategy_tushare.py 的配置/缓存/接口/评分)

用法:
    python pit_wash_analysis.py                  # 最新交易日
    python pit_wash_analysis.py --date 20260727  # 指定日期
"""
import os, sys, datetime, argparse, json
import pandas as pd
import numpy as np

# 阻止 tushare 写入主目录 ~/tk.csv(沙箱不允许), 重定向到项目安全目录
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_orig_expanduser = os.path.expanduser
_TK_SAFE_DIR = os.path.join(_BASE_DIR, "cache_tushare")
os.makedirs(_TK_SAFE_DIR, exist_ok=True)

def _safe_expanduser(path):
    if 'tk.csv' in path:
        return os.path.join(_TK_SAFE_DIR, 'tk.csv')
    return _orig_expanduser(path)

os.path.expanduser = _safe_expanduser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 复用 ETF配置(ETF_POOL)/缓存机制/数据接口/个股Alpha评分引擎
import etf_mainline_strategy_tushare as ems

REPORT_DIR = r"D:\mystock\report_daily"
TOP_N_PIT = 2          # 对挖坑形态最明确的几只ETF做个股攻守匹配
ALPHA_ATTACK = 65      # 进攻仓 α 阈值
INSTITUTION_DEF = 75   # 防守仓 机构分I 阈值
TRACK_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracking_state.json")  # 持仓跟踪状态持久化


# ──────────────────────────────────────────
# 技术指标
# ──────────────────────────────────────────
def calc_macd(close, fast=12, slow=26, signal=9):
    """MACD: 返回 (dif, dea, hist) 数组"""
    s = pd.Series(close)
    dif = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif.values, dea.values, hist.values


def detect_bullish_divergence(close, dif, window=60, lo_win=5):
    """
    底背离检测: 价格创新低(坑底) 但 DIF 低点抬高
    在近window日内找两个最低点, 若后低<前低 且 后低DIF>前低DIF → 底背离
    """
    n = len(close)
    start = max(0, n - window)
    lows = []
    for k in range(start + lo_win, n - lo_win):
        seg = close[k - lo_win:k + lo_win + 1]
        if close[k] == seg.min():
            lows.append((k, close[k], dif[k]))
    if len(lows) < 2:
        return False
    # 取两个最低的"局部低点"(按价格)
    lows_sorted = sorted(lows, key=lambda x: x[1])[:2]
    lows_sorted.sort(key=lambda x: x[0])  # 按时间排序
    (k1, p1, d1), (k2, p2, d2) = lows_sorted[0], lows_sorted[1]
    # 后低价格更低 但 DIF 更高 → 底背离
    return (p2 < p1) and (d2 > d1)


# ──────────────────────────────────────────
# Pit Wash 挖坑洗盘检测
# ──────────────────────────────────────────
def detect_pit_wash(df):
    """
    对单只ETF检测最近一次"挖坑洗盘"形态
    返回 dict 或 None (数据不足)
    """
    close = df['close'].values
    vol = df['vol'].values
    n = len(close)
    if n < 70:
        return None

    ma20 = pd.Series(close).rolling(20).mean().values
    ma60 = pd.Series(close).rolling(60).mean().values
    dif, dea, hist = calc_macd(close)
    dates = df['trade_date'].values

    # ── 0. 除权/份额折算跳变检测(单日|涨跌|>20% = 数据跳变, 坑深失真) ──
    daily_ret = pd.Series(close).pct_change().abs()
    jump_days = daily_ret[daily_ret > 0.20].dropna()
    data_jump = len(jump_days) > 0

    # ── 1. 寻找最近一次"坑"(收盘 < MA20 的连续区间) ──
    last_pit_day = None
    for k in range(n - 1, 59, -1):
        if close[k] < ma20[k]:
            last_pit_day = k
            break
    if last_pit_day is None:
        return {'has_pit': False}

    # 坑起点: 向前找到第一个 close >= MA20 的日子
    pit_start = last_pit_day
    while pit_start > 60 and close[pit_start] < ma20[pit_start]:
        pit_start -= 1
    pit_start += 1  # 坑内第一天

    # ── 1.5 前期平台检测: 在坑起点前滑动扫描最优15日窗口 ──
    # 平台特征: MA20最走平 + 价格低波动 + 价格贴MA20 + 平台放量(资金活跃)
    # 用滑动窗口自适应寻找平台段, 避免单日反弹(假收复)切断坑结构
    platform_ok = False
    platform_vol_ratio = 1.0
    plat_low = None
    if pit_start > 60:
        best = None  # (|斜率|, 窗口起点, 窗口终点, 斜率)
        for ws in range(max(60, pit_start - 45), pit_start - 14):
            we = ws + 15
            if we > pit_start:
                break
            slope = (ma20[we - 1] - ma20[ws]) / ma20[ws] * 100
            if best is None or abs(slope) < best[0]:
                best = (abs(slope), ws, we, slope)
        if best is not None:
            _, plat_s, plat_e, ma20_slope = best
            plat_close = close[plat_s:plat_e]
            plat_ma20 = ma20[plat_s:plat_e]
            plat_vol_price = plat_close.std() / plat_close.mean() * 100            # 平台价格波动率%
            plat_dist_ma20 = np.abs(plat_close - plat_ma20).mean() / plat_ma20.mean() * 100  # 贴MA20程度%
            # 平台判定: MA20走平(|斜率|<2.0) + 低波动(<5%) + 贴MA20(<3.5%)
            platform_ok = (abs(ma20_slope) < 2.0 and plat_vol_price < 5.0 and plat_dist_ma20 < 3.5)
            plat_low = float(plat_close.min())  # 平台低点(破位参考)
            # 平台放量: 平台均量 / 平台前20日均量
            if plat_s >= 20:
                plat_vol = vol[plat_s:plat_e].mean()
                prev_vol = vol[plat_s - 20:plat_s].mean()
                platform_vol_ratio = plat_vol / (prev_vol + 1e-6) if prev_vol > 0 else 1.0

    # 坑底: 坑区间内最低点
    seg = close[pit_start:last_pit_day + 1]
    bottom_idx = pit_start + int(np.argmin(seg))
    pit_bottom = close[bottom_idx]
    pit_depth_pct = (pit_bottom - ma20[bottom_idx]) / ma20[bottom_idx] * 100

    # 坑底相对平台低点的破位幅度(仅平台挖坑有意义)
    break_plat_pct = (pit_bottom / plat_low - 1) * 100 if (platform_ok and plat_low and plat_low > 0) else None

    # 收复日: 坑底之后第一个 close >= MA20
    recover_idx = bottom_idx + 1
    while recover_idx < n and close[recover_idx] < ma20[recover_idx]:
        recover_idx += 1
    recovered = recover_idx < n

    pit_width = last_pit_day - pit_start + 1               # 坑内天数
    days_to_recover = (recover_idx - bottom_idx) if recovered else (n - 1 - bottom_idx)

    # 出坑量比: 收复日成交量 / 前5日均量
    recover_vol_ratio = 1.0
    if recovered:
        avg5 = vol[max(0, recover_idx - 5):recover_idx].mean()
        recover_vol_ratio = vol[recover_idx] / (avg5 + 1e-6) if avg5 > 0 else 1.0

    # 当前价格相对 MA20 / MA60 位置
    cur_above_ma20 = bool(close[-1] >= ma20[-1])
    cur_above_ma60 = bool(close[-1] >= ma60[-1])
    dist_ma20_pct = (close[-1] - ma20[-1]) / ma20[-1] * 100
    dist_ma60_pct = (close[-1] - ma60[-1]) / ma60[-1] * 100

    # ── 2. MACD 动能状态 ──
    bull_div = detect_bullish_divergence(close, dif)
    if bull_div:
        macd_desc = "底背离"
    elif dif[-1] > dea[-1] and dif[-1] < 0:
        macd_desc = "零轴下方金叉"
    elif dif[-1] > dea[-1] and hist[-1] > hist[-2]:
        macd_desc = "多头红柱放大"
    elif dif[-1] > dea[-1]:
        macd_desc = "多头排列"
    else:
        macd_desc = "空头/未金叉"

    # ── 3. 阶段定性 ──
    if recovered:
        if cur_above_ma60:
            stage = "主升确认期"   # 站稳MA60
        else:
            stage = "右侧观察期"   # 收复MA20但未破MA60
    else:
        stage = "坑内洗盘中"

    # 出坑质量: 放量收复(>1.3倍) + 快速收复(≤5日)
    vol_confirm = bool(recovered and recover_vol_ratio >= 1.3)
    fast_confirm = bool(recovered and days_to_recover <= 5)

    # ── 平台挖坑完整确认(用户特征: 前期平台放量→下跌破位→收回) ──
    # 需同时满足: 平台走平 + 平台显著放量(≥1.2x) + 实质跌破平台低点(≤-3%) + 坑宽≥3日
    # 排除: 1日小回踩(银行/医疗器械) / 平台缩量(军工/机器人/家电) 的误判
    platform_wash = bool(
        platform_ok
        and platform_vol_ratio >= 1.2
        and break_plat_pct is not None
        and break_plat_pct <= -3.0
        and pit_width >= 3
    )

    return {
        'has_pit': True,
        'stage': stage,
        'data_jump': bool(data_jump),
        'platform_ok': bool(platform_ok),
        'platform_wash': platform_wash,
        'platform_vol_ratio': round(platform_vol_ratio, 2),
        'break_plat_pct': round(break_plat_pct, 2) if break_plat_pct is not None else None,
        'pit_bottom_date': str(dates[bottom_idx])[:10],
        'pit_bottom': round(pit_bottom, 3),
        'pit_depth_pct': round(pit_depth_pct, 2),
        'pit_width': int(pit_width),
        'days_to_recover': int(days_to_recover),
        'recovered': bool(recovered),
        'recover_vol_ratio': round(recover_vol_ratio, 2),
        'vol_confirm': vol_confirm,
        'fast_confirm': fast_confirm,
        'cur_above_ma20': cur_above_ma20,
        'cur_above_ma60': cur_above_ma60,
        'dist_ma20_pct': round(dist_ma20_pct, 2),
        'dist_ma60_pct': round(dist_ma60_pct, 2),
        'macd_desc': macd_desc,
    }


# ──────────────────────────────────────────
# 每日跟踪机制(顶级私募纪律)
# 买点分级: 突破/出坑 > 回踩 > 低吸 (下跌市禁低吸)
# 状态机: 观察 → 试仓 → 持有 →(加仓/止盈/止损)→ 清仓
# ──────────────────────────────────────────
def load_track_state():
    """加载持仓跟踪状态(JSON)"""
    if os.path.exists(TRACK_STATE_FILE):
        try:
            with open(TRACK_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_track_state(state):
    """持久化持仓跟踪状态"""
    try:
        with open(TRACK_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [WARN] 保存跟踪状态失败: {e}")


def classify_buy_point(pit, df, market_state):
    """
    买点类型分类(五类买点):
    - 突破买点: 放量(≥1.3x)突破前高 + 站上MA60, 主升确认
    - 出坑买点: 挖坑后放量收复MA20(平台挖坑优先)
    - 回踩买点: 已收复 + 缩量回踩MA20不破(量比<0.8, 距MA20<2%)
    - 低吸买点: 坑内缩量企稳(量比<0.7, 坑深>-5%)
    - 蓄势买点: 平台横盘末端温和放量(量比1.0-1.5)
    返回 (买点类型, 理由, 今日量比)
    """
    if not pit or not pit.get('has_pit'):
        return '观望', '无挖坑信号', 1.0
    close = df['close'].values
    vol = df['vol'].values
    n = len(close)
    if n < 6:
        return '观望', '数据不足', 1.0
    vol_ratio = vol[-1] / (vol[-6:-1].mean() + 1e-6)
    recent_high = float(close[-min(n, 60):].max())

    # 1. 突破买点: 放量突破前高 + 站上MA60
    if pit['cur_above_ma60'] and close[-1] >= recent_high * 0.995 and vol_ratio >= 1.3:
        return '突破买点', f'放量{vol_ratio:.1f}x突破前高{recent_high:.3f}, 主升确认', vol_ratio
    # 2. 出坑买点: 挖坑放量收复MA20(仅限坑底新鲜≤7自然日, 排除历史旧坑)
    if pit['recovered'] and pit['vol_confirm']:
        try:
            pit_dt = datetime.datetime.strptime(str(pit['pit_bottom_date'])[:10], "%Y-%m-%d")
            last_dt = pd.Timestamp(df['trade_date'].iloc[-1])
            days_since = (last_dt - pit_dt).days
        except Exception:
            days_since = 999
        if days_since <= 7:
            tag = '平台挖坑' if pit['platform_wash'] else '普通挖坑'
            return '出坑买点', f'{tag}放量{pit["recover_vol_ratio"]:.1f}x收复MA20(坑底{days_since}日前)', vol_ratio
        return '趋势延续', f'旧坑已收复{days_since}日, 属趋势票非新信号', vol_ratio
    # 3. 回踩买点: 已收复 + 缩量回踩MA20附近不破
    if (pit['recovered'] and not pit['cur_above_ma60']
            and vol_ratio < 0.8 and abs(pit['dist_ma20_pct']) < 2.0):
        return '回踩买点', f'缩量{vol_ratio:.1f}x回踩MA20({pit["dist_ma20_pct"]:+.1f}%)不破', vol_ratio
    # 4. 低吸买点: 坑内缩量企稳
    if not pit['recovered'] and pit['pit_depth_pct'] > -5.0 and vol_ratio < 0.7:
        return '低吸买点', f'坑内缩量{vol_ratio:.1f}x企稳(坑深{pit["pit_depth_pct"]:.1f}%)', vol_ratio
    # 5. 蓄势买点: 平台横盘末端温和放量
    if (pit.get('platform_ok') and 1.0 <= vol_ratio <= 1.5
            and abs(pit['dist_ma20_pct']) < 3.0):
        return '蓄势买点', f'平台末端温和放量{vol_ratio:.1f}x, 突破前夜', vol_ratio
    return '观望', '信号不足, 等待', vol_ratio


def decide_action(st, pit, buy_type, note, market_state, price):
    """
    持仓状态机(顶级私募纪律):
    持仓中: 硬止损→时间止损→止盈→加仓→持有
    空仓中: 突破/出坑→试仓; 回踩→轻仓; 低吸→试探(下跌市禁)
    返回 (操作, 新状态dict, 理由)
    """
    stage = st.get('stage', '观察')
    position = float(st.get('position', 0.0))
    entry = st.get('entry')
    stop = st.get('stop')
    t1 = st.get('t1')
    t2 = st.get('t2')
    hold_days = int(st.get('hold_days', 0))

    # 市场状态仓位约束(顶级私募风控: 下跌市减半, 趋势市放宽)
    if market_state == 'declining':
        trial_pct, add_pct = 0.05, 0.10
    elif market_state == 'trending':
        trial_pct, add_pct = 0.15, 0.15
    else:
        trial_pct, add_pct = 0.10, 0.15

    # ── 一、持仓中: 先查卖出/止盈 ──
    if position > 0 and entry:
        # 1. 硬止损: 收盘跌破止损线 → 无条件清仓
        if stop and price <= stop:
            st2 = dict(st, stage='清仓', position=0.0, entry=None, stop=None, t1=None, t2=None)
            return '清仓·硬止损', st2, f'收盘{price:.3f}跌破止损{stop:.3f}, 诱空逻辑失效, 无条件离场'
        # 2. 时间止损: 坑内持仓过久未收复
        if hold_days >= 8 and not pit['recovered']:
            st2 = dict(st, stage='清仓', position=0.0, entry=None, stop=None, t1=None, t2=None)
            return '清仓·时间止损', st2, f'持仓{hold_days}日仍处坑内未收复, 洗盘过久, 降险离场'
        # 3. 止盈: 达T2清仓 / 达T1减1/3并上移止损至成本
        if t2 and price >= t2:
            st2 = dict(st, stage='清仓', position=0.0, entry=None, stop=None, t1=None, t2=None)
            return '清仓·止盈达标', st2, f'达到目标2 {t2:.3f}(+{(price/entry-1)*100:.0f}%), 全部兑现'
        if t1 and price >= t1:
            st2 = dict(st, stage='止盈中', position=round(position * 0.66, 3),
                       stop=round(entry, 3), t1=None)
            return '减仓1/3·止盈', st2, f'达到目标1 {t1:.3f}(+{(price/entry-1)*100:.0f}%), 兑现1/3, 止损上移成本{entry:.3f}保本'
        # 4. 试仓→加仓: 站上MA60/突破确认(金字塔加仓)
        if stage == '试仓' and (pit['cur_above_ma60'] or buy_type == '突破买点'):
            new_pos = round(min(position + add_pct, 0.40), 3)
            st2 = dict(st, stage='加仓', position=new_pos)
            return '加仓·突破确认', st2, f'站上MA60/突破确认, 仓位{position*100:.0f}%→{new_pos*100:.0f}%'
        # 5. 默认持有
        return '持有', st, f'持仓{position*100:.0f}%, 止损{stop:.3f}, 目标T1={t1:.3f} T2={t2:.3f}'

    # ── 二、空仓: 依据买点类型定动作 ──
    if buy_type in ('突破买点', '出坑买点'):
        stop = round(min(pit['pit_bottom'], price * 0.92), 3)
        st2 = dict(st, stage='试仓', position=trial_pct, entry=round(price, 3),
                   stop=stop, t1=round(price * 1.15, 3), t2=round(price * 1.25, 3), hold_days=0)
        return '试仓·买入', st2, f'{buy_type}: 建仓{trial_pct*100:.0f}%, 止损{stop}(坑底/成本-8%), 目标T1={st2["t1"]} T2={st2["t2"]}'
    if buy_type == '回踩买点':
        if market_state == 'declining':
            return '观望', st, '下跌市不回踩追单, 等右侧出坑确认'
        stop = round(min(pit['pit_bottom'], price * 0.92), 3)
        st2 = dict(st, stage='试仓', position=round(trial_pct * 0.6, 3), entry=round(price, 3),
                   stop=stop, t1=round(price * 1.12, 3), t2=round(price * 1.22, 3), hold_days=0)
        return '试仓·轻仓', st2, f'{buy_type}: 轻仓{st2["position"]*100:.0f}%, 止损{stop}'
    if buy_type == '低吸买点':
        if market_state == 'declining':
            return '观望', st, '下跌市不做左侧低吸, 等出坑右侧信号'
        stop = round(min(pit['pit_bottom'], price * 0.92), 3)
        st2 = dict(st, stage='试仓', position=round(trial_pct * 0.4, 3), entry=round(price, 3),
                   stop=stop, t1=round(price * 1.10, 3), t2=round(price * 1.20, 3), hold_days=0)
        return '试仓·试探', st2, f'{buy_type}: 小仓试探{st2["position"]*100:.0f}%, 止损{stop}'
    return '观望', st, note


def _sig_key(pit):
    """买点强度排序: 平台挖坑 > 放量确认 > 坑窄 > 坑浅(新鲜)"""
    return (pit.get('platform_wash', False), pit.get('vol_confirm', False),
            -pit.get('pit_width', 0), -pit.get('pit_depth_pct', 0))


def gen_tracking_section(pit_results, market_state, trade_date):
    """
    生成每日跟踪决策表 + 更新持仓状态机(持久化到 tracking_state.json)
    顶级私募纪律: 下跌市集中度控制(买入信号最多2只), 弱市重质不重量
    """
    state = load_track_state()
    last_date = state.get('_meta', {}).get('last_date')
    is_new_day = (last_date != trade_date)
    # 当日再次运行: 重置状态, 基于当天最新数据重新判断(不延续上次持仓判断)
    if not is_new_day:
        state = {}
    L = []
    L.append(f"## 四、每日交易决策跟踪(顶级私募纪律)")
    L.append(f"")
    L.append(f"**市场状态**: {market_state} | 买点分级: **突破/出坑 > 回踩 > 低吸** (下跌市禁低吸/回踩追单)")
    L.append(f"")
    # 第一遍: 收集所有候选信号
    pending = []
    for p in pit_results:
        if not p['pit'] or not p['pit']['has_pit'] or p['pit']['data_jump']:
            continue
        name, code, pit, df = p['name'], p['code'], p['pit'], p['df']
        try:
            price = float(df['close'].values[-1])
        except Exception:
            continue
        buy_type, note, vr = classify_buy_point(pit, df, market_state)
        st = state.get(name, {'stage': '观察', 'position': 0.0})
        if is_new_day and st.get('entry'):
            st['hold_days'] = int(st.get('hold_days', 0)) + 1
        pending.append((name, code, pit, df, price, buy_type, note, st))
    # 下跌市集中度: 仅保留最强2只买入信号, 其余入观察池
    buy_cap = 2 if market_state == 'declining' else 5
    strong_buys = sorted([x for x in pending if x[5] in ('突破买点', '出坑买点')],
                         key=lambda x: _sig_key(x[2]), reverse=True)
    buy_names = {x[0] for x in strong_buys[:buy_cap]}
    # 第二遍: 生成决策并更新状态
    rows, action_summary, holdings = [], [], []
    for name, code, pit, df, price, buy_type, note, st in pending:
        if (market_state == 'declining' and buy_type in ('突破买点', '出坑买点')
                and name not in buy_names):
            action, st_new, reason = '观察候选', st, \
                '下跌市集中度控制: 今日仅保留最强2只买入信号, 本标的入观察池待明日'
        else:
            action, st_new, reason = decide_action(st, pit, buy_type, note, market_state, price)
        state[name] = st_new
        pos = float(st_new.get('position', 0.0))
        stop = st_new.get('stop')
        t1 = st_new.get('t1')
        t2 = st_new.get('t2')
        tp = f"{t1:.2f}/{t2:.2f}" if t1 else "-"
        rows.append((name, pit['stage'], buy_type, action, pos, stop, tp, reason))
        if pos > 0:
            holdings.append((name, code, pos, stop, t1, t2))
        if action not in ('观望', '持有', '观察候选'):
            action_summary.append((name, code, action, reason))
    # 更新状态日期并持久化
    state['_meta'] = {'last_date': trade_date}
    save_track_state(state)

    L.append(f"| 板块ETF | 挖坑阶段 | 买点类型 | 今日操作 | 仓位 | 止损线 | 止盈T1/T2 | 理由 |")
    L.append(f"| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- |")
    for name, stage, bt, act, pos, stop, tp, reason in rows:
        pos_s = f"{pos*100:.0f}%" if pos > 0 else "-"
        stop_s = f"{stop:.3f}" if stop else "-"
        L.append(f"| {name} | {stage} | {bt} | **{act}** | {pos_s} | {stop_s} | {tp} | {reason} |")
    L.append(f"")
    if action_summary:
        L.append(f"**今日操作指令(按优先级)**:")
        L.append(f"")
        for name, code, act, reason in action_summary:
            L.append(f"- **{act}** {name}({code}): {reason}")
        L.append(f"")
    L.append(f"**风控纪律**: 单板块上限30% | 硬止损无条件执行 | 盈利≥10%后止损上移至成本保本 | 趋势市总仓≤70%, 震荡≤50%, 下跌≤30% | 持仓滚动复核: 每交易日收盘后更新本表")
    L.append(f"")
    return "\n".join(L), action_summary, holdings


# ──────────────────────────────────────────
# 微信推送(盘后推送精简报告到手机)
# 优先 PushPlus(markdown) / 备选 Server酱; token 从环境变量读取
# ──────────────────────────────────────────
def build_wechat_message(action_summary, holdings, state_desc, trade_date):
    """构建微信推送用的精简报告: 市场状态 + 今日操作指令 + 持仓跟踪"""
    L = []
    L.append(f"# 挖坑洗盘跟踪 {trade_date}")
    L.append(f"")
    L.append(f"**市场**: {state_desc}")
    L.append(f"")
    if action_summary:
        L.append(f"## 今日操作指令")
        L.append(f"")
        for name, code, act, reason in action_summary:
            L.append(f"- **{act}** {name}({code}): {reason}")
        L.append(f"")
    if holdings:
        L.append(f"## 持仓跟踪")
        L.append(f"")
        for name, code, pos, stop, t1, t2 in holdings:
            tp = f"{t1:.2f}/{t2:.2f}" if t1 else "-"
            L.append(f"- {name}({code}): 持仓{pos*100:.0f}% 止损{stop if stop else '-'} 目标{tp}")
        L.append(f"")
    if not action_summary and not holdings:
        L.append(f"## 今日操作")
        L.append(f"")
        L.append(f"- 无操作指令, 观望为主")
    return "\n".join(L)


def send_wechat_report(msg, trade_date):
    """推送微信: 优先PushPlus(markdown), 备选Server酱; 未配置token则跳过"""
    import requests
    token = os.getenv("PUSHPLUS")
    if token:
        url = "https://www.pushplus.plus/send"
        payload = {"token": token, "title": f"挖坑洗盘跟踪 {trade_date}",
                   "content": msg, "template": "markdown"}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            if result.get("code") == 200:
                print("  ✅ 微信推送成功(PushPlus)")
            else:
                print(f"  ⚠️ PushPlus推送失败: {result.get('msg', '未知错误')}")
        except Exception as e:
            print(f"  ⚠️ PushPlus异常: {e}")
        return
    key = os.getenv("WECHAT_SCKEY") or os.getenv("WECHAT_KEY")
    if key:
        url = f"https://sctapi.ftqq.com/{key}.send"
        try:
            requests.post(url, data={"title": f"挖坑洗盘跟踪 {trade_date}", "desp": msg}, timeout=15)
            print("  ✅ 微信推送成功(Server酱)")
        except Exception as e:
            print(f"  ⚠️ Server酱异常: {e}")
        return
    print("  ⚠️ 未配置 PUSHPLUS/WECHAT_SCKEY 环境变量, 跳过微信推送")


# ──────────────────────────────────────────
# 个股攻守匹配
# ──────────────────────────────────────────
def match_stocks(etf_name, ts_code, etf_df, trade_date, today):
    """
    对单只ETF的成份股做攻守匹配(复用 stock_alpha_ranking 引擎)
    - 进攻仓: 角色 Leader/Core 且 α ≥ 65
    - 防守仓: 机构分 I ≥ 75
    返回 (attack_list, defense_list, err)
    """
    constituents = ems.get_etf_constituents(ts_code, trade_date)
    if not constituents:
        return [], [], f"无成份股数据"
    # 纯度过滤(仅保留白名单内/有权重的成份股)
    filtered, removed, ratio = ems.filter_by_purity(constituents, etf_name)
    if not filtered:
        return [], [], f"纯度过滤后无成份股"

    try:
        console_text, csv_path, df_ranked = ems.stock_alpha_ranking(
            filtered, etf_name, today, ems.pro, etf_df, trade_date)
    except Exception as e:
        return [], [], f"评分失败: {e}"

    attack, defense = [], []
    for _, r in df_ranked.iterrows():
        alpha = float(r.get('alpha_score', 0))     # Alpha综合分(0-100)
        inst = float(r.get('capital', 0))          # 机构/资金行为分(0-100)
        role = {'CORE_ALPHA': 'Leader', 'STRONG': 'Core'}.get(r.get('signal', ''), '跟随')
        name = r.get('name', '')
        code = r.get('code', '')
        alpha5 = r.get('alpha5', 0)
        a20 = r.get('alpha20', 0)
        if role in ('Leader', 'Core') and alpha >= ALPHA_ATTACK:
            attack.append({'code': code, 'name': name, 'role': role,
                           'alpha': round(alpha, 1), 'inst': round(inst, 1),
                           'alpha5': alpha5, 'alpha20': a20})
        if inst >= INSTITUTION_DEF:
            defense.append({'code': code, 'name': name, 'role': role,
                            'alpha': round(alpha, 1), 'inst': round(inst, 1),
                            'alpha5': alpha5, 'alpha20': a20})

    # 排序: 进攻按α降序, 防守按I降序
    attack.sort(key=lambda x: x['alpha'], reverse=True)
    defense.sort(key=lambda x: x['inst'], reverse=True)
    return attack, defense, None


# ──────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────
def gen_report(pit_results, attack_map, defense_map, market_desc, trade_date):
    """生成三部分结构分析报告(Markdown)"""
    L = []
    L.append(f"# 板块挖坑洗盘(Pit Wash)量化分析报告")
    L.append(f"")
    L.append(f"**分析日期**: {trade_date}  |  **市场状态**: {market_desc}")
    L.append(f"")
    L.append(f"> 模型: 假跌破/诱空洗盘(Liquidity Sweep & Pit Wash)")
    L.append(f"> 坑底=下穿MA20触发止损流动性后3-5日快速收复, 出坑需放量+MACD动能配合")
    L.append(f"")
    L.append(f"---")
    L.append(f"")

    # ── 一、板块/ETF 微观结构诊断 ──
    L.append(f"## 一、板块/ETF 微观结构诊断")
    L.append(f"")
    pit_ets = [p for p in pit_results if p['pit'] and p['pit']['has_pit'] and not p['pit']['data_jump']]
    pit_ets.sort(key=lambda x: (x['pit']['platform_wash'],
                                x['pit']['stage'] == '主升确认期', x['pit']['stage'] == '右侧观察期',
                                abs(x['pit']['pit_depth_pct'])), reverse=True)

    if not pit_ets:
        L.append(f"**结论**: 当前ETF池均未检出挖坑洗盘形态(价格未下穿MA20或已深度破位)。")
    else:
        L.append(f"**检出 {len(pit_ets)} 只存在挖坑形态的ETF**, 按阶段排序:")
        L.append(f"")
        L.append(f"| 板块ETF | 阶段 | 平台 | 坑底日期 | 坑底价 | 坑深% | 破平台% | 坑宽(日) | 收复(日) | 出坑量比 | 量能确认 | MACD | 距MA20% | 距MA60% | 备注 |")
        L.append(f"| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :--- |")
        for p in pit_ets:
            pit = p['pit']
            name = p['name']
            vol_c = "✓" if pit['vol_confirm'] else "✗"
            plat = f"✓放量{ pit['platform_vol_ratio']:.1f}x" if pit['platform_wash'] else "✗"
            bplat = f"{pit['break_plat_pct']:.1f}%" if pit.get('break_plat_pct') is not None else "-"
            note = "⚠数据跳变(疑似除权/折算)" if pit['data_jump'] else ""
            L.append(f"| {name} | {pit['stage']} | {plat} | {pit['pit_bottom_date']} | {pit['pit_bottom']} "
                     f"| {pit['pit_depth_pct']:.1f}% | {bplat} | {pit['pit_width']} | {pit['days_to_recover']} "
                     f"| {pit['recover_vol_ratio']:.2f} | {vol_c} | {pit['macd_desc']} "
                     f"| {pit['dist_ma20_pct']:+.1f}% | {pit['dist_ma60_pct']:+.1f}% | {note} |")
        L.append(f"")
        # 关键结论
        confirm = [p for p in pit_ets if p['pit']['recovered'] and p['pit']['vol_confirm']]
        if confirm:
            names = "、".join(p['name'] for p in confirm[:5])
            L.append(f"**挖坑形态判定**: {names} 已完成放量收复(出坑量比≥1.3), 诱空洗盘成立。")
            L.append(f"")
        plat_ok = [p for p in pit_ets if p['pit']['platform_wash']]
        if plat_ok:
            names = "、".join(p['name'] for p in plat_ok[:5])
            L.append(f"**平台挖坑确认**: {names} 满足[前期平台+平台放量≥1.2x+实质破位≤-3%+坑宽≥3日]特征, "
                     f"属于典型诱空洗盘结构, 优先跟踪。")
            L.append(f"")
        L.append(f"**阻力与突破节点**: 重点关注上方生命线 MA60/MA120 压制; 下方强支撑 MA20/坑底颈线。"
                 f"未站稳MA60的板块定性为右侧观察期, 站稳MA60后进入主升确认期。")
    L.append(f"")
    L.append(f"---")
    L.append(f"")

    # ── 二、量化因子与个股攻守匹配表 ──
    L.append(f"## 二、量化因子与个股攻守匹配表")
    L.append(f"")
    if not attack_map and not defense_map:
        L.append(f"**无达标标的**。")
    else:
        for etf_name in dict.fromkeys(list(attack_map.keys()) + list(defense_map.keys())):
            att = attack_map.get(etf_name, [])
            dfs = defense_map.get(etf_name, [])
            if not att and not dfs:
                continue
            L.append(f"### {etf_name}")
            L.append(f"")
            if att:
                L.append(f"**进攻仓位(高Alpha领涨, α≥{ALPHA_ATTACK})**:")
                L.append(f"")
                L.append(f"| 股票代码/名称 | 角色定位 | Alpha(α) | 机构分(I) | 组合角色 | 选股逻辑简述 |")
                L.append(f"| :--- | :--- | :---: | :---: | :---: | :--- |")
                for s in att[:8]:
                    code = str(s['code']).replace('.SZ', '').replace('.SH', '')
                    note = f"超额{ s['alpha5']:+.1f}%(5日)/{s['alpha20']:+.1f}%(20日)"
                    L.append(f"| {code}/{s['name']} | {s['role']} | {s['alpha']:.1f} | {s['inst']:.1f} | 进攻 | {note} |")
                L.append(f"")
            if dfs:
                L.append(f"**防守仓位(高机构控盘护航, I≥{INSTITUTION_DEF})**:")
                L.append(f"")
                L.append(f"| 股票代码/名称 | 角色定位 | Alpha(α) | 机构分(I) | 组合角色 | 选股逻辑简述 |")
                L.append(f"| :--- | :--- | :---: | :---: | :---: | :--- |")
                for s in dfs[:8]:
                    code = str(s['code']).replace('.SZ', '').replace('.SH', '')
                    note = f"资金行为分{ s['inst']:.1f}(大单主导/持续流入)"
                    L.append(f"| {code}/{s['name']} | {s['role']} | {s['alpha']:.1f} | {s['inst']:.1f} | 防守 | {note} |")
                L.append(f"")
    L.append(f"---")
    L.append(f"")

    # ── 三、实盘量化策略执行逻辑 ──
    L.append(f"## 三、实盘量化策略执行逻辑")
    L.append(f"")
    L.append(f"### 1. 建仓信号 (Entry Signal)")
    L.append(f"")
    L.append(f"**试探买点(出坑右侧)**: ETF收复MA20 + 出坑阳线放量(≥1.3倍5日均量)时:")
    L.append(f"- ETF底仓 40%: 买入该板块ETF, 保护线设坑底低点。")
    L.append(f"- 高Alpha进攻仓 40%: 优先 Leader/Core + α≥65 标的, 捕捉出坑后最高超额收益。")
    L.append(f"- 高I防守仓 20%: 机构分 I≥75 标的, 防范MA60附近二次洗盘/假突破。")
    L.append(f"")
    L.append(f"**确认买点(右侧突破)**: ETF放量突破并站稳MA60后:")
    L.append(f"- 加仓 20-30%: 主升确认期, 攻击仓位向高α标的集中。")
    L.append(f"- 调仓规则: 将未站上MA60板块中仓位切换至已确认板块。")
    L.append(f"")
    L.append(f"### 2. 风控与止损 (Risk Control)")
    L.append(f"")
    # 硬止损线: 各板块坑底低点
    stop_str = "、".join(f"{p['name']}:{p['pit']['pit_bottom']}" for p in pit_ets[:3]) if pit_ets else '各板块坑底'
    L.append(f"**硬止损线(无条件)**: 收盘价跌破坑底低点({stop_str}) → 无条件清仓该板块, 诱空逻辑失效。")
    L.append(f"- **逻辑止损**: 出坑3日内未站回MA20(收复失败), 视为真跌破而非诱空。")
    L.append(f"- **时间止损**: 坑内超过8个交易日未收复, 洗盘过久, 降低持仓。")
    L.append(f"- **仓位上限**: 单板块ETF+个股合计 ≤ 总仓位30%, 攻守比例 6:4 动态调整。")
    L.append(f"")
    L.append(f"> 免责声明: 本报告基于量化模型, 仅供参考, 不构成投资建议。")
    L.append(f"")
    return "\n".join(L)


# ──────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ETF挖坑洗盘(Pit Wash)分析")
    parser.add_argument("--date", type=str, default=None, help="指定交易日(YYYYMMDD), 默认最新交易日")
    args = parser.parse_args()

    trade_date = ems.get_last_trade_date(args.date)
    today = datetime.datetime.strptime(trade_date, "%Y%m%d")
    print("=" * 66)
    print(f"  板块挖坑洗盘(Pit Wash)分析 | 日期: {trade_date}")
    print("=" * 66)

    # 市场状态
    benchmark_df = None
    bm_cache = os.path.join(ems.ETF_FUND_CACHE_DIR, f"idx_000300_{trade_date}.csv")
    benchmark_df = ems._read_cache(bm_cache)
    if benchmark_df is None:
        try:
            benchmark_df = ems.pro.index_daily(
                ts_code="000300.SH",
                start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                end_date=trade_date)
            ems._save_cache(benchmark_df, bm_cache)
        except Exception:
            benchmark_df = None
    if benchmark_df is not None and len(benchmark_df) > 0:
        benchmark_df["trade_date"] = pd.to_datetime(benchmark_df["trade_date"], format="%Y%m%d")
        benchmark_df = benchmark_df.sort_values("trade_date").reset_index(drop=True)
    market_state, state_desc = ems.classify_market_state(benchmark_df, ems.MOM_PERIOD)

    # 1. 加载全部ETF日线(复用缓存)
    codes_ts = {}
    for name, code in ems.ETF_POOL.items():
        codes_ts[code] = f"{code}.SH" if code.startswith(("5", "6")) else f"{code}.SZ"

    all_data = {}
    for name, code in ems.ETF_POOL.items():
        ts_code = codes_ts[code]
        cache_file = ems._cache_key_fund(ts_code, trade_date)
        df = ems._read_cache(cache_file)
        if df is not None and 'vol' not in df.columns:
            df = None
        if df is None:
            try:
                df = ems.pro.fund_daily(ts_code=ts_code,
                                        start_date=(today - datetime.timedelta(days=150)).strftime("%Y%m%d"),
                                        end_date=trade_date,
                                        fields="ts_code,trade_date,open,close,high,low,vol,amount")
                ems._save_cache(df, cache_file)
                import time
                time.sleep(0.25)
            except Exception as e:
                print(f"  [WARN] {name}({ts_code}): {e}")
                continue
        if df is not None and len(df) > 0:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            df = df[df["trade_date"] <= today].reset_index(drop=True)
            all_data[code] = df

    print(f"  数据加载完成: {len(all_data)} 只ETF")
    print(f"  市场状态: {state_desc}")

    # 2. Pit Wash 检测(全部ETF)
    pit_results = []
    for code, df in all_data.items():
        name = {v: k for k, v in ems.ETF_POOL.items()}.get(code, code)
        pit = detect_pit_wash(df)
        pit_results.append({'name': name, 'code': code, 'df': df, 'pit': pit})

    # 3. 选出"值得个股分析"的ETF: 已收复 + 挖坑形态明确(坑深+放量)
    candidates = [p for p in pit_results if p['pit'] and p['pit']['has_pit'] and p['pit']['recovered'] and not p['pit']['data_jump']]
    candidates.sort(key=lambda x: (x['pit']['platform_wash'], x['pit']['vol_confirm'],
                                   x['pit']['fast_confirm'],
                                   abs(x['pit']['pit_depth_pct'])), reverse=True)
    top_pits = candidates[:TOP_N_PIT]

    # 4. 个股攻守匹配(仅对 top N)
    attack_map, defense_map = {}, {}
    for p in top_pits:
        name, code = p['name'], p['code']
        ts_code = codes_ts[code]
        print(f"  → 个股攻守匹配: {name}({ts_code})...")
        attack, defense, err = match_stocks(name, ts_code, p['df'], trade_date, today)
        if err:
            print(f"    [WARN] {err}")
            continue
        if attack or defense:
            if attack:
                attack_map[name] = attack
            if defense:
                defense_map[name] = defense
            print(f"    进攻 {len(attack)} 只 | 防守 {len(defense)} 只")

    # 5. 生成报告 + 每日跟踪决策
    report = gen_report(pit_results, attack_map, defense_map, state_desc, trade_date)
    tracking_md, action_summary, holdings = gen_tracking_section(pit_results, market_state, trade_date)
    report = report.rstrip() + "\n\n" + tracking_md + "\n"
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"pit_wash_report_{trade_date}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  报告已生成: {report_path}")
    print("\n" + report[:3000] + ("\n  ...(截断)" if len(report) > 3000 else ""))

    # 6. 微信推送(盘后推送: 市场状态 + 今日操作指令 + 持仓跟踪)
    try:
        wechat_msg = build_wechat_message(action_summary, holdings, state_desc, trade_date)
        print("\n  📤 微信推送...")
        send_wechat_report(wechat_msg, trade_date)
    except Exception as e:
        print(f"  [WARN] 微信推送异常: {e}")


if __name__ == "__main__":
    main()
