import argparse
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
BASIC_PATH = os.path.join(CACHE_DIR, "stock_basic.csv")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")
IGE_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ige", "output")
MIN_CIRC_MV = 500_000.0  # 流通市值下限：50 亿元（Tushare 单位：万元）
MIN_BARS = 250  # 最少 K 线：上市满一年才参与（次新股分位样本太少会失真）
DATA_START = "20230103"  # 天量分位历史起点：以 20230103 为起点，此后上市以上市日为起点
MAIN_EVENT_PCT = 99.0  # 主事件门槛：量能+成交额历史分位双≥P99 才算天量主事件（20261001 由「换手率」改为「成交金额」；原 OR≥P98 命中面过大，V4.3 收紧为 AND≥P99）
MAX_EVENT_AGE = 60  # 近 60 天内出现过天量事件即入候选池
WATCH_MIN_T120 = 60  # V4.3：WATCH 状态 T120 下限，低于此分值的低分兜底票不输出
ANCHORS = {"中际旭创": ("300308.SZ", "20250508"), "华正新材": ("603186.SH", "20250812")}
STATES = ["DOWNTREND", "BASE", "IMPULSE", "EXTREME_CHURN", "ABSORPTION", "DRYUP", "RE_EXPANSION", "BREAKOUT_CONFIRM", "SECOND_WAVE", "T0_CONFIRM", "BREAKOUT_RETEST", "MIDLINE_HOLD", "RIGHT_BOTTOM", "DISTRIBUTION", "FAILED"]
# V5.1：当日买点五态（同步下游 stock_pick_db / tushare 均只取这几态，与报告「今日可操作榜」口径一致）
# 20260914 新增 BREAKOUT_RETEST：放量突破后缩量回踩买点（移远通信：8/14 天量T0 → 8/31 放量突破 → 9/9 起缩量回踩）
# 20260930 新增 MIDLINE_HOLD「W7-不破中位」：放量长阳（须创60日新高）后缩量回踩、收盘不破长阳半分位（中红医疗 9/23~9/24）
# 20261002 新增 RIGHT_BOTTOM「W7-右底低吸」：前波大涨后回落形成双底，右底缩量、不破左底（百普赛斯 301080.SZ 9/11）
ACTION_BUY_STATES = ("SECOND_WAVE", "BREAKOUT_CONFIRM", "RE_EXPANSION", "T0_CONFIRM", "BREAKOUT_RETEST", "MIDLINE_HOLD", "RIGHT_BOTTOM")
# 20261001 热点主题扩池（与 HVT-BULL 同口径）：V2.4 热点主题全部成员并入准入池，
# 与 sli_v2 龙头取并集，解决"非龙头但属强势主题"漏选（诺唯赞 688105.SH / 创新药）。
THEME_EXPAND_ENABLED = True
THEME_EXPAND_WINDOW = "month"
THEME_EXPAND_HEAT_MIN = 80.0
W7_THEME_WATCH_CAP = 20  # 报告「热点主题新增跟踪」节展示上限（20261001）
# 20260909 用户口径「T0 确认买点」（我爱我家 8/19→8/27）：天量 T0 之后出现首根收盘回到
# T0 收盘价之上的“重夺日”（8/25），随后连续 ≥2 根收盘站稳 T0 收盘价（8/26/8/27），
# 确认日当日即视为有效买点（旁路 <5 根K线等待期与 major_risk 的早期误判）。仅限事件后
# 早期窗口，且真实放量突破（BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION）仍优先。
T0_CONFIRM_MAX_BARS = 10
# 20260914 用户口径「天量T0 → 放量突破 → 缩量回踩买点」门槛（移远通信 603236.SH：
# 8/14 天量T0(P99, 3.83×量) → 8/31 放量涨停突破日 → 9/3 起缩量、9/9 回踩至突破日收盘附近不破 = 买点）
BREAKOUT_DAY_PCT = 5.0  # 放量突破日：强势阳线涨幅下限（涨停/大涨）
BREAKOUT_DAY_VOLR = 1.3  # 放量突破日：量能 ≥1.3×前20日均量
RETEST_VOL_RATIO = 0.8  # 缩量回踩：近3日均量 ≤ 突破日量 ×0.8
RETEST_DRYUP = 0.95  # 真缩量：近3日均量 ≤ 突破前20日均量 ×0.95（突破日常为3~5倍量，仅对比突破日会虚松）
RETEST_TOL = 1.005  # 回踩到位：回踩期收盘曾回到 突破日收盘 ×1.005 以内
RETEST_MA20_K = 0.97  # 回踩不破：现价 ≥ MA20 ×0.97
RETEST_MAX_AGE = 15  # 回踩结构新鲜度：放量突破日距当日 ≤15 根（更早的突破已非“回踩位附近”）
RETEST_MAX_EXT = 1.10  # 不追高：现价 ≤ 突破日收盘 ×1.10（回踩位附近低吸；已拉开则转由突破/二波分支处理）
RETEST_PULLBACK = 0.95  # 须真实回踩：突破日前最低 ≤ T0 收盘 ×0.95（排除 T0 后单边直上的加速股）
# 20260930 新增分支「W7-不破中位」（用户口径，对标中红医疗 300981.SZ：
# 9/18 放量长阳 +14.01%（量 26.8 万手=前20日均量 3.8 倍）→ 9/21~9/22 高位缩量横盘不回落 →
# 9/23~9/24 缩量回踩，收盘始终未跌破长阳半分位 17.17 → 9/30 再放量 +13.41% 创月内新高）。
# 与 RETEST 分支的区别：RETEST 必须先有天量 T0 事件、且突破前须深踩至 T0 收盘×0.95；
# 本类结构无天量、无深踩，仅靠「放量长阳半分位」承接，原状态机整段漏判（300981 9/23~9/24 落在 ABSORPTION 等待态）。
# 口径：中位＝长阳日 (最高+最低)/2 的半分位（纯长阳自身价格，不含跳空歧义）；
# 「不破」用**收盘**口径，盘中插针容忍 MIDLINE_LOW_TOL（V5.3 教训：2~3% 插针曾洗掉 10 条事后收正的记录）。
MIDLINE_DAY_PCT = 7.0     # 放量长阳日：涨幅下限（需显著强于 breakout_day 的 5%）
MIDLINE_DAY_VOLR = 1.3    # 放量长阳日：量 ≥1.3×前20日均量（与 breakout_day 同源）
MIDLINE_CLOSE_POS = 67.0  # 放量长阳日：收盘位于当日振幅上 1/3（强势收盘，排除长上影）
MIDLINE_MAX_AGE = 10      # 新鲜度：长阳日距当日 ≤10 根
MIDLINE_VOL_RATIO = 0.60  # 缩量：当日量 ≤ 长阳日量 ×0.6
MIDLINE_MAX_EXT = 1.10    # 未拉开：现价 ≤ 长阳收盘 ×1.10（仍是低吸位；已拉开转突破/二波分支）
MIDLINE_LOW_TOL = 0.98    # 插针容忍：回踩期最低 ≥ 中位 ×0.98
MIDLINE_NH60 = 60         # 平台突破属性：长阳日收盘须 ≥ 前 60 根最高价（不含当日）
# 20260930 阈值标定（`_w7_midline.py`，2025-01-02~2026-09-30，T+1/3/5/10 收盘口径；全池基线 T+3 +0.14%/T+5 +0.26%/T+10 +0.55%）：
#   ① 裸条件（pct7/volratio0.70，stride2）：n=4036，T+3 +0.23%(超额 +0.08)、T+5 +0.32%(+0.05)、胜率 46.9% → 几乎无超额，不可用；
#   ② 加 MIDLINE_VOL_RATIO=0.60（stride2）：n=2509，T+3 +0.47%(+0.32)、T+5 +0.63%(+0.36) → 有改善但偏弱；
#   ③ 加 MIDLINE_NH60=60（关键判别，stride4）：n=1388，T+3 +0.74%(+0.60)、T+5 +1.00%(+0.73)，胜率 49.2% → 显著；
#   ④ ②+③并用（本版定稿，全池 stride=1 复算）：n=4468，T+3 +0.52%(+0.38)、T+5 +0.86%(+0.60)、T+10 +1.07%(+0.52)，T+3 胜率 47.2%。
#   样本外（2024-01-02~2024-12-31，stride3，n=596）：T+3 +0.46%(+0.46)、T+5 +0.57%(+0.54)、T+10 +1.33%(+1.21) → 跨年一致，非过拟合。
#   其它维度实测无效：MA20 走平/向上 1388→1383（几乎零增量）、pct=9 反而转负(T+5 超额 -0.03)、max_ext 收至 1.03 零增量。
#   结论：本分支的真实 edge 来自「长阳须是 60 日新高（平台突破属性）」+「回踩须真缩量」，单看「不破半分位」本身无 alpha；
#         且 T+1 超额为负（2024 年为 -0.20%），是典型「低吸后需 3~5 日发酵」的短中线信号，不是次日冲高型。
#   注：胜率约 47~50%、中位数略负而均值明显为正 → 右偏分布（少数标的走成二波贡献主要收益），与 SECOND_WAVE 家族同源。
# 20261002 新增分支「W7-右底低吸」（用户口径，对标百普赛斯 301080.SZ：
# 8/20 主高 102.79（前波 8/03 低 44.35 起 +131.8%）→ 8/31 左底 72.79 → 9/08 中间高点/颈线 92.62
# → 9/11 右底 76.01（缩量 0.58×20日均量、不破左底、收盘 78.70 ≤ MA10 且 ≥ MA60）。
# 与既有分支的区别：完全不以「天量 T0 事件」为锚（HVT 锚点每日被新天量刷新、且要求天量后缩量锁筹，
# 该类结构恒判派发；W7 的 T0 锚同样落在 8/20 后一路 FAILED），只看价格自身的「主高→左底→颈线→右底」几何。
# 结构定义（全部只用 ≤ 当日的数据）：
#   H0 = 近 RIGHT_BOTTOM_WINDOW 根的区间最高（且距当日 ≥3 根）；L1 = H0 之后的最低价；
#   M  = L1 之后的最高价（双底颈线）；T = 当日。
RIGHT_BOTTOM_WINDOW = 45      # 找 H0 的回看窗口（根）
RIGHT_BOTTOM_LOOKBACK = 60    # 计算前波涨幅的回看窗口（根）
RIGHT_BOTTOM_PRIOR_RISE = 0.25  # 前波涨幅下限（H0 前 LOOKBACK 内最低 → H0；与长期口径「≥25% 才算趋势确认」一致）
RIGHT_BOTTOM_PULLBACK = 0.10  # H0 → L1 回落下限（左底须是真实回调）
RIGHT_BOTTOM_REBOUND = 0.08   # L1 → M 反弹下限（颈线成立）
RIGHT_BOTTOM_TOL_LO = 0.02    # 不破左底：low[T] ≥ L1×(1-2%)
RIGHT_BOTTOM_TOL_HI = 0.08    # 右底不高出左底太多：low[T] ≤ L1×(1+8%)（超出即为「回踩不到位」，非同一底）
RIGHT_BOTTOM_RETRACE = 0.10   # 自 M 的回撤下限
RIGHT_BOTTOM_NEAR = 3         # 当日须为近 N 根最低（正在探右底）
RIGHT_BOTTOM_VOL_DRYUP = 0.75  # 右底缩量：vol[T] ≤ 0.75×前20日均量
RIGHT_BOTTOM_MA60_FLAT = -0.005  # 中期趋势：MA60 近 5 根不弱于 -0.5%
# 20261002 阈值标定（全市场总市值 ≥80 亿 2078 只，2026-09 全月）：命中 40 只 / 66 次，
# 9/11 全市场 7 只、9/30 当日 3 只 → 属可人工复核规模；301080 全历史仅 7/29、9/11 两次（均事后成立）。
# V5.2 质量过滤（20260917 加入三条；20260930 复检后只保留①，②③废止）。
# 复检脚本 `_w7_gate3.py`，口径＝「当日引擎产出的今日可操作榜」（非跟踪库，避免回填污染），
# 样本＝新引擎 79 条（0914~0917 过滤前 + 0918~0930 过滤后），超额＝前复权收盘 − 全市场等权：
#   ① state∈{BREAKOUT_CONFIRM, SECOND_WAVE, RE_EXPANSION} → 排除（保留）。
#      只排状态后 T+10 超额 -1.85%→+0.51%、胜率 32%→41%；短周期不劣（T+3 +0.29%→+0.55%）。
#   ② type=CORE（涨幅<80%）→ 废止。原依据「跟踪库 360 条称排除后 T+3 +2.23%→+2.78%」，
#      但该样本含回填污染（0909/0910/0911 库内记录与当日实时存档交集为 0）；改用当日产出榜复检：
#      CORE 17 条 T+3 +1.54%/胜75%，优于 MID -0.28%、EXT +0.14%；只排 CORE 使 T+3 +0.29%→-0.04%。
#   ③ 选股日量比 >0.66 → 废止。原依据同一样本称 1.0~1.43 档最差，复检方向相反——
#      BREAKOUT_RETEST 内 28 vs 28 对称对照：volr≤0.66（原保留）T+3 -0.82%/胜42%，
#      volr>0.66（原排除）T+3 +2.35%/胜57%。三条全开后 T+3 +0.29%→-1.58%，主因即②③。
#   ④ 信号日收盘 > 天量标志日开盘价（20260930 用户口径，作用于当日买点）：价格须站回天量事件
#      起点上方。配套 `extreme_event` 新增「天量标志日须为阳线」，剔除巨量大阴线型 T0 结构。
W7_EXCLUDE_STATES = ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION")
# V5.3（20260925）按跟踪库 w7_hvt 69 条有跟踪样本复核（口径=选股日收盘成本、昨日收盘市值、等权）：
# ① score 降级为纯展示。原按 score 定 A/B/C 级，但控制批次后 ≥60 分在三个批次内全部劣于 <60 分
#    （A批 8月底 +9.20% vs +16.28%；B批 9月中 +1.54% vs +7.49%；C批 9月下 -2.30% vs -0.70%，
#     C 批 ≥60 分 8 条胜率 0%），无正向区分度，不再参与定级与排序。
# ② 定级/排序改用实测单调的三因子：
#    type=MID：+13.64%/胜率74.1%（n=27） vs EXT +5.06%/61.9%（n=42）
#    ige_adj≥75：75-85 档 +21.93%/胜率93.3%（n=15）；60-75 档 +11.81%/85.7%（n=7）；<60 档 +5.18%/64.1%
#    volr<0.60：0.4-0.6 档 +10.71%/69.1%（n=42） vs 0.6-0.8 档 +4.56%/60.0%（n=25）
#    （MID 与时间变量有部分混淆——批次间 type 构成不均衡，故按「排序加权+分层」落地，不做硬过滤）
W7_IGE_STRONG = 75.0
W7_VOLR_STRONG = 0.60
# 失效位口径修订（20260925）：原 stop_price=触发价，距现价中位仅 2.73%、平均 3.11%；22 条带止损
# 记录中 17 条触发、其中 10 条事后仍收正（一博科技 +33.1%/宏昌电子 +24.9%/双星新材 +15.8%/
# 高能环境 +10.6% 均被 2~3% 插针洗出）。改为「结构失效位 + 百分比上下限」：结构位取
# 触发价 / MA20 / 放量突破日低点中最宽者，再强制距现价 ∈[5%,8%]——<5% 的插针区不触发，
# >8% 的结构位收敛到 8% 以控单笔风险。
W7_STOP_MIN_PCT = 0.05
W7_STOP_MAX_PCT = 0.08
# W7 输出层三状态执行分层（V1.0，20261001）——只改「展示 + 执行状态」分类，不动候选池生成、评分公式、
# IGE_ADJ、CORE/MID 类型、触发价、MA20、量比、回测与其他策略模块。口径：
#   EXECUTION             现价 ≥ 触发价 且 结构未失效（量比 ≥1.2 视为量能确认）
#   EXECUTION_WAIT_VOLUME 现价 ≥ 触发价 但 量比 <1.2 —— 仍属 EXECUTION 大类，禁止标为「已确认买入」
#   TRIGGER_WATCH         0 ≤ 距触发位 ≤3% —— 只差一个放量确认
#   PULLBACK_WATCH        距触发位 >3% —— 等待回踩/重新确认
#   INVALID               收盘跌破各形态自身结构失效位 —— 内部硬过滤，不作为三状态之一展示
# 失效位按各形态自身口径：MIDLINE_HOLD=长阳半分位、BREAKOUT_RETEST=回踩低点、其余=MA20。
# MA20 总防线留 3% 容差（20261001 裁定）：收盘低于 MA20 未超 3% 归 PULLBACK_WATCH，超 3% 才 INVALID，
# 与「§4 跌破 MA20 → PULLBACK_WATCH」自洽；长阳半分位/回踩低点仍按严格跌破判定。
# 排序：W7_STATUS > 距触发位 > IGE_ADJ > W7总分（W7 是交易执行模块，执行状态优先于分数，
# 禁止总分高即自动 EXECUTION、禁止距触发远却因 IGE_ADJ 高而 EXECUTION）。
# 量能阀门恒为量比 ≥1.2（原策略口径），不因状态升级而豁免，也禁止为凑数放宽阈值。
W7_TRIGGER_BAND = 0.03
W7_MA20_BREAK_TOL = 0.03
W7_VOL_CONFIRM = 1.2
W7_EXEC_RANK = {"EXECUTION": 0, "EXECUTION_WAIT_VOLUME": 1, "TRIGGER_WATCH": 2, "PULLBACK_WATCH": 3}
WANTED_COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "turnover_rate", "turnover_rate_f", "circ_mv", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60", "ma_bfq_120"]


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def w7_priority(x):
    """V5.3 因子优先级（0~3 命中数）。依据见文件头 W7_IGE_STRONG / W7_VOLR_STRONG 注释。
    score 已降级为纯展示（控制批次后无正向区分度），定级与排序均以本函数为准。"""
    hits = 0
    if x.get("type") == "MID":
        hits += 1
    ige = x.get("ige_adj")
    if isinstance(ige, (int, float)) and ige >= W7_IGE_STRONG:
        hits += 1
    if finite(x.get("volr"), 9.9) < W7_VOLR_STRONG:
        hits += 1
    return hits


def w7_assign_status(state, tp, ige_adj, volr, fallback="WATCH"):
    """V5.3 定级（20260925）：原 score≥85/≥80/≥70 阈值已废止——控制批次后 ≥60 分在 A/B/C 三批次内
    均劣于 <60 分（最新批次 0918~0923 高分胜率 0%），无正向区分度。买点态改按实测单调的三因子
    命中数分 A/B/C 级（w7_priority）；派发态恒 WATCH；非买点态沿用调用方兜底结论 fallback。"""
    if tp == "DISTRIBUTION":
        return "WATCH"
    if state not in ACTION_BUY_STATES:
        return fallback
    hits = w7_priority({"type": tp, "ige_adj": ige_adj, "volr": volr})
    if hits >= 3:
        return "PRIMARY_BUY"
    if hits == 2:
        return "T120_ROCKET"
    if hits == 1:
        return "CONFIRMED"
    return "WATCH"


