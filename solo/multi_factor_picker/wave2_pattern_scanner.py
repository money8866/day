# -*- coding: utf-8 -*-
"""
二波形态精选 v2.12 — 修复量比字段保存错误

v2.12升级（2026-06-28）:
  1. 修复量比字段保存错误：CSV中vol_ratio应保存当日量比，而非调整期平均量比
  2. 影响：强势横盘/深度回调/放量回调/V型急跌四种形态的vol_ratio字段统一使用当日量比

v2.11升级（2026-06-28）:
  1. V型急跌评分优化（机构量化经验）
     - 一波涨幅过滤：50-60%主力强势(+3分)，>60%主力出货风险(-5分)
     - 回踩深度优化：18-22%最佳深度(+3分)，>25%趋势破坏风险(-5分)
     - 放量反弹确认：量比>1.2资金确认(+5分)，<0.8诱多风险(-3分)
  2. 预期效果：评分分化更明显，高质量信号（≥45分）提升

v2.10升级（2026-06-28）:
  - 波峰局部最高点过滤
  - 修复光洋/华宏类型误判

v2.9升级（2026-06-27）:
  1. 双创板V型急跌/放量回调阈值提升至40分（过滤低质量信号）
  2. 回测依据：评分≥45分首日反弹胜率91.8%，超过第4天（80.3%）11.5pp
  3. 光智科技案例验证：41分符合高质量信号标准

v2.8升级（2026-06-26）:
  - 上方空间惩罚因子（基于16,828样本回测）：gap>30%→-8；20-30%→-5；10-20%→-2
  - 深度回调长期缩量盘整加分（MA250上方+调整>30天+量比<0.7：成功率89.4%→+3）
  - 大东南类型（距前高>30%+MA60下方）直接惩罚，避免假强势横盘信号

v2.6升级（2026-06-24）:
  20. 四形态并列：强势横盘/深度回调/放量回调/V型急跌独立检测
  21. 每种形态显示独立胜率：强势横盘98.6%/V型急跌97.2%/放量回调91.2%/深度回调87.2%

v2.5升级（2026-06-24）:
  19. 新增--date参数：支持手动指定分析日期(YYYYMMDD)，解决Tushare数据延迟问题

v2.4升级（2026-06-24）:
  15. V型急跌权重加倍：从+5调整为+10（双创平均胜率97.2%最高）
  16. 放量回调新增加分：+5（胜率91.2%次高）
  17. 形态细分：深度回调内部细分为深度/放量/V型三种
  18. pattern_type动态传递：返回字典和板块加分函数都使用动态形态名

v2.3升级（2026-06-24）:
  13. 创新低检测与过滤：调整期最低价 ≤ 一波启动前最低价 → 直接continue过滤
      回测依据（双创板300只样本）：不创新低胜率41.2%，创新低胜率16.7%
  14. 不创新低加分：is_higher_low=True → 共振评分+5分（主力未出逃，二波意愿强）

v2.2升级（2026-06-24）:
  12. 修复除权日指标失真bug：全部指标从_bfq（不复权）切换到_qfq（前复权）
      - RSI/KDJ/CCI/WR/MFI/BIAS等10维技术指标用qfq版
      - MA/EMA/布林带等价格类指标用qfq版
      - 一波涨幅/回调幅度计算用qfq价（避免除权虚假跳空）
      - 入场价/止损价/目标价仍用未复权实际交易价
      - 修复案例：300773拉卡拉6/22每10送4后，bfq RSI=16.5(假超卖)→qfq RSI=63.1(正常)

v2.1升级（2026-06-24）:
  9. 一波涨幅加分（≥30%+2 / ≥50%+5 / ≥80%+8）→ 修正负相关偏误
  10. 创新高确认加分（调整期突破一波高点→+5）→ 趋势确认
  11. 新高回踩形态加分（创新高后回踩企稳→+3）→ 经典买点

核心升级(v2.0):
  1. stk_factor_pro 单接口替代3接口（3倍提速）
  2. ATR动态止损替代固定百分比
  3. DMI趋势反转(PDI上穿MDI)作为二波确认
  4. 10维度共振评分替代单一RSI判断
  5. MFI底背离检测
  6. BIAS乖离率极端超卖
  7. 量比底部缩量+次日放量启动确认
  8. MA/EMA直接使用不复权版本

形态1 - 强势横盘（沪深300最优: 98.6%, 盈亏比19.9x）
  一波拉升>20%后，强势横盘（回调<10%，调整<15天，量能萎缩）
  入场：多指标共振≥7分 或 MACD金叉+MA20上方
  止损：2×ATR(14)，目标：+30%

形态2 - 深度回调（双创板最优: 92.0%, 盈亏比12.2x）
  一波拉升>20%后，深度回调>20%，调整期>10天
  入场：多指标共振≥10分 或 RSI<30+KDJ-J<20
  止损：2×ATR(14)，目标：+20~30%

用法:
  python wave2_pattern_scanner.py --pattern test --codes 600519.SH 300750.SZ
  python wave2_pattern_scanner.py --pattern sideways --pool hs300
  python wave2_pattern_scanner.py --pattern deep --pool gem_kc
"""
import os, sys, time, datetime, json, pickle, warnings
sys.path.insert(0, r'D:\mystock')

if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import pandas as pd
import numpy as np
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

import tushare as ts
from typing import Optional, Literal

# 设置 TS_TOKEN 环境变量（避免 set_token 写入 tk.csv 时被沙箱拦截）
os.environ['TS_TOKEN'] = os.environ['TUSHARE_TOKEN']
pro = ts.pro_api()

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

CACHE_DIR = r'D:\mystock\cache_daily'
os.makedirs(CACHE_DIR, exist_ok=True)

# 股票基本信息缓存
# 统一使用 sc.load_stock_basic()，详见 stock_cache.py

# SQLite 统一缓存模块
sys.path.insert(0, r'D:\mystock\solo')
import stock_cache as sc

# ═══════════════════════════════════════════════════════
# 缓存API调用（所有缓存函数统一入口：sc.*）
# ═══════════════════════════════════════════════════════

def batch_cache_stk_factor_pro(target_date):
    """委托给 sc.batch_cache_stk_factor_pro（统一入口）"""
    sc.batch_cache_stk_factor_pro(target_date)

def cached_daily(ts_code, start_date, end_date, pro=None):
    """委托给 sc.cached_daily（统一入口）"""
    return sc.cached_daily(ts_code, start_date, end_date, pro=pro)

def get_list_date(ts_code):
    """委托给 sc.get_list_date（统一入口）"""
    return sc.get_list_date(ts_code)


def cached_stk_factor_pro(ts_code, start_date, end_date):
    """委托给 sc.cached_stk_factor_pro（统一入口）"""
    return sc.cached_stk_factor_pro(ts_code, start_date, end_date)

def cached_daily_basic(ts_code, start_date, end_date):
    """委托给 sc（统一入口）"""
    return sc.cached_daily(ts_code, start_date, end_date)

# ═══════════════════════════════════════════════════════════════════
# 参数常量
# ═══════════════════════════════════════════════════════════════════
SURGE_DAYS   = 20
SURGE_MIN    = 0.20
ADJUST_MAX   = 60
WAVE2_WINDOW = 20
WAVE2_MIN    = 0.10

# ══════════════════════════════════════════════════════
# 交易日判断：15点后数据已更新=本交易日，15点前=上交易日
# ══════════════════════════════════════════════════════
def get_effective_date(force_date: str = '') -> str:
    """委托给 sc.get_effective_date（统一入口）"""
    return sc.get_effective_date(force_date)

# 强势横盘
SIDEWAYS_PULLBACK_MAX = 0.10
SIDEWAYS_ADJUST_MAX   = 15
SIDEWAYS_VOL_MAX      = 0.80

# 深度回调
DEEP_PULLBACK_MIN = 0.20
DEEP_ADJUST_MIN   = 10

# 入场评分阈值（v2.1含主力类因子，满分约40+）
#   强势横盘: 基础7分(纯技术) → 加主力类后通常15-25分
#   深度回调: 基础10分(纯技术) → 加主力类后通常15-30分
#   V型急跌: 双创专属，阈值20分
SCORE_SIDWAYS_MIN = 20    # 主板强势横盘(v3.4): 回调2-10%+一波20-60%+评分20+
SCORE_DEEP_MIN    = 10    # 深度回调保持10分
SCORE_VSHAPE_GEM_MIN = 20 # 双创V型急跌阈值20分
SCORE_VOL_PULLBACK_GEM_MIN = 20 # 双创放量回调阈值20分

# 评分档次参考（v2.1）:
#   7-12分: 纯技术信号，无一波涨幅/创新高加分
#   13-17分: 有一波涨幅加分(+2~5)，无创新高
#   18-22分: 一波涨幅加分+创新高确认
#   23+分:  全因子共振（涨幅大+创新高+新高回踩）= 最强信号

# 目标盈亏比
TARGET_RR_SIDWAYS = 10.0
TARGET_RR_DEEP    = 5.0


