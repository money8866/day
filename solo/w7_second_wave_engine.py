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
# 20261023 用户口径「先去掉市值阀门」：原流通市值 ≥50 亿门槛（MIN_CIRC_MV）已停用，
# 全市场沪深 A 股（剔除北交所、ST/次新）均进入分析——否则凡拓数创 301313.SZ（10/15 流通仅 24.7 亿）
# 这类底部起涨票永远进不了 SHAKEOUT_HOLD 候选。
MIN_BARS = 250  # 最少 K 线：上市满一年才参与（次新股分位样本太少会失真）
DATA_START = "20230103"  # 天量分位历史起点：以 20230103 为起点，此后上市以上市日为起点
MAIN_EVENT_PCT = 99.0  # 主事件门槛：量能+成交额历史分位双≥P99 才算天量主事件（20261001 由「换手率」改为「成交金额」；原 OR≥P98 命中面过大，V4.3 收紧为 AND≥P99）
MAX_EVENT_AGE = 60  # 近 60 天内出现过天量事件即入候选池
WATCH_MIN_T120 = 60  # V4.3：WATCH 状态 T120 下限，低于此分值的低分兜底票不输出
ANCHORS = {"中际旭创": ("300308.SZ", "20250508"), "华正新材": ("603186.SH", "20250812")}
STATES = ["DOWNTREND", "BASE", "IMPULSE", "EXTREME_CHURN", "ABSORPTION", "DRYUP", "RE_EXPANSION", "BREAKOUT_CONFIRM", "SECOND_WAVE", "T0_CONFIRM", "BREAKOUT_RETEST", "MIDLINE_HOLD", "SHAKEOUT_HOLD", "RIGHT_BOTTOM", "SQUAT_SHRINK", "T0_PULLBACK_HOLD", "T0_REBREAK", "DISTRIBUTION", "FAILED"]
# V5.1：当日买点五态（同步下游 stock_pick_db / tushare 均只取这几态，与报告「今日可操作榜」口径一致）
# 20260914 新增 BREAKOUT_RETEST：放量突破后缩量回踩买点（移远通信：8/14 天量T0 → 8/31 放量突破 → 9/9 起缩量回踩）
# 20260930 新增 MIDLINE_HOLD「W7-不破中位」：放量长阳（须创60日新高）后缩量回踩、收盘不破长阳半分位（中红医疗 9/23~9/24）
# 20261002 新增 RIGHT_BOTTOM「W7-右底低吸」：前波大涨后回落形成双底，右底缩量、不破左底（百普赛斯 301080.SZ 9/11）
# 20261023 新增 SHAKEOUT_HOLD：底部/首波起涨放量长阳（不要求60日新高）后回踩、不破半分位（凡拓数创 301313.SZ 10/15）
#          20261027 口径反转「W7-放量洗盘」→「W7-起涨缩量回踩」：T+10 超额三年全负，回踩放量/绝对量大/离MA20远均单调变差
# 20261026 新增 SQUAT_SHRINK「W7-缩量十字星下蹲」：天量 T0 后回撤、极致缩量的窄幅十字星跌破 MA20 但守住 MA60（极米科技 688696.SH 9/29）
# 20261028 用户口径：可操作信号归纳为两类（T0 锚定统一口径）。
#   类型A T0_PULLBACK_HOLD「缩量回踩至 T0 开盘价上方 + 止跌信号」→ 低吸买点（现价即买点区）
#   类型B T0_REBREAK「回踩后突破 T0 日收盘价」→ 二波买点（突破当日即买）
# 旧 5 形态（BREAKOUT_RETEST/MIDLINE_HOLD/SHAKEOUT_HOLD/RIGHT_BOTTOM/SQUAT_SHRINK）
# 已从可操作集合中移除（判定函数保留在模块内供 hvt_bull 独立扫描与历史回测复用）。
ACTION_BUY_STATES = ("T0_PULLBACK_HOLD", "T0_REBREAK")
# 20261028 用户口径「只要写跟踪库，后面再统一复查」：
# 新口径（类型A/类型B）信号落独立 strategy_id = w7_ab，与旧口径 w7_hvt 的 144 条历史彻底隔离。
# 原因：旧口径的 BREAKOUT_RETEST / RIGHT_BOTTOM / MIDLINE_HOLD / SHAKEOUT_HOLD / SQUAT_SHRINK
# 与新两类信号形态、阈值、市场门控全不相同，混在一个 strategy_id 下会让后续「统一复查」
# 的样本量 / 胜率 / T+N 收益无法归因到新口径。
# w7_hvt 保留为「旧口径历史归档」只读参考，不再写入；新口径自此只进 w7_ab。
W7_AB_STRATEGY_ID = "w7_ab"
W7_AB_STRATEGY_NAME = "W7 二波·新口径两类信号(T0锚定)"
# 20261014：T0_CONFIRM / MIDLINE_HOLD 仍留在本表（状态机与候选池展示沿用，退化为「观察」），
# 但在 w7_quality_gate 中由 W7_GATE_DROP_STATES 剔除，不再产出可操作买点、不再落库
# （依据分类收益率分析：两者 T+3 -7.89% / -8.50%，胜率 0~11%，盈亏比 ≤0.23，为全场最亏两类）。
W7_THEME_WATCH_CAP = 20  # 报告「热点主题新增跟踪」节展示上限（20261001；20261008 起该节随准入池一并停用）
# 20260909 用户口径「T0 确认买点」（我爱我家 8/19→8/27）：天量 T0 之后出现首根收盘回到
# T0 收盘价之上的“重夺日”（8/25），随后连续 ≥2 根收盘站稳 T0 收盘价（8/26/8/27），
# 确认日当日即视为有效买点（旁路 <5 根K线等待期与 major_risk 的早期误判）。仅限事件后
# 早期窗口，且真实放量突破（BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION）仍优先。
T0_CONFIRM_MAX_BARS = 10
# 20261008 用户口径「T0 日到买点信号之间一定要有大阴线大阳线交织」：
# 中间段（T0 之后、买点当日之前，不含两端）须至少各有 1 根大阳线与 1 根大阴线，剔除 T0 后
# 单边阴跌（无大阳）/单边直上（无大阴）的平滑结构。仅作用于 T0 锚定的买点分支
#（T0_CONFIRM/BREAKOUT_CONFIRM/SECOND_WAVE/RE_EXPANSION/BREAKOUT_RETEST）；
# 无天量 T0 锚的 MIDLINE_HOLD/RIGHT_BOTTOM 不适用（其结构本就无关 T0 段）。
BIG_BAR_PCT = 3.0  # 大阳线/大阴线：日涨跌幅阈值（%，正负同值）
# 20261023 用户口径「T0 须为起涨日」：连板/加速段的 P99 天量日常落在主升末端（凡拓数创 301313.SZ：
# 10/13 起涨放量长阳 +13.91%、量 3.76×20日均量，10/21 才是量能 P99 天量但已是第 4 根加速）。
# 做法：以 P99 天量日前 20 根内的最低价为「本轮上涨起点」，自该起点起取第一根
# 「收阳 + 涨幅≥7% + 量≥3×前20日均量」的放量长阳作为 T0；找不到则沿用原天量日。
T0_PIVOT_BARS = 20
T0_PRESHIFT_PCT = 7.0
T0_PRESHIFT_VOLR = 3.0
# 20261028 用户口径（斯菱智驱 301550.SZ）：T0 应取「长阳 + 巨量」的起涨日 0811（+20.00% 涨停、
# 量 2.31×前20日均量），而 0812（+1.45%、量 3.11×、跳空高开 89.58 后回落收 88.69）只是高位换手的
# 延续日。旧口径只认「量≥3.0×」，0811 被 3.0 卡掉、T0 反而锚到 0812，连带把失效位从 72.06
# 抬到 89.58 —— 0819 单日 -15.10%（低 75.07）就把整段结构判死。
# 修正：量能门槛按涨幅分档——涨停/准涨停级长阳（≥10%）本身就是「起涨确认」，量能要求降到 2.0×；
# 普通长阳（7%~10%）仍按 3.0×（原 20261023 凡拓数创标定值不变）。
T0_PRESHIFT_PCT_STRONG = 10.0
T0_PRESHIFT_VOLR_STRONG = 2.0
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
MIDLINE_VOL_RATIO = 0.60  # 缩量通道：当日量 ≤ 长阳日量 ×0.6（抛压衰竭型回踩）
# 20261023 用户口径「放量是洗盘彻底、换筹充分」：回踩不再强制缩量，放量回踩同样有效。
# 但须把「放量洗盘」与「放量出货」分开——缩量通道无条件通过，放量通道另加三道约束：
#   ① 量不过度：当日量 ≤ 长阳日量 ×MIDLINE_SHAKE_VOLR（超限视为派发/恐慌抛售）；
#   ② 趋势未破：当日收盘 ≥ MA10（放量下跌却破 MA10 → 破位，不是洗盘）；
#   ③ 非假突破：回踩期未曾收在长阳最高价上方（突破后再跌回＝一日游，如南威软件 603636.SH 20261022）。
# 收盘不破长阳半分位、回踩低点不破中位×MIDLINE_LOW_TOL 等结构条件对两条通道同样生效。
# 典型正样本：凡拓数创 301313.SZ 20261015（-8.07%、量 3.25×20日均量＝长阳日 0.87 倍、
#   收 31.43 仍在中位 30.73 上方、站上 MA10）——旧口径因「未缩量」漏判，其后 4 日 +74%。
MIDLINE_SHAKE_VOLR = 2.00  # 放量洗盘上限：当日量 ≤ 长阳日量 ×2.0
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
# 20261023 新增独立策略「W7-放量洗盘」（用户口径「另开一个新策略」，对标凡拓数创 301313.SZ：
# 10/13 起涨放量长阳 +13.91%（量 3.76×20日均量，收 33.01）→ 10/15 放量长阴 -8.07%（量 3.25×20日均量
# ＝长阳日 0.87 倍，收 31.43 仍在中位 30.73 上方、站上 MA10）→ 4 日后 54.80（+74%）。
# 与 MIDLINE_HOLD 的差异（故另开一支，不改原分支口径）：
#   ① 不做「60 日新高」要求 —— 该形态是底部反转/首波起涨，长阳日本就远离前高（301313 距前 60 日高点 -18.4%）；
#   ② 补「起涨」属性：长阳日前 20 根累计涨幅 ≤30%，排除已加速段的高位长阳。
# 结构：放量长阳半分位不破 + 回踩期未创长阳新高（未假突破）+ 收盘站上 MA10/MA20。
#
# 20261027 口径反转（用户指令「昨天 18 只，数量太多了，如何优化」；标定脚本 `_tmp_shake.py`，
# 全市场 5357 只 / 2024-01-01~2026-10-26 / 11,621 个信号 / 超额=个股−全市场等权）：
#   【裸口径是负 alpha】T+1 -0.00% / T+3 -0.26%(胜率 40.2%) / T+5 -0.52% / T+10 -0.93%(胜率 37.2%)，
#     分年 T+10 超额 2024 -1.00% / 2025 -1.14% / 2026 -0.60%，三年全负 → 「放量洗盘」假设被数据否定。
#   【三条轴严格单调递减】即回踩越放量、当日绝对量越大、离 MA20 越远，后续越差：
#     · 当日量/长阳日量(vr)：0.6~0.7 → T+10 0.00%；0.7~0.8 → -0.61%；0.8~0.9 → -0.92%；
#                            0.9~1.0 → -1.34%；1.0~1.3 → -1.43%；1.3~1.8 → -1.91%。
#                            （旧口径「回踩必须放量 (0.6, 2.5]」正是全样本最差区间的来源）
#     · 当日量/20日均量(vr20)：≤0.9 → +1.16%(胜率 46.2%，2024 +1.71/2025 +1.03/2026 +0.79 三年全正)；
#                            0.9~1.2 → -0.42%；1.2~1.6 → -0.40%；>1.6 → -1.27%。
#     · 距 MA20 缓冲：≤1% → +0.59%；1~2% → +0.42%；2~3% → -0.25%；3~5% → -0.82%；
#                            5~8% → -0.94%；>8% → -1.24%。
#   【定档 H】vr∈(0.6, 1.2]、vr20≤1.2、距 MA20 ≤3%：n=327（日均 0.5 只），
#     T+3 +0.71%(胜率 45.6%)、T+10 +1.66%(胜率 49.1%)，分年 T+10 +1.46%/+0.60%/+2.60% 三年全正。
#     20261026 由 18 只收敛为 1 只（002475.SZ 立讯精密），该批 18 只按三轴加权历史期望超额约 -0.8%。
#   【放弃更严的 K 档】vr≤1.0 & vr20≤1.2 & 距 MA20≤2%（n=187，T+10 +1.81%/胜率 50.8%、三年 +1.76/+1.83/+1.81
#     一致性最高），但日均仅 0.3 只且 20261026 归零，节奏过稀，故取 H 档。
#   【保留 vr 下限 0.6】不可再低，否则与 MIDLINE_HOLD 的「极致缩量承接」完全同质，失去独立分支意义。
SHAKEOUT_DAY_PCT = 7.0        # 起涨放量长阳：涨幅下限
SHAKEOUT_DAY_VOLR = 1.3       # 起涨放量长阳：量 ≥1.3×前20日均量
SHAKEOUT_CLOSE_POS = 67.0     # 起涨放量长阳：收盘位于当日振幅上 1/3
SHAKEOUT_MAX_AGE = 10         # 新鲜度：长阳日距当日 ≤10 根
SHAKEOUT_PRE20_RISE = 0.30    # 起涨属性：长阳日前 20 根累计涨幅 ≤30%（区间震荡/底部反弹，而非加速末端）
SHAKEOUT_VOL_LO = 0.60        # 回踩量能下限：当日量 >0.6×长阳日量（20261027：仅保留下限，防与 MIDLINE 同质）
SHAKEOUT_VOL_HI = 1.20        # 回踩量能上限：当日量 ≤1.2×长阳日量（20261027 由 2.50 收紧；越放量后续越差）
SHAKEOUT_VR20_HI = 1.20       # 当日绝对量能：当日量 ≤1.2×前20日均量（含当日，20261027 新增，最强单轴之一）
SHAKEOUT_MA20_MAX = 0.03      # 不追高：当日收盘 ≤ MA20×1.03（20261027 新增，距 MA20 越远后续越差）
SHAKEOUT_MAX_EXT = 1.10       # 未拉开：现价 ≤ 长阳收盘 ×1.10
SHAKEOUT_LOW_TOL = 0.98       # 插针容忍：回踩期最低 ≥ 中位 ×0.98
SHAKEOUT_MA10 = 1.00          # 趋势未破：当日收盘 ≥ MA10
SHAKEOUT_MA20 = 1.00          # 中期未破：当日收盘 ≥ MA20
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
# 20261010 用户口径「二波（L1→M 中间反弹）量能太少 → 双底失效」：中间反弹段须有放量承接，
# 段内最大量比 ≥ 本阈才算真二波；纯缩量反抽（价格反弹但全程不放量）视为无量二波，双底不成立。
# 与 W7_VOL_CONFIRM（1.2）同口径。典型样本：美格智能 002881.SZ（0914~0922 反弹 +22%，段内最大量比仅 1.07）。
RIGHT_BOTTOM_REBOUND_VOLR = 1.2  # 二波放量：L1→M 段内最大量比 ≥1.2
RIGHT_BOTTOM_TOL_LO = 0.02    # 不破左底：low[T] ≥ L1×(1-2%)
RIGHT_BOTTOM_TOL_HI = 0.08    # 右底不高出左底太多：low[T] ≤ L1×(1+8%)（超出即为「回踩不到位」，非同一底）
RIGHT_BOTTOM_RETRACE = 0.10   # 自 M 的回撤下限
RIGHT_BOTTOM_NEAR = 3         # 当日须为近 N 根最低（正在探右底）
RIGHT_BOTTOM_VOL_DRYUP = 0.75  # 右底缩量：vol[T] ≤ 0.75×前20日均量
RIGHT_BOTTOM_MA60_FLAT = -0.005  # 中期趋势：MA60 近 5 根不弱于 -0.5%
# 20261023 用户口径「持续放量的周期数已经太多了，这里不是洗盘而是出货尾声」（依顿电子 603328.SH 20261022）：
#   右底低吸要求的是「缩量、抛压衰竭后探底」，若 T0 之后长期持续放量，说明筹码仍在反复换手出逃，
#   后面的所谓右底只是下跌中继。口径：以 T0（未传锚时退化为前波高点 H0）为起点，统计最长连续
#   「量比 ≥ RIGHT_BOTTOM_CHURN_VOLR」的根数，超过 RIGHT_BOTTOM_MAX_CHURN_RUN 即剔除。
#   依顿电子：T0=20260914（P99.17，量 1,066,494）→ 9/15 起连续 8 根量比≥1.2、后段/前段量能 2.36 倍、
#   价格自 9/24 高点 15.29 一路走低且再未创新高；对照样本 信达地产 5 根、石药创新 2 根（保留）。
RIGHT_BOTTOM_CHURN_VOLR = 1.2   # 单日「放量」定义：量 ≥1.2×前20日均量
RIGHT_BOTTOM_MAX_CHURN_RUN = 6  # 连续放量根数上限：>6 根视为出货尾声（非洗盘）
# 20261026 新增分支「W7-缩量十字星下蹲」（用户口径，对标极米科技 688696.SH 20260929：
# T0=20260901（P99.17，量 40,748）→ 9/07 高点 91.88 → 9/29 开81.46/高81.98/低80.51/收81.00（-0.07%），
# 量 4,305＝前20日均量 14,800 的 0.29 倍（仅为近60日地量 3,793 的 1.14 倍），实体 31%、上影 35%、下影 33%，
# 收盘 81.00 跌破 MA20(85.11) 但仍在 MA60(72.77) 上方，自 9/07 高点回撤 11.84% → 次日 9/30 放量 +4.68%。
# 与 RIGHT_BOTTOM 的区别（决定性）：右底要求 H0→L1→M→T 的双底几何，而 9/29 自身就是最低点 L1，
# 颈线 M＝当日、反弹 0%，六条几何硬条件（③④⑤⑥⑦）必然全部不成立 → 结构上永远不可能命中，
# 该形态是「天量后第一只脚下蹲」，不是右底。故单开一支。
# 标定（`_tmp_squat.py`，全市场 5631 只，2025-01-01~2026-10-26，超额=个股−全市场等权）：
#   裸「缩量十字星+破MA20不破MA60+自T0回撤≥8%」n=2034 → T+10 超额 +0.29%、胜率 40.9%（≈无 alpha）；
#   加「T0量/当日量 ≥12」n=170 → T+10 +0.84%、胜率 47.6%，但极米仅 9.46 被排除；
#   加「T0量/当日量 ≥8」+「当日量 ≤1.2×近60日地量」n=239 → T+10 +0.75%、胜率 46.4%，
#     2025 +0.33% / 2026 +1.66% 两年同向为正，且极米（9.46 ≥8、1.14 ≤1.2）刚好入池 → 本版定稿此档。
#   另测无效或有害：加「次日放量阳线确认」样本崩至 7~10 条且 T+10 转负（-2.99%）；age_min≥15、回撤阈值 10%/12%、
#     仅 T0前20根涨幅≥20% 均为 +0.2~0.4 噪声水平。
#   T+3 超额为负（-0.19%）→ 与 MIDLINE_HOLD 同属「低吸后需 3~10 日发酵」的中短期信号，非次日冲高型。
SQUAT_VOLR = 0.35        # 极致缩量：当日量 ≤0.35×前20日均量
SQUAT_T0_RATIO = 8.0     # 天量衰减：T0 天量 ≥8×当日量（alpha 主来源）
SQUAT_LOW60 = 1.20       # 近地量：当日量 ≤1.2×近60日最低量
SQUAT_AMP = 0.04         # 窄幅：振幅(高-低)/前收 ≤4%
SQUAT_BODY = 0.35        # 十字星：实体 ≤35%×振幅
SQUAT_SHADOW = 0.15      # 十字星：上影、下影各 ≥15%×振幅
SQUAT_DD = 0.08          # 自 T0 后最高点回撤 ≥8%
SQUAT_MA60 = 1.00        # 中期未破：收盘 ≥ MA60
SQUAT_MA20 = 1.00        # 下蹲位置：收盘 ≤ MA20（本轮下跌确实跌破短期均线）
# ===== 20261028 用户口径：信号归纳为两类（V6 统一口径）=====
# 背景：引擎此前同时存在 5 个「缩量低吸」形态（BREAKOUT_RETEST/MIDLINE_HOLD/SHAKEOUT_HOLD/
# RIGHT_BOTTOM/SQUAT_SHRINK），各以不同价位为锚（突破日收盘/长阳半分位/长阳半分位/左底/下蹲低点），
# 既相互重叠又与「W7 二波」的命名承诺错位（无一只吃放量突破）。20261028 用户口径统一为两类，
# 全部锚定同一个 T0（量能放大激活日），两类共用 T0 的开盘价/收盘价两个价位：
#   类型A T0_PULLBACK_HOLD「缩量回踩止跌低吸」：
#       T0 强势激活 → 缩量回踩（最低回撤 ≥W7_T0_PULLBACK_MIN）→ 回踩守住 T0 开盘价上方
#       （≥T0开盘×(1-W7_T0_OPEN_TOL)）→ 当日出现止跌信号 → 当日即低吸买点。
#   类型B T0_REBREAK「回踩后突破 T0 日收盘价」：
#       T0 强势激活 → 缩量回踩洗盘 → 放量收盘突破 T0 日收盘价（且收盘位置/上影合格）→ 当日即二波买点。
# 价位口径（统一）：
#   T0 开盘价 = 回踩结构防线（跌破 = 本轮激活失效）；T0 日收盘价 = 二波突破目标位（触发价）。
W7_T0_MAX_AGE = 40          # 类型A：T0 锚年龄上限（根）：太久的激活日结构已不新鲜
W7_T0_B_MAX_AGE = 15        # 类型B：T0 锚年龄上限（根）——标定显示锚龄 >15 根后 T+3 超额
#                            由 +0.47% 衰减到 -0.03%（n=250 vs 394），突破信号时效性远高于低吸
W7_T0_PULLBACK_MIN = 0.04   # 回踩深度下限：回踩最低 ≤ T0收盘×(1-4%)（确属回踩，不是横盘）
W7_T0_OPEN_TOL = 0.02       # 结构容忍：回踩最低 ≥ T0开盘×(1-2%)（插针容忍，跌破即破位）
W7_T0_SHRINK = 0.50         # 类型A 缩量：回踩段均量 ≤ T0量×0.50（抛压衰竭）
#   标定（300 只样本 / 2024-2026 / 9.5 万行判定原语，单轴网格）：
#   0.5 → T+3 超额 +0.11% / T+10 +0.02%；0.8 → -0.00% / -0.26%；1.0 → -0.04% / -0.34%。
#   回踩越缩、后续越好，单调递减——「缩量」是类型A 的首要判别，不只是描述词。
W7_T0_SHRINK_B = 0.70       # 类型B 回踩期均量上限（≤T0量×0.7：允许温和放量洗盘，不容巨量派发）
#   标定：0.7 → T+3 超额 +0.25% / T+10 -0.32%；1.0 → -0.03% / -0.99%。
W7_T0_REACH = 0.03          # 回踩到位：回踩最低 ≤ T0开盘×(1+3%)（确曾回落至激活起点附近）
#   标定：0.03 → T+10 超额 -0.01%；0.05 → -0.26%；0.02 → -0.01%。要求回踩真正到位才有承接。
W7_T0_BREAK_MAX_EXT = 1.10  # 类型B 不过度拉升：当日收盘 ≤ T0收盘×1.10（不追高）
#   标定：1.10 → T+3 超额 +0.26% / T+10 -0.20%；1.15 → +0.03% / -0.99%；1.06 → -0.14% / -0.62%。
#   突破日离 T0 收盘价越远＝接盘价越高，alpha 在「刚突破」而不在「涨得远」。
W7_T0_STALL_VOLR = 0.55     # 止跌日量比上限：当日量 ≤ 0.55×前20日均量（止跌必须真缩量）
#   标定（类型A 按当日量比分层）：<0.45 → T+3 超额 +0.87%/T+10 +2.07%（n=91）；
#   <0.55 → +0.34%/+0.86%（n=221）；<0.65 → -0.08%/+0.09%（n=372）；全部 → +0.16%/-0.05%（n=734）。
#   止跌日的量能越小＝抛压越衰竭＝承接越好，单调递减——「缩量」二字在类型A 是硬判别而非描述词。
#   注：该阈值同时决定 w7_priority 的量能因子（类型A 命中条件：量比 < W7_VOLR_STRONG）。
W7_T0_STALL_MAX_BARS = 5    # 回踩低点距买点日上限（根）：买点须紧邻回踩低点（≤5根）
W7_T0_STALL_MAX_EXT = 1.08  # 类型A 不追高：当日收盘 ≤ T0收盘×1.08（离激活日越远后续越差）
W7_T0_MA60_K = 0.97         # 中期未破：收盘 ≥ MA60×0.97
W7_T0_BREAK_VOLR = 2.20     # 类型B 突破日放量：量 ≥ 2.20×前20日均量（不含当日）
#   标定（类型B 按当日量比分层）：≥1.4 → T+3 -0.09%（n=111）；≥1.8 → n=52 仍为负；
#   ≥2.2 → T+3 超额 +2.83%/T+10 +1.56%（n=29）；≥2.6 → +3.36%/+3.13%（n=20）。
#   突破日的量能必须「显著放大」才算真突破——温和放量（1.2~1.8×）在统计上不构成有效确认。
W7_T0_BREAK_CLOSE_POS = 70  # 类型B 突破日收盘位置 ≥70（收在当日振幅上1/3）
W7_T0_BREAK_SHADOW = 0.35   # 类型B 突破日上影 ≤35%（收在高位，非冲高回落）
W7_T0_MIN_AMOUNT = 50000.0  # 流动性下限：当日成交额 ≥ 50000 千元（amount 单位＝千元 → 5000 万元）
#   20261028 用户口径修复「微盘票无流动性下限」：原实现对成交额零门槛，
#   微盘/低流动性票可凭形态入选，实盘无法成交。用当日成交额（千元）作硬下限。
W7_T0_MIN_BASE_SCORE = 40.0  # 兜底分下限：HVT-V3 BaseScore ≥40（替代旧「底部结构豁免低分剔除」漏洞）
# 20261028 市场环境门（接入原本是死代码的 market_regime）：
#   标定（300 只样本 / 2024-2026 / 9.5 万行原语）分环境超额：
#     类型A  RECOVERY T+10 +0.23% / RANGE -1.58% / BULL +1.59% / BEAR 样本不足
#     类型B  RECOVERY T+10 +0.53% / RANGE -2.45% / BULL +0.04%
#   BEAR 阻断类型A 的依据最强且有独立证据（跟踪库 202610 现役门控 55 个信号 T+3 -3.19%，
#   同期上证 -4.29% 仍为负超额），故默认阻断；
#   RANGE 虽同样偏弱，但样本仅 300 只（占全市场 6%）且 2024 年该环境下类型A 反而为正
#   （T+10 超额 +1.02%），跨期不一致 → 不作硬阻断，仅在报告中提示，按此开关可一键启用。
W7_REGIME_BLOCK_TYPE_A = ("BEAR",)   # 阻断类型A 的市场环境集合；加入 "RANGE" 可启用震荡市过滤
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
# 20261014 用户口径「按分类收益率分析剔除亏损大的」（样本：stock_pick w7_hvt 已跟踪 135 条，
# 基准=选股日收盘价，收益=其后第 N 个交易日收盘涨幅；分组明细见 _w7_class_analysis.py）：
#   ① 执行状态：TRIGGER_WATCH / PULLBACK_WATCH 组 T+3 -2.21% / -2.91%、盈亏比 0.70 / 0.81、
#      止损率最高 58.3%；仅 EXECUTION 组为正期望（T+5 +2.76%、T+10 +8.45%、盈亏比 1.25），
#      EXECUTION_WAIT_VOLUME 中性（盈亏比 0.97）→ 只保留执行区两态进可操作榜。
#   ② 买点档：w7_assign_status 的 WATCH 档（三因子 0 命中）T+3 -4.39%、胜率 18.5%、
#      盈亏比 0.53，整个档位不落库。
#   ③④ 分支：T0_CONFIRM T+3 -7.89% / T+5 -13.91%、盈亏比 0.23；MIDLINE_HOLD T+3 -8.50%、
#      胜率 0%、盈亏比 0.11（全场最亏）→ 两者不再产出可操作买点。
#   四道过滤叠加实测：全样本 135 → 89 条，T+3 由 +0.45%（胜 49.6%）升到 +1.95%（胜 60%+）。
W7_GATE_DROP_STATES = ("T0_CONFIRM", "MIDLINE_HOLD")
W7_EXEC_KEEP = ("EXECUTION", "EXECUTION_WAIT_VOLUME")
W7_GATE_DROP_BUY = ("WATCH",)
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
    score 已降级为纯展示（控制批次后无正向区分度），定级与排序均以本函数为准。

    20261028 修正：量能因子的方向随信号类型而变——
      类型A（缩量回踩止跌低吸）沿用「量比 < W7_VOLR_STRONG」（缩量＝抛压衰竭，是优点）；
      类型B（回踩后突破 T0 收盘价）要求放量突破，量比高正是信号成立的前提，
      若仍用「量比<0.6」判优则类型B 恒 0 命中（恒被 W7_GATE_DROP_BUY 拦下，信号永不入库）。
      故类型B 的量能因子改为「量比 ≥ W7_T0_BREAK_VOLR」（放量确认），方向与信号定义一致。
    """
    hits = 0
    if x.get("type") == "MID":
        hits += 1
    ige = x.get("ige_adj")
    if isinstance(ige, (int, float)) and ige >= W7_IGE_STRONG:
        hits += 1
    if x.get("state") == "T0_REBREAK":
        # 类型B：量能因子＝放量突破确认（量比 ≥ W7_T0_BREAK_VOLR）
        if finite(x.get("volr"), 0.0) >= W7_T0_BREAK_VOLR:
            hits += 1
    elif x.get("state") == "T0_PULLBACK_HOLD":
        # 类型A：量能因子＝止跌日真缩量（量比 ≤ W7_T0_STALL_VOLR）
        if finite(x.get("volr"), 9.9) <= W7_T0_STALL_VOLR:
            hits += 1
    elif finite(x.get("volr"), 9.9) < W7_VOLR_STRONG:
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
    hits = w7_priority({"type": tp, "ige_adj": ige_adj, "volr": volr, "state": state})
    if hits >= 3:
        return "PRIMARY_BUY"
    if hits == 2:
        return "T120_ROCKET"
    if hits == 1:
        return "CONFIRMED"
    return "WATCH"


def w7_stop_price(close, pressure, ma20, retest_low=None, mid_line=None, right_low=None, squat_low=None,
                  t0_open=None, pull_low=None):
    """V5.3 失效位：结构位（触发价/MA20/放量突破日低点/长阳半分位/双底左底/下蹲日最低价）取最宽者，再 clamp 到距现价 [5%,8%]。
    现价无效时返回 None（下游不设止损）。
    20261028：新增 t0_open（类型A 防线）与 pull_low（类型B 防线）两个结构位候选。"""
    close = finite(close, 0.0)
    if close <= 0:
        return None
    cands = [v for v in (finite(pressure, 0.0), finite(ma20, 0.0), finite(retest_low, 0.0),
                         finite(mid_line, 0.0), finite(right_low, 0.0), finite(squat_low, 0.0),
                         finite(t0_open, 0.0), finite(pull_low, 0.0))
             if 0 < v < close]
    struct = min(cands) if cands else close
    return round(min(max(struct, close * (1 - W7_STOP_MAX_PCT)),
                     close * (1 - W7_STOP_MIN_PCT)), 3)


def w7_fail_line(x):
    """各形态自身结构失效位（20261028 统一两类口径）：
      T0_PULLBACK_HOLD＝T0 开盘价（跌破＝本轮量能激活失效）；
      T0_REBREAK＝突破前回踩最低价（跌破＝突破失败）；
      其余=MA20。
    返回 (失效位价格, 名称)；位缺失时返回 (0.0, 名称)。"""
    if x.get("state") == "T0_PULLBACK_HOLD" and finite(x.get("t0_open"), 0.0) > 0:
        return finite(x["t0_open"]), "T0开盘价"
    if x.get("state") == "T0_REBREAK" and finite(x.get("pull_low"), 0.0) > 0:
        return finite(x["pull_low"]), "回踩低点"
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

    20261028 统一两类口径：
    - T0_PULLBACK_HOLD（类型A 缩量回踩止跌低吸）：买点＝当日止跌位本身（现价即买点区），
      不以「距触发位」分档，结构未失效（收盘 ≥ T0 开盘价）即归入执行区 EXECUTION。
      止跌日不适用放量阀门（当日本就是缩量），量比口径不参与该形态判定。
    - T0_REBREAK（类型B 回踩后突破 T0 收盘价）：买点＝突破当日，现价已 ≥ T0 收盘价（触发价），
      沿用通用口径：现价 ≥ 触发价 且 量比 ≥1.2 → EXECUTION；量比不足 → EXECUTION_WAIT_VOLUME。
    """
    fail_line, fail_name = w7_fail_line(x)
    close = finite(x.get("close"), 0.0)
    line_eff = fail_line * (1 - W7_MA20_BREAK_TOL) if fail_name == "MA20" else fail_line
    if line_eff > 0 and close < line_eff:
        return "INVALID", fail_line, fail_name
    if x.get("state") == "T0_PULLBACK_HOLD":
        # 类型A：当日止跌位即买点区（现价执行，无需等站上触发价）
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
        # 20261028：T0_PULLBACK_HOLD（类型A 缩量回踩止跌低吸）当日即无等待条件的买点，
        # 排在 EXECUTION 组内最前；其余 EXECUTION 仍按量能确认程度排序。
        rb = 0 if x.get("state") == "T0_PULLBACK_HOLD" else 1
        return (rank, rb, -volr, abs(d), -ige, -sc)
    if rank == 2:  # TRIGGER_WATCH
        return (rank, d, -volr, -ige, -sc)
    line = x.get("w7_fail_line")  # PULLBACK_WATCH
    if not isinstance(line, (int, float)) or line <= 0:
        line = w7_fail_line(x)[0]
    close = finite(x.get("close"), 0.0)
    dbuf = (close - line) / line if line > 0 and close > 0 else -1e9
    return (rank, -dbuf, d, -ige, -sc)


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
        # 20261023：市值阀门已按用户口径去掉，不再按流通市值过滤
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
    #   ① 天量判定第二维改比成交金额：换手率维度由 amount（成交额）承担，仍保留 vol（成交量）维度。
    #   ② 天量标志日前一日涨幅 > 10% 时，当日方向豁免（不再要求收阳）；
    #      豁免只免方向、保留「不烂尾」底线：T0 不跌停 且 收盘位置 ≥ 0.30（防巨量大阴线）。
    #   原口径（20260930）：须收阳（close>open），巨量大阴线不计入极端换手事件。
    #   案例：300458 全志科技 7/30 巨量 -19.93% 阴线原被锚为 T0（锚点 P99@20260730），
    #   加「须收阳」后 7/30 被剔除，最近一簇回退到 7/22 放量长阳（+11.14%，量能更大）。
    # 20261009 用户口径「恢复换手率维度」：第二维改为「成交额 P99 或 换手率 P99」的并集。
    #   20261001 整维替换后，中红医疗 300981 的 20260730（换手/量双 P99、成交额仅 98.3 分位）
    #   失去 T0 锚 → find_event_anchor 返回 None → analyze() 提前 return None，连带屏蔽了
    #   本应独立于 T0 锚的 MIDLINE_HOLD / RIGHT_BOTTOM 分支（该分支注释即以此为对标样本）。
    #   故保留 20261001 的成交额维度，同时恢复换手率维度，使旧锚点重新进入候选。
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
    elif finite(row.pct_chg) <= 0.0:
        # 20261026 用户口径：方向判定由「收阳(close>open)」放宽为「收涨(pct_chg>0)」，
        # 高开低走的假阴线不再被判成大阴线剔除。案例：新产业 300832 的 20260814，
        # vol/amount/turnover 三项均在近 120 日 P99 以上（量比20 2.51x），
        # 但 open 51.50 > close 51.24（当日实际 +3.22%），原口径漏掉了这个最近的天量 T0。
        # 保留「不烂尾」底线：收盘位置 < 0.30 的巨量日视为派发，不计入 T0。
        span = finite(row.high) - finite(row.low)
        if span > 0 and (finite(row.close) - finite(row.low)) / span < 0.30:
            return False, 0.0
    amt = finite(row.amount)
    vol = finite(row.vol)
    turn = finite(row.get("turnover_rate")) if "turnover_rate" in df.columns else 0.0
    amts = pd.to_numeric(df.amount.iloc[start:i], errors="coerce").fillna(0).values
    vols = pd.to_numeric(df.vol.iloc[start:i], errors="coerce").fillna(0).values
    p_amt = percentile_rank(amts, amt)
    p_vol = percentile_rank(vols, vol)
    p_turn = 0.0
    if turn > 0:
        turns = pd.to_numeric(df.turnover_rate.iloc[start:i], errors="coerce").fillna(0).values
        p_turn = percentile_rank(turns, turn)
    # V4.3→V5：OR≥P98 命中面过大 → AND≥P99；窗口由全历史改为 120 日（规格口径）
    # 20261009：必要条件 = 成交量 P99；第二维取「成交额 P99 或 换手率 P99」并集（见上方口径注释）
    if p_vol < MAIN_EVENT_PCT:
        return False, 0.0
    if p_amt >= MAIN_EVENT_PCT:
        return True, max(p_amt, p_vol)
    if p_turn >= MAIN_EVENT_PCT:
        return True, max(p_turn, p_vol)
    return False, 0.0


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