def w7_stop_price(close, pressure, ma20, retest_low=None, mid_line=None, right_low=None):
    """V5.3 失效位：结构位（触发价/MA20/放量突破日低点/长阳半分位/双底左底）取最宽者，再 clamp 到距现价 [5%,8%]。
    现价无效时返回 None（下游不设止损）。"""
    close = finite(close, 0.0)
    if close <= 0:
        return None
    cands = [v for v in (finite(pressure, 0.0), finite(ma20, 0.0), finite(retest_low, 0.0),
                         finite(mid_line, 0.0), finite(right_low, 0.0))
             if 0 < v < close]
    struct = min(cands) if cands else close
    return round(min(max(struct, close * (1 - W7_STOP_MAX_PCT)),
                     close * (1 - W7_STOP_MIN_PCT)), 3)


def w7_fail_line(x):
    """各形态自身结构失效位（20261001 裁定）：MIDLINE_HOLD=长阳半分位；BREAKOUT_RETEST=回踩低点；
    RIGHT_BOTTOM=左底（前低）；其余=MA20。返回 (失效位价格, 名称)；位缺失时返回 (0.0, 名称)。"""
    if x.get("state") == "MIDLINE_HOLD" and finite(x.get("mid_line"), 0.0) > 0:
        return finite(x["mid_line"]), "长阳半分位"
    if x.get("state") == "BREAKOUT_RETEST" and finite(x.get("retest_low"), 0.0) > 0:
        return finite(x["retest_low"]), "回踩低点"
    if x.get("state") == "RIGHT_BOTTOM" and finite(x.get("rb_left_low"), 0.0) > 0:
        return finite(x["rb_left_low"]), "左底（前低）"
    return finite(x.get("ma20"), 0.0), "MA20"


def w7_distance_to_trigger(x):
    """距触发位 = (触发价 − 现价) / 触发价；已站上触发价记为 0（不取负，避免“越涨越靠前”）。"""
    close, pressure = finite(x.get("close"), 0.0), finite(x.get("pressure"), 0.0)
    if close <= 0 or pressure <= 0:
        return 0.0
    return max(0.0, (pressure - close) / pressure)


def w7_exec_status(x):
    """W7 输出层三状态执行分层（V1.0，20261001）。返回 (status, 失效位价格, 失效位名称)。

    只做执行状态分类：候选池、评分、IGE_ADJ、CORE/MID 类型、触发价、MA20、量比口径一律沿用上游结果。
    INVALID 为内部硬过滤（收盘跌破各形态自身失效位），不进入三状态展示；
    量能阀门恒为量比 ≥1.2，不因状态升级而豁免（价到位而量未到 → EXECUTION_WAIT_VOLUME）。
    MA20 总防线留 W7_MA20_BREAK_TOL 容差，未超容差归 PULLBACK_WATCH；其余失效位按严格跌破。

    20261002 例外·RIGHT_BOTTOM（右底低吸）：买点＝当日右底本身（现价即低吸区），
    并非「等放量站上颈线才触发」，故不以「距颈线距离」分档；结构未失效（收盘 ≥ 左底/前低）
    即归入执行区 EXECUTION。右底须缩量（不适用放量阀门），量比口径不参与该形态判定。
    """
    fail_line, fail_name = w7_fail_line(x)
    close = finite(x.get("close"), 0.0)
    line_eff = fail_line * (1 - W7_MA20_BREAK_TOL) if fail_name == "MA20" else fail_line
    if line_eff > 0 and close < line_eff:
        return "INVALID", fail_line, fail_name
    if x.get("state") == "RIGHT_BOTTOM":
        return "EXECUTION", fail_line, fail_name
    dist = w7_distance_to_trigger(x)
    if dist <= 0:  # 现价 ≥ 触发价：已进入价格执行区
        if finite(x.get("volr"), 0.0) < W7_VOL_CONFIRM:
            return "EXECUTION_WAIT_VOLUME", fail_line, fail_name
        return "EXECUTION", fail_line, fail_name
    if dist <= W7_TRIGGER_BAND:
        return "TRIGGER_WATCH", fail_line, fail_name
    return "PULLBACK_WATCH", fail_line, fail_name


def w7_exec_sort_key(x):
    """三状态榜排序（20261001 §6）：W7_STATUS 优先，同级内按各状态自身口径，最后统一 IGE_ADJ → W7总分。
      EXECUTION          量能确认程度 → 距触发位 → IGE_ADJ → W7总分
      TRIGGER_WATCH      距触发最近 → 量能接近阀门 → IGE_ADJ → W7总分
      PULLBACK_WATCH     结构完整度（离自身失效位缓冲）→ 距触发 → IGE_ADJ → W7总分
    执行状态优先于分数：总分高的 PULLBACK_WATCH 不得越过总分低的 EXECUTION。"""
    st = x.get("w7_status") or w7_exec_status(x)[0]
    rank = W7_EXEC_RANK.get(st, 9)
    d = w7_distance_to_trigger(x)
    ige = x.get("ige_adj") if isinstance(x.get("ige_adj"), (int, float)) else -1e9
    sc = finite(x.get("score"), 0.0)
    volr = finite(x.get("volr"), -1.0)
    if rank <= 1:  # EXECUTION / EXECUTION_WAIT_VOLUME
        # 20261002：RIGHT_BOTTOM（右底低吸）当日即无等待条件的买点，排在 EXECUTION 组内最前；
        # 其余 EXECUTION 仍按量能确认程度排序。
        rb = 0 if x.get("state") == "RIGHT_BOTTOM" else 1
        return (rank, rb, -volr, abs(d), -ige, -sc)
    if rank == 2:  # TRIGGER_WATCH
        return (rank, d, -volr, -ige, -sc)
    line = x.get("w7_fail_line")  # PULLBACK_WATCH
    if not isinstance(line, (int, float)) or line <= 0:
        line = w7_fail_line(x)[0]
    close = finite(x.get("close"), 0.0)
    dbuf = (close - line) / line if line > 0 and close > 0 else -1e9
    return (rank, -dbuf, d, -ige, -sc)


def load_sli_codes(date):
    """加载 SLI V2 细分赛道 Top5 龙头代码集合（V4.4：不在票池中的候选直接过滤）
    标准接口 sli.reader.get_subsector_top5（asof 自动对齐最近快照）；无快照时返回 None 不启用过滤"""
    try:
        from sli.reader import get_subsector_top5
        panel = get_subsector_top5(asof=date)
    except Exception as exc:  # 快照缺失/包未生成时降级，不中断主流程
        print(f"[w7] 警告：SLI 龙头票池加载失败({exc})，跳过联动过滤", flush=True)
        return None
    codes = set(panel["ts_code"].astype(str).str.strip())
    codes.discard("")
    print(f"[w7] SLI 龙头票池 {len(codes)} 只，启用联动过滤", flush=True)
    return codes


def build_allow_codes(date, sli_codes):
    """准入池 = sli_v2 龙头票池 ∪ 热点主题(V2.4)全部成员；sli_codes 为 None 时不过滤。

    与 HVT-BULL 同口径（theme_hot_pool.hot_theme_codes）。扩池数据缺失时退回纯龙头过滤。
    """
    if sli_codes is None:  # 龙头过滤不可用 -> 池子本就未过滤，无需扩池
        return None
    allow = set(sli_codes)
    if THEME_EXPAND_ENABLED:
        try:
            from theme_hot_pool import hot_theme_codes
            hot = hot_theme_codes(date, window=THEME_EXPAND_WINDOW,
                                  heat_min=THEME_EXPAND_HEAT_MIN)
        except Exception as exc:
            print(f"[w7] 警告：热点主题扩池加载失败({exc})，维持龙头过滤", flush=True)
            hot = None
        if hot:
            print(f"[w7] 热点主题扩池：+{len(hot - allow)} 只非龙头"
                  f"（{THEME_EXPAND_WINDOW}热度>={THEME_EXPAND_HEAT_MIN:g}）", flush=True)
            allow |= hot
    return allow or None


def load_ige_adj(asof=""):
    """加载行业增长弹性 IGE_ADJ 快照（ige/output/ige_full_{date}.csv，股票级）。
    优先选与 asof 同日期的文件，无则回退最近快照。返回 (info, snap)；
    info[code] = {ige_adj, ige_mix, sw_l1, sw_l3}，缺失股票不入表（主流程注入 None 排尾）。"""
    if not os.path.isdir(IGE_OUT_DIR):
        print("[w7] IGE 输出目录不存在，跳过 IGE_ADJ 接入", flush=True)
        return {}, ""
    try:
        files = sorted(f for f in os.listdir(IGE_OUT_DIR)
                       if re.fullmatch(r"ige_full_\d{8}\.csv", f))
    except OSError as exc:
        print(f"[w7] IGE 目录读取失败: {exc}", flush=True)
        return {}, ""
    if not files:
        print("[w7] 无 ige_full_*.csv 快照，跳过 IGE_ADJ 接入", flush=True)
        return {}, ""
    target = f"ige_full_{asof}.csv" if asof else ""
    chosen = target if target in files else files[-1]
    snap = chosen[len("ige_full_"):-len(".csv")]
    if target and chosen != target:
        print(f"[w7] IGE 无 {asof} 同日快照，回退最近快照 {snap}", flush=True)
    try:
        df = pd.read_csv(os.path.join(IGE_OUT_DIR, chosen),
                         encoding="utf-8-sig", dtype={"code": str})
    except Exception as exc:
        print(f"[w7] IGE 快照 {chosen} 读取失败: {exc}", flush=True)
        return {}, ""
    need = [c for c in ("code", "ige_adj", "ige_mix", "sw_l1", "sw_l3") if c in df.columns]
    if "code" not in need or "ige_adj" not in need:
        print(f"[w7] IGE 快照 {chosen} 缺必需列，跳过 IGE_ADJ 接入", flush=True)
        return {}, ""
    info = {}
    for rec in df[need].to_dict("records"):
        code = str(rec.get("code") or "").strip()
        if not code:
            continue
        info[code] = {
            "ige_adj": finite(rec.get("ige_adj"), None),
            "ige_mix": finite(rec.get("ige_mix"), None),
            "sw_l1": "" if pd.isna(rec.get("sw_l1")) else str(rec["sw_l1"]),
            "sw_l3": "" if pd.isna(rec.get("sw_l3")) else str(rec["sw_l3"]),
        }
    print(f"[w7] IGE_ADJ 快照={snap} 覆盖={len(info)}", flush=True)
    return info, snap


def clip(value, low=0.0, high=100.0):
    return max(low, min(high, finite(value)))


def percentile_rank(values, value):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return 0.0
    return float(np.mean(values <= value) * 100.0)


def pct_position(open_, high, low, close):
    span = high - low
    return clip((close - low) / span * 100.0 if span > 0 else 50.0)


def safe_mean(values, default=0.0):
    values = [finite(x) for x in values if np.isfinite(finite(x))]
    return float(np.mean(values)) if values else default


# ─────────────────────────────────────────────
# 数据源：窄表（W7 迁移，替代 stk_factor_pro 261 列宽表）
#   行情(OHLCV/pct_chg/vol) = daily_cache(pro.daily)
#   换手/市值             = daily_basic_cache
#   复权因子              = adj_factor_cache
# ma_bfq_* 不再读宽表：本地按前复权收盘(=close×adj/每股末因子)滚动计算，复现原口径。
# ─────────────────────────────────────────────
_BARS_SELECT = """SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
       d.pct_chg, d.vol, d.amount,
       b.turnover_rate, b.turnover_rate_f, b.volume_ratio, b.circ_mv,
       a.adj_factor
  FROM daily_cache AS d
  LEFT JOIN daily_basic_cache AS b
         ON b.ts_code = d.ts_code AND b.trade_date = d.trade_date
  LEFT JOIN adj_factor_cache AS a
         ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date"""
_MA_WINDOWS = ((10, "ma_bfq_10"), (20, "ma_bfq_20"), (60, "ma_bfq_60"), (120, "ma_bfq_120"))


def _qfq_price(df):
    """前复权收盘序列：close × adj_factor / 每股末个有效因子（锚定序列末端=引擎 end_date）。

    与 stk_factor_pro 入库口径一致（入库日最新因子做整体缩放，不改变相对比值）。
    adj_factor 全缺时退回原始 close，保证均线仍可用。
    """
    close = df["close"].astype(float)
    if "adj_factor" not in df.columns or df["adj_factor"].isna().all():
        return close
    if "ts_code" not in df.columns:
        adjf = df["adj_factor"].astype(float).ffill()
        if adjf.isna().all() or not np.isfinite(adjf.iloc[-1]):
            return close
        return close * adjf / adjf.iloc[-1]
    adjf = df.groupby("ts_code")["adj_factor"].ffill()
    last = adjf.groupby(df["ts_code"]).transform("last")
    has = last.notna() & adjf.notna()
    price = close.copy()
    price[has] = close[has] * adjf[has] / last[has]
    return price


def _fill_ma_columns(df, price=None):
    """写入 ma_bfq_10/20/60/120（满窗滚动均线；组内需按 trade_date 升序）"""
    df = df.copy()
    if price is None:
        price = _qfq_price(df)
    grp = df["ts_code"] if "ts_code" in df.columns else None
    if grp is None:
        for w, col in _MA_WINDOWS:
            df[col] = price.rolling(w, min_periods=w).mean()
    else:
        for w, col in _MA_WINDOWS:
            df[col] = price.groupby(grp).transform(
                lambda s: s.rolling(w, min_periods=w).mean())
    return df


MARKET_INDEX_CODE = "000001.SH"  # 市场环境基准指数（原用全市场等权日收益近似，现改用 index_daily_cache 真实指数）


def market_index_series(conn, end_date, start_date=None):
    """读取 index_daily_cache 基准指数收盘序列（升序）

    Returns: (dates ndarray, close ndarray)；缓存缺失时返回空数组
    """
    sql = ("SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? AND trade_date<=?")
    params = [MARKET_INDEX_CODE, str(end_date)]
    if start_date:
        sql += " AND trade_date>=?"
        params.append(str(start_date))
    sql += " ORDER BY trade_date"
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        return np.array([], dtype=object), np.array([])
    return (df.trade_date.astype(str).to_numpy(),
            pd.to_numeric(df.close, errors="coerce").to_numpy(dtype=float))


def market_regime(conn, trade_date):
    """市场环境判断：基于 index_daily_cache 基准指数的 r20/r60（原实现为全市场等权日收益近似）"""
    _dates, c = market_index_series(conn, trade_date)
    if len(c) < 25:
        return "RANGE"
    c = c[-65:]
    r20 = c[-1] / c[-21] - 1.0
    r60 = c[-1] / c[0] - 1.0
    if r20 > 0.06 and r60 > 0.03:
        return "BULL"
    if r20 > 0.02 or (r20 > -0.02 and r60 > 0):
        return "RECOVERY"
    if r20 < -0.06 and r60 < -0.03:
        return "BEAR"
    return "RANGE"


class CacheReader:
    def __init__(self, db_path=DB_PATH, cache_dir=CACHE_DIR):
        self.db_path = db_path
        self.cache_dir = cache_dir
        self.conn = sqlite3.connect(db_path)
        self.basic = self._load_basic()

    def _load_basic(self):
        if not os.path.exists(BASIC_PATH):
            return pd.DataFrame(columns=["ts_code", "name", "industry", "list_date"])
        df = pd.read_csv(BASIC_PATH, dtype={"ts_code": str, "list_date": str})
        return df.drop_duplicates("ts_code").set_index("ts_code")

    def latest_date(self):
        row = self.conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()
        return str(row[0]) if row and row[0] else ""

    def universe(self, trade_date):
        df = pd.read_sql_query("SELECT ts_code, trade_date, circ_mv, total_mv FROM daily_basic_cache WHERE trade_date=?", self.conn, params=(trade_date,))
        if df.empty:
            return df
        snapshot_path = os.path.join(self.cache_dir, "market_" + trade_date + ".csv")
        if os.path.exists(snapshot_path):
            snapshot = pd.read_csv(snapshot_path, dtype={"ts_code": str})
            keep = [col for col in ["ts_code", "name", "industry", "total_mv", "circ_mv"] if col in snapshot]
            df = df.merge(snapshot[keep].drop_duplicates("ts_code"), on="ts_code", how="left", suffixes=("", "_snapshot"))
            for col in ["name", "industry"]:
                if col not in df:
                    df[col] = np.nan
        if "circ_mv_snapshot" in df:
            df["circ_mv"] = df["circ_mv"].fillna(df["circ_mv_snapshot"])
        if "circ_mv" not in df:
            df["circ_mv"] = df.get("total_mv", np.nan)
        df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
        df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
        df = df[df.circ_mv.fillna(0) >= MIN_CIRC_MV]
        if "name" not in df:
            df["name"] = np.nan
        if "industry" not in df:
            df["industry"] = np.nan
        df["name"] = df["name"].fillna(df.ts_code.map(self.basic.get("name", pd.Series(dtype=str))))
        df["industry"] = df["industry"].fillna(df.ts_code.map(self.basic.get("industry", pd.Series(dtype=str))))
        return df.drop_duplicates("ts_code")

    def bars_sql(self, ts_code, end_date):
        """逐只查询全历史（仅用于锚点等少量代码）；ma_bfq_* 本地前复权滚动计算"""
        q = (_BARS_SELECT + " WHERE d.ts_code=? AND d.trade_date<=? ORDER BY d.trade_date")
        df = pd.read_sql_query(q, self.conn, params=(ts_code, end_date))
        if df.empty:
            return df
        df = _fill_ma_columns(df)
        numeric = [c for c in df.columns if c not in ("ts_code", "trade_date")]
        for col in numeric:
            if not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close", "high", "low", "vol"]).reset_index(drop=True)

    def bars(self, ts_code, end_date):
        df = self.frames.get(ts_code)
        if df is None or df.empty:
            return pd.DataFrame()
        return df[df.trade_date <= end_date].reset_index(drop=True)

    def load_all(self, end_date, codes=None, min_date=None, chunk=500, verbose=False):
        """按代码分批 + 日期范围加载历史（IN 参数过多会导致 SQLite 编译极慢，chunk 保持 500）

        W7: 源为 daily_cache + daily_basic_cache + adj_factor_cache 窄表三表连接；
        ma_bfq_* 加载后按每股前复权收盘本地滚动计算（不再读 stk_factor_pro 宽表）。
        """
        if min_date is None:
            min_date = DATA_START
        parts = []
        code_list = list(codes) if codes else [None]
        t0 = time.time()
        for start in range(0, len(code_list), chunk):
            batch = code_list[start:start + chunk]
            if batch == [None]:
                where, params = "d.trade_date>=? AND d.trade_date<=?", [min_date, end_date]
            else:
                placeholders = ",".join(["?"] * len(batch))
                where, params = (f"d.trade_date>=? AND d.trade_date<=? AND d.ts_code IN ({placeholders})",
                                 [min_date, end_date] + batch)
            q = f"{_BARS_SELECT} WHERE {where} ORDER BY d.trade_date"
            parts.append(pd.read_sql_query(q, self.conn, params=params))
            if verbose:
                print(f"[load] {len(parts)}/{len(code_list)} 行数={len(parts[-1])} 耗时={time.time()-t0:.1f}s", flush=True)
        if not parts:
            self.frames = {}
            return 0
        df = pd.concat(parts, ignore_index=True)
        df = df.dropna(subset=["close", "high", "low", "vol"]).copy()
        numeric = [c for c in df.columns if c not in ("ts_code", "trade_date")]
        for col in numeric:
            if not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = _fill_ma_columns(df)
        frames = {}
        for code, group in df.groupby("ts_code", sort=False):
            frames[code] = group.reset_index(drop=True)
        self.frames = frames
        return len(frames)

    def load_fina(self):
        """加载财务指标缓存（or_yoy 营收增速 / netprofit_yoy 净利增速 / 毛利率 / 现金流质量）"""
        try:
            df = pd.read_sql_query(
                "SELECT ts_code, end_date, ann_date, grossprofit_margin, netprofit_yoy, or_yoy, netprofit_2yoy, ocf_to_or, roe FROM fina_indicator_cache",
                self.conn)
        except Exception:
            df = pd.DataFrame()
        self.fina_frames = {}
        if not df.empty:
            for code, g in df[df.end_date != "EMPTY"].groupby("ts_code"):
                self.fina_frames[code] = g.sort_values("end_date").reset_index(drop=True)
        return len(self.fina_frames)

    def fina(self, ts_code, as_of=None):
        """返回 (最新一期, 上一期) 财务指标；as_of 用于 point-in-time（仅取 ann_date<=as_of 的已公告数据，防未来函数）"""
        g = self.fina_frames.get(ts_code)
        if g is None or g.empty:
            return None, None
        if as_of is not None:
            ann = g.ann_date.astype(str).str.slice(0, 8)
            valid = g[ann <= as_of]
            if valid.empty:
                return None, None
            g = valid
        now = g.iloc[-1]
        prev = g.iloc[-2] if len(g) >= 2 else None
        return now, prev

    def market_curve(self, end_date, min_date=None):
        """市场环境基准曲线（RS 与市场环境判断）：改用 index_daily_cache 真实指数收盘，
        归一化到起点=1.0，与原全市场等权累计收益曲线同量纲（原 CSV 增量缓存不再需要）"""
        if min_date is None:
            min_date = DATA_START
        dates, vals = market_index_series(self.conn, end_date, min_date)
        if len(dates) == 0 or vals[0] <= 0:
            return dates, vals
        return dates, vals / vals[0]

    def close(self):
        self.conn.close()