# ═══════════════════════════════════════════════════════════════════
# 多指标共振评分引擎
# ═══════════════════════════════════════════════════════════════════
class ResonanceScorer:
    """10维度共振评分：量价+动量+资金+趋势+情绪"""

    @staticmethod
    def score(row: pd.Series, prev_row: Optional[pd.Series] = None,
              wave1_gain_pct: float = 0, new_high_confirmed: bool = False,
              new_high_pullback: bool = False,
              is_higher_low: bool = False,
              pattern_type: str = '深度回调',
              gap_to_peak_pct: float = 0,
              pullback_pct: float = 0,
              is_deep_long_consolidation: bool = False,
              limitup_score: int = 0,
              volume_recovery_score: int = 0,
              atr_pct: float = 0,
              market_cap_b: float = 0) -> dict:
        """
        评分维度（满分约65+，v3.5新增市值区间加减分）:
          动量类: RSI(3) + KDJ-J(3) + CCI(2) + WR(2)
          资金类: MFI(2) + OBV方向(1) + 量比启动(2)
          趋势类: MACD金叉(2) + DMI反转(3) + MA位置(1)
          情绪类: BIAS(3) + PSY(2) + VR(1)
          背离类: RSI底背离(3) + MFI底背离(3)
          主力类: 一波涨幅(8) + 创新高确认(5) + 新高回踩(3) + 不创新低(5)
          形态类: V型急跌(8) + 放量回调(5) + 强势横盘(3)
          压力类: 上方空间惩罚(-2~-8)  [v2.8新增]
          增强类: 长期缩量盘整加分(+3)  [v2.8新增]
          ATR类:   ATR 2~3% +8分（回测: 平均涨幅31%，远高于<1.5%的11%）
        回测依据（16,828沪深300样本）:
          上方gap<10%: 98.0%成功率 → 不扣分
          gap 10-20%:  92.0%成功率 → -2
          gap 20-30%:  88.3%成功率 → -5
          gap>30%:    85.7%成功率 → -8（大东南类型）
          深度回调+长期缩量盘整(MA250上): 89.4% → +3
          ATR 2~3%:   平均涨幅31% → +8（最优区间）
          ATR <1.5%:  平均涨幅11% → 不加分
        """
        total = 0
        details = []

        def _add(pts, desc):
            nonlocal total
            total += pts
            if pts >= 0:
                details.append(f'{desc}(+{pts})')
            else:
                details.append(f'{desc}({pts})')

        # 安全取值
        def v(col, default=0.0):
            val = row.get(col, default)
            return float(val) if not pd.isna(val) else default

        # ── 动量类 ─────────────────────────────────────
        rsi = v('rsi_qfq_6', 50)
        if rsi < 20:    _add(3, f'RSI={rsi:.0f}极度超卖')
        elif rsi < 30:  _add(3, f'RSI={rsi:.0f}超卖')
        elif rsi < 40:  _add(2, f'RSI={rsi:.0f}偏低')
        elif rsi < 50:  _add(1, f'RSI={rsi:.0f}中性偏弱')

        kdj_j = v('kdj_qfq', 50)
        if kdj_j < -20:  _add(3, f'KDJ-J={kdj_j:.0f}极度超卖')
        elif kdj_j < 0:  _add(3, f'KDJ-J={kdj_j:.0f}超卖')
        elif kdj_j < 20: _add(2, f'KDJ-J={kdj_j:.0f}偏低')

        cci = v('cci_qfq', 0)
        if cci < -200:  _add(3, f'CCI={cci:.0f}极度超卖')
        elif cci < -100: _add(2, f'CCI={cci:.0f}超卖')

        wr = v('wr_hfq', 50)
        if wr > 90:  _add(3, f'WR={wr:.0f}极度超卖')
        elif wr > 80: _add(2, f'WR={wr:.0f}超卖')

        # ── 资金类 ─────────────────────────────────────
        mfi = v('mfi_qfq', 50)
        if mfi < 20:  _add(2, f'MFI={mfi:.0f}资金枯竭')
        elif mfi < 30: _add(1, f'MFI={mfi:.0f}资金偏弱')

        # OBV方向（与前一交易日比较）
        if prev_row is not None:
            obv_now = v('obv_qfq')
            obv_prev = float(prev_row.get('obv_qfq', 0)) if not pd.isna(prev_row.get('obv_qfq', 0)) else 0
            if obv_now > obv_prev:
                _add(1, 'OBV上升')

        # 量比（v3.1：极度缩量<0.6不加分，回测显示量比<0.7胜率仅30%）
        vol_ratio = v('volume_ratio', 1.0)
        if 0.6 <= vol_ratio < 0.8: _add(1, f'量比={vol_ratio:.2f}缩量')
        elif vol_ratio >= 0.8 and vol_ratio < 1.2: _add(1, f'量比={vol_ratio:.2f}温和放量')

        # 量比启动：底部缩量+次日放量
        if prev_row is not None:
            prev_vr = float(prev_row.get('volume_ratio', 1.0)) if not pd.isna(prev_row.get('volume_ratio', 1.0)) else 1.0
            if prev_vr < 0.8 and vol_ratio > 1.2:
                _add(2, f'缩量({prev_vr:.2f})→放量({vol_ratio:.2f})启动')

        # ── 趋势类 ─────────────────────────────────────
        macd_dif = v('macd_dif_hfq', 0)
        macd_dea = v('macd_dea_qfq', 0)
        if macd_dif > macd_dea:
            _add(2, 'MACD金叉')

        # DMI趋势反转
        pdi = v('dmi_pdi_hfq', 20)
        mdi = v('dmi_mdi_hfq', 20)
        adx = v('dmi_adx_hfq', 20)
        if pdi > mdi:
            _add(1, f'PDI({pdi:.0f})>MDI({mdi:.0f})多头')
        else:
            # 检测PDI即将上穿MDI（差距<3）
            if mdi - pdi < 3:
                _add(1, f'PDI({pdi:.0f})≈MDI({mdi:.0f})即将交叉')
        # ADX趋势强度
        if adx > 25:
            _add(1, f'ADX={adx:.0f}>25强趋势')

        # MA位置（三均线支撑用前复权体系，均线连续无除权缺口，更准确）
        close = v('close_qfq', 0)
        ma20 = v('ma_qfq_20', 0)
        ma60_qfq = v('ma_qfq_60', 0)
        ma90_qfq = v('ma_qfq_90', 0)
        ma250_qfq = v('ma_qfq_250', 0)
        ma90_roll = v('ma90', 0)
        ma250_roll = v('ma250', 0)
        ma90 = ma90_qfq if ma90_qfq > 0 else ma90_roll
        ma250 = ma250_qfq if ma250_qfq > 0 else ma250_roll
        
        if close > ma20 and ma20 > 0:
            _add(1, 'MA20上方')
        # 均线只用于过滤，不加分（v2.7）
        # 中长线趋势支撑（三均线=最强二波信号）
        # 用前复权体系判断：MA60 + MA90 + MA250（API无MA120，用MA90替代）
        qfq_above_ma60 = close > ma60_qfq and ma60_qfq > 0
        above_ma90 = close > ma90 and ma90 > 0
        above_ma250 = close > ma250 and ma250 > 0

        # ── 情绪类 ─────────────────────────────────────
        bias1 = v('bias1_hfq', 0)
        bias2 = v('bias2_hfq', 0)
        if bias1 < -5:   _add(2, f'BIAS1={bias1:.1f}%极端超卖')
        elif bias1 < -3: _add(1, f'BIAS1={bias1:.1f}%超卖')
        if bias2 < -10:  _add(3, f'BIAS2={bias2:.1f}%极端超卖')
        elif bias2 < -7: _add(1, f'BIAS2={bias2:.1f}%超卖')

        psy = v('psy_hfq', 50)
        if psy <= 25:  _add(2, f'PSY={psy:.0f}极度悲观')
        elif psy < 37: _add(1, f'PSY={psy:.0f}偏悲观')

        vr = v('vr_hfq', 100)
        if vr < 70:   _add(1, f'VR={vr:.0f}地量')

        # ── 主力类（新增 v2.1）───────────────────────────────────
        # 一波涨幅加分：涨幅越大=主力介入越深=二波意愿越强
        # v3.1：V型急跌用反转逻辑（一波<35%主力没吃饱），跳过通用加分
        if pattern_type != 'V型急跌':
            if wave1_gain_pct >= 80:
                _add(8, f'一波涨幅+{wave1_gain_pct:.0f}%极强')
            elif wave1_gain_pct >= 50:
                _add(5, f'一波涨幅+{wave1_gain_pct:.0f}%强')
            elif wave1_gain_pct >= 30:
                _add(2, f'一波涨幅+{wave1_gain_pct:.0f}%中')

        # 创新高确认：调整期间曾突破一波高点=趋势向上确认
        if new_high_confirmed:
            _add(5, '创新高确认(趋势向上)')

        # 新高回踩形态：创新高后回踩MA20/MA60企稳=经典买点
        if new_high_pullback:
            _add(3, '新高回踩企稳')

        # 不创新低加分（v2.3）：调整低点 > 一波启动前最低价
        # 回测依据：不创新低胜率41.2%，创新低胜率16.7%
        if is_higher_low:
            _add(5, '不创新低(低点抬高/主力未出逃)')

        # ── 形态类加分（v2.4新增）─────────────────────────────
        # 回测依据（双创板52,949样本）：
        #   V型急跌: 胜率97.2%均涨13.2% → +8分（最高胜率形态）
        #   放量回调: 胜率91.2%均涨12.5% → +5分（次高胜率形态）
        #   强势横盘: 胜率90.9%均涨13.1% → +3分（主板98.6%更强）
        #   深度回调: 胜率87.2%均涨12.1% → 不加分（基准形态）
        if pattern_type == 'V型急跌':
            _add(8, f'形态加分(V型急跌胜率97.2%)')
        elif pattern_type == '放量回调':
            _add(5, f'形态加分(放量回调胜率91.2%)')
        elif pattern_type == '强势横盘':
            _add(3, f'形态加分(强势横盘胜率90.9%)')

        # ── 上方压力惩罚（v2.8新增，基于16,828样本回测）─────────────────────
        # 大东南距前高+31.2%就是典型：虽然形态过关但上方空间太大拖累二波
        # 回测：距前高<10%→98.0%；10-20%→92.0%；20-30%→88.3%；>30%→85.7%
        if gap_to_peak_pct > 0.30:
            _add(-8, f'上方压力(gap={gap_to_peak_pct*100:.0f}% > 30%)')
        elif gap_to_peak_pct > 0.20:
            _add(-5, f'上方压力(gap={gap_to_peak_pct*100:.0f}% > 20%)')
        elif gap_to_peak_pct > 0.10:
            _add(-1, f'上方压力(gap={gap_to_peak_pct*100:.0f}% > 10%)')

        # ── 涨停/巨量收复加分（v3.0新增）───────────────────────────────
        # 近期涨停 = 主力实力强，巨量收复 = 主力强势吸筹
        if limitup_score > 0:
            _add(limitup_score, f'近期有涨停(主力强)')
        if volume_recovery_score > 0:
            _add(volume_recovery_score, f'涨停量能突破(收复巨量)')

        # ── 深度回调-长期缩量盘整加分（v2.8新增）─────────────────────────────
        # 回测：深度回调+调整>30天+MA250上方+量<70%→89.4%(优于平均86.2%)
        if is_deep_long_consolidation:
            _add(3, '深度回调-长期缩量盘整(MA250支撑)')
        
        # ── V型急跌特异性加分（v3.1新增，基于30只40分+样本回测）────────
        if pattern_type == 'V型急跌':
            # 连续缩量（回调末端主力惜售，反弹概率高）
            if prev_row is not None:
                prev_vr = float(prev_row.get('volume_ratio', 1.0)) if not pd.isna(prev_row.get('volume_ratio', 1.0)) else 1.0
                curr_vr = v('volume_ratio', 1.0)
                if prev_vr < 0.8 and curr_vr < 0.8:
                    _add(2, 'V型急跌-连续缩量(惜售)')
            # 价格远超MA250（强势股回调特征）
            if ma250 > 0 and close / ma250 > 1.5:
                _add(1, '价格远超MA250(强势股)')
            # 回调深度黄金区间（15-18%：胜率56.2%，涨幅均值124%，远超18-25%的43%）
            # 用pullback_pct（实际回调深度）而非gap_to_peak_pct（距前高空间），避免分母差异导致黄金区间错判
            if 0.15 <= pullback_pct < 0.18:
                _add(3, 'V型急跌回调深度黄金(15-18%)')
            elif 0.12 <= pullback_pct < 0.15:
                _add(1, 'V型急跌回调偏浅(12-15%)')
            # 一波涨幅反转（v3.1）：V型急跌中一波<35%主力没吃饱，反而涨更多
            # 回测：一波<35%胜率63.6%涨幅130%，一波>=35%胜率31.6%涨幅60%
            if wave1_gain_pct < 25:
                _add(5, 'V型一波涨幅偏小(主力没吃饱)')
            elif wave1_gain_pct < 35:
                _add(3, 'V型一波涨幅适中(筹码健康)')
            
            # ══════════════════════════════════════════════════════════════
            # v2.11新增：V型急跌评分优化（机构量化经验）
            # ══════════════════════════════════════════════════════════════
            # 1. 一波涨幅过滤：30-60%最佳（>60%主力可能出货）
            if wave1_gain_pct >= 50 and wave1_gain_pct <= 60:
                _add(3, 'V型一波涨幅50-60%(主力强势)')
            elif wave1_gain_pct > 60:
                _add(-5, 'V型一波涨幅>60%(主力出货风险)')
            
            # 2. 回踩深度优化：15-25%最佳（>25%趋势可能破坏）
            if 0.18 <= pullback_pct < 0.22:
                _add(3, 'V型回踩深度18-22%(最佳深度)')
            elif pullback_pct > 0.25:
                _add(-5, 'V型回踩深度>25%(趋势破坏风险)')
            
            # 3. 调整天数优化：4-5天最佳（>7天非V型）
            # 注：adjust_days参数未传入score函数，此处用wave1_high_idx推断（简化）
            # 实际调整天数在detect_vshape_pattern中计算并传递
            
            # 4. RSI超卖确认：<40明确超卖（v3.1已部分实现）
            # 当前已有RSI<40加分，此处不重复
            
            # 5. 放量反弹确认：量比>1.2资金确认（缩量可能诱多）
            curr_vr = v('volume_ratio', 1.0)
            if curr_vr > 1.2:
                _add(5, 'V型放量反弹(量比>1.2资金确认)')
            elif curr_vr < 0.8:
                _add(-3, 'V型缩量反弹(量比<0.8诱多风险)')

        # ── ATR加分（v3.2新增，基于ATR区间与平均涨幅回测）────────────────
        #   ATR 2~3%: 平均涨幅31%（最优区间）→ +8
        #   其他区间不加分也不扣分
        if 0.02 <= atr_pct < 0.03:
            _add(8, f'ATR={atr_pct*100:.1f}%黄金区间(2~3%均涨31%)')

        # ── 市值区间加减分（v3.5新增）────────────────────────────────────
        #   80~200亿:   弹性好但波动大 → +2
        #   200~500亿:  兼顾弹性与稳定性（最佳）→ +5
        #   500~1000亿: 大盘股，趋势稳 → +3
        #   1000亿以上: 流动性好但弹性不足 → +1
        if market_cap_b > 0:
            if 200 <= market_cap_b < 500:
                _add(5, f'市值{market_cap_b:.0f}亿(最佳区间200~500亿)')
            elif 500 <= market_cap_b < 1000:
                _add(3, f'市值{market_cap_b:.0f}亿(较好区间500~1000亿)')
            elif 80 <= market_cap_b < 200:
                _add(2, f'市值{market_cap_b:.0f}亿(中等区间80~200亿)')
            elif market_cap_b >= 1000:
                _add(1, f'市值{market_cap_b:.0f}亿(大盘股流动性好)')

        # ── 中长线趋势过滤（v2.7核心规则）───────────────────────────────────
        # 三均线支撑=二波成功率100%，不满足则直接过滤
        # 用前复权体系判断：MA60 + MA90 + MA250（均线连续无除权缺口，更准确）
        if not (qfq_above_ma60 and above_ma90 and above_ma250):
            return {'total': 0, 'details': ['过滤: 不满足三均线支撑(MA60+MA90+MA250)'], 'filtered': True}

        return {'total': total, 'details': details}

    @staticmethod
    def check_divergence(df: pd.DataFrame, idx: int) -> dict:
        """检测RSI/MFI底背离：当前价格更低但指标更高"""
        results = {}
        if idx < 3 or idx >= len(df):
            return results

        close = float(df.iloc[idx]['close'])
        rsi   = float(df.iloc[idx].get('rsi_qfq_6', 50))
        mfi   = float(df.iloc[idx].get('mfi_qfq', 50))

        for lookback in range(1, min(15, idx + 1)):
            prev_close = float(df.iloc[idx - lookback]['close'])
            if prev_close > close:  # 前高 > 当前低
                prev_rsi = float(df.iloc[idx - lookback].get('rsi_qfq_6', 50))
                prev_mfi = float(df.iloc[idx - lookback].get('mfi_qfq', 50))
                if prev_rsi < rsi and not pd.isna(prev_rsi):
                    results['rsi_divergence'] = {
                        'found': True,
                        'pts': 3,
                        'desc': f'RSI底背离: {lookback}天前价格更高但RSI更低'
                    }
                    break
                if prev_mfi < mfi and not pd.isna(prev_mfi):
                    results['mfi_divergence'] = {
                        'found': True,
                        'pts': 3,
                        'desc': f'MFI底背离: {lookback}天前价格更高但MFI更低'
                    }
                    break
        return results

    @staticmethod
    def check_dmi_crossover(df: pd.DataFrame, idx: int) -> dict:
        """检测PDI上穿MDI（趋势反转确认）"""
        if idx < 1 or idx >= len(df):
            return {'found': False}
        pdi_now  = float(df.iloc[idx].get('dmi_pdi_hfq', 0))
        mdi_now  = float(df.iloc[idx].get('dmi_mdi_hfq', 0))
        pdi_prev = float(df.iloc[idx-1].get('dmi_pdi_hfq', 0))
        mdi_prev = float(df.iloc[idx-1].get('dmi_mdi_hfq', 0))

        if pdi_prev <= mdi_prev and pdi_now > mdi_now:
            return {
                'found': True,
                'pts': 3,
                'desc': f'PDI({pdi_prev:.0f}→{pdi_now:.0f})上穿MDI({mdi_prev:.0f}→{mdi_now:.0f})趋势反转!!'
            }
        return {'found': False}