def _t0_levels(df, event_idx):
    """取 T0（量能放大激活日）的两个价位基准：开盘价（回踩防线）与收盘价（二波突破位）。"""
    ev = df.iloc[event_idx]
    return {
        "t0_date": str(ev.trade_date),
        "t0_open": finite(ev.open),
        "t0_close": finite(ev.close),
        "t0_high": finite(ev.high),
        "t0_low": finite(ev.low),
        "t0_vol": finite(ev.vol, 1.0),
        "t0_pct": finite(ev.pct_chg),
    }


def _t0_pullback_segment(df, event_idx, end):
    """切分 T0 之后的「冲高段」与「回踩段」，并给出回踩段关键量价特征。

    T0 之后价格通常先冲高再回落。若用「T0 之后全部 K 线」算均量，冲高段的放量会被算进
    「回踩缩量」里，导致真正的缩量回踩判不出来。故以段内最高价（第一波高点）为界：
      冲高段 = T0+1 .. 峰值根；回踩段 = 峰值根+1 .. 当日。
    返回 (回踩段 df, 峰值根相对下标, 回踩段最低价, 最低价相对下标)；无回踩段时 low_rel=-1。
    """
    seg = df.iloc[event_idx + 1:end + 1]
    if seg.empty:
        return None
    highs = seg.high.to_numpy(dtype=float)
    if not np.isfinite(highs).any():
        return None
    pk_rel = int(np.nanargmax(highs))
    pull = seg.iloc[pk_rel + 1:]
    if pull.empty:
        # 尚无回踩段（价格仍在冲高途中）——回踩类信号此时不成立
        return seg, pk_rel, 0.0, -1
    lows = pull.low.to_numpy(dtype=float)
    low_rel = int(np.nanargmin(lows))
    return pull, pk_rel, float(np.nanmin(lows)), low_rel


