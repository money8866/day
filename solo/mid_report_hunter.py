# -*- coding: utf-8 -*-
"""
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃              中报预告寻宝 — 业绩增长 × 估值提升标的筛选               ┃
┃                                                                      ┃
┃  核心逻辑：在2026年中报预告密集发布期，筛选同时满足：                ┃
┃    1. 业绩大幅增长（净利润增速>30%+）                                ┃
┃    2. 估值有较大提升空间（PE处于历史低位/低于行业均值）              ┃
┃    3. 叠加专精特新/高壁垒属性（小市值高弹性）                       ┃
┃                                                                      ┃
┃  数据源：Tushare Pro                                                 ┃
┃     - forecast: 业绩预告（净利润增速、预告类型）                     ┃
┃     - fina_indicator: 最新季度财务指标（netprofit_yoy等）            ┃
┃     - daily_basic: 当前估值（PE_TTM、PB）                            ┃
┃                                                                      ┃
┃  评分体系：100分制                                                    ┃
┃    - 业绩增长维度 50分                                                ┃
┃    - 估值提升空间 30分                                                ┃
┃    - 专精特新叠加 20分                                                ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
"""

import os
import sys
import time
import json
import threading
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
from dotenv import load_dotenv

warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

load_dotenv("d:/mystock/config/.env")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'report_daily')
os.makedirs(OUTPUT_DIR, exist_ok=True)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

CACHE_DIR = Path(r"D:\mystock\cache_daily")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_rate_lock = threading.Lock()
_last_ts = time.time()
_MIN_INTERVAL = 0.13


def _rate_limit():
    global _last_ts
    with _rate_lock:
        elapsed = time.time() - _last_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_ts = time.time()


def _ts_call(func, *args, **kwargs):
    _rate_limit()
    last_err = None
    for attempt in range(3):
        try:
            res = func(*args, **kwargs)
            return res
        except Exception as e:
            last_err = e
            msg = str(e)
            if '频率' in msg or 'frequency' in msg.lower():
                time.sleep(2.0 + attempt * 2.0)
            else:
                time.sleep(1.0)
    if last_err:
        raise last_err
    return None


def load_cache(path: Path, expire_hours: int = 24):
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > expire_hours * 3600:
            return None
        if path.suffix == '.parquet':
            return pd.read_parquet(path)
        elif path.suffix == '.json':
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        return None
    return None