# ═══════════════════════════════════════════════════════════════════
# 核心检测类
# ═══════════════════════════════════════════════════════════════════
class WavePatternDetector:

    def __init__(self, force_date: str = ''):
        self.scorer = ResonanceScorer()
        self.force_date = force_date  # 强制指定日期
        self._scan_data: Optional[pd.DataFrame] = None  # scan_pool 时共享给4个detect方法

    # ── 数据获取（单接口！）─────────────────────────────────────
    def load_data(self, ts_code: str, lookback: int = 180) -> Optional[pd.DataFrame]:
        # scan_pool 批量模式：优先使用预加载的共享数据（返回副本避免原地修改）
        if self._scan_data is not None:
            return self._scan_data.copy()
        trade_date = get_effective_date(self.force_date)
        trade_date_dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
        start = (trade_date_dt - datetime.timedelta(days=lookback + 1)).strftime('%Y%m%d')
        try:
            # 使用缓存版API获取历史因子数据
            df = cached_stk_factor_pro(ts_code, start, trade_date)
            if df is None or len(df) < 60:
                return None
            df = df.sort_values('trade_date').reset_index(drop=True)

            # 用 daily 补充今日数据（stk_factor_pro 有延迟）
            df_daily = cached_daily(ts_code, trade_date, trade_date)
            if df_daily is not None and not df_daily.empty:
                today_data = df_daily.iloc[-1]
                today_str = str(today_data['trade_date'])
                # 如果今日数据比stk_factor_pro最新数据更新，则用daily数据补充
                if today_str > df['trade_date'].iloc[-1]:
                    new_row = {col: today_data.get(col, df.iloc[-1].get(col)) for col in df.columns}
                    new_row['trade_date'] = today_str
                    # 价格数据：daily返回的就是当日实际价（不复权）
                    new_row['close'] = today_data['close']
                    new_row['close_qfq'] = today_data['close']  # 今日价格直接作为前复权价
                    new_row['close_hfq'] = today_data['close']
                    new_row['open'] = today_data['open']
                    new_row['open_qfq'] = today_data['open']
                    new_row['open_hfq'] = today_data['open']
                    new_row['high'] = today_data['high']
                    new_row['high_qfq'] = today_data['high']
                    new_row['high_hfq'] = today_data['high']
                    new_row['low'] = today_data['low']
                    new_row['low_qfq'] = today_data['low']
                    new_row['low_hfq'] = today_data['low']
                    new_row['vol'] = today_data['vol']
                    new_row['amount'] = today_data.get('amount', 0)
                    new_row['pct_chg'] = today_data.get('pct_chg', 0)
                    new_row['turnover_rate'] = today_data.get('turnover_rate', 0)
                    new_row['volume_ratio'] = today_data.get('volume_ratio', 1)
                    new_row['pe_ttm'] = df.iloc[-1].get('pe_ttm', 0)
                    new_row['pb'] = df.iloc[-1].get('pb', 0)
                    # 因子数据暂用昨日值（因子更新有延迟）
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

            # 过滤停牌
            df = df[df['vol'] > 0].reset_index(drop=True)
            if len(df) < 60:
                return None

            # ⚠️ 修复DataFrame碎片化警告：在修改前先复制一次
            df = df.copy()

            # 在 stk_factor_pro 中已计算好MA/RSI/换手率等，直接取用
            # 只需补算pct_5d/10d/20d
            # ⚠️ 统一使用后复权（close_hfq）进行形态计算
            # 后复权价格序列天然连续，除权/分红不会产生跳空缺口
            if 'close_hfq' in df.columns:
                df['close_bfq'] = df['close']      # 保留原始价（止损/目标价需要）
                df['close'] = df['close_hfq']       # 价格计算用后复权
            if 'high_hfq' in df.columns:
                df['high'] = df['high_hfq']
            if 'low_hfq' in df.columns:
                df['low'] = df['low_hfq']
            
            # 技术指标保留 qfq（前复权）版本，hfq 后复权版本部分字段缺失
            # 评分代码统一使用 _qfq 后缀字段，避免 NaN 污染

            df['pct_5d']  = df['close'].pct_change(5)
            df['pct_10d'] = df['close'].pct_change(10)
            df['pct_20d'] = df['close'].pct_change(20)

            return df
        except Exception:
            return None

    # ── 涨停/巨量收复特征预计算 ──────────────────────────────
    @staticmethod
    def _calc_limitup_features(df, entry_idx):
        """预计算近期涨停加分和巨量收复加分
        返回: (limitup_score, volume_recovery_score)
        """
        limitup_days = 0
        volume_recovery_count = 0
        lookback = min(20, entry_idx)
        closes = df['close'].values
        volumes = df['vol'].values

        for i in range(entry_idx, entry_idx - lookback - 1, -1):
            if i < 0:
                continue
            pct_chg = float(df.iloc[i].get('pct_chg', 0))
            if not np.isnan(pct_chg) and pct_chg > 9.5:
                ts_code = str(df.iloc[i].get('ts_code', ''))
                is_20cm = ts_code.startswith('3') or ts_code.startswith('688')
                threshold = 19.5 if is_20cm else 9.8
                if pct_chg >= threshold:
                    limitup_days += 1
                    # 巨量收复：涨停日成交量 >= 前60日均量的2倍
                    vol_peak = volumes[max(0, i-60):i].mean()
                    if vol_peak > 0 and volumes[i] > vol_peak * 2:
                        volume_recovery_count += 1

        # 涨停次数 → 加分
        if limitup_days >= 3:
            limitup_score = 5
        elif limitup_days >= 2:
            limitup_score = 4
        elif limitup_days >= 1:
            limitup_score = 2
        else:
            limitup_score = 0

        # 巨量收复 → 加分
        if volume_recovery_count >= 2:
            volume_recovery_score = 4
        elif volume_recovery_count >= 1:
            volume_recovery_score = 3
        else:
            volume_recovery_score = 0

        return limitup_score, volume_recovery_score

    # ── volume_ratio 为0时用当日成交量/前5日均量替代 ─────────
    @staticmethod
    def _fix_volume_ratio(df, entry_idx, row_sc):
        """当 volume_ratio 为0时，用 vol / 前5日均vol 替代"""
        vr = float(row_sc.get('volume_ratio', 0))
        if vr != 0 or pd.isna(vr) or float(row_sc.get('vol', 0)) <= 0:
            return row_sc
        vol_today = float(row_sc['vol'])
        start = max(0, entry_idx - 5)
        if start >= entry_idx:
            return row_sc
        vol_prev5 = df.iloc[start:entry_idx]['vol'].mean()
        if vol_prev5 > 0:
            corrected = vol_today / vol_prev5
            row_sc['volume_ratio'] = corrected
        return row_sc

    # ── 板块适配加分 ──────────────────────────────────────
    @staticmethod
    def _board_bonus(ts_code: str, pattern: str) -> tuple:
        """板块形态适配加分：基于52,949样本回测结果优化

        回测依据（双创板52,949样本，不创新低条件）：
          V型急跌: 胜率97.2% → 双创优选(+8)
          放量回调: 胜率91.2% → 中性(+0)
          深度回调: 胜率88.2% → 双创压制(-2)
          强势横盘: 胜率93.3% → **双创过滤**

        沪深300（主板最优）：
          强势横盘: 胜率98.6% → 主板优选(+5)
          深度回调: 胜率86.2% → 主板压制(-3)
        """
        is_gem_kc = ts_code.startswith(('688', '300', '301'))  # 双创板
        is_main   = ts_code.startswith(('600', '601', '603', '605', '000', '002'))   # 主板(含上海60x)

        if pattern == 'V型急跌':
            # V型急跌是双创最高胜率形态(97.2%)
            if is_gem_kc:
                return (8, '双创优选V型急跌(+8)')
            elif is_main:
                return (0, '')
        elif pattern == '强势横盘':
            if is_main:
                return (5, '主板优选强势横盘(+5)')
            elif is_gem_kc:
                # 双创强势横盘直接过滤
                return (-100, '双创强势横盘过滤')
        elif pattern == '放量回调':
            # 放量回调中性，无额外加分
            return (0, '')
        elif pattern == '深度回调':
            if is_gem_kc:
                # 双创深度回调88.2%，略低于其他形态
                return (-2, '双创深度回调较弱(-2)')
            elif is_main:
                return (-3, '主板深度回调较弱(-3)')
        return (0, '')

    # ── 核心辅助: 找近期wave1候选高点 ────────────────────────────
    def _find_recent_wave1(self, closes: np.ndarray, n: int, max_lookback: int = 150) -> list:
        candidates = []
        for lookback in range(3, min(max_lookback, n - SURGE_DAYS - 5)):
            end_idx = n - lookback
            if end_idx < SURGE_DAYS:
                continue
            window = closes[end_idx - SURGE_DAYS:end_idx + 1]
            low_in_win  = np.argmin(window)
            high_in_win = np.argmax(window)
            if high_in_win <= low_in_win:
                continue
            if (high_in_win - low_in_win) > SURGE_DAYS - 2:
                continue
            surge_gain = (window[high_in_win] - window[low_in_win]) / window[low_in_win]
            if surge_gain < SURGE_MIN:
                continue
            wave1_high_idx = end_idx - SURGE_DAYS + high_in_win
            wave1_low_idx  = end_idx - SURGE_DAYS + low_in_win
            if not any(h == wave1_high_idx for h, *_ in candidates):
                # v3.7: 波峰必须是局部最高点（比前后3天都高）
                # 防止下跌趋势中的日线反弹被误判为波峰（如000021.SZ 20260605）
                is_local_peak = True
                for offset in range(1, 4):
                    if wave1_high_idx - offset >= 0 and closes[wave1_high_idx - offset] > closes[wave1_high_idx]:
                        is_local_peak = False
                        break
                    if wave1_high_idx + offset < n and closes[wave1_high_idx + offset] > closes[wave1_high_idx]:
                        is_local_peak = False
                        break
                if not is_local_peak:
                    continue
                lookback_start = max(0, wave1_low_idx - 200)
                pre_history = closes[lookback_start:wave1_low_idx]
                if len(pre_history) >= 20:
                    pre_high = pre_history.max()
                    if pre_high > closes[wave1_high_idx] * 1.15:
                        continue

                candidates.append((wave1_high_idx, wave1_low_idx, surge_gain))
        # ── 合并同一波的相近高点，只保留最高点（v3.5）──
        # 问题：6月15日和6月16日的高点是同一波上涨，不应被拆成两个独立信号
        # 解决：5天内的高点视为同一波，只保留价格最高的那个
        merged = []
        used = set()
        for i, (h1, l1, g1) in enumerate(candidates):
            if i in used:
                continue
            best_h, best_l, best_g = h1, l1, g1
            for j, (h2, l2, g2) in enumerate(candidates):
                if j <= i or j in used:
                    continue
                if abs(h2 - h1) <= 5:
                    used.add(j)
                    if closes[h2] > closes[best_h]:
                        best_h, best_l, best_g = h2, l2, g2
            used.add(i)
            merged.append((best_h, best_l, best_g))
        merged.sort(key=lambda x: (n - x[0]))
        return merged

    # ── ATR动态止损 ──────────────────────────────────────────────
    def _calc_atr_stop(self, entry_price: float, atr: float,
                       min_pct: float = 0.02, max_pct: float = 0.10) -> tuple:
        """2×ATR止损，限制在2%~10%范围内"""
        stop_distance = 2 * atr
        stop_pct = stop_distance / entry_price
        stop_pct = max(min_pct, min(max_pct, stop_pct))
        stop_price = round(entry_price * (1 - stop_pct), 2)
        return stop_price, round(stop_pct * 100, 1)

    # ── 形态1: 强势横盘 ──────────────────────────────────────────
    def detect_sideways_pattern(self, ts_code: str, today_only: bool = False, target_date: str = '') -> Optional[dict]:
        df = self.load_data(ts_code, lookback=500)
        if df is None or len(df) < 60:
            return None

        # 支持按目标日期截断（用于回测/历史扫描）
        if target_date:
            mask = df['trade_date'].astype(str) <= target_date
            if not mask.any():
                return None
            df = df[mask].copy()
            if len(df) < 60:
                return None

        # 计算MA120/MA250（stk_factor_pro无此字段）
        df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
        df['ma250'] = df['close'].rolling(250, min_periods=120).mean()

        closes  = df['close'].values
        highs   = df['high'].values
        lows    = df['low'].values
        volumes = df['vol'].values
        n = len(df)

        wave1_candidates = self._find_recent_wave1(closes, n, max_lookback=80)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
            wave1_high_price = highs[wave1_high_idx]

            post_high_closes = closes[wave1_high_idx:]
            post_high_lows   = lows[wave1_high_idx:]
            if len(post_high_closes) < 5:
                continue

            low_after_high = post_high_lows.min()
            pullback_pct   = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos        = int(np.argmin(post_high_lows))
            adjust_days    = low_pos
            entry_idx      = wave1_high_idx + low_pos

            if entry_idx >= n:
                continue

            # 标准强势横盘：回调<10%
            is_standard_sideways = (0 < pullback_pct < SIDEWAYS_PULLBACK_MAX)
            
            # N字回调到MA20支撑模式：回调幅度可放宽到25%，但必须回踩MA20上方5%以内
            is_ma20_support = False
            ma20_bfq_key = [k for k in ['ma_bfq_20'] if k in df.columns]
            
            if ma20_bfq_key:
                ma20_val = df[ma20_bfq_key[0]].iloc[entry_idx]
                if ma20_val > 0:
                    adj_factor = df['adj_factor'].iloc[entry_idx] if 'adj_factor' in df.columns else 1.0
                    ma20_bfq_actual = ma20_val * adj_factor
                    if low_after_high > ma20_bfq_actual * 0.95:
                        is_ma20_support = (0 < pullback_pct < 0.25)
            
            # pullback必须>0：股价必须实际回调过，突破新高不算横盘
            if not ((is_standard_sideways or is_ma20_support) and adjust_days <= SIDEWAYS_ADJUST_MAX):
                continue
            
            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = volumes[wave1_high_idx:wave1_high_idx+adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0
            
            if is_standard_sideways:
                if vol_ratio >= SIDEWAYS_VOL_MAX:
                    continue
            else:
                if vol_ratio >= 3.0:
                    continue

            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = volumes[wave1_high_idx:wave1_high_idx+adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0

            if is_standard_sideways:
                if vol_ratio >= SIDEWAYS_VOL_MAX:
                    continue
            else:
                if vol_ratio >= 3.0:
                    continue

            # v3.6: 震荡蓄力突破 - 回调浅、震荡久、已突破一波高点
            # 广合科技典型：一波涨到174.87后回调5.2%到165.78，之后震荡上涨到199.53
            # 20260624收186.85已突破174.87，此时入场不算追高
            if low_pos >= 3 and low_pos <= SIDEWAYS_ADJUST_MAX and (low_pos + 10) < (n - wave1_high_idx):
                after_pullback = closes[entry_idx:]
                if len(after_pullback) >= 20:
                    # 震荡蓄力突破：波幅必须≥25%（v3.8），避免过低波幅的误标
                    # 光洋(20260624): 候选波峰42.36涨幅仅20.1%，先涨后跌再反弹→非横盘
                    if surge_gain < 0.25:
                        continue
                    if closes[n-1] >= wave1_high_price:
                        entry_idx = n - 1
                        adjust_days = entry_idx - wave1_high_idx
                        if adjust_days > 60:
                            continue
                        post_range_all = lows[wave1_high_idx:entry_idx+1]
                        new_low_all = post_range_all.min()
                        pullback_pct = (wave1_high_price - new_low_all) / wave1_high_price

            if today_only and entry_idx != n - 1:
                continue

            # ── 创新高检测（v2.1） ──
            # 调整期间是否突破一波高点
            new_high_confirmed = False
            new_high_pullback = False
            post_high_all = closes[wave1_high_idx:entry_idx + 1]
            if len(post_high_all) > 1:
                max_post = post_high_all.max()
                if max_post > wave1_high_price:
                    new_high_confirmed = True
                    # 创新高后回踩到当前价：新高回踩形态
                    new_high_idx_local = np.argmax(post_high_all)
                    if new_high_idx_local < len(post_high_all) - 1:
                        # 创新高后确实回踩了
                        new_high_pullback = True

                    ma20_key = [k for k in ['ma_bfq_20', 'ma20', 'ma_20'] if k in df.columns]
                    if ma20_key:
                        ma20_val = df[ma20_key[0]].iloc[entry_idx]
                        if ma20_val > 0:
                            ma20_dist = (closes[entry_idx] - ma20_val) / ma20_val
                            if ma20_dist > 0.15:
                                continue

            # ── 创新低检测（v2.3）──
            # 创新低 = 调整期最低价 ≤ 一波启动前最低价 → 主力出逃，直接过滤
            # 回测依据：不创新低胜率41.2%，创新低胜率16.7%
            wave1_start_idx = max(0, wave1_high_idx - 20)
            pre_low_start  = max(0, wave1_start_idx - 20)
            if wave1_high_idx >= 40:
                pre_low = closes[pre_low_start:wave1_start_idx+1].min()
            else:
                pre_low = closes[0:wave1_high_idx+1].min()
            adj_low        = closes[wave1_high_idx:entry_idx+1].min()
            is_higher_low  = adj_low > pre_low
            if not is_higher_low:
                # 创新低，主力出逃信号，跳过此候选
                continue

            # ── 距近日高点检查（v3.8）──────────────────────────
            # 当前价必须距近10日高点 ≤ 15%，确保仍处于强势横盘区间
            # 华宏科技(20260626): 近10日最高240.79，当前189.58(回撤21.6%) → 过滤
            recent_window = closes[max(0, entry_idx - 9):entry_idx + 1]
            recent_high = recent_window.max()
            if closes[entry_idx] < recent_high * 0.85:
                continue

            # ── 高点下降检测（v3.8）───────────────────────────
            # 近20日最高 < 近30日最高 * 0.95 → 高点逐波下降
            # 光洋(20260624): 20日最高47.67 < 30日最高50.18*0.95=47.67 → 过滤(刚好相等)
            high_30d = closes[-30:].max()
            high_20d = closes[-20:].max()
            if high_20d < high_30d * 0.95:
                continue

            # ── 除权检测（v3.8）───────────────────────────────
            # HFQ后复权价格距MA20超过30%可能是除权导致的价格失真
            # 时代新材(20260624): 距MA20+800% → 除权股，跳过
            # 光华科技(20260624): 距MA20+240% → 除权股，跳过
            ma20_key = [k for k in ['ma_bfq_20', 'ma20', 'ma_20'] if k in df.columns]
            if ma20_key:
                ma20_val = df[ma20_key[0]].iloc[entry_idx]
                if ma20_val > 0:
                    adj_factor = df['adj_factor'].iloc[entry_idx] if 'adj_factor' in df.columns else 1.0
                    ma20_bfq_actual = ma20_val * adj_factor
                    ma20_dist = abs(closes[entry_idx] - ma20_bfq_actual) / ma20_bfq_actual
                    if ma20_dist > 0.30:
                        continue

            # ── 强势横盘最优条件硬过滤（v3.4）────────────────
            # 标准强势横盘：回调2-10% + 一波20-60%
            # MA20支撑模式：回调2-25% + 一波20-80%
            surge_pct = round(surge_gain * 100, 1)
            if is_standard_sideways:
                if not (0.02 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
                    continue
            else:
                if not (0.02 <= pullback_pct < 0.25 and 20 <= surge_pct < 80):
                    continue

            # ── 多指标共振评分 ──
            prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
            gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
            # 深度回调-长期缩量盘整：调整>30天+MA250上方+量比<0.7
            is_long_consolidation = (adjust_days > 30 and
                                     closes[entry_idx] > df.iloc[entry_idx].get('ma250', 0) and
                                     vol_ratio < 0.7)
            limitup_score, volume_recovery_score = self._calc_limitup_features(df, entry_idx)
            # ATR占比（用于评分加分）
            row_sc = df.iloc[entry_idx]
            # v3.5: volume_ratio=0时用成交量/前5日均量替代
            row_sc = self._fix_volume_ratio(df, entry_idx, row_sc)
            total_mv_b = float(row_sc.get('total_mv', 0)) / 1e8
            atr_pct_sc = float(row_sc.get('atr_qfq', 0)) / float(row_sc.get('close_qfq', row_sc['close'])) if float(row_sc.get('close_qfq', 0)) > 0 else 0.02
            score_result = self.scorer.score(row_sc, prev_row,
                                              wave1_gain_pct=round(surge_gain * 100, 1),
                                              new_high_confirmed=new_high_confirmed,
                                              new_high_pullback=new_high_pullback,
                                              is_higher_low=is_higher_low,
                                              pattern_type='强势横盘',
                                              gap_to_peak_pct=gap_to_peak,
                                              pullback_pct=pullback_pct,
                                              is_deep_long_consolidation=is_long_consolidation,
                                              limitup_score=limitup_score,
                                              volume_recovery_score=volume_recovery_score,
                                              atr_pct=atr_pct_sc,
                                              market_cap_b=total_mv_b)

            # 底背离检测
            divs = self.scorer.check_divergence(df, entry_idx)
            for key, div in divs.items():
                if div.get('found'):
                    score_result['total'] += div['pts']
                    score_result['details'].append(f"{div['desc']}(+{div['pts']})")

            # DMI交叉检测
            dmi_cross = self.scorer.check_dmi_crossover(df, entry_idx)
            if dmi_cross.get('found'):
                score_result['total'] += dmi_cross['pts']
                score_result['details'].append(f"{dmi_cross['desc']}(+{dmi_cross['pts']})")

            # ── 板块形态适配加分 ──
            bonus_pts, bonus_desc = self._board_bonus(ts_code, '强势横盘')
            if bonus_pts != 0:
                score_result['total'] += bonus_pts
                if bonus_desc:
                    score_result['details'].append(bonus_desc)

            # 共振评分阈值过滤（分数已包含所有维度）
            if score_result['total'] < SCORE_SIDWAYS_MIN:
                continue

            row = df.iloc[entry_idx]
            rsi = float(row.get('rsi_qfq_6', 50))
            atr = float(row.get('atr_qfq', 0))
            # 入场价用未复权实际交易价
            entry_price = float(row.get('close_bfq', row['close']))
            # ATR止损比例基于前复权价（避免除权失真）
            close_qfq = float(row.get('close_qfq', row['close']))
            atr_pct = atr / close_qfq if close_qfq > 0 else 0.02
            stop_distance_pct = 2 * atr_pct
            stop_pct = max(0.02, min(0.08, stop_distance_pct))
            stop_price = round(entry_price * (1 - stop_pct), 2)
            target_price = round(entry_price * 1.30, 2)
            rr = round((target_price - entry_price) / (entry_price - stop_price), 1) if entry_price > stop_price else 10.0

            # 二波确认
            wave2_gain = wave2_60d_max = 0.0
            wave2_confirmed = False
            if entry_idx + WAVE2_WINDOW < n:
                post_low = closes[entry_idx:]
                wave2_gain = (post_low[WAVE2_WINDOW] - entry_price) / entry_price
                wave2_confirmed = wave2_gain >= WAVE2_MIN
                if entry_idx + min(60, n - entry_idx) < n:
                    wave2_60d_max = (closes[entry_idx:entry_idx+60].max() - entry_price) / entry_price

            # DMI二波确认
            dmi_confirmed = False
            if entry_idx + 3 < n:
                for check_idx in range(entry_idx + 1, min(entry_idx + 5, n)):
                    dc = self.scorer.check_dmi_crossover(df, check_idx)
                    if dc.get('found'):
                        dmi_confirmed = True
                        break

            confidence = '⭐⭐⭐⭐⭐' if (wave2_confirmed or dmi_confirmed) else '⭐⭐⭐⭐'
            if score_result['total'] >= 15:
                confidence = '⭐⭐⭐⭐⭐' + '🔥' if score_result['total'] >= 20 else '⭐⭐⭐⭐⭐'

            pattern_detail = '强势横盘'

            return {
                'ts_code':         ts_code,
                'pattern':         pattern_detail,
                'score':           score_result['total'],
                'score_details':   '; '.join(score_result['details']),
                'wave1_gain':     round(surge_gain * 100, 1),
                'pullback_pct':   round(pullback_pct * 100, 1),
                'adjust_days':    adjust_days,
                'rsi':            round(rsi, 1),
                'vol_ratio':      round(float(row.get('volume_ratio', 1.0)), 2),
                'atr':            round(atr, 2),
                'entry_price':    entry_price,
                'stop_loss':      stop_price,
                'stop_pct':       stop_pct,
                'target':         target_price,
                'rr':             rr,
                'wave2_gain':     round(wave2_gain * 100, 1),
                'wave2_confirmed': wave2_confirmed,
                'dmi_confirmed':   dmi_confirmed,
                'confidence':     confidence,
                'entry_date':     df.iloc[entry_idx]['trade_date'],
            }
        return None

    # ── 形态2: 深度回调（纯深度，不含放量/V型）──────────────────
    def detect_deep_pullback_pattern(self, ts_code: str, today_only: bool = False, target_date: str = '') -> Optional[dict]:
        """纯深度回调形态：回调>=20%，调整>=10天，非放量非V型"""
        df = self.load_data(ts_code, lookback=500)
        if df is None or len(df) < 60:
            return None

        if target_date:
            mask = df['trade_date'].astype(str) <= target_date
            if not mask.any():
                return None
            df = df[mask].copy()
            if len(df) < 60:
                return None

        # 计算MA120/MA250
        df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
        df['ma250'] = df['close'].rolling(250, min_periods=120).mean()

        closes  = df['close'].values
        volumes = df['vol'].values
        n = len(df)

        wave1_candidates = self._find_recent_wave1(closes, n)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
            wave1_high_price = closes[wave1_high_idx]

            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct  = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos       = int(np.argmin(post_high))
            adjust_days   = low_pos

            # 深度回调基本条件：回调>=20%，调整>=10天
            if not (pullback_pct >= DEEP_PULLBACK_MIN and adjust_days >= DEEP_ADJUST_MIN):
                continue

            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue
            if today_only and entry_idx != n - 1:
                continue

            # ── 排除放量回调形态 ──
            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            adj_vol = volumes[wave1_high_idx+1:entry_idx+1].mean()
            vol_ratio_adj = adj_vol / base_vol if base_vol > 0 else 1.0
            if vol_ratio_adj > 1.2 and 0.10 <= pullback_pct < 0.20:
                continue  # 放量回调形态，跳过

            # ── 排除V型急跌形态 ──
            if adjust_days <= 10 and pullback_pct >= 0.15:
                continue  # V型急跌形态，跳过

            # ── 创新低检测（v2.3）──
            wave1_start_idx = max(0, wave1_high_idx - 20)
            pre_low_start  = max(0, wave1_start_idx - 20)
            if wave1_high_idx >= 40:
                pre_low = closes[pre_low_start:wave1_start_idx+1].min()
            else:
                pre_low = closes[0:wave1_high_idx+1].min()
            adj_low        = closes[wave1_high_idx:entry_idx+1].min()
            is_higher_low  = adj_low > pre_low
            if not is_higher_low:
                continue  # 创新低，跳过

            # ── 多指标共振评分 ──
            prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
            gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
            # 深度回调的vol_ratio使用vol_ratio_adj（调整期均值/一波基期均值）
            is_long_consolidation = (adjust_days > 30 and
                                     closes[entry_idx] > df.iloc[entry_idx].get('ma250', 0) and
                                     vol_ratio_adj < 0.7)
            limitup_score, volume_recovery_score = self._calc_limitup_features(df, entry_idx)
            # ATR占比（用于评分加分）
            row_sc = df.iloc[entry_idx]
            # v3.5: volume_ratio=0时用成交量/前5日均量替代
            row_sc = self._fix_volume_ratio(df, entry_idx, row_sc)
            total_mv_b = float(row_sc.get('total_mv', 0)) / 1e8
            atr_pct_sc = float(row_sc.get('atr_qfq', 0)) / float(row_sc.get('close_qfq', row_sc['close'])) if float(row_sc.get('close_qfq', 0)) > 0 else 0.02
            score_result = self.scorer.score(row_sc, prev_row,
                                              wave1_gain_pct=round(surge_gain * 100, 1),
                                              new_high_confirmed=False,
                                              new_high_pullback=False,
                                              is_higher_low=is_higher_low,
                                              pattern_type='深度回调',
                                              gap_to_peak_pct=gap_to_peak,
                                              pullback_pct=pullback_pct,
                                              is_deep_long_consolidation=is_long_consolidation,
                                              limitup_score=limitup_score,
                                              volume_recovery_score=volume_recovery_score,
                                              atr_pct=atr_pct_sc,
                                              market_cap_b=total_mv_b)

            # 底背离
            divs = self.scorer.check_divergence(df, entry_idx)
            for key, div in divs.items():
                if div.get('found'):
                    score_result['total'] += div['pts']
                    score_result['details'].append(f"{div['desc']}(+{div['pts']})")

            # DMI交叉
            dmi_cross = self.scorer.check_dmi_crossover(df, entry_idx)
            if dmi_cross.get('found'):
                score_result['total'] += dmi_cross['pts']
                score_result['details'].append(f"{dmi_cross['desc']}(+{dmi_cross['pts']})")

            # ── 板块形态适配加分 ──
            bonus_pts, bonus_desc = self._board_bonus(ts_code, '深度回调')
            if bonus_pts != 0:
                score_result['total'] += bonus_pts
                if bonus_desc:
                    score_result['details'].append(bonus_desc)

            # 共振评分阈值过滤
            if score_result['total'] < SCORE_DEEP_MIN:
                continue

            row = df.iloc[entry_idx]
            rsi = float(row.get('rsi_qfq_6', 50))
            atr = float(row.get('atr_qfq', 0))
            # 入场价用未复权实际交易价
            entry_price = float(row.get('close_bfq', row['close']))
            # ATR止损比例基于前复权价（避免除权失真）
            close_qfq = float(row.get('close_qfq', row['close']))
            atr_pct = atr / close_qfq if close_qfq > 0 else 0.03
            stop_distance_pct = 2 * atr_pct
            stop_pct = max(0.03, min(0.12, stop_distance_pct))
            stop_price = round(entry_price * (1 - stop_pct), 2)
            target_price = round(entry_price * 1.25, 2)
            rr = round((target_price - entry_price) / (entry_price - stop_price), 1) if entry_price > stop_price else 5.0

            # 二波确认
            wave2_gain = wave2_60d_max = 0.0
            wave2_confirmed = False
            if entry_idx + WAVE2_WINDOW < n:
                post_low = closes[entry_idx:]
                wave2_gain = (post_low[WAVE2_WINDOW] - entry_price) / entry_price
                wave2_confirmed = wave2_gain >= WAVE2_MIN
                if entry_idx + min(60, n - entry_idx) < n:
                    wave2_60d_max = (closes[entry_idx:entry_idx+60].max() - entry_price) / entry_price

            # DMI二波确认
            dmi_confirmed = False
            if entry_idx + 3 < n:
                for check_idx in range(entry_idx + 1, min(entry_idx + 5, n)):
                    dc = self.scorer.check_dmi_crossover(df, check_idx)
                    if dc.get('found'):
                        dmi_confirmed = True
                        break

            confidence = '⭐⭐⭐⭐⭐' if (wave2_confirmed or dmi_confirmed) else '⭐⭐⭐⭐'
            if score_result['total'] >= 15:
                confidence = '⭐⭐⭐⭐⭐🔥'

            return {
                'ts_code':         ts_code,
                'pattern':         '深度回调',
                'score':           score_result['total'],
                'score_details':   '; '.join(score_result['details']),
                'wave1_gain':     round(surge_gain * 100, 1),
                'pullback_pct':   round(pullback_pct * 100, 1),
                'adjust_days':    adjust_days,
                'rsi':            round(rsi, 1),
                'vol_ratio':      round(float(row.get('volume_ratio', 1.0)), 2),
                'atr':            round(atr, 2),
                'entry_price':    entry_price,
                'stop_loss':      stop_price,
                'stop_pct':       stop_pct,
                'target':         target_price,
                'rr':             rr,
                'wave2_gain':     round(wave2_gain * 100, 1),
                'wave2_confirmed': wave2_confirmed,
                'dmi_confirmed':   dmi_confirmed,
                'confidence':     confidence,
                'entry_date':     df.iloc[entry_idx]['trade_date'],
            }
        return None

    # ── 形态3: 放量回调（独立形态）────────────────────────────
    def detect_volume_pullback_pattern(self, ts_code: str, today_only: bool = False, target_date: str = '') -> Optional[dict]:
        """放量回调形态：回调10-25%，量比>1.2，胜率91.2%"""
        df = self.load_data(ts_code, lookback=500)
        if df is None or len(df) < 60:
            return None

        if target_date:
            mask = df['trade_date'].astype(str) <= target_date
            if not mask.any():
                return None
            df = df[mask].copy()
            if len(df) < 60:
                return None
        df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
        df['ma250'] = df['close'].rolling(250, min_periods=120).mean()

        closes  = df['close'].values
        volumes = df['vol'].values
        n = len(df)

        wave1_candidates = self._find_recent_wave1(closes, n)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
            wave1_high_price = closes[wave1_high_idx]

            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct  = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos       = int(np.argmin(post_high))
            adjust_days   = low_pos

            # 放量回调条件：回调10-<20%，调整>=10天（排除20-25%重叠区）
            if not (0.10 <= pullback_pct < 0.20 and adjust_days >= DEEP_ADJUST_MIN):
                continue

            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue
            if today_only and entry_idx != n - 1:
                continue

            # ── 放量检测 ──
            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            adj_vol = volumes[wave1_high_idx+1:entry_idx+1].mean()
            vol_ratio_adj = adj_vol / base_vol if base_vol > 0 else 1.0
            if vol_ratio_adj <= 1.2:  # 必须放量
                continue

            # ── 创新低检测 ──
            wave1_start_idx = max(0, wave1_high_idx - 20)
            pre_low_start  = max(0, wave1_start_idx - 20)
            if wave1_high_idx >= 40:
                pre_low = closes[pre_low_start:wave1_start_idx+1].min()
            else:
                pre_low = closes[0:wave1_high_idx+1].min()
            adj_low        = closes[wave1_high_idx:entry_idx+1].min()
            is_higher_low  = adj_low > pre_low
            if not is_higher_low:
                continue

            # ── 多指标共振评分 ──
            prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
            gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
            # 放量回调的vol_ratio使用vol_ratio_adj
            is_long_consolidation = (adjust_days > 30 and
                                     closes[entry_idx] > df.iloc[entry_idx].get('ma250', 0) and
                                     vol_ratio_adj < 0.7)
            limitup_score, volume_recovery_score = self._calc_limitup_features(df, entry_idx)
            # ATR占比（用于评分加分）
            row_sc = df.iloc[entry_idx]
            # v3.5: volume_ratio=0时用成交量/前5日均量替代
            row_sc = self._fix_volume_ratio(df, entry_idx, row_sc)
            total_mv_b = float(row_sc.get('total_mv', 0)) / 1e8
            atr_pct_sc = float(row_sc.get('atr_qfq', 0)) / float(row_sc.get('close_qfq', row_sc['close'])) if float(row_sc.get('close_qfq', 0)) > 0 else 0.02
            score_result = self.scorer.score(row_sc, prev_row,
                                              wave1_gain_pct=round(surge_gain * 100, 1),
                                              new_high_confirmed=False,
                                              new_high_pullback=False,
                                              is_higher_low=is_higher_low,
                                              pattern_type='放量回调',
                                              gap_to_peak_pct=gap_to_peak,
                                              pullback_pct=pullback_pct,
                                              is_deep_long_consolidation=is_long_consolidation,
                                              limitup_score=limitup_score,
                                              volume_recovery_score=volume_recovery_score,
                                              atr_pct=atr_pct_sc,
                                              market_cap_b=total_mv_b)

            # 底背离
            divs = self.scorer.check_divergence(df, entry_idx)
            for key, div in divs.items():
                if div.get('found'):
                    score_result['total'] += div['pts']
                    score_result['details'].append(f"{div['desc']}(+{div['pts']})")

            # DMI交叉
            dmi_cross = self.scorer.check_dmi_crossover(df, entry_idx)
            if dmi_cross.get('found'):
                score_result['total'] += dmi_cross['pts']
                score_result['details'].append(f"{dmi_cross['desc']}(+{dmi_cross['pts']})")

            # 板块加分
            bonus_pts, bonus_desc = self._board_bonus(ts_code, '放量回调')
            if bonus_pts != 0:
                score_result['total'] += bonus_pts
                if bonus_desc:
                    score_result['details'].append(bonus_desc)

            # 双创板放量回调阈值40分，主板保持10分
            is_gem = ts_code.startswith('3')  # 创业板
            is_star = ts_code.startswith('688')  # 科创板
            threshold = SCORE_VOL_PULLBACK_GEM_MIN if (is_gem or is_star) else SCORE_DEEP_MIN
            
            if score_result['total'] < threshold:
                continue

            row = df.iloc[entry_idx]
            rsi = float(row.get('rsi_qfq_6', 50))
            atr = float(row.get('atr_qfq', 0))
            entry_price = float(row.get('close_bfq', row['close']))
            close_qfq = float(row.get('close_qfq', row['close']))
            atr_pct = atr / close_qfq if close_qfq > 0 else 0.03
            stop_pct = max(0.03, min(0.12, 2 * atr_pct))
            stop_price = round(entry_price * (1 - stop_pct), 2)
            target_price = round(entry_price * 1.25, 2)
            rr = round((target_price - entry_price) / (entry_price - stop_price), 1) if entry_price > stop_price else 5.0

            wave2_gain = wave2_60d_max = 0.0
            wave2_confirmed = False
            if entry_idx + WAVE2_WINDOW < n:
                post_low = closes[entry_idx:]
                wave2_gain = (post_low[WAVE2_WINDOW] - entry_price) / entry_price
                wave2_confirmed = wave2_gain >= WAVE2_MIN
                if entry_idx + min(60, n - entry_idx) < n:
                    wave2_60d_max = (closes[entry_idx:entry_idx+60].max() - entry_price) / entry_price

            dmi_confirmed = False
            if entry_idx + 3 < n:
                for check_idx in range(entry_idx + 1, min(entry_idx + 5, n)):
                    dc = self.scorer.check_dmi_crossover(df, check_idx)
                    if dc.get('found'):
                        dmi_confirmed = True
                        break

            confidence = '⭐⭐⭐⭐⭐' if (wave2_confirmed or dmi_confirmed) else '⭐⭐⭐⭐'
            if score_result['total'] >= 15:
                confidence = '⭐⭐⭐⭐⭐🔥'

            return {
                'ts_code':         ts_code,
                'pattern':         '放量回调',
                'score':           score_result['total'],
                'score_details':   '; '.join(score_result['details']),
                'wave1_gain':     round(surge_gain * 100, 1),
                'pullback_pct':   round(pullback_pct * 100, 1),
                'adjust_days':    adjust_days,
                'rsi':            round(rsi, 1),
                'vol_ratio':      round(float(row.get('volume_ratio', 1.0)), 2),
                'atr':            round(atr, 2),
                'entry_price':    entry_price,
                'stop_loss':      stop_price,
                'stop_pct':       stop_pct,
                'target':         target_price,
                'rr':             rr,
                'wave2_gain':     round(wave2_gain * 100, 1),
                'wave2_confirmed': wave2_confirmed,
                'dmi_confirmed':   dmi_confirmed,
                'confidence':     confidence,
                'entry_date':     df.iloc[entry_idx]['trade_date'],
            }
        return None

    # ── 形态4: V型急跌（独立形态）────────────────────────────
    def detect_vshape_pattern(self, ts_code: str, today_only: bool = False, target_date: str = '') -> Optional[dict]:
        """V型急跌形态：调整<=10天，回调>=15%，胜率97.2%"""
        df = self.load_data(ts_code, lookback=500)
        if df is None or len(df) < 60:
            return None

        if target_date:
            mask = df['trade_date'].astype(str) <= target_date
            if not mask.any():
                return None
            df = df[mask].copy()
            if len(df) < 60:
                return None
        df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
        df['ma250'] = df['close'].rolling(250, min_periods=120).mean()

        closes  = df['close'].values
        volumes = df['vol'].values
        n = len(df)

        wave1_candidates = self._find_recent_wave1(closes, n)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
            wave1_high_price = closes[wave1_high_idx]

            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct  = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos       = int(np.argmin(post_high))
            adjust_days   = low_pos

            # V型急跌条件：调整5-10天，回调>=15%
            # 优化：最小调整天数从0增加到5天，防止下跌动能过大（强瑞技术4天急跌19.7%失败案例）
            if not (5 <= adjust_days <= 10 and pullback_pct >= 0.15):
                continue

            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue
            if today_only and entry_idx != n - 1:
                continue

            # ── 创新低检测 ──
            wave1_start_idx = max(0, wave1_high_idx - 20)
            pre_low_start  = max(0, wave1_start_idx - 20)
            if wave1_high_idx >= 40:
                pre_low = closes[pre_low_start:wave1_start_idx+1].min()
            else:
                pre_low = closes[0:wave1_high_idx+1].min()
            adj_low        = closes[wave1_high_idx:entry_idx+1].min()
            is_higher_low  = adj_low > pre_low
            if not is_higher_low:
                continue

            # ── 多指标共振评分 ──
            prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
            gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
            # V型急跌调整期很短(≤10天)，adjust_days>30不成立，但保持完整逻辑避免NameError
            vol_base = max(0, wave1_high_idx - 20)
            base_v = volumes[vol_base:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            adj_v = volumes[wave1_high_idx+1:entry_idx+1].mean()
            vratio_local = adj_v / base_v if base_v > 0 else 1.0
            is_long_consolidation = (adjust_days > 30 and
                                     closes[entry_idx] > df.iloc[entry_idx].get('ma250', 0) and
                                     vratio_local < 0.7)
            limitup_score, volume_recovery_score = self._calc_limitup_features(df, entry_idx)
            # ATR占比（用于评分加分）
            row_sc = df.iloc[entry_idx]
            # v3.5: volume_ratio=0时用成交量/前5日均量替代
            row_sc = self._fix_volume_ratio(df, entry_idx, row_sc)
            total_mv_b = float(row_sc.get('total_mv', 0)) / 1e8
            atr_pct_sc = float(row_sc.get('atr_qfq', 0)) / float(row_sc.get('close_qfq', row_sc['close'])) if float(row_sc.get('close_qfq', 0)) > 0 else 0.02
            score_result = self.scorer.score(row_sc, prev_row,
                                              wave1_gain_pct=round(surge_gain * 100, 1),
                                              new_high_confirmed=False,
                                              new_high_pullback=False,
                                              is_higher_low=is_higher_low,
                                              pattern_type='V型急跌',
                                              gap_to_peak_pct=gap_to_peak,
                                              pullback_pct=pullback_pct,
                                              is_deep_long_consolidation=is_long_consolidation,
                                              limitup_score=limitup_score,
                                              volume_recovery_score=volume_recovery_score,
                                              atr_pct=atr_pct_sc,
                                              market_cap_b=total_mv_b)

            # 底背离
            divs = self.scorer.check_divergence(df, entry_idx)
            for key, div in divs.items():
                if div.get('found'):
                    score_result['total'] += div['pts']
                    score_result['details'].append(f"{div['desc']}(+{div['pts']})")

            # DMI交叉
            dmi_cross = self.scorer.check_dmi_crossover(df, entry_idx)
            if dmi_cross.get('found'):
                score_result['total'] += dmi_cross['pts']
                score_result['details'].append(f"{dmi_cross['desc']}(+{dmi_cross['pts']})")

            # 板块加分
            bonus_pts, bonus_desc = self._board_bonus(ts_code, 'V型急跌')
            if bonus_pts != 0:
                score_result['total'] += bonus_pts
                if bonus_desc:
                    score_result['details'].append(bonus_desc)

            # 双创板V型急跌阈值40分，主板保持10分
            is_gem = ts_code.startswith('3')  # 创业板
            is_star = ts_code.startswith('688')  # 科创板
            threshold = SCORE_VSHAPE_GEM_MIN if (is_gem or is_star) else SCORE_DEEP_MIN
            
            if score_result['total'] < threshold:
                continue

            row = df.iloc[entry_idx]
            rsi = float(row.get('rsi_qfq_6', 50))
            atr = float(row.get('atr_qfq', 0))
            entry_price = float(row.get('close_bfq', row['close']))
            close_qfq = float(row.get('close_qfq', row['close']))
            atr_pct = atr / close_qfq if close_qfq > 0 else 0.03
            stop_pct = max(0.03, min(0.12, 2 * atr_pct))
            stop_price = round(entry_price * (1 - stop_pct), 2)
            target_price = round(entry_price * 1.25, 2)
            rr = round((target_price - entry_price) / (entry_price - stop_price), 1) if entry_price > stop_price else 5.0

            wave2_gain = wave2_60d_max = 0.0
            wave2_confirmed = False
            if entry_idx + WAVE2_WINDOW < n:
                post_low = closes[entry_idx:]
                wave2_gain = (post_low[WAVE2_WINDOW] - entry_price) / entry_price
                wave2_confirmed = wave2_gain >= WAVE2_MIN
                if entry_idx + min(60, n - entry_idx) < n:
                    wave2_60d_max = (closes[entry_idx:entry_idx+60].max() - entry_price) / entry_price

            dmi_confirmed = False
            if entry_idx + 3 < n:
                for check_idx in range(entry_idx + 1, min(entry_idx + 5, n)):
                    dc = self.scorer.check_dmi_crossover(df, check_idx)
                    if dc.get('found'):
                        dmi_confirmed = True
                        break

            confidence = '⭐⭐⭐⭐⭐' if (wave2_confirmed or dmi_confirmed) else '⭐⭐⭐⭐'
            if score_result['total'] >= 15:
                confidence = '⭐⭐⭐⭐⭐🔥'

            return {
                'ts_code':         ts_code,
                'pattern':         'V型急跌',
                'score':           score_result['total'],
                'score_details':   '; '.join(score_result['details']),
                'wave1_gain':     round(surge_gain * 100, 1),
                'pullback_pct':   round(pullback_pct * 100, 1),
                'adjust_days':    adjust_days,
                'rsi':            round(rsi, 1),
                'vol_ratio':      round(float(row.get('volume_ratio', 1.0)), 2),
                'atr':            round(atr, 2),
                'entry_price':    entry_price,
                'stop_loss':      stop_price,
                'stop_pct':       stop_pct,
                'target':         target_price,
                'rr':             rr,
                'wave2_gain':     round(wave2_gain * 100, 1),
                'wave2_confirmed': wave2_confirmed,
                'dmi_confirmed':   dmi_confirmed,
                'confidence':     confidence,
                'entry_date':     df.iloc[entry_idx]['trade_date'],
            }
        return None

    # ── 批量扫描 ──────────────────────────────────────────────────
    def scan_pool(self, ts_codes: list,
                  pattern: Literal['sideways', 'deep', 'volume', 'vshape', 'all'] = 'all',
                  pool_name: str = '',
                  today_only: bool = False) -> pd.DataFrame:
        """扫描股票池，检测四种二波形态

        形态类型（v2.6四形态并列）：
          - 强势横盘: 主板优选，胜率98.6%
          - 深度回调: 双创优选，胜率87.2%
          - 放量回调: 胜率91.2%
          - V型急跌: 胜率97.2%
        """
        results = []
        total = len(ts_codes)
        print(f"\n{'='*60}")
        print(f"  二波形态扫描v2.9 | 池: {pool_name or '自定义'} | 共 {total} 只"
              f"{' | 仅今日' if today_only else ''}")
        print(f"{'='*60}")
        t0 = time.time()

        # 预先获取股票名称（从本地缓存读取，不调API）
        name_map = {}
        try:
            cache_path = os.path.join(CACHE_DIR, 'stock_basic.csv')
            if os.path.exists(cache_path):
                sb = pd.read_csv(cache_path)
                if not sb.empty and 'ts_code' in sb.columns and 'name' in sb.columns:
                    name_map = dict(zip(sb['ts_code'], sb['name']))
        except Exception:
            pass

        # 扫描前静默预加载所有股票数据到缓存
        trade_date = get_effective_date(self.force_date)
        trade_date_dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
        start = (trade_date_dt - datetime.timedelta(days=501)).strftime('%Y%m%d')
        preload_needed = 0
        for code in ts_codes:
            if code.startswith(('8', '4')) or code.startswith('9'):
                continue
            cached_min, cached_max = sc.get_stk_factor_range(code)
            if not cached_min or cached_min > start:
                preload_needed += 1
        if preload_needed > 0:
            print(f"  预加载 {preload_needed}/{total} 只到 SQLite 缓存（并发10线程）...")
            preload_codes = []
            for code in ts_codes:
                if code.startswith(('8', '4')) or code.startswith('9'):
                    continue
                cached_min, cached_max = sc.get_stk_factor_range(code)
                if not cached_min or cached_min > start:
                    preload_codes.append(code)
            loaded = [0]
            def _preload_one(code):
                sc.cached_stk_factor_pro(code, start, trade_date, silent=True)
                loaded[0] += 1
                if loaded[0] % 100 == 0:
                    print(f"  缓存预加载 {loaded[0]}/{preload_needed}...")
            from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
            with ThreadPoolExecutor(max_workers=10) as ex:
                futures = {ex.submit(_preload_one, code): code for code in preload_codes}
                for f in _as_completed(futures):
                    try: f.result()
                    except Exception: pass
            print(f"  缓存预加载完成")

        # 全局去重：同一天同一只股票只保留评分最高的
        seen_signals = {}
        
        for i, code in enumerate(ts_codes):
            if (i + 1) % 50 == 0 or i == 0:
                eta = (time.time() - t0) / max(i + 1, 1) * (total - i - 1) if i > 0 else 0
                print(f"  进度 {i+1}/{total} ({code})  ETA {eta:.0f}s")

            # ⚠️ 北交所股票跳过（不交易）
            # 北交所代码规则：8xxxxx、4xxxxx、9xxxxx（含92开头的北交所新股）
            if code.startswith(('8', '4')) or (code.startswith('9')):
                continue

            # ── 预加载一次数据，共享给4个detect方法 ────────────
            self._scan_data = self.load_data(code, lookback=500)
            if self._scan_data is None:
                self._scan_data = None
                continue

            stock_patterns = set()
            
            if pattern in ('sideways', 'all'):
                r = self.detect_sideways_pattern(code, today_only=today_only)
                if r:
                    r['name'] = name_map.get(code, '')
                    key = (code, r['entry_date'])
                    if key not in seen_signals or r['score'] > seen_signals[key]['score']:
                        seen_signals[key] = r
                    stock_patterns.add(r['pattern'])

            if pattern in ('deep', 'all'):
                r = self.detect_deep_pullback_pattern(code, today_only=today_only)
                if r and '深度回调' not in stock_patterns:
                    r['name'] = name_map.get(code, '')
                    key = (code, r['entry_date'])
                    if key not in seen_signals or r['score'] > seen_signals[key]['score']:
                        seen_signals[key] = r
                    stock_patterns.add(r['pattern'])

            if pattern in ('volume', 'all'):
                r = self.detect_volume_pullback_pattern(code, today_only=today_only)
                if r and '放量回调' not in stock_patterns:
                    r['name'] = name_map.get(code, '')
                    key = (code, r['entry_date'])
                    if key not in seen_signals or r['score'] > seen_signals[key]['score']:
                        seen_signals[key] = r
                    stock_patterns.add(r['pattern'])

            if pattern in ('vshape', 'all'):
                r = self.detect_vshape_pattern(code, today_only=today_only)
                if r and 'V型急跌' not in stock_patterns:
                    r['name'] = name_map.get(code, '')
                    key = (code, r['entry_date'])
                    if key not in seen_signals or r['score'] > seen_signals[key]['score']:
                        seen_signals[key] = r
                    stock_patterns.add(r['pattern'])

            time.sleep(0.02)
            self._scan_data = None  # 清除共享数据，下一只重新加载

        elapsed = time.time() - t0
        # 合并去重后的信号
        results = list(seen_signals.values())
        df = pd.DataFrame(results)
        print(f"\n  扫描完成！耗时 {elapsed:.1f}s，找到 {len(df)} 只信号")
        return df


# ═══════════════════════════════════════════════════════════════════
# 预设股票池
# ═══════════════════════════════════════════════════════════════════
def get_hs300_pool() -> list:
    cache = r'D:\mystock\dragon\cache\csi2000_stocks.pkl'
    try:
        if os.path.exists(cache):
            with open(cache, 'rb') as f:
                data = pickle.load(f)
            codes = list(data) if isinstance(data, set) else list(data.values()) if isinstance(data, dict) else []
            codes = [c for c in codes if isinstance(c, str)]
            print(f"  沪深300池(CSI2000缓存): {len(codes)} 只")
            return codes
    except Exception:
        pass
    try:
        sb = sc.load_stock_basic()
        if sb is not None:
            codes = sb[~sb['ts_code'].str.startswith('688')]['ts_code'].tolist()[:200]
            print(f"  沪深300池(本地缓存): {len(codes)} 只")
            return codes
    except Exception:
        pass
    return []


def get_gem_kc_pool() -> list:
    try:
        sb = sc.load_stock_basic()
        if sb is not None:
            cy = sb[sb['ts_code'].str.startswith(('300', '688'))]
            codes = cy['ts_code'].tolist()
            print(f"  双创板(本地缓存): {len(codes)} 只")
            return codes
    except Exception:
        pass
    return []


def get_hot_leaders(n: int = 50) -> list:
    cache_dir = r'D:\mystock\dragon\cache'
    try:
        files = sorted([f for f in os.listdir(cache_dir)
                       if f.startswith('ths_all_concepts_')], reverse=True)
        if not files:
            return []
        with open(os.path.join(cache_dir, files[0]), 'rb') as f:
            data = pickle.load(f)
        if isinstance(data, pd.DataFrame):
            data = data.sort_values('pct_change', ascending=False)
            top_concepts = data.head(5)['ts_code'].tolist()
            leaders = []
            for ccode in top_concepts:
                mfile = os.path.join(cache_dir, f'ths_member_{ccode}.pkl')
                if os.path.exists(mfile):
                    with open(mfile, 'rb') as f:
                        mdf = pickle.load(f)
                    leaders.extend(mdf['con_code'].tolist())
            codes = list(set(leaders))[:n]
            print(f"  近期强势龙头: {len(codes)} 只")
            return codes
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════════════════════
# 从CSV读取股票池
# ═══════════════════════════════════════════════════════════════════
def load_stocks_from_csv(csv_path: str) -> list:
    """从CSV文件读取股票代码列表，自动识别ts_code/code列"""
    if not os.path.exists(csv_path):
        print(f"  CSV文件不存在: {csv_path}")
        return []
    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        for col in ['ts_code', 'code', '股票代码', '代码']:
            if col in df.columns:
                codes = df[col].dropna().unique().tolist()
                # 统一格式化：补齐6位+交易所后缀
                formatted = []
                for c in codes:
                    c = str(c).strip().zfill(6)
                    if not c.endswith('.SH') and not c.endswith('.SZ'):
                        if c.startswith(('60', '688')):
                            c += '.SH'
                        else:
                            c += '.SZ'
                    formatted.append(c)
                print(f"  从 {os.path.basename(csv_path)} 读取 {len(formatted)} 只股票")
                return formatted
        print(f"  CSV缺少股票代码列(ts_code/code)，列: {df.columns.tolist()}")
        return []
    except Exception as e:
        print(f"  CSV读取失败: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════
# PDF报告生成
# ═══════════════════════════════════════════════════════════════════
def _add_market_overview(elements, font_name, data_date, styles):
    """从market_analysis.db读取昨日大盘概览，插入PDF"""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer
    import sqlite3

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'cache_backbone_tushare', 'market_analysis.db')
    db_path = os.path.abspath(db_path)
    print(f"[_add_market_overview] db_path={db_path}, exists={os.path.exists(db_path)}")
    if not os.path.exists(db_path):
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 读取overall_analysis最新一行
        cur.execute('SELECT * FROM overall_analysis ORDER BY trade_date DESC LIMIT 1')
        oa = cur.fetchone()

        # 读取limit_stats最新一行
        cur.execute('SELECT * FROM limit_stats ORDER BY trade_date DESC LIMIT 1')
        ls = cur.fetchone()

        conn.close()

        print(f"[_add_market_overview] oa={dict(oa) if oa else None}")
        print(f"[_add_market_overview] ls={dict(ls) if ls else None}")
        if not oa:
            return

        trade_date = oa['trade_date']
        market_status = oa['market_status'] or ''
        position = oa['total_position'] or ''
        index_trend = f"{oa['index_trend']:.0f}" if oa['index_trend'] else ''
        theme_trend = f"{oa['theme_trend']:.0f}" if oa['theme_trend'] else ''
        trend_score = f"{oa['trend_score']:.1f}" if oa['trend_score'] else ''

        zt = ls['zt_count'] if ls else '?'
        dt = ls['dt_count'] if ls else '?'
        up = ls['up_count'] if ls else '?'
        down = ls['down_count'] if ls else '?'

        overview = (
            f"数据{trade_date} | {market_status} | 趋势分{trend_score} "
            f"(指数{index_trend}/主题{theme_trend}) | 仓位{position}% | "
            f"涨停{zt} 跌停{dt} | 上涨{up} 下跌{down}"
        )

        ov_style = ParagraphStyle('OV', parent=styles['Normal'],
            fontName=font_name, fontSize=9, alignment=0,
            textColor=colors.HexColor('#2c3e50'),
            borderWidth=1, borderColor=colors.HexColor('#3498db'),
            borderPadding=6, backColor=colors.HexColor('#ebf5fb'))
        elements.append(Paragraph(f'📊 大盘概览：{overview}', ov_style))
        elements.append(Spacer(1, 3*mm))
    except Exception as e:
        # 静默失败，不影响PDF生成
        import traceback
        print(f"[_add_market_overview] ERROR: {e}")
        traceback.print_exc()


def generate_pdf_report(all_results: list, total_scanned: int, csv_name: str = '', force_date: str = ''):
    """生成PDF分析报告（按共振评分降序排列）"""
    # 按共振评分降序排序
    all_results = sorted(all_results, key=lambda x: x.get('score', 0), reverse=True)
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_paths = [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc']
    font_name = 'Helvetica'
    for fp in font_paths:
        try:
            pdfmetrics.registerFont(TTFont('CNFont', fp))
            font_name = 'CNFont'
            break
        except:
            continue

    # 报告日期
    scan_date = get_effective_date(force_date)
    if all_results:
        data_dates = [r.get('entry_date', '') for r in all_results if r.get('entry_date')]
        if data_dates:
            data_date = max(data_dates)
        else:
            data_date = scan_date
    else:
        data_date = scan_date
    today_str = data_date  # 报告显示数据日期
    csv_tag = f'_{os.path.splitext(os.path.basename(csv_name))[0]}' if csv_name else ''
    timestamp = time.strftime('%H%M%S')
    pdf_path = os.path.join(OUT_DIR, f'wave2_pattern{csv_tag}_{scan_date}_{timestamp}.pdf')

    doc = SimpleDocTemplate(pdf_path, pagesize=landscape(A4),
        topMargin=15*mm, bottomMargin=15*mm, leftMargin=10*mm, rightMargin=10*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TCN', parent=styles['Title'],
        fontName=font_name, fontSize=18, alignment=1, spaceAfter=6*mm)
    sub_style = ParagraphStyle('SCN', parent=styles['Normal'],
        fontName=font_name, fontSize=10, alignment=1, spaceAfter=4*mm)
    hdr_style = ParagraphStyle('HCN', parent=styles['Normal'],
        fontName=font_name, fontSize=8, alignment=1)
    cel_style = ParagraphStyle('CCN', parent=styles['Normal'],
        fontName=font_name, fontSize=7.5, alignment=1)

    elements = []
    elements.append(Paragraph("二波形态精选扫描报告 (共振评分版)", title_style))
    elements.append(Paragraph(
        f"数据日期: {today_str}  |  扫描: {total_scanned}只  |  信号: {len(all_results)}个"
        f"{'  |  CSV: ' + os.path.basename(csv_name) if csv_name else ''}",
        sub_style))
    elements.append(Spacer(1, 3*mm))

    # ── 昨日大盘概览（从market_analysis缓存读取）──────────────
    _add_market_overview(elements, font_name, today_str, styles)

    # ── 今日精选 TOP3 ──────────────────────────────
    # 主板强势横盘TOP3 + 双创V型急跌TOP3 + 双创放量回调TOP3
    main_sideways = [r for r in all_results
                     if r['pattern'] == '强势横盘' and r['ts_code'].startswith(('600', '601', '603', '605', '000', '002'))]
    gem_vshape = [r for r in all_results
                  if r['pattern'] == 'V型急跌' and r['ts_code'].startswith(('688', '300', '301'))]
    gem_volume = [r for r in all_results
                  if r['pattern'] == '放量回调' and r['ts_code'].startswith(('688', '300', '301'))]
    main_sideways = sorted(main_sideways, key=lambda x: x.get('score', 0), reverse=True)[:3]
    gem_vshape = sorted(gem_vshape, key=lambda x: x.get('score', 0), reverse=True)[:3]
    gem_volume = sorted(gem_volume, key=lambda x: x.get('score', 0), reverse=True)[:3]

    if main_sideways or gem_vshape or gem_volume:
        pick_title_style = ParagraphStyle('PICK_T', parent=styles['Normal'],
            fontName=font_name, fontSize=13, alignment=0, spaceAfter=2*mm,
            textColor=colors.HexColor('#1a5276'))
        pick_style = ParagraphStyle('PICK', parent=styles['Normal'],
            fontName=font_name, fontSize=10, alignment=0, spaceAfter=1*mm,
            textColor=colors.HexColor('#2c3e50'), leading=14)
        pick_highlight = ParagraphStyle('PICK_H', parent=styles['Normal'],
            fontName=font_name, fontSize=10, alignment=0, spaceAfter=1*mm,
            textColor=colors.HexColor('#c0392b'), leading=14)

        elements.append(Paragraph('⭐ 今日精选', pick_title_style))

        if main_sideways:
            elements.append(Paragraph('【主板强势横盘 TOP3】(成功率98.6%, 盈亏比19.9x)', pick_highlight))
            for i, r in enumerate(main_sideways, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"入场{r.get('entry_date','')}  价格{r['entry_price']:.2f}  "
                    f"止损{r['stop_loss']:.2f}  目标{r['target']:.2f}",
                    pick_style))

        if gem_vshape:
            elements.append(Paragraph('【双创V型急跌 TOP3】(成功率97.2%, 盈亏比16.1x)', pick_highlight))
            for i, r in enumerate(gem_vshape, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"入场{r.get('entry_date','')}  价格{r['entry_price']:.2f}  "
                    f"止损{r['stop_loss']:.2f}  目标{r['target']:.2f}",
                    pick_style))

        if gem_volume:
            elements.append(Paragraph('【双创放量回调 TOP3】(成功率91.2%, 盈亏比14.5x)', pick_highlight))
            for i, r in enumerate(gem_volume, 1):
                name = r.get('name', '') or r['ts_code']
                elements.append(Paragraph(
                    f"  {i}. {r['ts_code']} {name}  评分{r['score']}  "
                    f"一波+{r['wave1_gain']:.0f}%  回调-{r['pullback_pct']:.0f}%  "
                    f"入场{r.get('entry_date','')}  价格{r['entry_price']:.2f}  "
                    f"止损{r['stop_loss']:.2f}  目标{r['target']:.2f}",
                    pick_style))

        elements.append(Spacer(1, 3*mm))

    # ── 操作建议（基于16,828样本回测结论）──────────────────
    tip_style = ParagraphStyle('TIP', parent=styles['Normal'],
        fontName=font_name, fontSize=9, alignment=0,
        textColor=colors.HexColor('#c0392b'),
        borderWidth=1, borderColor=colors.HexColor('#e74c3c'),
        borderPadding=6, backColor=colors.HexColor('#fdf2f2'))
    elements.append(Paragraph(
        '💡 操作建议（基于52,949样本回测）：'
        '双创板(688/300/301)优选<b>V型急跌</b>（成功率97.2%，+8分）或<b>强势横盘</b>（成功率93.3%，+3分）；'
        '主板(60x/000/002)优选<b>强势横盘</b>（成功率98.6%，+5分）；'
        '深度回调和放量回调为中性选择（0分），深度回调在双创已降权(-2分)。'
        '通用最强信号：RSI低位回升+MA20上方+不创新低 → 二波几乎必出',
        tip_style))
    elements.append(Spacer(1, 4*mm))

    if not all_results:
        elements.append(Paragraph("今日无二波信号", cel_style))
        doc.build(elements)
        print(f"  PDF: {pdf_path}")
        return pdf_path

    # 形态分布
    pc = {}
    for r in all_results:
        p = r['pattern']
        pc[p] = pc.get(p, 0) + 1
    summary = "形态分布: " + " | ".join(f"{p}: {c}只" for p, c in sorted(pc.items(), key=lambda x: -x[1]))
    elements.append(Paragraph(summary, sub_style))
    elements.append(Spacer(1, 3*mm))

    headers = ['股票代码', '股票名称', '形态', '共振评分', '一波涨幅%', '回调%', '调整天数',
               'RSI', '入场日期', '入场价', '止损价', '目标价', '盈亏比']
    col_widths = [26*mm, 22*mm, 18*mm, 16*mm, 16*mm, 14*mm, 14*mm,
                  12*mm, 20*mm, 18*mm, 18*mm, 18*mm, 14*mm]

    data_rows = [[Paragraph(h, hdr_style) for h in headers]]
    for r in all_results:
        data_rows.append([
            Paragraph(r['ts_code'], hdr_style),
            Paragraph(r.get('name', ''), cel_style),
            Paragraph(r['pattern'], cel_style),
            Paragraph(f"{r['score']}", cel_style),
            Paragraph(f"+{r['wave1_gain']:.1f}", cel_style),
            Paragraph(f"{r['pullback_pct']:.1f}", cel_style),
            Paragraph(f"{r['adjust_days']}", cel_style),
            Paragraph(f"{r['rsi']:.1f}", cel_style),
            Paragraph(f"{r.get('entry_date', '')}", cel_style),
            Paragraph(f"{r['entry_price']:.2f}", cel_style),
            Paragraph(f"{r['stop_loss']:.2f}", cel_style),
            Paragraph(f"{r['target']:.2f}", cel_style),
            Paragraph(f"{r['rr']:.1f}x", cel_style),
        ])

    t = Table(data_rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    for i in range(min(3, len(all_results))):
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, i+1), (-1, i+1), colors.HexColor('#d4efdf')),
        ]))
    elements.append(t)
    elements.append(Spacer(1, 4*mm))
    note_style = ParagraphStyle('NCN', parent=styles['Normal'],
        fontName=font_name, fontSize=8, textColor=colors.HexColor('#888888'))
    elements.append(Paragraph("* 绿色高亮 = TOP3", note_style))
    elements.append(Paragraph(f"* 生成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", note_style))
    doc.build(elements)
    print(f"  PDF: {pdf_path}")
    return pdf_path

