# -*- coding: utf-8 -*-
"""
EDB（Extreme Dry-up Breakout）极致缩量→爆量突破 选股引擎 V1.0 —— 配置
================================================================================
全部阈值集中于此，禁止散落硬编码。

模型定位（不是「放量突破指标」）：
    时间压缩 + 价格压缩 + 成交量压缩 + 需求突然释放 + 平台有效突破 + 突破位置不过度

最核心组合：
    BaseDays>=60 + DryUpRatio<=0.45 + Range60<=25% + BreakoutVR>=2.0
    + Close>PlatformHigh + ClosePosition>=0.75
强信号组合：
    BaseDays>=90 + DryUpRatio<=0.35 + Range60<=18% + BreakoutVR>=2.5
    + BreakoutDistance<=5% + ClosePosition>=0.80
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)

# ── 数据源路径（全部复用项目既有缓存）──
CACHE_DIR = r"D:\mystock\cache_daily"
CACHE_DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
STOCK_BASIC_CSV = os.path.join(CACHE_DIR, "stock_basic.csv")
FIN_IND_PARQUET = os.path.join(CACHE_DIR, "fin_ind_2026H1_full.parquet")
# HVT 结构层（7 态归一化词表）产物；legacy 14 态已在此归一
HVT_SCORE_CSV = os.path.join(SOLO_DIR, "sector_0915", "data", "stock_structure_score.csv")
# 事件标记本地缓存（业绩预告/快报/大股东减持）
EVENT_CACHE = os.path.join(CACHE_DIR, "edb_event_flags.parquet")
# 输出目录
REPORT_DIR = os.path.join(SOLO_DIR, "output", "edb")


EDB_CONFIG = {
    # ═════════════════════════════════════════════════
    # 一、数据要求与股票池基础过滤
    # ═════════════════════════════════════════════════
    'lookback_bars': 300,          # 单股回看最大K线数（支撑 BaseDays 250 + MA120）
    'min_bars': 140,               # 最少K线数（MA120 + HH120 + 平台预热）
    'min_amount_yi_20': 0.20,      # 近20日均成交额下限（亿元），流动性阀
    'exclude_st': True,
    'exclude_bj': True,            # 北交所不做
    'one_line_amp': 0.005,         # 一字板振幅阈值 (high-low)/close
    'consec_limit_min': 2,         # 连续涨停板数下限 → EVENT_ONLY

    # ═════════════════════════════════════════════════
    # 二、Base：长期横盘周期
    # ═════════════════════════════════════════════════
    'base_candidates': (250, 220, 200, 180, 150, 120, 90, 60),  # 由长到短取最长有效窗口
    'base_days_min': 60,           # 横盘最短
    'base_days_prefer': 90,        # 优先
    'base_range_max': 0.25,        # 正常横盘
    'base_range_strong': 0.20,     # 强横盘
    'base_range_very_strong': 0.18,
    'base_range_extreme': 0.15,    # 极致压缩
    # 横盘有效性（防止「从高位持续下跌 + 缩量」被误判为横盘）
    'base_ma60_slope_min': -0.06,  # MA60 的20日斜率下限
    'base_dd20_min': -0.20,        # 近20日最大回撤下限
    'base_down_days_max': 14,      # 近20日下跌天数上限

    # ═════════════════════════════════════════════════
    # 三、量能极致收缩
    # ═════════════════════════════════════════════════
    'dry_up_none': 0.70,           # 无明显缩量
    'dry_up_normal': 0.65,         # 普通缩量（= 引擎最宽 dry 准入边界，回测预筛同源）
    'dry_up_obvious': 0.45,        # 明显缩量
    'dry_up_extreme': 0.35,        # 极致缩量（强信号）
    'dry_up_pass': 0.45,           # 核心门槛
    'dry_up_strong': 0.35,         # 强信号门槛
    # 持续缩量确认（不能只看一天）
    'persist_days': 10,
    'persist_thr_pass': 0.50,      # VOL/MA120 < 0.50
    'persist_cnt_pass': 6,         # 至少6日
    'persist_thr_strong': 0.45,
    'persist_cnt_strong': 7,       # 强：至少7日 <0.45
    'minvol10_pass': 0.35,         # MinVOL10/MA120 要求
    'minvol10_strong': 0.30,       # 强

    # ═════════════════════════════════════════════════
    # 四、价格压缩
    # ═════════════════════════════════════════════════
    'range60_max': 0.25,
    'range60_strong': 0.18,
    'range60_extreme': 0.15,
    'atr_ratio_max': 0.80,         # ATR20/ATR120
    'atr_ratio_strong': 0.70,

    # ═════════════════════════════════════════════════
    # 五、平台高点与突破位
    # ═════════════════════════════════════════════════
    'platform_window_short': 60,   # HH60
    'platform_window_long': 90,    # HH90；PlatformHigh = max(HH60, HH90)
    # PRE_EDB 平台接近度：Close 距 PlatformHigh 不超过该比例（防止「远低于失效高点」被判为即将突破）
    'pre_max_gap': 0.10,

    # ═════════════════════════════════════════════════
    # 六、爆量突破
    # ═════════════════════════════════════════════════
    'vr_normal': 1.5,              # 普通放量
    'vr_valid': 1.6,               # 有效放量 / 核心 EDB 门槛
    'vr_strong': 2.5,              # EDB 强爆量
    'vr_extreme': 3.0,             # EDB 极强爆量
    'vr_climax': 5.0,              # 天量/高潮警戒（超过不再加分）
    'vr_climax_hard': 7.0,
    # 突破位置
    'bd_ideal': (0.0, 0.05),       # 最佳启动区
    'bd_allow_low': -0.02,         # 允许下沿（临界/假突破观察）
    'bd_allow_high': 0.08,         # 允许上沿
    'bd_max': 0.10,                # 原则上不进入主买入池
    'bd_penalty': 0.08,            # 超过 8% 起扣
    # 突破日涨幅
    'day_gain_min': 4.0,
    'day_gain_strong': 6.0,
    'day_gain_ext': 9.0,           # EXTENSION_RISK 触发
    # 收盘质量
    'cp_min': 0.75,
    'cp_strong': 0.85,
    'cp_weak': 0.60,

    # ═════════════════════════════════════════════════
    # 七、趋势过滤 / 死猫跳
    # ═════════════════════════════════════════════════
    'ma60_slope_flat': -0.01,      # MA60 走平/向上阈值
    'ma60_slope_down': -0.03,      # MA60 明显下降
    'down_trend_penalty': -8,
    'ret20_no_drop': -0.10,        # 突破前20日无明显下跌
    'dead_cat_ret20': -0.15,       # DEAD_CAT_RISK
    'dead_cat_penalty': -8,
    'dead_cat_soft_penalty': -3,   # -10% ~ -15% 降分
    'near_52w_high': -0.05,        # 距250日高点 <5% → NEAR_52W_HIGH

    # ═════════════════════════════════════════════════
    # 八、突破后的二次机会
    # ═════════════════════════════════════════════════
    'recent_breakout_window': 10,  # 回看最近突破日窗口
    'pullback_window': 5,          # EDB_PULLBACK：D+1~D+5
    'pullback_vol_shrink': 0.75,   # 回踩段均量/突破日量 上限
    'pullback_hold': 0.98,         # Close >= PlatformHigh*0.98
    'pullback_floor': 0.95,        # 平台不破下限
    'rebreakout_vol_ratio': 1.5,   # VOL > 1.5 × MA5
    # ── EDB_PULLBACK 实盘化 V1.3（回测明细 EDB_PULLBACK 行 n=31 全期诊断；
    #    IS 20240601~20251231 / OOS 2026，闸门参数仅用 IS 推导、OOS 仅验证）──
    # 现状缺口：_classify_after 结构条件已含「回踩段均量<0.75×突破日量 + 平台
    #   不破(≥0.95×ph 且现收≥0.98×ph)」，但 _action 对 EDB_PULLBACK 无条件输出
    #   BUY_ON_PULLBACK —— 好坏回踩不区分。
    # 全期基线（n=31，fut 统计基于可评估子集）：fut3 +2.17%/w40 · fut5 +4.29%/w50
    #   · fut10 +5.14%/w63 · fut20 +12.21%/w77；IS n=25 fut20 +9.19%/w80，
    #   OOS n=6 fut20 +27.29%/w60（OOS 最强层，见上文 walk-forward 注）。
    # [证伪·score 闸门] IS 加 score_adj≥70 反而变差（fut20 +8.55% < 全量 +9.19%）；
    #   score 分档无单调性（[0,65) 档胜率 100%）→ 不采用。
    # [证伪·VR 闸门] 天量（VR>5）回踩 fut20 +18.33%/w64 不差于 VR≤5 档；OOS 最强
    #   样本民爆光电（fut20 +142.06%）恰为 VR 5.33 天量 → 数据验证「等回踩本来就是
    #   应对天量突破的正解」（见下 climax 闸门注），VR 闸门只会误杀 → 不采用。
    # [采用·BreakoutDistance 闸门 pb_bd_reject=0.02] 机制：突破日收盘已高于平台
    #   ≥2% 的「回踩」并未真正回到承接位。IS：bd<0.02 n=17 fut5 +2.40%/w65 ·
    #   fut20 +9.74%/w88；bd≥0.02 n=8 fut5 -2.54%/w25。OOS：bd<0.02 n=4 fut5
    #   +27.90%/w50 · fut20 +33.75%/w50；bd≥0.02 n=2 fut5 -3.30%/w0 —— 双窗一致。
    #   全期 bd∈[0.02,0.05) n=9 fut3/5/10 全负（-4.11%/-3.27%/-2.33%）。
    #   边界敏感性：bd≥0.010/0.015/0.020/0.025/0.030 五档 IS fut5 单调走弱
    #   （-0.29/-1.57/-2.54/-2.74/-2.81），0.02 处平台区；IS 半窗拆分方向一致
    #   （25H2：bd<0.02 n=15 +9.14%/w87 vs bd≥0.02 n=7 fut5 -2.74%/w29）；
    #   与 EXT/CLIMAX 标签重叠但不等同（corr(bd, DayGain)=0.39~0.44 中度相关）。
    # [标注·核心回踩 pb_dry_core=0.45] dry≤0.45 档两窗一致走强：IS n=10 fut20
    #   +10.48%/w70，OOS n=4 +33.75%/w50 → 仅在报告明细标注「核心回踩」档位，
    #   不新增状态、不改分层、不改回测口径。
    # 风险警示：闸门两侧样本 IS n=17/8、OOS n=4/2，远低于显著性门槛（≥30），
    #   属方向性质量过滤；改动仅作用于建议动作层（_action），status 不变
    #   —— 回测 fut 证据按 status 键控，无需重跑回测。
    'pb_bd_reject': 0.02,          # EDB_PULLBACK 质量闸门：BreakoutDistance≥2% → WAIT_DEEPER_PULLBACK
    'pb_dry_core': 0.45,           # EDB_PULLBACK 核心档标注：DryUpRatio≤0.45

    # ═════════════════════════════════════════════════
    # 九、分层门槛
    # ═════════════════════════════════════════════════
    # ── 主池扩容 V1.1（回测驱动，区间 20240601~20260918，突破日 n=26）──
    # 瓶颈诊断：突破日 EDB_WATCH n=22 中 16 只死于 dry>0.45（主瓶颈）、
    #   1 只死于 score<70、5 只死于硬排除闸门；24/26 样本 base_days=60 → A 项 8/15，
    #   score 天然难上 70（A 项天花板，本轮不动以免多重改动无法归因）。
    # edb_score 70→65：score[65,70) 未命中闸门样本 n=4，T+20 +37.25% / 胜率 50%。
    # edb_dry_up_max（EDB 层准入 0.45→0.55）：dry(0.45,0.55] & score≥70 未命中闸门
    #   样本 n=4，T+20 +10.63% / 胜率 75% / PF 15.09，2024 +4.10% / 2025 +12.81% 均为正；
    #   C 项在该档自动降至 10 分（评分体系天然对宽档压分），无需额外惩罚。
    #   EDB_STRONG 仍要求 dry≤dry_up_pass(0.45)，核心「极致缩量」定义不变。
    # 风险警示：各桶 n≤8，远低于统计显著性门槛（≥30），属方向性放宽；
    #   blocked 桶（n=13，T+20 -5.62% / PF 0.23）维持硬排除不变。
    # ── 评分标尺校准 V1.2（V1.1 基线再诊断，单杠杆归因）──
    # A 项 ≥60 档 8→10（engine._score）：突破日 36 行中 32 行 base_days∈[60,90)
    #   （>150 行 n=0），8/15=53% 得分率属体系性压分。base_days 逐行精确模拟 adj+2：
    #   莱茵生物 20240926 adj 64→66（dry 0.539 宽档 / VR 2.02 / CP 1.0）T+20 +12.10%；
    #   四川美丰 20260828 adj 78→80（dry 0.421 核心档 / VR 2.74 / CP 0.935）晋级
    #   EDB_STRONG（数据末端 fut20 缺失，收益未验证）。
    # 同轮证伪项（维持不动）：入口闸门 dry>0.55 的 8 行全为 EVENT_ONLY（不可买），
    #   可评估的 dry(0.55,0.70] n=2 T+20 均为负；WATCH 池 17 只中 16 只为 VR>5 硬
    #   排除样本，T+20 -4.07% / 胜率 37.5% —— climax 闸门被负收益持续验证；
    #   edb_score 再降（62/60）无增量：[60,65) 未命中闸门样本仅莱茵生物 1 只。
    # ── Walk-forward 样本外验证（训练窗 IS 20240601~20251231 / 测试窗 OOS 2026）──
    # 方法：仅用 IS 数据复现 V1.1→V1.2 全部参数决策，再用 OOS 检验；OOS 收益不参与任何调参。
    # [复现成功·无前视泄漏] A 项压分证据 IS-only 即成立：IS 突破行 n=127，
    #   112 只（88%）base_days∈[60,90)、≥150 行 n=0（OOS 同型 39/42）→ 仅用训练窗
    #   会做出相同 A 项 8→10 决策。入口闸门不放宽：dry>0.55 未命中闸门样本
    #   IS n=6 / OOS n=2 全部 EVENT_ONLY（两窗一致）。edb_score=65 边界：
    #   score[60,65) 过全闸样本 6 行全部在 IS、OOS n=0 → 65 为数据边界。
    #   A+2 仅两条变层行：莱茵生物（IS，T+20 +12.10%，参与决策）；四川美丰
    #   （OOS，特征晋级，fut20 缺失收益未参与决策）。
    # [OOS 结果] 主池 n=4（可评估 2，两行 20260828 末端 fut20 未到期）：
    #   T+20 +3.83% / 胜率 50%；同窗基准超额 +3.39%（IS 超额 +16.51%，衰减仍为正）。
    #   OOS 漏斗分层：EDB_PULLBACK +27.29% / 胜率 60%（最强层）；
    #   EDB_WATCH -6.28% / 33%（负收益持续验证其「仅观察」定位）。
    #   四川美丰部分持有期 fut3 +10.41% / fut10 +13.94%（方向正面，fut20 未到期）。
    # [结论] 方向样本外成立（决策可复现 + 正超额），幅度不可信（可评估 n=2 << 30）；
    #   backtest-expert 结构化评分 40/100 Refine（样本量 / 滑点未测 / 10 参数 / 2 年
    #   历史四项红旗）。定位：可小规模实盘跟踪 + 持续积累 OOS 样本，非放大仓位。
    # ── 主池扩容 V1.4（参数扫描驱动，区间 20240601~20260918，C5 保守包）──
    # 方法：edb/scan_volume.py 单趟数据加载 × 参数变体矩阵，worker 内换配置跑
    #   真实引擎；BASE 与生产回测逐行自校验（剔 EVENT_ONLY 后 357 行全对齐，
    #   fut20 偏差 3.55e-15）。单杠杆 19 变体定出活杠杆（variant_stats_single.csv），
    #   组合 7 变体量化叠加（variant_stats.csv）。
    # C5 = vr_valid 2.0→1.6 + min_amount_yi_20 0.30→0.20 + dry_up_normal
    #   0.55→0.65 + pre_max_gap 0.08→0.10：n 357→868(+143%)、主池 20→34(+70%)、
    #   剔事件 T+20 med 1.78→2.24 / 胜率 60.9→61.7 / 剔前二 3.16→3.43 —— 全部
    #   改善（与 V1.2「信号多了胜率反而更高」同型：原闸门在优质形态左侧过度收紧）。
    # 新增构成：EDB 19→32、EDB_WATCH 18→118、EDB_PULLBACK 31→126、PRE_EDB 285→587。
    # 死杠杆（放松零增益，勿动）：edb_dry_up_max / cp_min（总分是边际约束）；
    # 反向杠杆：base_range_max（新增行桥接 episode，去重后反而减少）。
    # 风险警示：样本内结论，主池 34 行统计功效仍弱；C6 极限包
    #   （dry_up_normal=0.70 / pre_max_gap=0.12，n 1254）贴平台边缘，未采用。
    'strong_score': 80,
    'edb_score': 65,
    'watch_score': 60,
    'edb_dry_up_max': 0.55,        # EDB 层缩量准入上限（宽档；EDB_STRONG 仍用 dry_up_pass）
    # ── 回测驱动硬排除闸门（区间 20240601~20260918，去重后 335 个独立事件）──
    # 语义：命中后**不剔除**，只降级为 EDB_WATCH 观察；且仅作用于「突破日」判定，
    #       突破已发生后的 EDB_PULLBACK / EDB_REBREAKOUT 不受限制
    #       ——「等回踩」本来就是应对天量突破的正解。
    # CLIMAX 闸门（强证据，保留）：突破日 VR > vr_climax(5.0) → n=13，
    #   T+5 -2.40% / T+20 -5.62% / 胜率 30.8%，负期望；
    #   对照 3.0~5.0 → n=10，T+20 +25.46% / 盈亏比 6.02（最优区间）。
    # DRY_UP 单边闸门（弱证据，高收益优先 → 关闭）：突破日样本 n=26 分档——
    #   0.35~0.40 → n=5，T+5 -3.12% / T+20 -2.87% / 胜率 20%（弱）
    #   0.40~0.45 → n=4，T+5 +3.39% / T+20 +23.70% / 胜率 75%（强，含样本期最大赢家）
    # 单边闸门无法只切弱档而保强档（强档成员 dry 最高），样本量不支持在 (0.35,0.45] 内再切档；
    # 且 dry > 0.45 本就无法评 EDB（规范核心组合已是 _classify_today 硬条件）。
    # 主买入池准入改由：CLIMAX 闸门 + C 项缩量评分排序（<=0.35 得 25 分、0.35~0.45 得 18 分）
    #   + EDB_PULLBACK 路径（n=20，T+20 +14.31%，盈亏比 3.62，不受任何闸门影响）。
    'climax_hard_block': True,     # VR > vr_climax 不得进入主买入池
    'dry_up_tier_block': False,    # DryUpRatio 单边闸门（0.35 评分档保留，闸门关闭）

    # ═════════════════════════════════════════════════
    # 十、与 HVT 系统联动（词表映射）
    # ═════════════════════════════════════════════════
    # 项目内 HVT 权威词表为 7 态（sector_0915 归一化）：
    #   HVT_NONE / HVT_EVENT / HVT_ADJUSTING / HVT_LOCKING / HVT_REBREAKOUT / HVT_RETEST / HVT_FAILED
    # 仓库中不存在 HVT_NORMAL / HVT_WEAK 字面量，此处按结构语义映射：
    #   HVT_STRONG ← HVT_REBREAKOUT / HVT_LOCKING（且结构质量达标）
    #   HVT_NORMAL ← HVT_ADJUSTING / HVT_RETEST / HVT_EVENT
    #   HVT_WEAK   ← HVT_NONE / HVT_FAILED
    'hvt_strong_states': ('HVT_REBREAKOUT', 'HVT_LOCKING'),
    'hvt_normal_states': ('HVT_ADJUSTING', 'HVT_RETEST', 'HVT_EVENT'),
    'hvt_strong_quality': 60.0,    # HVT_STRONG 需 hvt_quality_score 达标

    # ═════════════════════════════════════════════════
    # 十一、事件污染标记
    # ═════════════════════════════════════════════════
    'event_lookback_days': 15,     # 事件回看交易日窗口
    'event_holdertrade_min_ratio': 0.5,   # 大股东减持比例下限(%)，过滤微量
    'event_surge_yoy': 100.0,      # 业绩暴增阈值(%)

    # ═════════════════════════════════════════════════
    # 十二、输出
    # ═════════════════════════════════════════════════
    'top_n': 30,
}


# ── 评分权重（总分 100，禁止因涨停/巨量无限加分）──
SCORE_WEIGHTS = {
    'A_base_days': 15,        # A 长期横盘
    'B_range': 15,            # B 价格压缩
    'C_dry_up': 25,           # C 极致缩量
    'D_vr': 20,               # D 爆量
    'E_breakout': 15,         # E 突破质量
    'F_trend': 10,            # F 趋势背景
}

# ── 状态中文名 ──
STATUS_CN = {
    'EDB_STRONG': '极致缩量后有效突破',
    'EDB': '标准EDB（重点观察）',
    'EDB_WATCH': '结构接近，未达强信号',
    'PRE_EDB': '即将突破（极致缩量待爆）',
    'EDB_PULLBACK': '突破后缩量回踩（平台未破）',
    'EDB_REBREAKOUT': '回踩后再次放量突破',
    'EVENT_ONLY': '一字/连板，无法正常交易',
    'NO_SIGNAL': '无信号',
}

# ── 状态优先级（互斥，取最高）──
STATUS_PRIORITY = (
    'EDB_REBREAKOUT', 'EDB_PULLBACK', 'EDB_STRONG', 'EDB',
    'EDB_WATCH', 'PRE_EDB', 'EVENT_ONLY',
)

ACTION_CN = {
    'BREAKOUT_READY': '突破就绪',
    'WAIT_PULLBACK': '等回踩',
    'BREAKOUT_CONFIRM': '突破确认',
    'WATCH': '观察',
    'BUY_ON_PULLBACK': '回踩可买',
    'WAIT_DEEPER_PULLBACK': '等回踩至平台',
    'REBREAKOUT_BUY_CANDIDATE': '再突破候选',
    'EVENT_ONLY_WATCH': '仅记录',
}