@dataclass
class EventAnalysis:
    state: str
    event_idx: int
    event_date: str
    event_percentile: float
    cq: float
    acceptance: float
    sds: float
    lock_score: float
    pp_score: float
    reexpansion: bool
    breakout: bool
    sim_zjxc: float
    sim_hzxc: float
    hvt_sim: float
    score: float
    grade: str
    buy_point: str
    explanation: str
    hard_fail: bool


def trend_features(df, i):
    if i < 65:
        return {"trend": 25.0, "rs": 50.0, "ma20_slope": 0.0, "ma60_slope": 0.0, "position": 50.0}
    row = df.iloc[i]
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[i - 19:i + 1]))
    ma60 = finite(row.ma_bfq_60, safe_mean(df.close.iloc[i - 59:i + 1]))
    ma20_prev = finite(df.iloc[i - 10].ma_bfq_20, safe_mean(df.close.iloc[i - 29:i - 9]))
    ma60_prev = finite(df.iloc[i - 20].ma_bfq_60, safe_mean(df.close.iloc[i - 79:i - 19]))
    slope20 = ma20 / ma20_prev - 1 if ma20_prev else 0
    slope60 = ma60 / ma60_prev - 1 if ma60_prev else 0
    ret20 = finite(row.close) / finite(df.iloc[i - 20].close, row.close) - 1
    trend = clip(35 + (ma20 > ma60) * 25 + clip(slope20 * 500, -20, 20) + clip(slope60 * 300, -10, 10) + clip(ret20 * 100, -15, 15))
    position = clip((row.close / ma60 - 0.9) * 500) if ma60 else 50
    return {"trend": trend, "rs": clip(50 + ret20 * 500), "ma20_slope": slope20, "ma60_slope": slope60, "position": position}


def limit_down_pct(ts_code):
    """板块跌停幅度（%）：科创板/创业板 20cm，主板 10cm（与 HVT `_limit_up_ratio` 同源）"""
    return 20.0 if str(ts_code or "")[:3] in ("688", "689", "300", "301", "302") else 10.0


def extreme_event(df, i, window=None):
    # window=None：V5 主口径——事件日前 120 日分位（HVT-V3 规格：成交量≥120日99%分位；量能+成交额双≥P99）
    # window=250：锚点口径——事件日前 250 日分位（与锚点识别时的历史口径一致）
    # 20261001 用户口径（两项变更）：
    #   ① 天量判定不再比换手率，改比成交金额：换手率维度整维替换为 amount（成交额），
    #      仍保留 vol（成交量）维度；两维历史分位双 ≥ P99。
    #   ② 天量标志日前一日涨幅 > 10% 时，当日方向豁免（不再要求收阳）；
    #      豁免只免方向、保留「不烂尾」底线：T0 不跌停 且 收盘位置 ≥ 0.30（防巨量大阴线）。
    #   原口径（20260930）：须收阳（close>open），巨量大阴线不计入极端换手事件。
    #   案例：300458 全志科技 7/30 巨量 -19.93% 阴线原被锚为 T0（锚点 P99@20260730），
    #   加「须收阳」后 7/30 被剔除，最近一簇回退到 7/22 放量长阳（+11.14%，量能更大）。
    window = 120 if window is None else window
    start = i - window
    if start < 0 or i - start < min(window, MIN_BARS):
        return False, 0.0
    row = df.iloc[i]
    prev_pct = finite(df.iloc[i - 1].pct_chg) if i >= 1 else 0.0
    if prev_pct > 10.0:
        # 方向豁免：仅保留「不跌停 + 收盘位置≥0.30」
        if finite(row.pct_chg) <= -(limit_down_pct(row.get("ts_code")) - 0.5):
            return False, 0.0
        span = finite(row.high) - finite(row.low)
        if span > 0 and (finite(row.close) - finite(row.low)) / span < 0.30:
            return False, 0.0
    elif finite(row.close) <= finite(row.open):
        return False, 0.0
    amt = finite(row.amount)
    vol = finite(row.vol)
    amts = pd.to_numeric(df.amount.iloc[start:i], errors="coerce").fillna(0).values
    vols = pd.to_numeric(df.vol.iloc[start:i], errors="coerce").fillna(0).values
    p_amt = percentile_rank(amts, amt)
    p_vol = percentile_rank(vols, vol)
    # V4.3→V5：OR≥P98 命中面过大 → AND≥P99；窗口由全历史改为 120 日（规格口径）
    return min(p_amt, p_vol) >= MAIN_EVENT_PCT, max(p_amt, p_vol)


def extreme_cluster_anchor(df, candidates, merge_gap=2):
    """天量事件簇归并 + 簇首 T0 锚定（20260909 用户口径：我爱我家 8/19 天量涨停为 T0，
    8/20 量能更大但属同一事件延续 T1，不重锚——W7 原逻辑取 candidates[-1](最近一根)，
    会把主事件错锚到 8/20(收阴在锚线下方)，导致后续确认链整体错位）。

    规则：间隔 <= merge_gap 个交易日的极端量日归为同一事件簇；取最近一簇；
    簇内优先选“最早的涨停/极强阳日”(首根放量强势日) 为 T0 锚；簇内无强势日时，
    取**簇内量能最大的一天**为 T0（20260914 用户口径：移远通信 8/14 量 471246 为天量日 T0、
    8/17 量 320575 属同簇次高，原回退 candidates[-1] 会错锚到 8/17 且后续 8/31 放量突破
    与 9/9 缩量回踩整段漏判）。
    返回 (event_idx, event_percentile)。
    """
    if not candidates:
        return None
    clusters, cur = [], [candidates[0]]
    for c in candidates[1:]:
        if c[0] - cur[-1][0] <= merge_gap:
            cur.append(c)
        else:
            clusters.append(cur)
            cur = [c]
    clusters.append(cur)
    last_cluster = clusters[-1]

    def limit_like(i):
        r = df.iloc[i]
        if finite(r.close) <= finite(r.open):
            return False
        if finite(r.pct_chg) >= 9.0:  # 涨停/接近涨停
            return True
        high = finite(r.high)
        return high > 0 and finite(r.close) >= high * 0.98 and finite(r.pct_chg) >= 3.0

    for i, ep in last_cluster:
        if limit_like(i):
            return i, ep
    # 无强势首日：取簇内天量最大日（T0=天量日主事件），而非最近一根极端日
    return max(last_cluster, key=lambda c: finite(df.iloc[c[0]].vol))


def anchor_features(df, event_date):
    if df.empty or event_date not in set(df.trade_date.astype(str)):
        return None
    i = int(df.index[df.trade_date.astype(str) == event_date][0])
    if i < 250:
        return None
    is_extreme, ep = extreme_event(df, i, window=250)
    if not is_extreme:
        return None
    return behavior_features(df, i, ep)


def behavior_features(df, event_idx, event_percentile, end=None):
    end = min(len(df) - 1, event_idx + 20 if end is None else end)
    event = df.iloc[event_idx]
    after = df.iloc[event_idx + 1:end + 1]
    if after.empty:
        return None
    event_close = finite(event.close)
    event_low = finite(event.low)
    core = (finite(event.high) + event_low + event_close) / 3
    # 破坏程度以“跌破极端换手日低点”为基准（跌破才视为破坏，正常回调不误伤）
    after_low = finite(after.low.min(), event_low)
    breach = max(0.0, (event_low - after_low) / max(event_low, 1))
    max_dd = max(0.0, 1 - after_low / max(event_close, 1))
    vol_decay = clip((1 - safe_mean(after.vol.iloc[:min(5, len(after))]) / finite(event.vol, 1)) * 100)
    acceptance = clip(100 - breach * 400 - (finite(after.close.iloc[min(2, len(after) - 1)]) < event_low) * 25)
    if len(after) >= 3 and (after.pct_chg.iloc[:3] < 0).sum() >= 2:
        acceptance -= 10
    position = pct_position(event.open, event.high, event.low, event.close)
    down = after[after.close < after.open]
    down_decay = clip((1 - safe_mean(down.vol.iloc[-min(5, len(down)):]) / finite(event.vol, 1)) * 100) if not down.empty else 80
    hold = clip(100 - breach * 400)
    cq = clip(0.25 * position + 0.2 * hold + 0.2 * acceptance + 0.15 * vol_decay + 0.1 * hold + 0.1 * 70)
    recent = df.iloc[max(0, end - 4):end + 1]
    ratios = recent.vol / finite(event.vol, 1)
    drawdown = max(0.0, 1 - finite(recent.close.min(), event_close) / event_close)
    support = clip(100 - breach * 400 - (finite(recent.low.min(), event_low) < core * 0.97) * 15)
    trend = trend_features(df, end)
    vol_ratio_score = clip((1 - min(1, safe_mean(ratios.iloc[-5:]) / 0.6)) * 100)
    pos_score = pct_position(recent.open.iloc[-1], recent.high.iloc[-1], recent.low.iloc[-1], recent.close.iloc[-1])
    sds = clip(0.25 * vol_ratio_score + 0.30 * down_decay + 0.20 * support + 0.15 * pos_score + 0.10 * trend["trend"])
    lock = clip(0.30 * sds + 0.25 * cq + 0.20 * acceptance + 0.15 * support + 0.10 * trend["trend"])
    return {"event_percentile": event_percentile, "cq": cq, "acceptance": acceptance, "sds": sds, "lock": lock, "max_dd": max_dd, "trend": trend["trend"], "rs": trend["rs"], "position": position, "event_close": event_close, "event_open": finite(event.open), "event_low": event_low, "core": core, "vol_decay": vol_decay}


def pp_score(df, i, locked, market_ok=True):
    if i < 12 or not locked:
        return 0.0, False
    row = df.iloc[i]
    down = df.iloc[i - 10:i]
    max_down_vol = down.loc[down.close < down.open, "vol"].max()
    pp = finite(row.close) > finite(row.open) and finite(row.vol) > finite(max_down_vol) and pct_position(row.open, row.high, row.low, row.close) >= 65 and market_ok
    if not pp:
        return 0.0, False
    ma10 = finite(row.ma_bfq_10, safe_mean(df.close.iloc[i - 9:i + 1]))
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[i - 19:i + 1]))
    score = 60 + 15 * (row.close > ma10) + 10 * (ma10 > ma20) + 10 * (pct_position(row.open, row.high, row.low, row.close) >= 80) + 5 * (row.pct_chg > 2)
    return clip(score), True


def breakout_day(df, i, ref_close):
    """「放量突破日」判定（20260914 用户口径：移远通信 20260831 涨停 +9.99% / 量 232430=1.47×20日均量
    / 收盘 61.64 站上 T0(8/14) 收盘 60.69 = 放量突破日；沿用原 pressure 口径会因事件后首波冲高
    (8/18 高 62.77) 把压力位抬到 61.64 之上而漏判）。

    条件：① 强势阳线（收阳且涨幅 ≥ BREAKOUT_DAY_PCT）；② 显著放量（量 ≥ BREAKOUT_DAY_VOLR×前20日均量）；
    ③ 收盘创近 10 日收盘新高，或站上 ref_close（T0 收盘线）。
    """
    if i < 20:
        return False
    r = df.iloc[i]
    if finite(r.close) <= finite(r.open) or finite(r.pct_chg) < BREAKOUT_DAY_PCT:
        return False
    vol20 = safe_mean(df.vol.iloc[i - 20:i])
    if vol20 <= 0 or finite(r.vol) < vol20 * BREAKOUT_DAY_VOLR:
        return False
    prev_close_hi = finite(df.close.iloc[max(0, i - 10):i].max())
    return finite(r.close) > prev_close_hi or finite(r.close) > finite(ref_close)


def breakout_retest(df, event_idx, end, latest, ref_close):
    """「放量突破 → 缩量回踩」买点识别（返回 (回踩成立, 突破日 index or -1)）。

    原状态机在 T0 天量后若先深踩（移远通信 8/26 低 54.61 < T0 低 59.90×0.95）再放量突破，
    因 recent_hold 恒 False → major_risk 一路 FAILED；且 pressure 被事件后首波冲高抬到
    突破日收盘之上，breakout 也恒 False，整段漏判。

    规则：在事件后（不含当日）找「放量突破日」B，若其后满足
      ① 新鲜度：B 距当日 ≤ RETEST_MAX_AGE 根（更早的突破已非"回踩位附近"）；
      ② 真实回踩：B 之前（T0 之后）最低 ≤ T0 收盘 ×RETEST_PULLBACK（排除 T0 后单边直上的加速股）；
      ③ 不追高：现价 ≤ B 收盘 ×RETEST_MAX_EXT（已拉开则转由突破/二波分支处理）；
      ④ 缩量：回踩期近 3 日均量 ≤ B 量 ×RETEST_VOL_RATIO，且 ≤ 突破前 20 日均量 ×RETEST_DRYUP（真缩量）；
      ⑤ 回踩到位：回踩期收盘曾回到 B 收盘 ×RETEST_TOL 以内；
      ⑥ 回踩不破：回踩期最低 ≥ B 低点 ×0.99；
      ⑦ 守住防线：现价 ≥ MA20 ×RETEST_MA20_K。
    则回踩期每个交易日均为有效买点（取最早满足结构的突破日，保证跨日稳定）。
    """
    for j in range(event_idx + 1, end):
        if not breakout_day(df, j, ref_close):
            continue
        if end - j > RETEST_MAX_AGE:
            continue
        b = df.iloc[j]
        pre = df.iloc[event_idx + 1:j]
        if pre.empty or finite(pre.low.min()) > finite(ref_close) * RETEST_PULLBACK:
            continue
        if finite(latest.close) > finite(b.close, latest.close) * RETEST_MAX_EXT:
            continue
        seg = df.iloc[j + 1:end + 1]
        if seg.empty:
            continue
        vol20_base = safe_mean(df.vol.iloc[max(0, j - 20):j])
        retest_vol = safe_mean(seg.vol.iloc[-3:])
        if retest_vol > finite(b.vol, 1) * RETEST_VOL_RATIO:
            continue
        if vol20_base > 0 and retest_vol > vol20_base * RETEST_DRYUP:
            continue
        if finite(seg.close.min()) > finite(b.close) * RETEST_TOL:
            continue
        if finite(seg.low.min()) < finite(b.low) * 0.99:
            continue
        if finite(latest.close) < finite(latest.ma_bfq_20, latest.close) * RETEST_MA20_K:
            continue
        return True, j
    return False, -1


def midline_hold(df, end):
    """「W7-不破中位」买点识别（返回 (成立, 放量长阳日 index or -1)）。

    不依赖 T0 天量事件锚，只看最近 MIDLINE_MAX_AGE 根内的「放量长阳」及其后的缩量回踩：
      长阳日 B：收阳且涨幅 ≥MIDLINE_DAY_PCT、量 ≥MIDLINE_DAY_VOLR×前20日均量、收盘位于振幅上 1/3、
                且收盘 ≥ 前 MIDLINE_NH60 根最高价（平台突破属性，实测为本分支唯一有效判别）；
      中位    ：mid = (B.最高 + B.最低)/2（半分位）；
      ① 未创长阳新高：当日收盘 ≤ B.最高（仍在长阳范围内整理，非新一轮突破）；
      ② 未拉开    ：当日收盘 ≤ B.收盘 ×MIDLINE_MAX_EXT（仍是低吸位，已拉开转突破/二波分支）；
      ③ 不破中位  ：回踩期（B+1~当日）收盘均 ≥ mid（收盘口径），且最低 ≥ mid×MIDLINE_LOW_TOL（插针容忍）；
      ④ 缩量      ：当日量 ≤ B.量 ×MIDLINE_VOL_RATIO；
      ⑤ 确有回踩  ：回踩期收盘曾回落至 B.收盘 下方（排除长阳后单边直上的加速股）。
    满足即当日为有效买点；取最早满足的长阳日，保证跨日稳定。
    """
    if end < 21:
        return False, -1
    latest = df.iloc[end]
    for j in range(max(20, end - MIDLINE_MAX_AGE), end):
        b = df.iloc[j]
        if finite(b.close) <= finite(b.open) or finite(b.pct_chg) < MIDLINE_DAY_PCT:
            continue
        vol20 = safe_mean(df.vol.iloc[j - 20:j])
        if vol20 <= 0 or finite(b.vol) < vol20 * MIDLINE_DAY_VOLR:
            continue
        if pct_position(b.open, b.high, b.low, b.close) < MIDLINE_CLOSE_POS:
            continue
        if MIDLINE_NH60 > 0:
            if j < MIDLINE_NH60:
                continue
            prior_high = finite(df.high.iloc[j - MIDLINE_NH60:j].max(), 0.0)
            if prior_high <= 0 or finite(b.close) < prior_high:
                continue
        mid = (finite(b.high) + finite(b.low)) / 2.0
        if mid <= 0:
            continue
        seg = df.iloc[j + 1:end + 1]
        if seg.empty:
            continue
        if finite(latest.close) > finite(b.high):
            continue
        if finite(latest.close) > finite(b.close, latest.close) * MIDLINE_MAX_EXT:
            continue
        if finite(seg.close.min()) < mid:
            continue
        if finite(seg.low.min()) < mid * MIDLINE_LOW_TOL:
            continue
        if finite(latest.vol) > finite(b.vol, 1) * MIDLINE_VOL_RATIO:
            continue
        if finite(seg.close.min()) > finite(b.close, latest.close) * 0.995:
            continue
        return True, j
    return False, -1