def t0_pullback_hold(df, event_idx, end):
    """类型A「缩量回踩至 T0 开盘价上方 + 止跌信号」买点识别（20261028 用户口径统一两类之一）。

    规则（全部只用 ≤ 当日的数据）：
      ① T0 须为放量激活日：当日量 ≥ 1.3×前20日均量（量能放大、股性激活）；
      ② 回踩成立：回踩段最低价 ≤ T0收盘×(1-W7_T0_PULLBACK_MIN)（确属回踩，不是横盘）；
      ③ 回踩到位：最低价 ≤ T0开盘×(1+W7_T0_REACH)（已回落至 T0 开盘价附近——「回踩至 T0 开盘价上方」）；
      ④ 守住结构：最低价 ≥ T0开盘×(1-W7_T0_OPEN_TOL)（跌破 T0 开盘价＝本轮激活失效）；
      ⑤ 缩量：回踩段均量 ≤ T0量×W7_T0_SHRINK（抛压衰竭）；
      ⑥ 止跌信号（当日，三选一）：
           (a) 十字星：实体 ≤35%×振幅且上下影均 ≥15%×振幅；
           (b) 长下影：下影 ≥40%×振幅；
           (c) 阳线收复：当日收阳且涨幅 ≥1.5%；
      ⑦ 买点须在低位附近：距回踩最低 ≤W7_T0_STALL_MAX_BARS 根，且收盘距回踩最低 ≤5%；
      ⑧ 中期未破：当日收盘 ≥ MA60×W7_T0_MA60_K；
      ⑨ 不追高：当日收盘 ≤ T0收盘×W7_T0_STALL_MAX_EXT。
    满足即当日为有效买点（现价即买点区），失效位＝T0 开盘价。
    """
    if event_idx is None or end - event_idx < 3 or end - event_idx > W7_T0_MAX_AGE:
        return False, None
    lv = _t0_levels(df, event_idx)
    t0_open, t0_close, t0_vol = lv["t0_open"], lv["t0_close"], lv["t0_vol"]
    if t0_open <= 0 or t0_close <= 0 or t0_vol <= 0:
        return False, None
    latest = df.iloc[end]
    # ① T0 须为放量激活日
    vol20_pre = safe_mean(df.vol.iloc[max(0, event_idx - 20):event_idx])
    if vol20_pre <= 0 or t0_vol < vol20_pre * 1.3:
        return False, None
    # 回踩段切分（冲高段放量不计入缩量判定）
    cut = _t0_pullback_segment(df, event_idx, end)
    if cut is None:
        return False, None
    pull, pk_rel, cur_low, low_rel = cut
    if low_rel < 0 or cur_low <= 0:
        return False, None
    # 最深回撤（T0 之后全部 K 线的最低）——「回踩到位/守住结构」用最深值判定
    seg_all = df.iloc[event_idx + 1:end + 1]
    deep_low = finite(seg_all.low.min(), 0.0)
    if deep_low <= 0:
        return False, None
    # ② 回踩成立（自 T0 收盘价回落）
    if deep_low > t0_close * (1 - W7_T0_PULLBACK_MIN):
        return False, None
    # ③ 回踩到位（回落至 T0 开盘价附近）
    if deep_low > t0_open * (1 + W7_T0_REACH):
        return False, None
    # ④ 守住 T0 开盘价
    if deep_low < t0_open * (1 - W7_T0_OPEN_TOL):
        return False, None
    # ⑤ 缩量回踩（只算回踩段，冲高段放量不计入）
    pull_vol = safe_mean(pull.vol)
    if pull_vol > t0_vol * W7_T0_SHRINK:
        return False, None
    # ⑥ 止跌信号
    o, h, l, c = (finite(latest.open), finite(latest.high),
                  finite(latest.low), finite(latest.close))
    if min(o, h, l, c) <= 0:
        return False, None
    span = h - l
    if span <= 0:
        return False, None
    body = abs(c - o)
    up_sh = (h - max(o, c)) / span
    dn_sh = (min(o, c) - l) / span
    pct = finite(latest.pct_chg)
    stall = ""
    if body <= 0.35 * span and up_sh >= 0.15 and dn_sh >= 0.15:
        stall = "十字星"
    elif dn_sh >= 0.40:
        stall = "长下影"
    elif c > o and pct >= 1.5:
        stall = "阳线收复"
    if not stall:
        return False, None
    # ⑦ 买点须在回踩低点附近
    low_idx = event_idx + 1 + pk_rel + 1 + low_rel
    if end - low_idx > W7_T0_STALL_MAX_BARS:
        return False, None
    if c > cur_low * 1.05:
        return False, None
    # ⑧ 中期未破
    ma60 = finite(latest.ma_bfq_60, 0.0)
    if ma60 > 0 and c < ma60 * W7_T0_MA60_K:
        return False, None
    # ⑨ 不追高
    if c > t0_close * W7_T0_STALL_MAX_EXT:
        return False, None
    # ⑩ 止跌非放量杀跌
    vol20_now = safe_mean(df.vol.iloc[max(0, end - 19):end])
    if vol20_now > 0 and finite(latest.vol) > vol20_now * W7_T0_STALL_VOLR:
        return False, None
    return True, {
        "t0_open": t0_open, "t0_close": t0_close, "t0_date": lv["t0_date"],
        "t0_vol": t0_vol, "t0_pct": lv["t0_pct"],
        "pull_low": deep_low, "pull_low_date": str(pull.iloc[low_rel].trade_date),
        "cur_low": cur_low,
        "pull_bars": end - low_idx,
        "pull_vol_ratio": pull_vol / t0_vol,
        "stall": stall, "stall_dn_sh": dn_sh, "stall_body": body / span,
        "t0_ext": c / t0_close,
    }