def save_cache(data, path: Path):
    try:
        if isinstance(data, pd.DataFrame):
            data.to_parquet(path, index=False)
        elif isinstance(data, (dict, list)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 数据获取 ──────────────────────────────────────────────

def get_last_trade_date() -> str:
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    now = datetime.now()
    today = now.strftime('%Y%m%d')
    try:
        cal = _ts_call(pro.trade_cal, exchange='SSE',
                       start_date=(now - timedelta(days=10)).strftime('%Y%m%d'),
                       end_date=today)
        if cal is not None and len(cal) > 0:
            trading = cal[cal['is_open'] == 1].sort_values('cal_date')
            if len(trading) > 0:
                if now.hour < 16:
                    return str(trading[trading['cal_date'] < today].iloc[-1]['cal_date'])
                else:
                    if today in trading['cal_date'].values:
                        return today
                    return str(trading.iloc[-1]['cal_date'])
    except Exception:
        pass
    return today


def get_forecast_data() -> pd.DataFrame:
    """获取所有已发布的中报(yearly)预告数据"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_path = CACHE_DIR / "mid_report_forecast.parquet"
    cached = load_cache(cache_path, 6)
    if cached is not None:
        return cached

    all_dfs = []
    today = datetime.now()
    for i in range(60):
        d = (today - timedelta(days=i)).strftime('%Y%m%d')
        try:
            df = _ts_call(pro.forecast, ann_date=d, end_date='20260630',
                          fields='ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,last_parent_net,summary')
            if df is not None and len(df) > 0:
                all_dfs.append(df)
        except Exception:
            pass

    if all_dfs:
        df_all = pd.concat(all_dfs).drop_duplicates(subset=['ts_code']).reset_index(drop=True)
        save_cache(df_all, cache_path)
        return df_all
    return pd.DataFrame()


def get_industry_pe() -> pd.DataFrame:
    """获取行业平均PE（用于估值对比）"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_path = CACHE_DIR / "industry_pe.parquet"
    cached = load_cache(cache_path, 24)
    if cached is not None:
        return cached

    trade_date = get_last_trade_date()
    try:
        df = _ts_call(pro.stock_vs_industry, trade_date=trade_date)
        if df is not None and len(df) > 0:
            save_cache(df, cache_path)
            return df
    except Exception:
        pass
    return pd.DataFrame()


def get_stock_basic() -> pd.DataFrame:
    """获取全市场股票列表"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_path = CACHE_DIR / "stock_basic_L.parquet"
    cached = load_cache(cache_path, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.stock_basic, exchange='', list_status='L',
                  fields='ts_code,symbol,name,area,industry,market,list_date,is_hs')
    if df is not None and len(df) > 0:
        df['list_date'] = df['list_date'].astype(str)
        save_cache(df, cache_path)
    return df if df is not None else pd.DataFrame()


def get_daily_basic(trade_date: str) -> pd.DataFrame:
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_path = CACHE_DIR / f"daily_basic_{trade_date}.parquet"
    cached = load_cache(cache_path, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.daily_basic, trade_date=trade_date,
                  fields='ts_code,trade_date,close,total_mv,circ_mv,pe,pe_ttm,pb,turnover_rate,volume_ratio')
    if df is not None and len(df) > 0:
        save_cache(df, cache_path)
    return df if df is not None else pd.DataFrame()


def get_fina_indicator(ts_code: str) -> pd.DataFrame:
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_path = CACHE_DIR / f"fin_ind_{ts_code.replace('.','_')}.parquet"
    cached = load_cache(cache_path, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.fina_indicator, ts_code=ts_code,
                  fields='ts_code,end_date,eps,roe,grossprofit_margin,netprofit_margin,'
                         'netprofit_yoy,basic_eps_yoy,tr_yoy,or_yoy,op_yoy,'
                         'adminexp_of_gr,roic')
    if df is not None and len(df) > 0:
        save_cache(df, cache_path)
    return df if df is not None else pd.DataFrame()


# ── 行业PE对照表（静态备用，当API无数据时使用） ──────────

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


# ── 评分系统（100分制） ─────────────────────────────────


def _get_industry_pe(industry: str, industry_pe_df: pd.DataFrame = None) -> float:
    """获取行业参考PE"""
    # 优先从API数据获取
    if industry_pe_df is not None and len(industry_pe_df) > 0:
        matched = industry_pe_df[industry_pe_df['industry'].str.contains(industry, na=False)]
        if len(matched) > 0:
            return float(matched['pe_ttm'].median())
    # 回退到静态参考表
    for kw, pe in _INDUSTRY_PE_REF.items():
        if kw in industry:
            return pe
    return 25.0  # 默认


def compute_score(row: dict, industry_pe_df: pd.DataFrame = None) -> Dict:
    """
    中报业绩增长 × 估值提升 评分（100分制）

    维度：
      - 业绩增长维度 50分
        - 预告净利润增速 30分
        - 最新季度同比增速 10分
        - 预告类型 10分
      - 估值提升空间 30分
        - PE_TTM vs 行业PE 15分
        - PE历史分位 10分
        - PEG 5分
      - 专精特新叠加 20分
        - 双创/科创板 5分
        - 小市值弹性 8分
        - 标签壁垒 7分
    """
    details = {}
    total = 0.0

    # ── 1. 业绩增长维度 (50分) ──

    # 1a. 预告净利润增速 (30分)
    p_min = row.get('p_change_min', 0)
    p_max = row.get('p_change_max', 0)
    p_change = (p_min + p_max) / 2  # 取中值

    if p_change >= 200:
        growth_score = 30.0
    elif p_change >= 100:
        growth_score = 25.0 + (p_change - 100) / 100 * 5
    elif p_change >= 50:
        growth_score = 18.0 + (p_change - 50) / 50 * 7
    elif p_change >= 30:
        growth_score = 10.0 + (p_change - 30) / 20 * 8
    elif p_change >= 0:
        growth_score = 3.0 + p_change / 30 * 7
    else:
        growth_score = 0.0
    total += growth_score
    details['业绩增速分'] = round(growth_score, 1)
    details['预告增速中值(%)'] = round(p_change, 1)
    details['预告增速下限(%)'] = p_min
    details['预告增速上限(%)'] = p_max

    # 1b. 最新季度同比增速 (10分) — 用netprofit_yoy
    yoy = row.get('netprofit_yoy', 0)
    if yoy >= 200:
        yoy_score = 10.0
    elif yoy >= 100:
        yoy_score = 8.0 + (yoy - 100) / 100 * 2
    elif yoy >= 50:
        yoy_score = 5.0 + (yoy - 50) / 50 * 3
    elif yoy >= 20:
        yoy_score = 2.0 + (yoy - 20) / 30 * 3
    elif yoy > 0:
        yoy_score = 1.0
    else:
        yoy_score = 0.0
    total += yoy_score
    details['最新季度同比分'] = round(yoy_score, 1)
    details['最新季度净利同比(%)'] = round(yoy, 1) if pd.notna(yoy) else 0

    # 1c. 预告类型 (10分)
    ftype = str(row.get('type', ''))
    type_score_map = {
        '预增': 10.0, '大幅上升': 10.0,
        '扭亏': 8.0,
        '略增': 6.0,
        '续盈': 4.0,
        '预平': 2.0,
        '略减': 0.0, '预减': 0.0, '首亏': 0.0, '续亏': 0.0, '增亏': 0.0,
    }
    ftype_score = type_score_map.get(ftype, 2.0)
    total += ftype_score
    details['预告类型分'] = ftype_score
    details['预告类型'] = ftype

    # ── 2. 估值提升空间 (30分) ──

    # 2a. PE_TTM vs 行业PE (15分) — 低于行业水平有提升空间
    pe_ttm = row.get('pe_ttm', 0)
    industry = str(row.get('industry', ''))
    ind_pe = _get_industry_pe(industry, industry_pe_df)

    if pe_ttm <= 0 or pe_ttm >= 500:
        # PE为负或极高，不参与估值分
        pe_score = 0.0
    elif pe_ttm < ind_pe * 0.5:
        pe_score = 15.0
    elif pe_ttm < ind_pe * 0.8:
        pe_score = 12.0 + (ind_pe * 0.8 - pe_ttm) / (ind_pe * 0.3) * 3
    elif pe_ttm <= ind_pe:
        pe_score = 8.0 + (ind_pe - pe_ttm) / (ind_pe * 0.2) * 4
    elif pe_ttm <= ind_pe * 1.5:
        pe_score = 4.0 + (ind_pe * 1.5 - pe_ttm) / (ind_pe * 0.5) * 4
    elif pe_ttm <= ind_pe * 2.0:
        pe_score = 2.0 + (ind_pe * 2.0 - pe_ttm) / (ind_pe * 0.5) * 2
    else:
        pe_score = 0.0
    total += pe_score
    details['PE估值分'] = round(pe_score, 1)
    details['PE_TTM'] = round(pe_ttm, 1) if pe_ttm > 0 else 0
    details['行业参考PE'] = round(ind_pe, 1)

    # 2b. PEG得分 (5分) — PEG < 1 说明估值有提升空间
    if pe_ttm > 0 and p_change > 0:
        peg = pe_ttm / p_change if p_change > 0 else 999
        if peg <= 0.5:
            peg_score = 5.0
        elif peg <= 1.0:
            peg_score = 4.0 + (1.0 - peg) / 0.5 * 1
        elif peg <= 2.0:
            peg_score = 2.0 + (2.0 - peg) / 1.0 * 2
        elif peg <= 5.0:
            peg_score = 0.5 + (5.0 - peg) / 3.0 * 1.5
        else:
            peg_score = 0.0
    else:
        peg_score = 0.0
    total += peg_score
    details['PEG分'] = round(peg_score, 1)
    details['PEG'] = round(peg, 2) if pe_ttm > 0 and p_change > 0 else 0

    # 2c. PB历史分位替代 (10分) — 用PB相对行业
    pb = row.get('pb', 0)
    if pb <= 0:
        pb_score = 0.0
    elif pb <= 2.0:
        pb_score = 10.0
    elif pb <= 4.0:
        pb_score = 7.0 + (4.0 - pb) / 2.0 * 3
    elif pb <= 8.0:
        pb_score = 3.0 + (8.0 - pb) / 4.0 * 4
    else:
        pb_score = 0.0
    total += pb_score
    details['PB分'] = round(pb_score, 1)
    details['PB'] = round(pb, 2) if pb > 0 else 0

    # ── 3. 专精特新叠加 (20分) ──

    # 3a. 双创/科创板 (5分)
    board = str(row.get('board', ''))
    if board in ('创业板', '科创板'):
        board_score = 5.0
    else:
        board_score = 2.0
    total += board_score
    details['板块分'] = round(board_score, 1)

    # 3b. 小市值弹性 (8分) — 30~80亿最优
    mv = row.get('total_mv', 0) / 10000  # 万元转亿
    if 30 <= mv <= 80:
        mv_score = 8.0
    elif 20 <= mv < 30:
        mv_score = 6.0 + (mv - 20) / 10 * 2
    elif 80 < mv <= 150:
        mv_score = 5.0 + (150 - mv) / 70 * 3
    elif 150 < mv <= 300:
        mv_score = 2.0 + (300 - mv) / 150 * 3
    else:
        mv_score = 0.0
    total += mv_score
    details['市值弹性分'] = round(mv_score, 1)
    details['总市值(亿)'] = round(mv, 1)

    # 3c. 标签壁垒 (7分) — 主营业务含壁垒关键词
    bz_items = str(row.get('main_bz', ''))
    tag_score = 0.0
    tag_details = []

    _bz_keywords = [
        '半导体', '芯片', '树脂', '吸附', '分离', '膜', '催化', '靶材',
        '核电', '核能', '核工业', '军工', '航天', '航空', '发动机',
        '机器人', '智能装备', '数控', '精密',
        '生物医药', '医疗器械', '创新药',
        '新能源', '锂电', '光伏', '氢能', '储能',
        '新材料', '特种材料', '复合材料',
        '传感器', '激光', '光学', '光电',
        '特种气体', '高纯', '超纯', '超净',
        '专精特新', '小巨人', '国产替代', '卡脖子',
        '密封', '绝缘', '靶材', '溅射', '镀膜',
    ]

    matched = [kw for kw in _bz_keywords if kw in bz_items]
    if matched:
        tag_score += min(5.0, len(matched) * 1.0)
        tag_details.extend(matched[:4])
    name = str(row.get('name', ''))
    name_matched = [kw for kw in _bz_keywords if kw in name]
    if name_matched:
        tag_score += min(2.0, len(name_matched) * 0.8)
        tag_details.extend(name_matched[:2])

    tag_score = min(7.0, tag_score)
    total += tag_score
    details['壁垒标签分'] = round(tag_score, 1)
    details['壁垒标签'] = ';'.join(tag_details[:5]) if tag_details else ''
    details['主营业务'] = bz_items[:80] if bz_items else ''

    # ── 总分 ──
    total = round(total, 1)
    details['总分'] = total

    return details


# ── 主扫描流程 ──────────────────────────────────────────


def run_screening(min_score: float = 50.0) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    执行中报预告 × 估值提升全市场扫描

    Args:
        min_score: 最低入围分

    Returns:
        (passed, all_df)
    """
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)

    trade_date = get_last_trade_date()

    print(f"{'='*70}")
    print(f"  中报预告寻宝 — 业绩增长 × 估值提升标的筛选")
    print(f"  交易日: {trade_date}  最低入围分: {min_score}")
    print(f"{'='*70}")

    # ── Phase 1: 获取中报预告数据 ──
    print("\n[Phase 1] 获取中报预告数据...")
    forecast = get_forecast_data()
    if len(forecast) == 0:
        print("  ✗ 无中报预告数据！")
        return pd.DataFrame(), pd.DataFrame()
    print(f"  → 已发布中报预告: {len(forecast)} 只")

    # 只保留正增长或有意义的预告
    valid_types = ['预增', '略增', '扭亏', '大幅上升', '续盈']
    forecast = forecast[forecast['type'].isin(valid_types)].copy()
    print(f"  → 正增长类型: {len(forecast)} 只")

    # ── Phase 2: 获取全市场基本信息 ──
    print("\n[Phase 2] 获取股票基础信息...")
    stocks = get_stock_basic()
    # 排除北交所
    stocks = stocks[~stocks['ts_code'].str.match(r'^(8|4|9)\d{5}\.')]
    print(f"  → 上市股票(排除北交所): {len(stocks)}")

    # 合并
    df = forecast.merge(stocks, on='ts_code', how='inner')
    print(f"  → 合并后: {len(df)} 只")

    # 标记板块
    def _detect_board(ts_code, market):
        if market == '科创板' or ts_code.startswith('688'):
            return '科创板'
        if ts_code.startswith('30'):
            return '创业板'
        return '主板'
    df['board'] = df.apply(lambda r: _detect_board(r['ts_code'], str(r.get('market', ''))), axis=1)

    # ── Phase 3: 获取市值/估值数据 ──
    print("\n[Phase 3] 获取市值/估值数据...")
    basic = get_daily_basic(trade_date)
    if basic is None or len(basic) == 0:
        print("  ✗ 无法获取市值数据！")
        forecast.to_csv(os.path.join(OUTPUT_DIR, f'forecast_raw_{trade_date}.csv'), index=False, encoding='utf-8-sig')
        return pd.DataFrame(), pd.DataFrame()

    df = df.merge(basic[['ts_code', 'total_mv', 'circ_mv', 'pe_ttm', 'pb']],
                  on='ts_code', how='inner')
    print(f"  → 合并后: {len(df)} 只")

    # ── Phase 4: 获取行业PE数据 ──
    print("\n[Phase 4] 获取行业PE参考数据...")
    industry_pe_df = get_industry_pe()
    print(f"  → 行业PE: {len(industry_pe_df) if industry_pe_df is not None else 0} 条")

    # ── Phase 5: 获取最新季度财务指标 ──
    print(f"\n[Phase 5] 获取最新季度财务指标...")
    print(f"  → 共 {len(df)} 只股票，逐个获取中...")

    fin_data = {}
    for idx, (_, row) in enumerate(df.iterrows()):
        code = row['ts_code']
        name = row['name']
        if (idx + 1) % 30 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(df)}] {name}({code})")

        try:
            fin = get_fina_indicator(code)
            if fin is not None and len(fin) > 0:
                fin = fin.sort_values('end_date', ascending=False)
                latest = fin.iloc[0]
                fin_data[code] = {
                    'netprofit_yoy': float(latest.get('netprofit_yoy', 0) or 0),
                    'basic_eps_yoy': float(latest.get('basic_eps_yoy', 0) or 0),
                    'tr_yoy': float(latest.get('tr_yoy', 0) or 0),
                    'roe': float(latest.get('roe', 0) or 0),
                    'grossprofit_margin': float(latest.get('grossprofit_margin', 0) or 0),
                    'netprofit_margin': float(latest.get('netprofit_margin', 0) or 0),
                    'adminexp_of_gr': float(latest.get('adminexp_of_gr', 0) or 0),
                }
            else:
                fin_data[code] = {k: 0 for k in ['netprofit_yoy', 'basic_eps_yoy', 'tr_yoy',
                                                   'roe', 'grossprofit_margin', 'netprofit_margin',
                                                   'adminexp_of_gr']}
        except Exception:
            fin_data[code] = {k: 0 for k in ['netprofit_yoy', 'basic_eps_yoy', 'tr_yoy',
                                               'roe', 'grossprofit_margin', 'netprofit_margin',
                                               'adminexp_of_gr']}

    # 合并财务数据
    fin_records = []
    for _, row in df.iterrows():
        code = row['ts_code']
        fd = fin_data.get(code, {})
        fin_records.append({**row.to_dict(), **fd})
    df = pd.DataFrame(fin_records)

    # ── Phase 6: 获取主营业务 ──
    print(f"\n[Phase 6] 主营业务识别...")
    bz_data = {}
    for idx, (_, row) in enumerate(df.iterrows()):
        code = row['ts_code']
        if (idx + 1) % 30 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(df)}] 标签分析中...")
        try:
            _rate_limit()
            import tushare as ts
            _pro = ts.pro_api(TUSHARE_TOKEN)
            bz = _ts_call(_pro.fina_mainbz, ts_code=code)
            if bz is not None and len(bz) > 0:
                bz = bz.sort_values('end_date', ascending=False)
                latest_date = bz.iloc[0]['end_date']
                bz = bz[bz['end_date'] == latest_date]
                bz_items = '; '.join(bz['bz_item'].dropna().astype(str).tolist()[:5]) if len(bz) > 0 else ''
            else:
                bz_items = ''
        except Exception:
            bz_items = ''
        bz_data[code] = bz_items

    df['main_bz'] = df['ts_code'].map(bz_data)

    # ── Phase 7: 评分 ──
    print(f"\n[Phase 7] 综合评分...")
    score_results = []
    for _, row in df.iterrows():
        details = compute_score(row.to_dict(), industry_pe_df)
        score_results.append({
            'ts_code': row['ts_code'],
            'name': row['name'],
            **details,
        })

    result_df = pd.DataFrame(score_results)
    result_df = result_df.sort_values('总分', ascending=False).reset_index(drop=True)
    result_df['排名'] = range(1, len(result_df) + 1)

    # 入围筛选
    passed = result_df[(result_df['总分'] >= min_score) &
                       (result_df['业绩增速分'] >= 5)].copy()
    passed = passed.sort_values('总分', ascending=False).reset_index(drop=True)

    print(f"\n{'='*70}")
    print(f"  扫描完成！")
    print(f"  中报预告发布总数: {len(forecast)}")
    print(f"  有估值数据: {len(df)}")
    print(f"  入围(≥{min_score}分): {len(passed)} 只")
    print(f"{'='*70}")

    return passed, result_df