def right_bottom(df, end):
    """「W7-右底低吸」买点识别（返回 (成立, 结构字典 or None)）。

    不依赖天量 T0 事件锚，只看价格自身的双底几何：H0（左侧主高）→ L1（左底）→ M（颈线）→ T（右底）。
    条件：
      ① 前波大涨：H0 之前 RIGHT_BOTTOM_LOOKBACK 内最低 → H0 涨幅 ≥RIGHT_BOTTOM_PRIOR_RISE；
      ② 左底有效回落：H0 → L1 回落 ≥RIGHT_BOTTOM_PULLBACK；
      ③ 中间反弹有效：L1 → M 反弹 ≥RIGHT_BOTTOM_REBOUND；
      ④ 当日探右底：low[T] 为近 RIGHT_BOTTOM_NEAR 根最低；
      ⑤ 不破左底 且 不高于左底 RIGHT_BOTTOM_TOL_HI：low[T] ∈ [L1×(1-TOL_LO), L1×(1+TOL_HI)]；
      ⑥ 自颈线回撤 ≥RIGHT_BOTTOM_RETRACE；
      ⑦ 右底缩量：vol[T] ≤RIGHT_BOTTOM_VOL_DRYUP×前20日均量；
      ⑧ 中期趋势未破：收盘 ≥MA60 且 MA60 近 5 根不弱；
      ⑨ 低吸位置：收盘 ≤MA10。
    满足即当日为右底低吸买点；红线为左底（收盘跌破即结构失效）。
    """
    if end < RIGHT_BOTTOM_WINDOW + 25:
        return False, None
    lows = df.low.to_numpy(dtype=float)
    highs = df.high.to_numpy(dtype=float)
    w0 = max(0, end - RIGHT_BOTTOM_WINDOW)
    h0 = w0 + int(np.argmax(highs[w0:end]))
    if end - h0 < 3:
        return False, None
    l1 = h0 + int(np.argmin(lows[h0:end + 1]))
    if end - l1 < 2:
        return False, None
    m = l1 + int(np.argmax(highs[l1:end + 1]))
    if m - l1 < 2:
        return False, None
    base_lo = lows[max(0, h0 - RIGHT_BOTTOM_LOOKBACK):h0 + 1].min()
    if base_lo <= 0 or highs[h0] <= 0 or highs[h0] / base_lo - 1 < RIGHT_BOTTOM_PRIOR_RISE:
        return False, None
    if 1 - lows[l1] / highs[h0] < RIGHT_BOTTOM_PULLBACK:
        return False, None
    if lows[l1] <= 0 or highs[m] / lows[l1] - 1 < RIGHT_BOTTOM_REBOUND:
        return False, None
    if lows[end] > lows[max(0, end - RIGHT_BOTTOM_NEAR + 1):end + 1].min() + 1e-9:
        return False, None
    if lows[end] < lows[l1] * (1 - RIGHT_BOTTOM_TOL_LO):
        return False, None
    if lows[end] > lows[l1] * (1 + RIGHT_BOTTOM_TOL_HI):
        return False, None
    if highs[m] <= 0 or 1 - lows[end] / highs[m] < RIGHT_BOTTOM_RETRACE:
        return False, None
    v20 = safe_mean(df.vol.iloc[max(0, end - 20):end])
    if v20 <= 0 or finite(df.iloc[end].vol) > v20 * RIGHT_BOTTOM_VOL_DRYUP:
        return False, None
    ma10 = finite(df.iloc[end].ma_bfq_10, 0.0)
    ma60 = finite(df.iloc[end].ma_bfq_60, 0.0)
    ma60_prev = finite(df.iloc[max(0, end - 5)].ma_bfq_60, 0.0)
    close = finite(df.iloc[end].close, 0.0)
    if ma60 > 0 and (close < ma60 or (ma60_prev > 0 and ma60 / ma60_prev - 1 < RIGHT_BOTTOM_MA60_FLAT)):
        return False, None
    if ma10 > 0 and close > ma10:
        return False, None
    return True, {
        "rb_h0_date": str(df.iloc[h0].trade_date), "rb_h0_high": highs[h0],
        "rb_left_date": str(df.iloc[l1].trade_date), "rb_left_low": lows[l1],
        "rb_mid_date": str(df.iloc[m].trade_date), "rb_mid_high": highs[m],
        "rb_right_low": lows[end], "rb_ratio": lows[end] / lows[l1],
        "rb_prior_rise": highs[h0] / base_lo - 1.0,
        "rb_retrace": 1 - lows[end] / highs[m],
        "rb_volr20": finite(df.iloc[end].vol) / v20,
    }


def state_and_features(df, event_idx, event_percentile, end=None):
    end = len(df) - 1 if end is None else min(end, len(df) - 1)
    base = behavior_features(df, event_idx, event_percentile, end=end)
    if not base:
        return None
    event = df.iloc[event_idx]
    event_close = base["event_close"]
    core = base["core"]
    post = df.iloc[event_idx + 1:end + 1]
    recent = df.iloc[max(event_idx + 1, end - 4):end + 1]
    down = post[post.close < post.open]
    ratios = post.vol / finite(event.vol, 1)
    low_volume = safe_mean(ratios.iloc[-5:]) <= 0.65 if len(ratios) >= 3 else False
    drawdown = max(0.0, 1 - finite(post.low.min(), event_close) / event_close)
    early = post.iloc[:min(3, len(post))]
    early_hold = finite(early.low.min(), event.low) >= finite(event.low) * 0.97
    recent_window = df.iloc[max(0, end - 19):end + 1]
    recent_hold = finite(recent_window.low.min(), event.low) >= finite(event.low) * 0.95 and finite(df.iloc[end].close) >= finite(df.iloc[end].ma_bfq_20, event.low) * 0.92
    support_ok = early_hold and recent_hold
    persistent_sell = len(post) >= 3 and (post.close < post.open).tail(5).sum() >= 3 and safe_mean(down.vol.tail(3)) > finite(event.vol) * 0.75 if not down.empty else False
    # V4.1：CQ/Acceptance/SDS 阈值不再判死（转由 T120_ALPHA 的 HVT 维度降分体现），
    # 重大风险只保留两类：持续抛压、跌破天量关键支撑未收复
    major_risk = persistent_sell or not support_ok
    # 20260909 用户口径「T0 确认买点」：天量 T0 后首根收盘重回 event_close 之上的重夺日，
    # 其后连续 ≥2 根收盘仍站稳 event_close → 确认日当日即有效买点（8/26/8/27 站稳 → 8/27 买入）。
    # 收盘口径判定（盘中刺破不算，如 8/26 低 2.34<2.38 仍为确认日）；仅事件后早期窗口生效。
    # 收紧（20260909 实测 41→2，剔除阴跌/平淡天量 T0）：① T0 须为强势大涨日（涨停/近最高收大阳，
    # 与 extreme_cluster_anchor 的强势日口径同源）；② 必须“先回踩后重夺”（重夺日前至少 1 根收盘
    # 低于 T0 收盘，K>=1），排除自 T0 起单边站上的连板加速股（那类归真实突破分支处理）。
    t0_hold = False
    if not persistent_sell and 3 <= len(post) <= T0_CONFIRM_MAX_BARS:
        ev0 = df.iloc[event_idx]
        strong_t0 = (finite(ev0.close) > finite(ev0.open)) and (
            finite(ev0.pct_chg) >= 7.0 or
            (finite(ev0.high) > 0 and finite(ev0.close) >= finite(ev0.high) * 0.98 and finite(ev0.pct_chg) >= 3.0))
        cls = post.close.to_numpy(dtype=float)
        evc = float(event_close)
        ge = cls >= evc
        if strong_t0 and ge.any():
            k = int(np.argmax(ge))  # 首根重夺日（如 8/25）
            if k >= 1 and len(post) - (k + 1) >= 2:  # 先回踩，重夺后仍需 ≥2 根站稳（8/26、8/27）
                t0_hold = bool(cls[-1] >= evc and cls[-2] >= evc)
    latest = df.iloc[end]
    pressure = finite(df.high.iloc[max(event_idx + 1, end - 10):end].max(), latest.high)
    # V4.1 DISTRIBUTION 组合判定：巨量/长上影/收盘弱/破位 需同时出现多项，单因子只降分不淘汰
    vol20 = safe_mean(df.vol.iloc[max(0, end - 19):end])
    weak_close = pct_position(latest.open, latest.high, latest.low, latest.close) < 35
    huge_down = finite(latest.vol) > finite(event.vol) * 0.9 and finite(latest.close) < finite(latest.open)
    upper_shadow = (finite(latest.high) - max(finite(latest.open), finite(latest.close))) / max(finite(latest.high) - finite(latest.low), 0.01)
    distribution = (huge_down and weak_close and not recent_hold) or (upper_shadow > 0.5 and weak_close and not recent_hold and finite(latest.vol) > vol20 * 1.5)
    pp, pp_ok = pp_score(df, end, base["lock"] >= 70 and base["sds"] >= 65)
    reexp = pp_ok and finite(latest.vol) >= vol20 * 0.9 and finite(latest.close) >= pressure * 0.995
    breakout = finite(latest.close) > pressure and finite(latest.vol) >= vol20 * 1.2 and pct_position(latest.open, latest.high, latest.low, latest.close) >= 70 and (latest.high - latest.close) / max(latest.high - latest.low, 0.01) < 0.35
    # 20260914「放量突破日 / 放量突破后缩量回踩」买点（旁路 major_risk 的 T0 低点口径误判：
    # T0 后先深踩再放量突破时，旧 recent_hold 恒 False + pressure 被首波冲高抬高，导致整段 FAILED）
    breakout_now = breakout_day(df, end, event_close)
    retest, retest_bar = (False, -1)
    if not persistent_sell:
        retest, retest_bar = breakout_retest(df, event_idx, end, latest, event_close)
    # 20260930「W7-不破中位」：与上者并列的独立分支，不依赖 T0 天量事件锚；已成立放量突破回踩时不再叠加
    midhold, mid_bar = (False, -1)
    if not persistent_sell and not retest:
        midhold, mid_bar = midline_hold(df, end)
    # 20261002「W7-右底低吸」：与上者并列的独立分支，同样不依赖 T0 天量事件锚；
    # 该类结构（深度回调后双底）在 T0 口径下恒为 major_risk/DISTRIBUTION，故须在 major_risk 分支之前判定。
    right_bt, rb_info = (False, None)
    if not persistent_sell and not retest and not midhold:
        right_bt, rb_info = right_bottom(df, end)
    if rb_info:
        base.update(rb_info)
    base["breakout_now"] = breakout_now
    base["retest_bar"] = retest_bar
    base["retest_date"] = str(df.iloc[retest_bar].trade_date) if retest_bar >= 0 else ""
    base["retest_close"] = finite(df.iloc[retest_bar].close) if retest_bar >= 0 else 0.0
    base["mid_bar"] = mid_bar
    base["mid_date"] = str(df.iloc[mid_bar].trade_date) if mid_bar >= 0 else ""
    base["mid_close"] = finite(df.iloc[mid_bar].close) if mid_bar >= 0 else 0.0
    base["mid_high"] = finite(df.iloc[mid_bar].high) if mid_bar >= 0 else 0.0
    base["mid_line"] = (finite(df.iloc[mid_bar].high) + finite(df.iloc[mid_bar].low)) / 2.0 if mid_bar >= 0 else 0.0
    if len(post) < 5 and not t0_hold:
        # V4.2 放宽：事件太新不判死，仅持续放量抛压标记观察；其余等待验证期
        state = "FAILED" if persistent_sell else "EXTREME_CHURN"
    elif breakout and len(post) >= 5 and (pp_ok or reexp) and base["lock"] >= 70:
        state = "SECOND_WAVE"
    elif breakout or reexp:
        # V4.2 修复突破：重新放量突破压力位即算有效信号（优先于 FAILED/DISTRIBUTION）
        state = "BREAKOUT_CONFIRM" if breakout else "RE_EXPANSION"
    elif t0_hold:
        # 20260909：T0 天量锚定 + 重夺站稳≥2根 → 确认日当日有效买点（旁路等待期/派发误判）
        state = "T0_CONFIRM"
    elif breakout_now:
        # 20260914：放量突破日当日（强势阳线≥5% + 量≥1.3×20日均量 + 站上 T0 收盘线/近10日新高）
        state = "BREAKOUT_CONFIRM"
    elif retest:
        # 20260914：放量突破后缩量回踩，回踩期每个交易日均为有效买点
        state = "BREAKOUT_RETEST"
    elif midhold:
        # 20260930「W7-不破中位」：放量长阳后缩量回踩、收盘不破长阳半分位（插针容忍见 MIDLINE_LOW_TOL），当日为买点
        state = "MIDLINE_HOLD"
    elif right_bt:
        # 20261002「W7-右底低吸」：回调后双底右底（缩量、不破左底、收盘 ≤MA10 且 ≥MA60），当日为低吸买点
        state = "RIGHT_BOTTOM"
    elif major_risk:
        state = "FAILED" if len(post) >= 8 else "DISTRIBUTION"
    elif distribution:
        state = "DISTRIBUTION"
    elif pp_ok and base["lock"] >= 80 and base["sds"] >= 75:
        # DRYUP 收紧：回测显示宽口径 DRYUP 为负期望（close5 -0.35%），需较强锁筹才保留
        state = "DRYUP"
    elif low_volume and support_ok and base["sds"] >= 80 and base["lock"] >= 80:
        state = "DRYUP"
    elif support_ok and base["acceptance"] >= 70:
        state = "ABSORPTION"
    else:
        state = "EXTREME_CHURN"
    if state == "BREAKOUT_RETEST" and retest_bar >= 0:
        # 回踩买点：触发价改用放量突破日收盘（=回踩位，与其余状态「触发价=突破位」口径一致，
        # 下游 trade_execution_engine 的 Buy Zone=Trigger±1.5%、retest_score 均据此判定）；
        # 而非“事件后10日平台高点”（该高点常被 T0 后首波冲高抬高，与实际结构无关）。
        # 失效位仍为放量突破日低点/MA20，见报告「操作口径」。
        pressure = finite(df.iloc[retest_bar].close, pressure)
    elif state == "MIDLINE_HOLD" and mid_bar >= 0:
        # 触发价改用长阳日最高价：站上长阳上沿即转突破/二波分支；防线为长阳半分位（见报告「失效位」）
        pressure = finite(df.iloc[mid_bar].high, pressure)
    elif state == "RIGHT_BOTTOM" and rb_info:
        # 触发价改用双底颈线（中间高点）：放量站上颈线即转突破/二波分支；防线为左底（前低，见报告「失效位」）
        pressure = finite(rb_info["rb_mid_high"], pressure)
    return base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure


def grade(score):
    if score >= 92: return "★★★★★ SECOND_WAVE_A"
    if score >= 88: return "★★★★☆ SECOND_WAVE_B"
    if score >= 84: return "PRE_SECOND_WAVE"
    if score >= 78: return "LOCKING_WATCH"
    if score >= 70: return "WATCH"
    return "IGNORE"


def similarity(current, anchor):
    if not anchor:
        return 0.0
    fields = [("trend", 0.20), ("event_percentile", 0.15), ("cq", 0.15), ("acceptance", 0.15), ("sds", 0.15), ("lock", 0.10), ("rs", 0.10)]
    distance = sum(weight * min(1.0, abs(finite(current[k]) - finite(anchor[k])) / 100.0) for k, weight in fields)
    return clip((1 - distance) * 100)


# ===== V4.1：T120_ALPHA 六维 / ENTRY_SCORE =====
DIM_NAMES = {"hvt": "天量吸收", "trend": "趋势", "fina": "基本面", "rs": "相对强度", "upside": "上方空间", "sector": "板块"}


class MarketCtx:
    """全市场等权累计收益曲线（RS 基准）：个股交易日必然是市场交易日，searchsorted 可精确命中"""

    def __init__(self, dates, vals):
        self.dates = dates
        self.vals = vals

    def ret(self, d0, d1):
        p0 = int(np.searchsorted(self.dates, d0))
        p1 = int(np.searchsorted(self.dates, d1))
        if p0 >= len(self.vals) or p1 >= len(self.vals):
            return 0.0
        v0, v1 = self.vals[p0], self.vals[p1]
        return v1 / v0 - 1.0 if v0 > 0 else 0.0


def alpha_hvt(base, hvt_sim, drawdown):
    # 维度1 天量吸收 25%：Acceptance + CQ + SDS + 锚点相似度；浅回撤加分、深回撤扣分
    s = 0.35 * base["acceptance"] + 0.30 * base["cq"] + 0.20 * base["sds"] + 0.15 * hvt_sim
    if drawdown <= 0.10:
        s += 4
    elif drawdown > 0.20:
        s -= 8
    if base["acceptance"] >= 90 and hvt_sim >= 90 and drawdown <= 0.10:
        s = max(s, 96.0)  # 特别奖励：Acceptance≥90 AND HVT_SIM≥90 AND 天量后回撤≤10%
    return clip(s)


def alpha_trend(df, i):
    # 维度2 趋势 20%：均线排列 + 斜率 + 平台突破 + 更高低点 + 趋势加速
    row = df.iloc[i]
    close = finite(row.close)
    if close <= 0:
        return 40.0
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[max(0, i - 19):i + 1]))
    ma60 = finite(row.ma_bfq_60, safe_mean(df.close.iloc[max(0, i - 59):i + 1]))
    ma120 = finite(row.ma_bfq_120, safe_mean(df.close.iloc[max(0, i - 119):i + 1]))
    align = (close > ma20) + (ma20 > ma60) + (ma60 > ma120)
    ma20_prev = finite(df.iloc[i - 20].ma_bfq_20, close) if i >= 20 else close
    ma60_prev = finite(df.iloc[i - 40].ma_bfq_60, close) if i >= 40 else close
    slope20 = ma20 / ma20_prev - 1 if ma20_prev > 0 else 0.0
    slope60 = ma60 / ma60_prev - 1 if ma60_prev > 0 else 0.0
    hi60 = finite(df.high.iloc[max(0, i - 59):i + 1].max(), close)
    near_high = close / hi60 if hi60 > 0 else 1.0
    lo_recent = finite(df.low.iloc[max(0, i - 29):i + 1].min(), close)
    lo_prior = finite(df.low.iloc[max(0, i - 89):max(1, i - 29)].min(), lo_recent) if i >= 30 else lo_recent
    higher_low = lo_prior > 0 and lo_recent > lo_prior
    ret20 = close / finite(df.iloc[i - 20].close, close) - 1 if i >= 20 else 0.0
    ret20_prev = finite(df.iloc[i - 20].close, close) / finite(df.iloc[i - 40].close, close) - 1 if i >= 40 else 0.0
    accel = ret20 > ret20_prev and ret20 > 0
    s = clip(align * 15 + clip(slope20 * 400, 0, 20) + clip(slope60 * 250, 0, 15) + clip((near_high - 0.9) * 200, 0, 15) + (12 if higher_low else 0) + (8 if accel else 0) + 15)
    return clip(s)