def t0_rebreak(df, event_idx, end):
    """类型B「回踩后突破 T0 日收盘价」买点识别（20261028 用户口径统一两类之二）。

    规则（全部只用 ≤ 当日的数据）：
      ① T0 须为放量激活日：当日量 ≥ 1.3×前20日均量；
      ② 先有回踩：回踩段最低价 ≤ T0收盘×(1-W7_T0_PULLBACK_MIN)，且最低 ≥ T0开盘×(1-W7_T0_OPEN_TOL)
         （回踩洗盘但未破 T0 起点）；
      ③ 回踩缩量：回踩段均量 ≤ T0量×W7_T0_SHRINK_B（允许温和放量洗盘）；
      ④ 当日突破：收盘 > T0收盘（突破 T0 日收盘价＝二波确认）；
      ⑤ 放量确认：当日量 ≥ 1.20×前20日均量（不含当日）；
      ⑥ 收盘强势：收盘位置 ≥70%（收在当日振幅上 1/3）、上影 ≤35%（不是冲高回落）；
      ⑦ 中期未破：收盘 ≥ MA60×W7_T0_MA60_K；
      ⑧ 不过度拉升：收盘 ≤ T0收盘×W7_T0_BREAK_MAX_EXT（不追高）。
    满足即当日为有效买点（突破当日即买），失效位＝回踩段最低价。
    """
    if event_idx is None or end - event_idx < 3 or end - event_idx > W7_T0_B_MAX_AGE:
        return False, None
    lv = _t0_levels(df, event_idx)
    t0_open, t0_close, t0_vol = lv["t0_open"], lv["t0_close"], lv["t0_vol"]
    if t0_open <= 0 or t0_close <= 0 or t0_vol <= 0:
        return False, None
    latest = df.iloc[end]
    vol20_pre = safe_mean(df.vol.iloc[max(0, event_idx - 20):event_idx])
    if vol20_pre <= 0 or t0_vol < vol20_pre * 1.3:
        return False, None
    cut = _t0_pullback_segment(df, event_idx, end)
    if cut is None:
        return False, None
    pull, pk_rel, cur_low, low_rel = cut
    if low_rel < 0 or cur_low <= 0:
        return False, None
    # 突破前区间：T0+1 .. 昨日（不含突破日——突破日自身是放量突破，量不能算进回踩量）
    pre = df.iloc[event_idx + 1:end]
    if pre.empty:
        return False, None
    pre_low = finite(pre.low.min(), 0.0)
    if pre_low <= 0:
        return False, None
    # ② 先有回踩且守住 T0 起点
    if pre_low > t0_close * (1 - W7_T0_PULLBACK_MIN):
        return False, None
    if pre_low < t0_open * (1 - W7_T0_OPEN_TOL):
        return False, None
    o, h, l, c = (finite(latest.open), finite(latest.high),
                  finite(latest.low), finite(latest.close))
    if min(o, h, l, c) <= 0:
        return False, None
    if l < t0_open * (1 - W7_T0_OPEN_TOL):
        return False, None
    # ③ 回踩缩量（只算突破前的 K 线）
    pre_vol = safe_mean(pre.vol)
    if pre_vol > t0_vol * W7_T0_SHRINK_B:
        return False, None
    # ④ 当日突破 T0 日收盘价
    if c <= t0_close:
        return False, None
    # ⑤ 放量确认
    vol20_now = safe_mean(df.vol.iloc[max(0, end - 19):end])
    if vol20_now <= 0 or finite(latest.vol) < vol20_now * W7_T0_BREAK_VOLR:
        return False, None
    # ⑥ 收盘强势
    span = h - l
    if span <= 0:
        return False, None
    if pct_position(o, h, l, c) < W7_T0_BREAK_CLOSE_POS:
        return False, None
    if (h - c) / span > W7_T0_BREAK_SHADOW:
        return False, None
    # ⑦ 中期未破
    ma60 = finite(latest.ma_bfq_60, 0.0)
    if ma60 > 0 and c < ma60 * W7_T0_MA60_K:
        return False, None
    # ⑧ 不过度拉升
    if c > t0_close * W7_T0_BREAK_MAX_EXT:
        return False, None
    return True, {
        "t0_open": t0_open, "t0_close": t0_close, "t0_date": lv["t0_date"],
        "t0_vol": t0_vol, "t0_pct": lv["t0_pct"],
        "pull_low": pre_low,
        "pull_low_date": str(pre.iloc[int(np.nanargmin(pre.low.to_numpy(dtype=float)))].trade_date),
        "pull_bars": len(pre),
        "pull_vol_ratio": pre_vol / t0_vol,
        "break_volr": finite(latest.vol) / vol20_now,
        "break_ext": c / t0_close,
    }