# ── 报告输出 ──────────────────────────────────────────


def print_report(passed: pd.DataFrame, all_df: pd.DataFrame, trade_date: str, min_score: float = 50.0):
    """打印格式化报告"""
    output_csv = os.path.join(OUTPUT_DIR, f'mid_report_hunt_{trade_date}.csv')
    if len(passed) > 0:
        passed.to_csv(output_csv, index=False, encoding='utf-8-sig')
        print(f"\nCSV已保存: {output_csv}")

    all_csv = os.path.join(OUTPUT_DIR, f'mid_report_hunt_all_{trade_date}.csv')
    all_df.to_csv(all_csv, index=False, encoding='utf-8-sig')

    if len(passed) == 0:
        print("\n⚠ 未找到符合条件的标的。")
        print("  可能原因：中报预告尚在发布初期，数据量不足以支撑筛选")
        print("  建议：随着更多公司发布预告，每天运行一次即可")
        return

    # 分级
    tier1 = passed[passed['总分'] >= 75].sort_values('总分', ascending=False)
    tier2 = passed[(passed['总分'] >= 60) & (passed['总分'] < 75)].sort_values('总分', ascending=False)
    tier3 = passed[(passed['总分'] >= min_score) & (passed['总分'] < 60)].sort_values('总分', ascending=False)

    print(f"\n{'━'*70}")
    print(f"  中报预告寻宝结果报告 — {trade_date}")
    print(f"{'━'*70}")

    print(f"\n{'█'*70}")
    print(f"  ★★★ 第一梯队（总分≥75，业绩估值双击）★★★")
    print(f"{'█'*70}")
    if len(tier1) > 0:
        for _, r in tier1.iterrows():
            _print_card(r)
    else:
        print("  （无）")

    print(f"\n{'▌'*35}")
    print(f"  ★★ 第二梯队（总分60~75，重点关注）")
    print(f"{'▌'*35}")
    if len(tier2) > 0:
        for _, r in tier2.iterrows():
            _print_card(r)
    else:
        print("  （无）")

    print(f"\n{'▌'*35}")
    print(f"  ★ 第三梯队（总分{min_score}~60，纳入观察）")
    print(f"{'▌'*35}")
    if len(tier3) > 0:
        for _, r in tier3.iterrows():
            _print_mini(r)
    else:
        print("  （无）")

    # 统计摘要
    print(f"\n{'─'*70}")
    print(f"  统计摘要")
    print(f"{'─'*70}")
    print(f"  入围总数: {len(passed)}")
    print(f"  平均总分: {passed['总分'].mean():.1f}")
    print(f"  平均预告增速: {passed['预告增速中值(%)'].mean():.1f}%")
    print(f"  平均PE_TTM: {passed['PE_TTM'].mean():.1f}")
    print(f"  平均PEG: {passed['PEG'].mean():.2f}")
    print(f"  平均市值: {passed['总市值(亿)'].mean():.1f}亿")
    tier1_count = len(passed[passed['总分'] >= 75])
    tier2_count = len(passed[(passed['总分'] >= 60) & (passed['总分'] < 75)])
    print(f"  第一梯队: {tier1_count}  第二梯队: {tier2_count}  第三梯队: {len(passed)-tier1_count-tier2_count}")

    print(f"\n{'═'*70}")
    print(f"  操作建议")
    print(f"{'═'*70}")
    print(f"  • 已发布中报预告公司筛选，建议每周更新一次")
    print(f"  • 重点关注：预告增速>50% + PE_TTM<行业均值 + 小市值")
    print(f"  • 业绩预告类型为'预增'或'扭亏'的置信度最高")
    print(f"  • 叠加专精特新/壁垒标签的标的弹性更大")
    print(f"  • 注意：PEG<1 说明估值相对业绩增速低估，有较大修复空间")
    print(f"{'═'*70}")


