"""
空头陷阱（Bear Trap）择时系统 — 每日盘后选股程序
================================================
三层分层算法体系，识别优质股被程序化假破位砸盘后的低吸机会。

核心定义：优质白马/成长龙头基本面无恶化，短期被恐慌杀跌后快速收复失地的洗盘行情。

算法架构:
  Layer 1: 基本面前置过滤（锁死安全边界，排除真下跌）
  Layer 2: 量价+微观诱空识别（区分真实抛压 vs 程序化砸盘诱空）
  Layer 3: 多因子综合评分择时（自适应阈值 + 时序信号生成）

用法:
    python bear_trap_timing.py                         # 默认最新交易日
    python bear_trap_timing.py --date 20260724         # 指定日期
    python bear_trap_timing.py --pool all              # 全市场扫描
    python bear_trap_timing.py --top 30                # 显示前30只
"""

import os
import sys
import json
import argparse
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import pandas as pd
import numpy as np

# ── 路径 ──
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

DB_PATH = r'D:\mystock\cache_daily\stock_data.db'
CACHE_DIR = r'D:\mystock\cache_daily'
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'report_daily')
os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('bear_trap')

# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _safe(val, default=0.0):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return float(val)


def _resolve_trade_date(trade_date: str = None) -> str:
    """解析交易日（同 daily_timing.py 逻辑）"""
    if trade_date:
        return trade_date
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()
    if weekday == 5:
        offset = -1
    elif weekday == 6:
        offset = -2
    elif hour < 16:
        if weekday == 0:
            offset = -3
        else:
            offset = -1
    else:
        offset = 0
    target = now + timedelta(days=offset)
    return target.strftime('%Y%m%d')


def _get_trading_days(start: str, end: str) -> List[str]:
    """从SQLite获取交易日列表"""
    try:
        conn = sqlite3.connect(DB_PATH)
        sql = """SELECT DISTINCT trade_date FROM stk_factor_pro
                 WHERE trade_date BETWEEN ? AND ?
                 ORDER BY trade_date"""
        df = pd.read_sql(sql, conn, params=(start, end))
        conn.close()
        return df['trade_date'].tolist()
    except Exception as e:
        logger.error(f"获取交易日失败: {e}")
        return []


