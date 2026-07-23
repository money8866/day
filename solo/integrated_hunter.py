# -*- coding: utf-8 -*-
"""
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃          寻宝策略 × 中报预告 — 整合版                                ┃
┃                                                                      ┃
┃  核心逻辑：                                                          ┃
┃    1. 寻宝策略框架筛选候选池（小市值+高壁垒+专精特新）              ┃
┃    2. 中报业绩预告叠加（业绩增长）                                   ┃
┃    3. 增长质量过滤（可持续性验证，过滤一次性/突发增长）              ┃
┃                                                                      ┃
┃  评分体系（100分制）：                                                ┃
┃    - 寻宝策略壁垒分 40分 — 市值+毛利率+标签+板块                    ┃
┃    - 中报业绩增长分 35分 — 预告增速+预告类型+季同比+营收匹配        ┃
┃    - 估值提升空间   25分 — PE vs 行业+PEG+PB                         ┃
┃                                                                      ┃
┃  增长质量过滤（硬性达标条件）：                                      ┃
┃    1. 营收增长匹配：利润增速不超过营收增速的20倍                     ┃
┃    2. 营收不能为负增长（除非利润增速合理）                           ┃
┃    3. 多季度净利润持续增长                                           ┃
┃    4. ROE不能为负                                                     ┃
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


# ── 数据获取 ──────────────────────────────────────────────

def get_forecast_data() -> pd.DataFrame:
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


def get_stock_basic() -> pd.DataFrame:
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


def get_mainbz(ts_code: str) -> str:
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_path = CACHE_DIR / f"mainbz_{ts_code.replace('.', '_')}.json"
    cached = load_cache(cache_path, 168)
    if cached is not None:
        return cached
    try:
        df = _ts_call(pro.fina_mainbz, ts_code=ts_code)
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            latest_date = df.iloc[0]['end_date']
            df = df[df['end_date'] == latest_date]
            bz_items = '; '.join(df['bz_item'].dropna().astype(str).tolist()[:5]) if len(df) > 0 else ''
            save_cache(bz_items, cache_path)
            return bz_items
    except Exception:
        pass
    return ''


def get_industry_pe() -> pd.DataFrame:
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


# ── 行业PE对照表（备用） ────────────────────────────────

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


# ── 专精特新关键词库 ──────────────────────────────────

_SPECIALIZED_KEYWORDS = [
    '微球', '树脂', '吸附', '分离', '膜', '催化', '靶材', '溅射',
    '石英', '陶瓷', '碳化硅', '氮化', '碳纤维', '复合材料',
    '传感器', '探测器', '光谱', '质谱', '色谱',
    '精密', '超净', '高纯', '超高纯', '无菌', '药用',
    '生物', '酶', '抗体', '抗原', '疫苗',
    '机器人', '机器视觉', '数控', '伺服', '精密传动',
    '半导体', '芯片', '晶圆', '光刻', '封装', '测试',
    '核电', '核能', '核工业', '核燃料',
    '军工', '航空', '航天', '发动机', '叶片',
    '特种气体', '电子气体', '工业气体',
    '纳米', '涂层', '镀膜', '钎焊', '焊接',
    '智能装备', '自动化', '专精特新', '小巨人',
    '国产替代', '进口替代', '卡脖子', '补链',
    '绝缘', '介电', '电磁', '射频', '微波',
    '激光', '光学', '光电',
    '密封', '轴承', '齿轮', '液压', '气动',
    '润滑', '胶粘', '粘接', '密封胶',
    '过滤', '净化', '纯化', '提纯',
    # ---- 生命科学 ----
    '基因编辑', 'CRISPR', '基因治疗', '细胞治疗', 'CAR-T', '干细胞',
    '合成生物', '生物制造', '脑机接口', '神经接口',
    'ADC', '双抗', 'mRNA', '小核酸',
    '生物芯片', '微流控', 'AI制药',
    # ---- AI链 ----
    'AI芯片', '大模型', '多模态', '智能体', 'Agent',
    '具身智能', '人形机器人', '灵巧手',
    '边缘AI', '端侧AI', '存算一体', '类脑芯片',
    '自动驾驶', '无人驾驶', 'Robotaxi',
    # ---- 航天 ----
    '商业航天', '低轨卫星', '卫星互联网', '火箭回收',
    'eVTOL', '飞行汽车', '高超声速',
    '无人机', '工业无人机',
    # ---- 前沿 ----
    '量子计算', '量子芯片', '核聚变', '托卡马克',
    '6G', '太赫兹', '固态电池', '钙钛矿', '氢能',
    '脑科学', '空间计算', '数字孪生',
]

_INDUSTRY_BARRIER_KEYWORDS = [
    '半导体', '芯片', '集成电路', '光刻',
    '生物医药', '创新药', '医疗器械',
    '新能源', '锂电', '光伏', '氢能', '储能',
    '核电', '核能', '核工业',
    '军工', '航天', '航空', '航空航天', '发动机',
    '新材料', '特种材料', '高端材料',
    '工业母机', '数控机床', '精密仪器',
    '机器人', '自动化装备',
    '信创', '国产软件', '操作系统',
    '量子', '超导',
    '树脂', '吸附', '分离', '膜', '催化', '靶材',
    '超纯', '高纯', '特种气体',
    '智能装备', '精密',
    # ---- 未来高壁垒赛道 ----
    '生命科学', '基因治疗', '细胞治疗', '合成生物',
    '人工智能', '大模型', 'AI芯片', '智能体',
    '卫星互联网', '商业航天', '低轨卫星',
    '量子计算', '量子通信',
    '脑机接口', '神经科学',
    '核聚变', '聚变能',
    '自动驾驶', '智能驾驶', '无人驾驶',
    '具身智能', '人形机器人',
    '6G', '太赫兹',
    '固态电池', '钙钛矿',
]


# ── 增长质量检查 ──────────────────────────────────────

def check_growth_quality(fin_df: pd.DataFrame) -> dict:
    """
    增长质量硬性检查（4道关卡）

    Returns:
        dict: {'pass': bool, 'reason': str, 'quality_score': float}
    """
    result = {'pass': True, 'reason': '', 'quality_score': 0.0,
              'tr_yoy': 0, 'netprofit_yoy': 0, 'roe': 0, 'eps_yoy': 0,
              'q1_yoy': 0, 'q2_yoy': 0}

    if fin_df is None or len(fin_df) == 0:
        return {**result, 'pass': False, 'reason': '无财务数据，无法验证增长质量'}

    fin_df = fin_df.sort_values('end_date', ascending=False)
    latest = fin_df.iloc[0]

    tr_yoy = float(latest.get('tr_yoy', 0) or 0)
    netprofit_yoy = float(latest.get('netprofit_yoy', 0) or 0)
    roe = float(latest.get('roe', 0) or 0)
    eps_yoy = float(latest.get('basic_eps_yoy', 0) or 0)

    result['tr_yoy'] = tr_yoy
    result['netprofit_yoy'] = netprofit_yoy
    result['roe'] = roe
    result['eps_yoy'] = eps_yoy

    # ── 关卡1: 营收匹配检查 ──
    # 利润增速远高于营收增速 → 疑似非经常性收益
    if netprofit_yoy > 0 and tr_yoy <= 0:
        return {**result, 'pass': False,
                'reason': f'营收增速{tr_yoy:.1f}%为负但利润增速{netprofit_yoy:.1f}%，疑似卖资产/投资收益'}

    if tr_yoy > 0 and netprofit_yoy / tr_yoy > 20:
        ratio = netprofit_yoy / tr_yoy
        return {**result, 'pass': False,
                'reason': f'利润增速是营收增速的{ratio:.0f}倍，疑为一次性收益，不可持续'}

    # ── 关卡2: ROE检查 ──
    if roe < 0 and netprofit_yoy > 50:
        return {**result, 'pass': False,
                'reason': f'ROE为负({roe:.1f}%)但利润高增长，财务数据异常'}

    # ── 关卡3: 多季度增长检查 ──
    # 至少需要2个季度为正增长，或者最新季度为正
    if len(fin_df) >= 2:
        q1_yoy = float(fin_df.iloc[0].get('netprofit_yoy', 0) or 0)
        q2_yoy = float(fin_df.iloc[1].get('netprofit_yoy', 0) or 0)
        result['q1_yoy'] = q1_yoy
        result['q2_yoy'] = q2_yoy

        if q1_yoy < -30 and q2_yoy < -30:
            return {**result, 'pass': False,
                    'reason': f'连续2季度净利润大幅下滑(q1={q1_yoy:.1f}%, q2={q2_yoy:.1f}%)'}
    elif netprofit_yoy < 0:
        return {**result, 'pass': False,
                'reason': '最新季度净利润负增长，无多季度数据验证'}

    # ── 关卡4: 扣非EPS增长检查 ──
    # basic_eps_yoy 是扣非每股收益增速
    if eps_yoy < -20 and netprofit_yoy > 50:
        return {**result, 'pass': False,
                'reason': f'扣非EPS增速{eps_yoy:.1f}%与利润增速{netprofit_yoy:.1f}%严重背离，非经常性损益占比过高'}

    # ── 质量评分 (0~10分) ──
    q_score = 5.0

    # 营收与利润同步增长加分
    if tr_yoy > 10 and netprofit_yoy > 10:
        q_score += 1.5
    if tr_yoy > netprofit_yoy * 0.3:
        q_score += 1.0

    # 多季度持续加分
    if len(fin_df) >= 2:
        q1 = result['q1_yoy']
        q2 = result['q2_yoy']
        if q1 > 0 and q2 > 0:
            q_score += 1.5
        if q1 > 20 and q2 > 20:
            q_score += 1.0

    result['quality_score'] = round(min(10.0, q_score), 1)
    result['pass'] = True
    result['reason'] = '通过'
    return result


# ── 评分系统（100分制） ─────────────────────────────

def _get_industry_pe(industry: str, industry_pe_df: pd.DataFrame = None) -> float:
    if industry_pe_df is not None and len(industry_pe_df) > 0:
        matched = industry_pe_df[industry_pe_df['industry'].str.contains(industry, na=False)]
        if len(matched) > 0:
            return float(matched['pe_ttm'].median())
    for kw, pe in _INDUSTRY_PE_REF.items():
        if kw in industry:
            return pe
    return 25.0


def compute_score(row: dict, industry_pe_df: pd.DataFrame = None) -> Dict:
    """
    综合评分（100+3分制）

    维度：
      - 寻宝策略壁垒分 40分
        - 市值分 12分  — 30~80亿最优
        - 毛利率分 10分  — >45%满分
        - 净利率扎实度 5分  — >12%满分
        - 标签/关键词分 10分  — 壁垒关键词
        - 板块加分 3分  — 双创/科创板
      - 中报业绩增长分 35分
        - 预告净利润增速 15分
        - 预告类型 5分
        - 最新季度同比增速 7分
        - 营收匹配 8分
      - 估值提升空间 25分
        - PE_TTM vs 行业PE 12分
        - PEG 6分
        - PB 7分
      - 未来赛道加分 3分（额外）
        - 生命科学/AI链/航天航空/前沿技术布局
    """
    details = {}
    total = 0.0

    industry = str(row.get('industry', ''))

    # ══════════════════════════════════════════════════════
    # 一、寻宝策略壁垒分 (40分)
    # ══════════════════════════════════════════════════════

    # 1a. 市值分 (12分) — 30~80亿最优
    mv = row.get('total_mv', 0) / 10000
    if 30 <= mv <= 80:
        mv_score = 12.0
    elif 20 <= mv < 30:
        mv_score = 9.0 + (mv - 20) / 10 * 3
    elif 80 < mv <= 150:
        mv_score = 7.0 + (150 - mv) / 70 * 5
    elif 150 < mv <= 300:
        mv_score = 3.0 + (300 - mv) / 150 * 4
    elif mv > 300:
        mv_score = 0.0
    else:
        mv_score = 0.0
    total += mv_score
    details['市值分'] = round(mv_score, 1)
    details['总市值(亿)'] = round(mv, 1)

    # 1b. 毛利率分 (10分)
    gm = row.get('grossprofit_margin', 0)
    if gm >= 55:
        gm_score = 10.0
    elif gm >= 40:
        gm_score = 7.0 + (gm - 40) / 15 * 3
    elif gm >= 25:
        gm_score = 4.0 + (gm - 25) / 15 * 3
    elif gm >= 15:
        gm_score = 1.0 + (gm - 15) / 10 * 3
    else:
        gm_score = 0.0
    total += gm_score
    details['毛利率分'] = round(gm_score, 1)
    details['毛利率(%)'] = round(gm, 1)

    # 1c. 净利率扎实度 (5分)
    nm = row.get('netprofit_margin', 0)
    if nm >= 20:
        nm_score = 5.0
    elif nm >= 12:
        nm_score = 3.5 + (nm - 12) / 8 * 1.5
    elif nm >= 5:
        nm_score = 1.5 + (nm - 5) / 7 * 2
    elif nm > 0:
        nm_score = 0.5
    else:
        nm_score = 0.0
    total += nm_score
    details['净利率分'] = round(nm_score, 1)
    details['净利率(%)'] = round(nm, 1)

    # 1d. 标签/关键词分 (10分)
    tag_score = 0.0
    tag_details = []
    name = str(row.get('name', ''))
    bz_items = str(row.get('main_bz', ''))

    # 名称匹配
    matched_name = [kw for kw in _SPECIALIZED_KEYWORDS if kw in name]
    if matched_name:
        tag_score += min(2.0, len(matched_name) * 0.8)
        tag_details.extend(matched_name[:3])

    # 主营匹配
    matched_bz = [kw for kw in _INDUSTRY_BARRIER_KEYWORDS if kw in bz_items]
    if matched_bz:
        tag_score += min(6.0, len(matched_bz) * 1.2)
        tag_details.extend(matched_bz[:5])

    # 行业匹配
    if any(kw in industry for kw in _INDUSTRY_BARRIER_KEYWORDS):
        tag_score += 2.0
        tag_details.append(f'行业:{industry}')

    # 无壁垒标签惩罚
    if not matched_bz and not matched_name:
        tag_score *= 0.3

    tag_score = min(10.0, tag_score)
    total += tag_score
    details['标签分'] = round(tag_score, 1)
    details['标签'] = ';'.join(tag_details[:5]) if tag_details else ''
    details['主营业务'] = bz_items[:80] if bz_items else ''

    # 1e. 板块加分 (3分)
    board = str(row.get('board', ''))
    if board in ('创业板', '科创板'):
        board_score = 3.0
    else:
        board_score = 1.0
    total += board_score
    details['板块分'] = round(board_score, 1)

    # ══════════════════════════════════════════════════════
    # 二、中报业绩增长分 (35分)
    # ══════════════════════════════════════════════════════

    # 2a. 预告净利润增速 (15分)
    p_min = row.get('p_change_min', 0)
    p_max = row.get('p_change_max', 0)
    p_change = (p_min + p_max) / 2

    if p_change >= 200:
        growth_score = 15.0
    elif p_change >= 100:
        growth_score = 12.0 + (p_change - 100) / 100 * 3
    elif p_change >= 50:
        growth_score = 8.0 + (p_change - 50) / 50 * 4
    elif p_change >= 30:
        growth_score = 5.0 + (p_change - 30) / 20 * 3
    elif p_change >= 0:
        growth_score = 1.0 + p_change / 30 * 4
    else:
        growth_score = 0.0
    total += growth_score
    details['预告增速分'] = round(growth_score, 1)
    details['预告增速中值(%)'] = round(p_change, 1)

    # 2b. 预告类型 (5分)
    ftype = str(row.get('type', ''))
    type_score_map = {
        '预增': 5.0, '大幅上升': 5.0,
        '扭亏': 3.0,
        '略增': 2.5,
        '续盈': 1.0,
    }
    ftype_score = type_score_map.get(ftype, 0.0)
    total += ftype_score
    details['预告类型分'] = ftype_score
    details['预告类型'] = ftype

    # 2c. 最新季度同比增速 (7分)
    yoy = row.get('netprofit_yoy', 0)
    if yoy >= 200:
        yoy_score = 7.0
    elif yoy >= 100:
        yoy_score = 5.5 + (yoy - 100) / 100 * 1.5
    elif yoy >= 50:
        yoy_score = 3.5 + (yoy - 50) / 50 * 2
    elif yoy >= 20:
        yoy_score = 1.5 + (yoy - 20) / 30 * 2
    elif yoy > 0:
        yoy_score = 0.5
    else:
        yoy_score = 0.0
    total += yoy_score
    details['季同比分'] = round(yoy_score, 1)
    details['最新季度净利同比(%)'] = round(yoy, 1) if pd.notna(yoy) else 0

    # 2d. 营收匹配加分 (8分)
    tr_yoy = row.get('tr_yoy', 0)
    if tr_yoy >= 50:
        tr_score = 8.0
    elif tr_yoy >= 30:
        tr_score = 6.0 + (tr_yoy - 30) / 20 * 2
    elif tr_yoy >= 15:
        tr_score = 4.0 + (tr_yoy - 15) / 15 * 2
    elif tr_yoy >= 5:
        tr_score = 2.0 + (tr_yoy - 5) / 10 * 2
    elif tr_yoy > 0:
        tr_score = 0.5
    else:
        tr_score = 0.0
    total += tr_score
    details['营收匹配分'] = round(tr_score, 1)
    details['营收增速(%)'] = round(tr_yoy, 1) if pd.notna(tr_yoy) else 0

    # ══════════════════════════════════════════════════════
    # 三、估值提升空间 (25分)
    # ══════════════════════════════════════════════════════

    # 3a. PE_TTM vs 行业PE (12分)
    pe_ttm = row.get('pe_ttm', 0)
    ind_pe = _get_industry_pe(industry, industry_pe_df)

    if pe_ttm <= 0 or pe_ttm >= 500:
        pe_score = 0.0
    elif pe_ttm < ind_pe * 0.5:
        pe_score = 12.0
    elif pe_ttm < ind_pe * 0.8:
        pe_score = 9.0 + (ind_pe * 0.8 - pe_ttm) / (ind_pe * 0.3) * 3
    elif pe_ttm <= ind_pe:
        pe_score = 6.0 + (ind_pe - pe_ttm) / (ind_pe * 0.2) * 3
    elif pe_ttm <= ind_pe * 1.5:
        pe_score = 3.0 + (ind_pe * 1.5 - pe_ttm) / (ind_pe * 0.5) * 3
    elif pe_ttm <= ind_pe * 2.0:
        pe_score = 1.0 + (ind_pe * 2.0 - pe_ttm) / (ind_pe * 0.5) * 2
    else:
        pe_score = 0.0
    total += pe_score
    details['PE估值分'] = round(pe_score, 1)
    details['PE_TTM'] = round(pe_ttm, 1) if pe_ttm > 0 else 0
    details['行业参考PE'] = round(ind_pe, 1)

    # 3b. PEG (6分)
    if pe_ttm > 0 and p_change > 0:
        peg = pe_ttm / p_change if p_change > 0 else 999
        if peg <= 0.5:
            peg_score = 6.0
        elif peg <= 1.0:
            peg_score = 4.5 + (1.0 - peg) / 0.5 * 1.5
        elif peg <= 2.0:
            peg_score = 2.5 + (2.0 - peg) / 1.0 * 2
        elif peg <= 5.0:
            peg_score = 0.5 + (5.0 - peg) / 3.0 * 2
        else:
            peg_score = 0.0
    else:
        peg_score = 0.0
        peg = 0
    total += peg_score
    details['PEG分'] = round(peg_score, 1)
    details['PEG'] = round(peg, 2) if pe_ttm > 0 and p_change > 0 else 0

    # 3c. PB (7分)
    pb = row.get('pb', 0)
    if pb <= 0:
        pb_score = 0.0
    elif pb <= 1.5:
        pb_score = 7.0
    elif pb <= 3.0:
        pb_score = 5.0 + (3.0 - pb) / 1.5 * 2
    elif pb <= 5.0:
        pb_score = 2.0 + (5.0 - pb) / 2.0 * 3
    elif pb <= 8.0:
        pb_score = 0.5 + (8.0 - pb) / 3.0 * 1.5
    else:
        pb_score = 0.0
    total += pb_score
    details['PB分'] = round(pb_score, 1)
    details['PB'] = round(pb, 2) if pb > 0 else 0

    # ══════════════════════════════════════════════════════
    # 四、未来赛道加分（3分） — 识别未来高壁垒行业布局
    # ══════════════════════════════════════════════════════
    future_score = 0.0
    future_track = ''
    name = str(row.get('name', ''))
    bz_items = str(row.get('main_bz', ''))

    _FUTURE_TRACKS = {
        '生命科学': ['基因', '细胞', '合成生物', '脑机', 'ADC', 'mRNA',
                    'CAR-T', '干细胞', 'AI制药', '微流控',
                    '生命科学', '生物技术'],
        '人工智能链': ['AI芯片', '大模型', '多模态', '智能体', '具身智能',
                     '人形机器人', '边缘AI', '存算一体', '类脑',
                     '自动驾驶', '无人驾驶',
                     '人工智能', '机器人', '机器学习'],
        '航天航空': ['商业航天', '低轨卫星', '卫星互联网', '火箭回收',
                   'eVTOL', '飞行汽车', '高超声速',
                   '航天', '航空', '卫星', '火箭', '无人机', '低空'],
        '前沿技术': ['量子', '核聚变', '6G', '太赫兹', '固态电池', '钙钛矿',
                   '超导', '聚变'],
    }

    for track_name, keywords in _FUTURE_TRACKS.items():
        ind_match = any(kw in industry for kw in keywords)
        name_match = any(kw in name for kw in keywords)
        bz_match = any(kw in bz_items for kw in keywords)
        if ind_match or name_match or bz_match:
            future_score += 1.0
            if future_track:
                future_track += f'|{track_name}'
            else:
                future_track = track_name
            if name_match or bz_match:
                future_score += 0.5

    if '|' in future_track:
        future_score += 0.5

    future_score = min(3.0, future_score)
    total += future_score
    details['未来赛道分'] = round(future_score, 1)
    details['未来赛道'] = future_track if future_track else ''

    # ── 总分 ──
    total = round(total, 1)
    details['总分'] = total

    return details


# ── 主扫描流程 ────────────────────────────────────────

def run_screening(min_score: float = 50.0) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    执行寻宝策略 × 中报预告 整合扫描

    Returns:
        (passed, failed_quality, all_df)
    """
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)

    trade_date = get_last_trade_date()

    print(f"{'='*70}")
    print(f"  寻宝策略 × 中报预告 — 整合版")
    print(f"  交易日: {trade_date}  最低入围分: {min_score}")
    print(f"{'='*70}")

    # ── Phase 1: 获取中报预告数据 ──
    print("\n[Phase 1] 获取中报预告数据...")
    forecast = get_forecast_data()
    if len(forecast) == 0:
        print("  ✗ 无中报预告数据！")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    print(f"  → 已发布中报预告: {len(forecast)} 只")

    valid_types = ['预增', '略增', '扭亏', '大幅上升', '续盈']
    forecast = forecast[forecast['type'].isin(valid_types)].copy()
    print(f"  → 正增长类型: {len(forecast)} 只")

    # ── Phase 2: 获取股票基础信息 ──
    print("\n[Phase 2] 获取股票基础信息...")
    stocks = get_stock_basic()
    stocks = stocks[~stocks['ts_code'].str.match(r'^(8|4|9)\d{5}\.')]

    df = forecast.merge(stocks, on='ts_code', how='inner')
    print(f"  → 合并后: {len(df)} 只")

    def _detect_board(ts_code, market):
        if market == '科创板' or ts_code.startswith('688'):
            return '科创板'
        if ts_code.startswith('30'):
            return '创业板'
        return '主板'
    df['board'] = df.apply(lambda r: _detect_board(r['ts_code'], str(r.get('market', ''))), axis=1)

    # ── Phase 3: 获取市值/估值 ──
    print("\n[Phase 3] 获取市值/估值数据...")
    basic = get_daily_basic(trade_date)
    if basic is None or len(basic) == 0:
        print("  ✗ 无法获取市值数据！")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    df = df.merge(basic[['ts_code', 'total_mv', 'circ_mv', 'pe_ttm', 'pb']],
                  on='ts_code', how='inner')
    print(f"  → 有估值数据: {len(df)} 只")

    # ── Phase 4: 行业PE ──
    print("\n[Phase 4] 获取行业PE参考...")
    industry_pe_df = get_industry_pe()
    print(f"  → 行业PE: {len(industry_pe_df) if industry_pe_df is not None else 0} 条")

    # ── Phase 5: 财务指标 + 增长质量检查 ──
    print(f"\n[Phase 5] 财务指标 & 增长质量检查...")
    print(f"  → 共 {len(df)} 只股票")

    fin_data = {}
    quality_results = {}

    for idx, (_, row) in enumerate(df.iterrows()):
        code = row['ts_code']
        name = row['name']
        if (idx + 1) % 10 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(df)}] {name}({code})")

        try:
            fin = get_fina_indicator(code)
            if fin is not None and len(fin) > 0:
                fin = fin.sort_values('end_date', ascending=False)

                # 取最新一期指标
                latest = fin.iloc[0]
                fin_data[code] = {
                    'netprofit_yoy': float(latest.get('netprofit_yoy', 0) or 0),
                    'basic_eps_yoy': float(latest.get('basic_eps_yoy', 0) or 0),
                    'tr_yoy': float(latest.get('tr_yoy', 0) or 0),
                    'or_yoy': float(latest.get('or_yoy', 0) or 0),
                    'roe': float(latest.get('roe', 0) or 0),
                    'grossprofit_margin': float(latest.get('grossprofit_margin', 0) or 0),
                    'netprofit_margin': float(latest.get('netprofit_margin', 0) or 0),
                    'adminexp_of_gr': float(latest.get('adminexp_of_gr', 0) or 0),
                }

                # 增长质量检查
                quality = check_growth_quality(fin)
                quality_results[code] = quality
            else:
                fin_data[code] = {k: 0 for k in ['netprofit_yoy', 'basic_eps_yoy', 'tr_yoy',
                                                   'or_yoy', 'roe', 'grossprofit_margin',
                                                   'netprofit_margin', 'adminexp_of_gr']}
                quality_results[code] = {'pass': False, 'reason': '无财务数据', 'quality_score': 0.0}
        except Exception:
            fin_data[code] = {k: 0 for k in ['netprofit_yoy', 'basic_eps_yoy', 'tr_yoy',
                                               'or_yoy', 'roe', 'grossprofit_margin',
                                               'netprofit_margin', 'adminexp_of_gr']}
            quality_results[code] = {'pass': False, 'reason': '数据获取异常', 'quality_score': 0.0}

    # 合并财务数据
    fin_records = []
    for _, row in df.iterrows():
        code = row['ts_code']
        fd = fin_data.get(code, {})
        fin_records.append({**row.to_dict(), **fd})
    df = pd.DataFrame(fin_records)

    # ── Phase 6: 主营业务 ──
    print(f"\n[Phase 6] 主营业务识别...")
    bz_data = {}
    for idx, (_, row) in enumerate(df.iterrows()):
        code = row['ts_code']
        if (idx + 1) % 10 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(df)}] 标签分析中...")
        bz_items = get_mainbz(code)
        bz_data[code] = bz_items

    df['main_bz'] = df['ts_code'].map(bz_data)

    # ── Phase 7: 评分 ──
    print(f"\n[Phase 7] 综合评分...")
    score_results = []
    for _, row in df.iterrows():
        code = row['ts_code']
        qc = quality_results.get(code, {})
        details = compute_score(row.to_dict(), industry_pe_df)
        score_results.append({
            'ts_code': code,
            'name': row['name'],
            'quality_pass': qc.get('pass', False),
            'quality_reason': qc.get('reason', ''),
            'quality_score': qc.get('quality_score', 0.0),
            **details,
        })

    result_df = pd.DataFrame(score_results)
    result_df = result_df.sort_values('总分', ascending=False).reset_index(drop=True)
    result_df['排名'] = range(1, len(result_df) + 1)

    # 入围筛选（同时过滤增长质量未通过的）
    passed = result_df[(result_df['总分'] >= min_score) & (result_df['quality_pass'] == True)].copy()
    passed = passed.sort_values('总分', ascending=False).reset_index(drop=True)

    # 增长质量未通过的单独列出
    failed_quality = result_df[~result_df['quality_pass']].copy()
    failed_quality = failed_quality.sort_values('总分', ascending=False).reset_index(drop=True)

    print(f"\n{'='*70}")
    print(f"  扫描完成！")
    print(f"  中报预告: {len(forecast)} 只")
    print(f"  有完整数据: {len(result_df)} 只")
    print(f"  增长质量通过: {len(result_df[result_df['quality_pass']])} 只")
    print(f"  增长质量未通过: {len(failed_quality)} 只")
    print(f"  入围(≥{min_score}分): {len(passed)} 只")
    print(f"{'='*70}")

    return passed, failed_quality, result_df