def _print_card(r):
    tags = str(r.get('壁垒标签', ''))
    bz = str(r.get('主营业务', ''))
    print(f"\n  ┌─────────────────────────────────────────────────────┐")
    print(f"  │ {r['name']} ({r['ts_code']})  ┃  总分: {r['总分']}")
    print(f"  ├─────────────────────────────────────────────────────┤")
    print(f"  │ 业绩增速 {r.get('预告增速中值(%)', 0):>6.1f}%  │ 类型 {r.get('预告类型', ''):>4}  │ 季同比 {r.get('最新季度净利同比(%)', 0):>6.1f}%")
    print(f"  │ PE_TTM {r.get('PE_TTM', 0):>6.1f}  │ 行业PE {r.get('行业参考PE', 0):>6.1f}  │ PEG {r.get('PEG', 0):>6.2f}")
    print(f"  │ 市值 {r.get('总市值(亿)', 0):>6.1f}亿  │ PB {r.get('PB', 0):>6.2f}  │ 板块 {r.get('板块分', 0):>2.0f}分")
    if tags:
        print(f"  │ 标签: {tags}")
    if bz:
        bz_short = bz if len(bz) <= 78 else bz[:75] + '...'
        print(f"  │ 主营: {bz_short}")
    print(f"  └─────────────────────────────────────────────────────┘")


def _print_mini(r):
    print(f"  {r['name']:>8}({r['ts_code']})  "
          f"总分{r['总分']:>5.1f}  "
          f"增速{r.get('预告增速中值(%)', 0):>6.1f}%  "
          f"PE{r.get('PE_TTM', 0):>6.1f}  "
          f"市值{r.get('总市值(亿)', 0):>5.1f}亿")


# ── 主入口 ──────────────────────────────────────────────


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='中报预告寻宝 — 业绩增长×估值提升标的筛选')
    parser.add_argument('--min_score', type=float, default=50.0, help='最低入围分（默认50）')
    args = parser.parse_args()

    t0 = time.time()
    passed, all_df = run_screening(min_score=args.min_score)

    trade_date = get_last_trade_date()
    print_report(passed, all_df, trade_date, min_score=args.min_score)

    elapsed = time.time() - t0
    print(f"\n⏱ 总耗时: {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")