def _load_token() -> str:
    """读取 Tushare Token"""
    token = os.environ.get('TUSHARE_TOKEN')
    if token:
        return token
    for parent in [PROJECT_ROOT, os.path.join(PROJECT_ROOT, '..')]:
        env_path = os.path.join(parent, 'config', '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith('TUSHARE_TOKEN'):
                        token = line.split('=', 1)[1].strip().strip('"\' ')
                        if token:
                            os.environ['TUSHARE_TOKEN'] = token
                            return token
    logger.error("TUSHARE_TOKEN 未配置")
    sys.exit(1)


# ════════════════════════════════════════════════════════════
# Layer 1: 基本面硬过滤算法
# ════════════════════════════════════════════════════════════

class FundamentalFilter:
    """
    基本面硬过滤 — 前置筛除"真下跌"，只保留优质股诱空候选池。
    使用 Tushare 现有接口获取财务数据，配合 SQLite 缓存。
    """

    def __init__(self, token: str):
        import tushare as ts
        ts.set_token(token)
        self.pro = ts.pro_api(token)
        self._rate_limit_last = time.time()
        self._min_interval = 0.22  # 220ms 间隔

    def _rate_limit(self):
        elapsed = time.time() - self._rate_limit_last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._rate_limit_last = time.time()

    def _get_fina_indicator(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取财务指标（ROE、净利润增速等），带缓存"""
        cache_path = os.path.join(CACHE_DIR, f"fin_ind_{ts_code.replace('.','_')}.parquet")
        # 检查缓存
        if os.path.exists(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if (time.time() - mtime) < 86400:  # 24小时缓存
                    df = pd.read_parquet(cache_path)
                    if len(df) > 0:
                        return df
            except Exception:
                pass

        self._rate_limit()
        try:
            df = self.pro.fina_indicator(ts_code=ts_code,
                fields='ts_code,end_date,eps,roe,grossprofit_margin,netprofit_margin,'
                       'netprofit_yoy,basic_eps_yoy,tr_yoy,or_yoy,op_yoy,'
                       'adminexp_of_gr,roic,dt_liab_to_assets,ocf_to_or,'
                       'ocf,profit_to_gr,roe_yearly')
            if df is not None and len(df) > 0:
                df.to_parquet(cache_path, index=False)
                return df
        except Exception as e:
            logger.warning(f"get_fina_indicator {ts_code} 失败: {e}")
        return None

    def _get_income(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取利润表（扣非净利润）"""
        cache_path = os.path.join(CACHE_DIR, f"income_{ts_code.replace('.','_')}.parquet")
        if os.path.exists(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if (time.time() - mtime) < 86400:
                    df = pd.read_parquet(cache_path)
                    if len(df) > 0:
                        return df
            except Exception:
                pass

        self._rate_limit()
        try:
            df = self.pro.income(ts_code=ts_code,
                fields='ts_code,ann_date,f_ann_date,end_date,report_type,'
                       'basic_eps,diluted_eps,total_revenue,revenue,n_income,'
                       'n_income_attr_p,rd_exp,total_profit,total_cogs,'
                       'operate_profit,oper_exp,minority_interest')
            if df is not None and len(df) > 0:
                df.to_parquet(cache_path, index=False)
                return df
        except Exception as e:
            logger.warning(f"get_income {ts_code} 失败: {e}")
        return None

    def _get_cashflow(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取现金流量表"""
        cache_path = os.path.join(CACHE_DIR, f"cashflow_{ts_code.replace('.','_')}.parquet")
        if os.path.exists(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if (time.time() - mtime) < 86400:
                    df = pd.read_parquet(cache_path)
                    if len(df) > 0:
                        return df
            except Exception:
                pass

        self._rate_limit()
        try:
            df = self.pro.cashflow(ts_code=ts_code,
                fields='ts_code,ann_date,f_ann_date,end_date,report_type,'
                       'net_operate_cash_flow,net_invest_cash_flow,'
                       'payment_for_assets,cap_expend_ra')
            if df is not None and len(df) > 0:
                df.to_parquet(cache_path, index=False)
                return df
        except Exception as e:
            logger.warning(f"get_cashflow {ts_code} 失败: {e}")
        return None

    def _get_balancesheet(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取资产负债表"""
        cache_path = os.path.join(CACHE_DIR, f"balance_{ts_code.replace('.','_')}.parquet")
        if os.path.exists(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if (time.time() - mtime) < 86400:
                    df = pd.read_parquet(cache_path)
                    if len(df) > 0:
                        return df
            except Exception:
                pass

        self._rate_limit()
        try:
            df = self.pro.balancesheet(ts_code=ts_code,
                fields='ts_code,ann_date,f_ann_date,end_date,report_type,'
                       'total_assets,total_current_assets,inventories,fix_assets,'
                       'total_liability,total_hldr_eqy_exc_min_int,'
                       'total_hldr_eqy_inc_min_int,contract_liability,'
                       'advance_payment,operating_liability,operating_asset,'
                       'goodwill,intan_assets,accounts_receive')
            if df is not None and len(df) > 0:
                df.to_parquet(cache_path, index=False)
                return df
        except Exception as e:
            logger.warning(f"get_balancesheet {ts_code} 失败: {e}")
        return None

    def _get_fina_audit(self, ts_code: str) -> Dict:
        """获取审计意见"""
        cache_path = os.path.join(CACHE_DIR, f"audit_{ts_code.replace('.','_')}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    return json.load(f)
            except Exception:
                pass

        self._rate_limit()
        try:
            df = self.pro.fina_audit(ts_code=ts_code)
            if df is not None and len(df) > 0:
                df = df.sort_values('end_date', ascending=False)
                opinion = str(df.iloc[0].get('audit_result', '')) if 'audit_result' in df.columns else ''
                result = {'audit_opinion': opinion}
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
                return result
        except Exception:
            pass
        return {'audit_opinion': ''}

    def _get_supplement(self, ts_code: str) -> Optional[pd.DataFrame]:
        """获取业绩预告/快报（一致预期）"""
        cache_path = os.path.join(CACHE_DIR, f"forecast_{ts_code.replace('.','_')}.parquet")
        if os.path.exists(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if (time.time() - mtime) < 86400:
                    df = pd.read_parquet(cache_path)
                    if len(df) > 0:
                        return df
            except Exception:
                pass

        self._rate_limit()
        try:
            df = self.pro.forecast(ts_code=ts_code,
                fields='ts_code,ann_date,end_date,type,period,'
                       'p_change_min,p_change_max,net_profit_min,net_profit_max,'
                       'last_parent_net,profit_change')
            if df is not None and len(df) > 0:
                df.to_parquet(cache_path, index=False)
                return df
        except Exception:
            pass
        return None

    def _get_daily_basic(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """获取个股每日基本面（PE/PB/市值等）"""
        try:
            self._rate_limit()
            df = self.pro.daily_basic(ts_code=ts_code, trade_date=trade_date)
            if df is not None and len(df) > 0:
                return df.iloc[0]
        except Exception:
            pass
        return None

    def _get_industry_pe(self, industry: str) -> float:
        """获取行业中位数PE（简化版，使用预设参考值）"""
        _INDUSTRY_PE_REF = {
            '半导体': 55, '芯片': 55, '元器件': 40, '电气设备': 30, '化工': 25,
            '医药': 40, '医疗保健': 45, '生物医药': 50, '医疗器械': 45,
            '机械': 30, '汽车': 25, '汽车配件': 25, '航空': 50,
            '软件': 50, '互联网': 45, '通信': 35, 'IT设备': 35,
            '环保': 25, '水务': 20, '电力': 18, '煤炭': 12,
            '钢铁': 15, '有色': 25, '建材': 20, '建筑': 15,
            '食品': 35, '饮料': 35, '农业': 25, '牧渔': 25,
            '纺织': 20, '服饰': 20, '家电': 20, '家居': 25,
            '房地产': 15, '金融': 10, '银行': 8, '证券': 25, '保险': 15,
            '传媒': 30, '广告': 30, '游戏': 30, '教育': 35,
            '军工': 55, '航空航天': 55, '核电': 40, '新能源': 35,
            '公路': 15, '铁路': 15, '机场': 20, '港口': 18, '物流': 20,
            '商贸': 22, '零售': 25, '贸易': 20,
        }
        for kw, v in _INDUSTRY_PE_REF.items():
            if kw in industry:
                return v
        return 30  # 默认行业PE

    def check_quality(self, ts_code: str, industry: str = '',
                      lookback_quarters: int = 4) -> Dict:
        """
        Layer 1.1: 质量硬阈值筛选
        返回 {'pass': bool, 'scores': dict, 'reasons': list}

        v2 改进：财务数据不足时不直接否决，使用保守默认分。
        确保首次运行无缓存时也能产生可用结果。
        """
        result = {'pass': False, 'scores': {}, 'reasons': [], 'fund_deviation': None}

        # ── 1.1 盈利质量 ──
        fin_ind = self._get_fina_indicator(ts_code)
        income = self._get_income(ts_code)
        cashflow = self._get_cashflow(ts_code)

        # v2: 财务数据不足 → 使用保守默认分（允许但扣分）
        has_fund_data = fin_ind is not None and len(fin_ind) >= 2
        if not has_fund_data:
            result['reasons'].append('财务数据不足(保守评分)')
            # 使用保守默认值继续，不完全否决
            result['scores']['roe_ttm'] = 8.0   # 保守默认ROE
            result['scores']['roe_ok'] = False
            result['scores']['profit_ok'] = True
            result['scores']['ocf_ratio'] = 0.6
            result['scores']['ocf_ok'] = False
            result['scores']['debt_ratio'] = 0.45
            result['scores']['debt_ok'] = True
            result['scores']['goodwill_ok'] = True
            result['scores']['audit_ok'] = True
            result['scores']['forecast_ok'] = True
            result['scores']['_missing_fund'] = True
            result['pass'] = False  # 硬条件没通过，但数据给了
            return result

        fin_ind = fin_ind.sort_values('end_date', ascending=False).head(8)
        latest = fin_ind.iloc[0]

        # ROE_TTM > 行业中位数
        roe_ttm = _safe(latest.get('roe', 0))
        industry_roe_med = 8.0  # 默认行业中位数ROE
        if industry:
            # 简化：使用预设行业中位数
            ind_map = {'医药': 12, '半导体': 10, '芯片': 10, '软件': 12,
                       '金融': 10, '银行': 10, '房地产': 8, '化工': 8,
                       '机械': 8, '汽车': 8, '电力': 7, '军工': 8,
                       '消费': 12, '食品': 12}
            for kw, v in ind_map.items():
                if kw in industry:
                    industry_roe_med = v
                    break

        roe_ok = roe_ttm >= industry_roe_med
        result['scores']['roe_ttm'] = roe_ttm

        # 连续4个季度扣非净利润同比≥0
        profit_ok = True
        if income is not None and len(income) > 4:
            income_q = income.sort_values('end_date', ascending=False).head(4)
            for _, row in income_q.iterrows():
                n_income = _safe(row.get('n_income_attr_p', row.get('n_income', 0)))
                if n_income < 0:
                    profit_ok = False
                    break

        # 经营性现金流/净利润 ≥ 0.8
        ocf_ok = False
        if cashflow is not None and len(cashflow) > 0 and income is not None and len(income) > 0:
            cf = cashflow.sort_values('end_date', ascending=False)
            inc = income.sort_values('end_date', ascending=False)
            latest_cf = cf.iloc[0]
            latest_inc = inc.iloc[0]
            ocf = _safe(latest_cf.get('net_operate_cash_flow', 0))
            np_ = _safe(latest_inc.get('n_income_attr_p', latest_inc.get('n_income', 0)))
            if np_ != 0:
                ocf_ratio = abs(ocf / np_) if np_ > 0 else 0
                ocf_ok = ocf_ratio >= 0.8
                result['scores']['ocf_ratio'] = ocf_ratio

        # ── 1.2 资产安全 ──
        balance = self._get_balancesheet(ts_code)
        debt_ok = True
        goodwill_ok = True
        if balance is not None and len(balance) > 0:
            bs = balance.sort_values('end_date', ascending=False)
            latest_bs = bs.iloc[0]
            total_liab = _safe(latest_bs.get('total_liability', 0))
            total_assets = _safe(latest_bs.get('total_assets', 0))
            debt_ratio = total_liab / total_assets if total_assets > 0 else 0

            # 资产负债率低于行业均值（简化：60%为阈值）
            ind_debt_ratio = 0.60
            debt_ok = debt_ratio <= ind_debt_ratio
            result['scores']['debt_ratio'] = debt_ratio

            # 商誉检查
            goodwill = _safe(latest_bs.get('goodwill', 0))
            if goodwill > total_assets * 0.1:  # 商誉超过总资产10%视为高风险
                goodwill_ok = False
                result['reasons'].append(f'商誉过高({goodwill/1e8:.1f}亿)')

        # ── 审计意见 ──
        audit = self._get_fina_audit(ts_code)
        audit_opinion = audit.get('audit_opinion', '')
        audit_ok = True
        if audit_opinion and '标准无保留' not in audit_opinion and '无保留意见' not in audit_opinion:
            audit_ok = False
            result['reasons'].append(f'审计非标: {audit_opinion}')

        # ── 1.3 估值约束（从daily_basic获取PE/PB） ──
        # 简化：跳过分位计算，直接从stk_factor_pro获取pe/pb
        val_ok = True  # 后续在价格分析中补充

        # ── 1.4 景气度（业绩预告） ──
        supplement = self._get_supplement(ts_code)
        forecast_ok = True
        if supplement is not None and len(supplement) > 0:
            sup = supplement.sort_values('end_date', ascending=False)
            latest_sup = sup.iloc[0]
            p_min = _safe(latest_sup.get('p_change_min', 0))
            if p_min < -30:  # 业绩预减超-30%视为重大问题
                forecast_ok = False
                result['reasons'].append(f'业绩预告降幅{p_min:.0f}%')

        # ── 综合判定 ──
        all_ok = roe_ok and profit_ok and ocf_ok and debt_ok and goodwill_ok and audit_ok and forecast_ok
        result['pass'] = all_ok
        result['scores']['roe_ok'] = roe_ok
        result['scores']['profit_ok'] = profit_ok
        result['scores']['ocf_ok'] = ocf_ok
        result['scores']['debt_ok'] = debt_ok
        result['scores']['goodwill_ok'] = goodwill_ok
        result['scores']['audit_ok'] = audit_ok
        result['scores']['forecast_ok'] = forecast_ok

        if not all_ok:
            fails = []
            if not roe_ok: fails.append('ROE不足')
            if not profit_ok: fails.append('利润下滑')
            if not ocf_ok: fails.append('现金流差')
            if not debt_ok: fails.append('负债高')
            if not audit_ok: fails.append('审计非标')
            result['reasons'] = fails + result['reasons']

        return result

    def calc_quality_score(self, scores: Dict) -> float:
        """计算标准化基本面综合得分 (0~1)"""
        score = 0.0
        n = 0

        # ROE评分（0~1）
        if 'roe_ttm' in scores:
            roe = scores['roe_ttm']
            roe_score = min(roe / 20.0, 1.0)  # ROE 20%得满分
            score += roe_score * 0.30
            n += 0.30

        # 现金流评分
        if 'ocf_ratio' in scores:
            ocf = scores['ocf_ratio']
            ocf_score = min(ocf / 1.5, 1.0)  # OCF/净利润1.5得满分
            score += ocf_score * 0.25
            n += 0.25

        # 负债评分（越低越好）
        if 'debt_ratio' in scores:
            dr = scores['debt_ratio']
            debt_score = 1.0 - min(dr / 0.8, 1.0)  # 负债率80%得0分
            score += debt_score * 0.20
            n += 0.20

        # 硬条件通过分
        bool_factors = ['roe_ok', 'profit_ok', 'ocf_ok', 'debt_ok',
                        'goodwill_ok', 'audit_ok', 'forecast_ok']
        passed = sum(1 for k in bool_factors if scores.get(k, False))
        bool_score = passed / len(bool_factors)
        score += bool_score * 0.25
        n += 0.25

        return score / n if n > 0 else 0.0


# ════════════════════════════════════════════════════════════
# Layer 2: 量价+微观诱空识别算法
# ════════════════════════════════════════════════════════════

class PriceVolumeTrapDetector:
    """
    量价结构 + 微观订单流估算 — 识别空头陷阱的量价特征。
    无Level2数据，使用日线高频特征进行近似估算。
    """

    def __init__(self):
        pass

    def get_stk_factor_data(self, ts_code: str, trade_date: str = None,
                             lookback: int = 120) -> Optional[pd.DataFrame]:
        """从SQLite获取stk_factor_pro数据（含完整技术指标），截止到指定交易日"""
        try:
            conn = sqlite3.connect(DB_PATH)
            if trade_date:
                sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, amount,
                                volume_ratio, turnover_rate,
                                ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60, ma_bfq_90,
                                macd_dif_bfq, macd_dea_bfq, macd_bfq,
                                rsi_bfq_6, rsi_bfq_12, rsi_bfq_24,
                                kdj_k_bfq, kdj_d_bfq, kdj_bfq,
                                boll_mid_bfq, boll_upper_bfq, boll_lower_bfq,
                                atr_bfq, total_mv, circ_mv, pe_ttm, pb
                         FROM stk_factor_pro
                         WHERE ts_code=? AND trade_date<=?
                         ORDER BY trade_date DESC LIMIT ?
                      """
                df = pd.read_sql(sql, conn, params=(ts_code, trade_date, lookback))
            else:
                sql = """SELECT trade_date, open, high, low, close, pct_chg, vol, amount,
                                volume_ratio, turnover_rate,
                                ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60, ma_bfq_90,
                                macd_dif_bfq, macd_dea_bfq, macd_bfq,
                                rsi_bfq_6, rsi_bfq_12, rsi_bfq_24,
                                kdj_k_bfq, kdj_d_bfq, kdj_bfq,
                                boll_mid_bfq, boll_upper_bfq, boll_lower_bfq,
                                atr_bfq, total_mv, circ_mv, pe_ttm, pb
                         FROM stk_factor_pro
                         WHERE ts_code=? ORDER BY trade_date DESC LIMIT ?
                      """
                df = pd.read_sql(sql, conn, params=(ts_code, lookback))
            conn.close()
            if df.empty:
                return None
            df = df.sort_values('trade_date').reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"读取stk_factor_pro {ts_code} 失败: {e}")
            return None

    def _calc_divergence_score(self, df: pd.DataFrame) -> float:
        """
        计算多周期动量背离强度得分 (0~1)
        RSI(6)/RSI(12)/MACD DIFF 是否与价格下跌背离

        v2: 不再要求股价创新低。检测近5日价格下跌时RSI/MACD是否走平或回升，
        适用于更广泛的回调背离场景。
        """
        if df is None or len(df) < 30:
            return 0.0

        closes = df['close'].values
        n = len(closes)

        # v2: 检查近5日是否显著下跌（比创新低更宽松）
        price_dropped = False
        drop_pct = 0
        for lookback in [5, 10]:
            if n > lookback:
                ret = (closes[-1] / closes[-lookback] - 1) * 100
                if ret < -3:  # 近5日或10日跌幅>3%
                    price_dropped = True
                    drop_pct = ret
                    break

        if not price_dropped:
            return 0.0

        # RSI/MACD背离检测
        rsi6_col = df['rsi_bfq_6'].values
        rsi12_col = df['rsi_bfq_12'].values
        macd_dif = df['macd_dif_bfq'].values

        if n < 30:
            return 0.0

        # 比较最近5日均值 vs 前5日均值
        def window_avg(arr, w=5):
            return np.mean(arr[-w:]) if len(arr) >= w else np.mean(arr)

        recent_rsi6 = window_avg(rsi6_col, 5)
        prev_rsi6 = window_avg(rsi6_col[-(10):-(5)], 5) if n >= 10 else window_avg(rsi6_col[:5], 3)
        recent_rsi12 = window_avg(rsi12_col, 5)
        prev_rsi12 = window_avg(rsi12_col[-(10):-(5)], 5) if n >= 10 else window_avg(rsi12_col[:5], 3)
        recent_macd = window_avg(macd_dif, 5)
        prev_macd = window_avg(macd_dif[-(10):-(5)], 5) if n >= 10 else window_avg(macd_dif[:5], 3)

        diver_score = 0.0
        checks = [
            (recent_rsi6, prev_rsi6, 0.35),
            (recent_rsi12, prev_rsi12, 0.30),
            (recent_macd, prev_macd, 0.35),
        ]

        for recent, prev, weight in checks:
            if recent > prev:  # 指标回升（价格下跌中反弹 → 背离）
                recovery_pct = (recent / max(prev, 0.01) - 1) * 100 if prev != 0 else 0
                ratio = min(recovery_pct / max(abs(drop_pct), 0.5), 1.0)
                diver_score += ratio * weight

        # 使用原始macd_dif绝对值辅助判断
        if len(macd_dif) >= 5 and macd_dif[-1] > macd_dif[-3]:
            diver_score += 0.05

        return min(diver_score, 1.0)

    def _calc_support_fake_break(self, df: pd.DataFrame) -> Dict:
        """
        支撑假突破判定
        条件：最低价<MA20 且 收盘价>MA20（长下影回收）
        Returns: {detected, lower_shadow_ratio, ma20_recovered}
        """
        if df is None or len(df) < 25:
            return {'detected': False, 'lower_shadow_ratio': 0, 'ma20_recovered': False}

        latest = df.iloc[-1]
        close = _safe(latest['close'])
        low = _safe(latest['low'])
        high = _safe(latest['high'])
        open_ = _safe(latest.get('open', close))
        ma20 = _safe(latest.get('ma_bfq_20', 0))
        ma60 = _safe(latest.get('ma_bfq_60', 0))

        if ma20 <= 0:
            return {'detected': False}

        # 条件1：日内最低价跌破或接近MA20（v2: 距离MA20<2%也算接近破位）
        dist_to_ma20 = (low - ma20) / ma20 * 100 if ma20 > 0 else 0
        near_or_broke_ma20 = low < ma20 or (0 <= dist_to_ma20 < 3.0)
        # 条件2：收盘价收回MA20上方或接近（v2: 距离<1%也算收复）
        close_dist = (close - ma20) / ma20 * 100 if ma20 > 0 else 0
        recovered_or_near_ma20 = close > ma20 or (close_dist > -1.0 and not low < ma20)
        # 条件3：20日内最低未有效击穿MA60（中期支撑完好）
        if len(df) >= 20:
            last_20_low = df['low'].iloc[-20:].min()
            ma60_ok = ma60 <= 0 or last_20_low > ma60 * 0.97
        else:
            ma60_ok = True

        # 计算下影线比例
        max_body = max(open_, close)
        min_body = min(open_, close)
        total_range = high - low
        lower_shadow = min_body - low
        lower_shadow_ratio = lower_shadow / total_range if total_range > 0 else 0

        detected = near_or_broke_ma20 and ma60_ok

        return {
            'detected': detected,
            'near_or_broke_ma20': bool(near_or_broke_ma20),
            'recovered_or_near_ma20': bool(recovered_or_near_ma20),
            'lower_shadow_ratio': lower_shadow_ratio,
            'total_range_pct': total_range / close * 100 if close > 0 else 0,
        }

    def _calc_volume_decay(self, df: pd.DataFrame) -> Dict:
        """
        量能衰竭特征因子
        空头陷阱标准量能序列：破位放量 → 随后缩量至地量
        Returns: {decay_detected, break_vol_ratio, decay_vol_ratio, decay_score}
        """
        if df is None or len(df) < 25:
            return {'decay_detected': False}

        vols = df['vol'].values.astype(float)
        n = len(vols)

        # 近20日均量
        vol_ma20 = np.mean(vols[-20:]) if n >= 20 else np.mean(vols)

        if vol_ma20 <= 0:
            return {'decay_detected': False}

        # 寻找最近5日中的放量日（破位日候选）
        recent_5 = vols[-5:]
        recent_vol_ratios = recent_5 / vol_ma20

        # 破位日：量比>1.5（v2放宽从1.8→1.5）
        break_day_idx = None
        for i in range(len(recent_5)):
            if recent_vol_ratios[i] > 1.5:
                break_day_idx = -(5 - i)
                break

        if break_day_idx is None:
            # 进一步放宽：量比>1.2
            for i in range(len(recent_5)):
                if recent_vol_ratios[i] > 1.2:
                    break_day_idx = -(5 - i)
                    break

        if break_day_idx is None:
            return {'decay_detected': False, 'break_day_exists': False,
                    'break_vol_ratio': 0, 'decay_vol_ratio': 0}

        break_vol_ratio = float(recent_vol_ratios[-(5 + break_day_idx)]) if break_day_idx < 0 else 0

        # 检查破位后是否缩量
        if len(vols[break_day_idx:]) >= 3:
            post_break_vols = vols[break_day_idx + 1:break_day_idx + 4]  # T+1 ~ T+3
            if len(post_break_vols) >= 2:
                post_vol_ma = np.mean(post_break_vols[:2])  # 取T+1,T+2
                decay_vol_ratio = post_vol_ma / vol_ma20
                decay_detected = decay_vol_ratio < 0.7
            else:
                decay_vol_ratio = 1.0
                decay_detected = False
        else:
            decay_vol_ratio = 1.0
            decay_detected = False

        return {
            'decay_detected': decay_detected,
            'break_vol_ratio': break_vol_ratio,
            'decay_vol_ratio': decay_vol_ratio,
            'break_day_exists': break_day_idx is not None,
        }

    def _calc_order_flow_proxy(self, df: pd.DataFrame) -> Dict:
        """
        订单流微观特征估算（无Level2数据，使用日线代理指标）
        - 虚假压单代理：大振幅+长下影（砸盘后反弹，模拟fake sell）
        - 隐性承接代理：尾盘资金净流入估算（使用量价关系）
        - 融券行为代理：无法直接获取，简化处理
        """
        if df is None or len(df) < 5:
            return {'fake_sell_score': 0, 'institutional_buying_score': 0, 'overall_score': 0}

        latest = df.iloc[-1]
        close = _safe(latest['close'])
        open_ = _safe(latest.get('open', close))
        high = _safe(latest['high'])
        low = _safe(latest['low'])
        pct_chg = _safe(latest.get('pct_chg', 0))
        volume_ratio = _safe(latest.get('volume_ratio', 1))
        turnover = _safe(latest.get('turnover_rate', 0))

        total_range = high - low

        # ── 虚假压单代理分 ──
        # 特征：大幅低开+长下影+日线收阳（模拟砸盘后拉回）
        fake_sell_score = 0
        if open_ > low and total_range > 0:
            lower_shadow = min(open_, close) - low
            upper_shadow = high - max(open_, close)
            # 下影线占比较大 + 收阳 + 放量 = 虚假砸盘嫌疑
            if lower_shadow > upper_shadow * 1.5 and close >= open_ and volume_ratio > 1.2:
                fake_sell_score = min(lower_shadow / total_range * 100, 100)

        # ── 隐性承接代理分 ──
        # 特征：下跌收红K + 缩量 + 分时尾盘转正（日线近似）
        # 简化为：日内低开高走、收盘接近当日中上部的特征
        inst_buy_score = 0
        if close > open_ and pct_chg < 3:  # 小幅上涨（非大涨追高）
            # 收盘在当日振幅的上1/3区域 = 买方强势
            close_position = (close - low) / total_range if total_range > 0 else 0.5
            if close_position > 0.66:
                inst_buy_score += 50
            # 放量阳线（主动性买盘）
            if volume_ratio > 1.3 and close > open_:
                inst_buy_score += 30
            # 换手率适中（非异常放量出货）
            if turnover > 0 and turnover < 10:
                inst_buy_score += 20
        inst_buy_score = min(inst_buy_score, 100)

        # ── 综合订单流评分 ──
        overall = (fake_sell_score * 0.4 + inst_buy_score * 0.6)

        return {
            'fake_sell_score': fake_sell_score,
            'institutional_buying_score': inst_buy_score,
            'overall_score': overall / 100.0,  # 归一化到0~1
        }

    def _calc_sector_resonance(self, ts_code: str, df: pd.DataFrame,
                               industry_df: pd.DataFrame = None) -> Dict:
        """
        板块宏观共振校验
        优质个股诱空一定是独立个股行情，无板块同步杀跌
        Sector_Beta = 个股20日跌幅 - 行业指数20日跌幅
        """
        if df is None or len(df) < 20:
            return {'sector_beta': 0, 'independent': False}

        # 个股20日跌幅
        stock_ret_20d = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100

        # 行业指数数据缺失时的简化处理
        # 通过查询行业ETF数据，但这里简化处理
        sector_ret_20d = 0
        if industry_df is not None and len(industry_df) >= 20:
            sector_ret_20d = (industry_df['close'].iloc[-1] / industry_df['close'].iloc[-20] - 1) * 100

        sector_beta = stock_ret_20d - sector_ret_20d
        # Sector_Beta < -0.18：个股跌幅显著大于板块，确认个股独立诱空
        independent = sector_beta < -0.18

        return {
            'sector_beta': sector_beta,
            'stock_ret_20d': stock_ret_20d,
            'sector_ret_20d': sector_ret_20d,
            'independent': independent,
        }

    def _check_ma20_recovery_seq(self, df: pd.DataFrame, lookback: int = 5) -> Dict:
        """
        二次确认：T+1日股价不再创新低，分时均价线上拐
        简化版：检查最近几日价格是否企稳在MA20附近
        """
        if df is None or len(df) < 25:
            return {'confirmed': False, 'stable_days': 0}

        ma20 = df['ma_bfq_20'].values[-lookback:]
        closes = df['close'].values[-lookback:]
        lows = df['low'].values[-lookback:]

        # 不再创新低：最近2日的low未低于前3日最低
        if len(closes) >= 5:
            lows_prev = lows[:-2] if len(lows) > 2 else lows
            lows_recent = lows[-2:] if len(lows) >= 2 else lows
            no_new_low = all(l >= min(lows_prev) for l in lows_recent)
        else:
            no_new_low = True

        # 企稳天数：收盘价在MA20上方或接近（>0.97）的天数
        stable_days = 0
        for i in range(len(closes)):
            if closes[i] > ma20[i] * 0.97:
                stable_days += 1

        confirmed = no_new_low and stable_days >= max(1, len(closes) // 2)

        return {
            'confirmed': confirmed,
            'no_new_low': no_new_low,
            'stable_days': stable_days,
            'total_days': len(closes),
        }

    def detect_bear_trap(self, df: pd.DataFrame,
                         industry_df: pd.DataFrame = None) -> Dict:
        """
        完整Layer 2检测：量价诱空识别
        v3: 增加门条件——空头陷阱当天必须是放量中阳线收复失地
        """
        if df is None or len(df) < 30:
            return {'detected': False, 'score': 0,
                    'modules': {}, 'reasons': []}

        # ── v3 门条件: 最新交易日必须是放量中阳线收复失地 ──
        latest = df.iloc[-1]
        close_latest = _safe(latest['close'])
        open_latest = _safe(latest.get('open', close_latest))
        pct_chg_latest = _safe(latest.get('pct_chg', 0))
        vol_ratio_latest = _safe(latest.get('volume_ratio', 0))
        ma20_latest = _safe(latest.get('ma_bfq_20', 0))
        low_latest = _safe(latest.get('low', close_latest))

        # 中阳线：涨幅2%~7%，排除涨停（<9.5%安全线）也排除涨幅不足的弱反弹
        is_medium_yang = 2.0 <= pct_chg_latest <= 7.0
        # 放量：量比≥1.3（主动性买盘）
        is_volume_up = vol_ratio_latest >= 1.3
        # 收复失地：收盘站上MA20，且日内曾跌破或接近MA20（假破位特征）
        is_recovery = close_latest > ma20_latest
        near_or_broke_ma20 = low_latest < ma20_latest or (low_latest - ma20_latest) / ma20_latest * 100 < 3.0 if ma20_latest > 0 else False

        recovery_candle_ok = is_medium_yang and is_volume_up and is_recovery and near_or_broke_ma20

        # 模块1：量价结构
        support = self._calc_support_fake_break(df)
        volume = self._calc_volume_decay(df)
        divergence = self._calc_divergence_score(df)

        # 模块2：订单流估算代理
        order_flow = self._calc_order_flow_proxy(df)

        # 模块3：板块宏观共振校验
        sector = self._calc_sector_resonance('', df, industry_df)

        # 二次确认
        confirm = self._check_ma20_recovery_seq(df)

        # ── v3 门条件裁决: 非放量中阳线收复失地 → 直接否决 ──
        if not recovery_candle_ok:
            return {
                'detected': False, 'score': 0,
                'support_fake_break': support,
                'volume_decay': volume,
                'divergence_score': divergence,
                'order_flow': order_flow,
                'sector_resonance': sector,
                'confirmation': confirm,
                'recovery_candle': {
                    'ok': False,
                    'pct_chg': round(pct_chg_latest, 2),
                    'volume_ratio': round(vol_ratio_latest, 2),
                    'close_gt_ma20': bool(close_latest > ma20_latest),
                    'near_or_broke_ma20': bool(near_or_broke_ma20),
                },
                'reasons': [f'非放量中阳线收复(涨幅{pct_chg_latest:.1f}% 量比{vol_ratio_latest:.2f})'],
            }

        # ── v3 门条件通过 = 诱空信号确认，其余模块仅影响评分排名 ──
        # 基础分50（门条件全满足即过阈值），加分项量化信号强度
        l2_score = 50.0

        # Module 1 加分（量价结构细化，max +30）
        if support.get('lower_shadow_ratio', 0) > 0.5:
            l2_score += 10  # 长下影=假破位加分
        if support.get('total_range_pct', 0) > 3:
            l2_score += 5   # 振幅大=多空博弈激烈
        if volume.get('decay_detected', False):
            l2_score += 15  # 破位后缩量衰竭=洗盘特征
        elif volume.get('break_day_exists', False):
            l2_score += 5
        if divergence > 0.65:
            l2_score += 15
        elif divergence > 0.40:
            l2_score += 10
        elif divergence > 0.20:
            l2_score += 5

        # Module 2 加分（订单流微观，max +10）
        l2_score += order_flow['overall_score'] * 10

        # Module 3 加分（板块独立，max +5）
        if sector.get('independent', False):
            l2_score += 5

        # 二次确认加权
        if confirm.get('confirmed', False):
            l2_score *= 1.05

        l2_score = min(l2_score, 100)
        detected = True  # 门条件已确认诱空

        reasons = []
        reasons.append(f"放量中阳({pct_chg_latest:.1f}% 量比{vol_ratio_latest:.2f})")
        if support.get('detected'):
            reasons.append(f"假突破(下影{support['lower_shadow_ratio']:.0%})")
        if volume.get('decay_detected'):
            reasons.append(f"缩量衰竭(量比{volume['decay_vol_ratio']:.2f})")
        if divergence > 0.65:
            reasons.append(f"动量背离({divergence:.2f})")
        if sector.get('independent'):
            reasons.append("独立于板块")

        return {
            'detected': detected,
            'score': l2_score,
            'support_fake_break': support,
            'volume_decay': volume,
            'divergence_score': divergence,
            'order_flow': order_flow,
            'sector_resonance': sector,
            'confirmation': confirm,
            'recovery_candle': {
                'ok': True,
                'pct_chg': round(pct_chg_latest, 2),
                'volume_ratio': round(vol_ratio_latest, 2),
                'close_gt_ma20': bool(close_latest > ma20_latest),
                'near_or_broke_ma20': bool(near_or_broke_ma20),
            },
            'reasons': reasons,
        }


# ════════════════════════════════════════════════════════════
# Layer 3: 多因子综合评分择时模型
# ════════════════════════════════════════════════════════════

class BearTrapScorer:
    """
    三层融合评分+信号生成
    综合Layer 1(L1) + Layer 2(L2) 结果，输出最终择时信号。
    """

    def __init__(self, market_regime: str = '震荡市'):
        self.market_regime = market_regime
        # 自适应阈值（分数制 0-100）
        self._thresholds = {
            '牛市': {'S': 75, 'A': 65, 'B': 55, 'C': 40, 'D': 25},
            '震荡市': {'S': 80, 'A': 70, 'B': 60, 'C': 45, 'D': 30},
            '熊市': {'S': 85, 'A': 75, 'B': 65, 'C': 50, 'D': 35},
        }

    def get_threshold(self, level: str = 'C') -> float:
        t = self._thresholds.get(self.market_regime, self._thresholds['震荡市'])
        return t.get(level, t['C'])

    def score(self, fund_result: Dict, pv_result: Dict,
              ts_code: str = '', name: str = '') -> Dict:
        """
        三层综合评分 + 信号生成
        使用直接分数制（0-100），分段映射到S/A/B/C/D/E等级。

        Returns:
            dict: 综合评分、信号类型、建议等
        """
        t = self._thresholds.get(self.market_regime, self._thresholds['震荡市'])

        # ── L1: 基本面分 (0-100) ──
        fund_quality = fund_result.get('quality_score', 0.0)  # 0~1
        l1_pass = 1.0 if fund_result.get('pass') else 0.0
        missing_fund = fund_result.get('scores', {}).get('_missing_fund', False)

        # 基础分 + 质量分加成
        l1_score = fund_quality * 60 + l1_pass * 40  # 0~100
        # v3: pool_trusted 的票跳过缺失数据扣分（股池已前置验证基本面）
        if missing_fund and not fund_result.get('pool_trusted', False):
            l1_score *= 0.7  # 缺数据扣30%
        l1_score = min(l1_score, 100)

        # ── L2: 量价诱空识别分 (0-100) ──
        l2_score = min(pv_result.get('score', 0), 100)  # 来自detect_bear_trap

        # ── 综合分 = 基本面×35% + 量价×45% + 信号强度×20% ──
        # 信号强度: 核心信号数量加权
        n_signals = 0
        core_signals = []
        if fund_result.get('pass') or fund_quality > 0.4:
            core_signals.append('基本面')
            n_signals += 1
        if pv_result.get('support_fake_break', {}).get('detected'):
            core_signals.append('假突破')
            n_signals += 1
        if pv_result.get('volume_decay', {}).get('decay_detected'):
            core_signals.append('缩量衰竭')
            n_signals += 1
        if pv_result.get('divergence_score', 0) > 0.40:
            core_signals.append('动量背离')
            n_signals += 1
        if pv_result.get('sector_resonance', {}).get('independent'):
            core_signals.append('板块独立')
            n_signals += 1
        if pv_result.get('confirmation', {}).get('confirmed'):
            core_signals.append('二次确认')
            n_signals += 1

        signal_strength = min(n_signals / 4.0, 1.5)  # 最高1.5倍增益

        composite = l1_score * 0.30 + l2_score * 0.50 + l2_score * signal_strength * 0.20
        composite = min(composite, 100)

        # ── 信号等级判定 ──
        signal_type = 'none'
        signal_level = 'E级-不符合条件'
        suggestion = '基本面或量价条件不满足'

        if composite >= t['S'] and n_signals >= 3:
            signal_type = 'buy'
            signal_level = 'S级-强烈诱空信号'
            suggestion = '★★★ 强烈低吸：基本面优质+量价诱空确认+多信号共振'
        elif composite >= t['A'] and n_signals >= 2:
            signal_type = 'buy'
            signal_level = 'A级-确认诱空信号'
            suggestion = '★★ 逢低关注：诱空特征明显，分批建仓'
        elif composite >= t['B'] and n_signals >= 2:
            signal_type = 'buy'
            signal_level = 'B级-观察诱空信号'
            suggestion = '★ 观察等待：有诱空特征，需T+1确认'
        elif composite >= t['C']:
            signal_level = 'C级-弱信号'
            suggestion = '信号偏弱，继续观察'
        elif composite >= t['D']:
            signal_level = 'D级-关注候选'
            suggestion = '量价有特征但基本面待确认'

        return {
            'ts_code': ts_code,
            'name': name,
            'prob_score': round(composite, 1),
            'signal_level': signal_level,
            'signal_type': signal_type,
            'suggestion': suggestion,
            'core_signals': core_signals,
            'n_core_signals': n_signals,
            'fund_pass': fund_result.get('pass', False),
            'fund_quality_score': round(fund_quality * 100, 1),
            'pv_score': round(l2_score, 1),
            'divergence_score': round(pv_result.get('divergence_score', 0), 2),
            'confirmed': pv_result.get('confirmation', {}).get('confirmed', False),
            'stop_loss_triggered': False,
        }

    def get_trading_decision(self, result: Dict, price: float,
                             ma60: float, atr: float) -> Dict:
        """
        交易决策生成（含风控参数）
        """
        if result.get('signal_type') != 'buy':
            return None

        if atr is None or atr <= 0:
            atr = price * 0.03  # 默认3% ATR

        # 分批建仓
        base_pos = 0.4  # 40%底仓

        # 止盈位：反弹至前期下跌起点
        take_profit_1 = price * 1.10  # 第一止盈10%
        take_profit_2 = price * 1.20  # 第二止盈20%

        # 止损位：跌穿MA60且3日不收复
        stop_loss = ma60 * 0.97 if ma60 > 0 else price * 0.92

        # 仓位上限（不超过组合8%）
        max_position = 0.08

        return {
            'ts_code': result['ts_code'],
            'signal_level': result['signal_level'],
            'entry_price': price,
            'stop_loss': stop_loss,
            'stop_loss_pct': (stop_loss / price - 1) * 100,
            'take_profit_1': take_profit_1,
            'take_profit_1_pct': (take_profit_1 / price - 1) * 100,
            'take_profit_2': take_profit_2,
            'take_profit_2_pct': (take_profit_2 / price - 1) * 100,
            'base_position_pct': base_pos * max_position * 100,
            'max_position_pct': max_position * 100,
            'atr': round(atr, 3),
            'atr_pct': round(atr / price * 100, 2),
        }


# ════════════════════════════════════════════════════════════
# 主程序：每日盘后空头陷阱择时扫描
# ════════════════════════════════════════════════════════════

def load_stock_pool(pool_type: str = 'qualified') -> pd.DataFrame:
    """
    加载股票池
    - 'qualified': 从 bull_stocks_qualified.csv 加载
    - 'all': 从 bull_stocks_all.csv 加载
    - 其他：尝试直接读取路径
    """
    if pool_type == 'qualified':
        path = os.path.join(OUTPUT_DIR, 'bull_stocks_qualified.csv')
        fallback = os.path.join(OUTPUT_DIR, 'bull_stocks_all.csv')
    elif pool_type == 'all':
        path = os.path.join(OUTPUT_DIR, 'bull_stocks_all.csv')
        fallback = None
    else:
        path = pool_type
        fallback = None

    if os.path.exists(path):
        df = pd.read_csv(path)
        logger.info(f"加载股池: {path} ({len(df)}只)")
        return df

    if fallback and os.path.exists(fallback):
        df = pd.read_csv(fallback)
        logger.info(f"回退加载股池: {fallback} ({len(df)}只)")
        return df

    # 全市场扫描：从SQLite加载所有股票
    logger.info(f"未找到股池文件，从SQLite加载全市场股票: {pool_type}")
    try:
        conn = sqlite3.connect(DB_PATH)
        sql = """SELECT DISTINCT ts_code FROM stk_factor_pro
                 WHERE trade_date = (SELECT MAX(trade_date) FROM stk_factor_pro)
              """
        codes = pd.read_sql(sql, conn)['ts_code'].tolist()
        conn.close()
        df = pd.DataFrame({'code': [c.split('.')[0] for c in codes],
                          'name': [''] * len(codes),
                          'theme': [''] * len(codes),
                          'industry': [''] * len(codes)})
        logger.info(f"全市场 {len(codes)} 只")
        return df
    except Exception as e:
        logger.error(f"加载股票列表失败: {e}")
        return pd.DataFrame()


def run_bear_trap_scan(
    trade_date: str = None,
    pool_type: str = 'qualified',
    top_n: int = 30,
    max_workers: int = 4,
) -> pd.DataFrame:
    """
    主执行函数：空头陷阱择时扫描
    """
    trade_date = _resolve_trade_date(trade_date)
    logger.info(f"=' 空头陷阱择时扫描 交易日期: {trade_date} ='")

    # ── 1. 加载股池 ──
    pool = load_stock_pool(pool_type)
    if len(pool) == 0:
        logger.error("无股票数据")
        return pd.DataFrame()

    # ── 2. 初始化各模块 ──
    token = _load_token()
    fund_filter = FundamentalFilter(token)
    pv_detector = PriceVolumeTrapDetector()

    # ── 3. 市场状态判断（简化版） ──
    market_regime = '震荡市'
    try:
        import tushare as ts
        ts.set_token(token)
        pro = ts.pro_api(token)
        idx_df = pro.index_daily(ts_code='000001.SH',
                                 start_date=(datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d'),
                                 end_date=trade_date)
        if idx_df is not None and len(idx_df) > 0:
            idx_df = idx_df.sort_values('trade_date')
            last_20_ret = (idx_df['close'].iloc[-1] / idx_df['close'].iloc[-20] - 1) * 100 if len(idx_df) >= 20 else 0
            if last_20_ret > 5:
                market_regime = '牛市'
            elif last_20_ret < -5:
                market_regime = '熊市'
            else:
                market_regime = '震荡市'
        logger.info(f"市场状态: {market_regime} (近20日涨跌{last_20_ret:.1f}%)")
    except Exception as e:
        logger.warning(f"市场状态判断失败: {e}")

    scorer = BearTrapScorer(market_regime)

    # ── 4. 逐只扫描 ──
    results = []
    codes_to_scan = pool['code'].tolist()

    logger.info(f"开始扫描 {len(codes_to_scan)} 只标的...")

    processed = 0

    for code in codes_to_scan:
        code = str(code).strip().zfill(6)

        # 跳过北交所
        if code.startswith(('8', '4', '9')):
            continue

        # 转 ts_code
        if code.startswith('6'):
            ts_code = f"{code}.SH"
        else:
            ts_code = f"{code}.SZ"

        # 获取名称、主题、行业信息
        mask = pool['code'].astype(str).str.strip().str.zfill(6) == code
        matched = pool[mask]
        name = str(matched.iloc[0].get('name', '')) if len(matched) > 0 else ''
        theme = str(matched.iloc[0].get('theme', '')) if len(matched) > 0 else ''
        industry = str(matched.iloc[0].get('industry', '')) if len(matched) > 0 else ''
        # 获取股池已有基本面评分（来自 multi_factor_picker 系统）
        pool_fund_score = float(matched.iloc[0].get('最终分', 0)) if len(matched) > 0 else 0.0
        pool_grade = str(matched.iloc[0].get('等级', '')) if len(matched) > 0 else ''

        try:
            # Layer 1: 基本面过滤
            fund_result = fund_filter.check_quality(ts_code, industry)
            quality_score = fund_filter.calc_quality_score(fund_result.get('scores', {}))
            fund_result['quality_score'] = quality_score

            # v3: bull_stocks_all.csv 中基本面已验证的优质股（A级/最终分≥80）给予信任豁免
            pool_trusted = pool_fund_score >= 80 or 'A级' in pool_grade or 'S级' in pool_grade
            if pool_trusted:
                fund_result['pool_trusted'] = True
                fund_result['reasons'].append(f'股池验证通过(最终分{pool_fund_score:.0f}/{pool_grade})')
                if not fund_result['pass']:
                    fund_result['pass'] = True  # 覆盖Tushare财务数据不足导致的false

            # Layer 2: 量价诱空识别
            df = pv_detector.get_stk_factor_data(ts_code, trade_date, lookback=120)
            if df is None or len(df) < 30:
                continue

            pv_result = pv_detector.detect_bear_trap(df)

            # v3: 门条件过滤——非放量中阳线收复失地(量价未识别到诱空)的不进入结果
            if not pv_result.get('detected', False):
                continue

            # Layer 3: 综合评分
            result = scorer.score(fund_result, pv_result, ts_code, name)
            result['theme'] = theme
            result['industry'] = industry

            # 价格数据
            latest = df.iloc[-1]
            result['close'] = _safe(latest.get('close', 0))
            result['pct_chg'] = _safe(latest.get('pct_chg', 0))
            result['volume_ratio'] = _safe(latest.get('volume_ratio', 0))
            result['ma20'] = _safe(latest.get('ma_bfq_20', 0))
            result['ma60'] = _safe(latest.get('ma_bfq_60', 0))
            result['total_mv'] = _safe(latest.get('total_mv', 0))
            result['pe_ttm'] = _safe(latest.get('pe_ttm', 0))

            # 交易决策（仅对信号有效）
            price = result['close']
            ma60 = result['ma60']
            atr = _safe(latest.get('atr_bfq', 0))
            trading = scorer.get_trading_decision(result, price, ma60, atr)
            if trading:
                result['stop_loss'] = trading['stop_loss']
                result['stop_loss_pct'] = trading['stop_loss_pct']
                result['take_profit_1'] = trading['take_profit_1']
                result['take_profit_1_pct'] = trading['take_profit_1_pct']
                result['take_profit_2'] = trading['take_profit_2']
                result['take_profit_2_pct'] = trading['take_profit_2_pct']
                result['base_position_pct'] = trading['base_position_pct']
                result['max_position_pct'] = trading['max_position_pct']
            else:
                result['stop_loss'] = 0
                result['take_profit_1'] = 0

            results.append(result)

        except Exception as e:
            logger.warning(f"处理 {ts_code} 异常: {e}")
            continue

        processed += 1
        if processed % 20 == 0:
            logger.info(f"  进度: {processed}/{len(codes_to_scan)}")

    logger.info(f"扫描完成: 处理{processed}只, 有效{len(results)}只")

    if len(results) == 0:
        return pd.DataFrame()

    # ── 5. 结果排序 ──
    results.sort(key=lambda x: x['prob_score'], reverse=True)

    # ── 6. 输出报告 ──
    df_out = pd.DataFrame(results)
    _print_report(df_out, top_n)

    # ── 7. 保存结果 ──
    out_path = os.path.join(OUTPUT_DIR, f'bear_trap_signals_{trade_date}.csv')
    save_cols = ['ts_code', 'name', 'theme', 'industry', 'signal_level', 'signal_type',
                 'prob_score', 'fund_pass', 'fund_quality_score', 'pv_score',
                 'divergence_score', 'confirmed', 'n_core_signals', 'core_signals',
                 'close', 'pct_chg', 'volume_ratio', 'ma20', 'ma60',
                 'total_mv', 'pe_ttm', 'stop_loss', 'stop_loss_pct',
                 'take_profit_1', 'take_profit_1_pct', 'take_profit_2', 'take_profit_2_pct',
                 'base_position_pct', 'max_position_pct', 'suggestion']
    save_cols = [c for c in save_cols if c in df_out.columns]
    df_out[save_cols].to_csv(out_path, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存: {out_path} ({len(df_out)}只)")

    # ── 8. 精选信号清单 ──
    strong = df_out[df_out['signal_type'] == 'buy'].copy()
    if len(strong) > 0:
        strong_path = os.path.join(OUTPUT_DIR, f'bear_trap_strong_{trade_date}.csv')
        strong.to_csv(strong_path, index=False, encoding='utf-8-sig')
        logger.info(f"精选信号清单已保存: {strong_path} ({len(strong)}只)")

    return df_out


def _print_report(df: pd.DataFrame, top_n: int):
    """打印择时报告"""
    buy_signals = df[df['signal_type'] == 'buy']
    candidates = df[df['signal_level'].str.contains('C级', na=False)]
    others = df[~df.index.isin(buy_signals.index.union(candidates.index))]

    print()
    print("━" * 110)
    print("  空头陷阱 (Bear Trap) 择时信号矩阵")
    print("━" * 110)
    header = (f"  {'序号':>3} {'代码':>9} {'名称':>10} {'主题':>14} "
              f"{'综合分':>6} {'基本面':>6} {'量价分':>6} {'背离':>6} "
              f"{'涨跌':>6} {'信号级别':>18}  建议")
    print(header)
    print("─" * 110)

    display = df.head(top_n)
    for i, (_, r) in enumerate(display.iterrows(), 1):
        code = str(r.get('ts_code', ''))[:9]
        name_str = str(r.get('name', '') or '')[:8]
        theme_str = str(r.get('theme', '') or '')[:12]
        sig = str(r.get('signal_level', ''))[:16]
        sug = str(r.get('suggestion', ''))[:30]
        pct = r.get('pct_chg', 0)
        pct_str = f"{pct:+.1f}%" if pct != 0 else "  0.0%"

        print(f"  {i:>3} {code:>9} {name_str:>10} {theme_str:>14} "
              f"{r.get('prob_score', 0):>6.1f} "
              f"{r.get('fund_quality_score', 0):>6.1f} "
              f"{r.get('pv_score', 0):>6.1f} "
              f"{r.get('divergence_score', 0):>6.2f} "
              f"{pct_str:>6} {sig:>18}  {sug}")

    print("─" * 110)
    print(f"  Buy信号: {len(buy_signals)}只 | C级候选: {len(candidates)}只 | 其他: {len(others)}只")

    # 打印Buy信号详情
    if len(buy_signals) > 0:
        print()
        print("━" * 110)
        print("  ★ 空头陷阱信号清单 — 优选标的")
        print("━" * 110)
        for _, r in buy_signals.head(20).iterrows():
            code = str(r.get('ts_code', ''))[:9]
            name_str = str(r.get('name', '') or '')
            sigs = ', '.join(r.get('core_signals', [])) or '无'
            stop_pct = r.get('stop_loss_pct', 0)
            tp1_pct = r.get('take_profit_1_pct', 0)
            pos_pct = r.get('base_position_pct', 0)
            print(f"  {code} {name_str} | {r.get('signal_level','')} "
                  f"| 信号: {sigs[:50]} "
                  f"| 止损: {stop_pct:+.1f}% 止盈1: {tp1_pct:+.1f}% "
                  f"| 仓位: {pos_pct:.1f}%")
    print()


def main():
    parser = argparse.ArgumentParser(description='空头陷阱择时系统 — 每日盘后选股')
    parser.add_argument('--date', type=str, default=None, help='交易日 YYYYMMDD')
    parser.add_argument('--start', type=str, default=None, help='回溯起始日 YYYYMMDD')
    parser.add_argument('--end', type=str, default=None, help='回溯结束日 YYYYMMDD')
    parser.add_argument('--pool', type=str, default='qualified',
                        help='股池: qualified/all/文件路径')
    parser.add_argument('--top', type=int, default=30, help='显示前N只')
    parser.add_argument('--workers', type=int, default=4, help='并行线程数')
    args = parser.parse_args()

    # 回溯模式: --start ~ --end
    if args.start:
        end_date = args.end or args.date or datetime.now().strftime('%Y%m%d')
        all_dates = _get_trading_days(args.start, end_date)
        if not all_dates:
            logger.error(f"无交易日数据: {args.start}~{end_date}")
            return

        logger.info(f"═' 回溯模式: {all_dates[0]} ~ {all_dates[-1]}, 共{len(all_dates)}天 ═")
        summary_all = []
        for i, trade_date in enumerate(all_dates):
            logger.info(f"\n[{i+1}/{len(all_dates)}] {trade_date}")
            df = run_bear_trap_scan(
                trade_date=trade_date,
                pool_type=args.pool,
                top_n=args.top,
                max_workers=args.workers,
            )
            if df is not None and len(df) > 0:
                df['scan_date'] = trade_date
                summary_all.append(df)

        if summary_all:
            full = pd.concat(summary_all, ignore_index=True)
            out_path = os.path.join(OUTPUT_DIR,
                f'bear_trap_backtest_{args.start}_{all_dates[-1]}.csv')
            full.to_csv(out_path, index=False, encoding='utf-8-sig')
            logger.info(f"\n回溯汇总: {out_path} ({len(full)}条信号, {len(summary_all)}天有信号)")
        else:
            logger.info("回溯期间无信号")
        return

    # 单日模式
    run_bear_trap_scan(
        trade_date=args.date,
        pool_type=args.pool,
        top_n=args.top,
        max_workers=args.workers,
    )


if __name__ == '__main__':
    main()