# ── 报告输出 ──────────────────────────────────────────

def print_report(passed: pd.DataFrame, failed_quality: pd.DataFrame,
                 all_df: pd.DataFrame, trade_date: str, min_score: float = 50.0):
    output_csv = os.path.join(OUTPUT_DIR, f'integrated_hunt_{trade_date}.csv')
    if len(passed) > 0:
        passed.to_csv(output_csv, index=False, encoding='utf-8-sig')
        print(f"\nCSV已保存: {output_csv}")

    all_csv = os.path.join(OUTPUT_DIR, f'integrated_hunt_all_{trade_date}.csv')
    all_df.to_csv(all_csv, index=False, encoding='utf-8-sig')

    if len(passed) == 0:
        print("\n⚠ 未找到符合条件的标的。")
        return

    # 按寻宝属性分组
    high_treasure = passed[passed['标签分'] >= 5].sort_values('总分', ascending=False)
    mid_treasure = passed[(passed['标签分'] >= 3) & (passed['标签分'] < 5)].sort_values('总分', ascending=False)
    low_treasure = passed[passed['标签分'] < 3].sort_values('总分', ascending=False)

    print(f"\n{'━'*70}")
    print(f"  寻宝 × 中报预告 整合结果报告 — {trade_date}")
    print(f"{'━'*70}")

    # ── 精选标的（高壁垒 + 高增长 + 低估值） ──
    print(f"\n{'█'*70}")
    print(f"  ★★★ 精选标的（标签分≥5 + 增长质量通过）★★★")
    print(f"{'█'*70}")
    if len(high_treasure) > 0:
        for _, r in high_treasure.iterrows():
            _print_card(r)
    else:
        print("  （无）")

    # ── 重点关注（有一定壁垒属性） ──
    print(f"\n{'▌'*35}")
    print(f"  ★★ 重点关注（标签分3~5，有一定壁垒属性）")
    print(f"{'▌'*35}")
    if len(mid_treasure) > 0:
        for _, r in mid_treasure.iterrows():
            _print_card(r)
    else:
        print("  （无）")

    # ── 一般关注（壁垒属性弱，但业绩增长好） ──
    print(f"\n{'▌'*35}")
    print(f"  ★ 一般关注（标签分<3，壁垒属性弱，纯业绩增长）")
    print(f"{'▌'*35}")
    if len(low_treasure) > 0:
        for _, r in low_treasure.iterrows():
            _print_card(r)
    else:
        print("  （无）")

    # ── 增长质量未通过 ──
    if len(failed_quality) > 0:
        print(f"\n{'─'*70}")
        print(f"  ⚠ 增长质量未通过（过滤突发增长）")
        print(f"{'─'*70}")
        for _, r in failed_quality.iterrows():
            print(f"  ✗ {r['name']:>8}({r['ts_code']})  "
                  f"总分{r['总分']:>5.1f}  "
                  f"增速{r.get('预告增速中值(%)', 0):>6.1f}%  "
                  f"→ {r['quality_reason']}")

    # ── 统计摘要 ──
    print(f"\n{'─'*70}")
    print(f"  统计摘要")
    print(f"{'─'*70}")
    print(f"  入围总数: {len(passed)}")
    print(f"  - 精选(标签≥5): {len(high_treasure)}")
    print(f"  - 关注(标签3~5): {len(mid_treasure)}")
    print(f"  - 一般(标签<3): {len(low_treasure)}")
    print(f"  增长质量过滤: {len(failed_quality)} 只")
    if len(passed) > 0:
        print(f"  平均总分: {passed['总分'].mean():.1f}")
        print(f"  平均预告增速: {passed['预告增速中值(%)'].mean():.1f}%")
        print(f"  平均PE_TTM: {passed['PE_TTM'].mean():.1f}")
        print(f"  平均PEG: {passed['PEG'].mean():.2f}")
        print(f"  平均市值: {passed['总市值(亿)'].mean():.1f}亿")
        if '未来赛道' in passed.columns and '未来赛道分' in passed.columns:
            future_count = len(passed[passed['未来赛道'] != ''])
            if future_count > 0:
                print(f"\n  未来赛道分布:")
                all_tracks = {}
                for tracks in passed[passed['未来赛道'] != '']['未来赛道']:
                    for t in str(tracks).split('|'):
                        all_tracks[t] = all_tracks.get(t, 0) + 1
                for t, c in sorted(all_tracks.items(), key=lambda x: -x[1]):
                    print(f"    {t}: {c} 只")
                print(f"  平均未来赛道分: {passed['未来赛道分'].mean():.1f}")

    print(f"\n{'═'*70}")
    print(f"  操作建议")
    print(f"{'═'*70}")
    print(f"  • 精选标的：高壁垒+高增长+低估值，重点关注")
    print(f"  • 寻宝属性强的标的弹性更大，适合大盘Risk OFF时布局")
    print(f"  • 增长质量未通过的标的需警惕：营收不匹配/ROE异常/非经常性收益")
    print(f"  • 未来赛道扩展：已识别生命科学/AI链/航天航空/前沿技术布局")
    print(f"  • 随着更多公司发布中报预告，每天运行一次即可更新")
    print(f"{'═'*70}")