def alpha_fina(fina_now, fina_prev):
    # 维度3 基本面 20%：营收/净利增速 + 利润加速度 + 毛利率 + 现金流；无数据给中性分（不因缺数据否定潜力股）
    if fina_now is None:
        return 55.0
    or_g = finite(fina_now.or_yoy)
    np_g = finite(fina_now.netprofit_yoy)
    margin = finite(fina_now.grossprofit_margin)
    ocf = finite(fina_now.ocf_to_or)

    def gsc(g):
        return clip(50 + g * 1.1)

    s = 0.30 * gsc(or_g) + 0.40 * gsc(np_g) + 0.15 * clip(margin * 1.3) + 0.15 * clip(50 + ocf * 160)
    if fina_prev is not None:
        acc = np_g - finite(fina_prev.netprofit_yoy)
        s += clip(acc * 0.4, -10, 10)  # 利润加速度：增速环比改善加分
    return clip(s)


def alpha_rs(df, i, mkt):
    # 维度4 相对强度 15%：20/60/120 日相对全市场等权超额（不只看绝对 RS），短>中>长 视为加速
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 50.0
    dates = df.trade_date.astype(str).to_numpy()

    def stock_ret(n):
        j = i - n
        base_px = finite(df.iloc[j].close, 0) if j >= 0 else 0.0
        return close / base_px - 1 if base_px > 0 else 0.0

    def mkt_ret(n):
        j = max(0, i - n)
        return mkt.ret(dates[j], dates[i]) if len(dates) else 0.0

    e20 = stock_ret(20) - mkt_ret(20)
    e60 = stock_ret(60) - mkt_ret(60)
    e120 = stock_ret(120) - mkt_ret(120)
    s20, s60, s120 = clip(50 + e20 * 250), clip(50 + e60 * 150), clip(50 + e120 * 100)
    s = 0.40 * s20 + 0.35 * s60 + 0.25 * s120
    if s20 >= s60 and s60 >= s120:
        s = min(100.0, s + 8)
    return clip(s)


def alpha_upside(df, i):
    # 维度5 上方空间 10%：距 60/120/250/全历史高点距离；趋势刚启动 + 长期套牢区远 = 修复空间大
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 40.0

    def dist(n):
        hi = finite(df.high.iloc[max(0, i - n + 1):i + 1].max(), close)
        return max(0.0, 1 - close / hi) if hi > 0 else 0.0

    def spd(d):
        return clip(40 + d * 300)

    return clip(0.25 * spd(dist(60)) + 0.25 * spd(dist(120)) + 0.30 * spd(dist(250)) + 0.20 * spd(dist(750)))


def alpha_sector(industry, sector_strength, sector_growth):
    # 维度6 板块 10%：行业内个股 20 日收益中位数（行业强度）+ 行业净利增速中位数（行业景气）
    if not industry or industry not in sector_strength:
        return 50.0
    return clip(0.6 * sector_strength[industry] + 0.4 * sector_growth.get(industry, 50.0))


# ===== V5：HVT-V3 生命周期 / 未来空间 / 加速度 / 平台 / 派发风险 / 评分 =====
BASE_DATE = "20240801"  # V5：生命周期 BasePrice 起点（本轮主要趋势启动基准日）
EXTREME_EXTENSION = 3.00  # 300% 以上标记 EXTREME_EXTENSION（国恩股份类高位股，不剔除、重分类）
WATCH_MIN_SCORE = 62.0  # V5：C榜(WATCH) 基分下限，低于此的低分兜底票不输出
LIFECYCLE_BANDS = [(0.30, "L1", "初始启动"), (0.80, "L2", "趋势启动"), (1.50, "L3", "趋势中段"),
                   (3.00, "L4", "趋势成熟"), (5.00, "L5", "高位扩张"), (float("inf"), "L6", "极端扩张")]


def lifecycle(df, i):
    """V5 生命周期：BasePrice=20240801 以来最低价，Trend Extension=close/BasePrice-1，分 L1~L6"""
    dates = df.trade_date.astype(str).to_numpy()
    idx = np.where(dates[:i + 1] >= BASE_DATE)[0]
    if len(idx) == 0:
        return {"level": "L1", "band": "初始启动", "extension": 0.0, "base_price": finite(df.low.iloc[max(0, i - 120)], 1.0), "extreme": False}
    base_price = float(df.low.iloc[idx].min())
    close = finite(df.iloc[i].close)
    ext = close / base_price - 1 if base_price > 0 else 0.0
    level, band = "L6", "极端扩张"
    for thr, lv, bn in LIFECYCLE_BANDS:
        if ext < thr:
            level, band = lv, bn
            break
    return {"level": level, "band": band, "extension": ext, "base_price": base_price, "extreme": ext >= EXTREME_EXTENSION}


def lifecycle_score(lc):
    """生命周期评分：L2（趋势刚启动）最优，L1 次之，L6 最低；EXT 靠加速度/趋势维度补偿"""
    return {"L1": 72.0, "L2": 85.0, "L3": 72.0, "L4": 52.0, "L5": 40.0, "L6": 30.0}.get(lc["level"], 50.0)


def hvt_future_space(df, i, lc):
    """V5 未来空间：距60/120/250日高点 + 平台突破临近 + ATR 适中 + MA 排列；CORE 重修复空间、EXT 重突破临近"""
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 40.0
    hi = {}
    for n in (60, 120, 250):
        lo_i = max(0, i - n + 1)
        hi[n] = finite(df.high.iloc[lo_i:i + 1].max(), close)

    def spd(d):
        return clip(40 + d * 280)  # 距高点越远，上方修复空间越大

    d60 = max(0.0, 1 - close / hi[60]) if hi[60] > 0 else 0.0
    d120 = max(0.0, 1 - close / hi[120]) if hi[120] > 0 else 0.0
    d250 = max(0.0, 1 - close / hi[250]) if hi[250] > 0 else 0.0
    near_high = close / hi[60] if hi[60] > 0 else 1.0  # 距60日高点越近=突破临近
    trs = []
    for j in range(max(1, i - 19), i + 1):
        h = finite(df.high.iloc[j]); l = finite(df.low.iloc[j]); c = finite(df.close.iloc[j - 1], h)
        trs.append(h - l)
    atr_pct = (safe_mean(trs) / close) if trs and close else 0.0
    atr_s = clip(100 - abs(atr_pct * 100 - 3.0) * 12)  # 日均振幅 3% 附近最利于趋势延续
    ma20 = finite(df.iloc[i].ma_bfq_20, safe_mean(df.close.iloc[max(0, i - 19):i + 1]))
    ma60 = finite(df.iloc[i].ma_bfq_60, safe_mean(df.close.iloc[max(0, i - 59):i + 1]))
    align = (close > ma20) + (ma20 > ma60)
    if lc["extension"] < 1.5:  # CORE/MID：低位重修复空间
        s = 0.35 * spd(d250) + 0.25 * spd(d120) + 0.20 * clip(near_high * 60 + 40) + 0.10 * atr_s + 0.10 * align * 40
    else:  # EXT：高位重突破临近 + 斜率
        slope = close / finite(df.close.iloc[max(0, i - 20)], close) - 1 if i >= 20 else 0.0
        s = 0.30 * clip(near_high * 90 + 10) + 0.30 * clip(50 + slope * 400) + 0.20 * spd(d60) + 0.10 * atr_s + 0.10 * align * 40
    return clip(s)


def hvt_acceleration(df, i, mkt=None):
    """V5 趋势加速度：RS5/20/60 递进 + 斜率递进（奖励 短>中>长，即加速度>趋势本身）"""
    close = finite(df.iloc[i].close)
    if close <= 0:
        return 50.0

    def sret(n):
        j = i - n
        base = finite(df.iloc[j].close, 0) if j >= 0 else 0.0
        return close / base - 1 if base > 0 else 0.0

    s5, s20, s60 = sret(5), sret(20), sret(60)
    dates = df.trade_date.astype(str).to_numpy()
    mr5 = mr20 = mr60 = 0.0
    if mkt is not None and len(dates):
        mr5 = mkt.ret(dates[max(0, i - 5)], dates[i])
        mr20 = mkt.ret(dates[max(0, i - 20)], dates[i])
        mr60 = mkt.ret(dates[max(0, i - 60)], dates[i])
    rs5, rs20, rs60 = clip(50 + (s5 - mr5) * 400), clip(50 + (s20 - mr20) * 250), clip(50 + (s60 - mr60) * 150)
    s = 0.45 * rs5 + 0.35 * rs20 + 0.20 * rs60
    if rs5 >= rs20 and rs20 >= rs60:
        s = min(100.0, s + 10)  # RS 加速：短>中>长
    if s5 >= s20 >= 0 and s60 >= 0:
        s = min(100.0, s + 6)  # 斜率加速
    return clip(s)