def midline_hold(df, end):
    """「W7-不破中位」买点识别（返回 (成立, 放量长阳日 index or -1)）。

    不依赖 T0 天量事件锚，只看最近 MIDLINE_MAX_AGE 根内的「放量长阳」及其后的回踩：
      长阳日 B：收阳且涨幅 ≥MIDLINE_DAY_PCT、量 ≥MIDLINE_DAY_VOLR×前20日均量、收盘位于振幅上 1/3、
                且收盘 ≥ 前 MIDLINE_NH60 根最高价（平台突破属性，实测为本分支唯一有效判别）；
      中位    ：mid = (B.最高 + B.最低)/2（半分位）；
      ① 未创长阳新高：当日收盘 ≤ B.最高（仍在长阳范围内整理，非新一轮突破）；
      ② 未拉开    ：当日收盘 ≤ B.收盘 ×MIDLINE_MAX_EXT（仍是低吸位，已拉开转突破/二波分支）；
      ③ 不破中位  ：回踩期（B+1~当日）收盘均 ≥ mid（收盘口径），且最低 ≥ mid×MIDLINE_LOW_TOL（插针容忍）；
      ④ 回踩量能（20261023 重构，双通道）：
                   缩量通道：当日量 ≤ B.量 ×MIDLINE_VOL_RATIO（抛压衰竭）；
                   放量通道：当日量 ≤ B.量 ×MIDLINE_SHAKE_VOLR、收盘 ≥ MA10、且回踩期未曾收在 B.最高上方
                             （放量＝洗盘彻底、换筹充分；限量防派发，MA10 之上才算洗盘而非破位，
                              回踩期未破长阳高点排除「突破后跌回」的一日游）；
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
        # ④ 回踩量能双通道：缩量（抛压衰竭）或 放量洗盘（换筹充分）皆可，放量须限量且站上 MA10。
        v_latest = finite(latest.vol)
        v_base = finite(b.vol, 1)
        if v_latest > v_base * MIDLINE_SHAKE_VOLR:
            continue
        if v_latest > v_base * MIDLINE_VOL_RATIO:
            ma10 = finite(latest.ma_bfq_10, 0.0)
            if ma10 <= 0 or finite(latest.close) < ma10:
                continue
            # 放量日须全程留在长阳实体内：回踩期若曾收在长阳最高价上方再跌回，
            # 属「假突破/一日游」而非洗盘（典型：南威软件 603636.SH 20261022，10/21 收 9.78 > 长阳高 8.89，10/22 跌停回 8.80）。
            if finite(seg.close.max()) > finite(b.high):
                continue
        if finite(seg.close.min()) > finite(b.close, latest.close) * 0.995:
            continue
        return True, j
    return False, -1


def shakeout_hold(df, end):
    """「W7-起涨缩量回踩」独立策略买点识别（返回 (成立, 起涨长阳日 index or -1)）。

    20261027 口径反转（原「W7-放量洗盘」→ 现「起涨缩量回踩」，标定依据见文件头 SHAKEOUT 常量注释）：
      历史 11,621 个信号显示三条轴严格单调递减——回踩越放量、当日绝对量越大、离 MA20 越远，后续越差。
      原「回踩必须放量 (0.6, 2.5]」恰是全样本最差区间，三年 T+10 超额全负，故反转为「缩量 + 贴近 MA20」。

    只看长阳自身的价格结构：
      起涨长阳 B：收阳且涨幅 ≥SHAKEOUT_DAY_PCT、量 ≥SHAKEOUT_DAY_VOLR×前20日均量、
                 收盘位于振幅上 1/3、且长阳日前 20 根累计涨幅 ≤SHAKEOUT_PRE20_RISE（起涨而非加速末端）；
      中位      ：mid = (B.最高 + B.最低)/2；
      ① 未创长阳新高：回踩期（B+1~当日）收盘最高 ≤ B.最高（含当日，排除假突破/一日游）；
      ② 未拉开     ：当日收盘 ≤ B.收盘 ×SHAKEOUT_MAX_EXT；
      ③ 不破中位   ：回踩期收盘均 ≥ mid，最低 ≥ mid×SHAKEOUT_LOW_TOL（插针容忍）；
      ④ 缩量回踩   ：当日量 ∈ (SHAKEOUT_VOL_LO, SHAKEOUT_VOL_HI]×B.量（≤1.2×；放量＝出逃而非换筹）；
                    且当日量 ≤SHAKEOUT_VR20_HI×前20日均量（含当日；绝对量能才是承接真伪的分水岭）；
      ⑤ 趋势未破   ：当日收盘 ≥ MA10 且 ≥ MA20，且 ≤ MA20×SHAKEOUT_MA20_MAX（贴近均线，不追高）；
      ⑥ 确有回踩   ：回踩期收盘曾回落至 B.收盘 下方（排除长阳后单边直上）。
    满足即当日为买点；取最早满足的长阳日，保证跨日稳定。
    """
    if end < 21:
        return False, -1
    latest = df.iloc[end]
    for j in range(max(20, end - SHAKEOUT_MAX_AGE), end):
        b = df.iloc[j]
        if finite(b.close) <= finite(b.open) or finite(b.pct_chg) < SHAKEOUT_DAY_PCT:
            continue
        vol20 = safe_mean(df.vol.iloc[j - 20:j])
        if vol20 <= 0 or finite(b.vol) < vol20 * SHAKEOUT_DAY_VOLR:
            continue
        if pct_position(b.open, b.high, b.low, b.close) < SHAKEOUT_CLOSE_POS:
            continue
        c20 = finite(df.close.iloc[j - 20], 0.0)
        if c20 > 0 and finite(b.close) / c20 - 1 > SHAKEOUT_PRE20_RISE:
            continue
        mid = (finite(b.high) + finite(b.low)) / 2.0
        if mid <= 0:
            continue
        seg = df.iloc[j + 1:end + 1]
        if seg.empty:
            continue
        if finite(seg.close.max()) > finite(b.high):
            continue
        if finite(latest.close) > finite(b.close, latest.close) * SHAKEOUT_MAX_EXT:
            continue
        if finite(seg.close.min()) < mid:
            continue
        if finite(seg.low.min()) < mid * SHAKEOUT_LOW_TOL:
            continue
        vr = finite(latest.vol) / finite(b.vol, 1)
        if not (SHAKEOUT_VOL_LO < vr <= SHAKEOUT_VOL_HI):
            continue
        # ④b 当日绝对量能（含当日的前20日均量；标定最强单轴之一：≤0.9 时三年超额全正）
        vol20_now = safe_mean(df.vol.iloc[max(0, end - 19):end + 1])
        if vol20_now <= 0 or finite(latest.vol) / vol20_now > SHAKEOUT_VR20_HI:
            continue
        ma10 = finite(latest.ma_bfq_10, 0.0)
        ma20 = finite(latest.ma_bfq_20, 0.0)
        if ma10 <= 0 or ma20 <= 0 or finite(latest.close) < ma10 * SHAKEOUT_MA10 or finite(latest.close) < ma20 * SHAKEOUT_MA20:
            continue
        # ⑤b 不追高：距 MA20 不超过 3%（标定显示该轴严格单调，>3% 后转为明显负超额）
        if finite(latest.close) > ma20 * (1.0 + SHAKEOUT_MA20_MAX):
            continue
        if finite(seg.close.min()) > finite(b.close, latest.close) * 0.995:
            continue
        return True, j
    return False, -1


def squat_shrink(df, event_idx, end):
    """「W7-缩量十字星下蹲」买点识别（返回 (成立, 结构字典 or None)）。

    形态定位：天量 T0 之后的**第一只脚下蹲**（不是双底右底，故与 RIGHT_BOTTOM 并存不重叠）。
    极米科技 688696.SH 20260929 的教科书样本：T0=20260901（P99.17 天量）→ 9/07 高点 91.88 →
    9/29 缩量十字星（量比 0.29、实体 31%、上下影 35%/33%）跌破 MA20 仍守 MA60，次日 +4.68%。
    条件（全部只用 ≤ 当日的数据）：
      ① 锚：T0 距当日 ≥3 根（天量后确有回落过程）；
      ② 窄幅：振幅 (高-低)/前收 ≤SQUAT_AMP；
      ③ 十字星：实体 ≤SQUAT_BODY×振幅，上影、下影各 ≥SQUAT_SHADOW×振幅；
      ④ 极致缩量：当日量 ≤SQUAT_VOLR×前20日均量；
      ⑤ 天量衰减：T0 天量 ≥SQUAT_T0_RATIO×当日量（alpha 主来源，见文件头标定）；
      ⑥ 近地量：当日量 ≤SQUAT_LOW60×近60日最低量；
      ⑦ 下蹲位置：收盘 ≤MA20（本轮回落已跌破短期均线）；
      ⑧ 中期未破：收盘 ≥MA60；
      ⑨ 回撤到位：自 T0 后最高点回撤 ≥SQUAT_DD。
    失效位＝下蹲日最低价（第一只脚没有左底可依，破当日低点即形态失效）。
    """
    if event_idx is None or end - event_idx < 3:
        return False, None
    row = df.iloc[end]
    o, h, l, c = finite(row.open), finite(row.high), finite(row.low), finite(row.close)
    if min(o, h, l, c) <= 0:
        return False, None
    span = h - l
    if span <= 0:
        return False, None
    prev_c = finite(df.iloc[end - 1].close, 0.0)
    if prev_c <= 0 or span / prev_c > SQUAT_AMP:
        return False, None
    if abs(c - o) / span > SQUAT_BODY:
        return False, None
    if (h - max(o, c)) / span < SQUAT_SHADOW or (min(o, c) - l) / span < SQUAT_SHADOW:
        return False, None
    vol = finite(row.vol, 0.0)
    vol20 = safe_mean(df.vol.iloc[max(0, end - 20):end])
    if vol <= 0 or vol20 <= 0 or vol / vol20 > SQUAT_VOLR:
        return False, None
    t0_vol = finite(df.iloc[event_idx].vol, 0.0)
    if t0_vol <= 0 or t0_vol / vol < SQUAT_T0_RATIO:
        return False, None
    lo60 = finite(df.vol.iloc[max(0, end - 60):end + 1].min(), 0.0)
    if lo60 <= 0 or vol > lo60 * SQUAT_LOW60:
        return False, None
    ma20 = finite(row.ma_bfq_20, 0.0)
    ma60 = finite(row.ma_bfq_60, 0.0)
    if ma20 <= 0 or ma60 <= 0 or c > ma20 * SQUAT_MA20 or c < ma60 * SQUAT_MA60:
        return False, None
    seg_high = df.high.iloc[event_idx:end + 1].to_numpy(dtype=float)
    pk = finite(float(np.nanmax(seg_high)), 0.0)
    if pk <= 0:
        return False, None
    dd = (pk - c) / pk
    if dd < SQUAT_DD:
        return False, None
    pk_idx = event_idx + int(np.nanargmax(seg_high))
    return True, {
        "squat_date": str(row.trade_date), "squat_low": l, "squat_high": h,
        "squat_volr20": vol / vol20, "squat_t0_ratio": t0_vol / vol, "squat_dd": dd,
        "squat_pk_date": str(df.iloc[pk_idx].trade_date), "squat_pk_high": pk,
    }


def right_bottom(df, end, event_idx=None):
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
      ⑨ 低吸位置：收盘 ≤MA10；
      ⑩ 20261023 出货尾部否决：T0 起连续放量根数 ≤RIGHT_BOTTOM_MAX_CHURN_RUN。
      ⑪ 20261027 跌停否决：当日跌幅不得触及跌停（主板 ≤-9.5%、创业板/科创板 ≤-19.0%）。
    满足即当日为右底低吸买点；红线为左底（收盘跌破即结构失效）。
    event_idx：W7 主链传入的 T0 天量锚下标；未传（如 HVT-BULL 独立调用）时退化为前波高点 H0。
    """
    if end < RIGHT_BOTTOM_WINDOW + 25:
        return False, None
    # ⑪ 20261027 用户口径：跌停日不得作为右底买点。跌停当天的「缩量」是卖不出去、不是抛压衰竭，
    #    右侧根本没有承接。案例：000993.SZ 闽东电力 20261019 一字跌停 -10.00%
    #    （close=low=16.38、量比 0.71）原被当作右底低吸，次日 20261020 继续 -10.01%。
    #    跌停幅度按板块判定：主板 -9.5%、创业板/科创板 -19.0% 起算。
    row_end = df.iloc[end]
    if finite(row_end.pct_chg) <= -(limit_down_pct(row_end.get("ts_code")) - 0.5):
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
    # ③b 二波放量（20261010 用户口径）：L1→M 中间反弹段须有放量承接（段内最大量比 ≥ RIGHT_BOTTOM_REBOUND_VOLR），
    #     否则即「二波量能太少」的缩量反抽 → 双底失效，直接剔除。
    v20 = safe_mean(df.vol.iloc[max(0, end - 20):end])
    if v20 <= 0:
        return False, None
    seg_vol = df.vol.iloc[l1:m + 1].to_numpy(dtype=float)
    seg_vol = seg_vol[np.isfinite(seg_vol)]
    if seg_vol.size == 0 or seg_vol.max() < v20 * RIGHT_BOTTOM_REBOUND_VOLR:
        return False, None
    if lows[end] > lows[max(0, end - RIGHT_BOTTOM_NEAR + 1):end + 1].min() + 1e-9:
        return False, None
    if lows[end] < lows[l1] * (1 - RIGHT_BOTTOM_TOL_LO):
        return False, None
    if lows[end] > lows[l1] * (1 + RIGHT_BOTTOM_TOL_HI):
        return False, None
    if highs[m] <= 0 or 1 - lows[end] / highs[m] < RIGHT_BOTTOM_RETRACE:
        return False, None
    if finite(df.iloc[end].vol) > v20 * RIGHT_BOTTOM_VOL_DRYUP:
        return False, None
    ma10 = finite(df.iloc[end].ma_bfq_10, 0.0)
    ma60 = finite(df.iloc[end].ma_bfq_60, 0.0)
    ma60_prev = finite(df.iloc[max(0, end - 5)].ma_bfq_60, 0.0)
    close = finite(df.iloc[end].close, 0.0)
    if ma60 > 0 and (close < ma60 or (ma60_prev > 0 and ma60 / ma60_prev - 1 < RIGHT_BOTTOM_MA60_FLAT)):
        return False, None
    if ma10 > 0 and close > ma10:
        return False, None
    # ⑩ 出货尾部否决（20261023）：T0 之后连续放量根数过多＝筹码仍在出逃，后面的右底是下跌中继而非洗盘。
    start = event_idx if (event_idx is not None and 0 <= event_idx < end) else h0
    run = best = 0
    for i in range(start + 1, end + 1):
        m20 = safe_mean(df.vol.iloc[max(0, i - 20):i])
        if m20 > 0 and finite(df.vol.iloc[i]) >= m20 * RIGHT_BOTTOM_CHURN_VOLR:
            run += 1
            best = max(best, run)
        else:
            run = 0
    if best > RIGHT_BOTTOM_MAX_CHURN_RUN:
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