# ═══════════════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(description='二波形态精选 v2.0 (stk_factor_pro多指标共振)')
    parser.add_argument('--pattern', choices=['sideways', 'deep', 'volume', 'vshape', 'all'], default='all')
    parser.add_argument('--pool', choices=['hs300', 'gem_kc', 'hot', 'all'], default='test')
    parser.add_argument('--codes', nargs='*', default=[])
    parser.add_argument('--output', choices=['csv', 'json', 'print'], default='print')
    parser.add_argument('--csv', type=str, default='', help='从CSV文件读取股票池')
    parser.add_argument('--pdf', action='store_true', help='输出PDF报告')
    parser.add_argument('--today', action='store_true', help='仅输出最新交易日符合入场条件的股票')
    parser.add_argument('--date', type=str, default='', help='指定分析日期(YYYYMMDD)，默认使用最近交易日')
    args = parser.parse_args()

    detector = WavePatternDetector(force_date=args.date)
    
    # 显示分析日期
    analyze_date = get_effective_date(args.date)
    print(f"\n{'='*60}")
    print(f"  二波形态精选v2.9 | 分析日期: {analyze_date}")
    print(f"{'='*60}")

    # CSV模式：读取CSV文件中的股票池
    csv_codes = []
    if args.csv:
        csv_codes = load_stocks_from_csv(args.csv)
        if not csv_codes:
            print("  CSV读取为空，退出")
            return

    # 测试模式
    if args.pattern == 'test':
        codes = args.codes or csv_codes or ['688787.SH', '688629.SH', '688981.SH',
                               '603163.SH', '002192.SZ', '301128.SZ',
                               '688041.SH', '603993.SH', '600519.SH']
        today_label = '仅今日' if args.today else '历史回溯'
        print(f"  测试模式 | {len(codes)} 只 | {today_label}")
        # 获取股票名称（从本地缓存读取，不调API）
        name_map = {}
        try:
            cache_path = os.path.join(CACHE_DIR, 'stock_basic.csv')
            if os.path.exists(cache_path):
                sb = pd.read_csv(cache_path)
                if not sb.empty and 'ts_code' in sb.columns and 'name' in sb.columns:
                    name_map = dict(zip(sb['ts_code'], sb['name']))
        except Exception:
            pass

        results = []
        for code in codes:
            r1 = detector.detect_sideways_pattern(code, today_only=args.today)
            r2 = detector.detect_deep_pullback_pattern(code, today_only=args.today)
            if r1:
                r1['name'] = name_map.get(code, '')
                results.append(r1)
                print(f"\nOK {code} | {r1['pattern']} | 评分{r1['score']}分")
                print(f"   一波+{r1['wave1_gain']}% -> 回调-{r1['pullback_pct']}%({r1['adjust_days']}天) | RSI{r1['rsi']}")
                if r1['wave2_confirmed']: print(f"   二波确认+{r1['wave2_gain']}%")
                if r1['dmi_confirmed']:   print(f"   DMI趋势反转确认")
            elif r2:
                r2['name'] = name_map.get(code, '')
                results.append(r2)
                print(f"\nOK {code} | {r2['pattern']} | 评分{r2['score']}分")
                print(f"   一波+{r2['wave1_gain']}% -> 回调-{r2['pullback_pct']}%({r2['adjust_days']}天) | RSI{r2['rsi']}")
                if r2['wave2_confirmed']: print(f"   二波确认+{r2['wave2_gain']}%")
                if r2['dmi_confirmed']:   print(f"   DMI趋势反转确认")
            else:
                print(f"/ {code} | 无信号")

        if args.output in ('csv', 'json') and results:
            trade_date = get_effective_date(args.date)
            time_str = datetime.datetime.now().strftime('%H%M%S')
            if args.output == 'csv':
                fpath = os.path.join(OUT_DIR, f'wave2_test_{trade_date}_{time_str}.csv')
                pd.DataFrame(results).to_csv(fpath, index=False, encoding='utf-8-sig')
            else:
                fpath = os.path.join(OUT_DIR, f'wave2_test_{trade_date}_{time_str}.json')
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\n已保存: {fpath}")
        
        # PDF输出
        if args.pdf and results:
            generate_pdf_report(results, len(codes), args.csv, args.date)
        return

    # 批量扫描
    pools = {
        'hs300':  (get_hs300_pool(),  '沪深300',  ['sideways']),
        'gem_kc': (get_gem_kc_pool(), '双创板',   ['deep', 'volume', 'vshape']),
        'hot':    (get_hot_leaders(50), '近期强势龙头', ['all']),
        'all':    (get_hs300_pool() + get_gem_kc_pool(), '全市场', ['all']),
    }

    # CSV模式：用CSV中的股票替换pool
    if csv_codes:
        pool, pname = csv_codes, os.path.basename(args.csv)
        print(f"  股票池: {pname} ({len(pool)} 只)")

        df_list = []
        for pat in ['all']:
            df_p = detector.scan_pool(pool, pat, pname, today_only=args.today)
            if len(df_p):
                df_list.append(df_p)
    else:
        pool, pname, pats = pools.get(args.pool, ([], args.pool, ['all']))
        if not pool:
            print("股票池为空！")
            return
        print(f"  股票池: {pname} ({len(pool)} 只)")

        df_list = []
        for pat in pats:
            df_p = detector.scan_pool(pool, pat, pname, today_only=args.today)
            if len(df_p):
                df_list.append(df_p)

    if not df_list:
        print("\n未找到符合条件的股票！")
        return

    results_df = pd.concat(df_list, ignore_index=True)
    
    # ── 去重：每只股票只保留胜率最高的形态 ────────────────────────
    pattern_priority = {'V型急跌': 1, '强势横盘': 2, '放量回调': 3, '深度回调': 4}
    results_df['_priority'] = results_df['pattern'].map(pattern_priority)
    results_df = results_df.sort_values(['ts_code', '_priority']).drop_duplicates(subset=['ts_code'], keep='first')
    results_df = results_df.drop(columns=['_priority']).sort_values('score', ascending=False)
    print(f"\n去重后: {len(results_df)} 只")

    trade_date_str = get_effective_date(args.date)
    time_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = os.path.join(OUT_DIR, f'wave2_pattern_{trade_date_str}_{time_str}.csv')
    json_path = os.path.join(OUT_DIR, f'wave2_pattern_{trade_date_str}_{time_str}.json')
    results_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results_df.to_dict('records'), f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  扫描完成！共 {len(results_df)} 只信号")
    print(f"  CSV: {csv_path}")
    print(f"{'='*60}")

    # 输出TOP20
    for _, r in results_df.head(20).iterrows():
        name_s = r.get('name', '')
        w2 = f"+{r['wave2_gain']}%" if r.get('wave2_confirmed') else '待确认'
        dmi = 'DMI' if r.get('dmi_confirmed') else ''
        print(f"{r['ts_code']:<12} {name_s:<8} {r['pattern']:<8} 评分{r['score']:>2}分 "
              f"+{r['wave1_gain']:>5}% -{r['pullback_pct']:>5}% "
              f"RSI{r['rsi']:>3.0f} ATR止损-{r['stop_pct']}% "
              f"RR{r['rr']:>4.1f}x {w2:>6} {dmi}")

    # PDF输出
    if args.pdf:
        all_results = results_df.to_dict('records')
        generate_pdf_report(all_results, len(pool), args.csv, args.date)


if __name__ == '__main__':
    main()