def hvt_platform(df, event_idx, i):
    """V5 平台压缩：天量后至今 振幅/ATR/量能压缩 + 回撤 + 低点抬高 + 高点测试"""
    if i <= event_idx + 4:
        return 50.0
    post = df.iloc[event_idx + 1:i + 1]
    pre10 = df.iloc[max(0, event_idx - 9):event_idx + 1]
    ev_vol = finite(df.iloc[event_idx].vol, 1)
    amp_now = safe_mean((post.high - post.low) / finite(post.close, 1))
    amp_pre = safe_mean((pre10.high - pre10.low) / finite(pre10.close, 1)) if len(pre10) >= 3 else amp_now
    amp_c = clip((1 - amp_now / max(amp_pre, 1e-6)) * 100) if amp_pre > 0 else 50.0
    vol_ratio = safe_mean(post.vol.iloc[-5:]) / max(ev_vol, 1)
    vol_c = clip((1 - vol_ratio / 0.4) * 60)  # 量缩到事件量 4 成以下为佳
    dd = max(0.0, 1 - finite(post.low.min(), 0) / finite(df.iloc[event_idx].close, 1))
    dd_s = clip(100 - dd * 300)
    half = max(1, len(post) // 2)
    lo_f = finite(post.low.iloc[:half].min(), 0)
    lo_b = finite(post.low.iloc[half:].min(), lo_f)
    lift = clip((lo_b / max(lo_f, 1e-6) - 1) * 500 + 50)  # 平台低点抬高
    hi_plat = finite(post.high.max(), 0)
    near = clip(finite(post.close.iloc[-1]) / max(hi_plat, 1e-6) * 100)
    test = clip((near - 70) * 2.5)  # 贴近平台高点=多次测试未破
    return clip(0.30 * amp_c + 0.30 * vol_c + 0.20 * dd_s + 0.10 * lift + 0.10 * test)


def hvt_distribution_risk(df, i, base, lc):
    """V5 派发风险（0~100，越高越危险）：高位滞涨/放量长阴/破中枢/MA20拐头/RS回落/涨缩量跌放量/高位大换手
    V5.1：初版阈值过严几乎不命中（TOP20 全 0），已放宽各因子阈值"""
    risk = 0.0
    row = df.iloc[i]
    close = finite(row.close)
    vol20 = safe_mean(df.vol.iloc[max(0, i - 19):i])
    vr = finite(row.vol) / vol20 if vol20 > 0 else 1.0
    ext = lc["extension"]
    if ext >= 1.5:
        ret10 = close / finite(df.close.iloc[max(0, i - 10)], close) - 1
        if ret10 <= 0.02 and vr >= 1.2:  # 高位滞涨+放量
            risk += 15
    pct = finite(row.pct_chg, 0)
    if pct < -1.5 and vr >= 1.3 and pct_position(row.open, row.high, row.low, row.close) < 40:  # 放量长阴
        risk += 20
    if close < finite(base["core"], 0) * 0.99:  # 跌破天量中枢
        risk += 20
    ma20 = finite(row.ma_bfq_20, 0)
    ma20_prev = finite(df.iloc[max(0, i - 5)].ma_bfq_20, ma20)
    if ma20 > 0 and ma20_prev > 0 and ma20 < ma20_prev * 0.998:  # MA20 拐头
        risk += 14
    s5 = close / finite(df.close.iloc[max(0, i - 5)], close) - 1
    s20 = close / finite(df.close.iloc[max(0, i - 20)], close) - 1
    if s5 < s20 - 0.02:  # 短期 RS 回落
        risk += 10
    up = df.iloc[max(0, i - 9):i + 1]
    up_bars = up[up.close >= up.open]
    down_bars = up[up.close < up.open]
    if len(up_bars) >= 2 and len(down_bars) >= 2:
        if safe_mean(down_bars.vol) > safe_mean(up_bars.vol) * 1.2:  # 下跌放量、上涨缩量
            risk += 12
    if ext >= 1.5 and finite(row.turnover_rate_f, finite(row.turnover_rate, 0)) > 8:  # 高位大换手
        risk += 10
    return clip(risk)


def hvt_v3_score(base, lc, hvt_q, fs, acc, rs, fina, plat, dist_risk):
    """V5 最终评分：BaseScore=0.25*HVT+0.20*Absorption+0.15*Lifecycle+0.15*FutureSpace+0.10*Acceleration+0.05*RS+0.10*Fundamental
    Score = BaseScore - DistributionRiskPenalty（独立惩罚，非线性）"""
    absorption = clip(0.5 * base["lock"] + 0.3 * base["acceptance"] + 0.2 * plat)
    base_score = clip(0.25 * hvt_q + 0.20 * absorption + 0.15 * lifecycle_score(lc)
                      + 0.15 * fs + 0.10 * acc + 0.05 * rs + 0.10 * fina)
    penalty = clip(dist_risk * 0.6, 0, 45)
    return clip(base_score - penalty), base_score, absorption, penalty


def rank_score_v5(score, lc, dist_risk, drawdown):
    """V5 排名分（收益风险比）：Score / (1 + 扩张风险 + 派发风险 + 回撤风险)"""
    ext_risk = clip((lc["extension"] - 2.0) * 30) if lc["extension"] > 2.0 else 0.0
    dd_risk = clip(drawdown * 100 * 0.5)
    return clip(score / (1.0 + (ext_risk + dist_risk + dd_risk) / 100.0))


def hvt_type(state, lc, dist_risk):
    """V5 生命周期类型：DISTRIBUTION（派发）/ CORE（低位）/ MID（中段）/ EXT（高位延续）"""
    if state == "RIGHT_BOTTOM":
        # 20261002：右底低吸是「深回调后的双底」，dist_risk（天量后量价派发）不适用于该形态，
        # 否则整类被标为 DISTRIBUTION → legacy_watch 剔除 / 四周期预期全写「回避」。改按生命周期涨幅定档。
        if lc["extension"] < 0.80:
            return "CORE"
        if lc["extension"] < 1.50:
            return "MID"
        return "EXT"
    if dist_risk >= 60 or state in ("DISTRIBUTION", "FAILED"):
        return "DISTRIBUTION"
    if lc["extension"] < 0.80:
        return "CORE"
    if lc["extension"] < 1.50:
        return "MID"
    return "EXT"


def horizon_phases(tp, lc, breakout, t0_confirm=False, retest=False, midhold=False, right_bottom=False):
    """V5 四周期预期阶段文本（T+10/20/60/120）"""
    if tp == "DISTRIBUTION":
        return {"t10": "回避，观察派发确认", "t20": "回避", "t60": "回避", "t120": "回避"}
    lv = lc["level"]
    if t0_confirm:
        t10, t20 = "确认日买点：T0收盘价上方站稳≥2日", "回踩不破T0收盘价持有，收盘跌破离场"
    elif retest:
        t10, t20 = "回踩突破位（放量突破日收盘）缩量企稳即低吸", "跌破 MA20 或放量突破日低点离场"
    elif midhold:
        t10, t20 = "回踩长阳半分位缩量企稳即低吸，站上长阳上沿=转突破", "收盘跌破长阳半分位离场"
    elif right_bottom:
        t10, t20 = "右底缩量低吸，放量站上颈线（中间高点）=转突破", "收盘跌破左底（前低）离场"
    elif breakout:
        t10, t20 = "突破放量确认", "趋势启动，回踩不破MA10持有"
    else:
        t10, t20 = "平台内蓄势，等放量突破", "突破进趋势，不破继续观察"
    if lv in ("L1", "L2"):
        t60, t120 = "中期趋势扩张，空间大", "右尾概率高，跟踪主升"
    elif lv == "L3":
        t60, t120 = "中段换手，斜率决定高度", "需持续加速才有右尾"
    else:
        t60, t120 = "高位延续，破MA20离场", "二次加速机会，严守风险"
    return {"t10": t10, "t20": t20, "t60": t60, "t120": t120}


def t120_alpha_score(dims):
    return clip(0.25 * dims["hvt"] + 0.20 * dims["trend"] + 0.20 * dims["fina"] + 0.15 * dims["rs"] + 0.10 * dims["upside"] + 0.10 * dims["sector"])


def entry_score_v2(df, i, pp, pp_ok, reexp, breakout, event_low, mkt):
    # V4.1 ENTRY_SCORE：独立买点评分。Close_Position/量能只降分不淘汰（Distribution 由组合判定处理）
    row = df.iloc[i]
    close = finite(row.close)
    cp = pct_position(row.open, row.high, row.low, row.close) / 100.0
    vol20 = safe_mean(df.vol.iloc[max(0, i - 19):i])
    vr = finite(row.vol) / vol20 if vol20 > 0 else 1.0
    s_pp = pp if pp_ok else clip(pp * 0.6)  # PP10 是买点指标，未成立只降分
    s_cp = clip(cp * 115)  # CP<0.75 降分不删除
    if breakout:
        s_bo = 92.0
    elif reexp:
        s_bo = 80.0
    elif pp_ok:
        s_bo = 70.0
    else:
        s_bo = 40.0
    if 0.9 <= vr <= 2.5:
        s_v = 85.0  # 温和放量最佳
    elif 0.6 <= vr < 0.9:
        s_v = 60.0
    elif 2.5 < vr <= 3.5:
        s_v = 55.0
    elif vr > 3.5:
        s_v = 30.0  # 巨量警惕
    else:
        s_v = 40.0
    ma20 = finite(row.ma_bfq_20, safe_mean(df.close.iloc[max(0, i - 19):i + 1]))
    d20 = close / ma20 - 1 if ma20 > 0 else 0.0
    if -0.03 <= d20 <= 0.08:
        s_pb = 85.0  # 贴近 MA20 上方：回踩不破最佳
    elif 0.08 < d20 <= 0.15:
        s_pb = 65.0
    elif d20 > 0.15:
        s_pb = 40.0
    elif -0.08 <= d20 < -0.03:
        s_pb = 60.0
    else:
        s_pb = 30.0
    dates = df.trade_date.astype(str).to_numpy()
    sr5 = close / finite(df.iloc[i - 5].close, close) - 1 if i >= 5 and finite(df.iloc[i - 5].close, 0) > 0 else 0.0
    mr5 = mkt.ret(dates[max(0, i - 5)], dates[i]) if (mkt is not None and len(dates)) else 0.0
    s_rs = clip(50 + (sr5 - mr5) * 400)  # 短期 RS：5 日超额
    support = max(ma20, finite(event_low, close) * 0.97)
    downside = max((close - support) / close, 0.0) if close > 0 else 0.02
    rrr = 0.08 / max(downside, 0.02)  # 波段目标 +8% 对比止损距离
    s_rr = clip(30 + rrr * 35)
    total = clip(0.30 * s_pp + 0.15 * s_cp + 0.20 * s_bo + 0.10 * s_v + 0.10 * s_pb + 0.10 * s_rs + 0.05 * s_rr)
    dims = {"pp": s_pp, "cp": s_cp, "breakout": s_bo, "volume": s_v, "pullback": s_pb, "rs": s_rs, "rrr": s_rr}
    return total, dims


ENTRY_KEYS = {"pp": "PP10", "cp": "收盘位置", "breakout": "突破", "volume": "量能", "pullback": "回踩", "rs": "短期RS", "rrr": "风险收益比"}
STATUS_ORDER = ["PRIMARY_BUY", "T120_ROCKET", "CONFIRMED", "WATCH"]


def next_trigger(status, entry_dims, breakout, pp_ok):
    # 下一触发条件：状态升级所需的最小条件组合
    if status == "PRIMARY_BUY":
        return "买点已确认；跌破MA20/事件低点离场"
    if status == "T120_ROCKET":
        parts = []
        if not breakout:
            parts.append("放量突破平台")
        if not pp_ok:
            parts.append("PP10成立")
        if entry_dims["cp"] < 70:
            parts.append("收盘站上当日上1/3")
        if entry_dims["volume"] < 55:
            parts.append("温和放量1~2.5x")
        return "等待：" + "、".join(parts[:2]) if parts else "等待ENTRY回升≥80"
    if status == "CONFIRMED":
        return "等待T120≥85且ENTRY≥80升级PRIMARY"
    return "观察吸收/趋势修复，暂不参与"


def find_event_anchor(df):
    """扫描最近事件窗口内的天量主事件并归并锚定 T0(等价 analyze 内的锚定逻辑)。

    T0 锚只由历史数据决定(候选区间不含当日/昨日两根),盘中重复调用昂贵但单日结果固定,
    供实时盘中预检「一次扫描、全天复用」。返回 (event_idx, event_percentile) 或 None。
    """
    if len(df) < MIN_BARS:
        return None
    candidates = []
    for i in range(max(MIN_BARS, len(df) - MAX_EVENT_AGE - 1), len(df) - 2):
        ok, ep = extreme_event(df, i)
        if ok:
            candidates.append((i, ep))
    if not candidates:
        return None
    # 20260909：天量簇归并，取簇内最早涨停/极强阳日为 T0 锚（8/19），而非最近极端日(8/20)
    return extreme_cluster_anchor(df, candidates)


def analyze(code, name, industry, df, anchors, reader=None, mkt=None, sector_strength=None, sector_growth=None,
            event_hint=None, ige_adj=None):
    if len(df) < MIN_BARS:
        return None
    if event_hint is not None:
        # 盘中预检:同一交易日复用已缓存的 T0 锚,跳过昂贵的 60 日极端事件重扫
        event_idx, ep = event_hint
    else:
        anchor = find_event_anchor(df)
        if not anchor:
            return None
        event_idx, ep = anchor
    result = state_and_features(df, event_idx, ep)
    if not result:
        return None
    base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure = result
    # V5：移除 V4.1.2 涨幅剔除——高位大涨股不再简单剔除，改由生命周期分类（CORE/MID/EXT/DISTRIBUTION）处理
    last = len(df) - 1
    lc = lifecycle(df, last)
    current = dict(base)
    sim_a = similarity(current, anchors.get("中际旭创"))
    sim_b = similarity(current, anchors.get("华正新材"))
    hvt = (sim_a + sim_b) / 2
    fina_now, fina_prev = (None, None)
    if reader is not None:
        fina_now, fina_prev = reader.fina(code, as_of=str(df.iloc[last].trade_date))
    # V4.1：潜力（T120_ALPHA）与买点（ENTRY_SCORE）分离（保留参考）
    dims = {
        "hvt": alpha_hvt(base, hvt, drawdown),
        "trend": alpha_trend(df, last),
        "fina": alpha_fina(fina_now, fina_prev),
        "rs": alpha_rs(df, last, mkt),
        "upside": alpha_upside(df, last),
        "sector": alpha_sector(industry, sector_strength or {}, sector_growth or {}),
    }
    t120 = t120_alpha_score(dims)
    # V5 新增维度：未来空间 / 趋势加速度 / 平台压缩 / 派发风险
    fs = hvt_future_space(df, last, lc)
    acc = hvt_acceleration(df, last, mkt)
    plat = hvt_platform(df, event_idx, last)
    dist_risk = hvt_distribution_risk(df, last, base, lc)
    score, base_score, absorption, penalty = hvt_v3_score(base, lc, dims["hvt"], fs, acc, dims["rs"], dims["fina"], plat, dist_risk)
    rank = rank_score_v5(score, lc, dist_risk, drawdown)
    tp = hvt_type(state, lc, dist_risk)
    event_low = finite(df.low.iloc[event_idx], 0.0)
    entry, entry_dims = entry_score_v2(df, last, pp, pp_ok, reexp, breakout, event_low, mkt)
    # V5.3 定级（20260925）：score 降级为纯展示。跟踪库复核显示控制批次后 ≥60 分在 A/B/C 三批次内
    # 均劣于 <60 分、最新批次（0918~0923）高分胜率 0%，无正向区分度，故 A/B/C 定级改用实测单调的
    # 三因子（type=MID / ige_adj≥75 / volr<0.60，见 w7_priority），由 w7_assign_status 统一给出；
    # 非买点态沿用趋势/类型判据作为兜底（原 score>=70 分支的等价替代）。
    # 注意：定级只改报告 A/B/C 榜归属、reason/next 文案与落库 action 标签，
    # 选股口径仍只看 state + w7_quality_gate。
    latest_bar = df.iloc[last]
    vol20 = safe_mean(df.vol.iloc[max(0, last - 19):last])
    volr = finite(latest_bar.vol, 0.0) / vol20 if vol20 > 0 else 0.0
    fallback = "CONFIRMED" if (breakout or reexp or dims["trend"] >= 70 or tp in ("MID", "EXT")) else "WATCH"
    status = w7_assign_status(state, tp, ige_adj, volr, fallback)
    # V5：C榜低分兜底票不输出。判据沿用「旧 score 定级下的 WATCH」定义（与 V5.3 三因子定级解耦），
    # 逐条复刻改版前的剔除条件：派发态，或非买点态且趋势/类型兜底为 WATCH，或买点态 score<70。
    # 不能简化为 `tp=="DISTRIBUTION" or score<70`——那会把非买点态的低分 MID/EXT 一并剔除，收缩候选池。
    legacy_watch = ((tp == "DISTRIBUTION")
                    or (state not in ACTION_BUY_STATES and fallback == "WATCH")
                    or (state in ACTION_BUY_STATES and score < 70.0))
    if state == "RIGHT_BOTTOM":
        # 20261002：右底低吸不存在天量 T0，HVT-V3 分族（天量/锁筹/吸收/平台压缩）对本形态天然给低分
        # （301080 9/11：score 24.7 / base_score 42.7，而 trend 92、upside 100），
        # 故不适用「买点态 score<70 → 需 base_score≥62」的兜底剔除；质量由 right_bottom 九条硬条件 +
        # w7_quality_gate 保证（全市场总市值≥80亿、2026-09 全月仅 40 只 / 66 次）。
        legacy_watch = False
    if legacy_watch and base_score < WATCH_MIN_SCORE:
        return None
    event_date = str(df.iloc[event_idx].trade_date)
    core = f"Ac{base['acceptance']:.0f}/CQ{base['cq']:.0f}/SIM{hvt:.0f}/回撤{drawdown * 100:.0f}%"
    ext_txt = f"涨幅{lc['extension'] * 100:.0f}%({lc['level']})"
    v5_dims = {"天量": dims["hvt"], "吸收": absorption, "生命周期": lifecycle_score(lc), "空间": fs, "加速": acc, "RS": dims["rs"], "基本面": dims["fina"]}
    if tp == "DISTRIBUTION":
        reason = f"派发风险(dist={dist_risk:.0f})暂不参与：{core}；{ext_txt}"
    elif state == "T0_CONFIRM":
        reason = (f"T0天量确认买点（{event_date}锚，重夺T0收盘站稳≥2日，收盘未破"
                  f"{base['event_close']:.2f}）：{core}；{ext_txt}；ENTRY={entry:.0f}")
    elif state == "BREAKOUT_RETEST":
        reason = (f"放量突破后缩量回踩买点（{base.get('retest_date', '')}放量突破，回踩至其收盘"
                  f"{base.get('retest_close', 0):.2f}附近缩量不破、守MA20）：{core}；{ext_txt}；ENTRY={entry:.0f}")
    elif state == "MIDLINE_HOLD":
        reason = (f"W7-不破中位（{base.get('mid_date', '')}放量长阳收{base.get('mid_close', 0):.2f}，"
                  f"回踩缩量、收盘不破半分位{base.get('mid_line', 0):.2f}）：{core}；{ext_txt}；ENTRY={entry:.0f}")
    elif state == "RIGHT_BOTTOM":
        reason = (f"W7-右底低吸（{base.get('rb_left_date', '')}左底{base.get('rb_left_low', 0):.2f} → "
                  f"{base.get('rb_mid_date', '')}颈线{base.get('rb_mid_high', 0):.2f} → 右底"
                  f"{base.get('rb_right_low', 0):.2f}，缩量{base.get('rb_volr20', 0):.2f}×20日均量、"
                  f"不破前低）：{core}；{ext_txt}；ENTRY={entry:.0f}")
    elif status == "PRIMARY_BUY":
        reason = f"三因子全中（MID/IGE_ADJ≥{W7_IGE_STRONG:g}/量比<{W7_VOLR_STRONG:g}）且有买点：{core}；{ext_txt}；ENTRY={entry:.0f}"
    elif status == "T120_ROCKET":
        blk = min(entry_dims.items(), key=lambda kv: kv[1])
        short = f"{ENTRY_KEYS.get(blk[0], blk[0])}{blk[1]:.0f}"
        reason = f"三因子中 2/3 且有买点：{core}；{ext_txt}；ENTRY短板={short}"
    elif status == "CONFIRMED":
        wk = min(v5_dims, key=v5_dims.get)
        reason = f"定级C（因子命中≤1/3 或趋势/类型兜底）：{core}；{ext_txt}；弱维={wk}={v5_dims[wk]:.0f}"
    elif status == "WATCH" and (major_risk or state in ("FAILED", "DISTRIBUTION")):
        reason = f"重大风险（抛压/破位）暂不参与：{core}；{ext_txt}"
    else:
        wk = min(v5_dims, key=v5_dims.get)
        reason = f"潜力不足：{core}；{ext_txt}；弱维={wk}={v5_dims[wk]:.0f}"
    explanation = (f"P{int(ep)}天量@{event_date}，{ext_txt}，状态{state}，类型{tp}；"
                   f"HVT-V3={score:.0f}（天量{dims['hvt']:.0f}/吸收{absorption:.0f}/生命{lifecycle_score(lc):.0f}/空间{fs:.0f}/加速{acc:.0f}/RS{dims['rs']:.0f}/基本面{dims['fina']:.0f}），"
                   f"派发风险{dist_risk:.0f}，ENTRY={entry:.0f}，{'今日PP10成立并重新放量' if pp_ok else '尚未出现合格PP10'}。")
    rb = int(base.get("retest_bar", -1))
    retest_low = finite(df.iloc[rb].low, 0.0) if rb >= 0 else None  # V5.3：失效位候选之一
    mid_line = finite(base.get("mid_line"), 0.0) or None  # W7-不破中位：长阳半分位，失效位候选之一
    rb_left_low = finite(base.get("rb_left_low"), 0.0) or None  # W7-右底低吸：左底（前低），失效位候选之一
    return {"code": code, "name": name, "industry": industry or "未覆盖", "state": state,
            "close": finite(latest_bar.close, 0.0), "pressure": pressure,
            "ma20": finite(latest_bar.ma_bfq_20, 0.0), "volr": volr,
            "score": score, "base_score": base_score, "rank": rank, "type": tp,
            "level": lc["level"], "extension": lc["extension"], "extreme_extension": lc["extreme"],
            "t120": t120, "entry": entry, "entry_dims": entry_dims, "dims": dims, "v5_dims": v5_dims,
            "fs": fs, "acc": acc, "plat": plat, "dist_risk": dist_risk, "absorption": absorption, "penalty": penalty,
            "cq": base["cq"], "acceptance": base["acceptance"], "sds": base["sds"], "lock": base["lock"], "pp": pp,
            "hvt": hvt, "sim_zjxc": sim_a, "sim_hzxc": sim_b, "buy": status, "grade": grade(t120),
            "event_date": event_date, "event_percentile": ep, "event_open": base.get("event_open"), "reexpansion": reexp, "breakout": breakout,
            "retest_low": retest_low, "mid_line": mid_line, "rb_left_low": rb_left_low,
            # 20261002：右底低吸单列节所需的结构点（右底当日最低 / 双底颈线 / 左底日期）
            "rb_right_low": finite(base.get("rb_right_low"), 0.0) or None,
            "rb_mid_high": finite(base.get("rb_mid_high"), 0.0) or None,
            "rb_left_date": base.get("rb_left_date") or "",
            "major_risk": major_risk, "hard_fail": major_risk, "reason": reason,
            "next": next_trigger(status, entry_dims, breakout, pp_ok), "explanation": explanation,
            "horizons": horizon_phases(tp, lc, breakout, t0_confirm=(state == "T0_CONFIRM"),
                                       retest=(state == "BREAKOUT_RETEST"),
                                       midhold=(state == "MIDLINE_HOLD"),
                                       right_bottom=(state == "RIGHT_BOTTOM"))}


def w7_quality_gate(x):
    """V5.2 质量过滤（20260930 修订：①状态排除 + ④信号日收盘须高于天量标志日开盘价；
    原②CORE、③选股日量比已废止）。返回 (是否通过, 未通过原因)。
    仅对 ACTION_BUY_STATES 内的标的做二次筛选，依据见文件头 W7_EXCLUDE_STATES 注释。
    20261002：RIGHT_BOTTOM（右底低吸）豁免④——该形态是深度回调后的双底，信号日收盘本就
    可能低于天量标志日开盘价（301080 9/11 收 78.70 < 8/20 天量日开 92.00），
    结构质量由 right_bottom 的九条硬条件保证。"""
    if x.get("state") in W7_EXCLUDE_STATES:
        return False, f"状态{x['state']}历史弱（库内 T+3 -0.29%~+1.26%）"
    if x.get("state") == "RIGHT_BOTTOM":
        return True, ""
    # 20260930 用户口径：信号日收盘必须大于天量标志日开盘价（价格须站回天量事件起点上方）
    ev_open = x.get("event_open")
    if isinstance(ev_open, (int, float)) and ev_open > 0 and finite(x.get("close"), 0.0) <= ev_open:
        return False, (f"信号日收盘{x['close']:.2f}≤天量标志日{x.get('event_date', '')}"
                       f"开盘{ev_open:.2f}（未站回天量事件起点上方）")
    return True, ""


def v52_sort_key(x):
    """V5.3 可操作榜排序键（20260925 起）：因子优先级降序 → 「生命周期 + 空间」降序 →
    选股日量比升序 → IGE_ADJ 降序。报告「今日可操作榜」与 stock_pick_db 落库共用此键，
    保证展示序与 rank_no 同口径。
    历史：原键（20260923 起）为「生命周期+空间 → 量比 → IGE_ADJ」，不含优先级；
    该二项在 20260922 复核中与后续收益正相关（生命周期 pearson +0.50、空间 +0.39）。
    本次在其上叠加 W7_IGE_STRONG / W7_VOLR_STRONG 注释所述三因子命中数（w7_priority），
    score 退出排序（控制批次后无正向区分度）。"""
    lead = (finite((x.get("v5_dims") or {}).get("生命周期"), 0.0)
            + finite((x.get("v5_dims") or {}).get("空间"), 0.0))
    ige = x.get("ige_adj") if isinstance(x.get("ige_adj"), (int, float)) else -1.0
    return (-w7_priority(x), -lead, finite(x.get("volr"), 0.0), -ige)


def markdown(results, date, theme_meta=None, rb_extra=None):
    # V5.1 过滤层：展示/推送不再铺开候选池，只收口到「当日买点」标的 + 高分等待池摘要
    # V5：HVT-V3 三榜单（A/CORE、B/EXT、C/WATCH）+ TOP20 总榜 + 行为解释含四周期预期
    # theme_meta：热点主题扩池新增跟踪节元数据 {codes:set, info:{code:[(主题,热度)]}, label:str}
    # rb_extra：20261002 龙头池外的「右底低吸」信号（单独成节 + 落跟踪池）
    results = sorted(results, key=lambda x: (-x["rank"], x["code"]))
    ige_snap = next((str(r.get("ige_snap") or "") for r in results if r.get("ige_snap")), "")

    # 当日买点=已突破/确认七态（状态机在“当日有效放量突破触发价”当天即标为
    # SECOND_WAVE/BREAKOUT_CONFIRM/RE_EXPANSION，T0_CONFIRM=确认日当日买点，
    # BREAKOUT_RETEST=放量突破后缩量回踩买点当日，MIDLINE_HOLD=W7-不破中位买点当日，
    # RIGHT_BOTTOM=W7-右底低吸买点当日）。
    # 不再做 close>pressure&volr≥1.2 的宽松兜底——实测会把派发/失败/巨量追高票混入。
    ACTION_STATES_5 = ACTION_BUY_STATES

    def _today_action(x):
        return x["state"] in ACTION_STATES_5

    # V5.2 质量过滤：当日买点先过 w7_quality_gate，未通过者降级为「过滤观察」（仅展示、不进跟踪表）
    _buy_signal = [x for x in results if _today_action(x)]
    _gate = {id(x): w7_quality_gate(x) for x in _buy_signal}
    actionable = [x for x in _buy_signal if _gate[id(x)][0]]
    gated_out = [(x, _gate[id(x)][1]) for x in _buy_signal if not _gate[id(x)][0]]
    waiting = [x for x in results if not _today_action(x)]
    n_action = len(actionable)
    n_gated = len(gated_out)
    # 20261002 用户口径：右底低吸（RIGHT_BOTTOM）单列——不分龙头池内/池外统一进独立节；
    # 三状态主体（EXECUTION/TRIGGER/PULLBACK）不再重复列示，避免同一票出现两次。
    rb_show = [x for x in actionable if x["state"] == "RIGHT_BOTTOM"] + list(rb_extra or [])
    actionable_main = [x for x in actionable if x["state"] != "RIGHT_BOTTOM"]
    n_rb_show = len(rb_show)
    n_rb_off = len(rb_extra or [])  # 龙头池外的右底低吸（单列④，不计入上方 n_action）
    # 高分等待池阈值（C 池仅列总分≥75，避免整池铺开）
    WAIT_TOP_SCORE = 75.0

    def _ige_tag(r):
        return f"{r['ige_adj']:.1f}" if isinstance(r.get("ige_adj"), (int, float)) else "-"
    n_core = sum(1 for x in results if x["type"] == "CORE")
    n_mid = sum(1 for x in results if x["type"] == "MID")
    n_ext = sum(1 for x in results if x["type"] == "EXT")
    n_dist = sum(1 for x in results if x["type"] == "DISTRIBUTION")
    lines = [f"# W7 HVT-V3 过滤后榜单（今日可操作 · C池等待）\n\n交易日：{date}　|　候选总数：{len(results)}"]
    lines.append(f"类型分布：CORE={n_core}　MID={n_mid}　EXT={n_ext}　DISTRIBUTION={n_dist}（DISTRIBUTION=派发风险，仅观察不进 A/B 榜）")
    lines.append(f"今日可操作（当日买点）＝ {n_action + n_rb_off} 只：二波/突破确认/重新扩张/T0天量确认/放量突破后缩量回踩/W7-不破中位/W7-右底低吸"
                 + (f"（其中右底低吸 龙头池外 {n_rb_off} 只，见本节④）" if n_rb_off else "")
                 + f"；其余 {len(waiting)} 只等待型仅入 C 池观察不逐列展示。")
    lines.append(f"V5.2 质量过滤（仅龙头池内候选）：当日买点原始 {len(_buy_signal)} 只 → 通过 {n_action} 只、过滤 {n_gated} 只"
                 + (f"；另龙头池外右底低吸 {n_rb_off} 只豁免准入、直接进本节④" if n_rb_off else "")
                 + f"（剔除 BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION 三态；要求信号日收盘＞天量标志日开盘价"
                 f"（RIGHT_BOTTOM 右底低吸豁免此条，结构质量由 right_bottom 九条硬条件保证）；"
                 f"原「CORE 低位型涨幅<80%」与「选股日量比>0.66」两条已于 20260930 废止——"
                 f"按当日产出榜复检二者均无正贡献。被过滤标的见文末「过滤观察」。）")
    cnt_state = {}
    for x in results:
        cnt_state[x["state"]] = cnt_state.get(x["state"], 0) + 1
    n_broken = sum(cnt_state.get(s, 0) for s in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION", "T0_CONFIRM", "BREAKOUT_RETEST", "MIDLINE_HOLD", "RIGHT_BOTTOM"))
    lines.append(f"状态分布：{'　'.join(f'{s}={c}' for s, c in sorted(cnt_state.items()))}　"
                 f"（已突破/确认类=BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION/T0_CONFIRM/BREAKOUT_RETEST/MIDLINE_HOLD/RIGHT_BOTTOM 合计 {n_broken} 家，"
                 f"T0_CONFIRM=天量T0确认买点当日，BREAKOUT_RETEST=放量突破后缩量回踩买点当日，MIDLINE_HOLD=放量长阳（须创60日新高）后缩量回踩不破半分位当日，"
                 f"RIGHT_BOTTOM=前波大涨后回落形成双底、右底缩量不破左底当日）")
    lines.append("价格口径：现价/触发价/MA20均为元；触发价=事件日后10日平台高点（BREAKOUT_RETEST 回踩买点=放量突破日收盘=回踩位，MIDLINE_HOLD=放量长阳日最高价，RIGHT_BOTTOM=双底颈线即中间高点），放量(量比≥1.2)突破触发价=买点触发；失效位(止损)见下「失效位（V5.3 修订）」；MA20=总防线；量比=当日量/前20日均量（不含当日）")
    lines.append("")
    if ige_snap:
        lines.append(f"> 行业增长弹性 IGE_ADJ（申万三级行业，快照 {ige_snap}）：全部榜单已附 IGE_ADJ 列；「W7 二波·今日执行状态」按 W7_STATUS 优先、同级内按各状态自身口径（EXECUTION=量能确认程度／TRIGGER_WATCH=距触发最近／PULLBACK_WATCH=结构完整度）重排（20261001 起，执行状态优先于分数）；RIGHT_BOTTOM（右底低吸）不以距触发分档、直接归执行区并置 EXECUTION 组内最前（20261002 起）；其余榜单保留 HVT-V3 总分/Rank 原序仅加列标注。")
        lines.append("")
    # ===== W7 输出层三状态执行分层（V1.0，20261001）=====
    # 候选集合不变（actionable = 七态当日买点 ∩ V5.2 质量过滤），只把展示与执行状态按
    # 「实际交易距离 + 触发条件」重排；结构失效 INVALID 移出主体、本节末尾单列。
    exec_rows, invalid_rows = [], []
    for x in actionable_main:
        _st, _fl, _fn = w7_exec_status(x)
        x["w7_status"], x["w7_fail_line"], x["w7_fail_name"] = _st, _fl, _fn
        (invalid_rows if _st == "INVALID" else exec_rows).append(x)
    exec_rows.sort(key=w7_exec_sort_key)
    exec_seq = [x for x in exec_rows if x["w7_status"] in ("EXECUTION", "EXECUTION_WAIT_VOLUME")]
    trig_rows = [x for x in exec_rows if x["w7_status"] == "TRIGGER_WATCH"]
    pull_rows = [x for x in exec_rows if x["w7_status"] == "PULLBACK_WATCH"]
    n_exec = sum(1 for x in exec_seq if x["w7_status"] == "EXECUTION")
    n_exec_all = len(exec_seq)

    def _fail_txt(x):
        return (f"收盘跌破{x['w7_fail_name']}{x['w7_fail_line']:.2f}"
                if x["w7_fail_line"] > 0 else "结构位缺失（以 MA20 为总防线）")

    def _blk(k, x):
        """单只标的的执行状态块（字段与措辞按 V1.0 规格固定，评分不得改变状态结论）。"""
        st, dist = x["w7_status"], w7_distance_to_trigger(x)
        out = [f"{k}. {x['name']}（{x['code']}）"]
        if st == "EXECUTION_WAIT_VOLUME":
            out.append("状态：EXECUTION_WAIT_VOLUME（已进入价格执行区，量能未确认）")
        out.append(f"类型：{x['type']}")
        out.append(f"现价：{x['close']:.2f}")
        out.append(f"触发价：{x['pressure']:.2f}")
        if st in ("TRIGGER_WATCH", "PULLBACK_WATCH"):
            out.append(f"距触发：{dist * 100:.2f}%")
        out.append(f"MA20：{x['ma20']:.2f}")
        out.append(f"量比：×{x['volr']:.1f}")
        out.append(f"IGE_ADJ：{_ige_tag(x)}")
        out.append(f"W7总分：{x['score']:.1f}")
        out.append("")
        if st == "EXECUTION" and x.get("state") == "RIGHT_BOTTOM":
            # 20261002：右底低吸的买点＝当日右底（现价即买点区），不走「已站上触发价」口径
            out += ["执行条件：",
                    f"- 右底低吸：现价{x['close']:.2f} 位于右底区（左底/前低 {x['w7_fail_line']:.2f} 上方）",
                    f"- 缩量特征：量比×{x['volr']:.1f}（右底须缩量，不适用放量阀门）",
                    "- 结构未破坏（收盘未跌破左底/前低）", "",
                    "操作：",
                    f"右底低吸买点，可按计划分批建仓；放量（量比≥{W7_VOL_CONFIRM:g}）站上双底颈线 "
                    f"{x['pressure']:.2f}＝转突破/二波，可加仓", "",
                    "失效：", _fail_txt(x)]
        elif st == "EXECUTION":
            out += ["执行条件：",
                    f"- 已站上触发价（现价{x['close']:.2f} ≥ 触发价{x['pressure']:.2f}）",
                    "- 结构未破坏",
                    f"- 量能：已确认（量比×{x['volr']:.1f} ≥ {W7_VOL_CONFIRM:g}）", "",
                    "操作：", "可执行（沿用原仓位规则）；收盘跌回触发价下方离场", "",
                    "失效：", _fail_txt(x)]
        elif st == "EXECUTION_WAIT_VOLUME":
            out += ["执行条件：",
                    f"- 已站上触发价（现价{x['close']:.2f} ≥ 触发价{x['pressure']:.2f}）",
                    "- 结构未破坏",
                    f"- 量能：未确认（量比×{x['volr']:.1f} < {W7_VOL_CONFIRM:g}）", "",
                    "操作：",
                    f"已进入价格执行区，但量能尚未确认——不得视为「已确认买入」；"
                    f"待量比 ≥{W7_VOL_CONFIRM:g} 再执行，或持有等待量能确认", "",
                    "失效：", _fail_txt(x)]
        elif st == "TRIGGER_WATCH":
            out += ["等待条件：", f"放量站上{x['pressure']:.2f}",
                    f"+ 量比 ≥{W7_VOL_CONFIRM:g}（原策略量能阀门）", "+ 结构未破坏", "",
                    "当前：", f"尚未触发（距触发 {dist * 100:.2f}%），不追价。", "",
                    "失效：", _fail_txt(x)]
        else:
            out += ["当前结构：", "仍处于回踩/未突破状态。", "",
                    "等待：", "重新站上关键位", f"+ 满足原量能条件（量比 ≥{W7_VOL_CONFIRM:g}）", "",
                    "当前：", f"观察，不主动交易（距触发 {dist * 100:.2f}%）。", "",
                    "失效：", _fail_txt(x)]
        return out

    lines.append(f"\n## 【W7 二波·今日执行状态】（当日买点 共{n_action}只 · 执行状态优先于综合评分）\n")
    lines.append("W7状态汇总：")
    lines.append(f"- EXECUTION：{n_exec_all}只"
                 + (f"（量能已确认 {n_exec} 只 / 量能待确认 {n_exec_all - n_exec} 只）" if n_exec_all else ""))
    lines.append(f"- TRIGGER_WATCH：{len(trig_rows)}只")
    lines.append(f"- PULLBACK_WATCH：{len(pull_rows)}只")
    if n_rb_show:
        lines.append(f"- 右底低吸（单列，见本节④）：{n_rb_show}只")
    if invalid_rows:
        lines.append(f"- INVALID（结构失效，已移出主体）：{len(invalid_rows)}只")
    lines.append("")
    lines.append(f"今日真正进入执行区：{n_exec_all}只　｜　今日等待触发：{len(trig_rows)}只　｜　今日等待回踩/确认：{len(pull_rows)}只")
    lines.append(f"> 真正可以执行的股票 ≠ W7候选总数（{n_action} 只）：仅 EXECUTION 已进入价格执行区；"
                 "TRIGGER_WATCH 只差一个放量确认；PULLBACK_WATCH 仍等待回踩/重新确认。")
    lines.append("> 排序口径：W7_STATUS 优先，同级内 EXECUTION 按量能确认程度、TRIGGER_WATCH 按距触发最近、"
                 "PULLBACK_WATCH 按结构完整度，其后统一 IGE_ADJ ＞ W7总分——W7 是交易执行模块，"
                 "执行状态优先于分数，总分不得覆盖执行状态。")
    lines.append("")
    if n_exec_all == 0:
        lines.append("**今日 W7 无 EXECUTION 标的。**")
        lines.append("")
        lines.append("结论：不强行交易。")
        lines.append("")
        lines.append(f"TRIGGER_WATCH：{len(trig_rows)}只")
        lines.append("")
        lines.append(f"PULLBACK_WATCH：{len(pull_rows)}只")
        lines.append("")
    if exec_seq:
        lines.append(f"### ① EXECUTION｜已进入执行区（{n_exec_all}只）\n")
        for k, x in enumerate(exec_seq, 1):
            lines += _blk(k, x)
            lines.append("")
    if trig_rows:
        lines.append(f"### ② TRIGGER_WATCH｜等待触发（{len(trig_rows)}只）\n")
        for k, x in enumerate(trig_rows, 1):
            lines += _blk(k, x)
            lines.append("")
    if pull_rows:
        lines.append(f"### ③ PULLBACK_WATCH｜等待回踩/重新确认（{len(pull_rows)}只）\n")
        for k, x in enumerate(pull_rows, 1):
            lines += _blk(k, x)
            lines.append("")
    if not actionable:
        lines.append("_（今日无当日买点标的，空仓等待 C 池高分票放量突破）_")
        lines.append("")
    if invalid_rows:
        lines.append(f"### 已失效（{len(invalid_rows)}只 · 移出今日可操作主体、不参与排名）\n")
        for x in sorted(invalid_rows, key=lambda y: -y["score"]):
            lines.append(f"- 已失效：{x['name']}（{x['code']}）　原因：{_fail_txt(x)}（结构防线）　现价 {x['close']:.2f}")
        lines.append("")
    # ④ 右底低吸信号（20261002 用户口径：不分龙头池内/池外，单列 + 同步跟踪池）
    if rb_show:
        def _px(v):
            return f"{v:.2f}" if isinstance(v, (int, float)) else "-"
        lines.append(f"### ④ 右底低吸信号｜W7-RIGHT_BOTTOM（单列 · 不受龙头池限制，{n_rb_show}只）\n")
        lines.append("说明：前波大涨 → 回落 → 双底；右底缩量（≤0.75×20日均量）、不破左底（前低）当日即低吸买点，"
                     "收盘 ≤MA10 且在 MA60 上方。含「不在 sli 细分龙头池 / 热点主题扩池」的标的，"
                     "本节独立列示并同步跟踪池（不受 V4.4 准入限制）。\n")
        lines.append("| # | 代码 | 名称 | 现价 | 左底(前低) | 颈线 | 右底/左底 | 量比 | 总分 | 类型 | 来源 |")
        lines.append("| -- | -- | -- | --: | --: | --: | --: | --: | --: | -- | -- |")
        for k, x in enumerate(sorted(rb_show, key=lambda y: -finite(y.get("score"), 0.0)), 1):
            lo, hi, rl = x.get("rb_left_low"), x.get("rb_mid_high"), x.get("rb_right_low")
            ratio = (rl / lo) if (isinstance(lo, (int, float)) and lo and isinstance(rl, (int, float))) else None
            lines.append(f"| {k} | {x['code']} | {x['name']} | {x['close']:.2f} | {_px(lo)} | {_px(hi)} "
                         f"| {('%.3f' % ratio) if ratio else '-'} | ×{x['volr']:.1f} | {x['score']:.1f} "
                         f"| {x['type']} | {'龙头池外' if x.get('rb_offpool') else '候选池内'} |")
        lines.append("")
        lines.append("操作：右底低吸买点，可按计划分批建仓；放量（量比≥1.2）站上颈线＝转突破/二波，可加仓。"
                     "失效：收盘跌破左底（前低）离场。")
        lines.append("")
    if actionable:
        lines.append(f"优先=V5.3 因子命中数（0~3）：type=MID（+13.6%/胜74% vs EXT +5.1%/62%，n=27）"
                     f"｜IGE_ADJ≥{W7_IGE_STRONG:g}（75-85 档 +21.9%/胜93%，n=15）"
                     f"｜量比<{W7_VOLR_STRONG:g}（0.4-0.6 档 +10.7%/胜69% vs 0.6-0.8 档 +4.6%/60%，n=42）。"
                     "「W7总分」（HVT-V3）仅表示候选质量，不参与执行状态判定与排序。")
        lines.append("")
        lines.append("操作口径：触发价=原策略买点触发位；放量（量比≥1.2）站上触发价=买点触发；量比≥3 的巨量日不追、只等回踩。"
                     "BREAKOUT_RETEST=放量突破后缩量回踩买点（触发价=放量突破日收盘=回踩位）；"
                     "MIDLINE_HOLD=W7-不破中位（触发价=放量长阳日最高价，防线=长阳半分位）；"
                     "RIGHT_BOTTOM=W7-右底低吸（触发价=双底颈线即中间高点，防线=左底/前低；结构为前波大涨后回落形成的双底，右底缩量不破左底）。"
                     "执行失效位按各形态自身口径：MIDLINE_HOLD=收盘跌破长阳半分位、BREAKOUT_RETEST=跌破回踩低点、RIGHT_BOTTOM=跌破左底（前低）、"
                     f"其余=跌破 MA20（留 {W7_MA20_BREAK_TOL * 100:.0f}% 容差，未超容差归 PULLBACK_WATCH）；"
                     f"跟踪表止损位（w7_stop_price）仍按 结构位取最宽者 + 限定距现价 {W7_STOP_MIN_PCT * 100:.0f}%~{W7_STOP_MAX_PCT * 100:.0f}% 计算，两者用途不同、互不替代。")
    # 池内形态分布（替代原 A/B/MID 大列表，只给分布不给明细）
    act_by_type, wait_by_type = {}, {}
    for x in actionable:
        act_by_type[x["type"]] = act_by_type.get(x["type"], 0) + 1
    for x in waiting:
        wait_by_type[x["type"]] = wait_by_type.get(x["type"], 0) + 1
    lines.append("\n形态分布（候选池覆盖口径，仅供了解，不逐列展示）：")
    lines.append(f"- 今日买点：{'　'.join(f'{t}={c}' for t, c in sorted(act_by_type.items())) or '无'}")
    lines.append(f"- 等待型：{'　'.join(f'{t}={c}' for t, c in sorted(wait_by_type.items()))}（CORE/MID/EXT 为原 A/B/MID 池等待票，DISTRIBUTION 不参与）")
    # 热点主题新增跟踪（独立成节）：列出经 V2.4 热点主题扩池纳入（不在 sli 龙头池）的候选
    if theme_meta and theme_meta.get("codes"):
        added = theme_meta["codes"]
        info = theme_meta.get("info") or {}
        cap = W7_THEME_WATCH_CAP
        tw = [x for x in results if x["code"] in added]
        # 当日买点优先，其次按总分降序
        tw.sort(key=lambda y: (0 if y["state"] in ACTION_BUY_STATES else 1, -y["score"]))
        lines.append(f"\n## 热点主题新增跟踪（V2.4 {theme_meta.get('label', '')} 扩池纳入，{len(tw)}只）\n")
        if tw:
            lines.append("说明：以下标的经「V2.4 热点主题扩池」纳入候选池（不在 sli_v2 细分龙头池），属主题共振型机会；"
                         "本节独立列示，不占用其他榜单展示上限。")
            lines.append("")
            lines.append("| # | 代码 | 名称 | 来源热点主题(热度) | 总分 | 类型 | 现价 | 触发价 | MA20 | 量比 | 状态 |")
            lines.append("| -- | -- | -- | -- | --: | -- | --: | --: | --: | --: | -- |")
            for k, x in enumerate(tw[:cap], 1):
                ths = info.get(x["code"]) or []
                th_str = "、".join(f"{t}({h:.0f})" for t, h in ths) or "-"
                lines.append(f"| {k} | {x['code']} | {x['name']} | {th_str} | {x['score']:.1f} | {x['type']} "
                             f"| {x['close']:.2f} | {x['pressure']:.2f} | {x['ma20']:.2f} | ×{x['volr']:.1f} | {x['state']} |")
            if len(tw) > cap:
                lines.append(f"\n> 其余 {len(tw) - cap} 只已省略；本类标的为扩池纳入，盘中以实时监控为准。")
        else:
            lines.append("_（当期无经热点主题扩池纳入的候选）_")
    # C池 高分等待突破（次日埋伏，触发价=突破触发位）
    wait_top = [x for x in waiting if x["score"] >= WAIT_TOP_SCORE and x["type"] != "DISTRIBUTION"]
    lines.append(f"\n## C池 高分等待突破（总分≥{WAIT_TOP_SCORE:.0f} 共{len(wait_top)}只 · 等量比≥1.2放量突破触发价再买 · 列表前12）\n")
    if wait_top:
        lines.append("| # | 代码 | 名称 | IGE_ADJ | 总分 | 类型 | 现价 | 触发价 | MA20 | 量比 | 状态 |")
        lines.append("| -- | -- | -- | --: | --: | -- | --: | --: | --: | --: | -- |")
        for k, x in enumerate(sorted(wait_top, key=lambda y: (-y["score"], y["code"]))[:12], 1):
            lines.append(f"| {k} | {x['code']} | {x['name']} | {_ige_tag(x)} | {x['score']:.1f} | {x['type']} "
                         f"| {x['close']:.2f} | {x['pressure']:.2f} | {x['ma20']:.2f} | ×{x['volr']:.1f} | {x['state']} |")
        if len(wait_top) > 12:
            lines.append(f"\n> 其余 {len(wait_top) - 12} 只已省略；盘中实时突破以实时监控为准。")
        lines.append("\n高分等待重点说明（前6，供次日盯突破）：")
        for x in sorted(wait_top, key=lambda y: (-y["score"], y["code"]))[:6]:
            lines.append(f"- **{x['name']}({x['code']})** 总分{x['score']:.1f}｜{x['state']}｜{x['reason']}")
    else:
        lines.append("_（今日无总分≥75 的等待型）_")
    # 派发风险摘要（仅列前6，规避）
    dist_top = [x for x in waiting if x["type"] == "DISTRIBUTION"]
    if dist_top:
        lines.append("\n## 派发风险回避（DISTRIBUTION 不参与 · 仅列前6）\n")
        for x in sorted(dist_top, key=lambda y: -y["score"])[:6]:
            lines.append(f"- **{x['name']}({x['code']})** 总分{x['score']:.1f}｜{x['state']}｜{x['reason']}")
    # 行为解释 + 四周期预期（只解释当日买点，等待票见上方 C 池说明，控制篇幅）
    lines.append("\n## 行为解释与 T+10/20/60/120 预期\n")
    for x in actionable:
        h = x["horizons"]
        lines.append(f"- **{x['name']}({x['code']})** [{x['type']}/{x['level']}]：{x['explanation']}")
        lines.append(f"　T+10={h['t10']}　|　T+20={h['t20']}　|　T+60={h['t60']}　|　T+120={h['t120']}")
    # V5.2 过滤观察：当日买点被质量过滤拦下的标的（不进「今日可操作榜」、不落跟踪表）
    if gated_out:
        lines.append(f"\n## 过滤观察（当日买点被拦下 {n_gated} 只 · 不可买入、不进跟踪表）\n")
        lines.append("| # | 代码 | 名称 | 总分 | 类型 | 现价 | 触发价 | 量比 | 状态 | 过滤原因 |")
        lines.append("| -- | -- | -- | --: | -- | --: | --: | --: | -- | -- |")
        for k, (x, why) in enumerate(sorted(gated_out, key=lambda t: -t[0]["score"])[:10], 1):
            lines.append(f"| {k} | {x['code']} | {x['name']} | {x['score']:.1f} | {x['type']} "
                         f"| {x['close']:.2f} | {x['pressure']:.2f} | ×{x['volr']:.1f} | {x['state']} | {why} |")
        if n_gated > 10:
            lines.append(f"\n> 其余 {n_gated - 10} 只已省略。")
    # 机器可读明细：与上方三状态同源、同一批标的（不含 INVALID），保持 12 列口径不变，
    # 供 trade_execution_engine.parse_md_pool 与 tushare_quant 的 md 回退解析继续可用。
    if exec_rows:
        lines.append(f"\n## 今日可操作榜（三状态明细 · 机器可读，与上方同一批 {len(exec_rows)} 只，按执行状态排序）\n")
        lines.append("| # | 代码 | 名称 | 优先 | IGE_ADJ | 总分 | 类型 | 现价 | 触发价 | MA20 | 量比 | 状态 |")
        lines.append("| -- | -- | -- | --: | --: | --: | -- | --: | --: | --: | --: | -- |")
        for k, x in enumerate(exec_rows, 1):
            lines.append(f"| {k} | {x['code']} | {x['name']} | {w7_priority(x)}/3 | {_ige_tag(x)} | {x['score']:.1f} | {x['type']} "
                         f"| {x['close']:.2f} | {x['pressure']:.2f} | {x['ma20']:.2f} | ×{x['volr']:.1f} | {x['state']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def sync_downstream(date, results, output, rb_extra=None):
    """V5.1 同步：把过滤后的「今日可操作（当日买点）」信号写给下游。
    1) 写 w7_today_action_{date}.json（report_daily）供 tushare_quant 汇总引用；
    2) 落 stock_pick_db 跟踪表（strategy=w7_hvt），盘后由 stock_pick_db.py tracking 回填 T+N/胜率。
    只同步当日买点七态（ACTION_BUY_STATES）中通过 V5.2 质量过滤（w7_quality_gate）的标的，
    等价报告「今日可操作榜」，不再把全候选池铺进跟踪表；
    rb_extra：20261002 用户口径——「右底低吸」不受 V4.4 龙头池/热点主题扩池限制，池外命中一并
    并入下游 JSON 与跟踪池（与报告「④ 右底低吸信号」节同源）；
    任一步失败都不阻塞报告输出。"""
    actionable = [x for x in results if x["state"] in ACTION_BUY_STATES]
    # 20261002：池外右底低吸并入（报告单列节同源）
    if rb_extra:
        actionable = actionable + [x for x in rb_extra if x["state"] in ACTION_BUY_STATES]
    # V5.2 质量过滤：与报告「今日可操作榜」同口径，被拦下的标的既不落跟踪表也不进下游 JSON
    n_before = len(actionable)
    actionable = [x for x in actionable if w7_quality_gate(x)[0]]
    actionable.sort(key=v52_sort_key)
    if n_before != len(actionable):
        print(f"[w7] V5.2 质量过滤: {n_before} → {len(actionable)} 只（拦下 {n_before - len(actionable)} 只）", flush=True)
    act_cn = {"SECOND_WAVE": "二波买点", "BREAKOUT_CONFIRM": "放量突破确认",
              "RE_EXPANSION": "重新扩张", "T0_CONFIRM": "T0天量确认买点",
              "BREAKOUT_RETEST": "放量突破后缩量回踩买点", "MIDLINE_HOLD": "W7-不破中位",
              "RIGHT_BOTTOM": "W7-右底低吸"}
    out_dir = os.path.dirname(os.path.abspath(output)) or OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    signals = []
    for x in actionable:
        _st, _fl, _fn = w7_exec_status(x)
        signals.append({
            "code": x["code"], "name": x["name"], "industry": x.get("industry"),
            "state": x["state"], "state_cn": act_cn.get(x["state"], x["state"]),
            "type": x["type"], "level": x["level"], "score": round(float(x["score"]), 1),
            "entry": round(float(x["entry"] or 0), 1), "action": x["buy"],
            "close": x["close"], "pressure": x["pressure"], "ma20": x["ma20"], "volr": x["volr"],
            "retest_low": x.get("retest_low"), "mid_line": x.get("mid_line"),
            "rb_left_low": x.get("rb_left_low"), "rb_offpool": bool(x.get("rb_offpool")),
            "priority": w7_priority(x),
            # 20261001 三状态执行分层：JSON 与跟踪表共享同一批字段（跟踪表经 record_picks 自动打包进 indicators）
            "w7_status": _st,
            "distance_to_trigger": round(w7_distance_to_trigger(x), 4),
            "w7_fail_line": round(_fl, 2) if _fl else None,
            "w7_fail_name": _fn,
            "ige_adj": x.get("ige_adj"), "event_date": x.get("event_date"),
            "reason": x.get("reason", ""), "t120": x.get("t120"),
        })
    try:
        jpath = os.path.join(out_dir, f"w7_today_action_{date}.json")
        with open(jpath, "w", encoding="utf-8") as fh:
            json.dump({"trade_date": date, "count": len(signals), "signals": signals},
                      fh, ensure_ascii=False)
        print(f"[w7] 今日可操作 JSON 已写: {jpath} ({len(signals)}只)", flush=True)
    except (OSError, TypeError) as exc:
        print(f"[w7] 今日可操作 JSON 写入失败(不影响报告): {exc}", flush=True)
    try:
        from stock_pick_db import record_picks
    except Exception:
        record_picks = None
    if record_picks is None or not signals:
        return
    try:
        rows = []
        for idx, x in enumerate(signals, 1):
            # stop_price(失效位) V5.3 修订：结构位(触发价/MA20/放量突破日低点)取最宽者 + clamp 距现价[5%,8%]。
            # 原口径直接取触发价，仅在已突破时生效且距现价中位仅 2.7%，导致 2~3% 插针即触发 STOP_HIT。
            # 新口径恒低于现价 ≥5%，无需再区分是否已突破。
            rows.append({
                "ts_code": x["code"], "stock_name": x["name"], "industry": x.get("industry"),
                "close": x["close"], "signal": x["state"], "action": f"{x['state_cn']}·{x['action']}",
                "score": x["score"], "rank_no": idx,
                "stop_price": w7_stop_price(x["close"], x["pressure"], x["ma20"], x.get("retest_low"),
                                            x.get("mid_line"), x.get("rb_left_low")),
                "reason": x["reason"],
                "state": x["state"], "type": x["type"], "level": x["level"], "entry": x["entry"],
                "volr": x["volr"], "ma20": x["ma20"], "event_date": x["event_date"],
                "ige_adj": x["ige_adj"], "t120": x["t120"],
                # 20261002：右底低吸池外标记（不受龙头池限制），随 indicators 一并落库便于复核
                "rb_offpool": bool(x.get("rb_offpool")),
                # 20261001 三状态执行分层：非 STD_COLS 字段由 record_picks 自动打包进 stock_pick.indicators，
                # 使跟踪池可按「入场执行状态」回测分组胜率（不改表结构、不改 tracking 回填逻辑）。
                "w7_status": x["w7_status"], "distance_to_trigger": x["distance_to_trigger"],
                "w7_fail_line": x["w7_fail_line"], "w7_fail_name": x["w7_fail_name"],
            })
        n = record_picks("w7_hvt", "W7 二波/突破当日买点", rows, pick_date=date)
        print(f"[w7] stock_pick_db 写入 {n}/{len(rows)} 条 (strategy=w7_hvt pick_date={date})", flush=True)
    except Exception as exc:
        print(f"[w7] stock_pick_db 写入失败(不影响报告): {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    reader = CacheReader()
    date = args.date or reader.latest_date()
    universe = reader.universe(date)
    load_codes = list(universe["ts_code"].tolist()) if not universe.empty else []
    print(f"[w7] 日期={date} 股池={len(load_codes)} 开始加载历史...", flush=True)
    reader.load_all(date, codes=load_codes, verbose=args.verbose)
    anchors = {}
    for label, (code, anchor_date) in ANCHORS.items():
        adf = reader.bars_sql(code, date)
        anchors[label] = anchor_features(adf, anchor_date)
    # V4.1：市场等权曲线（RS 基准）+ 财务数据（point-in-time）+ 板块聚合
    mdates, mvals = reader.market_curve(date)
    mkt = MarketCtx(mdates, mvals)
    nfina = reader.load_fina()
    industry_map = {}
    if not universe.empty:
        for _, r in universe.iterrows():
            industry_map[str(r.get("ts_code", ""))] = str(r.get("industry") or "")
    by_ind, fin_ind = {}, {}
    for code, f in reader.frames.items():
        if len(f) < 21:
            continue
        c0, c1 = finite(f.iloc[-21].close, 0.0), finite(f.iloc[-1].close, 0.0)
        ind = industry_map.get(code, "")
        if c0 <= 0 or not ind or ind == "nan":
            continue
        by_ind.setdefault(ind, []).append(c1 / c0 - 1.0)
        g = reader.fina_frames.get(code)
        if g is not None and len(g):
            np_g = finite(g.iloc[-1].netprofit_yoy, None)
            if np_g is not None:
                fin_ind.setdefault(ind, []).append(np_g)
    sector_strength = {ind: clip(50 + float(np.median(v)) * 150) for ind, v in by_ind.items() if len(v) >= 3}
    sector_growth = {ind: clip(50 + float(np.median(v)) * 1.1) for ind, v in fin_ind.items() if len(v) >= 3}
    print(f"[w7] 财务覆盖={nfina} 行业强度={len(sector_strength)} 行业景气={len(sector_growth)}", flush=True)
    sli_codes = load_sli_codes(date)  # V4.4：SLI 龙头票池联动过滤
    allow_codes = build_allow_codes(date, sli_codes)  # V4.4 龙头 ∪ V2.4 热点主题扩池
    # 热点主题扩池新增跟踪节元数据（供报告独立成节：列出扩池纳入、不在 sli 龙头池的候选）
    theme_meta = None
    if THEME_EXPAND_ENABLED and sli_codes is not None and allow_codes is not None:
        _added = set(allow_codes) - set(sli_codes)
        try:
            from theme_hot_pool import stock_hot_themes
            _info = stock_hot_themes(date, window=THEME_EXPAND_WINDOW, heat_min=THEME_EXPAND_HEAT_MIN)
        except Exception as exc:
            print(f"[w7] 警告：热点主题明细加载失败({exc})，新增跟踪节仅列代码", flush=True)
            _info = None
        _wincn = {"today": "日", "week": "周", "month": "月"}.get(THEME_EXPAND_WINDOW, THEME_EXPAND_WINDOW)
        theme_meta = {"codes": _added, "info": _info or {},
                      "label": f"{_wincn}热度≥{THEME_EXPAND_HEAT_MIN:g}"}
    ige_info, ige_snap = load_ige_adj(date)  # IGE_ADJ 行业增长弹性接入（高弹性行业候选优先）
    results = []
    rows = universe.to_dict("records")
    if args.limit:
        rows = rows[:args.limit]

    def _analyze_row(row):
        """单票完整分析（含 ST/退市/次新前置校验）。池内池外共用，保证口径一致。"""
        code = str(row.get("ts_code", ""))
        if "ST" in str(row.get("name", "")).upper() or "退" in str(row.get("name", "")):
            return None
        name = str(row.get("name") or code)
        basic = reader.basic.loc[code] if code in reader.basic.index else {}
        list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if list_date and list_date.isdigit() and int(list_date) > int(date) - 365:
            return None
        df = reader.bars(code, date)
        industry = str(row.get("industry") or (basic.get("industry", "") if hasattr(basic, "get") else ""))
        ig = ige_info.get(code)  # V5.3：IGE_ADJ 需在 analyze 内参与定级，故先取快照再分析
        result = analyze(code, name, industry, df, anchors, reader=reader, mkt=mkt,
                         sector_strength=sector_strength, sector_growth=sector_growth,
                         ige_adj=ig["ige_adj"] if ig else None)
        if result:
            result["ige_adj"] = ig["ige_adj"] if ig else None
            result["ige_mix"] = ig["ige_mix"] if ig else None
            result["ige_sw_l1"] = ig["sw_l1"] if ig else ""
            result["ige_sw_l3"] = ig["sw_l3"] if ig else ""
            result["ige_snap"] = ige_snap
        return result

    t_start = time.time()
    for n, row in enumerate(rows):
        if n and n % 500 == 0:
            print(f"[w7] 分析进度 {n}/{len(rows)} 耗时={time.time()-t_start:.1f}s", flush=True)
        code = str(row.get("ts_code", ""))
        if allow_codes is not None and code not in allow_codes:  # V4.4 龙头 ∪ V2.4 热点主题
            continue
        result = _analyze_row(row)
        if result:
            results.append(result)
    # 20261002 用户口径：右底低吸（RIGHT_BOTTOM）不受 V4.4 龙头池/热点主题扩池准入限制——
    # 池外标的单独扫描，命中即单列（报告「④ 右底低吸信号」节）并同步跟踪池。
    rb_extra = []
    if allow_codes is not None:
        for row in rows:
            if str(row.get("ts_code", "")) in allow_codes:
                continue  # 池内已在 results 里，由常规通道处理，避免重复
            result = _analyze_row(row)
            if result and result.get("state") == "RIGHT_BOTTOM" and w7_quality_gate(result)[0]:
                result["rb_offpool"] = True
                _st, _fl, _fn = w7_exec_status(result)
                result["w7_status"], result["w7_fail_line"], result["w7_fail_name"] = _st, _fl, _fn
                rb_extra.append(result)
        if rb_extra:
            print(f"[w7] 龙头池外右底低吸 {len(rb_extra)} 只: "
                  f"{[x['code'] for x in rb_extra]}", flush=True)
    text = markdown(results, date, theme_meta, rb_extra=rb_extra)
    output = os.path.abspath(args.output or os.path.join(OUTPUT_DIR, f"w7_second_wave_{date}.md"))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(text)
    state_counts = {s: 0 for s in STATES}
    for x in results:
        state_counts[x["state"]] = state_counts.get(x["state"], 0) + 1
    buy_counts = {}
    for x in results:
        buy_counts[x["buy"]] = buy_counts.get(x["buy"], 0) + 1
    type_counts = {}
    for x in results:
        type_counts[x["type"]] = type_counts.get(x["type"], 0) + 1
    ige_vals = [x["ige_adj"] for x in results if isinstance(x.get("ige_adj"), (int, float))]
    stats = {
        "date": date, "universe": len(rows), "results": len(results),
        "output": output, "states": {k: v for k, v in state_counts.items() if v},
        "buys": buy_counts, "types": type_counts,
        "ige": {"snapshot": ige_snap, "covered": len(ige_vals),
                "adj_min": min(ige_vals) if ige_vals else None,
                "adj_max": max(ige_vals) if ige_vals else None},
    }
    print(json.dumps(stats, ensure_ascii=False))
    if not args.limit:  # V5.1：完整跑批才同步下游（--limit 调试跑不污染跟踪表/JSON）
        sync_downstream(date, results, output, rb_extra=rb_extra)
    reader.close()


if __name__ == "__main__":
    main()