def big_bar_alt(df, event_idx, end):
    """20261008 用户口径：T0(event_idx) 到买点日(end) 之间须「大阴线/大阳线交织」。

    只考察中间段（T0 之后、买点当日之前，不含两端）：至少各有 1 根大阳线（pct_chg ≥ BIG_BAR_PCT）
    与 1 根大阴线（pct_chg ≤ -BIG_BAR_PCT）；段长不足（≤2 根）无法交织时返回 False。
    """
    if end - event_idx <= 2:
        return False
    seg = df.iloc[event_idx + 1:end]
    if len(seg) == 0:
        return False
    pc = seg["pct_chg"].to_numpy(dtype=float)
    fin = pc[np.isfinite(pc)]
    if fin.size == 0:
        return False
    return bool((fin >= BIG_BAR_PCT).any()) and bool((fin <= -BIG_BAR_PCT).any())


def state_and_features(df, event_idx, event_percentile, end=None):
    end = len(df) - 1 if end is None else min(end, len(df) - 1)
    base = behavior_features(df, event_idx, event_percentile, end=end)
    if not base:
        return None
    event = df.iloc[event_idx]
    event_close = base["event_close"]
    core = base["core"]
    post = df.iloc[event_idx + 1:end + 1]
    # 20261008 用户口径：T0 到买点当日之间须大阴线/大阳线交织（仅约束下方 T0 锚定买点分支）
    alt_ok = big_bar_alt(df, event_idx, end)
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
    # ===== 20261028 用户口径：信号归纳为两类（T0 锚定统一口径）=====
    # 旧 5 个缩量低吸形态（BREAKOUT_RETEST/MIDLINE_HOLD/SHAKEOUT_HOLD/RIGHT_BOTTOM/SQUAT_SHRINK）
    # 各以不同价位为锚、彼此重叠，且全部只吃低吸、不产生「放量再度突破」，与「W7 二波」命名错位。
    # 统一为两类，二者共用同一个 T0（量能放大激活日）的两个价位：
    #   类型A T0_PULLBACK_HOLD「缩量回踩至 T0 开盘价上方 + 止跌信号」→ 低吸买点（现价即买点区）
    #   类型B T0_REBREAK「回踩后突破 T0 日收盘价」→ 二波买点（突破当日即买）
    # 判定优先级：类型B（突破确认）> 类型A（回踩止跌）。
    # 说明：旧 5 个形态的判定函数（breakout_retest/midline_hold/shakeout_hold/right_bottom/
    # squat_shrink）仍保留在模块内，供 hvt_bull/daily.py 独立扫描与历史回测复用，
    # 但不再进入 W7 主链的状态判定——避免同一标的被重复归入多个低吸形态。
    t0_ph, t0_ph_info = (False, None)
    t0_rb, t0_rb_info = (False, None)
    if not persistent_sell:
        t0_rb, t0_rb_info = t0_rebreak(df, event_idx, end)
        if not t0_rb:
            t0_ph, t0_ph_info = t0_pullback_hold(df, event_idx, end)
    if t0_rb_info:
        base.update(t0_rb_info)
    if t0_ph_info:
        base.update(t0_ph_info)
    base["breakout_now"] = breakout_now
    if len(post) < 5 and not t0_hold and not t0_ph and not t0_rb:
        # V4.2 放宽：事件太新不判死，仅持续放量抛压标记观察；其余等待验证期
        state = "FAILED" if persistent_sell else "EXTREME_CHURN"
    elif t0_rb:
        # 20261028 类型B「回踩后突破 T0 日收盘价」：缩量回踩守住 T0 起点后，放量收盘突破 T0 收盘价＝二波确认
        state = "T0_REBREAK"
    elif t0_ph:
        # 20261028 类型A「缩量回踩至 T0 开盘价上方 + 止跌信号」：现价即买点区，失效位＝T0 开盘价
        state = "T0_PULLBACK_HOLD"
    elif breakout and len(post) >= 5 and (pp_ok or reexp) and base["lock"] >= 70 and alt_ok:
        state = "SECOND_WAVE"
    elif (breakout or reexp) and alt_ok:
        # V4.2 修复突破：重新放量突破压力位即算有效信号（优先于 FAILED/DISTRIBUTION）
        state = "BREAKOUT_CONFIRM" if breakout else "RE_EXPANSION"
    elif t0_hold and alt_ok:
        # 20260909：T0 天量锚定 + 重夺站稳≥2根 → 确认日当日有效买点（旁路等待期/派发误判）
        state = "T0_CONFIRM"
    elif breakout_now and alt_ok:
        # 20260914：放量突破日当日（强势阳线≥5% + 量≥1.3×20日均量 + 站上 T0 收盘线/近10日新高）
        state = "BREAKOUT_CONFIRM"
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
    if state == "T0_REBREAK" and t0_rb_info:
        # 类型B：当日已放量突破 T0 日收盘价 → 触发价＝T0 收盘价（突破位）；
        # 防线＝突破前回踩最低价（跌破即突破失败）。现价 ≥ 触发价 → 直接进执行区。
        pressure = finite(t0_rb_info.get("t0_close"), pressure)
    elif state == "T0_PULLBACK_HOLD" and t0_ph_info:
        # 类型A：现价即买点区（低吸），触发价＝T0 收盘价（向上突破目标位），
        # 防线＝T0 开盘价（跌破即本轮激活失效）。
        pressure = finite(t0_ph_info.get("t0_close"), pressure)
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
    if state == "T0_PULLBACK_HOLD":
        # 20261028 类型A 缩量回踩止跌低吸：T0 锚定但买点位于回踩低位，
        # dist_risk（天量后量价派发）在本形态上系统性偏高（回踩期缩量反而被计为派发），
        # 故改按生命周期涨幅定档（同旧 RIGHT_BOTTOM / SHAKEOUT_HOLD 的处理）。
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


def horizon_phases(tp, lc, breakout, t0_pullback=False, t0_rebreak=False):
    """V5 四周期预期阶段文本（T+10/20/60/120）

    20261028 统一两类口径：
      t0_pullback（类型A 缩量回踩止跌低吸）：T+10 看回踩后能否收复 T0 收盘价（＝转二波）；
      t0_rebreak（类型B 回踩后突破 T0 收盘价）：T+10 看二波加速，T+20 守回踩低点。
    """
    if tp == "DISTRIBUTION":
        return {"t10": "回避，观察派发确认", "t20": "回避", "t60": "回避", "t120": "回避"}
    lv = lc["level"]
    if t0_rebreak:
        t10, t20 = "二波突破T0收盘价，放量站稳即持有", "跌破回踩低点离场"
    elif t0_pullback:
        t10, t20 = "回踩止跌低吸，收复T0收盘价＝转二波", "收盘跌破T0开盘价离场"
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


def is_preshift_bar(df, i):
    """是否为「起涨放量长阳」：收阳 + 涨幅≥T0_PRESHIFT_PCT + 量能按涨幅分档达标。

    20261028：量能门槛分档（≥10% 的涨停/准涨停级长阳只需 2.0×，7%~10% 仍需 3.0×），
    避免把涨停起涨日（如 301550 0811，2.31×）漏掉、反而把次日高位换手日锚成 T0。
    """
    row = df.iloc[i]
    pct = finite(row.pct_chg)
    if finite(row.close) <= finite(row.open) or pct < T0_PRESHIFT_PCT:
        return False
    v20 = safe_mean(df.vol.iloc[max(0, i - 19):i])
    need = T0_PRESHIFT_VOLR_STRONG if pct >= T0_PRESHIFT_PCT_STRONG else T0_PRESHIFT_VOLR
    return v20 > 0 and finite(row.vol) >= v20 * need


def preshift_start_bar(df, event_idx):
    """20261023 用户口径：把 T0 由 P99 天量日前移到本轮上涨的起涨放量长阳日。

    本轮起点 = 天量日前 T0_PIVOT_BARS 根内的最低价那根；自起点起取第一根
    「收阳 + 涨幅≥T0_PRESHIFT_PCT + 量能分档达标」的长阳，
    找不到则沿用原天量日（不改变既有口径）。
    """
    lo = max(0, event_idx - T0_PIVOT_BARS)
    if event_idx - lo < 5:
        return event_idx
    pivot = min(range(lo, event_idx), key=lambda i: finite(df.iloc[i].low))
    for i in range(pivot, event_idx):
        if is_preshift_bar(df, i):
            return i
    return event_idx


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
    # 20261023：再前移到簇前 ≤5 根内的起涨放量长阳（P99 天量常落在加速末端）
    ei, ep = extreme_cluster_anchor(df, candidates)
    return preshift_start_bar(df, ei), ep


def find_activation_anchor(df, max_age=60):
    """找「量能放大激活日」参照锚（T0 兜底锚）：最近一根「收阳 + 涨幅≥T0_PRESHIFT_PCT +
    量能按涨幅分档达标」的放量长阳，且距今 ≤ max_age 根。

    用途（20261028 P0 修复）：现行 analyze() 入口门要求存在 P99 天量事件，否则直接 return None，
    导致「从未出现 P99 天量但确有放量激活」的个股（如凡拓数创 301313.SZ 在 20261015 之前
    120 日最大量分位仅 95.8）永远进不了池——名义上「不依赖天量锚」的形态实际不可达。
    本函数把这类个股的「放量起涨长阳」作为参照锚（event_percentile=0，不冒充天量），
    使两类统一信号（类型A/类型B）在无 P99 天量时同样可被判定。
    返回 (bar_index, 0.0) 或 None。
    """
    n = len(df)
    if n < 30:
        return None
    end = n - 1
    lo = max(21, end - max_age)
    for j in range(end, lo - 1, -1):
        if not is_preshift_bar(df, j):
            continue
        return j, 0.0
    return None