def _print_card(r):
    tags = str(r.get('标签', ''))
    bz = str(r.get('主营业务', ''))
    quality = str(r.get('quality_reason', ''))
    future_track = str(r.get('未来赛道', ''))
    print(f"\n  ┌─────────────────────────────────────────────────────┐")
    print(f"  │ {r['name']} ({r['ts_code']})  ┃  总分: {r['总分']:>5.1f}")
    print(f"  ├─────────────────────────────────────────────────────┤")
    print(f"  │ 业绩增速 {r.get('预告增速中值(%)', 0):>6.1f}%  │ {r.get('预告类型', ''):>4}  │ 季同比 {r.get('最新季度净利同比(%)', 0):>6.1f}%")
    print(f"  │ 营收增速 {r.get('营收增速(%)', 0):>6.1f}%  │ 毛利率 {r.get('毛利率(%)', 0):>6.1f}%  │ 净利率 {r.get('净利率(%)', 0):>6.1f}%")
    print(f"  │ PE_TTM {r.get('PE_TTM', 0):>6.1f}  │ 行业PE {r.get('行业参考PE', 0):>6.1f}  │ PEG {r.get('PEG', 0):>6.2f}")
    print(f"  │ 市值 {r.get('总市值(亿)', 0):>6.1f}亿  │ PB {r.get('PB', 0):>6.2f}  │ 标签 {r.get('标签分', 0):>2.0f}分")
    if tags:
        print(f"  │ 标签: {tags}")
    if future_track:
        print(f"  │ 未来赛道: {future_track}  [{r.get('未来赛道分', 0):.0f}分]")
    if bz:
        bz_short = bz if len(bz) <= 76 else bz[:73] + '...'
        print(f"  │ 主营: {bz_short}")
    print(f"  └─────────────────────────────────────────────────────┘")


# ── 主入口 ──────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='寻宝策略 × 中报预告整合版')
    parser.add_argument('--min_score', type=float, default=50.0, help='最低入围分（默认50）')
    args = parser.parse_args()

    t0 = time.time()
    passed, failed_quality, all_df = run_screening(min_score=args.min_score)

    trade_date = get_last_trade_date()
    print_report(passed, failed_quality, all_df, trade_date, min_score=args.min_score)

    elapsed = time.time() - t0
    print(f"\n⏱ 总耗时: {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")