def analyze(code, name, industry, df, anchors, reader=None, mkt=None, sector_strength=None, sector_growth=None,
            event_hint=None, ige_adj=None):
    if len(df) < MIN_BARS:
        return None
    if event_hint is not None:
        # 盘中预检:同一交易日复用已缓存的 T0 锚,跳过昂贵的 60 日极端事件重扫
        event_idx, ep = event_hint
        fallback_anchor = False
    else:
        anchor = find_event_anchor(df)
        fallback_anchor = False
        if not anchor:
            # 20261028 P0 修复：两类统一信号共用 T0 锚。天量 P99 事件不存在时，
            # 退化为「放量激活日（起涨放量长阳）」参照锚（event_percentile=0，不冒充天量），
            # 使「量能放大激活 → 缩量回踩 / 回踩后突破」这一主形态在无 P99 个股上同样可判定；
            # 原实现依赖 shakeout_hold 成立才放行，等于把入口门锁死在单一形态上。
            anchor = find_activation_anchor(df)
            if not anchor:
                return None
            fallback_anchor = True
        event_idx, ep = anchor
    result = state_and_features(df, event_idx, ep)
    if not result:
        return None
    base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure = result
    if fallback_anchor:
        # 参照锚非天量事件：报告/下游不得把它当 T0 天量读（质量门④条对兜底锚本就豁免）
        base["event_is_fallback"] = True
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
    # 20261027：SHAKEOUT_HOLD 专用量比（含当日的20日均量，与 shakeout_hold 的 SHAKEOUT_VR20_HI 同口径）
    vol20_incl = safe_mean(df.vol.iloc[max(0, last - 19):last + 1])
    shake_vr20 = finite(latest_bar.vol, 0.0) / vol20_incl if vol20_incl > 0 else 0.0
    fallback = "CONFIRMED" if (breakout or reexp or dims["trend"] >= 70 or tp in ("MID", "EXT")) else "WATCH"
    status = w7_assign_status(state, tp, ige_adj, volr, fallback)
    # V5：C榜低分兜底票不输出。判据沿用「旧 score 定级下的 WATCH」定义（与 V5.3 三因子定级解耦），
    # 逐条复刻改版前的剔除条件：派发态，或非买点态且趋势/类型兜底为 WATCH，或买点态 score<70。
    # 不能简化为 `tp=="DISTRIBUTION" or score<70`——那会把非买点态的低分 MID/EXT 一并剔除，收缩候选池。
    legacy_watch = ((tp == "DISTRIBUTION")
                    or (state not in ACTION_BUY_STATES and fallback == "WATCH")
                    or (state in ACTION_BUY_STATES and score < 70.0))
    if state in ("T0_PULLBACK_HOLD", "T0_REBREAK"):
        # 20261028 统一两类信号：HVT-V3 分族（天量/锁筹/吸收/平台压缩）对「T0 锚定的回踩/突破」
        # 这类结构天然给低分（回踩期回撤会压低天量维度），故不套用「买点态 score<70」的低分剔除；
        # 但不能像旧 RIGHT_BOTTOM/SHAKEOUT 那样完全无条件豁免——那会让万凯新材这类
        # score=15.5 的票凭形态入选。改为「BaseScore ≥ W7_T0_MIN_BASE_SCORE」的硬底线，
        # 底线之上仍由两类信号的十条/九条硬条件 + w7_quality_gate 共同把关。
        legacy_watch = False
    if legacy_watch and base_score < WATCH_MIN_SCORE:
        return None
    if state in ("T0_PULLBACK_HOLD", "T0_REBREAK") and base_score < W7_T0_MIN_BASE_SCORE:
        return None
    event_date = str(df.iloc[event_idx].trade_date)
    core = f"Ac{base['acceptance']:.0f}/CQ{base['cq']:.0f}/SIM{hvt:.0f}/回撤{drawdown * 100:.0f}%"
    ext_txt = f"涨幅{lc['extension'] * 100:.0f}%({lc['level']})"
    v5_dims = {"天量": dims["hvt"], "吸收": absorption, "生命周期": lifecycle_score(lc), "空间": fs, "加速": acc, "RS": dims["rs"], "基本面": dims["fina"]}
    if tp == "DISTRIBUTION":
        reason = f"派发风险(dist={dist_risk:.0f})暂不参与：{core}；{ext_txt}"
    elif state == "T0_PULLBACK_HOLD":
        reason = (f"类型A·缩量回踩止跌低吸（{base.get('t0_date', '')}量能激活日开{base.get('t0_open', 0):.2f}/"
                  f"收{base.get('t0_close', 0):.2f}，回踩至开{base.get('t0_open', 0):.2f}上方守住，"
                  f"当日{base.get('stall', '')}止跌，回踩量{base.get('pull_vol_ratio', 0):.2f}×T0量）："
                  f"{core}；{ext_txt}；ENTRY={entry:.0f}")
    elif state == "T0_REBREAK":
        reason = (f"类型B·回踩后突破T0收盘（{base.get('t0_date', '')}量能激活日收{base.get('t0_close', 0):.2f}，"
                  f"回踩至{base.get('pull_low', 0):.2f}不破开{base.get('t0_open', 0):.2f}后放量突破，"
                  f"突破量比{base.get('break_volr', 0):.2f}×20日均量）：{core}；{ext_txt}；ENTRY={entry:.0f}")
    elif state == "T0_CONFIRM":
        reason = (f"T0天量确认买点（{event_date}锚，重夺T0收盘站稳≥2日，收盘未破"
                  f"{base['event_close']:.2f}）：{core}；{ext_txt}；ENTRY={entry:.0f}")
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
    _anchor_txt = (f"起涨放量长阳@{event_date}（无天量锚，按量能激活日参照口径）" if fallback_anchor
                   else f"P{int(ep)}天量@{event_date}")
    explanation = (f"{_anchor_txt}，{ext_txt}，状态{state}，类型{tp}；"
                   f"HVT-V3={score:.0f}（天量{dims['hvt']:.0f}/吸收{absorption:.0f}/生命{lifecycle_score(lc):.0f}/空间{fs:.0f}/加速{acc:.0f}/RS{dims['rs']:.0f}/基本面{dims['fina']:.0f}），"
                   f"派发风险{dist_risk:.0f}，ENTRY={entry:.0f}，{'今日PP10成立并重新放量' if pp_ok else '尚未出现合格PP10'}。")
    # 20261028 统一两类信号的结构字段
    t0_open = finite(base.get("t0_open"), 0.0) or None      # 类型A 防线（T0 开盘价）
    t0_close = finite(base.get("t0_close"), 0.0) or None    # 类型B 触发价（T0 日收盘价）
    pull_low = finite(base.get("pull_low"), 0.0) or None    # 类型B 防线（回踩低点）
    # 20261028：当日成交额（千元，daily_cache.amount 口径）——流动性下限判定用
    amount = finite(df.iloc[last].get("amount"), 0.0) if "amount" in df.columns else 0.0
    return {"code": code, "name": name, "industry": industry or "未覆盖", "state": state,
            "close": finite(latest_bar.close, 0.0), "pressure": pressure,
            "ma20": finite(latest_bar.ma_bfq_20, 0.0), "volr": volr, "shake_vr20": shake_vr20,
            "amount": amount,
            "score": score, "base_score": base_score, "rank": rank, "type": tp,
            "level": lc["level"], "extension": lc["extension"], "extreme_extension": lc["extreme"],
            "t120": t120, "entry": entry, "entry_dims": entry_dims, "dims": dims, "v5_dims": v5_dims,
            "fs": fs, "acc": acc, "plat": plat, "dist_risk": dist_risk, "absorption": absorption, "penalty": penalty,
            "cq": base["cq"], "acceptance": base["acceptance"], "sds": base["sds"], "lock": base["lock"], "pp": pp,
            "hvt": hvt, "sim_zjxc": sim_a, "sim_hzxc": sim_b, "buy": status, "grade": grade(t120),
            "event_date": event_date, "event_percentile": ep, "event_open": base.get("event_open"), "reexpansion": reexp, "breakout": breakout,
            # 20261028 统一两类信号：T0 两个价位 + 回踩低点（失效位候选）
            "t0_open": t0_open, "t0_close": t0_close, "pull_low": pull_low,
            "t0_date": base.get("t0_date") or "",
            "t0_vol": finite(base.get("t0_vol"), 0.0),
            "pull_low_date": base.get("pull_low_date") or "",
            "pull_bars": int(finite(base.get("pull_bars"), 0.0)),
            "pull_vol_ratio": finite(base.get("pull_vol_ratio"), 0.0),
            "stall": base.get("stall") or "",
            "break_volr": finite(base.get("break_volr"), 0.0),
            "t0_ext": finite(base.get("t0_ext"), 0.0),
            "major_risk": major_risk, "hard_fail": major_risk, "reason": reason,
            "next": next_trigger(status, entry_dims, breakout, pp_ok), "explanation": explanation,
            "horizons": horizon_phases(tp, lc, breakout,
                                       t0_pullback=(state == "T0_PULLBACK_HOLD"),
                                       t0_rebreak=(state == "T0_REBREAK"))}


def w7_quality_gate(x):
    """20261028 统一两类信号质量门（返回 (是否通过, 未通过原因)）。

    门的构成（按执行顺序）：
      ① 状态排除：SECOND_WAVE / BREAKOUT_CONFIRM / RE_EXPANSION 历史弱，不再产可操作买点；
      ② 买点档剔除：w7_assign_status 定为 WATCH（三因子 0 命中）者不落库；
      ③ 执行状态硬门：仅 EXECUTION / EXECUTION_WAIT_VOLUME 进可操作榜，两类 *_WATCH 仅观察；
      ④ 流动性下限：当日成交额 < W7_T0_MIN_AMOUNT（5000 万元）的微盘票不落库——
         原实现对成交额零门槛，微盘/低流动性票可凭形态入选，实盘无法成交；
      ⑤ 市场环境门：BEAR（指数 r20<-6% 且 r60<-3%）时只保留类型B（放量突破确认），
         类型A（缩量回踩低吸）不产——弱市里低吸形态的 T+3/T+10 实测为负
         （202610 现役门控 55 个信号 T+3 -3.19%、同期上证 -4.29% 仍为负超额）；
         RANGE（震荡市）同样只保留类型B——标定显示 RANGE 下类型A T+10 超额 -1.58%、
         类型B -2.45%，而 RECOVERY 下类型A +0.23% / 类型B +0.53%（300 只样本 / 2024-2026）；
         BULL 下两类均为正（类型A +1.59%），全环境放行；
      ⑥ 结构内生门：类型A 的信号日收盘须 ≥ T0 开盘价、类型B 的收盘须 > T0 收盘价
         （两条均已内生于探测器，此处只做兜底复核，防止探测器口径被改动后失效）。
    报告「今日可操作榜」（markdown）与 stock_pick_db 落库共用本门，二者口径严格一致。"""
    if x.get("state") in W7_EXCLUDE_STATES:
        return False, f"状态{x['state']}历史弱（库内 T+3 -0.29%~+1.26%）"
    if x.get("state") in W7_GATE_DROP_STATES:
        return False, f"状态{x['state']}分类收益复核剔除（库内 T+3 -7.89%~-8.50%、盈亏比 ≤0.23）"
    if x.get("buy") in W7_GATE_DROP_BUY:
        return False, "买点档 WATCH（三因子 0 命中，库内 T+3 -4.39%、胜率 18.5%）"
    _st = w7_exec_status(x)[0]
    if _st not in W7_EXEC_KEEP:
        return False, f"执行状态{_st}（未进入价格/量能执行区，库内 T+3 负期望）"
    # ④ 流动性下限
    amt = x.get("amount")
    if isinstance(amt, (int, float)) and amt > 0 and amt < W7_T0_MIN_AMOUNT:
        return False, (f"当日成交额{amt / 10:.0f}万元 < {W7_T0_MIN_AMOUNT / 10:.0f}万元"
                       f"（流动性不足，实盘难成交）")
    # ⑤ 市场环境门
    if (x.get("market_regime") in W7_REGIME_BLOCK_TYPE_A
            and x.get("state") == "T0_PULLBACK_HOLD"):
        return False, (f"市场环境{x.get('market_regime')}（弱市低吸形态期望为负："
                       f"标定 T+10 超额 -1.58%，跟踪库 202610 T+3 -3.19%），只保留突破确认")
    # ⑥ 结构内生兜底
    st = x.get("state")
    if st == "T0_PULLBACK_HOLD":
        t0_open = finite(x.get("t0_open"), 0.0)
        if t0_open > 0 and finite(x.get("close"), 0.0) < t0_open:
            return False, (f"信号日收盘{x['close']:.2f}<T0开盘价{t0_open:.2f}"
                           f"（未站回量能激活日起点上方）")
    elif st == "T0_REBREAK":
        t0_close = finite(x.get("t0_close"), 0.0)
        if t0_close > 0 and finite(x.get("close"), 0.0) <= t0_close:
            return False, (f"信号日收盘{x['close']:.2f}≤T0收盘价{t0_close:.2f}"
                           f"（未突破量能激活日收盘）")
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
    # rb_extra：20261002 龙头池外的「右底低吸」信号（单独成节 + 落跟踪池）；20261028 统一两类
    #           信号后已无 RIGHT_BOTTOM，该参数保留仅为兼容旧调用方，恒为空。
    results = sorted(results, key=lambda x: (-x["rank"], x["code"]))
    ige_snap = next((str(r.get("ige_snap") or "") for r in results if r.get("ige_snap")), "")
    mkt_regime = next((str(r.get("market_regime") or "") for r in results if r.get("market_regime")), "")

    # 20261028 用户口径：当日买点＝两类统一信号
    #   类型A T0_PULLBACK_HOLD「缩量回踩至 T0 开盘价上方 + 止跌信号」→ 低吸买点（现价即买点区）
    #   类型B T0_REBREAK「回踩后突破 T0 日收盘价」→ 二波买点（突破当日即买）
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
    actionable_main = actionable
    n_rb_show = 0
    n_rb_off = 0
    # 高分等待池阈值（C 池仅列总分≥75，避免整池铺开）
    WAIT_TOP_SCORE = 75.0

    def _ige_tag(r):
        return f"{r['ige_adj']:.1f}" if isinstance(r.get("ige_adj"), (int, float)) else "-"
    n_core = sum(1 for x in results if x["type"] == "CORE")
    n_mid = sum(1 for x in results if x["type"] == "MID")
    n_ext = sum(1 for x in results if x["type"] == "EXT")
    n_dist = sum(1 for x in results if x["type"] == "DISTRIBUTION")
    n_typeA = sum(1 for x in actionable if x["state"] == "T0_PULLBACK_HOLD")
    n_typeB = sum(1 for x in actionable if x["state"] == "T0_REBREAK")
    lines = [f"# W7 二波·两类统一信号（今日可操作 · C池等待）\n\n交易日：{date}　|　候选总数：{len(results)}"]
    lines.append(f"类型分布：CORE={n_core}　MID={n_mid}　EXT={n_ext}　DISTRIBUTION={n_dist}（DISTRIBUTION=派发风险，仅观察不进可操作榜）")
    if mkt_regime:
        lines.append(f"市场环境：{mkt_regime}"
                     + (f"（{mkt_regime}：指数 r20/r60 判定。"
                        + ("弱市低吸形态期望为负（标定 T+10 超额 -1.58%），本日只保留类型B「回踩后突破 T0 收盘价」）"
                           if mkt_regime in W7_REGIME_BLOCK_TYPE_A else
                           "两类信号均可参与；震荡市下低吸形态历史偏弱（RANGE T+10 超额 -1.58%），建议控制仓位）")))
    lines.append(f"今日可操作（当日买点）＝ {n_action} 只：类型A 缩量回踩止跌低吸 {n_typeA} 只 + 类型B 回踩后突破T0收盘 {n_typeB} 只；"
                 f"其余 {len(waiting)} 只等待型仅入 C 池观察不逐列展示。")
    lines.append(f"质量过滤（全市场候选）：当日买点原始 {len(_buy_signal)} 只 → 通过 {n_action} 只、过滤 {n_gated} 只"
                 f"（剔除 SECOND_WAVE/BREAKOUT_CONFIRM/RE_EXPANSION 三态；"
                 f"成交额下限 {W7_T0_MIN_AMOUNT / 10:.0f} 万元（微盘票实盘难成交）；"
                 f"{'/'.join(W7_REGIME_BLOCK_TYPE_A)} 环境下类型A 不产（标定：弱市低吸 T+10 超额为负）；"
                 f"买点档 WATCH（三因子 0 命中）与非执行区（TRIGGER_WATCH/PULLBACK_WATCH）不落库。"
                 f"被过滤标的见文末「过滤观察」。）")
    cnt_state = {}
    for x in results:
        cnt_state[x["state"]] = cnt_state.get(x["state"], 0) + 1
    n_broken = cnt_state.get("T0_PULLBACK_HOLD", 0) + cnt_state.get("T0_REBREAK", 0)
    lines.append(f"状态分布：{'　'.join(f'{s}={c}' for s, c in sorted(cnt_state.items()))}　"
                 f"（两类统一信号合计 {n_broken} 家，"
                 f"T0_PULLBACK_HOLD=缩量回踩至T0开盘价上方且当日出现止跌信号（十字星/长下影/阳线收复），"
                 f"T0_REBREAK=回踩守住T0起点后放量收盘突破T0日收盘价）")
    lines.append("价格口径：现价/触发价/MA20均为元；触发价＝T0 日收盘价（两类统一，向上突破目标位）；"
                 "失效位：类型A＝T0 开盘价（跌破即本轮量能激活失效）、类型B＝突破前回踩低点（跌破即突破失败）；"
                 "放量(量比≥1.2)突破触发价＝类型B买点触发；类型A 为现价即买点区（止跌位低吸，不等触发价）；"
                 "MA20=总防线；量比=当日量/前20日均量（不含当日）")
    lines.append("")
    if ige_snap:
        lines.append(f"> 行业增长弹性 IGE_ADJ（申万三级行业，快照 {ige_snap}）：全部榜单已附 IGE_ADJ 列；「W7 二波·今日执行状态」按 W7_STATUS 优先、同级内按各状态自身口径（EXECUTION=量能确认程度／TRIGGER_WATCH=距触发最近／PULLBACK_WATCH=结构完整度）重排（20261001 起，执行状态优先于分数）；类型A（T0_PULLBACK_HOLD）不以距触发分档、直接归执行区并置 EXECUTION 组内最前（20261028 起）；其余榜单保留 HVT-V3 总分/Rank 原序仅加列标注。")
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
        if st == "EXECUTION" and x.get("state") == "T0_PULLBACK_HOLD":
            # 20261028 类型A：买点＝当日止跌位（现价即买点区），不等站上 T0 收盘价
            out += ["执行条件：",
                    f"- 类型A 缩量回踩止跌：现价{x['close']:.2f} 在 T0 开盘价 {x.get('t0_open') or 0:.2f} 上方"
                    f"（回踩最低 {x.get('pull_low') or 0:.2f}，未破激活起点）",
                    f"- 止跌信号：{x.get('stall') or '止跌形态'}（回踩段量能 {x.get('pull_vol_ratio') or 0:.2f}×T0量，"
                    f"当日量比×{x['volr']:.1f}）",
                    f"- 结构未破坏（收盘未跌破 T0 开盘价 {x['w7_fail_line']:.2f}）", "",
                    "操作：",
                    f"类型A 低吸买点，可按计划分批建仓；放量（量比≥{W7_VOL_CONFIRM:g}）站上 T0 收盘价 "
                    f"{x['pressure']:.2f}＝转二波突破（类型B），可加仓", "",
                    "失效：", _fail_txt(x)]
        elif st == "EXECUTION" and x.get("state") == "T0_REBREAK":
            # 20261028 类型B：买点＝放量突破 T0 收盘价当日
            out += ["执行条件：",
                    f"- 类型B 回踩后突破：现价{x['close']:.2f} > T0 收盘价 {x.get('t0_close') or 0:.2f}"
                    f"（放量突破，突破量比×{x.get('break_volr') or 0:.2f}）",
                    f"- 回踩守住 T0 起点：回踩最低 {x.get('pull_low') or 0:.2f} ≥ T0 开盘价 {x.get('t0_open') or 0:.2f}",
                    f"- 结构未破坏（收盘未跌破回踩低点 {x['w7_fail_line']:.2f}）", "",
                    "操作：",
                    f"类型B 二波突破买点，可按计划建仓；站稳 T0 收盘价 {x['pressure']:.2f} 之上持有，"
                    f"放量（量比≥{W7_VOL_CONFIRM:g}）续攻可加仓", "",
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
    # 20261028：原「④ 右底低吸信号」单列节已移除——右底低吸不再作为独立可操作信号，
    # 其「缩量回踩至 T0 开盘价上方 + 止跌」的结构由类型A（T0_PULLBACK_HOLD）统一承载。
    if actionable:
        lines.append(f"优先=V5.3 因子命中数（0~3）：type=MID（+13.6%/胜74% vs EXT +5.1%/62%，n=27）"
                     f"｜IGE_ADJ≥{W7_IGE_STRONG:g}（75-85 档 +21.9%/胜93%，n=15）"
                     f"｜量比<{W7_VOLR_STRONG:g}（0.4-0.6 档 +10.7%/胜69% vs 0.6-0.8 档 +4.6%/60%，n=42）。"
                     "「W7总分」（HVT-V3）仅表示候选质量，不参与执行状态判定与排序。")
        lines.append("")
        lines.append("操作口径：两类统一信号共用 T0 的两个价位——T0 开盘价＝回踩防线（跌破即本轮量能激活失效），"
                     "T0 日收盘价＝向上突破目标位。"
                     "类型A（T0_PULLBACK_HOLD）买点＝当日止跌位（现价即买点区），缩量回踩守住 T0 开盘价上方后出现止跌信号；"
                     "类型B（T0_REBREAK）买点＝放量收盘突破 T0 日收盘价当日，量比≥1.2 确认。"
                     "量比≥3 的巨量日不追、只等回踩。")
        lines.append("")
        lines.append("失效位（V5.3 修订）：类型A＝收盘跌破 T0 开盘价；类型B＝收盘跌破突破前回踩低点；"
                     "其余＝MA20（留 3% 容差，未超容差归 PULLBACK_WATCH）；"
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
    2) 落 stock_pick_db 跟踪表（strategy=w7_ab，20261028 起新口径独立策略 id），
       盘后由 stock_pick_db.py tracking 回填 T+N/胜率。
       （旧口径历史在 w7_hvt 下只读归档，不再写入，避免与新口径样本混淆）
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
    act_cn = {"T0_PULLBACK_HOLD": "类型A-缩量回踩止跌低吸",
              "T0_REBREAK": "类型B-回踩后突破T0收盘",
              "SECOND_WAVE": "二波买点", "BREAKOUT_CONFIRM": "放量突破确认",
              "RE_EXPANSION": "重新扩张", "T0_CONFIRM": "T0天量确认买点",
              "BREAKOUT_RETEST": "放量突破后缩量回踩买点", "MIDLINE_HOLD": "W7-不破中位",
              "SHAKEOUT_HOLD": "W7-起涨缩量回踩",
              "RIGHT_BOTTOM": "W7-右底低吸",
              "SQUAT_SHRINK": "W7-缩量十字星下蹲"}
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
            # 20261027：SHAKEOUT_HOLD 专用量比（含当日20日均量），下游复盘须能核验 ≤1.2 这条硬条件
            "shake_vr20": round(float(x.get("shake_vr20") or 0), 3),
            # 20261028 统一两类信号：T0 两个价位 + 回踩低点 + 止跌形态 + 成交额 + 市场环境
            "t0_open": x.get("t0_open"), "t0_close": x.get("t0_close"),
            "t0_date": x.get("t0_date"),
            "pull_low": x.get("pull_low"), "pull_low_date": x.get("pull_low_date"),
            "pull_bars": x.get("pull_bars"),
            "pull_vol_ratio": round(float(x.get("pull_vol_ratio") or 0), 3),
            "stall": x.get("stall"), "break_volr": round(float(x.get("break_volr") or 0), 3),
            "amount": x.get("amount"), "market_regime": x.get("market_regime"),
            "retest_low": x.get("retest_low"), "mid_line": x.get("mid_line"),
            "shake_line": x.get("shake_line"),
            "rb_left_low": x.get("rb_left_low"), "rb_offpool": bool(x.get("rb_offpool")),
            "squat_low": x.get("squat_low"), "squat_pk_date": x.get("squat_pk_date"),
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
    if record_picks is None:
        return
    try:
        rows = []
        for idx, x in enumerate(signals, 1):
            # stop_price(失效位) V5.3 修订：结构位(触发价/MA20/放量突破日低点)取最宽者 + clamp 距现价[5%,8%]。
            # 原口径直接取触发价，仅在已突破时生效且距现价中位仅 2.7%，导致 2~3% 插针即触发 STOP_HIT。
            # 新口径恒低于现价 ≥5%，无需再区分是否已突破。
            # 20261028：新增 T0 开盘价（类型A 防线）与回踩低点（类型B 防线）两个结构位候选。
            rows.append({
                "ts_code": x["code"], "stock_name": x["name"], "industry": x.get("industry"),
                "close": x["close"], "signal": x["state"], "action": f"{x['state_cn']}·{x['action']}",
                "score": x["score"], "rank_no": idx,
                "stop_price": w7_stop_price(x["close"], x["pressure"], x["ma20"], x.get("retest_low"),
                                            x.get("mid_line"), x.get("rb_left_low"), x.get("squat_low"),
                                            t0_open=x.get("t0_open"), pull_low=x.get("pull_low")),
                "reason": x["reason"],
                "state": x["state"], "type": x["type"], "level": x["level"], "entry": x["entry"],
                "volr": x["volr"], "ma20": x["ma20"], "event_date": x["event_date"],
                "ige_adj": x["ige_adj"], "t120": x["t120"],
                # 20261028 统一两类信号：结构字段随 indicators 落库，便于跟踪池按类型分组复盘
                "t0_open": x.get("t0_open"), "t0_close": x.get("t0_close"),
                "t0_date": x.get("t0_date"),
                "pull_low": x.get("pull_low"), "pull_low_date": x.get("pull_low_date"),
                "pull_bars": x.get("pull_bars"),
                "pull_vol_ratio": x.get("pull_vol_ratio"),
                "stall": x.get("stall"), "break_volr": x.get("break_volr"),
                "amount": x.get("amount"), "market_regime": x.get("market_regime"),
                # 20261002：右底低吸池外标记（不受龙头池限制），随 indicators 一并落库便于复核
                "rb_offpool": bool(x.get("rb_offpool")),
                # 20261001 三状态执行分层：非 STD_COLS 字段由 record_picks 自动打包进 stock_pick.indicators，
                # 使跟踪池可按「入场执行状态」回测分组胜率（不改表结构、不改 tracking 回填逻辑）。
                "w7_status": x["w7_status"], "distance_to_trigger": x["distance_to_trigger"],
                "w7_fail_line": x["w7_fail_line"], "w7_fail_name": x["w7_fail_name"],
            })
        # 20261014：replace=True —— 当日信号池会缩小的场景（新增 A/B/C 三道剔除门后重跑同一天，
        # 被剔除标的须从 stock_pick 与 pick_tracking 一并清除，否则仍残留在库/网页）。
        # 20261028 P0 修复：原实现在 `not signals` 时提前 return，导致 replace=True 从不生效——
        # 20261027 当日 JSON count=0 而 DB 仍残留 18:24 旧口径的 4 行，口径漂移。
        # 现在空池也调用 record_picks（replace=True 且 rows 为空 = 完整替换为当日空集，
        # record_picks 会清空该 (pick_date, strategy_id) 下的旧记录）。
        n = record_picks(W7_AB_STRATEGY_ID, W7_AB_STRATEGY_NAME, rows, pick_date=date, replace=True)
        print(f"[w7] stock_pick_db 写入 {n}/{len(rows)} 条 (strategy={W7_AB_STRATEGY_ID} pick_date={date})", flush=True)
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
    # 20261028 P0 修复：market_regime 此前是死代码（无任何调用点）。
    # 现接入质量门——BEAR（指数 r20<-6% 且 r60<-3%）时低吸形态期望为负，只保留突破确认。
    mkt_regime = market_regime(reader.conn, date)
    print(f"[w7] 市场环境={mkt_regime}", flush=True)
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
    # 20261008 用户口径：删除 SLI 龙头池阀门条件（含热点主题扩池）——全市场标的均进入分析，不再按票池过滤。
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
            result["market_regime"] = mkt_regime  # 20261028：接入质量门的市场环境判定
        return result

    t_start = time.time()
    for n, row in enumerate(rows):
        if n and n % 500 == 0:
            print(f"[w7] 分析进度 {n}/{len(rows)} 耗时={time.time()-t_start:.1f}s", flush=True)
        result = _analyze_row(row)
        if result:
            results.append(result)
    # 20261008 用户口径：删除 SLI 龙头池阀门后全市场标的均在 results 内，
    # 右底低吸（RIGHT_BOTTOM）由常规通道产出、报告「④ 右底低吸信号」节统一列示。
    text = markdown(results, date)
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
        sync_downstream(date, results, output)
    reader.close()


if __name__ == "__main__":
    main()
