###===自选复盘 - tushare接口===###

import io
import json
import re
import urllib.parse
import os
import struct
import sys

# =========================
# Windows GBK 控制台输出修复：强制 UTF-8 编码
# =========================
#sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
#sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# =========================
# 终极方案：patch os.path.expanduser，不让 tushare 访问用户根目录
# 必须在导入 tushare 之前执行！
# =========================
original_expanduser = os.path.expanduser
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 现在可以安全导入 tushare 了
import markdown2 # type: ignore
import requests
import pandas as pd
import numpy as np
import time
import warnings
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

# =========================
# 代理配置
# =========================
PROXY = 'http://127.0.0.1:7897'
PROXY_ENABLED = True  # 设为 True 启用代理

# 创建带代理的 requests Session
def get_requests_session():
    """获取 requests Session，支持代理"""
    session = requests.Session()
    if PROXY_ENABLED:
        session.proxies = {
            'http': PROXY,
            'https': PROXY
        }
    return session

# 默认 Session
default_session = get_requests_session()

from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3

# 新增：引用新版大盘/主题分析
import daily_analysis_summarizer as das

# SQLite 统一缓存模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_cache as sc

# =========================
# 产业资金定价AI模型（ICPM）延迟导入
# =========================
_MF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'multi_factor_picker')
if _MF_DIR not in sys.path:
    sys.path.insert(0, _MF_DIR)
_ICPM_AVAILABLE = True  # 标记可用，实际 import 在用到时执行

# =========================
# 环境变量
# =========================
load_dotenv("d:/mystock/config/.env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY") or os.getenv("GLM_API_KEY")
MINI_MAX_API_KEY = os.getenv("MINI_MAX_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
DOUBAO_BASE_URL = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "ark-code-latest")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_theme_stock_map_from_json():
    """直接从 JSON 文件加载主题-个股映射。

    返回: (theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts)
    """
    theme_stock_map = {}
    name_map_basic = {}
    stock_basic_industry = {}
    stock_concepts = {}

    json_path = os.path.join(
        os.path.dirname(BASE_DIR), "cache_daily", "theme_stock_map_latest.json"
    )

    if not os.path.exists(json_path):
        return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts

    themes = data.get("themes", {}) or {}
    stocks = data.get("stocks", {}) or {}

    # 构建 theme_stock_map
    for theme_name, stock_list in themes.items():
        if not isinstance(stock_list, list):
            continue
        code_map = {}
        for item in stock_list:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if not code:
                continue
            code_map[code] = {
                "via": item.get("via", ""),
                "chain_distance": item.get("chain_distance", 0),
                "industry_match": bool(item.get("industry_match", False)),
                "score": item.get("score", 0),
            }
        theme_stock_map[theme_name] = code_map

    # 构建 name_map_basic / stock_basic_industry / stock_concepts
    for code, info in stocks.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name")
        if name:
            name_map_basic[code] = name
        industry = info.get("industry")
        if industry:
            stock_basic_industry[code] = industry
        concepts = info.get("concepts")
        if isinstance(concepts, list):
            stock_concepts[code] = concepts

    return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts


def _load_fusion_result(expected_date=None):
    """加载 FUSION 融合排名结果。

    优先读取 fusion_rank 生成的 JSON，
    若不存在则回退到 _load_v6_result() 的逻辑。

    Args:
        expected_date: 交易日(YYYYMMDD)，None时不验证

    Returns:
        list: 融合排名结果列表（按融合分降序），含 meta + data 字段
              回退时返回 _load_v6_result 的原始结果
    """
    fusion_path = None
    if expected_date:
        fusion_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache',
                                   f'theme_fusion_rank_{expected_date}.json')

    if fusion_path and os.path.exists(fusion_path):
        try:
            with open(fusion_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            meta = payload.get("meta", {})
            data = payload.get("data", payload)  # 兼容旧格式（无meta包装）
            if isinstance(data, list) and len(data) > 0:
                print(f"[FUSION] 加载融合排名: {len(data)}个主题 | "
                      f"大盘={meta.get('大盘状态','?')} | 模式={meta.get('模式','?')}")
                return data
            else:
                print(f"[FUSION] 融合排名数据异常，回退V6/V8")
        except Exception as e:
            print(f"[FUSION] 读取失败: {e}，回退V6/V8")
    else:
        print(f"[FUSION] 融合排名文件不存在，回退V6/V8")

    # 回退：使用 V6/V8 原始结果
    return _load_v6_result(expected_date)


def _load_v2_theme_scores(trade_date):
    """加载 theme_score_v2.py 输出的 CSV 评分结果。

    Args:
        trade_date: 交易日(YYYYMMDD)

    Returns:
        list[dict]: 按综合分降序排列的评分列表, None 表示不可用
    """
    v2_csv = os.path.join(BASE_DIR, 'report_daily', f'theme_scores_v2_{trade_date}.csv')
    if not os.path.exists(v2_csv):
        print(f"[V2评分] 文件不存在: {v2_csv}")
        return None

    try:
        import pandas as pd
        df = pd.read_csv(v2_csv)
        if df.empty:
            return None
        # 填充 NaN
        df = df.where(pd.notna(df), None)
        records = df.to_dict('records')
        # 从扁平列重建 migration_factors 嵌套dict
        factor_keys = ['proximity', 'momentum', 'confirmation', 'money_resonance', 'leader_health', 'regime', 'age_penalty', 'macro_filter']
        for r in records:
            mf = {}
            for k in factor_keys:
                if k in r and r[k] is not None:
                    mf[k] = float(r[k])
            r['migration_factors'] = mf
        print(f"[V2评分] 加载完成: {len(records)} 个主题 (日期 {trade_date})")
        return records
    except Exception as e:
        print(f"[V2评分] 读取失败: {e}")
        return None


def _load_v6_result(expected_date=None):
    """加载 Theme Alpha V8.0 引擎结果，并验证 trade_date 是否匹配。
    优先尝试 V8 CSV (theme_alpha_v6_result_v8_{date}.csv)，
    其次 V8 JSON，最后回退到 V6 JSON。

    Args:
        expected_date: 期望的交易日(YYYYMMDD)，None时不验证

    Returns:
        list: 结果列表，若文件不存在或日期不匹配则返回None
    """
    v8_csv_path = None
    v8_json_path = None
    if expected_date:
        v8_csv_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache',
                                    f'theme_alpha_v6_result_v8_{expected_date}.csv')
        v8_json_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache',
                                    f'theme_alpha_v6_result_v8_{expected_date}.json')

    v6_result_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache', 'theme_alpha_v6_result.json')

    load_path = None
    source = None
    if v8_csv_path and os.path.exists(v8_csv_path):
        load_path = v8_csv_path
        source = "V8_CSV"
    elif v8_json_path and os.path.exists(v8_json_path):
        load_path = v8_json_path
        source = "V8"
    elif os.path.exists(v6_result_path):
        load_path = v6_result_path
        source = "V6"
    else:
        print(f"[V8] 引擎结果不存在: {v8_csv_path} / {v8_json_path}")
        print(f"[V6] 回退文件也不存在: {v6_result_path}")
        return None

    try:
        if source == "V8_CSV":
            import csv
            data = []
            NUMERIC_FIELDS = {'排名', 'V7综合得分', '资金分', '梯队分', '趋势分', '基础分',
                              '资金_换手率Z分', '资金_自由流通市值流入比', '资金_大阳线渗透率',
                              '梯队_龙头涨幅', '梯队_龙头创新高', '梯队_龙头连板',
                              '梯队_中军数量', '梯队_中军5日涨幅', '梯队_中军破位比例',
                              '梯队_中军缩量比例', '梯队_跟风>3%比例', '梯队_跟风>5%比例',
                              '梯队_跟风上涨比例', '趋势_RSRS强度', '趋势_均线多头天数',
                              '基础_催化得分', '基础_业绩预期分', '基础_事件驱动分',
                              'T_start', 'T_MA', 'R_volume'}
            with open(load_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    for field in NUMERIC_FIELDS:
                        if field in row and row[field]:
                            try:
                                row[field] = float(row[field]) if '.' in row[field] else int(row[field])
                            except ValueError:
                                pass
                    data.append(row)
        else:
            with open(load_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
    except Exception as e:
        print(f"[{source}] 读取结果失败: {e}")
        return None

    if not data:
        print(f"[{source}] 引擎结果为空")
        return None

    # 验证 trade_date（CSV 无该列，跳过）
    if expected_date and source != "V8_CSV":
        v_date = data[0].get('trade_date', '')
        if not v_date:
            pass
        elif v_date and v_date != expected_date:
            print(f"⚠️ [{source}] 日期不匹配: 结果日期={v_date}, 期望日期={expected_date}")
            print(f"  请先运行 python main.py --date {expected_date} 生成当天结果")
            return None

    # V8/V8_CSV → V6 字段兼容映射
    if source in ("V8", "V8_CSV"):
        for r in data:
            r['theme'] = r.get('主题', '')
            r['composite_score'] = r.get('V7综合得分', r.get('V7综合得分', 0))
            r['stage'] = r.get('D阶段', r.get('V7阶段', ''))
            r['trade_signal'] = _v8_stage_to_signal(r.get('D阶段', ''), r.get('V7综合得分', 0))
            r['trend_score'] = r.get('趋势分', 0)
            r['capital_score'] = r.get('资金分', 0)
            r['forward_alpha'] = r.get('FA分', 0)
            r['sentiment_score'] = 0
            r['continuation_score'] = 0
            r['alpha_gate'] = ''
            r['leader'] = ''
            r['divergence_buy'] = ''
            r['theme_status'] = ''
            if not r.get('trade_date'):
                r['trade_date'] = expected_date or ''

    return data


def _v8_stage_to_signal(d_stage, score):
    """V8 天数阶段 → V6 信号映射"""
    if d_stage in ("D1-D2",):
        if score >= 60: return "强买"
        elif score >= 50: return "看多"
        else: return "关注"
    if d_stage in ("D3",):
        if score >= 65: return "强买"
        elif score >= 50: return "看多"
        else: return "关注"
    if d_stage in ("D4-D5",):
        if score >= 60: return "强买"
        elif score >= 45: return "看多"
        else: return "关注"
    if d_stage in ("D6-D7",):
        return "关注"
    if d_stage in ("D8+", "潜伏期", "数据不足"):
        return "中性"
    return "中性"


def _load_market_analysis_result(trade_date):
    """读取 market_analysis.py 已生成的 txt 报告（避免重复运行 analyze_market）
    
    Returns:
        (ma_txt, ma_position, ma_reason)
    """
    ma_cache_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
    txt_path = os.path.join(ma_cache_dir, f"market_analysis_{trade_date}.txt")
    
    ma_txt = ""
    ma_position = 0
    ma_reason = ""
    
    if os.path.exists(txt_path):
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                ma_txt = f.read()
            
            # 从txt中提取仓位和理由
            import re
            m_pos = re.search(r'总体仓位建议:\s*(\d+)%', ma_txt)
            if m_pos:
                ma_position = int(m_pos.group(1))
            m_reason = re.search(r'理由:\s*(.+)', ma_txt)
            if m_reason:
                ma_reason = m_reason.group(1).strip()
            
            print(f"[MarketAnalysis] 已加载 {trade_date} 大盘分析报告（仓位{ma_position}%）")
        except Exception as e:
            print(f"[MarketAnalysis] 读取txt失败: {e}")
    else:
        print(f"[MarketAnalysis] 未找到 {trade_date} 的分析报告，请先运行 market_analysis.py")
    
    return ma_txt, ma_position, ma_reason


# 缓存/报告目录统一到 d:\mystock\ 下
STOCK_DATA_DIR = r"d:\mystock"
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")   
DB_PATH = os.path.join(REPORT_DIR, "stock_result.db")
NEWS_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "news_cache")
# Tushare API 数据缓存（研报、调研）
TUSHARE_API_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "tushare_api_cache")
FUND_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_fundamental")
MONEYFLOW_STOCK_DIR = os.path.join(CACHE_DIR, "moneyflow_stock")

os.makedirs(STOCK_DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
os.makedirs(TUSHARE_API_CACHE_DIR, exist_ok=True)
os.makedirs(FUND_CACHE_DIR, exist_ok=True)
os.makedirs(MONEYFLOW_STOCK_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════
# 缓存API调用（统一入口：sc.* 详见 stock_cache.py）
# ═══════════════════════════════════════════════════════

def batch_cache_stk_factor_pro(target_date):
    """委托给 sc.batch_cache_stk_factor_pro"""
    sc.batch_cache_stk_factor_pro(target_date)

def get_list_date(ts_code):
    """委托给 sc.get_list_date"""
    return sc.get_list_date(ts_code)

def cached_stk_factor_pro(ts_code, start_date, end_date):
    """委托给 sc.cached_stk_factor_pro"""
    return sc.cached_stk_factor_pro(ts_code, start_date, end_date)

# ═══════════════════════════════════════════════════════

# =========================
# 主题个股池路径
# =========================
THEME_STOCKS_CACHE = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_pattern_stocks.csv')

# DC热榜缓存目录
DC_HOT_CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'dc_hot')


def detect_breakout(ts_code, pro, trade_date=None):
    """
    突破型策略检测函数 — 机构级算法 V2.0

    借鉴顶尖机构模型：
      - William O'Neil CANSLIM：量价配合 + 阻力位突破 + 收盘确认
      - Mark Minervini VCP (Volatility Contraction Pattern)：波动率收缩后爆发
      - Stan Weinstein Stage Analysis：均线阶段确认
      - Stanley Druckenmiller：多周期共振 + 相对强度

    总分 100 分，三态输出：
      - 有效突破 (>=70分)：已突破且高概率延续
      - 即将突破 (VCP收缩 + 接近阻力位 + 多头排列)：未突破但具备爆发条件
      - 假突破过滤：识别冲高回落 / 缩量假突破

    评分维度（100分）：
      1. 量价确认     (25分): 量比 + 5/20日均量比 + 量价同向
      2. 阻力位突破   (20分): 突破20日高点 + 突破布林上轨 + 收盘确认
      3. 趋势均线     (15分): 多头排列 + MA60方向 + close离MA5距离
      4. 动能共振     (15分): MACD + KDJ + RSI 分级加成
      5. 波动率收缩VCP(10分): 布林宽度收缩 + 5/20日波动 + ATR分位
      6. 趋势延续     (10分): 距60日高点 + 上一波强度 + MA20斜率
      7. 多周期共振   (5分):  close>MA90/MA250 + 周/月线方向

    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例（保留兼容，内部不用）
        trade_date: 指定日期（None表示最新）
    返回:
        {
            "breakout_score": int,           # 总分（兼容旧字段）
            "is_valid_breakout": bool,       # 是否有效突破 (>=70 且非假突破)
            "is_imminent_breakout": bool,    # 是否即将突破
            "is_false_breakout": bool,       # 是否假突破
            "breakout_type": str,            # 状态分类
            "signal": str,                   # 信号建议（兼容旧字段）
            "sub_scores": dict,              # 7维子分（诊断用）
            "resistance_price": float,       # 阻力位价格
            "distance_to_resistance": float, # 距阻力位百分比
            "volume_ratio": float,
            "vol_5_to_20": float,            # 5日均量/20日均量
            "boll_width_shrink": float,      # 布林宽度收缩比
            "vol_contraction": float,        # 5日/20日波动收缩比
            "atr_percentile": float,         # ATR在60日中分位
            "wave_strength": float,          # 上一波振幅
            "ma20_slope": float,             # MA20 10日斜率
        }
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "breakout_score": 0,
        "is_valid_breakout": False,
        "is_imminent_breakout": False,
        "is_false_breakout": False,
        "signal": "非突破形态",
        "breakout_type": "形态不具备",
        "sub_scores": {},
        "resistance_price": 0.0,
        "distance_to_resistance": 0.0,
        "volume_ratio": 0.0,
        "vol_5_to_20": 0.0,
        "boll_width_shrink": 0.0,
        "vol_contraction": 0.0,
        "atr_percentile": 0.0,
        "wave_strength": 0.0,
        "ma20_slope": 0.0,
    }

    try:
        end_date = str(trade_date or TRADE_DATE)
        # 取约1年数据用于：60日高点、MA250、ATR分位、布林宽度收缩
        start_date = (pd.Timestamp(end_date) - pd.Timedelta(days=400)).strftime('%Y%m%d')
        df = cached_stk_factor_pro(ts_code, start_date, end_date)

        if df is None or df.empty:
            return result

        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 定位当前交易日行索引
        target_date = str(trade_date or TRADE_DATE)
        mask = df['trade_date'] == target_date
        if not mask.any():
            return result
        idx = mask.idxmax()
        latest = df.iloc[idx]
        df_hist = df.iloc[:idx + 1].copy()  # 当前日及之前

        # ===== 提取当日数据 =====
        close = float(latest.get('close', 0) or 0)
        open_ = float(latest.get('open', 0) or 0)
        high = float(latest.get('high', 0) or 0)
        low = float(latest.get('low', 0) or 0)
        vol = float(latest.get('vol', 0) or 0)
        volume_ratio = float(latest.get('volume_ratio', 1.0) or 1.0)

        boll_upper = float(latest.get('boll_upper_bfq', 0) or 0)
        boll_mid = float(latest.get('boll_mid_bfq', 0) or 0)
        boll_lower = float(latest.get('boll_lower_bfq', 0) or 0)

        ma5 = float(latest.get('ma_bfq_5', 0) or 0)
        ma10 = float(latest.get('ma_bfq_10', 0) or 0)
        ma20 = float(latest.get('ma_bfq_20', 0) or 0)
        ma60 = float(latest.get('ma_bfq_60', 0) or 0)
        ma90 = float(latest.get('ma_bfq_90', 0) or 0)
        ma250 = float(latest.get('ma_bfq_250', 0) or 0)

        macd = float(latest.get('macd_bfq', 0) or 0)
        dif = float(latest.get('macd_dif_bfq', 0) or 0)
        dea = float(latest.get('macd_dea_bfq', 0) or 0)
        kdj_j = float(latest.get('kdj_bfq', 50) or 50)
        rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
        atr = float(latest.get('atr_bfq', 0) or 0)

        if close <= 0:
            return result

        # ===== 历史统计量 =====
        n = len(df_hist)
        # 20日高点（不含当日，作为阻力位）
        high_20_prev = df_hist['high'].iloc[:-1].tail(20).max() if n >= 21 else (df_hist['high'].iloc[:-1].max() if n > 1 else close)
        # 60日高点（含当日）
        high_60 = df_hist['high'].tail(60).max() if n >= 60 else df_hist['high'].max()

        # 阻力位 = max(前20日高点, 布林上轨)
        resistance_price = max(high_20_prev, boll_upper) if boll_upper > 0 else high_20_prev
        distance_to_resistance = (close - resistance_price) / resistance_price * 100 if resistance_price > 0 else 0.0

        # 均量比
        vol_5_avg = df_hist['vol'].tail(5).mean() if n >= 5 else vol
        vol_20_avg = df_hist['vol'].tail(20).mean() if n >= 20 else vol
        vol_50_avg = df_hist['vol'].tail(50).mean() if n >= 50 else vol_20_avg
        vol_5_to_20 = vol_5_avg / vol_20_avg if vol_20_avg > 0 else 1.0

        # 5日/20日波动率（VCP核心：5日波动应小于20日波动）
        if n >= 20:
            df_20d = df_hist.tail(20)
            daily_range_5 = df_20d.tail(5).apply(lambda r: (r['high'] - r['low']) / max(r['close'], 0.01), axis=1).mean()
            daily_range_20 = df_20d.apply(lambda r: (r['high'] - r['low']) / max(r['close'], 0.01), axis=1).mean()
            vol_contraction = daily_range_20 / daily_range_5 if daily_range_5 > 0 else 1.0
        else:
            vol_contraction = 1.0

        # 布林带宽度收缩（当前 / 20天前）
        if boll_upper > 0 and boll_lower > 0 and boll_mid > 0:
            boll_width_now = (boll_upper - boll_lower) / boll_mid
            if idx >= 20:
                prev_row = df_hist.iloc[idx - 20]
                prev_bu = float(prev_row.get('boll_upper_bfq', 0) or 0)
                prev_bl = float(prev_row.get('boll_lower_bfq', 0) or 0)
                prev_bm = float(prev_row.get('boll_mid_bfq', 0) or 0)
                if prev_bu > 0 and prev_bl > 0 and prev_bm > 0:
                    boll_width_prev = (prev_bu - prev_bl) / prev_bm
                    boll_width_shrink = boll_width_now / boll_width_prev if boll_width_prev > 0 else 1.0
                else:
                    boll_width_shrink = 1.0
            else:
                boll_width_shrink = 1.0
        else:
            boll_width_shrink = 1.0

        # ATR在60日的分位（低位=蓄势）
        if n >= 60 and atr > 0:
            atr_series = df_hist['atr_bfq'].tail(60).dropna()
            atr_percentile = (atr_series < atr).sum() / len(atr_series) if len(atr_series) > 0 else 0.5
        else:
            atr_percentile = 0.5

        # MA20 斜率（10日变化百分比）
        if idx >= 10 and ma20 > 0:
            prev_ma20 = float(df_hist.iloc[idx - 10].get('ma_bfq_20', 0) or 0)
            ma20_slope = (ma20 - prev_ma20) / prev_ma20 * 100 if prev_ma20 > 0 else 0.0
        else:
            ma20_slope = 0.0

        # 上一波强度（20日振幅）
        if n >= 20:
            wave_high = df_hist['high'].tail(20).max()
            wave_low = df_hist['low'].tail(20).min()
            wave_strength = (wave_high - wave_low) / wave_low * 100 if wave_low > 0 else 0.0
        else:
            wave_strength = 0.0

        # 多头排列计数
        alignment_count = 0
        if ma5 > 0 and ma10 > 0 and ma5 > ma10: alignment_count += 1
        if ma10 > 0 and ma20 > 0 and ma10 > ma20: alignment_count += 1
        if ma20 > 0 and ma60 > 0 and ma20 > ma60: alignment_count += 1
        if ma5 > 0 and close > ma5: alignment_count += 1

        # ===== 1. 量价确认 (25分) =====
        score_vp = 0
        # 1a 量比 (10分) - 渐进式
        if volume_ratio >= 2.0: score_vp += 10
        elif volume_ratio >= 1.5: score_vp += 8
        elif volume_ratio >= 1.2: score_vp += 5
        elif volume_ratio >= 1.0: score_vp += 3
        # 1b 5/20日均量比 (8分) - 趋势性放量
        if vol_5_to_20 >= 1.5: score_vp += 8
        elif vol_5_to_20 >= 1.2: score_vp += 5
        elif vol_5_to_20 >= 1.0: score_vp += 3
        elif vol_5_to_20 < 0.7: score_vp -= 2
        # 1c 量价同向 (7分) - 涨时放量
        if n >= 5:
            df_5d = df_hist.tail(5)
            up_vol = df_5d[df_5d['pct_chg'] > 0]['vol'].mean()
            dn_vol = df_5d[df_5d['pct_chg'] < 0]['vol'].mean()
            if up_vol > 0 and dn_vol > 0 and dn_vol > 0:
                ratio = up_vol / dn_vol
                if ratio >= 1.5: score_vp += 7
                elif ratio >= 1.2: score_vp += 4
                elif ratio >= 1.0: score_vp += 2
                else: score_vp -= 2
            elif up_vol > 0:
                score_vp += 5
        score_vp = max(0, min(25, score_vp))

        # ===== 2. 阻力位突破强度 (20分) =====
        score_rb = 0
        # 2a 突破20日高点 (10分) - 渐进式
        if high_20_prev > 0:
            bk_pct = (close - high_20_prev) / high_20_prev * 100
            if bk_pct >= 3: score_rb += 10
            elif bk_pct >= 1: score_rb += 7
            elif bk_pct >= 0: score_rb += 5
            elif bk_pct >= -1: score_rb += 3
        # 2b 突破布林上轨 (5分)
        if boll_upper > 0:
            bb_pct = (close - boll_upper) / boll_upper * 100
            if bb_pct >= 1: score_rb += 5
            elif bb_pct >= 0: score_rb += 4
            elif bb_pct >= -1: score_rb += 2
        # 2c 收盘价确认 (5分) - 真突破要求收盘站在阻力位上方
        if resistance_price > 0:
            if close > resistance_price: score_rb += 5
            elif (close + open_) / 2 > resistance_price: score_rb += 2
        score_rb = max(0, min(20, score_rb))

        # ===== 3. 趋势均线排列 (15分) =====
        score_ta = 0
        # 3a 多头排列 (8分)
        score_ta += int(alignment_count / 4 * 8)
        # 3b MA60方向 (4分)
        if ma60 > 0 and idx >= 10:
            prev_ma60 = float(df_hist.iloc[idx - 10].get('ma_bfq_60', 0) or 0)
            if prev_ma60 > 0:
                if ma60 > prev_ma60: score_ta += 4
                elif ma60 >= prev_ma60 * 0.99: score_ta += 2
        # 3c close离MA5距离 (3分) - 不能太远（追涨风险）
        if ma5 > 0:
            d = (close - ma5) / ma5 * 100
            if 0 <= d <= 5: score_ta += 3
            elif 5 < d <= 10: score_ta += 1
            elif -2 <= d < 0: score_ta += 2
        score_ta = max(0, min(15, score_ta))

        # ===== 4. 动能共振 (15分) =====
        score_mr = 0
        # 4a MACD (6分)
        if macd > 0 and dif > dea: score_mr += 6
        elif macd > 0: score_mr += 4
        elif dif > dea: score_mr += 2
        # 4b KDJ J值 (5分)
        if kdj_j > 60: score_mr += 5
        elif kdj_j > 40: score_mr += 3
        elif kdj_j > 20: score_mr += 1
        # 4c RSI (4分)
        if 60 <= rsi_6 <= 80: score_mr += 4
        elif 50 <= rsi_6 < 60: score_mr += 2
        elif rsi_6 > 80: score_mr += 1
        score_mr = max(0, min(15, score_mr))

        # ===== 5. 波动率收缩 VCP (10分) =====
        score_vc = 0
        # 5a 布林宽度收缩 (4分)
        if boll_width_shrink < 0.6: score_vc += 4
        elif boll_width_shrink < 0.8: score_vc += 3
        elif boll_width_shrink < 1.0: score_vc += 2
        elif boll_width_shrink < 1.2: score_vc += 1
        # 5b 5/20日波动收缩 (3分)
        if vol_contraction >= 1.5: score_vc += 3
        elif vol_contraction >= 1.2: score_vc += 2
        elif vol_contraction >= 1.0: score_vc += 1
        # 5c ATR分位 (3分) - ATR低位=蓄势
        if atr_percentile < 0.3: score_vc += 3
        elif atr_percentile < 0.5: score_vc += 2
        elif atr_percentile < 0.7: score_vc += 1
        score_vc = max(0, min(10, score_vc))

        # ===== 6. 趋势延续 (10分) =====
        score_tc = 0
        # 6a 距60日高点距离 (4分) - Minervini: 25%以内
        if high_60 > 0:
            dist60 = (high_60 - close) / high_60 * 100
            if dist60 <= 5: score_tc += 4
            elif dist60 <= 15: score_tc += 3
            elif dist60 <= 25: score_tc += 2
            elif dist60 <= 40: score_tc += 1
        # 6b 上一波强度 (3分)
        if wave_strength >= 50: score_tc += 3
        elif wave_strength >= 30: score_tc += 2
        elif wave_strength >= 15: score_tc += 1
        # 6c MA20斜率 (3分)
        if ma20_slope > 2: score_tc += 3
        elif ma20_slope > 0.5: score_tc += 2
        elif ma20_slope > 0: score_tc += 1
        elif ma20_slope < -1: score_tc -= 1
        score_tc = max(0, min(10, score_tc))

        # ===== 7. 多周期共振 (5分) =====
        score_mt = 0
        if ma60 > 0 and close > ma60: score_mt += 2
        if ma90 > 0 and close > ma90: score_mt += 1
        if n >= 5:
            close_5d_ago = float(df_hist.iloc[idx - 4].get('close', 0) or 0)
            if close_5d_ago > 0 and close > close_5d_ago: score_mt += 1
        if n >= 20:
            close_20d_ago = float(df_hist.iloc[idx - 19].get('close', 0) or 0)
            if close_20d_ago > 0 and close > close_20d_ago: score_mt += 1
        score_mt = max(0, min(5, score_mt))

        # ===== 总分 =====
        total_score = int(min(100, score_vp + score_rb + score_ta + score_mr + score_vc + score_tc + score_mt))

        # ===== 假突破检测（仅在真正尝试突破时触发）=====
        is_false_breakout = False
        false_breakout_penalty = 0
        # 1. 冲高回落：当日高点突破布林上轨，但收盘回落到布林上轨下方2%以内
        #    （收盘距布林上轨超过2%说明根本没形成突破尝试，不视为假突破）
        if high > boll_upper > 0 and 0 <= (boll_upper - close) / boll_upper * 100 <= 2:
            is_false_breakout = True
            false_breakout_penalty += 20
        # 2. 缩量假突破：close 突破20日高点但量比 < 1.0
        if close > high_20_prev > 0 and volume_ratio < 1.0:
            is_false_breakout = True
            false_breakout_penalty += 15
        # 3. 长上影收阴：冲高回落幅度 > 70% 且 close 接近突破位（≥ 阻力位 97%）
        if (high > low and (high - close) / (high - low) > 0.7
                and close < open_ and resistance_price > 0
                and close >= resistance_price * 0.97):
            is_false_breakout = True
            false_breakout_penalty += 15

        # 假突破扣分：确保假突破评分远离75分阈值
        if is_false_breakout:
            total_score = max(0, total_score - false_breakout_penalty)

        # ===== 即将突破检测 =====
        # 条件：未真正突破 + VCP收缩明显 + 接近阻力位 + 多头排列基本成型
        is_imminent_breakout = False
        if not is_false_breakout:
            if close <= resistance_price * 1.01:
                if score_vc >= 6:
                    if distance_to_resistance >= -3:
                        if alignment_count >= 3:
                            is_imminent_breakout = True

        # ===== 状态判断 =====
        is_valid_breakout = (total_score >= 70 and not is_false_breakout)

        if is_false_breakout:
            breakout_type = "假突破"
            signal = "假突破预警，警惕回落"
        elif is_valid_breakout:
            breakout_type = "有效突破"
            signal = "有效突破！列入观察/买入名单"
        elif is_imminent_breakout:
            breakout_type = "即将突破"
            signal = "即将突破，关注次日量能"
        else:
            breakout_type = "形态不具备"
            signal = "非突破形态"

        result.update({
            "breakout_score": total_score,
            "is_valid_breakout": is_valid_breakout,
            "is_imminent_breakout": is_imminent_breakout,
            "is_false_breakout": is_false_breakout,
            "breakout_type": breakout_type,
            "signal": signal,
            "sub_scores": {
                "量价确认": score_vp,
                "阻力突破": score_rb,
                "趋势均线": score_ta,
                "动能共振": score_mr,
                "波动收缩": score_vc,
                "趋势延续": score_tc,
                "多周期": score_mt,
            },
            "resistance_price": round(resistance_price, 2),
            "distance_to_resistance": round(distance_to_resistance, 2),
            "volume_ratio": round(volume_ratio, 2),
            "vol_5_to_20": round(vol_5_to_20, 2),
            "boll_width_shrink": round(boll_width_shrink, 2),
            "vol_contraction": round(vol_contraction, 2),
            "atr_percentile": round(atr_percentile, 2),
            "wave_strength": round(wave_strength, 2),
            "ma20_slope": round(ma20_slope, 2),
        })

    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════════════════
# 二波形态检测（基于 wave2_pattern_scanner.py 的 WavePatternDetector）
# ═══════════════════════════════════════════════════════════════
_WAVE2_DETECTOR = None

def _get_wave2_detector():
    """延迟获取 WavePatternDetector 实例（避免启动时导入开销）"""
    global _WAVE2_DETECTOR
    if _WAVE2_DETECTOR is None:
        from wave2_pattern_scanner import WavePatternDetector
        _WAVE2_DETECTOR = WavePatternDetector(force_date=TRADE_DATE)
    return _WAVE2_DETECTOR


def detect_wave2_reversal(ts_code, pro, trade_date=None, lookback_days=20):
    """二波反转策略检测（复用 WavePatternDetector v2.9）
    
    返回格式与旧版兼容，便于无缝替换：
    {
        "ts_code": str,
        "trade_date": str,
        "wave2_score": int,       # 共振总评分
        "pattern_score": int,     # 形态基础分（保持兼容，=0）
        "resonance_score": int,   # 共振加分（= wave2_score）
        "is_perfect_signal": bool,
        "signal": str,
        "pattern": str,           # 形态类型
        "score_details": str,     # 评分明细
        "entry_price": float,
        "stop_loss": float,
        "target": float,
    }
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "wave2_score": 0,
        "pattern_score": 0,
        "resonance_score": 0,
        "is_perfect_signal": False,
        "signal": "非二波形态",
        "pattern": "",
        "score_details": "",
        "entry_price": 0,
        "stop_loss": 0,
        "target": 0,
    }
    
    try:
        detector = _get_wave2_detector()
        best = None
        
        # 四种形态并列检测，取评分最高的
        # today_only=False：检测当前是否处于二波形态中（不仅限今日刚出现）
        for detect_fn in [
            detector.detect_vshape_pattern,
            detector.detect_deep_pullback_pattern,
            detector.detect_volume_pullback_pattern,
            detector.detect_sideways_pattern,
        ]:
            r = detect_fn(ts_code, today_only=False)
            if r and (best is None or r['score'] > best['score']):
                best = r
        
        if best is None:
            return result
        
        score = int(best['score'])
        pattern = best['pattern']
        
        result["wave2_score"] = score
        result["resonance_score"] = score
        result["pattern"] = pattern
        result["score_details"] = best.get('score_details', '')
        result["entry_price"] = best.get('entry_price', 0)
        result["stop_loss"] = best.get('stop_loss', 0)
        result["target"] = best.get('target', 0)

        # 按形态给出持有天数建议（基于tdx_backtest回测优化结果）
        #   V型急跌: 5日持有最优（胜率59.6%/均收益2.91%）
        #   深度回调: 20日持有最优（胜率70.9%/均收益10.89%）
        #   放量回调: 5日持有最优（胜率64.5%/均收益5.34%/盈亏比2.21）
        #   强势横盘: 5-10日持有（低吸短线）
        _pattern_hold_sell = {
            'V型急跌':   {'hold_days': 5,  'sell_signal': '5日内收益>0可卖出，放量突破MA20可持有到10日，量比回升>0.8则出场'},
            '深度回调':   {'hold_days': 20, 'sell_signal': '20日内收益>10%分批止盈，站上所有均线中线持有，缩量滞涨卖出'},
            '放量回调':   {'hold_days': 5,  'sell_signal': '5日内量比再次放大可继续持有，缩量滞涨即卖出，跌破入场价-5%止损'},
            '强势横盘':   {'hold_days': 8,  'sell_signal': '跌破MA20或放量滞涨即卖出，5日不破MA20可持有到10日'},
        }
        _hs = _pattern_hold_sell.get(pattern, {'hold_days': 5, 'sell_signal': '动态止损，按ATR跟踪'})
        result["hold_days"] = _hs['hold_days']
        result["sell_signal"] = _hs['sell_signal']
        
        # 信号等级（与旧版阈值对齐：>=18完美, >=12跟踪, >=7疑似）
        if score >= 18:
            result["is_perfect_signal"] = True
            result["signal"] = "完美二波反转！可潜伏买入"
        elif score >= 12:
            result["signal"] = "二波形态初现，继续跟踪"
        elif score >= 7:
            result["signal"] = "疑似二波结构，等待确认"
        else:
            result["signal"] = "非二波形态"
        
    except Exception:
        pass
    
    return result


def north_moneyflow_score(ts_code, pro):
    """
    北向资金共振因子（0-20分）
    使用 hk_hold（沪深港通持股）和 moneyflow（个股资金流向）数据
    """

    score = 0
    detail = {}

    try:
        # =========================
        # 1. 大单资金净流入（短期，个股资金流向 proxy）
        # =========================
        flow = _df_moneyflow_by_code(ts_code)

        if flow is not None and len(flow) > 0:
            recent = flow.head(5)

            # 大单+特大单净流入作为机构资金代理
            net_amount = recent["net_mf_amount"].sum()

            # 标准化评分
            if net_amount > 1e6:       # >100万
                s1 = 10
            elif net_amount > 5e5:      # >50万
                s1 = 7
            elif net_amount > 0:
                s1 = 4
            else:
                s1 = 0

            score += s1
            detail["moneyflow_score"] = s1
            detail["net_mf_amount_5d"] = round(net_amount, 2)

        # =========================
        # 2. 趋势一致性（持续净流入天数）
        # =========================
        if flow is not None and len(flow) > 0:
            last_5 = flow.head(5)
            positive_days = (last_5["net_mf_amount"] > 0).sum()

            s2 = positive_days / 5 * 5  # 0-5分
            score += s2
            detail["moneyflow_consistency"] = s2

        # =========================
        # 3. 北向持仓变化（结构性增持）
        # =========================
        # DataFetcher.get_hk_hold_by_code 返回 dict（仅最新值），无法计算 delta，按持股状态给分
        hold_info = _df_hk_hold_by_code(ts_code)

        if hold_info and hold_info.get('vol', 0) > 0:
            s3 = 3  # 有北向持仓（无法判断增减趋势，给中性偏正分）
            score += s3
            detail["north_position_change"] = s3

    except:
        detail["north_error"] = 0

    return min(score, 20), detail


def get_hot_list_best_rank_bonus(ts_code, days=60):
    """获取股票在热榜中的最佳排名并返回加分
    
    加分规则：
    1. 排名加分（基于最佳排名）：
       - Top10: +12分
       - Top20: +10分
       - Top30: +8分
       - Top50: +6分
       - Top100: +4分
       - 未进Top100: +0分
    
    2. 出现次数加分（基于60天内上榜次数）：
       - 1次: +0分
       - 2-3次: +2分
       - 4-5次: +4分
       - 6-10次: +6分
       - 11次以上: +8分
    
    总加分 = 排名加分 + 出现次数加分
    
    Args:
        ts_code: 股票代码
        days: 统计天数，默认60天
    
    Returns:
        bonus: 总加分
        best_rank: 最佳排名（未进榜返回9999）
        appear_count: 出现次数
    """
    best_rank = 9999
    appear_count = 0
    
    if not os.path.exists(DC_HOT_CACHE_DIR):
        return 0, best_rank, appear_count
    
    from datetime import datetime, timedelta
    
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        csv_path = os.path.join(DC_HOT_CACHE_DIR, f'dc_hot_{date_str}.csv')
        
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                match = df[df['ts_code'] == ts_code]
                if not match.empty:
                    appear_count += 1
                    rank = match.iloc[0].get('hot_rank', match.iloc[0].get('rank', 9999))
                    if pd.notna(rank) and int(rank) < best_rank:
                        best_rank = int(rank)
            except Exception:
                pass
    
    # 1. 排名加分
    if best_rank <= 10:
        rank_bonus = 12
    elif best_rank <= 20:
        rank_bonus = 10
    elif best_rank <= 30:
        rank_bonus = 8
    elif best_rank <= 50:
        rank_bonus = 6
    elif best_rank <= 100:
        rank_bonus = 4
    else:
        rank_bonus = 0
    
    # 2. 出现次数加分
    if appear_count <= 1:
        count_bonus = 0
    elif appear_count <= 3:
        count_bonus = 2
    elif appear_count <= 5:
        count_bonus = 4
    elif appear_count <= 10:
        count_bonus = 6
    else:
        count_bonus = 8
    
    # 总加分
    bonus = rank_bonus + count_bonus
    
    return bonus, best_rank, appear_count


# =========================
# Tushare
# =========================
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

# 尝试安全设置 tushare token
pro = None
try:
    pro = ts.pro_api(TUSHARE_TOKEN)
except Exception as e:
    print(f"Token 设置失败: {e}")
    print("请正确配置 TUSHARE_TOKEN 后重新运行。")
    import sys
    sys.exit(1)

if pro is None:
    print("Tushare API 未初始化，请配置 Token 后重新运行。")
    import sys
    sys.exit(1)


# =========================
# DataFetcher 统一缓存（高频接口走 DataFetcher，低频保留 pro 直调）
# =========================
try:
    from data_fetcher import DataFetcher
except Exception:
    DataFetcher = None

_df_singleton = None
def _get_df():
    """获取 DataFetcher 单例（不可用则返回 None，调用方降级到 pro）"""
    global _df_singleton
    if _df_singleton is not None:
        return _df_singleton
    try:
        if DataFetcher is None:
            return None
        token = os.getenv("TUSHARE_TOKEN") or TUSHARE_TOKEN
        if not token:
            return None
        config = {
            'cache': {'enabled': True, 'dir': os.path.join(BASE_DIR, 'multi_factor_picker', 'cache'), 'expire_hours': 168},
            'tushare': {'max_retry': 3, 'retry_delay': 5}
        }
        _df_singleton = DataFetcher(token, config)
    except Exception:
        return None
    return _df_singleton

def _df_daily_by_code(ts_code, start_date=None, end_date=None, fields=None):
    """pro.daily(ts_code=...) 的 DataFetcher 优先版（按股票查日线）"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_daily_by_code(ts_code, start_date=start_date, end_date=end_date, fields=fields)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    kw = {'ts_code': ts_code}
    if start_date is not None: kw['start_date'] = start_date
    if end_date is not None: kw['end_date'] = end_date
    if fields is not None: kw['fields'] = fields
    return pro.daily(**kw)

def _df_daily_by_date(trade_date):
    """pro.daily(trade_date=...) 的 DataFetcher 优先版（按日期查全市场）"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_daily(trade_date)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    return pro.daily(trade_date=trade_date)


def _df_daily_basic_by_date(trade_date, fields=None):
    """pro.daily_basic(trade_date=...) 的 DataFetcher 优先版（按日期查全市场）"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_daily_basic(trade_date)
            if r is not None and len(r) > 0:
                if fields:
                    cols = [c.strip() for c in fields.split(',') if c.strip() in r.columns]
                    if cols:
                        r = r[cols]
                return r
        except Exception:
            pass
    kw = {'trade_date': trade_date}
    if fields: kw['fields'] = fields
    return pro.daily_basic(**kw)

def _df_stock_list(list_status='L'):
    """pro.stock_basic(list_status=...) 的 DataFetcher 优先版（全市场股票列表）"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_stock_list(list_status=list_status)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    return pro.stock_basic(exchange='', list_status=list_status)

def _df_trade_cal(start_date=None, end_date=None):
    """pro.trade_cal(...) 的 DataFetcher 优先版（交易日历）"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_trade_cal(start_date=start_date, end_date=end_date)
            if r is not None:
                return r
        except Exception:
            pass
    kw = {'exchange': ''}
    if start_date is not None: kw['start_date'] = start_date
    if end_date is not None: kw['end_date'] = end_date
    return pro.trade_cal(**kw)

def _df_hk_hold_by_code(ts_code):
    """pro.hk_hold(ts_code=...) 的 DataFetcher 优先版（返回 dict：最新持股）"""
    _df = _get_df()
    if _df is not None:
        try:
            return _df.get_hk_hold_by_code(ts_code)
        except Exception:
            pass
    return None

def _df_moneyflow_by_code(ts_code, start_date=None, end_date=None):
    """pro.moneyflow(ts_code=...) 的 DataFetcher 优先版"""
    _df = _get_df()
    if _df is not None:
        try:
            r = _df.get_moneyflow_by_code(ts_code, start_date=start_date, end_date=end_date)
            if r is not None and len(r) > 0:
                return r
        except Exception:
            pass
    return pro.moneyflow(ts_code=ts_code)


if not os.path.exists(REPORT_DIR):
    os.makedirs(REPORT_DIR)

# =========================
# 全局换手率缓存（当日批量加载）
# =========================
TURNOVER_CACHE = {}  # {ts_code: turnover_rate}

def load_turnover_cache():
    """批量加载当日换手率到缓存（从daily_basic表）"""
    global TURNOVER_CACHE
    if pro is None:
        return
    cache_file = os.path.join(CACHE_DIR, f"turnover_rate_{TRADE_DATE}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            TURNOVER_CACHE = dict(zip(df['ts_code'], df['turnover_rate']))
            print(f"[缓存] 换手率已加载: {len(TURNOVER_CACHE)} 只")
            return
        except Exception:
            pass
    # 没有缓存则从API批量拉取
    try:
        df = _df_daily_basic_by_date(
            TRADE_DATE,
            fields='ts_code,turnover_rate'
        )
        if df is not None and not df.empty:
            TURNOVER_CACHE = dict(zip(df['ts_code'], df['turnover_rate']))
            df.to_csv(cache_file, index=False)
            print(f"[缓存] 换手率已保存: {cache_file}")
    except Exception as e:
        print(f"[缓存] 换手率加载失败: {e}")

def get_cached_turnover(ts_code):
    """从缓存获取换手率（单位：%）"""
    return TURNOVER_CACHE.get(ts_code, 0.0)

# =========================
# 通达信目录（修改成你的）
# =========================
TDX_DIR = r"C:\new_tdx"

import pdfkit # type: ignore

WK_PATH = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"

config = pdfkit.configuration(wkhtmltopdf=WK_PATH)

# =========================
# 最近交易日
# =========================
# =========================
# 获取最近交易日
# =========================

def get_last_trade_date():
    """获取最近的交易日"""

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 如果没有 tushare，根据当前时间计算交易日
    # =========================
    if pro is None:
        # 简单处理：跳过周末
        from datetime import date
        d = date.today()
        if d.weekday() == 5:  # 周六
            d = d - timedelta(days=1)
        elif d.weekday() == 6:  # 周日
            d = d - timedelta(days=2)
        return d.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = _df_trade_cal(
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


def validate_trade_date(date_str):
    """验证日期是否为有效交易日，如果不是则返回最近的有效交易日"""
    if pro is None:
        return date_str
    
    try:
        # 获取交易日历
        cal = _df_trade_cal(
            start_date=date_str,
            end_date=date_str
        )
        
        # 如果当天是交易日
        if not cal.empty and cal.iloc[0]['is_open'] == 1:
            return date_str
        
        # 如果不是交易日，找之前最近的交易日
        cal = _df_trade_cal(
            start_date=(datetime.strptime(date_str, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d'),
            end_date=date_str
        )
        cal = cal[cal['is_open'] == 1]
        last_valid = cal[cal['cal_date'] <= date_str]['cal_date'].max()
        if last_valid:
            print(f"[警告] {date_str} 不是交易日，使用最近交易日: {last_valid}")
            return str(last_valid)
        return date_str
    except Exception as e:
        print(f"[警告] 日期验证失败: {e}，使用原日期: {date_str}")
        return date_str


# 全局交易日变量
TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260529" # for test

print("当前交易日1:", TRADE_DATE)

# 启动时加载换手率缓存
load_turnover_cache()

# =========================
# BARSLAST
# =========================
def barslast(series):
    """向量化版本：找到最近True的距离
    
    特殊规则：如果第1天是True，不更新last_true，后续False返回NaN
    """
    arr = series.values.astype(bool)
    n = len(arr)
    
    # 找到所有True的位置
    true_positions = np.where(arr)[0]
    
    if len(true_positions) == 0:
        return pd.Series([np.nan] * n, index=series.index)
    
    # 特殊处理：如果第1天是True，需要跳过它（不作为有效信号）
    start_idx = 0
    if arr[0]:
        # 第1天True，跳过它，从后续找有效True
        valid_positions = true_positions[true_positions > 0]
        if len(valid_positions) == 0:
            # 只有第1天是True，全部返回NaN
            return pd.Series([np.nan] * n, index=series.index)
        true_positions = valid_positions
        start_idx = 1  # 第1天返回NaN
    
    # 使用searchsorted快速定位每个位置最近的True
    indices = np.arange(n)
    idx = np.searchsorted(true_positions, indices, side='right') - 1
    
    # 计算距离
    result = np.where(idx >= 0, indices - true_positions[idx], np.nan)
    
    # 第1天符合条件返回NaN
    if start_idx == 1:
        result[0] = np.nan
    
    # True的位置返回0
    for pos in true_positions:
        result[pos] = 0
    
    return pd.Series(result, index=series.index)


def load_stock_dict():
    """使用tushare获取股票代码和名称映射"""
    global STOCK_DICT
    try:
        if pro is not None:
            df = _df_stock_list(list_status='L')
            stock_dict = {}
            for _, row in df.iterrows():
                # 同时存储带后缀和不带后缀的代码
                stock_dict[str(row['symbol'])] = row['name']
                stock_dict[str(row['ts_code'])] = row['name']
            STOCK_DICT = stock_dict
            return stock_dict
    except Exception as e:
        print(f"[警告] tushare股票字典获取失败: {e}")
    
    # 兜底：使用内置基础字典
    STOCK_DICT = {
        '000001': '平安银行', '600000': '浦发银行', '000002': '万科A',
        '600519': '贵州茅台', '300750': '宁德时代', '000001.SZ': '平安银行',
        '600000.SH': '浦发银行', '000002.SZ': '万科A', '600519.SH': '贵州茅台',
        '300750.SZ': '宁德时代'
    }
    return STOCK_DICT

STOCK_DICT = load_stock_dict()

# =========================
# 股票名（简单版）
# =========================
def get_stock_name(code):

    return STOCK_DICT.get(code, code)


# ======================================================
# 获取全部股票
# ======================================================


# ======================================================
# 全市场daily缓存更新（机构级）
# ======================================================
# ======================================================
# 全市场daily缓存（机构级最终版）
# ======================================================
# =========================
# 缓存历史数据
# =========================
def _get_daily_from_sqlite(ts_code, start_date='20250101', end_date=None):
    """从 SQLite daily_cache 表读取单股日线数据（替代 {ts_code}.csv 读取）

    供 get_news_sentiment / 价格壁垒评分 / 成长弹性评分 / 筹码分析等场景统一调用。
    返回 DataFrame（含 trade_date/close/open/high/low/vol/amount/pct_chg 等字段）或 None。
    """
    if end_date is None:
        end_date = TRADE_DATE
    try:
        from stock_cache import get_daily_cache
        df = get_daily_cache(ts_code, start_date, end_date)
        if df is not None and not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
            return df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    return None


def get_hist_data(ts_code):
    """获取单股历史日线数据（V2: 统一从 SQLite daily_cache 读取，废弃 CSV）"""

    # =========================
    # 优先读取 SQLite daily_cache
    # =========================
    try:
        from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
        _, max_date = get_daily_cache_range(ts_code)
        if max_date is not None and str(max_date) >= TRADE_DATE:
            df = get_daily_cache(ts_code, '20250101', TRADE_DATE)
            if df is not None and not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                return df[df['trade_date'] <= TRADE_DATE].sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f"{ts_code} SQLite缓存读取失败: {e}")

    # =========================
    # 缓存缺失 → 重新拉取日线并写入 SQLite daily_cache
    # =========================
    try:
        df = _df_daily_by_code(ts_code, start_date='20250101', end_date=TRADE_DATE)
        if df is None or df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)
        try:
            batch_insert_daily_cache(df)
        except Exception:
            pass
        time.sleep(0.15)
    except Exception as e:
        print(f"{ts_code} 下载失败:", e)
        return None

    return df


# ======================================================
# 强势股池优化：时点特征计算辅助函数
# 回测验证：这些特征对强势股池胜率有显著区分度
# ======================================================
def _calc_vol_ratio(df):
    """计算当日量比 = 当日成交量 / 近5日平均成交量（不含当日）"""
    try:
        if df is None or len(df) < 6 or 'vol' not in df.columns:
            return 0
        today_vol = float(df['vol'].iloc[-1])
        avg_5d = float(df['vol'].iloc[-6:-1].mean())
        if avg_5d > 0:
            return round(today_vol / avg_5d, 2)
        return 0
    except:
        return 0


def _calc_dist_ma(df, window=5):
    """计算当日收盘价距MA距离（百分比）"""
    try:
        if df is None or len(df) < window + 1 or 'close' not in df.columns:
            return 0
        today_close = float(df['close'].iloc[-1])
        ma_value = float(df['close'].iloc[-(window+1):].mean())
        if ma_value > 0:
            return round((today_close / ma_value - 1) * 100, 2)
        return 0
    except:
        return 0


def _calc_pct_n(df, n=20):
    """计算近N日涨幅（百分比）"""
    try:
        if df is None or len(df) < n + 1 or 'close' not in df.columns:
            return 0
        today_close = float(df['close'].iloc[-1])
        n_days_ago = float(df['close'].iloc[-(n+1)])
        if n_days_ago > 0:
            return round((today_close / n_days_ago - 1) * 100, 2)
        return 0
    except:
        return 0


# ======================================================
# 批量预取历史数据（解决高频API调用问题）
# ======================================================
def batch_prefetch_hist_data(codes, start_date='20250101'):
    """
    在主循环之前批量预取所有股票数据到本地缓存（V2: 统一用 SQLite stk_factor_pro）
    使用 tushare 批量接口 pro.daily(ts_code="code1,code2,...")
    之后 get_hist_data() 将全部命中 SQLite 缓存，不再调API
    """
    if not codes:
        return

    from stock_cache import get_daily_cache_range, batch_insert_daily_cache

    # 入参 codes 已由上游 get_daily_kline 判定为缺失（daily_cache 表里没有 max_date>=TRADE_DATE），
    # 这里再校验一次是为了防止并发其他流程刚写入；正常情况下 cached 数为 0 是预期。
    cached = []
    missing = []
    for ts_code in codes:
        try:
            _, max_date = get_daily_cache_range(ts_code)
            if max_date is not None and str(max_date) >= TRADE_DATE:
                cached.append(ts_code)
                continue
        except Exception:
            pass
        missing.append(ts_code)

    print(f"  批量预取: 传入 {len(codes)} 只(上游已判定缺失), 二次校验 {len(cached)} 已存在/ {len(missing)} 仍需下载")

    if not missing:
        return

    # 分批下载，每批最多20只（避免单次返回行数限制导致数据不全）
    batch_size = 20
    batch_downloaded = set()
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        try:
            ts_list = ",".join(batch)
            df = pro.daily(
                ts_code=ts_list,
                start_date=start_date,
                end_date=TRADE_DATE
            )

            if df is not None and not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                # 批量写入 SQLite daily_cache（INSERT OR REPLACE，安全：仅 11 列）
                try:
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
                for ts_code in batch:
                    if ts_code in df['ts_code'].values:
                        batch_downloaded.add(ts_code)

                downloaded_count = df['ts_code'].nunique()
                print(f"  批次 {i//batch_size + 1}: 成功下载 {downloaded_count}/{len(batch)} 只")
            else:
                print(f"  批次 {i//batch_size + 1}: 下载返回空")

            time.sleep(0.15)

        except Exception as e:
            print(f"  批次 {i//batch_size + 1} 下载失败: {e}")
            # 单批失败则逐只重试
            for ts_code in batch:
                try:
                    single_df = _df_daily_by_code(
                        ts_code,
                        start_date=start_date,
                        end_date=TRADE_DATE
                    )
                    if single_df is not None and not single_df.empty:
                        single_df['trade_date'] = single_df['trade_date'].astype(str)
                        try:
                            batch_insert_daily_cache(single_df)
                        except Exception:
                            pass
                        batch_downloaded.add(ts_code)
                    time.sleep(0.15)
                except:
                    pass

    # =============================================
    # 回填全量数据：批量接口有行数上限，返回的数据
    # 可能只有最近几十行，需要单独下载补全历史（V2: 改用 SQLite 检查）
    # =============================================
    print(f"  回填全量数据: 检查 {len(batch_downloaded)} 只批量下载的股票...")
    backfill_count = 0
    for ts_code in batch_downloaded:
        try:
            min_date, max_date = get_daily_cache_range(ts_code)
            if min_date is None:
                continue

            # 检查缓存是否包含从 start_date 开始的数据
            start_date_to_check = start_date
            if pro is not None:
                try:
                    cal = _df_trade_cal(start_date=start_date, end_date=start_date)
                    if cal.empty or cal.iloc[0]['is_open'] != 1:
                        end_cal = (datetime.strptime(start_date, '%Y%m%d') + timedelta(days=30)).strftime('%Y%m%d')
                        cal = _df_trade_cal(start_date=start_date, end_date=end_cal)
                        cal = cal[cal['is_open'] == 1]
                        first_trade_after_start = cal[cal['cal_date'] >= start_date]['cal_date'].min()
                        if first_trade_after_start:
                            start_date_to_check = str(first_trade_after_start)
                except:
                    pass

            if str(min_date) <= start_date_to_check:
                continue  # 已有全量数据，跳过

            # 缺失历史数据，单独下载补全
            single_df = _df_daily_by_code(
                ts_code,
                start_date=start_date,
                end_date=TRADE_DATE
            )
            if single_df is not None and not single_df.empty:
                single_df = single_df.sort_values('trade_date')
                single_df['trade_date'] = single_df['trade_date'].astype(str)
                # 写入 SQLite daily_cache（INSERT OR REPLACE 自动去重）
                try:
                    batch_insert_daily_cache(single_df)
                except Exception:
                    pass
                backfill_count += 1

            time.sleep(0.12)
        except Exception as e:
            print(f"    回填失败 {ts_code}: {e}")

    if backfill_count > 0:
        print(f"  回填完成: {backfill_count} 只股票已补全历史数据至 {start_date}")
    

# =========================
# AI新闻情绪（缓存版）
# 每日只请求一次，新闻内容无变化则复用缓存
# =========================
def get_news_sentiment(
        code,
        name,
        theme="",
        theme_state=""
):
    """AI基本面+事件驱动分析（集成真实数据）
    
    参数：
        code: 股票代码
        name: 股票名称
        theme: 所属主题（已由 filter_by_top_themes 匹配）
        theme_state: 主题状态
    
    数据来源（一周内）：
    - report_rc: 机构研报
    - stk_surv: 券商调研
    - 网页新闻: 抓取财经新闻（东财/新浪/财联社等来源）
    - 本地缓存: 行情/K线数据
    - 主题状态（直接传入）
    - 热榜数据
    
    缓存逻辑：
    - 如果采集的新闻内容跟上一次相同，直接复用 AI 分析结果
    - 避免重复调用 AI 接口，节省费用
    """
    import hashlib
    
    # =========================
    # 前置判断：如果当日分析文件已存在，直接返回缓存结果
    # =========================
    cache_file = os.path.join(NEWS_CACHE_DIR, f"ai_analysis_{code}_{TRADE_DATE}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_response = cached.get("response", "")
            
            # 提取综合情绪强度评分
            found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', cached_response)
            if found:
                cached_score = min(max(int(found.group(1)), 0), 100)
            else:
                lines = cached_response.strip().split('\n')
                cached_score = 50
                for line in reversed(lines):
                    nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                    if nums:
                        cached_score = min(max(int(nums[-1]), 0), 100)
                        break
            
            print(f"  [AI情绪] 当日分析已存在，直接返回缓存分数: {cached_score}")
            return cached_score
        except Exception as e:
            print(f"  [AI情绪] 读取缓存失败，继续分析: {e}")
    
    # =========================
    # 采集一周内真实数据（新闻实时采集，不缓存）
    # =========================
    week_ago = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d')
    
    # 1. 研报数据 (report_rc) — 量化结构化数据，直接提取关键字段
    report_text = ""
    try:
        report_cache_key = f"report_rc_{code}_{week_ago}_{TRADE_DATE}.json"
        report_cache_file = os.path.join(TUSHARE_API_CACHE_DIR, report_cache_key)
        if os.path.exists(report_cache_file):
            with open(report_cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            df_report = pd.DataFrame(raw) if isinstance(raw, list) else pd.read_json(report_cache_file)
        else:
            df_report = pro.report_rc(
                ts_code=code,
                start_date=week_ago,
                end_date=TRADE_DATE,
                fields=[
                    "ts_code", "name", "report_date", "report_title", "report_type",
                    "classify", "org_name", "author_name", "quarter",
                    "op_rt", "op_pr", "tp", "np", "eps", "pe", "rd", "roe",
                    "ev_ebitda", "rating", "max_price", "min_price"
                ]
            )
            if df_report is not None and not df_report.empty:
                df_report.to_json(report_cache_file, force_ascii=False, orient="records", indent=2)
        
        if df_report is not None and not df_report.empty:
            items = []
            for _, r in df_report.iterrows():
                dt = r.get('report_date', '')
                title = r.get('report_title', r.get('report_title', ''))
                org = r.get('org_name', '')
                rating = r.get('rating', '')
                tp = r.get('tp', '')  # 目标价
                eps = r.get('eps', '')  # 每股收益
                pe = r.get('pe', '')  # 市盈率
                roe = r.get('roe', '')  # ROE
                np_val = r.get('np', '')  # 净利润
                
                # 构造输出行
                item_line = f"  [{dt}] {title} ({org})"
                if rating:
                    item_line += f" | 评级: {rating}"
                if tp:
                    item_line += f" | 目标价: {tp}元"
                items.append(item_line)
                
                # 关键指标行
                metrics = []
                if eps:
                    metrics.append(f"EPS={eps}")
                if pe:
                    metrics.append(f"PE={pe}")
                if roe:
                    metrics.append(f"ROE={roe}%")
                if np_val:
                    metrics.append(f"净利润={np_val}亿")
                if metrics:
                    items.append(f"    指标: {', '.join(metrics)}")
            
            if items:
                report_text = "\n".join(items[:20])
                print(f"  [机构研报] 获取到 {len(df_report)} 条")
    except Exception as e:
        report_text = f"  (获取失败: {e})"
    
    # 2. 调研数据 (stk_surv) — 带接口缓存
    surv_text = ""
    try:
        surv_cache_key = f"stk_surv_{code}_{week_ago}_{TRADE_DATE}.json"
        surv_cache_file = os.path.join(TUSHARE_API_CACHE_DIR, surv_cache_key)
        if os.path.exists(surv_cache_file):
            with open(surv_cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            df_surv = pd.DataFrame(raw) if isinstance(raw, list) else pd.read_json(surv_cache_file, dtype={"surv_date": str})
        else:
            df_surv = pro.stk_surv(
                ts_code=code,
                start_date=week_ago,
                end_date=TRADE_DATE
            )
            if df_surv is not None and not df_surv.empty:
                df_surv.to_json(surv_cache_file, force_ascii=False, orient="records", indent=2)
        if df_surv is not None and not df_surv.empty:
            items = []
            date_col = 'surv_date' if 'surv_date' in df_surv.columns else (
                'survey_date' if 'survey_date' in df_surv.columns else None)
            for _, r in df_surv.iterrows():
                dt = r[date_col] if date_col else str(r.get(list(r.keys())[0], ''))
                content = str(r.get('content', r.get('desc', '')))
                inst = r.get('institution', r.get('org_name', ''))
                items.append(f"  [{dt}] {inst} 调研")
                if len(content) > 200:
                    content = content[:200] + '...'
                items.append(f"    内容: {content}")
            if items:
                surv_text = "\n".join(items[:12])  # 最多6条调研
    except Exception as e:
        surv_text = f"  (获取失败: {e})"
    
    # 3. 行情数据（一周涨跌幅、成交量变化）
    price_text = ""
    try:
        df = _get_daily_from_sqlite(code)
        if df is not None and len(df) >= 5:
            df = df[df['trade_date'] <= TRADE_DATE]
            recent = df.tail(5)
            chg_5d = ((recent['close'].iloc[-1] / recent['close'].iloc[0]) - 1) * 100
            avg_vol_5d = recent['vol'].mean()
            avg_vol_20d = df.tail(20)['vol'].mean() if len(df) >= 20 else avg_vol_5d
            vol_ratio = avg_vol_5d / avg_vol_20d if avg_vol_20d > 0 else 1.0
            price_text = (
                f"  近5日涨跌幅: {chg_5d:+.2f}%\n"
                f"  最新收盘: {recent['close'].iloc[-1]:.2f}\n"
                f"  量比(5日/20日): {vol_ratio:.2f}\n"
                f"  5日日均成交: {avg_vol_5d/10000:.0f}万\n"
                f"  5日高-低: {recent['high'].max():.2f} - {recent['low'].min():.2f}"
            )
    except Exception as e:
        pass
    
    # 4. 主题归属及状态（直接使用传入参数）
    theme_text = ""
    if theme:
        theme_state_str = f" | 状态:{theme_state}" if theme_state else ""
        theme_text = f"  {theme}{theme_state_str}"
    
    # 5. 热榜数据
    hot_text = ""
    try:
        hot_rank_bonus, best_rank, hot_count = get_hot_list_best_rank_bonus(code, days=60)
        if best_rank <= 100:
            hot_text = f"  近60日最佳热榜排名: Top{best_rank} | 上榜次数: {hot_count}次"
    except Exception as e:
        pass

    # 6. 基本面信息
    basic_text = ""
    try:
        df_basic = pro.stock_basic(ts_code=code, fields='ts_code,name,industry,area,list_date')
        if df_basic is not None and not df_basic.empty:
            row = df_basic.iloc[0]
            basic_text = f"  行业: {row.get('industry', '')} | 地区: {row.get('area', '')} | 上市日期: {row.get('list_date', '')}"
    except Exception as e:
        pass

    # 7. 网页财经新闻（已移除 Bing 搜索）

    # —— newspaper3k 辅助抓取函数（自动识别正文+回退到 BeautifulSoup）
    def _extract_article_body(url, http_headers, timeout=10):
        """从新闻URL提取正文摘要。优先用 newspaper3k，失败则回退到 CSS 选择器。
        返回: (body_summary_str, publish_date_str) 或 ('', '')
        """
        body_text = ""
        publish_date = ""
        method_used = ""
        
        # ====== 方法1: newspaper3k（自动识别正文+元信息）
        try:
            from newspaper import Article
            article = Article(url, language='zh', headers=http_headers, request_timeout=timeout)
            article.download()
            article.parse()
            body_text = article.text.strip()
            if article.publish_date:
                publish_date = str(article.publish_date)[:10]
            method_used = "newspaper3k"
        except Exception:
            pass  # newspaper3k 失败，回退
        
        # ====== 方法2: 回退到 BeautifulSoup CSS 选择器
        if not body_text or len(body_text) < 30:
            try:
                import requests as _req
                from bs4 import BeautifulSoup as _soup
                _resp = _req.get(url, headers=http_headers, timeout=timeout)
                if _resp.status_code == 200:
                    _b = _soup(_resp.text, 'html.parser')
                    # 尝试多种正文容器
                    for _sel in ['#mp-editor', '.article-content', '.article-body', '#artibody',
                                 'div[class*="content"]', '.main-text', 'main', '.article']:
                        _e = _b.select_one(_sel)
                        if _e:
                            body_text = _e.get_text(' ', strip=True)
                            break
                    # 回退：取 <p> 段落拼接
                    if not body_text or len(body_text) < 30:
                        _ps = _b.find_all('p')
                        if _ps:
                            body_text = ' '.join([_p.get_text(strip=True) for _p in _ps[:10] if len(_p.get_text(strip=True)) > 15])
                    method_used = "bs4-fallback"
            except Exception:
                pass
        
        # 清理多余空白并截取前300字
        if body_text and len(body_text) > 30:
            body_text = re.sub(r'\s+', ' ', body_text).strip()
            _s = body_text[:300]
            if len(body_text) > 300:
                _s += "..."
            return _s, publish_date, method_used
        return "", "", ""

    # 9. 同花顺个股新闻（近一周，抓取标题+正文摘要）— newspaper3k + 回退
    ths_news_text = ""
    try:
        import requests as ths_requests
        from bs4 import BeautifulSoup as ths_soup

        # 清理股票代码（去掉后缀）
        clean_code = code.replace('.SH', '').replace('.SZ', '')
        url = f'https://stockpage.10jqka.com.cn/{clean_code}/'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        resp = ths_requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = ths_soup(resp.text, 'html.parser')

            # 查找新闻链接
            all_links = soup.find_all('a', href=True)
            news_items = []

            # 计算一周前的日期
            week_ago = datetime.now() - timedelta(days=7)

            for a in all_links:
                href = a.get('href', '')
                text = a.text.strip()

                # 筛选新闻链接
                if 'news.10jqka.com.cn' in href and text and len(text) > 10:
                    date_str = href.split('/')[-2] if len(href.split('/')) > 3 else None
                    is_related = (clean_code in text or name in text or
                                  (date_str and len(date_str) == 8 and
                                   datetime.strptime(date_str, '%Y%m%d') >= week_ago))
                    if is_related or len(news_items) < 5:
                        news_items.append((text[:80], href))

            seen = set()
            filtered_news = []
            for text, href in news_items:
                key = text[:30]
                if key not in seen:
                    seen.add(key)
                    filtered_news.append((text, href))
                if len(filtered_news) >= 8:
                    break

            # 用 newspaper3k+回退抓取前4条正文
            if filtered_news:
                lines = []
                max_fetch_body = min(4, len(filtered_news))
                for idx, (text, href) in enumerate(filtered_news):
                    line = f"  - {text}"
                    if idx < max_fetch_body:
                        summary, pub_date, method = _extract_article_body(href, headers)
                        if summary:
                            line += f"\n    摘要: {summary}"
                            if pub_date:
                                line += f"（发布时间: {pub_date}）"
                    lines.append(line)
                ths_news_text = "\n".join(lines)
                print(f"  [同花顺新闻] 获取到 {len(filtered_news)} 条，前{max_fetch_body}条已抓取正文")
    except Exception as e:
        print(f"  [同花顺新闻] 获取失败: {e}")
    
    # 10. 新浪财经个股新闻（抓取标题+正文摘要）
    sina_news_text = ""
    try:
        import requests as sina_requests
        from bs4 import BeautifulSoup as sina_soup
        
        # 确定交易所前缀（沪市SH，深市SZ，北交所BJ）
        raw_code = code.split('.')[0]
        if raw_code.startswith('688') or raw_code.startswith('8') or raw_code.startswith('4'):
            sina_prefix = 'sh'  # 科创板、北交所用沪市前缀
        elif raw_code.startswith('9'):
            sina_prefix = 'bj'  # 北交所
        elif '.SH' in code.upper():
            sina_prefix = 'sh'
        else:
            sina_prefix = 'sz'
        
        url = f'https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{sina_prefix}{raw_code}.phtml'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        resp = sina_requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = sina_soup(resp.text, 'html.parser')
            
            # 查找新闻链接
            all_links = soup.find_all('a')
            news_items = []  # (title, href)
            
            for a in all_links:
                text = a.text.strip()
                href = a.get('href', '')
                
                # 严格筛选：必须包含股票名称或代码，且不是导航链接
                if text and len(text) > 10 and 'vip.stock' not in href:
                    # 优先选择包含股票名称的新闻
                    if name in text or raw_code in text:
                        # 处理相对链接
                        full_href = href if href.startswith('http') else f"https:{href}" if href.startswith('//') else f"https://vip.stock.finance.sina.com.cn{href}"
                        news_items.append((text[:80], full_href))
            
            # 去重并限制数量（最多8条标题，前4条抓正文）
            seen = set()
            filtered_news = []
            for text, href in news_items:
                key = text[:30]
                if key not in seen:
                    seen.add(key)
                    filtered_news.append((text, href))
                if len(filtered_news) >= 8:
                    break
            
            # 用 newspaper3k+回退抓取前4条正文
            if filtered_news:
                lines = []
                max_fetch_body = min(4, len(filtered_news))
                for idx, (text, href) in enumerate(filtered_news):
                    line = f"  - {text}"
                    if idx < max_fetch_body:
                        summary, pub_date, method = _extract_article_body(href, headers)
                        if summary:
                            line += f"\n    摘要: {summary}"
                            if pub_date:
                                line += f"（发布时间: {pub_date}）"
                    lines.append(line)
                sina_news_text = "\n".join(lines)
                print(f"  [新浪财经新闻] 获取到 {len(filtered_news)} 条，前{max_fetch_body}条已抓取正文")
    except Exception as e:
        print(f"  [新浪财经新闻] 获取失败: {e}")

    # 11. Google新闻（需要代理，使用RSS的description摘要+可选正文抓取）
    google_news_text = ""
    try:
        if PROXY_ENABLED:
            from bs4 import BeautifulSoup as google_soup
            from datetime import timezone
            
            # 搜索关键词：股票名称
            keyword = urllib.parse.quote(name)
            url = f'https://news.google.com/rss/search?q={keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            resp = default_session.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                soup = google_soup(resp.text, 'xml')
                items = soup.find_all('item')
                
                # 计算一个月前的日期（北京时间）
                month_ago_beijing = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=30)
                
                # 过滤一个月内的新闻（保存标题+链接+描述）
                news_list = []  # [(title, link, description)]
                for item in items[:50]:
                    title = item.find('title')
                    pubdate = item.find('pubDate')
                    link = item.find('link')
                    desc = item.find('description')
                    if title and title.text:
                        title_text = title.text.strip()
                        # 检查标题是否包含股票名称或代码
                        if name in title_text or clean_code in title_text:
                            # 解析日期（GMT时间转为北京时间）
                            try:
                                from email.utils import parsedate_to_datetime
                                pub_time_gmt = parsedate_to_datetime(pubdate.text)
                                # 转为北京时间
                                pub_time_beijing = pub_time_gmt.astimezone(timezone(timedelta(hours=8)))
                                if pub_time_beijing >= month_ago_beijing:
                                    link_text = link.text.strip() if link and link.text else ''
                                    desc_text = ''
                                    if desc and desc.text:
                                        # 从 description 中提取纯文本（RSS可能有HTML片段）
                                        desc_soup = google_soup(desc.text, 'html.parser')
                                        desc_text = desc_soup.get_text(' ', strip=True)
                                        desc_text = re.sub(r'\s+', ' ', desc_text).strip()
                                    news_list.append((title_text[:80], link_text, desc_text))
                            except Exception:
                                # 如果日期解析失败，只要标题相关也加入
                                link_text = link.text.strip() if link and link.text else ''
                                desc_text = ''
                                if desc and desc.text:
                                    desc_soup = google_soup(desc.text, 'html.parser')
                                    desc_text = desc_soup.get_text(' ', strip=True)
                                    desc_text = re.sub(r'\s+', ' ', desc_text).strip()
                                news_list.append((title_text[:80], link_text, desc_text))
                
                if news_list:
                    # 去重
                    seen = set()
                    unique_news = []
                    for title, link, desc in news_list:
                        key = title[:30]
                        if key not in seen:
                            seen.add(key)
                            unique_news.append((title, link, desc))
                    
                    # 组装输出：最多8条标题，前4条用 newspaper3k+回退抓取详情页正文
                    lines = []
                    max_fetch_body = min(4, len(unique_news))
                    for idx, (title, link, desc) in enumerate(unique_news[:8]):
                        line = f"  - {title}"
                        if idx < max_fetch_body:
                            # 方法1: 用 newspaper3k 抓取详情页正文（优先）
                            body_summary = ""
                            pub_date = ""
                            try:
                                from newspaper import Article
                                # Google 新闻链接可能是跳转链接（news.google.com/...），需要走 default_session 的代理
                                article = Article(link, language='zh', headers=headers, request_timeout=15)
                                article.download()
                                article.parse()
                                body_text = article.text.strip()
                                if article.publish_date:
                                    pub_date = str(article.publish_date)[:10]
                                if len(body_text) > 30:
                                    body_text_clean = re.sub(r'\s+', ' ', body_text).strip()
                                    body_summary = body_text_clean[:300]
                                    if len(body_text_clean) > 300:
                                        body_summary += "..."
                            except Exception:
                                pass  # newspaper3k 失败，回退

                            # 方法2: 回退到 RSS description
                            if not body_summary:
                                if desc and len(desc) > 30:
                                    body_summary = desc[:300]
                                    if len(desc) > 300:
                                        body_summary += "..."

                            if body_summary:
                                line += f"\n    摘要: {body_summary}"
                                if pub_date:
                                    line += f"（发布时间: {pub_date}）"
                        lines.append(line)
                    google_news_text = "\n".join(lines)
                    print(f"  [Google新闻] 获取到 {len(unique_news)} 条，前{max_fetch_body}条已抓取详情页")
    except Exception as e:
        print(f"  [Google新闻] 获取失败: {e}")

    # =========================
    # 组装AI Prompt
    # =========================
    data_section = f"""【股票基本信息】
{basic_text or '  (暂无)'}

【热榜数据】
{hot_text or '  (近60日未上热榜)'}

【行情数据（近5日）】
{price_text or '  (暂无)'}

【主题归属与状态】
{theme_text or '  (暂无主题数据)'}

【同花顺个股新闻（近一周）】
{ths_news_text or '  (暂无相关新闻)'}

【新浪财经个股新闻】
{sina_news_text or '  (暂无相关新闻)'}

【Google新闻（国际视野，近一月）】
{google_news_text or '  (暂无相关新闻)'}

【机构研报（近一月）】
{report_text or '  (无)'}

【券商调研（近一月）】
{surv_text or '  (无)'}
"""

    prompt = f"""你是一个A股短线“消息面驱动提纯分析器”，你的任务不是总结信息，而是：
从所有新闻、公告、研报、舆情中，提炼出唯一最强上涨驱动因素
并判断该股票当前的：
热度强度
驱动来源
是否处于资金关注阶段
一、输入内容

你将收到结构化信息：

新闻列表
公告列表
研报摘要
舆情/热榜数据
行业信息（如有）
⚠️ 二、核心任务（非常重要）
 ② 提炼“一句话驱动逻辑”（必须极简）

要求：

不超过30字
必须是“因果结构”
不能复述新闻

示例：

“AI服务器需求爆发带动光模块放量”
“机构调研确认订单超预期，净利润预测增长100%，买入评级”
“板块情绪共振带动资金抢筹”
🔵 ③ 判断热度等级（0-100）

热度 = 市场关注 + 资金参与 + 信息密度

分级：
0-20：无关注
20-40：弱关注
40-60：中等热度
60-80：强热度
80-100：极强主线热度
🧠 三、强约束规则（非常关键）
❌ 禁止行为：
不要总结所有新闻
不要罗列信息
不要多驱动并列
不要写技术分析
不要写基本面分析
✅ 必须遵守：
1️⃣ 只输出“一个主驱动”
所有信息必须压缩为：
👉 一个最强解释变量
2️⃣ 必须做“去噪”
规则：
公告 < 研报 < 机构调研 < 资金行为 < 龙头带动
3️⃣ 必须做“资金优先判断”

如果出现：

龙虎榜
北向流入
主力净流入
板块联动
👉 优先级最高


请分析以下股票：

{name}（{code}）

以下是系统采集的真实数据（近一个月）：

{data_section}

【信息优先级指引】（按重要程度从高到低）
1. 【最高优先级】券商调研（近一月）：机构实地调研记录，重点关注参与机构级别（券商/基金/保险/私募/QFII等）、调研次数、调研问询内容，机构密集调研通常意味着强烈关注
2. 【高优先级】机构研报（近一月）：券商/机构发布的评级报告、目标价、核心逻辑，重点关注评级变化（首次覆盖/上调/下调）和目标价预期差
3. 【参考优先级】Google新闻、同花顺/新浪财经新闻：作为信息补全，验证其他渠道信息的真实性

【解读要求】
- 券商调研：重点关注有多少家机构参与、什么类型的机构（知名机构>普通机构）、调研地点（现场>电话）、调研问题涉及哪些核心业务方向
- 机构研报：重点关注研报评级（买入/增持/中性/减持）、核心推荐逻辑是否持续有效
- 资讯：作为信息验证来源，判断是否与调研/研报信息相互印证

请标注：
- 上述信息所涉及的产业/概念方向
- 【共振判断】如果个股信息与某主题形成共振（如半导体行业国产替代与半导体主题共振、新能源产业链景气度提升与小金属/固态电池主题共振），请特别标注 ⭐⭐⭐ "与XX主题共振"
- 【差异化判断】如果信息与其他同主题个股存在明显差异，请标注"主题内阿尔法差异"


请给出三个分数（0-100）：
- 短期影响强度（1-5天）
- 中期影响强度（5-20天）
- 预期差强度（核心指标）

其中：
0-20：无影响/噪音
20-40：轻微扰动
40-60：可交易级别催化
60-80：强催化（可能趋势启动）
80-100：极强催化（可能主升浪）

最后，请基于以上分析，给出一个综合情绪强度评分（0-100整数），其中：
90-100：极强利好，机构持续看多
70-89：明显利好
50-69：中性偏好
30-49：偏空
0-29：明显利空

【输出格式和内容要求】
第一行输出综合结论，包括以上所有分析结果，以及最终的综合情绪强度评分。
中间输出：
- 最强驱动源
- 一句话驱动逻辑
- 热度等级
- 是否处于资金关注阶段
最后一行必须是单独的数字，即综合情绪强度评分。
【对AI输出的强制要求】输出的文字要求最精简（【重要】不要输出上面的步骤提示词），只给以上要求的关键词结果即可，对于影响非常重大的新闻进行标题显示。
【重要约束】：
1. 不做任何技术面分析（不要分析价格、均线、成交量、突破等）
2. 不做独立的主题分析（不评价主题整体强弱，仅标注个股信息与主题的共振关系）
3. 所有分析必须严格基于提供的信息内容，不得编造信息或进行无依据的推断
"""

    # =========================
    # 检查缓存：新闻内容无变化则复用
    # =========================
    cache_file = os.path.join(NEWS_CACHE_DIR, f"ai_analysis_{code}_{TRADE_DATE}.json")
    content_hash = hashlib.md5(data_section.encode('utf-8')).hexdigest()
    
    # 读取缓存检查内容哈希
    cached_content_hash = None
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_content_hash = cached.get("content_hash")
        except Exception:
            pass
    
    # 如果内容哈希相同，直接返回缓存结果
    if cached_content_hash == content_hash:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_response = cached.get("response", "")
            
            found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', cached_response)
            if found:
                cached_score = min(max(int(found.group(1)), 0), 100)
            else:
                lines = cached_response.strip().split('\n')
                cached_score = 50
                for line in reversed(lines):
                    nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                    if nums:
                        cached_score = min(max(int(nums[-1]), 0), 100)
                        break

            print(f"  [AI情绪] 内容无变化，复用缓存分数: {cached_score}")
            return cached_score
        except Exception:
            pass

    try:

        r = deepseek(prompt, use_flash=True)

        # =========================
        # 保存输入输出到缓存（便于复盘和问题排查）
        # =========================
        try:
            cache_data = {
                "code": code,
                "name": name,
                "trade_date": TRADE_DATE,
                "theme": theme,
                "content_hash": content_hash,  # 保存内容哈希
                "prompt": prompt,
                "response": r,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # =========================
        # 提取数字（优先搜索"综合情绪强度评分"关键词）
        # =========================
        found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', r)
        if found:
            score = min(max(int(found.group(1)), 0), 100)
        else:
            lines = r.strip().split('\n')
            score = 50
            for line in reversed(lines):
                nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                if nums:
                    score = min(max(int(nums[-1]), 0), 100)
                    break

        # 防止API过快
        time.sleep(0.5)

        return score

    except Exception as e:

        print(
            f"{code} AI情绪失败:",
            e
        )

        return 50


# =========================
# 批量AI情绪缓存
# =========================
def calc_trend_strength_v2(df):
    """
    更细腻的趋势强度评分（避免1.0被滥用）
    """
    C = df['close']

    ma10 = C.rolling(10).mean()
    ma20 = C.rolling(20).mean()
    ma30 = C.rolling(30).mean()
    ma60 = C.rolling(60).mean()

    score = 0

    # 1. 均线排列（更细化，避免全满分）
    if (ma10.iloc[-1] > ma20.iloc[-1] > ma30.iloc[-1] > ma60.iloc[-1]):
        score += 30  # 完美多头排列得30分
    elif (ma10.iloc[-1] > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]):
        score += 20  # 次好排列得20分
    elif (ma10.iloc[-1] > ma60.iloc[-1]):
        score += 10  # 仅短期在长期上得10分

    # 2. 均线斜率（更细腻）
    ma20_slope = (ma20.iloc[-1] - ma20.iloc[-5]) / ma20.iloc[-5]
    if ma20_slope > 0.03:
        score += 20
    elif ma20_slope > 0.01:
        score += 10
    elif ma20_slope > 0:
        score += 5

    ma60_slope = (ma60.iloc[-1] - ma60.iloc[-10]) / ma60.iloc[-10]
    if ma60_slope > 0.02:
        score += 15
    elif ma60_slope > 0.01:
        score += 8
    elif ma60_slope > 0:
        score += 3

    # 3. 股价位置（更细化）
    price_ma60_ratio = C.iloc[-1] / ma60.iloc[-1]
    if price_ma60_ratio > 1.1:
        score += 15
    elif price_ma60_ratio > 1.05:
        score += 10
    elif price_ma60_ratio > 1.0:
        score += 5

    # 最大不超过85分（避免1.0被滥用）
    return min(score, 85) / 100
def calc_trend_slope(close, window=20):

    if len(close) < window:
        return 0

    y = close.tail(window).values
    x = np.arange(window)

    slope = np.polyfit(x, y, 1)[0]

    # 标准化（按价格尺度）
    mean_price = np.mean(y)
    if mean_price == 0:
        return 0

    return slope / mean_price * 100

from scipy.stats import linregress
def calc_trend_stability2(close, window=20):

    y = close.tail(window).values

    x = np.arange(window)

    slope, intercept, r, p, stderr = linregress(x, y)

    return r * r


def calc_volume_structure(df):
    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.iloc[:-1].tail(20).mean() + 1e-6) if len(V) > 20 else V.iloc[-1] / (V.mean() + 1e-6)

    price_trend = C.iloc[-1] / C.iloc[-20] - 1

    obv = (np.sign(C.diff()) * V).fillna(0).cumsum()
    obv_strength = obv.iloc[-1] / (abs(obv.tail(20).mean()) + 1e-6)

    # 归一化到 0-1
    vol_component = np.tanh(np.log1p(vol_ratio) * 0.3)
    obv_component = np.tanh(np.log1p(abs(obv_strength)) * 0.3)
    price_component = np.tanh(max(price_trend, 0) * 2)
    return vol_component * 0.4 + obv_component * 0.4 + price_component * 0.2


def calc_accumulation_factor(df):
    if len(df) < 40:
        return 0

    C = df['close']
    V = df['vol']

    # 抗跌结构
    price_hold = C.iloc[-10:].min() / C.iloc[-20:-10].max()

    # 缩量
    vol_shrink = V.tail(5).mean() / (V.tail(20).mean() + 1e-6)

    # 稳定抬升
    slope = calc_trend_slope(C, 20)

    score = 0

    if price_hold > 0.92:
        score += 50

    if vol_shrink < 0.8:
        score += 30

    if slope > 0:
        score += 20

    # 归一化到 0-1
    return score / 100.0
def calc_big_money_factor(df):
    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.iloc[:-1].tail(20).mean() + 1e-6) if len(V) > 20 else V.iloc[-1] / (V.mean() + 1e-6)

    price_change = C.iloc[-1] / C.iloc[-2] - 1

    money_flow = (C.pct_change() * V).tail(5).sum()

    # 资金持续性（关键升级）
    flow_consistency = np.sum((C.pct_change().tail(5) > 0)) / 5

    # 归一化到 0-1
    vol_component = np.tanh(np.log1p(vol_ratio) * 0.3)
    price_component = np.tanh(max(price_change, 0) * 5)
    flow_component = np.tanh(np.log1p(abs(money_flow)) * 0.1)
    consistency_component = flow_consistency
    return vol_component * 0.3 + price_component * 0.3 + flow_component * 0.2 + consistency_component * 0.2    

def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def calc_dual_layer_score_v6(df, ts_code='', theme=''):
    """
    三路径概率系统（游资行为建模 v6）- 修复版
    1. 避免趋势强度1.0被滥用 ✅
    2. 失败概率分层惩罚机制 ✅ 
    3. 支持主题强度纳入（通过外部传入） ✅

    输出：
        P_up        上涨延续概率
        P_fail      突破失败概率
        P_squeeze   洗盘再启动概率
        edge_score  交易边际优势
    """

    C = df['close']
    H = df['high']
    L = df['low']
    VOL = df['vol']


    is_chuangchuang = ts_code.startswith('300') or ts_code.startswith('688')

    beta_multiplier = 1.0

    if ts_code.startswith('300'):   # 创业板（高弹性）
        beta_multiplier = 1.25

    elif ts_code.startswith('688'): # 科创板（更极端弹性）
        beta_multiplier = 1.30
    
    # =========================
    # 弹性结构（游资核心）
    # =========================
    HHV20 = H.iloc[:-1].tail(20).max() if len(H) > 1 else H.tail(20).max()
    LLV20 = L.tail(20).min()

    amp20 = (HHV20 - LLV20) / (LLV20 + 1e-6)

    if np.isnan(amp20) or np.isinf(amp20):
        compression_score = 0.5
    else:
        compression_score = (
            1.0 if amp20 <= 0.15 else
            0.8 if amp20 <= 0.25 else
            0.5 if amp20 <= 0.35 else
            0.2
        )
    volatility = (H.tail(20).max() - L.tail(20).min()) / C.iloc[-1]

    turnover_proxy = VOL.tail(5).mean() / (VOL.tail(20).mean() + 1e-6)

    elastic_score = sigmoid(
        volatility * 1.0 +
        turnover_proxy * 0.8 +
        (1 - compression_score) * 0.6
    )

    # =========================
    # 1. 基础趋势 & 资金结构
    # =========================
    trend_strength = calc_trend_strength_v2(df)
    trend_stability = calc_trend_stability2(C, 20)

    volume_structure = calc_volume_structure(df)
    accumulation = calc_accumulation_factor(df)
    big_money = calc_big_money_factor(df)

    money_momentum = (
        volume_structure * 0.5 +
        accumulation * 0.3 +
        big_money * 0.2
    )

    # =========================
    # 2. 突破结构（关键升级）
    # =========================
    HHV60 = H.iloc[:-1].rolling(60).max().iloc[-1] if len(H) > 1 else H.rolling(60).max().iloc[-1]
    breakout_position = np.clip(
        (C.iloc[-1] / HHV60 - 0.90) / 0.15,
        0, 1
    )

    MA20 = C.rolling(20).mean().iloc[-1]
    MA60 = C.rolling(60).mean().iloc[-1]

    # 价格效率（放量是否有效）
    price_efficiency = abs(C.iloc[-1] - C.iloc[-2]) / (VOL.iloc[-1] + 1e-6)
    price_efficiency = np.tanh(price_efficiency * 3)

    # =========================
    # 3. 压缩与爆发结构
    # =========================
    HHV20 = H.iloc[:-1].tail(20).max() if len(H) > 1 else H.tail(20).max()
    LLV20 = L.tail(20).min()
    amp20 = (HHV20 - LLV20) / LLV20

    compression_score = (
        1.0 if amp20 <= 0.15 else
        0.8 if amp20 <= 0.25 else
        0.5 if amp20 <= 0.35 else
        0.2
    )

    vol5 = VOL.tail(5).mean()
    vol20 = VOL.tail(20).mean()
    burst_score = np.clip(vol5 / (vol20 + 1e-6), 0, 3) / 3

    # =========================
    # 4. 趋势概率（核心）
    # =========================
    trend_prob = sigmoid(
        (trend_strength - 0.5) * 1.5 +
        (trend_stability - 0.5) * 1.0
    )

    # =========================
    # 5. 上涨推进概率（核心）
    # =========================
    break_strength = sigmoid(
        (breakout_position - 0.5) * 1.5 +
        (money_momentum - 0.5) * 1.0 +
        (price_efficiency - 0.5) * 0.8
    )

    P_up = (
        0.45 * trend_prob +
        0.35 * break_strength +
        0.20 * money_momentum
    )

    # =========================
    # 6. 失败概率（最关键风控）
    # =========================
    # 1. 高价风险因子 - 连续值而非二值
    price_ma60_ratio = C.iloc[-1] / MA60
    high_risk_zone = np.clip((price_ma60_ratio - 1.1) / 0.4, 0.0, 1.0)  # 1.1以下0，1.5以上1
    
    # 2. 阻力压力因子 - 连续值
    # 用amp20直接计算，而不是离散的compression_score
    resistance_pressure = np.clip((amp20 - 0.15) / 0.25, 0.0, 1.0)  # 0.15以下0，0.4以上1
    
    # 3. 派发风险因子 - 连续值
    vol_ratio = VOL.iloc[-1] / (VOL.tail(10).mean() + 1e-6)
    price_change = (C.iloc[-1] - C.iloc[-2]) / C.iloc[-2]
    # 放量下跌风险：量比越大且跌幅越大，风险越高
    distribution_risk = 0.0
    if price_change < 0:  # 下跌
        distribution_risk = np.clip((vol_ratio - 1.0) * abs(price_change) * 10, 0.0, 1.0)
    
    # 4. 额外维度：趋势稳定性下降风险
    ma20 = C.rolling(20).mean().iloc[-1]
    ma5 = C.rolling(5).mean().iloc[-1]
    trend_decline_risk = 0.0
    if ma5 < ma20:  # 5日均线跌破20日均线
        trend_decline_risk = np.clip((ma20 - ma5) / ma20 * 20, 0.0, 1.0)
    
    fail_prob = sigmoid(
        (resistance_pressure - 0.5) * 1.5 +
        (high_risk_zone - 0.5) * 1.2 +
        (distribution_risk - 0.5) * 1.5 +
        (trend_decline_risk - 0.5) * 0.8
    )

    # =========================
    # 7. 洗盘再启动概率（游资核心）
    # =========================
    squeeze_prob = sigmoid(
        (compression_score - 0.5) * 1.5 +
        (0.5 - burst_score) * 0.8 +
        (trend_stability - 0.5) * 0.6
    )

    # =========================
    # 8. 交易边际优势
    # =========================
    edge_score = P_up - fail_prob

    # =========================
    # 9. 风险等级（辅助）
    # =========================
    risk_ratio = C.iloc[-1] / MA60

    risk_level = (
        "极高" if risk_ratio > 1.6 else
        "高" if risk_ratio > 1.4 else
        "中" if risk_ratio > 1.2 else
        "低"
    )
    # =========================
    # 10. 总排序评分（用于选股优先级）
    # =========================

    trend_component = trend_prob
    momentum_component = break_strength
    money_component = money_momentum

    # =========================
    # 基础层（核心 alpha）
    # =========================
    base_score = (
        trend_component * 0.4 +
        momentum_component * 0.35 +
        money_component * 0.25
    )

    # =========================
    # 弹性层（游资核心）
    # =========================
    elastic_layer = beta_multiplier * (0.7 + 0.3 * elastic_score)

    # =========================
    # 风险层 - 分层惩罚机制 ✅
    # =========================
    if fail_prob < 0.2:
        # 低失败概率：几乎不惩罚
        risk_layer = 1.0
    elif fail_prob < 0.4:
        # 中低失败概率：轻微惩罚
        risk_layer = 0.9
    elif fail_prob < 0.5:
        # 中等失败概率：中等惩罚
        risk_layer = 0.75
    elif fail_prob < 0.6:
        # 中高失败概率：较重惩罚
        risk_layer = 0.55
    else:
        # 高失败概率：严厉惩罚
        risk_layer = 0.35

    # =========================
    # 主题强度加分（可选）✅
    # =========================
    theme_bonus = 1.0
    if theme:
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data:
                for t in theme_data.get('themes', []):
                    if t.get('theme_name') == theme:
                        theme_score = t.get('trend_score', 0)
                        if theme_score >= 80:
                            theme_bonus = 1.3  # 热主题大加分
                        elif theme_score >= 60:
                            theme_bonus = 1.15  # 次热主题加分
                        elif theme_score >= 40:
                            theme_bonus = 1.0  # 不加分
                        else:
                            theme_bonus = 0.9  # 冷主题减分
                        break
        except Exception as e:
            pass

    # =========================
    # 最终评分
    # =========================
    final_rank_score = base_score

    final_rank_score *= elastic_layer
    final_rank_score *= risk_layer
    final_rank_score *= theme_bonus

    # 调整放大倍数，让评分更合理
    final_rank_score = np.clip(final_rank_score * 200, 0, None)
    
    # =========================
    # 输出
    # =========================
    return {
        "趋势概率": round(P_up, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(squeeze_prob, 4),
        "交易优势": round(edge_score, 4),

        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),

        "资金动量": round(money_momentum, 3),
        "突破强度": round(break_strength, 3),

        "压缩度": round(compression_score, 3),
        "量能爆发": round(burst_score, 3),

        "风险等级": risk_level,
        "总排序评分": round(final_rank_score, 2)
    }


# =========================================================
# V7 评分系统 v6：主题纯度优化 + 主线共振加分
# =========================================================
def calc_dual_layer_score_v7(df, ts_code='', stock_info=None, theme=''):
    """
    V7评分系统 v6：主题纯度优化 + 主线共振加分
    """

    # =========================
    # 获取V6技术指标
    # =========================
    v6_result = calc_dual_layer_score_v6(df, ts_code, theme)

    # V6各指标
    trend_probability = v6_result.get('趋势概率', 0)  # 0-1
    fail_prob = v6_result.get('失败概率', 0)  # 0-1
    breakout_strength = v6_result.get('突破强度', 0)  # 0-1
    money_momentum = v6_result.get('资金动量', 0)  # 0-1
    trend_stability = v6_result.get('趋势稳定', 0)  # 0-1
    volume_explosion = v6_result.get('量能爆发', 0)  # 0-1
    compression_score = v6_result.get('压缩度', 0)  # 0-1
    squeeze_prob = v6_result.get('洗盘概率', 0)  # 0-1

    # =========================
    # 自动选择纯度最高的主题
    # =========================
    all_themes = ''
    if not theme and stock_info:
        theme = _find_best_theme(stock_info)
        all_themes = find_all_themes(stock_info)
    
    # 如果没有找到多主题，使用最佳主题
    if not all_themes:
        all_themes = theme or ''

    # =========================
    # 1. 主题真实性（防止蹭概念）
    # =========================
    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30

    # =========================
    # 2. 主题强度 + 主线共振
    # =========================
    theme_strength_bonus = 1.0
    theme_rank_bonus = 0
    mainline_resonance = 0  # 新增：主线共振加分
    if theme:
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data:
                for t in theme_data.get('themes', []):
                    if t.get('theme_name') == theme:
                        theme_score = t.get('trend_score', 0)
                        # 主题基础强度
                        if theme_score >= 80:
                            theme_strength_bonus = 1.2  # 降低从1.3降到1.2
                        elif theme_score >= 60:
                            theme_strength_bonus = 1.1
                        elif theme_score >= 40:
                            theme_strength_bonus = 1.0
                        else:
                            theme_strength_bonus = 0.95
                        
                        # 主线共振：如果趋势/情绪/成交量共振
                        trend = t.get('trend', 0)
                        sentiment = t.get('sentiment', 0)
                        vol_increase = t.get('volume_increase', 0)
                        if trend > 70 and sentiment > 70 and vol_increase > 0:
                            mainline_resonance = 5  # 三个共振加5分
                        elif trend > 60 or sentiment > 60:
                            mainline_resonance = 3  # 两个强共振加3分
                        
                        break
        except Exception as e:
            pass

    # =========================
    # 3. 动量维度捕捉启动股
    # =========================
    momentum_score = _calc_momentum_score(df)

    # =========================
    # 4. 压缩+洗盘统一建模
    # =========================
    squeeze_compression_score = _calc_squeeze_compression(df, compression_score, squeeze_prob)

    # =========================
    # 5. V6基础分（重新设计权重）
    # =========================
    # V6技术指标转为基础分（0-100）
    v6_base_score = (
        trend_probability * 30 +      # 趋势概率占30%
        breakout_strength * 25 +      # 突破强度占25%
        money_momentum * 20 +         # 资金动量占20%
        volume_explosion * 15 +       # 量能爆发占15%
        trend_stability * 10          # 趋势稳定占10%
    )

    # =========================
    # 6. 风险调整
    # =========================
    risk_adjustment = 0
    if fail_prob < 0.2:  # 极低失败率：加分
        risk_adjustment = 15
    elif fail_prob < 0.35:
        risk_adjustment = 10
    elif fail_prob < 0.45:
        risk_adjustment = 5
    elif fail_prob < 0.55:
        risk_adjustment = -3  # 从0改为稍微降3分
    elif fail_prob < 0.65:
        risk_adjustment = -10  # 从-8提高到-10
    else:
        risk_adjustment = -18  # 从-15提高到-18

    # =========================
    # 7. V7综合评分
    # =========================
    base_with_theme = v6_base_score * theme_strength_bonus
    momentum_bonus = momentum_score * 15
    squeeze_bonus = squeeze_compression_score * 10
    theme_purity_bonus = theme_confidence * 0.15  # 从10提高到15，注意这里是0.15（百分比系数）
    v7_total = (
        base_with_theme +
        momentum_bonus +
        squeeze_bonus +
        theme_purity_bonus +
        mainline_resonance +
        risk_adjustment
    )

    # 确保在0-100范围内
    v7_total = np.clip(v7_total, 0, 100)

    # =========================
    # 输出结果
    # =========================
    return {
        # V6技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(squeeze_prob, 4),
        "交易优势": round(v6_result.get('交易优势', 0), 4),
        "趋势强度": round(v6_result.get('趋势强度', 0), 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v6_result.get('风险等级', '低'),

        # V7新指标
        "所属主题": all_themes,
        "主题纯度": round(theme_confidence, 2),
        "主题强化系数": round(theme_strength_bonus, 2),
        "主线共振加分": round(mainline_resonance, 2),
        "动量得分": round(momentum_score, 2),
        "压缩洗盘得分": round(squeeze_compression_score, 2),
        "风险调整": round(risk_adjustment, 2),

        # V7总分
        "V7总评分": round(v7_total, 2)
    }


# =========================================================
# V7.5 综合评分系统
# =========================================================
def calc_dual_layer_score_v75(df, ts_code='', stock_info=None, theme=''):
    """
    V7.5综合评分系统 - 从V7函数引入所有指标再计算V7.5总分
    
    V7.5在V7基础上叠加：
    - 位置因子（position_factor）：120日高低位位置
    - 龙头因子（leader_factor）：趋势+资金+概率综合
    - 主题排名加成（theme_rank_bonus）：核心公司额外加分
    
    从V7引入的指标：
    - 基础技术指标：趋势概率、突破强度、资金动量等
    - V7复合指标：动量得分、压缩洗盘得分、主线共振加分、主题强化系数
    """

    # =========================
    # 从V7获取所有指标
    # =========================
    v7_result = calc_dual_layer_score_v7(df, ts_code=ts_code, stock_info=stock_info, theme=theme)
    
    # 计算涨跌幅（用于返回给AI报告，使用接口返回的 pct_chg 避免除权导致计算错误）
    if 'pct_chg' in df.columns and len(df) >= 1:
        today_pct = float(df['pct_chg'].iloc[-1])
    elif len(df) >= 2:
        today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100
    else:
        today_pct = 0.0

    # V7基础技术指标（0-1范围）
    trend_probability = float(v7_result.get('趋势概率', 0.5))
    fail_prob = float(v7_result.get('失败概率', 0.5))
    breakout_strength = float(v7_result.get('突破强度', 0.5))
    money_momentum = float(v7_result.get('资金动量', 0.5))
    trend_stability = float(v7_result.get('趋势稳定', 0.5))
    volume_explosion = float(v7_result.get('量能爆发', 0.5))
    compression_score = float(v7_result.get('压缩度', 0.5))
    trend_strength = float(v7_result.get('趋势强度', 0.5))

    # V7复合指标
    theme_confidence = float(v7_result.get('主题纯度', 30))
    theme_strength_bonus = float(v7_result.get('主题强化系数', 1.0))
    mainline_resonance = float(v7_result.get('主线共振加分', 0))
    momentum_score = float(v7_result.get('动量得分', 0))
    squeeze_compression_score = float(v7_result.get('压缩洗盘得分', 0))
    theme = v7_result.get('所属主题', theme)

    # =========================
    # V7.5独有：位置因子
    # =========================
    position_factor = calc_position_factor(df)

    # =========================
    # V7.5独有：龙头因子
    # =========================
    leader_factor = (
        trend_strength * 0.40 +
        money_momentum * 0.35 +
        trend_probability * 0.25
    )

    # =========================
    # V7.5独有：主题排名加成
    # =========================
    theme_rank_bonus = 0
    if stock_info and theme:
        try:
            cfg_path = os.path.join(BASE_DIR, 'theme.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    theme_cfg = json.load(f).get('HOT_THEMES', {})
                if theme in theme_cfg:
                    core_companies = theme_cfg[theme].get('core_companies', [])
                    if ts_code in core_companies:
                        theme_rank_bonus = 15  # 核心公司额外15分
        except:
            pass

    # ====================
    # V8.0 综合评分（优化版V3 — 聚焦二波启动模式）
    # ====================

    # 第一部分：基础线性评分（权重降低，让二波加分成为区分关键）
    base_score = (
        trend_strength * 18 +           # 趋势强度 — 核心因子
        trend_probability * 9 +
        money_momentum * 16 +           # 资金动量 — 重要
        breakout_strength * 6 +
        volume_explosion * 9 +
        trend_stability * 4 +
        compression_score * 7 +         # 压缩度（震荡调整的标志）
        momentum_score * 13 +           # 动量 — 体现二波启动
        squeeze_compression_score * 9 +  # 压缩洗盘 — 调整后启动前提
        mainline_resonance * 1 +
        position_factor * 6 +           
        leader_factor * 16              # 龙头因子
    )

    # 第二部分：因子交互共振项
    synergy_bonus = (
        compression_score * leader_factor * 7 +
        squeeze_compression_score * volume_explosion * 9 +
        leader_factor * max(theme_strength_bonus - 1, 0) * 15
    )

    # 第三部分：二波启动模式识别（用户偏好：拉升过→震荡调整→第二波刚启动）
    close_series = df['close']
    high_series = df['high']
    hhv60_s = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(high_series.tail(60).max())
    llv60_s = float(close_series.tail(60).min())
    hhv20_s = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(high_series.tail(20).max())
    llv20_s = float(close_series.tail(20).min())
    
    close_price = float(close_series.iloc[-1])
    first_wave_height = (hhv60_s - llv60_s) / max(llv60_s, 0.01)  # 60日第一波高度
    pullback_pct = 1 - close_price / max(hhv20_s, 0.01)           # 距20日高回撤
    pos60 = (close_price - llv60_s) / max(hhv60_s - llv60_s, 0.01)  # 60日位置
    
    # 判断是否为"第二波刚启动"（最多加25分）
    second_wave_bonus = 0
    # 条件1：有过明显的拉升（第一波15%+）
    if first_wave_height >= 0.15:
        second_wave_bonus += 6
        # 条件2：从高点回撤5-15%（震荡调整后蓄力）
        if pullback_pct >= 0.05 and pullback_pct <= 0.15:
            second_wave_bonus += 7
        # 条件3：60日位置在40-85%（非底非顶）
        if pos60 >= 0.40 and pos60 <= 0.85:
            second_wave_bonus += 6
        # 条件4：放量上涨3%+（启动迹象）
        if volume_explosion > 0.3 and today_pct > 3:
            second_wave_bonus += 6
    
    # 第四部分：非线性风险惩罚（适度，不压制高分）
    if fail_prob < 0.3:
        risk_penalty = fail_prob * 2
    elif fail_prob < 0.5:
        risk_penalty = 0.6 + (fail_prob - 0.3) * 6
    else:
        risk_penalty = 1.8 + (fail_prob - 0.5) * 10

    # 第五部分：主题置信度门控
    if theme_confidence < 30:
        confidence_gate = 0.85
    elif theme_confidence >= 70:
        confidence_gate = 1.05
    else:
        confidence_gate = 1.0

    # 汇总：直接加法
    v80_raw = (
        base_score 
        + synergy_bonus 
        + second_wave_bonus
        - risk_penalty
    )

    # 主题强化 + 置信度门控
    v75_total = v80_raw * confidence_gate

    # 确保范围
    v75_total = np.clip(v75_total, 0, 100)
    
    # =========================
    # 输出结果
    # =========================
    return {
        # V7技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(v7_result.get('洗盘概率', 0), 4),
        "交易优势": round(v7_result.get('交易优势', 0), 4),
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v7_result.get('风险等级', '低'),

        # V7复合指标
        "所属主题": theme,
        "主题纯度": round(theme_confidence, 2),
        "主题强化系数": round(theme_strength_bonus, 2),
        "主线共振加分": round(mainline_resonance, 2),
        "动量得分": round(momentum_score, 2),
        "压缩洗盘得分": round(squeeze_compression_score, 2),
        
        # V7.5独有指标
        "龙头因子": round(leader_factor, 3),
        "主题排名加成": round(theme_rank_bonus, 2),
        "位置因子": round(position_factor, 3),
        "涨跌幅": round(today_pct, 2),  # 添加涨跌幅给AI报告

        # V7.5总分
        "V7总评分": round(v75_total, 2)
    }


def calc_tli_score(theme, top_n=10, days=60):
    """
    TLI (Theme Life Index) 主题生命力评分
    
    核心逻辑：衡量主题在最近N天内的持续活跃程度
    高生命力 = 主题持续出现在市场前排，资金关注度高
    
    使用 Theme Alpha V6.2 引擎结果计算。
    
    参数：
        theme: 主题名称
        top_n: 前排定义（默认前10名）
        days: 统计天数（默认60天）
    
    返回：0-100 的主题生命力评分
    """
    try:
        if not theme:
            return 50, {"错误": "主题为空"}

        # 使用 V6 引擎结果
        v6_data = _load_v6_result(TRADE_DATE)
        if not v6_data:
            return 50, {"错误": "V6结果不存在或日期不匹配"}
        
        # 找到该主题
        theme_data = None
        rank = 0
        for i, r in enumerate(v6_data):
            if r.get('theme') == theme:
                theme_data = r
                rank = i + 1  # 按排序顺序作为排名
                break
        
        if not theme_data:
            return 50, {"说明": "V6中无该主题"}
        
        composite = theme_data.get('composite_score', 0)
        signal = theme_data.get('trade_signal', '')
        continuation = theme_data.get('continuation_score', 0)
        fa_score = theme_data.get('forward_alpha', 0)
        
        # 基于 V6.2 信号和综合分计算生命力
        # 强买=FA+热度共振，看多=FA预测强，关注=偏强，持有=稳定，看空/强烈看空=弱
        if signal == '强买':
            base_score = 70 + min(20, (composite - 60) * 0.5)
        elif signal == '看多':
            base_score = 60 + min(15, (fa_score - 50) * 0.4)
        elif signal == '关注':
            base_score = 55 + min(15, (composite - 50) * 0.5)
        elif signal == '持有':
            base_score = 50 + min(15, (composite - 45) * 0.5)
        elif signal in ('看空', '强烈看空', '回避'):
            base_score = max(15, composite * 0.3)
        else:
            base_score = max(20, composite * 0.4)
        
        # 延续分加分
        base_score += min(10, continuation * 0.1)
        
        # 排名加分
        rank_bonus = 0
        if rank <= 3:
            rank_bonus = 10
        elif rank <= 5:
            rank_bonus = 7
        elif rank <= 10:
            rank_bonus = 4
        
        tli_score = base_score + rank_bonus
        tli_score = min(100, max(0, tli_score))
        
        details = {
            "V6排名": rank,
            "综合分": round(composite, 1),
            "信号": signal,
            "延续分": round(continuation, 1),
        }
        
        return round(tli_score, 1), details
    
    except Exception as e:
        print(f"[TLI生命力] 计算失败: {e}")
        return 50, {"错误": str(e)}


def _get_stock_moneyflow_features(ts_code):
    """
    获取个股资金流向的60日行为特征 (缓存加速版)
    优先 moneyflow（标准接口），fallback moneyflow_ths（同花顺）
    返回: {'mf_slope': float, 'mf_persistence': float, 'mf_diffusion': float, 'mf_available': bool}
    """
    result = {'mf_slope': 0, 'mf_persistence': 0, 'mf_diffusion': 0.5, 'mf_available': False}
    if not ts_code:
        return result
    safe_name = ts_code.replace('.', '_')
    cache_path = os.path.join(MONEYFLOW_STOCK_DIR, f"{safe_name}.csv")
    try:
        mf_df = None
        if os.path.exists(cache_path):
            mf_age = time.time() - os.path.getmtime(cache_path)
            if mf_age < 86400 * 3:  # 3天内有效
                mf_df = pd.read_csv(cache_path)
        if mf_df is None:
            # 优先用 moneyflow（标准接口，大单/中单/小单）
            mf_df = pro.moneyflow(ts_code=ts_code,
                                  start_date=(datetime.now() - timedelta(days=60)).strftime("%Y%m%d"),
                                  end_date=TRADE_DATE)
            if mf_df is None or len(mf_df) == 0:
                # fallback: moneyflow_ths（同花顺，特大单/大单/小单）
                mf_df = pro.moneyflow_ths(ts_code=ts_code,
                                          start_date=(datetime.now() - timedelta(days=60)).strftime("%Y%m%d"),
                                          end_date=TRADE_DATE)
            if mf_df is not None and len(mf_df) > 0:
                mf_df.to_csv(cache_path, index=False, encoding='utf-8-sig')
        if mf_df is not None and len(mf_df) >= 10:
            mf_df = mf_df.sort_values("trade_date").reset_index(drop=True)
            # 判断数据源类型：moneyflow 有 buy_lg_amount（大单金额）；moneyflow_ths 有 buy_elg_amount（特大单金额）
            is_standard = 'buy_lg_amount' in mf_df.columns and 'sell_lg_amount' in mf_df.columns
            if is_standard:
                net_lg = (mf_df['buy_lg_amount'] - mf_df['sell_lg_amount']).values
                # 中单+小单作为非机构资金
                sm_cols = []
                if 'buy_md_amount' in mf_df.columns:
                    sm_cols.extend(['buy_md_amount', 'sell_md_amount'])
                if 'buy_sm_amount' in mf_df.columns:
                    sm_cols.extend(['buy_sm_amount', 'sell_sm_amount'])
                if sm_cols:
                    net_sm = mf_df[sm_cols[0::2]].sum(axis=1).values - mf_df[sm_cols[1::2]].sum(axis=1).values
                else:
                    net_sm = np.zeros(len(mf_df))
            else:
                # moneyflow_ths: 返回 buy_lg_amount(大单净额), buy_md_amount(中单净额),
                # buy_sm_amount(小单净额), net_amount(总净额), 均为净值(负=流出)
                if 'net_amount' in mf_df.columns:
                    net_lg = mf_df['buy_lg_amount'].values if 'buy_lg_amount' in mf_df.columns else np.zeros(len(mf_df))
                    # 中单归入机构端
                    if 'buy_md_amount' in mf_df.columns:
                        net_lg = net_lg + mf_df['buy_md_amount'].values
                    net_sm = mf_df['buy_sm_amount'].values if 'buy_sm_amount' in mf_df.columns else np.zeros(len(mf_df))
                else:
                    return result
            net_total = net_lg + net_sm
            n = min(20, len(net_total))
            net_20 = net_total[-n:]
            # Slope: 线性回归标准化
            if n >= 5 and np.std(net_20) > 0:
                slope, _ = np.polyfit(np.arange(n), net_20, 1)
                result['mf_slope'] = slope / (np.std(net_20) + 1e-12) * 10
            # Persistence: 最长连续净流入
            streak, max_s = 0, 0
            for v in net_20:
                if v > 0: streak += 1; max_s = max(max_s, streak)
                else: streak = 0
            result['mf_persistence'] = max_s / n
            # Diffusion: 机构主导度
            lg_abs = np.abs(net_lg[-n:]).sum() + 1e-12
            sm_abs = np.abs(net_sm[-n:]).sum() + 1e-12
            result['mf_diffusion'] = lg_abs / (lg_abs + sm_abs)
            result['mf_available'] = True
    except Exception:
        pass
    return result


def calc_unified_stock_score(df, ts_code='', theme='', theme_trend_score=0, theme_sentiment_score=0,
                             mainline_type='', mainline_quality=0, extra_mult=1.0):
    """
    V11: 主线分级系数 + 拆天花板 + 统一软顶
    =====================================
    在 V10 幻方风格基础上升级三点：
      1. 主题层级系数：主线核心 > 次主线 > 轮动 > 非主线（避免轮动周期股拿满分）
      2. 软天花板：S型压缩到 0~97，100分不再扎堆，排名有差异化
      3. extra_mult：外部乘数（如共振系数）统一纳入软顶计算，避免硬顶扎堆

    FinalScore = (base_raw + failure_bonus) × theme_mult × extra_mult → 软天花板
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
            return 0, "数据不足", {}, 50

        df = df.reset_index(drop=True)
        C = df['close'].values
        ts_code = ts_code or ''

        close_series = df['close']
        high_series = df['high']
        MA5 = float(close_series.rolling(5).mean().iloc[-1])
        MA10 = float(close_series.rolling(10).mean().iloc[-1])
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        HHV20 = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(close_series.tail(20).max())
        HHV60 = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(close_series.tail(60).max())
        LLV20 = float(close_series.tail(20).min())
        current_price = float(C[-1])
        today_pct = float((C[-1] / C[-2] - 1) * 100) if len(C) >= 2 else 0

        # ──────────────────────────────────────────────
        # 1. 动量爆发力 Momentum Power (35%)
        # ──────────────────────────────────────────────
        momentum_score = 50

        # 1a. MA20 斜率加速度 (8.75%) — 从"斜率水平"改为"斜率变化"
        if len(C) >= 30:
            ma20_now = float(close_series.rolling(20).mean().iloc[-1])
            ma20_5ago = float(close_series.rolling(20).mean().iloc[-6])
            ma20_10ago = float(close_series.rolling(20).mean().iloc[-11])
            slope_now = (ma20_now - ma20_5ago) / ma20_now if ma20_now > 0 else 0
            slope_prev = (ma20_5ago - ma20_10ago) / ma20_5ago if ma20_5ago > 0 else 0
            ma20_accel = slope_now - slope_prev  # 正值=刚翘头=最强爆发信号
            if ma20_accel > 0.02:
                momentum_score += 30  # MA20 明显翘头
            elif ma20_accel > 0.008:
                momentum_score += 20  # MA20 开始翘头
            elif ma20_accel > 0:
                momentum_score += 10  # 微幅加速
            elif ma20_accel > -0.008:
                momentum_score += 0   # 基本持平
            else:
                momentum_score -= 12  # 趋势走平/走弱

        # 1b. MA10 斜率加速度 (7%) — 短线加速
        if len(C) >= 20:
            ma10_now = float(close_series.rolling(10).mean().iloc[-1])
            ma10_5ago = float(close_series.rolling(10).mean().iloc[-6])
            ma10_10ago = float(close_series.rolling(10).mean().iloc[-11])
            s_now = (ma10_now - ma10_5ago) / ma10_now if ma10_now > 0 else 0
            s_prev = (ma10_5ago - ma10_10ago) / ma10_5ago if ma10_5ago > 0 else 0
            ma10_accel = s_now - s_prev
            if ma10_accel > 0.03:
                momentum_score += 25
            elif ma10_accel > 0.012:
                momentum_score += 16
            elif ma10_accel > 0:
                momentum_score += 8
            elif ma10_accel > -0.012:
                momentum_score -= 3
            else:
                momentum_score -= 10

        # 1c. ret_5 动量正向打分 (7%) — 从均值回归改为动量导向
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if ret_5 > 15:
                momentum_score += 25  # 强者恒强
            elif ret_5 > 8:
                momentum_score += 18  # 强势
            elif ret_5 > 3:
                momentum_score += 10  # 温和上涨
            elif ret_5 > -3:
                momentum_score += 3   # 横盘
            elif ret_5 > -10:
                momentum_score -= 5   # 回调=弱势
            else:
                momentum_score -= 15  # 大跌=无爆发力

        # 1d. 二阶加速度 ret_accel (7%) — 近端涨幅加速度
        if len(C) >= 11:
            ret_3 = (C[-1] / C[-4] - 1) * 100  # 近3日
            ret_7ago = (C[-4] / C[-11] - 1) * 100  # 前7日(不含近3日)
            ret_accel = ret_3 - ret_7ago  # 正值=短线正在加速
            if ret_accel > 8:
                momentum_score += 25
            elif ret_accel > 4:
                momentum_score += 18
            elif ret_accel > 1:
                momentum_score += 10
            elif ret_accel > -2:
                momentum_score += 3
            else:
                momentum_score -= 8

        # 1e. 突破强度 (5.25%) — 突破前高质量+量能
        dist_to_h20 = (HHV20 - current_price) / HHV20 if HHV20 > 0 else 0
        if current_price >= HHV20:
            breakout_power = 1.0
        elif dist_to_h20 <= 0.03:
            breakout_power = 0.85
        elif dist_to_h20 <= 0.08:
            breakout_power = 0.6
        elif dist_to_h20 <= 0.15:
            breakout_power = 0.3
        else:
            breakout_power = 0
        if breakout_power > 0 and len(C) >= 5:
            vol_recent = float(df['vol'].iloc[-5:].mean()) if 'vol' in df.columns else 0
            vol_prev = float(df['vol'].iloc[-15:-5].mean()) if 'vol' in df.columns and len(df) >= 15 else vol_recent
            vol_ratio_5_15 = vol_recent / (vol_prev + 1e-6) if vol_prev > 0 else 1.0
            if breakout_power >= 0.85 and vol_ratio_5_15 > 1.3:
                momentum_score += 20  # 放量突破=最强爆发信号
            elif breakout_power >= 0.85:
                momentum_score += 10  # 突破但量能一般
            elif breakout_power >= 0.6:
                momentum_score += 5   # 临近突破
        momentum_score = min(100, max(0, momentum_score))

        # ──────────────────────────────────────────────
        # 2. 资金行为 Capital Flow (25%)
        # ──────────────────────────────────────────────
        capital_score = 50

        # 2a. 量能趋势 (5%) — 连续放量天数 + 近期均量趋势
        if 'vol' in df.columns:
            vol_arr = df['vol'].values
            vol_hist = df['vol'].iloc[:-1]
            vol_ma5 = float(vol_hist.tail(5).mean()) if len(vol_hist) >= 5 else float(vol_arr[-1])
            vol_ma20 = float(vol_hist.tail(20).mean()) if len(vol_hist) >= 20 else vol_ma5
            vol_ma60 = float(vol_hist.tail(60).mean()) if len(vol_hist) >= 60 else vol_ma20
            vol_ratio = float(vol_arr[-1]) / vol_ma5 if vol_ma5 > 0 else 1.0
            vol_ratio_5_20 = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
            vol_ratio_20_60 = vol_ma20 / vol_ma60 if vol_ma60 > 0 else 1.0

            # 连续放量天数（近5日 vs 前5日不重叠）
            consec_vol_up = 0
            for i in range(1, 6):
                if len(vol_arr) > i * 2 + 5:
                    cur_5 = vol_arr[-(i*2+5):-(i*2)]
                    prev_5 = vol_arr[-(i*2+10):-(i*2+5)]
                    if cur_5.mean() > prev_5.mean() * 1.1:
                        consec_vol_up += 1
            # 量能趋势评分
            if consec_vol_up >= 3 and vol_ratio_20_60 > 1.5:
                capital_score += 25  # 持续堆量 = 大资金进场
            elif consec_vol_up >= 2 and vol_ratio_20_60 > 1.3:
                capital_score += 18
            elif vol_ratio_20_60 > 1.5:
                capital_score += 12  # 长期量能放大
            elif vol_ratio_20_60 > 1.2:
                capital_score += 6
            elif vol_ratio_20_60 > 1.0:
                capital_score += 2
            elif vol_ratio_20_60 > 0.7:
                capital_score -= 5   # 量能萎缩
            else:
                capital_score -= 15  # 严重缩量

            # 当日量比强化
            if vol_ratio > 3.0:
                capital_score += 10
            elif vol_ratio > 2.0:
                capital_score += 6
            elif vol_ratio > 1.3:
                capital_score += 3

        # 2b. 量价关系 (5%) — 上涨日量能 vs 下跌日量能
        if len(C) >= 20:
            up_vols, down_vols = [], []
            for i in range(1, min(20, len(C))):
                v = float(df['vol'].iloc[-i]) if 'vol' in df.columns else 0
                if C[-i] > C[-i-1]:
                    up_vols.append(v)
                elif C[-i] < C[-i-1]:
                    down_vols.append(v)
            up_avg_vol = np.mean(up_vols) if up_vols else 0
            down_avg_vol = np.mean(down_vols) if down_vols else 0
            vol_price_ratio = up_avg_vol / (down_avg_vol + 1e-6) if down_avg_vol > 0 else 1.0
            if vol_price_ratio > 1.5:
                capital_score += 10
            elif vol_price_ratio > 1.2:
                capital_score += 5

        # 2c. 同花顺资金流向特征 (10%) — 斜率/持续性/扩散率
        mf = _get_stock_moneyflow_features(ts_code)
        mf_avail = mf.get('mf_available', False)
        if mf_avail:
            ms = mf.get('mf_slope', 0)
            mp = mf.get('mf_persistence', 0)
            md = mf.get('mf_diffusion', 0.5)
            # 斜率: 正值机构持续流入
            if ms > 2.0:
                capital_score += 20
            elif ms > 1.0:
                capital_score += 14
            elif ms > 0.3:
                capital_score += 7
            elif ms > -0.3:
                capital_score += 0
            elif ms > -1.0:
                capital_score -= 5
            else:
                capital_score -= 12
            # 持续性: 连续净流入越长越好
            if mp >= 0.4:
                capital_score += 12
            elif mp >= 0.25:
                capital_score += 7
            elif mp >= 0.15:
                capital_score += 3
            # 扩散率: 机构主导度 (0-1, >0.6=机构主导)
            if md > 0.7:
                capital_score += 8
            elif md > 0.6:
                capital_score += 4
            elif md < 0.35:
                capital_score -= 5  # 散户主导=弱

        # 2d. 机构资金流 (5%) — 保留原 calc_institutional_flow_score
        inst_flow_score = calc_institutional_flow_score(ts_code)
        capital_score += inst_flow_score * 3

        capital_score = min(100, max(0, capital_score))

        # ──────────────────────────────────────────────
        # 3. 位置安全性 Position Safety (15%)
        # ──────────────────────────────────────────────
        position_score = 50

        # 3a. 距20日高点距离
        if current_price >= HHV20:
            # 创新高：检查是否连续新高+量能配合
            is_consec_high = False
            if len(C) >= 5:
                _high_hist = high_series.iloc[:-1]
                _high_20_ma = _high_hist.rolling(20, min_periods=1).max()
                _is_high_list = _high_hist >= _high_20_ma
                _cd = 0
                for ii in range(min(5, len(_is_high_list))):
                    if _is_high_list.iloc[-(ii+1)]:
                        _cd += 1
                    else:
                        break
                is_consec_high = _cd >= 2
            # 量能确认
            vol_strong = False
            if 'vol' in df.columns and len(df) >= 10:
                _v5 = float(df['vol'].iloc[-6:-1].mean())
                _v15 = float(df['vol'].iloc[-16:-6].mean()) if len(df) >= 16 else _v5
                vol_strong = _v5 > _v15 * 1.2

            if is_consec_high:
                if vol_strong:
                    position_score += 20  # 连续新高+放量=最强龙头信号
                else:
                    position_score += 5   # 连续新高但缩量=谨慎
            else:
                position_score += 15  # 第一天新高=突破形态
        elif dist_to_h20 <= 0.03:
            position_score += 12  # 极接近前高
        elif dist_to_h20 <= 0.08:
            position_score += 8
        elif dist_to_h20 <= 0.15:
            position_score += 4
        elif dist_to_h20 <= 0.25:
            position_score += 2

        # 3b. 从低点涨幅 (安全垫)
        run_up = (current_price - LLV20) / LLV20 if LLV20 > 0 else 0
        if run_up <= 0.15:
            position_score += 12
        elif run_up <= 0.25:
            position_score += 6

        # 3c. 90日振幅压缩度 (蓄势充分)
        if len(df) >= 90:
            range90 = (df['high'].values[-90:].max() - df['low'].values[-90:].min()) / df['low'].values[-90:].min()
            if range90 < 0.25:
                position_score += 8

        # 3d. 120日新高惩罚：仅缩量连续新高才惩罚
        if len(C) >= 120:
            HHV120 = float(high_series.iloc[:-1].tail(120).max())
            if current_price >= HHV120:
                is_consec_high_120 = False
                _h120 = high_series.iloc[:-1]
                _h120_ma = _h120.rolling(20, min_periods=1).max()
                _cd120 = 0
                for ii in range(min(3, len(_h120))):
                    if _h120.iloc[-(ii+1)] >= _h120_ma.iloc[-(ii+1)]:
                        _cd120 += 1
                    else:
                        break
                if _cd120 >= 2:
                    # 连续120日新高+缩量=风险
                    if 'vol' in df.columns and len(df) >= 10:
                        _v5 = float(df['vol'].iloc[-6:-1].mean())
                        _v15 = float(df['vol'].iloc[-16:-6].mean()) if len(df) >= 16 else _v5
                        if _v5 <= _v15 * 1.1:
                            position_score -= 8
        position_score = min(100, max(0, position_score))

        # ──────────────────────────────────────────────
        # 4. 热度持续性 Hotness (10%) — 从20%降至10%
        # ──────────────────────────────────────────────
        hot_score = 50
        tli_score, _ = calc_tli_score(theme, top_n=10, days=60)
        hot_score += (tli_score - 50) * 0.2

        hot_rank_bonus, best_rank, hot_appear_count = get_hot_list_best_rank_bonus(ts_code, days=60)
        hot_score += hot_rank_bonus * 0.3

        if theme:
            try:
                v6_data = _load_v6_result(TRADE_DATE)
                if v6_data:
                    for r in v6_data:
                        if r.get('theme') == theme:
                            signal = r.get('trade_signal', '')
                            stage = r.get('stage', '')
                            if signal in ('回避', '看空', '强烈看空') or stage in ('高潮', '衰退'):
                                hot_score -= 15
                            break
            except Exception:
                pass

        if theme_sentiment_score > 80:
            hot_score -= 15
        elif theme_sentiment_score > 70:
            hot_score -= 8
        elif theme_sentiment_score < 30:
            hot_score -= 8

        if theme_trend_score < 30:
            hot_score -= 12
        elif theme_trend_score < 40:
            hot_score -= 5
        elif theme_trend_score > 70:
            hot_score += 5
        hot_score = min(100, max(0, hot_score))

        # ──────────────────────────────────────────────
        # 5. 基本面 Fundamentals (15%)
        # ──────────────────────────────────────────────
        try:
            fund_result = calc_fundamental_score_v3(
                ts_code=ts_code, theme_name=theme,
                theme_trend_score=theme_trend_score,
                theme_sentiment_score=theme_sentiment_score,
                hot_rank=best_rank if best_rank <= 100 else 9999,
                hot_count=hot_appear_count
            )
            fundamental_score = fund_result['base_score']
            synergy_coeff = fund_result['synergy_coeff']
            fund_logic = fund_result['logic']
        except Exception:
            fundamental_score = 50
            synergy_coeff = 1.0
            fund_logic = []

        fundamental_score = min(100, max(0, fundamental_score))

        # ──────────────────────────────────────────────
        # 6. 追高惩罚（精简版）
        # ──────────────────────────────────────────────
        penalty = 0
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            ret_10 = (C[-1] / C[-11] - 1) * 100 if len(C) >= 11 else 0
            # 仅对同时满足"涨幅过大+量能萎缩/乖离过大"才惩罚
            if ret_5 > 15:
                bias_to_ma20 = (C[-1] - MA20) / MA20 * 100 if MA20 > 0 else 0
                if bias_to_ma20 > 25:
                    penalty += 20  # 严重乖离+大涨
                elif ret_5 > 25:
                    penalty += 15  # 纯大涨
                elif ret_10 > 35:
                    penalty += 10  # 10日过热
                elif bias_to_ma20 > 15:
                    penalty += 5

        # 长上影惩罚（保留）
        upper_shadow_penalty = 0
        if 'high' in df.columns and len(df) >= 2:
            today_high = float(df['high'].iloc[-1])
            upper_shadow_pct = (today_high - current_price) / current_price * 100
            if today_pct > 10 and upper_shadow_pct > 10:
                upper_shadow_penalty = 20
            elif today_pct > 5 and upper_shadow_pct > 7:
                upper_shadow_penalty = 12
            elif upper_shadow_pct > 4:
                upper_shadow_penalty = 8
        penalty += upper_shadow_penalty

        # ──────────────────────────────────────────────
        # 7. 龙头/辨识度加分
        # ──────────────────────────────────────────────
        leader_bonus = 0
        if current_price >= HHV20 * 0.97:
            if 'vol' in df.columns:
                _v5 = float(df['vol'].iloc[-6:-1].mean()) if len(df) > 6 else 0
                _v15 = float(df['vol'].iloc[-16:-6].mean()) if len(df) > 16 else _v5
                if _v5 > _v15 * 1.2:
                    leader_bonus = 12  # 放量近高点=龙头
                else:
                    leader_bonus = 6
            else:
                leader_bonus = 6

        # YRI-H 历史辨识度
        recognition_bonus = 0
        yri_h_score = 0
        yri_h_tags = []
        if ts_code:
            try:
                yri_result = calc_yri_history(ts_code, debug=False)
                if isinstance(yri_result, dict) and "错误" not in yri_result:
                    yri_h_score = float(yri_result.get("YRI历史总分", 0))
                    yri_h_tags = yri_result.get("核心历史标签", [])
                    recognition_bonus = (yri_h_score / 100) * 8
            except Exception:
                pass

        # ──────────────────────────────────────────────
        # 8. 综合得分
        # ──────────────────────────────────────────────
        base_score = (
            capital_score * 0.35 +
            position_score * 0.25 +
            hot_score * 0.15 +
            fundamental_score * 0.25
        )
        # 动量爆发力作为乘数 + 龙头加分 - 惩罚 + 辨识度
        momentum_mult = 0.7 + (momentum_score / 100) * 0.6  # 0.7 ~ 1.3
        synergy_bonus = (synergy_coeff - 0.8) * 25
        base_raw = base_score * momentum_mult + synergy_bonus - penalty + leader_bonus + recognition_bonus

        if momentum_score >= 80:
            base_raw += 6
        elif momentum_score >= 65:
            base_raw += 3
        elif momentum_score < 40:
            base_raw -= 6
        base_raw = min(120, max(5, base_raw))  # 暂存，后面还要乘主题系数

        # ──────────────────────────────────────────────
        # 9. 失败概率（幻方风格：动量越强失败越低）
        # ──────────────────────────────────────────────
        failure_prob = 50
        failure_prob -= (momentum_score - 50) * 0.30   # 动量爆发力 权重最大
        failure_prob -= (capital_score - 50) * 0.35    # 资金行为
        failure_prob -= (position_score - 50) * 0.18   # 位置
        failure_prob -= (hot_score - 50) * 0.10        # 热度（降低权重）
        failure_prob -= (fundamental_score - 50) * 0.12

        # 量能强化
        if 'vol' in df.columns and len(df) >= 10:
            vol_hist = df['vol'].iloc[:-1]
            vm5 = float(vol_hist.tail(5).mean())
            vm20 = float(vol_hist.tail(20).mean()) if len(vol_hist) >= 20 else vm5
            vrr = vm5 / vm20 if vm20 > 0 else 1.0
            if vrr > 1.5:
                failure_prob -= 10
            elif vrr > 1.2:
                failure_prob -= 5
            elif vrr < 0.6:
                failure_prob += 12
            elif vrr < 0.8:
                failure_prob += 5

        # 资金流向强化
        if mf_avail:
            ms = mf.get('mf_slope', 0)
            mp = mf.get('mf_persistence', 0)
            if ms > 1.5 and mp > 0.3:
                failure_prob -= 10  # 机构持续流入=低失败
            elif ms < -1.5:
                failure_prob += 10

        failure_prob += penalty * 1.2
        if hot_score >= 85:
            failure_prob += 8
        failure_prob = min(90, max(10, failure_prob))

        failure_bonus = (30 - failure_prob) * 0.4
        after_bonus = base_raw + failure_bonus

        # ── V11: 主题层级系数（主线分级 V1.0 集成）──
        # 主线核心(≥85): ×1.10   强主线(80~84): ×1.05
        # 次主线(70~79): ×1.00   轮动主题(60~69): ×0.90
        # 非主线(<60):   ×0.80   无主题数据: ×1.00（中性，不惩罚）
        if mainline_quality > 0:
            if mainline_quality >= 85:
                theme_mult = 1.10
            elif mainline_quality >= 80:
                theme_mult = 1.05
            elif mainline_quality >= 70:
                theme_mult = 1.00
            elif mainline_quality >= 60:
                theme_mult = 0.90
            else:
                theme_mult = 0.80
        else:
            theme_mult = 1.00

        after_theme = after_bonus * theme_mult * extra_mult

        # ── V11: 主题层级天花板（轮动股信号再强也不能超过主线高度）──
        # 核心主线(≥90): 上限 97   强主线(80-89): 上限 95
        # 次主线(70-79): 上限 90   轮动主题(60-69): 上限 85
        # 非主线(<60):   上限 78   无主题数据: 不设限（兼容旧数据）
        if mainline_quality > 0:
            if mainline_quality >= 90:
                tier_cap = 97.0
            elif mainline_quality >= 80:
                tier_cap = 95.0
            elif mainline_quality >= 70:
                tier_cap = 90.0
            elif mainline_quality >= 60:
                tier_cap = 85.0
            else:
                tier_cap = 78.0
        else:
            tier_cap = 97.0  # 无数据不限制

        # ── V11: 软天花板（S型压缩到 5~tier_cap，高分段拉开差异）──
        # soft_cap(x) = tier_cap - (tier_cap * 0.31) / (1 + exp(0.14 * (x - 80)))
        final_score = after_theme
        if final_score > 70:
            import math
            # S型压缩，上限为 tier_cap；压缩量 = tier_cap × 0.31（约30%的压缩空间）
            compress_range = tier_cap * 0.31
            final_score = tier_cap - compress_range / (1.0 + math.exp(0.14 * (final_score - 80.0)))
        final_score = max(5.0, min(tier_cap, final_score))

        # ──────────────────────────────────────────────
        # 10. 推荐理由
        # ──────────────────────────────────────────────
        reason_parts = []
        if momentum_score >= 80:
            reason_parts.append("🚀爆发力强")
        elif momentum_score >= 60:
            reason_parts.append("动量良好")

        if capital_score >= 80:
            reason_parts.append("资金充沛")
        elif capital_score >= 60:
            reason_parts.append("资金健康")

        if position_score >= 75:
            reason_parts.append("位置安全")
        elif position_score >= 55:
            reason_parts.append("位置合理")

        if leader_bonus >= 10:
            reason_parts.append("👑龙头")
        elif leader_bonus >= 5:
            reason_parts.append("⭐核心")

        if penalty > 8:
            reason_parts.append(f"⚠️追高-{penalty:.0f}")
        if yri_h_tags:
            reason_parts.append("/".join(yri_h_tags[:2]))
        recommendation = " | ".join(reason_parts) if reason_parts else "观察中"

        # ──────────────────────────────────────────────
        # 11. 详细信息
        # ──────────────────────────────────────────────
        details = {
            '动量爆发力': round(momentum_score, 1),
            '资金行为': round(capital_score, 1),
            '位置安全性': round(position_score, 1),
            '热度': round(hot_score, 1),
            '基本面': round(fundamental_score, 1),
            '追高惩罚': round(penalty, 1),
            '龙头加分': leader_bonus,
            '辨识度加分': round(recognition_bonus, 1),
            'YRI总分': round(yri_h_score, 1),
            'YRI标签': ", ".join(yri_h_tags[:3]) if yri_h_tags else "",
            '量比': round(vol_ratio, 2) if 'vol' in df.columns else 0,
            '热榜最佳排名': best_rank if best_rank <= 100 else 0,
            '热榜上榜次数': hot_appear_count,
            '共振系数': round(synergy_coeff, 2),
            '主题层级系数': round(theme_mult, 3),
            '主线类型': mainline_type,
            '主线质量分': mainline_quality,
            '层级上限': tier_cap,
            '基础裸分': round(base_raw, 1),
            '基本面逻辑': fund_logic[:3] if fund_logic else [],
            '资金斜率': round(mf.get('mf_slope', 0), 2),
            '资金持续性': round(mf.get('mf_persistence', 0), 2),
            '资金扩散率': round(mf.get('mf_diffusion', 0), 3),
            'Alpha信号': '',
        }

        return round(final_score, 1), recommendation, details, round(failure_prob, 1)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0, "计算异常", {}, 50


def calc_position_factor(df):

    low120 = df["low"].tail(120).min()
    high120 = df["high"].tail(120).max()

    close = df["close"].iloc[-1]

    pos = (close - low120) / (high120 - low120)

    if pos < 0.30:
        score = 0.2

    elif pos < 0.50:
        score = 0.4

    elif pos < 0.70:
        score = 0.7

    elif pos < 0.90:
        score = 1.0

    else:
        score = 0.8

    return score


def _calc_momentum_score(df):
    """
    新增：动量评分，捕捉启动股
    """
    C = df['close']
    VOL = df['vol']

    score = 0

    # 1. 5日价格动量
    if len(C) >= 5:
        pct5 = (C.iloc[-1] / C.iloc[-5]) - 1
        if pct5 > 0.05:
            score += 0.3
        elif pct5 > 0.02:
            score += 0.2
        elif pct5 > 0:
            score += 0.1

    # 2. 20日趋势
    if len(C) >= 20:
        pct20 = (C.iloc[-1] / C.iloc[-20]) - 1
        if pct20 > 0.15:
            score += 0.25
        elif pct20 > 0.08:
            score += 0.15
        elif pct20 > 0:
            score += 0.05

    # 3. 量能配合
    if len(VOL) >= 5:
        vol_ratio = VOL.tail(5).mean() / (VOL.tail(20).mean() + 1e-6)
        if vol_ratio > 1.5:
            score += 0.2
        elif vol_ratio > 1.2:
            score += 0.1
        elif vol_ratio > 1.0:
            score += 0.05

    # 4. 突破近期高点
    if len(C) >= 10:
        recent_high = C.tail(10).max()
        if C.iloc[-1] >= recent_high * 0.98:
            score += 0.25

    # 归一化到0-1
    return np.clip(score, 0, 1)

def _calc_squeeze_compression(df, compression_score, squeeze_prob):
    """
    新增：压缩+洗盘统一建模
    组合压缩度和洗盘概率
    """
    # 压缩度和洗盘概率的乘积，重点是高压缩度 + 高洗盘概率 = 好启动机会
    score = 0

    # 组合得分
    if compression_score >= 0.8 and squeeze_prob >= 0.5:
        score += 0.4
    elif compression_score >= 0.6 and squeeze_prob >= 0.4:
        score += 0.3
    elif compression_score >= 0.5:
        score += 0.2

    # 加上两个指标平均
    avg = (compression_score + squeeze_prob) / 2
    score += avg * 0.5

    return np.clip(score, 0, 1)


def _find_best_theme(stock_info):
    """
    自动选择纯度最高的主题
    遍历所有主题配置，计算个股与每个主题的纯度，返回最高的主题名
    """
    if not stock_info:
        return ''

    try:
        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return ''

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        best_theme = ''
        best_confidence = 0

        # 遍历所有主题，找纯度最高的
        for theme_name in theme_cfg.keys():
            confidence = calc_theme_confidence(stock_info, theme_name)
            if confidence > best_confidence:
                best_confidence = confidence
                best_theme = theme_name

        return best_theme

    except Exception as e:
        print(f"[V7自动主题] 选择失败: {e}")
        return ''


def find_all_themes(stock_info, min_confidence=0.3):
    """
    查找股票所属的所有主题（支持多主题）
    
    Args:
        stock_info: 股票信息字典
        min_confidence: 最小置信度阈值，默认0.3
    
    Returns:
        所有匹配主题的字符串，用逗号分隔
    """
    if not stock_info:
        return ''

    try:
        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return ''

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        # 收集所有置信度超过阈值的主题
        matched_themes = []
        for theme_name in theme_cfg.keys():
            confidence = calc_theme_confidence(stock_info, theme_name)
            if confidence >= min_confidence:
                # 带上置信度信息
                matched_themes.append(f"{theme_name}({confidence:.2f})")

        # 按置信度降序排序
        matched_themes.sort(key=lambda x: float(x.split('(')[1].rstrip(')')), reverse=True)
        
        return ','.join(matched_themes) if matched_themes else ''

    except Exception as e:
        print(f"[V7多主题] 选择失败: {e}")
        return ''


def calc_tech_barrier_score(ts_code, pro=None):
    """
    技术壁垒评分（基本面+价格）— 同步 bull_scorer v2 优化逻辑

    从 fina_indicator 读取 ROE/毛利率/研发费用率，
    加入绝对值下限约束（毛利率<25%上限、ROE<12%上限等），
    防止"矮子里拔将军"。

    Returns:
        float: 0~10 分
    """
    try:
        # ── 一、基本面壁垒评分（0~7分） ──
        fund_score = 0.0
        details = {}

        if pro is None:
            try:
                pro = ts.pro_api(TUSHARE_TOKEN)
            except Exception:
                return 0

        fin_df = pro.fina_indicator(ts_code=ts_code)
        if fin_df is not None and len(fin_df) >= 4:
            fin_df = fin_df.sort_values('end_date', ascending=False).head(8)

            # ROE
            roe_val = fin_df.head(4)['roe'].mean() if 'roe' in fin_df.columns else 0
            roe_val = roe_val / 100 if roe_val > 1 else roe_val  # 统一为小数
            # 毛利率
            gm_val = fin_df.head(4)['grossprofit_margin'].mean() if 'grossprofit_margin' in fin_df.columns else 0
            gm_val = gm_val / 100 if gm_val > 1 else gm_val
            # 研发费用率
            rd_val = fin_df.head(4)['rd_expenses'].mean() if 'rd_expenses' in fin_df.columns else 0
            rd_val = rd_val / 100 if rd_val > 1 else rd_val
            # 经营利润率
            op_val = fin_df.head(4)['profit_margin'].mean() if 'profit_margin' in fin_df.columns else 0

            # ROE评分（0~2分）
            if roe_val > 0.20:      roe_s = 2.0
            elif roe_val > 0.15:    roe_s = 1.6
            elif roe_val > 0.12:    roe_s = 1.3
            elif roe_val > 0.10:    roe_s = 1.0
            elif roe_val > 0.08:    roe_s = 0.7
            elif roe_val > 0.05:    roe_s = 0.4
            else:                   roe_s = 0.0
            fund_score += roe_s
            details['roe'] = round(roe_val * 100, 1)

            # 毛利率评分（0~2分）— 同步 bull_scorer 绝对值下限约束
            if gm_val > 0.40:       gm_s = 2.0
            elif gm_val > 0.30:     gm_s = 1.7
            elif gm_val > 0.25:     gm_s = 1.3
            elif gm_val > 0.20:     gm_s = 1.0
            elif gm_val > 0.15:     gm_s = 0.6
            elif gm_val > 0.10:     gm_s = 0.3
            else:                   gm_s = 0.0
            fund_score += gm_s
            details['gross_margin'] = round(gm_val * 100, 1)

            # 研发费用率评分（0~1.5分）
            if rd_val > 0.15:       rd_s = 1.5
            elif rd_val > 0.10:     rd_s = 1.2
            elif rd_val > 0.06:     rd_s = 1.0
            elif rd_val > 0.03:     rd_s = 0.7
            elif rd_val > 0.01:     rd_s = 0.4
            else:                   rd_s = 0.0
            fund_score += rd_s
            details['rd_ratio'] = round(rd_val * 100, 1)

            # 经营利润率评分（0~1.5分）
            if op_val > 0.25:       op_s = 1.5
            elif op_val > 0.15:     op_s = 1.2
            elif op_val > 0.10:     op_s = 0.9
            elif op_val > 0.05:     op_s = 0.5
            else:                   op_s = 0.0
            fund_score += op_s
            details['profit_margin'] = round(op_val * 100, 1)

        # ── 绝对值下限约束（同步 bull_scorer） ──
        # 即使有行业排名，绝对值过低也不给高分
        cap = 7.0
        if 0 < gm_val < 0.15:
            cap = min(cap, 5.0)
        elif 0 < gm_val < 0.20:
            cap = min(cap, 6.0)
        if 0 < roe_val < 0.05:
            cap = min(cap, 5.0)
        elif 0 < roe_val < 0.08:
            cap = min(cap, 6.0)
        if 0 < rd_val < 0.01:
            cap = min(cap, 4.0)
        elif 0 < rd_val < 0.02:
            cap = min(cap, 5.5)

        fund_score = min(fund_score, cap)

        # ── 二、价格壁垒信号评分（0~3分） ──
        df = _get_daily_from_sqlite(ts_code)
        price_score = 0.0
        if df is not None and not df.empty and 'close' in df.columns:
            df = df.sort_values('trade_date', ascending=False)

            # 低波动加分（壁垒稳固）
            if 'pct_chg' in df.columns and len(df) >= 20:
                pct_vals = df['pct_chg'].head(60).dropna().astype(float)
                if len(pct_vals) >= 20:
                    vol = pct_vals.std()
                    vol_score = max(0, min(3, 3 * (4 - vol) / 2))
                else:
                    vol_score = 0
            else:
                vol_score = 0

            # 60日趋势加分（基本面向好）
            if len(df) >= 60:
                close_60d = float(df.iloc[min(59, len(df)-1)]['close'])
                close_now = float(df.iloc[0]['close'])
                ret_60d = (close_now - close_60d) / close_60d if close_60d > 0 else 0
                trend_boost = min(1.0, max(0, ret_60d * 3))
            else:
                trend_boost = 0

            price_score = min(3.0, vol_score + trend_boost)
            details['volatility'] = round(vol_score, 1)
            details['trend_boost'] = round(trend_boost, 1)

        total = round(min(10, fund_score + price_score), 1)
        details['fund_score'] = round(fund_score, 1)
        details['price_score'] = round(price_score, 1)
        details['cap'] = round(cap, 1)
        return total

    except Exception:
        return 0


def calc_institutional_flow_score(ts_code):
    """
    机构资金流评分
    从 moneyflow 缓存（优先）或实时调用获取大单数据

    Returns:
        float: 0~5 分
    """
    try:
        from datetime import datetime, timedelta
        # 优先从 per-stock 缓存读取（_get_stock_moneyflow_features 已缓存）
        safe_name = ts_code.replace('.', '_')
        stock_cache = os.path.join(MONEYFLOW_STOCK_DIR, f"{safe_name}.csv")
        if os.path.exists(stock_cache):
            mf_df = pd.read_csv(stock_cache)
            if len(mf_df) > 0:
                mf_df = mf_df.sort_values("trade_date").reset_index(drop=True)
                latest = mf_df.iloc[-1]
                if 'buy_lg_amount' in mf_df.columns:
                    buy_lg = float(latest.get('buy_lg_amount', 0))
                    sell_lg = float(latest.get('sell_lg_amount', 0))
                else:
                    buy_lg = float(latest.get('buy_lg_vol', 0))
                    sell_lg = float(latest.get('sell_lg_vol', 0))
                net_lg = buy_lg - sell_lg
                if net_lg > 1e6:
                    return 5
                elif net_lg > 0:
                    return 3
                else:
                    return 0
        # fallback: 从 moneyflow_{date}.csv 批量缓存获取
        for offset in range(5):
            check_date = (datetime.now() - timedelta(days=offset)).strftime('%Y%m%d')
            mf_path = os.path.join(CACHE_DIR, f"moneyflow_{check_date}.csv")
            if os.path.exists(mf_path):
                mf_df = pd.read_csv(mf_path)
                if ts_code in mf_df['ts_code'].values:
                    row = mf_df[mf_df['ts_code'] == ts_code].iloc[0]
                    buy_lg = float(row.get('buy_lg_vol', 0)) if pd.notna(row.get('buy_lg_vol')) else 0
                    sell_lg = float(row.get('sell_lg_vol', 0)) if pd.notna(row.get('sell_lg_vol')) else 0
                    net_lg = buy_lg - sell_lg
                    if net_lg > 1e6:
                        return 5
                    elif net_lg > 0:
                        return 3
                    else:
                        return 0
        return 0
    except Exception:
        return 0


def calc_fundamental_score_v3(ts_code, theme_name='', theme_trend_score=0, theme_sentiment_score=0,
                                stock_info=None, hot_rank=9999, hot_count=0):
    """
    V3: 真实基本面因子版 — 接入利润增速/ROE/半年度预告/大宗交易
    返回: 与 V2 格式一致
    """
    try:
        logic = []

        # ── PART A: 热榜+主题信号 (30%) ──
        trend_str = 50
        if theme_trend_score >= 80: trend_str = 95
        elif theme_trend_score >= 65: trend_str = 80
        elif theme_trend_score >= 45: trend_str = 65
        elif theme_trend_score >= 30: trend_str = 45
        else: trend_str = 25

        concent = 50
        if hot_count >= 5:
            concent = 95 if hot_rank <= 10 else (80 if hot_rank <= 30 else 65)
        elif hot_count >= 2:
            concent = 55
        else:
            concent = 40

        stage_score = 50; stage = "未知"
        if theme_sentiment_score >= 80: stage, stage_score = "高潮期", 55
        elif theme_sentiment_score >= 60: stage, stage_score = "发酵期", 75
        elif theme_sentiment_score >= 40: stage, stage_score = "启动期", 90
        else: stage, stage_score = "退潮期", 30

        emotion_heat = min(90, 30 + hot_count * 6)
        heat_base = trend_str * 0.25 + concent * 0.25 + stage_score * 0.25 + emotion_heat * 0.25

        # ── PART B: 真实基本面因子 (70%) ──
        cache_key = f"{ts_code}_fund_v3.json"
        cache_path = os.path.join(FUND_CACHE_DIR, cache_key)
        fund_data = {}
        if os.path.exists(cache_path):
            age = time.time() - os.path.getmtime(cache_path)
            if age < 86400 * 7:
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        fund_data = json.load(f)
                except: pass
        if not fund_data:
            try: fin_df = pro.fina_indicator(ts_code=ts_code, fields="ts_code,end_date,roe,ystz,sjlrtz")
            except: fin_df = None
            try: fc_df = pro.forecast(ts_code=ts_code, fields="ts_code,end_date,type,p_change_min,p_change_max")
            except: fc_df = None
            try: bt_df = pro.block_trade(ts_code=ts_code, fields="ts_code,trade_date,price,vol,amount,discount")
            except: bt_df = None
            fund_data = {'fin': {}, 'fc': {}, 'bt': {}}
            if fin_df is not None and len(fin_df) > 0:
                fin_df = fin_df.sort_values("end_date", ascending=False)
                lr = fin_df.iloc[0]
                fund_data['fin']['roe'] = float(lr.get('roe', 0)) if pd.notna(lr.get('roe', 0)) else 0
                fund_data['fin']['ystz'] = float(lr.get('ystz', 0)) if pd.notna(lr.get('ystz', 0)) else 0
                fund_data['fin']['sjlrtz'] = float(lr.get('sjlrtz', 0)) if pd.notna(lr.get('sjlrtz', 0)) else 0
            if fc_df is not None and len(fc_df) > 0:
                fc_df = fc_df.sort_values("end_date", ascending=False)
                # forecast.type 可能是字符串(如"预增")，映射为整数
                _forecast_type_map = {
                    '预增': 1, '预减': 2, '略增': 3, '略减': 4,
                    '扭亏': 5, '首亏': 6, '续亏': 7, '续盈': 8, '减亏': 9
                }
                def _to_fc_type(v):
                    if pd.isna(v):
                        return 0
                    if isinstance(v, str):
                        return _forecast_type_map.get(v, 0)
                    try:
                        return int(v)
                    except (ValueError, TypeError):
                        return 0
                for _, rr in fc_df.iterrows():
                    ed = str(rr.get('end_date', ''))
                    if '0630' in ed or '06-30' in ed:
                        fund_data['fc']['half_p_min'] = float(rr.get('p_change_min', 0)) if pd.notna(rr.get('p_change_min', 0)) else 0
                        fund_data['fc']['half_p_max'] = float(rr.get('p_change_max', 0)) if pd.notna(rr.get('p_change_max', 0)) else 0
                        fund_data['fc']['half_type'] = _to_fc_type(rr.get('type'))
                        break
                lst = fc_df.iloc[0]
                fund_data['fc']['p_min'] = float(lst.get('p_change_min', 0)) if pd.notna(lst.get('p_change_min', 0)) else 0
                fund_data['fc']['p_max'] = float(lst.get('p_change_max', 0)) if pd.notna(lst.get('p_change_max', 0)) else 0
                fund_data['fc']['type'] = _to_fc_type(lst.get('type'))
            if bt_df is not None and len(bt_df) > 0:
                bt_df = bt_df.sort_values("trade_date", ascending=False)
                if 'discount' in bt_df.columns:
                    dsc = bt_df.head(10)['discount'].dropna().values
                    fund_data['bt']['avg_discount'] = float(np.mean(dsc)) if len(dsc) > 0 else 0
                fund_data['bt']['count'] = len(bt_df.head(10))
            time.sleep(0.15)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(fund_data, f, ensure_ascii=False)

        # 因子①: 利润同比增速 (30%)
        sjlrtz = fund_data.get('fin', {}).get('sjlrtz', 0)
        ps = 50
        if sjlrtz > 100: ps = 95; logic.append(f"利润爆发: 同比+{sjlrtz:.0f}%")
        elif sjlrtz > 50: ps = 85; logic.append(f"利润高增: 同比+{sjlrtz:.0f}%")
        elif sjlrtz > 20: ps = 70; logic.append(f"利润良好: 同比+{sjlrtz:.0f}%")
        elif sjlrtz > 0: ps = 55; logic.append(f"利润微增: 同比+{sjlrtz:.0f}%")
        elif sjlrtz > -20: ps = 35; logic.append(f"利润下滑: {sjlrtz:.0f}%")
        else: ps = 20; logic.append(f"利润大降: {sjlrtz:.0f}%")

        # 因子②: 半年度预告加分 (25%)
        fc = fund_data.get('fc', {})
        fs = 50
        hp_min = fc.get('half_p_min', 0); hp_max = fc.get('half_p_max', 0)
        ht = fc.get('half_type', 0)
        if ht == 5:
            fs = 90; logic.append(f"半年度: 扭亏为盈({hp_min:.0f}%~{hp_max:.0f}%)")
        elif ht in (1, 6, 8):
            ap = (hp_min + hp_max) / 2
            if ap > 100: fs = 95; logic.append(f"半年度: 预增+{ap:.0f}%(超预期)")
            elif ap > 50: fs = 85; logic.append(f"半年度: 预增+{ap:.0f}%")
            elif ap > 20: fs = 75; logic.append(f"半年度: 预增+{ap:.0f}%")
            else: fs = 65; logic.append(f"半年度: 略增+{ap:.0f}%")
        elif ht in (2, 7): fs = 35; logic.append(f"半年度: 预减({hp_min:.0f}%~{hp_max:.0f}%)")
        elif ht in (3, 4): fs = 15; logic.append(f"半年度: 亏损预警")
        else:
            pm = fc.get('p_min', 0); px = fc.get('p_max', 0)
            if pm > 0 or px > 0:
                ap = (pm + px) / 2
                fs = 75 if ap > 50 else 60
                logic.append(f"最新预告: {'预增' if ap>0 else '预减'}+{ap:.0f}%")

        # 因子③: ROE_TTM (25%)
        roe = fund_data.get('fin', {}).get('roe', 0)
        rs = 50
        if roe > 25: rs = 95; logic.append(f"ROE: {roe:.1f}%(卓越)")
        elif roe > 18: rs = 85; logic.append(f"ROE: {roe:.1f}%(优秀)")
        elif roe > 12: rs = 70; logic.append(f"ROE: {roe:.1f}%(良好)")
        elif roe > 6: rs = 55; logic.append(f"ROE: {roe:.1f}%(一般)")
        elif roe > 0: rs = 40; logic.append(f"ROE: {roe:.1f}%(偏低)")
        else: rs = 25; logic.append(f"ROE: {roe:.1f}%(亏损)")

        # 因子④: 大宗交易折溢价 (20%)
        bt = fund_data.get('bt', {})
        bs = 50; bt_cnt = bt.get('count', 0)
        if bt_cnt >= 3:
            ad = bt.get('avg_discount', 0)
            if ad > 3: bs = 90; logic.append(f"大宗: 溢价{ad:.1f}%(抢筹)")
            elif ad > 0: bs = 75; logic.append(f"大宗: 小幅溢价{ad:.1f}%")
            elif ad > -5: bs = 60; logic.append(f"大宗: 折价{abs(ad):.1f}%(正常)")
            elif ad > -10: bs = 45; logic.append(f"大宗: 折价{abs(ad):.1f}%(偏大)")
            else: bs = 30; logic.append(f"大宗: 大幅折价{abs(ad):.1f}%(异常)")
        elif bt_cnt > 0: bs = 55
        else: bs = 50

        real_score = min(100, max(0, ps * 0.30 + fs * 0.25 + rs * 0.25 + bs * 0.20))
        combined = heat_base * 0.30 + real_score * 0.70

        has_data = sjlrtz != 0 or roe != 0 or bt_cnt > 0 or hp_min != 0 or fc.get('p_min', 0) != 0
        if has_data and real_score >= 70: sc = 1.20 + min(0.3, (real_score - 70) / 100)
        elif has_data and real_score >= 50: sc = 1.0 + (real_score - 50) / 100
        elif has_data: sc = 0.85
        else: sc = 0.75

        if theme_sentiment_score >= 85 and hot_count >= 5: sc *= 0.9
        sc = round(max(0.5, min(1.5, sc)), 2)

        return {
            "industry_score": round(heat_base, 1), "fundamental_score": round(real_score, 1),
            "base_score": round(combined, 1), "synergy_coeff": sc,
            "is_mainline": not (heat_base < 40 and real_score < 45),
            "stage": stage, "logic": logic
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"industry_score": 50, "fundamental_score": 50, "base_score": 50,
                "synergy_coeff": 1.0, "is_mainline": False, "stage": "未知", "logic": ["评分异常"]}


def calc_theme_confidence(stock_info, theme):
    """
    计算个股与主题的纯度/置信度评分（0-100）

    参数：
        stock_info: 股票信息字典，包含：
            - industries: 行业列表
            - concepts: 概念列表
            - business_text: 业务描述文本
            - name: 股票名称
        theme: 主题名称（如"AI算力链"、"半导体"等）

    返回：
        主题纯度评分（0-100）
    """
    if not stock_info or not theme:
        return 0

    score = 0

    # 行业匹配得分
    score += calc_industry_score(
        stock_info.get("industries", []),
        theme
    )

    # 概念匹配得分
    score += calc_concept_score(
        stock_info.get("concepts", []),
        theme
    )

    # 关键词命中得分
    score += calc_keyword_score(
        stock_info.get("business_text", ""),
        theme
    )

    # 业务相关性得分
    score += calc_business_score(
        stock_info.get("business_text", ""),
        theme
    )

    # 核心公司加分
    score += calc_core_company_score(
        stock_info.get("name", ""),
        theme
    )

    # 核心龙头股额外加分（对行业内具有核心地位的龙头公司给予加成）
    score += calc_leader_bonus(
        stock_info.get("name", ""),
        theme
    )

    # 排除惩罚
    score -= calc_penalty(
        stock_info.get("business_text", ""),
        theme
    )

    return max(min(score, 100), 0)


def calc_industry_score(industries, theme):
    """行业匹配得分（满分25）"""
    if not industries or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    target_industries = set(cfg.get("industry", []))

    for ind in industries:
        if ind in target_industries:
            score += 25
            break
        # 部分匹配
        for target in target_industries:
            if target in ind or ind in target:
                score += 15
                break

    return min(score, 25)


def calc_concept_score(concepts, theme):
    """概念匹配得分（满分25）"""
    if not concepts or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    target_concepts = set(cfg.get("concept", []))

    matched_count = 0
    for c in concepts:
        if c in target_concepts:
            matched_count += 1

    # 匹配数量越多，得分越高
    if matched_count >= 5:
        score = 25
    elif matched_count >= 3:
        score = 20
    elif matched_count >= 2:
        score = 15
    elif matched_count >= 1:
        score = 10

    return min(score, 25)


def calc_keyword_score(business_text, theme):
    """关键词命中得分（满分20）"""
    if not business_text or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    keywords = cfg.get("keywords", [])
    exclude_keywords = set(cfg.get("exclude_keywords", []))

    text_lower = business_text.lower()

    hit_count = 0
    for kw in keywords:
        if kw.lower() in text_lower:
            hit_count += 1

    # 根据命中数量计算得分
    if len(keywords) > 0:
        hit_rate = hit_count / len(keywords)
        score = hit_rate * 20

    return min(score, 20)


def calc_business_score(business_text, theme):
    """业务相关性得分（满分15）"""
    if not business_text or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 核心关键词
    core_keywords = cfg.get("keywords", [])[:5]  # 取前5个核心词

    text_lower = business_text.lower()
    core_hits = 0
    for kw in core_keywords:
        if kw.lower() in text_lower:
            core_hits += 1

    # 核心关键词命中给高分
    if core_hits >= 3:
        score = 15
    elif core_hits >= 2:
        score = 10
    elif core_hits >= 1:
        score = 5

    return min(score, 15)


def calc_core_company_score(stock_name, theme):
    """核心公司加分（满分10）- 从theme.json读取"""
    if not stock_name or not theme:
        return 0

    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 从theme.json获取核心公司列表
    companies = cfg.get("core_companies", [])
    for c in companies:
        if c in stock_name:
            return 10

    return 0


def calc_leader_bonus(stock_name, theme):
    """核心龙头股额外加分（满分15）- 从theme.json读取"""
    if not stock_name or not theme:
        return 0

    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 从theme.json获取核心龙头股列表（优先使用leader_companies，没有则用core_companies前3个）
    leader_companies = cfg.get("leader_companies", [])
    if not leader_companies:
        # 如果没有定义leader_companies，使用core_companies的前3个作为龙头
        core_list = cfg.get("core_companies", [])
        leader_companies = core_list[:3]

    for c in leader_companies:
        if c in stock_name:
            return 15

    return 0


def calc_penalty(business_text, theme):
    """排除惩罚（扣分项）"""
    if not business_text or not theme:
        return 0

    penalty = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    exclude_keywords = cfg.get("exclude_keywords", [])
    text_lower = business_text.lower()

    for kw in exclude_keywords:
        if kw.lower() in text_lower:
            penalty += 15

    return min(penalty, 30)  # 最多扣30分


def _get_theme_config(theme):
    """获取主题配置"""
    try:
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return None

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        return theme_cfg.get(theme, {})
    except Exception as e:
        print(f"[主题纯度] 配置读取失败: {e}")
        return None


def strategy(df, code, emotion_stage, total_mv=0):
    """优化版本：向量化计算 + 提前过滤 + 缓存复用"""
    
    # ===== 快速前置过滤（低成本判断优先）=====
    if len(df) < 80:
        return False
    
    # 总市值≥80亿
    if total_mv / 10000 < 80:
        return False
    
    # ST股票过滤（代码前缀判断，无需查询字典）
    if code.startswith('1') or code.startswith('2'):
        return False
    
    # 两个月涨幅过滤
    if len(df) >= 40:
        close_values = df['close'].values
        ret_2m = close_values[-1] / close_values[-40] - 1
        if ret_2m > 1.0:
            return False
    
    # ===== 数据提取（一次提取，多次使用）=====
    C = df['close'].values
    O = df['open'].values
    H = df['high'].values
    L = df['low'].values
    VOL = df['vol'].values
    
    # ===== 创业板/科创板判断（仅用于今日涨停过滤）=====
    # 主板：10% 涨停；双创板：20% 涨停
    IS_CYB_KCB = (code.startswith('3') or code.startswith('688') or code.startswith('689'))
    ZT_SINGLE_UP = 1.198 if IS_CYB_KCB else 1.098

    # ===== 快速过滤：今天已涨停 → 直接排除 =====
    if len(df) >= 3:
        today_ratio = C[-1] / C[-2]
        if today_ratio >= ZT_SINGLE_UP:
            return False

    # ST名称过滤（延后到这里，只在必要时调用）
    StockName = get_stock_name(code)
    ST1 = (StockName.upper().startswith('ST') or 
            StockName.upper().startswith('*ST'))
    if ST1:
        return False
    
    # ===== 启动过滤：60日振幅 =====
    if len(df) >= 20:
        hh = H[-20:].max()
        ll = L[-20:].min()
        if (hh / ll - 1) > 1.8:
            return False
    
    # ===== 均线计算（一次计算，多次使用）=====
    close_arr = df['close'].values if hasattr(df['close'], 'values') else np.asarray(df['close'])
    C_series = pd.Series(close_arr)
    ma5 = C_series.rolling(5).mean().values
    ma10 = C_series.rolling(10).mean().values
    ma20 = C_series.rolling(20).mean().values
    ma22 = C_series.rolling(30).mean().values
    ma60 = C_series.rolling(60).mean().values
    
    # 均线条件
    if C[-1] >= ma20[-1] * 1.3 or C[-1] / ma60[-1] > 2:
        return False
    
    # 股价必须站上5日、10日、20日均线
    if  C[-1] < ma20[-1] or ma10[-1] < ma60[-1]*0.97 or ma5[-1] < ma60[-1]*0.97:
        return False
    
    # ===== 涨停判断（向量化）=====
    ZT_1day = (C_series.shift(1) / C_series.shift(2) < 1.08) & (C_series / C_series.shift(1) > 1.098)
    ZT_2day = (C_series.shift(1) / C_series.shift(2) >= 1.051) & (C_series / C_series.shift(1) >= 1.051) & (C_series / C_series.shift(2) >= 1.11)
    ZT = ZT_1day | ZT_2day
    
    # 使用向量化的barslast
    ZTTS = barslast(ZT)
    
    # 原版逻辑：如果今天信号(ztts=0)，取前一个信号
    ztts = ZTTS.iloc[-1]
    if ztts == 0:
        ztts = ZTTS.iloc[-2]
    
    if np.isnan(ztts):
        return False
    
    ztts = int(ztts)
    
    # ===== 过滤：近5天累计涨幅超过20% = 乖离过大，跳过 =====
    if len(C) >= 6 and (C[-1] / C[-6] - 1) > 0.3:
        return False
    
    # ===== 条件1：ztts范围（距上一波高点的天数）=====
    if ztts < 3 or ztts > 90:
        return False
    
    # ===== 缓存ztts区间数据（避免重复切片）=====
    ztts_close = C[-ztts:]
    ztts_df = df.iloc[-ztts:]
    ztts_vol = ztts_df['vol'].values
    vol_ma5 = ztts_df['vol'].rolling(5).mean().values  # 只计算一次
    
    # ===== TJ条件判断 =====
    ref_close = C[-ztts-1]
    cond2 = (ztts_close < ref_close).sum() == 0
    cond3 = (ztts_close.max() / ztts_close.min()) < 1.3
    cond4 = (C[-1] / H[-ztts-1]) < 1.2  # 修复：H.shift(ztts).iloc[-1] = H[-ztts-1]
    cond5 = H[-ztts:].max() >= H[-120:].max() * 0.8
    cond6 = ma22[-1] >= ma22[-2]
    
    # 量能条件（复用vol_ma5）
    cond_low_vol = (ztts_vol < vol_ma5 * 0.9).any()
    
    # 回撤计算（向量化）
    cum_max = np.maximum.accumulate(ztts_close)
    drawdown = (ztts_close - cum_max) / cum_max
    cond_dd = drawdown.min() >= -0.15
    
    # 放量大跌判断（复用vol_ma5）
    down_k = ztts_df['close'].values < ztts_df['open'].values
    big_vol = ztts_vol > vol_ma5 * 1.5
    big_drop = ztts_df['pct_chg'].values < -5 if 'pct_chg' in ztts_df.columns else False
    cond_no_bad_k = ~(down_k & big_vol & big_drop).any()
    
    cond7 = cond_low_vol and cond_no_bad_k
    
    TJ = cond3 and cond4 and cond5 and cond6 
    if not TJ:
        return False
    
    # ===== XH 判断（区分主板/双创）=====
    is_main_board = code.startswith(('600', '601', '603', '000', '001', '002'))
    is_chip_venture = code.startswith(('300', '688','301'))
    
    highest_close = C[-ztts-1:-1].max()
    vol_peak = VOL[-ztts-1:-1].max()
    vol_condition = VOL[-1] >= vol_peak * 0.7 if vol_peak > 0 else True
    
    if is_main_board:
        # 主板做突破：放量突破近期高点 + 偏离MA5不过远
        cond_break1 = C[-1] > C[-2] and C[-1] > C[-3] and C[-1]/C[-2]>1.05 and vol_condition
        cond_break3 = C[-2] < highest_close and C[-1] >= highest_close and C[-1]/C[-2]<1.09
        cond_near_ma5 = C[-1] >= ma5[-1] * 0.97 and C[-1] / ma5[-1] < 1.11
        result = (cond_break1 or cond_break3) and cond_near_ma5
    elif is_chip_venture:
        # 双创做低吸：回踩MA20企稳 + 缩量 + 距涨停高点有空间
        dist_from_high = (highest_close - C[-1]) / highest_close
        cond_pullback = 0.03 < dist_from_high < 0.15                      # 从涨停高点回落3%-15%
        cond_ma_support = C[-1] >= ma20[-1] * 0.97 and C[-1] <= ma20[-1] * 1.15  # 在MA20附近
        cond_shrink_vol = VOL[-1] < vol_peak * 0.6 if vol_peak > 0 else True      # 缩量企稳
        cond_stable = abs(C[-1] / C[-2] - 1) < 0.04 and abs(C[-1] / C[-3] - 1) < 0.06  # 近2日波动温和
        result = cond_pullback and cond_ma_support and cond_shrink_vol and cond_stable
    else:
        # 其他（北交所等）使用原逻辑
        cond_xh1 = C[-1] > C[-2] and C[-1] > C[-3] and C[-1]/C[-2]>1.05 and vol_condition
        cond_xh3 = C[-2] < highest_close and C[-1] >= highest_close and C[-1]/C[-2]<1.09
        cond_near_ma5 = C[-1] >= ma5[-1] * 0.97 and C[-1] / ma5[-1] < 1.11
        result = (cond_xh1 or cond_xh3) and cond_near_ma5 
    return result


def deepseek(prompt, use_flash=True):
    """AI 报告生成（DeepSeek flash，无联网搜索，精简输出）
    Args:
        prompt: 提示词
        use_flash: True=用Flash快速模型（默认）
    """
    if not DEEPSEEK_API_KEY:
        print("⚠️ 无可用 AI API Key（DEEPSEEK_API_KEY 为空）")
        return ""

    # 清理 prompt 中的联网搜索要求，避免 DeepSeek 凭空编造数据
    try:
        from ai_prompt_cleaner import strip_web_search_requirements
        clean_prompt = strip_web_search_requirements(prompt)
    except Exception as e:
        print(f"⚠️ prompt 清理失败，使用原始 prompt: {e}")
        clean_prompt = prompt

    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    model = "deepseek-v4-flash" if use_flash else "deepseek-v4-pro"
    data = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股顶级投资分析师，严格基于用户提供的数据进行分析，"
                    "绝不编造任何数据、新闻、游资动向、龙虎榜信息或外部事件。"
                    "如果数据中没有某只股票的信息，就如实说明'数据不足'，绝不能凭空捏造。"
                    "股票名称和代码必须严格引用用户提供的数据，不得自行修改或臆造。"
                    "输出要求：精简精炼，每只股票分析不超过3句话，重点突出关键数据和结论。"
                )
            },
            {"role": "user", "content": clean_prompt}
        ],
        "temperature": 0.1,
    }
    r = requests.post(url, headers=headers, json=data, timeout=120)
    if r.status_code != 200:
        print(r.text)
        return ""
    return r.json()['choices'][0]['message']['content']


def _call_glm(prompt, use_flash=False):
    """智谱 GLM-5.2 API 调用（带联网搜索）
    - 自动抓取实时新闻、公告、研报补充分析
    - 搜索结果会作为 context 注入，最终输出整合到报告
    """
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json"
    }
    # 模型选择：flash=免费快速，否则=旗舰深度
    model = "glm-5.2"

    data = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股顶级投资分析师，必须调用联网搜索工具补充实时信息。\n"
                    "【效率优先-绝对不能超时】严格控制联网搜索次数，总搜索次数控制在5次以内，绝不超过。\n"
                    "- 重点核查对象：突破股池前3名 + 量能爆发池强买信号股 +ETF成份股，每只最多1次搜索\n"
                    "- 其他股票（突破股池4-10名）：不单独联网搜索，直接输出'风险舆情：数据待补充'，把搜索配额留给重点股\n"
                    "【最高优先级-数据边界严格隔离】\n"
                    "- 第3部分【今日突破股池分析】只能使用'**【今日突破股池】**'和'**【今日突破股池到此为止】**'两个标记之间的股票\n"
                    "- 严禁把ETF数据区的成份股、量能爆发池的股票、中线股池的B浪信号股混入突破股池分析\n"
                    "- 第5部分【量能爆发池】只能使用'🔥 量能爆发·强买信号/观察信号/蓄势大涨'段落中的股票\n"
                    "- 第4/6部分【ETF】只能使用ETF数据区的成份股\n"
                    "- 各模块股票不得交叉混用，每个模块只分析本模块数据区的股票\n"
                )
            },
            {"role": "user", "content": prompt}
        ],
        "reasoning_effort": "max",
        "max_tokens": 65536,
        "temperature": 0.1,
        # 启用联网搜索工具（搜狗引擎，覆盖腾讯新闻/知乎/百科等）
        "tools": [{"type": "web_search", "web_search": {"enable": True, "search_result": True}}],
    }

    r = requests.post(url, headers=headers, json=data, timeout=300)
    if r.status_code != 200:
        print(f"⚠️ GLM API 错误 {r.status_code}: {r.text[:500]}")
        return ""

    resp = r.json()
    choice = resp['choices'][0]['message']

    # GLM-4-plus 联网搜索是隐式调用的（搜索结果作为 context 注入，不通过 tool_calls 暴露）
    # 只要配置了 web_search 工具，模型会自动决定是否搜索
    content = choice.get('content', '')
    print(f"[GLM] 模型={model} 联网搜索已启用，报告长度={len(content)}字符")

    return content


# =========================
# MiniMax（备用）
# =========================
def minimax(prompt):
    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"

    headers = {
        "Authorization": f"Bearer {MINI_MAX_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "MiniMax-M2.7",
        "messages": [
            {
                "role": "system",
                "content": "你是A股顶级机构趋势投资专家"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.2,
        "top_p": 0.5,
        "max_tokens": 40960
    }

    r = requests.post(
        url,
        headers=headers,
        json=data
    )

    if r.status_code != 200:

        print(r.text)

        return ""

    return r.json()['choices'][0]['message']['content']

##== KIMI ==##
def kimi(prompt):

    KIMI_API_KEY = os.getenv("KIMI_API_KEY")
    URL = "https://api.moonshot.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "kimi-k2.6",
        "messages": [
            {
                "role": "system",
                "content": "你是专业A股机构分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
                
        
    }

    try:

        response = requests.post(
            URL,
            headers=headers,
            json=payload,
            timeout=600
        )

        data = response.json()

        return data["choices"][0]["message"]["content"]

    except Exception as e:

        print("Kimi接口错误:", e)

        try:
            print(data)
        except:
            pass

        return ""
    
##== 豆包 ==##
def ask_doubao(prompt):
    from openai import OpenAI
    client = OpenAI(
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
    )
    try:
        completion = client.chat.completions.create(
            model=DOUBAO_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是专业A股机构分析师"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            top_p=0.5,
            max_tokens=40960
        )
        return completion.choices[0].message.content
    except Exception as e:
        print("Doubao接口错误:", e)
        return ""

from openai import OpenAI
##== 千问 ==##
def ask_qwen(prompt):
    try:
        client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        completion = client.chat.completions.create(
            model="qwen3-max",
            messages=[
                {
                    "role": "system",
                    "content": "你是专业A股机构分析师"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            # 稳定输出参数
            temperature=0.2,
            top_p=0.5,
            max_tokens=40960
        )

        if completion and completion.choices and len(completion.choices) > 0:
            message = completion.choices[0].message
            if message and hasattr(message, 'content'):
                return message.content
        print("千问接口返回数据格式异常")
        return ""
    except Exception as e:
        print(f"千问接口错误: {str(e)}")
        return ""
    

def send_wechat(msg, key):
    import re
    # 清理HTML标签（Server酱不支持HTML）
    msg = re.sub(r'<[^>]+>', '', msg)
    msg = msg.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

    if not key:
        print("⚠️ Server酱 SCKEY 为空，跳过推送")
        return
    url = f"https://sctapi.ftqq.com/{key}.send"

    data = {
        "title": f"每日复盘 - {TRADE_DATE}",
        "desp": msg
    }

    try:
        resp = requests.post(url, data=data, timeout=15)
        result = resp.json()
        if result.get("code") == 0:
            print("✅ Server酱 已发送")
        else:
            print(f"⚠️ Server酱 发送失败: code={result.get('code')} msg={result.get('message', '未知错误')}")
    except Exception as e:
        print(f"⚠️ Server酱 请求异常: {e}")


def send_pushplus(msg, token):
    """
    通过 PushPlus 推送微信消息（支持markdown）
    API: https://www.pushplus.plus/send
    """
    if not token:
        print("⚠️ PushPlus token 为空，跳过推送")
        return

    import re
    # 不清理HTML/标签，PushPlus原生支持markdown
    msg = msg.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": f"每日复盘 - {TRADE_DATE}",
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
        print(f"⚠️ PushPlus 请求异常: {e}")


def markdown_to_html_report(
        markdown_text,
        output_file="stock_report.html",
        pdf_file="stock_report.pdf",
        title="AI股票分析报告"
):

    # ========= Markdown 转 HTML =========
    body = markdown2.markdown(
        markdown_text,
        extras=[
            "tables",
            "fenced-code-blocks",
            "strike",
            "task_list"
        ]
    )

    # ========= CSS美化 =========
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">

<head>
<meta charset="UTF-8">

<title>{title}</title>

<style>

body {{
    background-color: #f5f7fa;
    color: #222;

    font-family:
        "PingFang SC",
        "Microsoft YaHei",
        Arial;

    max-width: 1000px;

    margin: 40px auto;

    padding: 40px;

    background: white;

    border-radius: 16px;

    box-shadow:
        0 4px 20px rgba(0,0,0,0.08);

    line-height: 1.8;
}}

h1 {{
    border-bottom: 3px solid #1677ff;
    padding-bottom: 12px;
    color: #1677ff;
}}

h2 {{
    margin-top: 35px;
    color: #0f172a;
}}

h3 {{
    color: #334155;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
    margin-bottom: 20px;
}}

th {{
    background: #1677ff;
    color: white;
    padding: 12px;
}}

td {{
    border: 1px solid #dcdfe6;
    padding: 10px;
}}

tr:nth-child(even) {{
    background: #f8fafc;
}}

code {{
    background: #f1f5f9;
    padding: 2px 6px;
    border-radius: 6px;
}}

pre {{
    background: #0f172a;
    color: #f8fafc;

    padding: 20px;

    border-radius: 12px;

    overflow-x: auto;
}}

blockquote {{
    border-left: 5px solid #1677ff;
    padding-left: 15px;
    color: #555;
    background: #f8fafc;
    margin: 20px 0;
}}

ul {{
    padding-left: 25px;
}}

li {{
    margin-bottom: 8px;
}}

strong {{
    color: #d4380d;
}}

</style>
</head>

<body>

{body}

</body>
</html>
"""

    # ========= 保存HTML =========
    with open(
            output_file,
            "w",
            encoding="utf-8"
    ) as f:

        f.write(html)

    print(f"HTML报告已生成: {output_file}")


# =========================
# 市场数据（带缓存）
# =========================
def get_market():
    cache_file = os.path.join(
        CACHE_DIR,
        f"market_{TRADE_DATE}.csv"
    )

    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            if not df.empty:  # 缓存文件有数据才使用
                return df
            else:
                print(f"[缓存] 缓存文件为空，重新拉取API")
        except Exception as e:
            print(f"[缓存] 市场数据读取失败: {e}")

    daily = _df_daily_by_date(TRADE_DATE)

    basic = _df_stock_list(list_status='L')
    if basic is not None and len(basic) > 0:
        basic = basic[['ts_code', 'name']]

    mv = _df_daily_basic_by_date(
        TRADE_DATE,
        fields='ts_code,total_mv'
    )

    df = daily.merge(
        basic,
        on='ts_code',
        how='left'
    )

    df = df.merge(
        mv,
        on='ts_code',
        how='left'
    )

    df.to_csv(cache_file, index=False)
    print(f"[缓存] 市场数据已保存: {cache_file}")

    return df

##==========缓存代码
def init_db():
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_result (
                date TEXT,
                rank INTEGER,
                code TEXT,
                name TEXT,
                close REAL,
                amount REAL,
                score REAL,
                theme TEXT
            )
        """)
        # 兼容旧表：如果表存在但没有theme列，则添加
        try:
            cursor.execute("ALTER TABLE stock_result ADD COLUMN theme TEXT")
        except:
            pass  # 列已存在，忽略
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"数据库初始化失败，跳过: {e}")

def save_result(df):
    try:
        conn = sqlite3.connect(DB_PATH)
        today = TRADE_DATE
        # 清理当天旧数据（避免重复）
        conn.execute(
            "DELETE FROM stock_result WHERE date=?",
            (today,)
        )
        for i, row in enumerate(df.itertuples()):
            conn.execute("""
                INSERT INTO stock_result
                (date, rank, code, name, close, amount, score, theme)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today,
                i + 1,
                getattr(row, "代码", ""),
                getattr(row, "名称", ""),
                float(getattr(row, "现价", 0)) if getattr(row, "现价", 0) not in ['', None] else 0.0,
                float(getattr(row, "成交额", 0)) if getattr(row, "成交额", 0) not in ['', None] else 0.0,
                float(getattr(row, "总排序评分", 0)) if getattr(row, "总排序评分", 0) not in ['', None] else 0.0,
                getattr(row, "所属主题", "")
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存结果到数据库失败，跳过: {e}")


# ======================================================
# 筹码分布（cyq_chips / cyq_perf）—— 压力/突破判断的核心数据源
# 采用文件缓存：chip_{ts_code}_{trade_date}_chips.csv / _perf.csv
# 一旦生成不会变化，命中缓存即可省 2 次 API 调用 + 节流等待
# ======================================================
def get_chip_distribution(ts_code, trade_date, current_price=None):
    """基于 cyq_chips + cyq_perf 计算筹码分布
    返回: dict，包含：
      - above_chips_pct: 当前价上方套牢盘比例(%)
      - below_chips_pct: 当前价下方获利盘比例(%)
      - avg_cost: 加权平均成本
      - winner_rate: 盈利筹码占比(%)
      - his_low / his_high: 历史最低/最高
      - pressure_peaks: 上方最大的3个筹码峰 [(价格, 占比), ...]
      - support_peaks: 下方最大的3个筹码峰 [(价格, 占比), ...]
      - cost_50pct: 成本中位数
      - cost_85pct / cost_95pct: 压力成本带
    """
    result = {}
    # ========= 缓存文件路径 =========
    td = str(trade_date).replace('-', '')
    chip_chips_file = os.path.join(CACHE_DIR, f"chip_{ts_code}_{td}_chips.csv")
    chip_perf_file = os.path.join(CACHE_DIR, f"chip_{ts_code}_{td}_perf.csv")

    try:
        # ========= cyq_chips：全量筹码分布（先读缓存，再回源） =========
        df = None
        cache_hit = False
        if os.path.exists(chip_chips_file):
            try:
                df = pd.read_csv(chip_chips_file)
                cache_hit = True
            except Exception as e:
                print(f"  [筹码] 缓存读失败 {ts_code} {td}: {e}")
                df = None

        if df is None or len(df) == 0:
            df = pro.cyq_chips(ts_code=ts_code, trade_date=td)
            time.sleep(0.12)
            # 缓存：仅写当日，非当日（回测）也写
            if df is not None and len(df) > 0:
                try:
                    df.to_csv(chip_chips_file, index=False)
                except Exception:
                    pass
        else:
            # 缓存命中记录（可保留）
            pass

        if df is None or len(df) == 0:
            return result

        if current_price is None:
            # 从 SQLite daily 缓存取最近收盘价
            d = _get_daily_from_sqlite(ts_code)
            if d is not None and len(d) > 0:
                current_price = float(d['close'].iloc[-1])
            if current_price is None:
                d = _df_daily_by_code(ts_code, start_date=td, end_date=td)
                if d is not None and len(d) > 0:
                    current_price = float(d['close'].iloc[0])
                else:
                    return result

        # 上方套牢盘 vs 下方获利盘（全部历史）
        above = df[df['price'] > current_price]
        below = df[df['price'] <= current_price]
        result['above_chips_pct'] = round(float(above['percent'].sum()), 2)
        result['below_chips_pct'] = round(float(below['percent'].sum()), 2)

        # 底部稳定筹码：成本在当前价下方15%以外的筹码（深度获利盘）
        # 这部分筹码的持有者已深度获利，不会因短期波动卖出，是底部支撑
        bottom_threshold = current_price * 0.85
        bottom_stable = below[below['price'] <= bottom_threshold]
        result['bottom_stable_pct'] = round(float(bottom_stable['percent'].sum()), 2)
        result['bottom_threshold'] = round(bottom_threshold, 2)

        # 短线套牢盘：当前价上方5%以内的筹码（近期追高被套的筹码）
        # 这部分筹码持有者还在犹豫，一解套就可能卖出，是短期压力源
        short_term_threshold = current_price * 1.05
        above_short_term = above[above['price'] <= short_term_threshold]
        result['short_term_above_pct'] = round(float(above_short_term['percent'].sum()), 2)
        result['short_term_threshold'] = round(short_term_threshold, 2)

        # 上方压力位（取占比最大的前3个）
        if len(above) > 0:
            peaks_above = above.nlargest(3, 'percent')
            result['pressure_peaks'] = [
                (float(r['price']), round(float(r['percent']), 2))
                for _, r in peaks_above.iterrows()
                if r['percent'] >= 0.3  # 过滤零散筹码
            ]
        else:
            result['pressure_peaks'] = []

        # 下方支撑位（取占比最大的前3个）
        if len(below) > 0:
            peaks_below = below.nlargest(3, 'percent')
            result['support_peaks'] = [
                (float(r['price']), round(float(r['percent']), 2))
                for _, r in peaks_below.iterrows()
            ]
        else:
            result['support_peaks'] = []

        # 最近压力位（取上方价格最近的显著压力位）
        if result['pressure_peaks']:
            nearest_peak = min(result['pressure_peaks'], key=lambda x: x[0])
            result['nearest_pressure'] = nearest_peak[0]
            result['nearest_pressure_pct'] = nearest_peak[1]
            # 最强压力位（占比最大的）
            result['strongest_pressure'] = result['pressure_peaks'][0][0]
            result['strongest_pressure_pct'] = result['pressure_peaks'][0][1]
        else:
            above_sorted = above[above['price'] > current_price].sort_values('price')
            if len(above_sorted) > 0:
                result['nearest_pressure'] = round(float(above_sorted['price'].iloc[0]), 2)
            else:
                result['nearest_pressure'] = round(float(df['price'].max()), 2)
            result['nearest_pressure_pct'] = 0
            result['strongest_pressure'] = result['nearest_pressure']
            result['strongest_pressure_pct'] = 0

        # 最近压力位距离（%）
        if current_price > 0 and result['nearest_pressure'] > current_price:
            result['nearest_pressure_dist_pct'] = round(
                ((result['nearest_pressure'] - current_price) / current_price) * 100, 2
            )
        else:
            result['nearest_pressure_dist_pct'] = 0

        # ========= cyq_perf：筹码成本与胜率（先读缓存，再回源） =========
        perf = None
        if os.path.exists(chip_perf_file):
            try:
                perf = pd.read_csv(chip_perf_file)
            except Exception:
                perf = None

        if perf is None or len(perf) == 0:
            perf = pro.cyq_perf(ts_code=ts_code, trade_date=td)
            time.sleep(0.12)
            if perf is not None and len(perf) > 0:
                try:
                    perf.to_csv(chip_perf_file, index=False)
                except Exception:
                    pass

        if perf is not None and len(perf) > 0:
            row = perf.iloc[0]
            result['his_low'] = round(float(row['his_low']), 2)
            result['his_high'] = round(float(row['his_high']), 2)
            result['avg_cost'] = round(float(row['weight_avg']), 2)
            result['winner_rate'] = round(float(row['winner_rate']), 2)
            result['cost_5pct'] = round(float(row['cost_5pct']), 2)
            result['cost_50pct'] = round(float(row['cost_50pct']), 2)
            result['cost_85pct'] = round(float(row['cost_85pct']), 2)
            result['cost_95pct'] = round(float(row['cost_95pct']), 2)

        # ========= 综合压力判断（短线套牢盘+底部稳定筹码） =========
        # 短线套牢盘（上方5%以内）：近期追高被套的筹码，解套就卖，是短期压力源
        # 底部稳定筹码（下方15%以外）：深度获利盘，不会轻易卖出，是底部支撑
        # 逻辑：短线套牢盘少 → 短期压力小；底部筹码多 → 底部扎实
        above_pct = result.get('above_chips_pct', 0)  # 全部历史套牢盘
        short_pct = result.get('short_term_above_pct', 0)  # 短线套牢盘（5%以内）
        bottom_pct = result.get('bottom_stable_pct', 0)  # 底部稳定筹码
        nearest_dist = result.get('nearest_pressure_dist_pct', 999)

        if short_pct < 3:
            result['pressure_level'] = '轻'
            result['pressure_desc'] = (
                f'短线套牢盘仅{short_pct:.1f}%，底筹{bottom_pct:.1f}%，短期无压力'
                f'（全部套牢盘{above_pct:.1f}%）'
            )
        elif short_pct < 10:
            result['pressure_level'] = '中'
            result['pressure_desc'] = (
                f'短线套牢盘{short_pct:.1f}%，底筹{bottom_pct:.1f}%，'
                f'压力位{result["nearest_pressure"]:.2f}元(距+{nearest_dist:.1f}%)，'
                f'有一定解套压力（全部套牢盘{above_pct:.1f}%）'
            )
        elif bottom_pct >= 20:
            # 短线套牢盘较多，但底部筹码扎实，短期抛压可控
            result['pressure_level'] = '中'
            result['pressure_desc'] = (
                f'短线套牢盘{short_pct:.1f}%，但底筹{bottom_pct:.1f}%扎实，'
                f'短期抛压可控（全部套牢盘{above_pct:.1f}%）'
            )
        else:
            result['pressure_level'] = '重'
            result['pressure_desc'] = (
                f'短线套牢盘{short_pct:.1f}%，底筹仅{bottom_pct:.1f}%，'
                f'筹码松散，压力位{result["nearest_pressure"]:.2f}元(距+{nearest_dist:.1f}%)，'
                f'突破需要放量（全部套牢盘{above_pct:.1f}%）'
            )

        # 突破有效性判断
        avg_cost = result.get('avg_cost', current_price)
        if current_price > avg_cost:
            result['breakout_status'] = '已突破筹码平均成本'
        else:
            result['breakout_status'] = '仍在平均成本下方运行'

    except Exception as e:
        # 失败时记录但不抛出，避免影响主流程
        print(f"  [筹码] {ts_code} {td} 获取失败: {e}")
    return result


def calc_tech_indicators(df, ts_code=None, trade_date=None):
    """计算关键技术指标价格（供AI分析使用）
    核心数据源：MA均线 + K线高点（辅助参考）+ 筹码分布 cyq_chips / cyq_perf（主压力判断）
    筹码分布是判断"上方是否真的有套牢盘"的权威数据源，取代仅靠K线高点推测的旧逻辑
    """
    result = {}
    if df is None or len(df) < 5 or not isinstance(df, pd.DataFrame):
        return result

    try:
        close = df['close']
        high = df['high']
        low = df['low'] if 'low' in df.columns else close
        current_price = float(close.iloc[-1])
        result['current_price'] = current_price

        # 均线价格
        result['ma5'] = round(float(close.rolling(5).mean().iloc[-1]), 2) if len(close) >= 5 else current_price
        result['ma10'] = round(float(close.rolling(10).mean().iloc[-1]), 2) if len(close) >= 10 else current_price
        result['ma20'] = round(float(close.rolling(20).mean().iloc[-1]), 2) if len(close) >= 20 else current_price
        result['ma60'] = round(float(close.rolling(60).mean().iloc[-1]), 2) if len(close) >= 60 else current_price

        # =========================
        # K线高点（辅助参考，不作为压力判断主依据）
        # =========================
        def hhv_exclude_today(window):
            if len(high) >= window + 1:
                return float(high.iloc[:-1].tail(window).max())
            elif len(high) >= 2:
                return float(high.iloc[:-1].max())
            else:
                return float(high.max())

        hist_high_20 = hhv_exclude_today(20)
        hist_high_60 = hhv_exclude_today(60)
        hist_high_120 = hhv_exclude_today(120)
        hist_high_250 = hhv_exclude_today(250)
        hist_high_all = float(high.iloc[:-1].max()) if len(high) > 1 else float(high.max())

        result['high_20d'] = round(hist_high_20, 2)
        result['high_60d'] = round(hist_high_60, 2)
        result['high_120d'] = round(hist_high_120, 2)
        result['high_250d'] = round(hist_high_250, 2)
        result['high_all'] = round(hist_high_all, 2)

        # 历史低点
        hist_low_20 = float(low.iloc[:-1].tail(20).min()) if len(close) > 1 else float(low.tail(20).min())
        hist_low_60 = float(low.iloc[:-1].tail(60).min()) if len(close) > 1 else float(low.tail(60).min())
        result['low_20d'] = round(hist_low_20, 2)
        result['low_60d'] = round(hist_low_60, 2)

        # 距各周期高点的百分比
        if hist_high_20 > 0:
            result['dist_to_high20_pct'] = round(((hist_high_20 - current_price) / current_price) * 100, 1)
        if hist_high_60 > 0:
            result['dist_to_high60_pct'] = round(((hist_high_60 - current_price) / current_price) * 100, 1)
        if hist_high_120 > 0:
            result['dist_to_high120_pct'] = round(((hist_high_120 - current_price) / current_price) * 100, 1)
        if hist_high_250 > 0:
            result['dist_to_high250_pct'] = round(((hist_high_250 - current_price) / current_price) * 100, 1)
        if hist_high_all > 0:
            result['dist_to_highall_pct'] = round(((hist_high_all - current_price) / current_price) * 100, 1)

        # =========================
        # 筹码分布（主压力判断依据，cyq_chips / cyq_perf）
        # =========================
        chip_result = {}
        if ts_code and trade_date:
            # 优先从 df 的最后一行取 trade_date
            try:
                td_actual = str(df['trade_date'].iloc[-1]).split(' ')[0] if 'trade_date' in df.columns else trade_date
            except Exception:
                td_actual = trade_date
            chip_result = get_chip_distribution(ts_code, td_actual, current_price)

        if chip_result:
            # 筹码核心数据
            result['above_chips_pct'] = chip_result.get('above_chips_pct', 0)
            result['bottom_stable_pct'] = chip_result.get('bottom_stable_pct', 0)
            result['short_term_above_pct'] = chip_result.get('short_term_above_pct', 0)
            result['below_chips_pct'] = chip_result.get('below_chips_pct', 0)
            result['avg_cost'] = chip_result.get('avg_cost', current_price)
            result['winner_rate'] = chip_result.get('winner_rate', 0)
            result['cost_50pct'] = chip_result.get('cost_50pct', current_price)
            result['cost_85pct'] = chip_result.get('cost_85pct', current_price)
            result['cost_95pct'] = chip_result.get('cost_95pct', current_price)
            result['chip_his_high'] = chip_result.get('his_high', 0)
            result['nearest_pressure'] = chip_result.get('nearest_pressure', 0)
            result['nearest_pressure_pct'] = chip_result.get('nearest_pressure_pct', 0)
            result['nearest_pressure_dist_pct'] = chip_result.get('nearest_pressure_dist_pct', 0)

            # 压力位（格式化：价格+占比）
            pressure_peaks = chip_result.get('pressure_peaks', [])
            if pressure_peaks:
                result['pressure_peak_1_price'] = pressure_peaks[0][0]
                result['pressure_peak_1_pct'] = pressure_peaks[0][1]
                if len(pressure_peaks) > 1:
                    result['pressure_peak_2_price'] = pressure_peaks[1][0]
                    result['pressure_peak_2_pct'] = pressure_peaks[1][1]
            support_peaks = chip_result.get('support_peaks', [])
            if support_peaks:
                result['support_peak_1_price'] = support_peaks[0][0]
                result['support_peak_1_pct'] = support_peaks[0][1]

            # 综合压力判断（筹码优先）
            result['pressure_level'] = chip_result.get('pressure_level', '未知')
            result['pressure_desc'] = chip_result.get('pressure_desc', '筹码数据不可用')
            result['breakout_status'] = chip_result.get('breakout_status', '')

            # ===== 筹码突破真假评分 =====
            chip_bt = calc_chip_breakthrough_score(chip_result, current_price)
            result['chip_breakthrough_score'] = chip_bt['score']
            result['chip_breakthrough_level'] = chip_bt['level']

            # 保留原有字段名（兼容）
            pct = chip_result.get('above_chips_pct', 0)
            result['has_upper_pressure'] = pct >= 3  # 3%为阈值，不是10%K线标准
            pressure_parts = []
            if pressure_peaks:
                for p_price, p_pct in pressure_peaks[:2]:
                    pressure_parts.append(f"{p_price:.1f}元(占比{p_pct:.1f}%)")
            if pressure_parts:
                result['upper_pressure_desc'] = "；".join(pressure_parts)
            else:
                result['upper_pressure_desc'] = result['pressure_desc']
        else:
            # 筹码不可用时退化为K线判断
            upper_pressure_levels = []
            if hist_high_all > current_price * 1.01:
                pct_all = ((hist_high_all - current_price) / current_price) * 100
                upper_pressure_levels.append(f"全历史高点{result['high_all']:.2f}元(距+{pct_all:.1f}%)")
            if hist_high_250 > current_price * 1.01:
                pct_250 = ((hist_high_250 - current_price) / current_price) * 100
                upper_pressure_levels.append(f"250日高点{result['high_250d']:.2f}元(距+{pct_250:.1f}%)")
            if hist_high_120 > current_price * 1.01:
                pct_120 = ((hist_high_120 - current_price) / current_price) * 100
                upper_pressure_levels.append(f"120日高点{result['high_120d']:.2f}元(距+{pct_120:.1f}%)")
            result['upper_pressure_desc'] = "；".join(upper_pressure_levels) if upper_pressure_levels else "上方无显著历史套牢盘"
            result['has_upper_pressure'] = len(upper_pressure_levels) > 0
            result['pressure_level'] = 'K线估算'
            result['pressure_desc'] = result['upper_pressure_desc']

        # 距20/60日均线百分比
        if result['ma20'] > 0:
            result['dist_to_ma20_pct'] = round(((current_price - result['ma20']) / result['ma20']) * 100, 1)
        if result['ma60'] > 0:
            result['dist_to_ma60_pct'] = round(((current_price - result['ma60']) / result['ma60']) * 100, 1)

        # 近5日K线概况
        recent_5 = df.tail(5)
        if len(recent_5) >= 5:
            result['recent5_high'] = round(float(recent_5['high'].max()), 2)
            result['recent5_low'] = round(float(recent_5['low'].min()), 2) if 'low' in df.columns else round(float(recent_5['close'].min()), 2)
            result['recent5_range_pct'] = round(((result['recent5_high'] - result['recent5_low']) / result['recent5_low']) * 100, 1)

        # 近10日涨跌幅
        if len(df) >= 10:
            price_10d_ago = float(close.iloc[-10])
            if price_10d_ago > 0:
                result['chg_10d_pct'] = round(((current_price - price_10d_ago) / price_10d_ago) * 100, 1)

        # 均线方向
        if len(close) >= 25:
            ma20_prev = float(close.iloc[:-1].tail(20).mean())
            result['ma20_trend'] = '向上' if result['ma20'] > ma20_prev else '向下'
        if len(close) >= 65:
            ma60_prev = float(close.iloc[:-1].tail(60).mean())
            result['ma60_trend'] = '向上' if result['ma60'] > ma60_prev else '向下'

    except Exception as e:
        print(f"  [tech] calc_tech_indicators 异常: {e}")
    return result


# ======================================================
# Chip Alpha Engine V2.1 集成
# 动态筹码Alpha分析：CRE、ChipMomentum、ChipTrendScore 等
# ======================================================
_chip_alpha_engine = None

def get_chip_alpha_engine():
    """获取 ChipAlphaEngineV2 单例（延迟初始化）"""
    global _chip_alpha_engine
    if _chip_alpha_engine is None:
        try:
            from chip_alpha_engine_v2 import ChipAlphaEngineV2
            _chip_alpha_engine = ChipAlphaEngineV2(token=TUSHARE_TOKEN)
        except Exception as e:
            print(f"[ChipAlpha] 引擎初始化失败: {e}")
            return None
    return _chip_alpha_engine


def batch_chip_alpha(stocks, lookback_days=20):
    """
    批量计算筹码Alpha因子
    参数: stocks - list of dict，至少包含'代码'字段
    返回: dict {ts_code: chip_result_dict}
    """
    engine = get_chip_alpha_engine()
    if engine is None:
        return {}

    results = {}
    total = len(stocks)
    for i, s in enumerate(stocks):
        ts_code = s.get('代码') or s.get('code') or s.get('ts_code', '')
        if not ts_code:
            continue
        try:
            r = engine.analyze(ts_code, lookback_days=lookback_days)
            results[ts_code] = r
            if (i + 1) % 10 == 0:
                print(f"[ChipAlpha] 批量计算 {i+1}/{total}")
        except Exception as e:
            print(f"[ChipAlpha] {ts_code} 计算失败: {e}")
    return results


def extract_chip_alpha_factors(chip_result):
    """
    从 ChipAlphaEngine 结果中提取关键因子（扁平化为简单dict）
    """
    if not chip_result:
        return {
            'ChipTrendScore': 50,
            'ChipGrade': 'C',
            'ChipStage': '未知',
            'CRE_Score': 50,
            'ChipMomentum_Score': 50,
            'PressureDecay_Score': 50,
            'Absorption_Score': 50,
            'CenterVelocity_Score': 50,
        }
    f = chip_result.get('Factors', {})
    dim = chip_result.get('DimensionScores', {})
    return {
        'ChipTrendScore': chip_result.get('ChipTrendScore', 50),
        'ChipGrade': chip_result.get('Grade', 'C'),
        'ChipStage': {
            'Accumulation': '吸筹中', 'Distribution': '派发中',
            'Expansion': '扩张期', 'Early Trend': '早期趋势',
            'Collapse': '崩溃', 'Unknown': '未知',
        }.get(chip_result.get('TrendStage', ''), chip_result.get('TrendStage', '未知')),
        'CRE_Score': f.get('CRE', {}).get('score', 50),
        'ChipMomentum_Score': f.get('ChipMomentum', {}).get('score', 50),
        'PressureDecay_Score': f.get('PressureDecay', {}).get('score', 50),
        'Absorption_Score': f.get('Absorption', {}).get('score', 50),
        'CenterVelocity_Score': f.get('CenterVelocity', {}).get('score', 50),
        'Structure_Score': dim.get('Structure', {}).get('score', 50),
        'Flow_Score': dim.get('Flow', {}).get('score', 50),
        'Momentum_Score': dim.get('Momentum', {}).get('score', 50),
    }


def get_chip_alpha_suggestion(stock_dict):
    """
    根据筹码Alpha因子给出操作建议
    返回 (建议, 理由)
    """
    score = stock_dict.get('ChipTrendScore', 50)
    cre = stock_dict.get('CRE_Score', 50)
    mom = stock_dict.get('ChipMomentum_Score', 50)
    pressure = stock_dict.get('PressureDecay_Score', 50)
    absorption = stock_dict.get('Absorption_Score', 50)
    stage = stock_dict.get('ChipStage', '未知')

    if score >= 75 and cre >= 65 and mom >= 60:
        return "积极参与", f"趋势强+CRE高+动量足，{stage}，可沿5日线持有"
    if score >= 65 and cre >= 50 and mom >= 50:
        if pressure >= 80:
            return "可逢低介入", f"趋势向好+上方压力轻，{stage}，回踩MA20低吸"
        if cre >= 60 and mom >= 60:
            return "可逢低介入", f"趋势转强+效率提升，{stage}，分批建仓"
        return "关注观察", f"趋势中性偏强，{stage}，等待CRE/动量进一步确认"
    if score >= 55 and cre >= 40:
        if mom >= 60 and pressure >= 70:
            return "左侧关注", f"动量转强+压力轻，但趋势分未达标，小仓位试错"
        if absorption >= 60:
            return "左侧关注", f"吸筹中+吸筹质量尚可，{stage}，等待启动信号"
        return "纳入观察", f"筹码改善中，{stage}，等待趋势确认信号"
    if mom >= 65 and pressure >= 80:
        return "左侧试错", f"动量加速+压力极轻，但趋势分偏低，轻仓试错"
    if score < 40:
        return "回避", f"筹码结构弱，{stage}，暂不参与"
    return "观望等待", f"筹码中性，{stage}，等待明确信号"


# ======================================================
# Chip Alpha Engine V5 集成 — Institutional Trend Intelligence Engine
# 集成到突破股池/量能爆发池，与V2共存互不冲突
# ======================================================
_chip_alpha_v5_engine = None

def get_chip_alpha_v5_engine():
    """获取 ChipAlphaV5Engine 单例（延迟初始化）"""
    global _chip_alpha_v5_engine
    if _chip_alpha_v5_engine is None:
        try:
            from chip_alpha_v5 import ChipAlphaV5Engine
            _chip_alpha_v5_engine = ChipAlphaV5Engine(token=TUSHARE_TOKEN)
        except Exception as e:
            print(f"[ChipAlphaV5] 引擎初始化失败: {e}")
            return None
    return _chip_alpha_v5_engine


def batch_chip_alpha_v5(v2_results):
    """
    一键V5升级：输入V2批量结果 {ts_code: v2_result}，输出V5批量分析 {ts_code: v5_profile}
    V5构建在V2之上，无需额外API调用，纯计算无新增耗时。
    """
    engine = get_chip_alpha_v5_engine()
    if engine is None:
        return {}
    v5_results = {}
    total = len(v2_results)
    for i, (ts_code, v2_r) in enumerate(v2_results.items()):
        try:
            v5 = engine.analyze_from_v2(v2_r)
            v5_results[ts_code] = v5
            if (i + 1) % 20 == 0:
                print(f"[ChipAlphaV5] 升级 {i+1}/{total}")
        except Exception as e:
            print(f"[ChipAlphaV5] {ts_code} 升级失败: {e}")
    return v5_results


def extract_chip_alpha_v5_factors(v5_result):
    """
    从V5分析结果中提取关键展示字段（扁平化为简单dict）
    用于注入突破/量能池的股票数据行
    """
    if not v5_result:
        return {
            'Alpha_Structure': 50, 'Alpha_Flow': 50, 'Alpha_Momentum': 50,
            'Alpha_Composite': 50, 'Alpha_Grade': 'C',
            'Risk_Score': 50, 'Risk_Level': 'Medium',
            'Trend_State': 'Unknown', 'Trend_Desc': '',
            'Next_State': 'Unknown', 'Next_Prob': 0,
            'Action': 'Hold', 'Confidence': 50,
            'DecisionSummary': '',
            'Opportunity_Score': 50.0,
            'OS_Details': '',
        }
    a = v5_result.get('alpha', {})
    r = v5_result.get('risk', {})
    t = v5_result.get('trend', {})
    d = v5_result.get('decision', {})
    tr = t.get('transition', {})
    rd = r.get('dimensions', {})
    price = v5_result.get('current_price', 0)
    center = v5_result.get('chip_center', 0)
    # 生成简短决策摘要
    _s = a.get('Structure', 50)
    _f = a.get('Flow', 50)
    _m = a.get('Momentum', 50)
    _c = a.get('Composite', 50)
    _risk = r.get('Composite', 50)
    _state = t.get('current_state', 'Unknown')
    _next = tr.get('primary_next', '')
    _prob = tr.get('primary_prob', 0)
    _action = d.get('action', 'Hold')
    _conf = d.get('confidence', 50)
    _s_desc = '优' if _s >= 80 else ('良' if _s >= 60 else '弱')
    _f_desc = '强' if _f >= 70 else ('中' if _f >= 50 else '弱')
    _m_desc = '强' if _m >= 70 else ('中' if _m >= 50 else '弱')
    _risk_desc = r.get('Level', 'Medium')
    _trans = f"{_next}({_prob*100:.0f}%)" if _next else ''
    # --- Alpha解读 ---
    alpha_interpret = f"结构{_s_desc}({_s:.0f}) 资金{_f_desc}({_f:.0f}) 动量{_m_desc}({_m:.0f}) | 复合{_c:.0f}({a.get('Grade','C')})"
    # --- 价格vs质心 ---
    pv_text = ''
    if price and center:
        dist = (price - center) / center * 100
        if dist < -5:
            pv_text = f"现价{price:.2f} 远低于质心{center:.2f} ({dist:.1f}%)，当前即为低吸窗口"
        elif dist < 0:
            pv_text = f"现价{price:.2f} 低于质心{center:.2f} ({dist:.1f}%)，折价区间，无需等回踩"
        elif dist < 5:
            pv_text = f"现价{price:.2f} 略高于质心{center:.2f} ({dist:+.1f}%)，成本支撑有效，可等回踩"
        elif dist < 15:
            pv_text = f"现价{price:.2f} 高于质心{center:.2f} ({dist:+.1f}%)，注意回调风险"
        else:
            pv_text = f"现价{price:.2f} 大幅高于质心{center:.2f} ({dist:+.1f}%)，追高风险大"
    # --- 风险维度详情 ---
    risk_dim_names = {
        'MomentumExhaustion': '动量衰竭',
        'ProfitCrowding': '获利拥挤',
        'Distribution': '派发信号',
        'StructureBreakdown': '结构破裂',
        'VolatilityExpansion': '波动放大',
        'LiquidityRisk': '流动性',
    }
    high_dims = []
    for k, name in risk_dim_names.items():
        v = rd.get(k, 0)
        if v >= 40:
            high_dims.append(f"{name}({v:.0f})")
    risk_detail = f"风险{_risk:.0f}({_risk_desc})"
    if high_dims:
        risk_detail += ' | 关注:' + ' '.join(high_dims)
    # --- 生命周期 + 转移 ---
    trans_detail = f"{_state}"
    if _trans:
        trans_detail += f" → {_trans}"
    # --- 操作建议详细 ---
    act_detail = f"{_action}({_conf:.0f}%)"
    if _action in ('Buy', 'Strong Buy'):
        if price and center:
            dist = (price - center) / center * 100
            if dist < 0:
                # 现价已低于质心→当前就是低吸区间，止损以现价为基准
                stop = price * 0.95
                act_detail += f" | 止损{stop:.2f} | 现价已低于质心，当前即为低吸区间"
            else:
                # 现价高于质心→等待回踩，止损设在质心下方
                stop = center * 0.97
                act_detail += f" | 止损{stop:.2f} | 回踩质心{center:.2f}低吸"
        else:
            act_detail += " | 逢低建仓"
    elif _action == 'Buy on Pullback':
        act_detail += " | 不追高，等缩量回踩"
    elif _action == 'Hold':
        act_detail += " | 持有观望"
    elif _action in ('Reduce', 'Take Profit'):
        act_detail += " | 减仓控风险"
    elif _action == 'Avoid':
        act_detail += " | 暂不参与"
    # --- 组合成详细决策行 ---
    decision_detail_parts = [alpha_interpret]
    if pv_text:
        decision_detail_parts.append(pv_text)
    decision_detail_parts.append(risk_detail)
    decision_detail_parts.append(trans_detail)
    decision_detail_parts.append(act_detail)
    decision_detail = ' | '.join(decision_detail_parts)

    # --- 简短摘要（向后兼容） ---
    summary_parts = [
        f"结构{_s_desc}({_s:.0f})",
        f"资金{_f_desc}({_f:.0f})",
        f"动量{_m_desc}({_m:.0f})",
        f"复合{_c:.0f}({a.get('Grade','C')})",
        f"风险{_risk:.0f}({_risk_desc})",
    ]
    if _trans:
        summary_parts.append(f"{_state}→{_trans}")
    summary_parts.append(f"{_action}({_conf:.0f}%)")
    decision_summary = ' | '.join(summary_parts)

    # --- Opportunity Score ---
    _os = None
    try:
        from chip_alpha_v5 import calc_opportunity_score
        _os = calc_opportunity_score(v5_result)
    except Exception:
        _os = None
    os_score = _os['score'] if _os else 50.0
    os_details = _os['details'] if _os else ''

    return {
        'Alpha_Structure': round(_s, 1),
        'Alpha_Flow': round(_f, 1),
        'Alpha_Momentum': round(_m, 1),
        'Alpha_Composite': round(_c, 1),
        'Alpha_Grade': a.get('Grade', 'C'),
        'Risk_Score': round(_risk, 1),
        'Risk_Level': _risk_desc,
        'Trend_State': _state,
        'Trend_Desc': t.get('description', ''),
        'Next_State': tr.get('primary_next', 'Unknown'),
        'Next_Prob': round(tr.get('primary_prob', 0) * 100, 1),
        'Action': _action,
        'Confidence': round(_conf, 1),
        'Action_Combined': d.get('combined', ''),
        'DecisionSummary': decision_summary,
        'DecisionDetail': decision_detail,
        'Price_vs_Center': pv_text,
        'Risk_Detail': risk_detail,
        'Alpha_Interpret': alpha_interpret,
        'Trans_Detail': trans_detail,
        'Act_Detail': act_detail,
        'Opportunity_Score': os_score,
        'OS_Details': os_details,
    }


# ======================================================
# 筹码突破真假判断评分（基于 cyq_chips / cyq_perf）
# 判断突破是否健康：真突破vs假突破
# 返回 0-100 分，越高代表真突破可信度越高
# ======================================================
def calc_chip_breakthrough_score(chip_result, current_price):
    """
    基于筹码分布数据，计算短线真突破可信度评分
    
    核心逻辑：
    - 真突破：上方套牢盘少、集中度健康、筹码已获利、无密集压力峰
    - 假突破：上方套牢盘多、筹码分散、avg_cost在上方、压力峰密集
    
    参数：
        chip_result: get_chip_distribution 返回值
        current_price: 当前价
    返回：
        dict: {score, level, factors}  score=0-100
    """
    if not chip_result or not current_price or current_price <= 0:
        return {'score': 50, 'level': '未知', 'factors': {}, 'judgement': '数据不足'}
    
    factors = {}
    
    # ===== 因子1: 上方套牢盘比例（weight=30） =====
    above_pct = chip_result.get('above_chips_pct', 100)
    if above_pct < 3:
        s1 = 100
    elif above_pct < 8:
        s1 = 85
    elif above_pct < 15:
        s1 = 70
    elif above_pct < 25:
        s1 = 50
    elif above_pct < 40:
        s1 = 30
    else:
        s1 = 10
    factors['above_chips'] = {'score': s1, 'weight': 30, 'value': above_pct}
    
    # ===== 因子2: 盈利筹码比例（weight=20） =====
    winner = chip_result.get('winner_rate', 0)
    if winner > 85:
        s2 = 100
    elif winner > 70:
        s2 = 80
    elif winner > 55:
        s2 = 60
    elif winner > 40:
        s2 = 40
    elif winner > 25:
        s2 = 20
    else:
        s2 = 0
    factors['winner_rate'] = {'score': s2, 'weight': 20, 'value': winner}
    
    # ===== 因子3: 当前价与平均成本距离（weight=20） =====
    avg_cost = chip_result.get('avg_cost', current_price)
    if avg_cost > 0:
        dist_ratio = (current_price - avg_cost) / avg_cost * 100
        if dist_ratio > 15:
            s3 = 100
        elif dist_ratio > 8:
            s3 = 80
        elif dist_ratio > 3:
            s3 = 60
        elif dist_ratio > -2:
            s3 = 50
        elif dist_ratio > -10:
            s3 = 30
        else:
            s3 = 10
    else:
        s3 = 50
    factors['cost_distance'] = {'score': s3, 'weight': 20, 'value': round(dist_ratio, 1)}
    
    # ===== 因子4: 最近压力位距离（weight=15） =====
    nearest_p = chip_result.get('nearest_pressure', 0)
    if nearest_p > 0:
        dist_to_pressure = (nearest_p - current_price) / current_price * 100
        if dist_to_pressure < 0:
            s4 = 100
        elif dist_to_pressure < 2:
            s4 = 30
        elif dist_to_pressure < 5:
            s4 = 50
        elif dist_to_pressure < 10:
            s4 = 70
        else:
            s4 = 85
    else:
        s4 = 80
    factors['pressure_distance'] = {'score': s4, 'weight': 15,
                                     'value': round(dist_to_pressure, 1) if nearest_p > 0 else 999}
    
    # ===== 因子5: 集中度（weight=15） =====
    cost_85 = chip_result.get('cost_85pct', 0)
    cost_95 = chip_result.get('cost_95pct', 0)
    if cost_85 > 0 and cost_95 > 0:
        spread_pct = (cost_95 - cost_85) / cost_85 * 100
        if spread_pct < 8:
            s5 = 100
        elif spread_pct < 15:
            s5 = 80
        elif spread_pct < 25:
            s5 = 60
        elif spread_pct < 40:
            s5 = 40
        else:
            s5 = 20
    else:
        s5 = 50
    factors['concentration'] = {'score': s5, 'weight': 15,
                                 'value': round(spread_pct, 1) if cost_85 > 0 and cost_95 > 0 else 999}
    
    # ===== 综合评分 =====
    total_weight = sum(f['weight'] for f in factors.values())
    total_score = sum(f['score'] * f['weight'] for f in factors.values()) / total_weight if total_weight > 0 else 50
    total_score = round(max(0, min(100, total_score)), 1)
    
    # 判断等级
    if total_score >= 85:
        level = '真突破'
    elif total_score >= 70:
        level = '大概率真突破'
    elif total_score >= 55:
        level = '中性偏多'
    elif total_score >= 40:
        level = '中性偏空'
    elif total_score >= 25:
        level = '很可能假突破'
    else:
        level = '假突破'
    
    return {
        'score': total_score,
        'level': level,
        'factors': factors,
        'judgement': level
    }

# =========================
# 涨跌停数据（替代 emotion.get_limit_stats）
# =========================
def get_limit_stats():
    """获取涨跌停数据，替代原 emotion.get_limit_stats
    优化：优先使用收盘后的实际涨跌停数据，避免盘中触板数据干扰
    """
    try:
        print("开始获取涨跌停数据...")
        zt_codes = []
        dt_codes = []
        broken_rate = 0.0

        # 方法1：使用每日行情数据计算真实的涨跌停（收盘价）
        try:
            # 获取当日所有股票的收盘价和涨跌幅
            daily = _df_daily_by_date(TRADE_DATE)
            if daily is not None and not daily.empty:
                # 计算涨跌停阈值（简化版：主板10%，科创板/创业板20%）
                daily['is_kcb'] = daily['ts_code'].str.startswith(('688', '301'))
                daily['is_cn'] = daily['ts_code'].str.startswith('300')
                daily['limit_up'] = daily.apply(
                    lambda x: 20.0 if x['is_kcb'] or x['is_cn'] else 10.0, axis=1
                )
                daily['limit_down'] = -daily['limit_up']
                
                # 真实涨停：收盘价涨幅接近涨停价（>=99%的涨停幅度）
                if 'pct_chg' in daily.columns:
                    zt_mask = (daily['pct_chg'] >= daily['limit_up'] * 0.99) & (daily['pct_chg'] < daily['limit_up'] + 0.1)
                    # 真实跌停：收盘价跌幅接近跌停价
                    dt_mask = (daily['pct_chg'] <= daily['limit_down'] * 0.99) & (daily['pct_chg'] > daily['limit_down'] - 0.1)
                else:
                    zt_mask = pd.Series([False] * len(daily), index=daily.index)
                    dt_mask = pd.Series([False] * len(daily), index=daily.index)
                
                zt_codes = daily[zt_mask]['ts_code'].tolist()
                dt_codes = daily[dt_mask]['ts_code'].tolist()
                
                print(f"涨停(真实收盘): {len(zt_codes)}只")
                print(f"跌停(真实收盘): {len(dt_codes)}只")
                
                # 获取炸板数据（盘中触及涨停但未封住）
                try:
                    limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
                    if limit_df is not None and not limit_df.empty:
                        # limit='D'表示最终封住, limit='Z'表示炸板
                        zhaban_codes = limit_df[limit_df['limit'] == 'Z']['ts_code'].astype(str).tolist()
                        zhaban_count = len(zhaban_codes)
                        
                        # 炸板率 = 炸板数 ÷ (封住数 + 炸板数)
                        total_touch = len(zt_codes) + zhaban_count
                        if total_touch > 0:
                            broken_rate = (zhaban_count / total_touch) * 100
                            print(f"炸板率: {broken_rate:.1f}% (炸板{zhaban_count}只/触及涨停{total_touch}只)")
                except Exception as e:
                    print(f"获取炸板数据失败: {e}")
                    
        except Exception as e:
            print(f"方法1失败: {e}")

        # 如果以上方法都失败，使用ths接口作为备选（但不作为主要数据源）
        if not zt_codes and not dt_codes:
            print("[备选] 使用ths接口...")
            try:
                ths_zt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='涨停池')
                if ths_zt is not None and not ths_zt.empty:
                    zt_codes = ths_zt['ts_code'].astype(str).tolist()
                    print(f"涨停(ths备选): {len(zt_codes)}只")
            except Exception as e:
                print(f"ths涨停失败: {e}")

            try:
                ths_dt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='跌停池')
                if ths_dt is not None and not ths_dt.empty:
                    dt_codes = ths_dt['ts_code'].astype(str).tolist()
                    print(f"跌停(ths备选): {len(dt_codes)}只")
            except Exception as e:
                print(f"ths跌停失败: {e}")

        return {
            "zt_count": len(zt_codes),
            "dt_count": len(dt_codes),
            "zt_codes": zt_codes,
            "dt_codes": dt_codes,
            "broken_rate": round(broken_rate, 1)
        }
    except Exception as e:
        print("获取涨跌停失败:", e)
        return {"zt_count": 0, "dt_count": 0, "zt_codes": [], "dt_codes": [], "broken_rate": 0}

def calc_max_limit_height():
    """计算最高连板高度，替代原 emotion.calc_max_limit_height"""
    try:
        # 优先使用 akshare 免费接口
        import akshare as ak
        try:
            # akshare 1.18.x 版本使用 stock_zt_pool_em
            zt_df = ak.stock_zt_pool_em(date=TRADE_DATE)
            if zt_df is not None and not zt_df.empty:
                # 提取连板数并转换为整数
                if '连板数' in zt_df.columns:
                    zt_df['连板数'] = pd.to_numeric(zt_df['连板数'], errors='coerce').fillna(1).astype(int)
                    max_lb = zt_df['连板数'].max()
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return int(max_lb)
                elif '连扳数' in zt_df.columns:  # 兼容不同版本的字段名
                    zt_df['连扳数'] = pd.to_numeric(zt_df['连扳数'], errors='coerce').fillna(1).astype(int)
                    max_lb = zt_df['连扳数'].max()
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return int(max_lb)
        except Exception as ak_error:
            print(f"[连板高度] akshare获取失败: {ak_error}")
        
        # akshare失败时，尝试 tushare pro 接口
        if pro is not None:
            zt_df = pro.limit_step(trade_date=TRADE_DATE)
            if zt_df is not None and not zt_df.empty:
                if 'nums' in zt_df.columns:
                    max_lb = zt_df['nums'].fillna(1).astype(int).max()
                    return int(max_lb)
            return 1
        
        # 都失败时返回默认值
        return 3
    except Exception as e:
        print(f"[连板高度] 计算失败: {e}")
        return 3


# =========================
# 主题过滤：读取 theme_analysis_v2 报告，以 主线 + 轮动 主题为过滤范围
# =========================
# V3 生命周期 → 共振系数可识别阶段
_LC_STAGE_V6 = {'启动': '启动', '升温': '启动', '主升': '主升', '分歧转一致': '调整',
                '高潮': '高潮', '退潮': '衰退', '震荡': '调整', '弱势': '衰退'}


def _load_mainline_rotation_themes(trade_date):
    """读取 theme_analysis_v2_{date}.txt，提取 主线(▶)+轮动(▸) 主题及其报告字段

    报告行格式（theme_score_v2.py 生成，含主线类型分级 V1.0）：
      主线: ▶ 半导体 [主升] 情绪+趋势共振 质量90 | 策略:龙头+中军 | 趋势81 情绪74 涨停13 迁移11.0
      轮动: ▸ 可控核聚变 [升温] 非主线 质量55 | 趋势50 综合51 涨停1 迁移6.9
      回避: ✕ 消费 [退潮] 综合34 → 【坚决回避/清仓】
    返回 {主题名: {theme, stage, kind, mainline_type, mainline_quality, trading_style,
                   trend_score, sentiment_score, composite_score, zt_count,
                   migration_score, stage_v6, ...}}
    """
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "report_daily", f"theme_analysis_v2_{trade_date}.txt")
    if not os.path.exists(path):
        print(f"[主题过滤] 未找到主题评分报告: {path}")
        return {}

    themes = {}
    section = None
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()
    for ln in lines:
        ln = ln.strip()
        if ln.startswith('### 第一部分'):
            section = 'mainline'
            continue
        if ln.startswith('### 第二部分'):
            section = 'rotation'
            continue
        if ln.startswith('### 第三部分'):
            section = 'junk'
            continue
        if ln.startswith('### 主线与轮动交易决策表'):
            break
        if section not in ('mainline', 'rotation', 'junk'):
            continue
        if not (ln.startswith('▶') or ln.startswith('▸') or ln.startswith('✕')):
            continue
        m = re.match(r'^[▶▸✕]\s+(\S+)\s+\[([^\]]+)\](.*)$', ln)
        if not m:
            continue
        theme, stage, rest = m.group(1), m.group(2), m.group(3)
        _num = lambda pat: (lambda mm: float(mm.group(1)) if mm else 0.0)(re.search(pat, rest))
        _int = lambda pat: (lambda mm: int(float(mm.group(1))) if mm else 0)(re.search(pat, rest))
        trend = _num(r'趋势([\d.]+)')
        senti = _num(r'情绪([\d.]+)')
        comp = _num(r'综合([\d.]+)')
        # 主线类型分级 V1.0 附加字段（无则留空，兼容旧报告）
        _mt = re.search(r'(情绪\+趋势共振|情绪主线|趋势主线)', rest)
        _mq = re.search(r'质量(\d+)', rest)
        _ms = re.search(r'策略:([^\s|]+)', rest)
        themes[theme] = {
            'theme': theme, 'stage': stage, 'kind': section,
            'mainline_type': _mt.group(1) if _mt else '',
            'mainline_quality': int(_mq.group(1)) if _mq else 0,
            'trading_style': _ms.group(1) if _ms else '',
            'trend_score': trend,
            'sentiment_score': senti,
            'composite_score': comp if comp else trend,  # 主线行无综合，用趋势兜底
            'zt_count': _int(r'涨停(\d+)'),
            'migration_score': _num(r'迁移([\d.]+)'),
            'stage_v6': _LC_STAGE_V6.get(stage, '调整'),
            # V6 兼容默认字段（报告未提供）
            'capital_score': 0, 'continuation_score': 50,
            'risk_score': 50, 'confidence': 0,
            'continuation_tag': '', 'leader': '', 'divergence_buy': False,
            'trade_signal': '看多' if section == 'mainline' else ('回避' if section == 'junk' else '关注'),
        }
    return themes


def filter_by_top_themes(result_df, top_n=15, mode='filter'):
    """
    主题筛选 / 共振评分 - 使用 theme_analysis_v2 报告（主线+轮动主题）

    加载 report_daily/theme_analysis_v2_{date}.txt，提取"核心主线阵营(▶)"与
    "潜在轮动与接力机会(▸)"全部主题作为过滤范围，然后匹配股票并注入评分字段。

    参数：
        result_df: 待过滤的股票DataFrame
        top_n: 保留（兼容旧签名；主线+轮动为主题范围，不截断）
        mode: 'filter'=二元过滤（淘汰不匹配股票，用于跟踪池）
              'resonance'=共振评分（保留全部股票，注入共振系数，用于突破股池）

    返回：
        mode='filter': 过滤后的DataFrame（仅保留匹配股票）
        mode='resonance': 全部股票DataFrame + 共振系数列
    """
    if result_df.empty:
        return result_df

    # ===== 1. 加载主线+轮动主题（读取 theme_analysis_v2 报告）=====
    theme_report_data = _load_mainline_rotation_themes(TRADE_DATE)
    if not theme_report_data:
        print(f"[主题过滤] 主题评分报告不可用，跳过过滤")
        # 确保至少有所属主题列
        if '所属主题' not in result_df.columns:
            result_df['所属主题'] = ''
        return result_df

    # 主线+轮动全部作为过滤范围；回避区(✕)主题单独处理，命中时标注"(回避)"
    junk_themes_info = {t: v for t, v in theme_report_data.items() if v.get('kind') == 'junk'}
    keep_themes_info = {t: v for t, v in theme_report_data.items() if v.get('kind') != 'junk'}
    keep_themes = set(keep_themes_info.keys())

    print(f"\n[主题过滤] 主线+轮动 -> 保留 {len(keep_themes)} 个主题:")
    for t, info in sorted(
            keep_themes_info.items(),
            key=lambda x: (0 if x[1].get('kind') == 'mainline' else 1,
                           -x[1].get('composite_score', 0))):
        print(f"  [{info.get('kind', ''):<8}] {t:<16} stage={info.get('stage', ''):<4} "
              f"趋势{info.get('trend_score', 0):<5.1f} 涨停{info.get('zt_count', 0)}")
    if junk_themes_info:
        print(f"  回避区主题 {len(junk_themes_info)} 个: {', '.join(sorted(junk_themes_info))}")
    print()

    # ===== 2. 加载主题配置（只保留有效主题）=====
    theme_cfg = {}
    cfg_path = os.path.join(BASE_DIR, 'theme.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            all_themes = json.load(f).get('HOT_THEMES', {})
            for theme_name in keep_themes:
                if theme_name in all_themes:
                    theme_cfg[theme_name] = all_themes[theme_name]

    # ===== 3. 从 JSON 缓存加载主题-个股映射 =====
    try:
        theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = _load_theme_stock_map_from_json()
    except Exception as e:
        print(f"[主题过滤] load_theme_stock_map_from_json 失败: {e}")
        import traceback
        traceback.print_exc()
        return _filter_by_top_themes_fallback(result_df, keep_themes, theme_cfg)

    # ===== 4. 遍历股票，匹配主题并注入V6多维评分 =====
    keep = []
    matched_themes = []
    match_scores = []
    secondary_themes_list = []
    resonance_coeffs = []  # 共振系数（resonance mode）
    # V6 多维评分
    theme_stages = []
    theme_trends = []
    theme_capitals = []
    theme_sentiments = []
    theme_continuations = []
    theme_risks = []
    theme_confidences = []
    theme_signals = []
    theme_cont_tags = []
    theme_leaders = []
    theme_div_buy = []
    theme_mainline_types = []
    theme_mainline_qualities = []
    theme_mainline_labels = []
    theme_trading_styles = []

    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        # 收集该股票在所有保留主题中的匹配记录
        theme_hits = []
        for theme_name in keep_themes:
            stocks = theme_stock_map.get(theme_name, {})
            if ts_code in stocks:
                s_info = stocks[ts_code]
                s_score = s_info.get('score', 0) if isinstance(s_info, dict) else 0
                theme_hits.append((theme_name, s_score))

        if theme_hits:
            theme_hits.sort(key=lambda x: -x[1])
            found_theme = theme_hits[0][0]
            secondary_theme = theme_hits[1][0] if len(theme_hits) >= 2 and theme_hits[1][1] > 0 else ''
            keep.append(True)
            matched_themes.append(found_theme)
            match_scores.append(theme_hits[0][1] if theme_hits[0][1] > 0 else 100)
            secondary_themes_list.append(secondary_theme)
            # 从V6结果注入字段
            vi = keep_themes_info.get(found_theme, {})
            theme_stages.append(vi.get("stage", ""))
            theme_trends.append(vi.get("trend_score", 0))
            theme_capitals.append(vi.get("capital_score", 0))
            theme_sentiments.append(vi.get("sentiment_score", 0))
            theme_continuations.append(vi.get("continuation_score", 0))
            theme_risks.append(vi.get("risk_score", 0))
            theme_confidences.append(vi.get("confidence", 0))
            theme_signals.append(vi.get("trade_signal", ""))
            theme_cont_tags.append(vi.get("continuation_tag", ""))
            theme_leaders.append(vi.get("leader", ""))
            theme_div_buy.append(vi.get("divergence_buy", ""))
            theme_mainline_types.append(vi.get("mainline_type", ""))
            theme_mainline_qualities.append(vi.get("mainline_quality", 0))
            theme_mainline_labels.append(vi.get("mainline_quality_label", ""))
            theme_trading_styles.append(vi.get("trading_style", ""))
            # 共振系数 = f(信号, 阶段, 延续分)（阶段用 V3 生命周期映射后的 V6 阶段）
            resonance_coeffs.append(_calc_resonance_coeff(
                vi.get("trade_signal", ""),
                vi.get("stage_v6", vi.get("stage", "")),
                vi.get("continuation_score", 50),
            ))
        else:
            # 无主线/轮动主题匹配：检查是否属于回避区主题（标注"(回避)"，不入选）
            junk_hit = ''
            for jname in junk_themes_info:
                if ts_code in theme_stock_map.get(jname, {}):
                    junk_hit = jname
                    break
            if junk_hit:
                jv = junk_themes_info[junk_hit]
                keep.append(False)
                matched_themes.append(f"{junk_hit}(回避)")
                match_scores.append(0)
                secondary_themes_list.append('')
                theme_stages.append(jv.get("stage", ""))
                theme_trends.append(jv.get("trend_score", 0))
                theme_capitals.append(jv.get("capital_score", 0))
                theme_sentiments.append(jv.get("sentiment_score", 0))
                theme_continuations.append(jv.get("continuation_score", 0))
                theme_risks.append(jv.get("risk_score", 0))
                theme_confidences.append(jv.get("confidence", 0))
                theme_signals.append(jv.get("trade_signal", "回避"))
                theme_cont_tags.append(jv.get("continuation_tag", ""))
                theme_leaders.append(jv.get("leader", ""))
                theme_div_buy.append(jv.get("divergence_buy", ""))
                theme_mainline_types.append(jv.get("mainline_type", "非主线"))
                theme_mainline_qualities.append(jv.get("mainline_quality", 0))
                theme_mainline_labels.append(jv.get("mainline_quality_label", ""))
                theme_trading_styles.append(jv.get("trading_style", ""))
                # 回避区主题：共振系数压到最低
                resonance_coeffs.append(0.3)
            else:
                keep.append(False)
                matched_themes.append('')
                match_scores.append(0)
                secondary_themes_list.append('')
                theme_stages.append('')
                theme_trends.append(0)
                theme_capitals.append(0)
                theme_sentiments.append(0)
                theme_continuations.append(0)
                theme_risks.append(0)
                theme_confidences.append(0)
                theme_signals.append('')
                theme_cont_tags.append('')
                theme_leaders.append('')
                theme_div_buy.append('')
                theme_mainline_types.append('')
                theme_mainline_qualities.append(0)
                theme_mainline_labels.append('')
                theme_trading_styles.append('')
                # 无主题匹配：基础共振系数 0.5
                resonance_coeffs.append(0.5)

    # ===== 5. 过滤/共振 处理 =====
    before = len(result_df)

    if mode == 'filter':
        # ── 二元过滤模式：只保留匹配股票（跟踪池） ──
        result_df = result_df[keep].reset_index(drop=True)
        kept_indices = [i for i in range(len(keep)) if keep[i]]

        result_df['所属主题'] = [matched_themes[i] for i in kept_indices]
        result_df['主题匹配度'] = [match_scores[i] for i in kept_indices]
        result_df['次强主题'] = [secondary_themes_list[i] for i in kept_indices]
        result_df['共振系数'] = [resonance_coeffs[i] for i in kept_indices]
        # V6 多维评分
        result_df['所属状态'] = [theme_cont_tags[i] for i in kept_indices]
        result_df['主题趋势分'] = [theme_trends[i] for i in kept_indices]
        result_df['主题情绪分'] = [theme_sentiments[i] for i in kept_indices]
        result_df['主题阶段'] = [theme_stages[i] for i in kept_indices]
        result_df['主题资金分'] = [theme_capitals[i] for i in kept_indices]
        result_df['主题延续分'] = [theme_continuations[i] for i in kept_indices]
        result_df['主题风险分'] = [theme_risks[i] for i in kept_indices]
        result_df['主题置信度'] = [theme_confidences[i] for i in kept_indices]
        result_df['主题信号'] = [theme_signals[i] for i in kept_indices]
        result_df['主题龙头'] = [theme_leaders[i] for i in kept_indices]
        result_df['非一日游阶段'] = [theme_stages[i] for i in kept_indices]
        result_df['确认天数'] = [0 for _ in kept_indices]
        result_df['龙头序列'] = [theme_leaders[i] for i in kept_indices]
        result_df['分歧买点'] = [theme_div_buy[i] for i in kept_indices]
        result_df['主线类型'] = [theme_mainline_types[i] for i in kept_indices]
        result_df['主线质量分'] = [theme_mainline_qualities[i] for i in kept_indices]
        result_df['主线质量'] = [theme_mainline_labels[i] for i in kept_indices]
        result_df['交易方式'] = [theme_trading_styles[i] for i in kept_indices]
    else:
        # ── 共振模式：保留全部股票，不匹配的注入空值（突破股池） ──
        kept_indices = list(range(len(result_df)))
        result_df = result_df.reset_index(drop=True)
        result_df['共振系数'] = resonance_coeffs
        result_df['所属主题'] = matched_themes
        result_df['主题匹配度'] = match_scores
        result_df['次强主题'] = secondary_themes_list
        result_df['所属状态'] = theme_cont_tags
        result_df['主题趋势分'] = theme_trends
        result_df['主题情绪分'] = theme_sentiments
        result_df['主题阶段'] = theme_stages
        result_df['主题资金分'] = theme_capitals
        result_df['主题延续分'] = theme_continuations
        result_df['主题风险分'] = theme_risks
        result_df['主题置信度'] = theme_confidences
        result_df['主题信号'] = theme_signals
        result_df['主题龙头'] = theme_leaders
        result_df['非一日游阶段'] = theme_stages
        result_df['确认天数'] = [0] * len(result_df)
        result_df['龙头序列'] = theme_leaders
        result_df['分歧买点'] = theme_div_buy
        result_df['主线类型'] = theme_mainline_types
        result_df['主线质量分'] = theme_mainline_qualities
        result_df['主线质量'] = theme_mainline_labels
        result_df['交易方式'] = theme_trading_styles

    print(f"[主题过滤] {'共振评分' if mode=='resonance' else '过滤'}后 {before} -> {len(result_df)} 只 (mode={mode})")
    return result_df


def _calc_resonance_coeff(signal, stage, continuation_score):
    """
    计算个股-主题共振系数 (0.5 ~ 1.5)

    核心逻辑：
      个股技术与主题热度的共振强度。
      主题越强、阶段越早期、延续分越高 → 共振系数越大 → 评分加权越高。
      无主题匹配时系数=0.5（不死，但有惩罚）。

    公式：
      共振系数 = signal_weight * stage_weight * cont_weight
        signal_weight: 强买1.3 / 看多1.15 / 关注1.0 / 持有0.9 / 中性0.8
        stage_weight:  启动1.2 / 主升1.1 / 调整1.0 / 筑底0.9 / 高潮0.8 / 衰退0.7
        cont_weight:   1.0 + (continuation_score - 50)/200
        范围：0.5 ~ 1.5
    """
    signal_map = {'强买': 1.30, '看多': 1.15, '关注': 1.00, '持有': 0.90, '中性': 0.80}
    stage_map = {'启动': 1.20, '主升': 1.10, '调整': 1.00, '筑底': 0.90, '高潮': 0.80, '衰退': 0.70}
    signal_weight = signal_map.get(signal, 0.60)
    stage_weight = stage_map.get(stage, 0.80)
    cont_weight = 1.0 + (float(continuation_score) - 50) / 200
    coeff = signal_weight * stage_weight * cont_weight
    return round(max(min(coeff, 1.5), 0.5), 2)


def _filter_by_top_themes_fallback(result_df, valid_themes, theme_cfg):
    """降级版主题匹配算法：当match_theme_stocks不可用时使用"""
    print("[主题过滤] 使用降级匹配逻辑")
    
    keep = []
    matched_themes = []
    match_scores = []
    
    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        
        best_theme = ''
        best_score = 0
        
        # 检查核心公司
        for theme_name in valid_themes:
            cfg = theme_cfg.get(theme_name, {})
            core_companies = cfg.get('core_companies', [])
            if stock_name in core_companies:
                best_theme = theme_name
                best_score = 100
                break
        
        if best_theme:
            keep.append(True)
            matched_themes.append(best_theme)
            match_scores.append(best_score)
        else:
            keep.append(False)
            matched_themes.append('')
            match_scores.append(0)
    
    before = len(result_df)
    result_df = result_df[keep].reset_index(drop=True)
    result_df['所属主题'] = [matched_themes[i] for i in range(len(matched_themes)) if keep[i]]
    result_df['主题匹配度'] = [match_scores[i] for i in range(len(match_scores)) if keep[i]]
    
    print(f"[主题过滤] 降级过滤后 {before} -> {len(result_df)} 只")
    return result_df


def add_themes_to_stocks_no_filter(result_df):
    """
    给股票添加主题信息，但不做过滤（保留所有股票）
    
    参数：
        result_df: 待添加主题的股票DataFrame
        
    返回：
        添加了主题字段的DataFrame（保留所有股票）
    """
    if result_df.empty:
        return result_df
    
    # 1. 加载 V6 引擎结果，获取所有主题及状态
    keep_themes = []
    theme_state_map = {}
    
    try:
        v6_data = _load_v6_result(TRADE_DATE)
        if v6_data:
            for r in v6_data:
                tname = r.get('theme', '')
                if tname:
                    keep_themes.append(tname)
                    theme_state_map[tname] = {
                        'theme_state': r.get('trade_signal', ''),
                        'trend_score': float(r.get('trend_score', 0) or 0),
                        'sentiment_score': float(r.get('sentiment_score', 0) or 0),
                        'composite_score': float(r.get('composite_score', 0) or 0),
                        'forward_alpha': float(r.get('forward_alpha', 0) or 0),
                        'forward_signal': r.get('forward_signal', ''),
                        'alpha_gate': r.get('alpha_gate', ''),
                        'cycle_phase': r.get('stage', ''),
                        'confirmed_days': 0,
                        'leader_sequence': r.get('leader', ''),
                    }
    except Exception as e:
        print(f"[添加主题] 读取V6结果失败: {e}")
    
    if not keep_themes:
        print("[添加主题] 无主题数据，仅返回原始DataFrame")
        return result_df
    
    # 2. 加载主题配置
    theme_cfg = {}
    cfg_path = os.path.join(BASE_DIR, 'theme.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            all_themes_cfg = json.load(f).get('HOT_THEMES', {})
            for theme_name in keep_themes:
                if theme_name in all_themes_cfg:
                    theme_cfg[theme_name] = all_themes_cfg[theme_name]
    
    # 3. 从 JSON 缓存加载主题-个股映射
    try:
        theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = _load_theme_stock_map_from_json()
    except Exception as e:
        print(f"[添加主题] load_theme_stock_map_from_json 失败: {e}")
        return result_df
    
    # 4. 遍历股票，匹配主题并注入主题状态
    matched_themes = []
    match_scores = []
    theme_states_list = []
    theme_trends = []
    theme_sentiments = []
    cycle_phases = []
    confirmed_days_list = []
    leader_sequences_list = []
    secondary_themes_list = []
    
    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        theme_hits = []
        for theme_name in keep_themes:
            stocks = theme_stock_map.get(theme_name, {})
            if ts_code in stocks:
                s_info = stocks[ts_code]
                s_score = s_info.get('score', 0) if isinstance(s_info, dict) else 0
                theme_hits.append((theme_name, s_score))
        
        if theme_hits:
            theme_hits.sort(key=lambda x: -x[1])
            found_theme = theme_hits[0][0]
            secondary_theme = theme_hits[1][0] if len(theme_hits) >= 2 and theme_hits[1][1] > 0 else ''
            matched_themes.append(found_theme)
            match_scores.append(theme_hits[0][1] if theme_hits[0][1] > 0 else 100)
            secondary_themes_list.append(secondary_theme)
            st = theme_state_map.get(found_theme, {})
            theme_states_list.append(st.get("theme_state", ""))
            theme_trends.append(st.get("trend_score", 0))
            theme_sentiments.append(st.get("sentiment_score", 0))
            cycle_phases.append(st.get("cycle_phase", ""))
            confirmed_days_list.append(st.get("confirmed_days", 0))
            leader_sequences_list.append(st.get("leader_sequence", ""))
        else:
            matched_themes.append('')
            match_scores.append(0)
            secondary_themes_list.append('')
            theme_states_list.append('')
            theme_trends.append(0)
            theme_sentiments.append(0)
            cycle_phases.append('')
            confirmed_days_list.append(0)
            leader_sequences_list.append('')
    
    # 5. 注入字段（不过滤，保留所有股票）
    result_df = result_df.copy()
    result_df['所属主题'] = matched_themes
    result_df['主题匹配度'] = match_scores
    result_df['次强主题'] = secondary_themes_list
    result_df['所属状态'] = theme_states_list
    result_df['主题趋势分'] = theme_trends
    result_df['主题情绪分'] = theme_sentiments
    result_df['非一日游阶段'] = cycle_phases
    result_df['确认天数'] = confirmed_days_list
    result_df['龙头序列'] = leader_sequences_list
    
    print(f"[添加主题] 已给 {len(result_df)} 只股票添加主题信息，其中 {sum(1 for t in matched_themes if t)} 只有匹配主题")
    return result_df


# =========================
# 主程序
# =========================
def run(target_date=None, simple_mode=False):
    """运行量化选股分析
    
    Args:
        target_date: 目标日期，格式为 'YYYYMMDD'，默认为当前交易日
        simple_mode: 简易模式，只输出个股和评分，不进行AI分析、不发送微信
    """
    global TRADE_DATE
    
    # 如果指定了目标日期，验证并设置
    if target_date:
        target_date = str(target_date)
        TRADE_DATE = validate_trade_date(target_date)
        print(f"\n{'='*60}")
        print(f"[回溯模式] 目标日期: {TRADE_DATE}")
        print(f"{'='*60}\n")
    
    # =========================
    # 主题状态全景（来自 Theme Score V2 引擎，含阶段迁移预测）
    # =========================
    print("\n========== 主题状态全景（来自 Theme Score V2 引擎）==========\n")
    sector_text_his = ""
    try:
        # 优先加载 V2 评分结果
        v2_data = _load_v2_theme_scores(TRADE_DATE)
        if v2_data:
            lines = []
            date_str = f" ({TRADE_DATE})" if TRADE_DATE else ""
            lines.append(f"━━ V2 Theme Score 引擎报告{date_str} ━━")
            lines.append(f"共{len(v2_data)}个主题 | 趋势分(长期动量)+情绪分(短期爆发)→综合分 | 含阶段迁移预测&交易动作建议")
            lines.append("")

            # ── 排名表 ──
            lines.append(f"{'#':<3} {'主题':<12} {'趋势':<6} {'情绪':<6} {'综合':<6} {'涨停':<4} {'状态':<8} {'迁移分':<6} {'目标':<8} {'交易动作':<8}")
            lines.append("")
            for i, r in enumerate(v2_data[:30]):
                lines.append(
                    f"{i+1:<3} {r['theme']:<12} {r['trend_score']:<6.1f} {r['sentiment_score']:<6.1f} "
                    f"{r['composite_score']:<6.1f} {r.get('zt_count',0):<4} {r.get('theme_state',''):<8} "
                    f"{r.get('migration_score',0):<6.1f} {r.get('target_state',''):<8} {r.get('trade_action',''):<8}"
                )
            lines.append("")

            # ── 迁移预测详情 ──
            lines.append("【阶段迁移预测（未来3-5个交易日）- 6因子分解】")
            lines.append(f"{'#':<3} {'主题':<12} {'P':<5} {'M':<5} {'C':<5} {'$':<5} {'L':<5} {'R':<5} {'方向':<6} {'目标':<8} {'动作':<8} {'理由'}")
            lines.append("")
            for i, r in enumerate(v2_data[:7]):
                mf = r.get('migration_factors', {}) or {}
                direction_icon = '↑向上' if r.get('migration_direction') == 'upward' else ('↓向下' if r.get('migration_direction') == 'downward' else '→震荡')
                lines.append(
                    f"{i+1:<3} {r['theme']:<12} "
                    f"{mf.get('proximity',0):<5.0f} {mf.get('momentum',0):<5.0f} {mf.get('confirmation',0):<5.0f} "
                    f"{mf.get('money_resonance',0):<5.0f} {mf.get('leader_health',0):<5.0f} {mf.get('regime',0):<5.0f} "
                    f"{direction_icon:<6} {r.get('target_state',''):<8} {r.get('trade_action',''):<8} {r.get('action_reason','')}"
                )
            lines.append("")

            # ── 龙头/中军一览 ──
            lines.append("【龙头 & 中军一览（V2引擎评分）】")
            for i, r in enumerate(v2_data[:7]):
                leader = r.get('leader_name', '') or '-'
                core = r.get('core_name', '') or '-'
                lines.append(f"  {r['theme']:<12} 龙头:{leader} 中军:{core}")
            lines.append("")

            sector_text_his = "\n".join(lines)
        else:
            print(f"  V2评分数据不可用")

            # 回退：使用原 FUSION/V8 引擎当兜底
            v6_data = _load_fusion_result(TRADE_DATE)
            if v6_data:
                lines = []
                is_fusion = '融合分' in (v6_data[0] if v6_data else {})
                if is_fusion:
                    meta = {}
                    try:
                        fusion_path = os.path.join(BASE_DIR, 'theme_alpha_v6', 'cache',
                                                   f'theme_fusion_rank_{TRADE_DATE}.json')
                        if os.path.exists(fusion_path):
                            with open(fusion_path, encoding='utf-8') as _ff:
                                _fp = json.load(_ff)
                                meta = _fp.get("meta", {})
                    except Exception:
                        pass
                    mode = meta.get('模式', '')
                    date_str = f" ({TRADE_DATE})" if TRADE_DATE else ""
                    lines.append(f"FUSION 融合排名报告{date_str} | {mode} | 共{len(v6_data)}个主题（V2数据不可用，回退）")
                    lines.append(f"{'#':<3} {'主题':<14} {'融合分':<6} {'V6':<6} {'V8':<6} {'奖惩':<5} {'信号':<18} {'操作建议':<10}")
                    lines.append("")
                    for i, r in enumerate(v6_data[:30]):
                        lines.append(f"{i+1:<3} {r.get('主题',''):<14} {r.get('融合分',0):<6.1f} "
                                     f"{r.get('V6分',0):<6.1f} {r.get('V8分',0):<6.1f} "
                                     f"{r.get('奖惩',0):<5} {r.get('信号',''):<18} {r.get('操作建议',''):<10}")
                else:
                    v6_date = v6_data[0].get('trade_date', '')
                    date_str = f" ({v6_date})" if v6_date else ""
                    source_label = "V8.0" if any('T_start' in r for r in v6_data[:5]) else "V6.2"
                    lines.append(f"Theme Alpha {source_label} 引擎报告{date_str} (共{len(v6_data)}个主题, V2回退)")
                    lines.append(f"{'#':<3} {'主题':<14} {'综合':<6} {'D阶段':<8} {'动作':<12} {'T_s':<4} {'T_M':<4} {'R_v':<6} {'趋势':<6} {'资金':<6} {'信号':<6}")
                    lines.append("")
                    for i, r in enumerate(v6_data[:30]):
                        t_start = r.get('T_start', '')
                        t_ma = r.get('T_MA', '')
                        r_vol = r.get('R_volume', '')
                        d_stage = r.get('D阶段', r.get('stage', ''))
                        d_action = r.get('策略动作', '')
                        if t_start == '' or t_start is None: t_start = '-'
                        if t_ma == '' or t_ma is None: t_ma = '-'
                        if r_vol == '' or r_vol is None: r_vol = '-'
                        else: r_vol = f"{r_vol:.2f}" if isinstance(r_vol, (int, float)) else r_vol
                        lines.append(f"{i+1:<3} {r.get('theme',''):<14} {r.get('composite_score',0):<6.1f} "
                                     f"{str(d_stage):<8} {str(d_action):<12} "
                                     f"{str(t_start):<4} {str(t_ma):<4} {str(r_vol):<6} "
                                     f"{r.get('trend_score',0):<6.1f} {r.get('capital_score',0):<6.1f} "
                                     f"{r.get('trade_signal',''):<6}")
                lines.append("")
                sector_text_his = "\n".join(lines)
            else:
                print(f"  引擎结果均不可用")
                sector_text_his = ""
    except Exception as e:
        print(f"⚠️ 读取主题状态简报失败: {e}")
        import traceback
        traceback.print_exc()
        sector_text_his = ""
    
    emotion_stage = "强"
    
    # 市场情绪 -> 直接读取 market_analysis.py 已生成的 txt 报告
    ma_txt, ma_position, ma_reason = _load_market_analysis_result(TRADE_DATE)

    # 从txt中提取趋势分和市场状态（用于策略判断）
    import re as _re
    _m_ts = _re.search(r'总趋势分.*?:\s*(\d+\.?\d*)', ma_txt)
    ts = float(_m_ts.group(1)) if _m_ts else 50.0
    _m_ms = _re.search(r'市场状态:\s*(.+)', ma_txt)
    ms = _m_ms.group(1).strip() if _m_ms else "震荡"
    _m_it = _re.search(r'指数趋势.*?:\s*(\d+\.?\d*)', ma_txt)
    it = float(_m_it.group(1)) if _m_it else 50.0
    _m_tt = _re.search(r'主题趋势.*?:\s*(\d+\.?\d*)', ma_txt)
    tt = float(_m_tt.group(1)) if _m_tt else 50.0
    tp = ma_position
    pr = ""

    # 根据大盘状态确定操作策略
    if "主升浪" in ms or ts >= 75:
        market_action = "大盘强势，聚焦强势股池追涨"
    elif "上升" in ms or ts >= 50:
        market_action = "大盘偏强，强势股池为主，低吸辅助"
    elif "退潮" in ms or "主跌" in ms or ts < 40:
        market_action = "大盘弱势，重点关注低吸股池潜伏"
    else:
        market_action = "大盘经历中期调整，关注中线股池B浪机会"

    # 直接用 txt 报告原文作为 emotion_text
    emotion_text = ma_txt if ma_txt else "（无大盘分析报告）"
    
    # 过滤掉【指数趋势分】【市场趋势总评分】【市场情绪】三个区块（手机端冗余）
    emotion_text = _re.sub(
        r'【指数趋势分】[^\n]*\n?', '', emotion_text
    )
    emotion_text = _re.sub(
        r'【市场趋势总评分】[^\n]*\n?', '', emotion_text
    )
    emotion_text = _re.sub(
        r'【市场情绪】[^\n]*\n?', '', emotion_text
    )
    
    print(emotion_text)

    result = []

    # 批量预取：解决高频API调用问题
    # 在循环之前一次性下载所有股票数据到本地缓存

    market = get_market()
    if market is not None and not market.empty:
        all_codes = market['ts_code'].tolist()
        print(f"\n[批量预取] 共 {len(all_codes)} 只股票，开始下载历史数据...")
        batch_prefetch_hist_data(all_codes)
        print(f"[批量预取] 完成，后续循环将命中本地缓存\n")

    total = len(market)

    for idx, row in market.iterrows():

        ts_code = row['ts_code']

        #print(f"[{idx+1}/{total}] {ts_code}")

        try:

            hist = get_hist_data(ts_code)

            if hist is None or len(hist) < 80:
                continue
            #print(f"[{idx+1}/{total}] {ts_code}")
            ok = strategy(
                hist,
                ts_code,
                emotion_stage,
                total_mv=row.get('total_mv', 0)
            )
            
            if ok:

                result.append({
                    '代码': ts_code,
                    '名称': row.get('name', ''),
                    '现价': row.get('close', 0),
                    '涨跌幅': row.get('pct_chg', 0),
                    '成交额': row.get('amount', 0),
                    '总市值（亿元）': row.get('total_mv', 0)/10000,
                    'total_market_cap': row.get('total_mv', 0) * 10000,  # 转换为元
                    'market_cap': row.get('total_mv', 0) * 10000,       # 兼容字段
                })

                print("✅ 命中突破:", ts_code, row.get('name', ''))
               
        except Exception as e:

            print(ts_code, e)

            continue

    # 量能爆发+宽幅震荡池：直接读取 volume_surge_select.py 每日生成的报告文本（不重复扫描/生成）
    # 详见下方"构建量能爆发+宽幅震荡池文本"段

    # =========================
    # 输出
    # =========================
    result_df = pd.DataFrame(result)

    if result_df.empty:
        print("无结果")
        return

    # =========================
    # 主题过滤：注入所属主题等字段
    # =========================
    if not result_df.empty:
        result_df = filter_by_top_themes(result_df, mode='resonance')


    # =========================
    # 突破股池：统一评分 + 二波形态 + 筹码
    # =========================
    ranked_stocks = []
    for idx, row in result_df.iterrows():
        ts_code = row['代码']
        name = row['名称']
        theme_name = str(row.get('所属主题', ''))
        
        df = get_hist_data(ts_code)
        if df is None or len(df) < 20 or not isinstance(df, pd.DataFrame) or 'close' not in df.columns:
            continue
        
        try:
            today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100 if len(df) >= 2 else float(row.get('涨跌幅', 0))
            today_amount = 0.0
            today_close = float(df['close'].iloc[-1])
            if 'amount' in df.columns:
                today_amount = round(float(df['amount'].iloc[-1]) / 100000, 2)
            today_turnover = get_cached_turnover(ts_code)
            
            theme_trend_score = float(row.get('主题趋势分', 0))
            theme_sentiment_score = float(row.get('主题情绪分', 0))
            _ml_type = str(row.get('主线类型', ''))
            _ml_quality = float(row.get('主线质量分', 0) or 0)
            # 共振系数统一纳入软天花板计算（避免硬顶扎堆）
            resonance_coeff = float(row.get('共振系数', 1.0))
            integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(
                df, ts_code, theme_name, theme_trend_score, theme_sentiment_score,
                mainline_type=_ml_type, mainline_quality=_ml_quality,
                extra_mult=resonance_coeff,
            )
            integrated_score_orig = details.get('基础裸分', integrated_score)
            tech = calc_tech_indicators(df, ts_code, TRADE_DATE)
            
            stock_data = {
                '代码': ts_code, '名称': name, '现价': today_close,
                '涨跌幅': today_pct, '成交额': today_amount, '换手率': today_turnover,
                '所属主题': theme_name, '整合评分': integrated_score, '失败概率': failure_prob,
                '推荐理由': recommendation,
                'Alpha评分': details.get('Alpha评分', 0), 'Alpha信号': details.get('Alpha信号', ''),
                '量能爆发': details.get('量能爆发', 0),
                '所属状态': str(row.get('所属状态', '')),
                '主题趋势分': float(row.get('主题趋势分', 0)), '主题情绪分': float(row.get('主题情绪分', 0)),
                '非一日游阶段': str(row.get('非一日游阶段', '')),
                '确认天数': int(row.get('确认天数', 0)),
                '龙头序列': str(row.get('龙头序列', '')),
                '共振系数': round(resonance_coeff, 2),
                '原始整合评分': round(integrated_score_orig, 1),
                'YRI历史总分': details.get('YRI历史总分', 0), 'YRI标签': details.get('YRI标签', ''),
                'YRI最大连板': details.get('YRI最大连板', 0),
                # V10 完整评分详情（传递给AI prompt）
                '评分详情': details,
                # 强势股池回测优化：保存买入日时点特征用于硬过滤
                # 回测验证：量比<1胜率0%, 距MA20>20%胜率9%, 近20日涨幅>40%胜率0%, 距MA5<0%胜率20%
                '当日量比': _calc_vol_ratio(df),
                '距MA5_pct': _calc_dist_ma(df, 5),
                '距MA10_pct': _calc_dist_ma(df, 10),
                '距MA20_pct': _calc_dist_ma(df, 20),
                '近20日涨幅_pct': _calc_pct_n(df, 20),
            }
            ranked_stocks.append(stock_data)
        except Exception as e:
            print(f"[突破评分] {ts_code} {name} 失败: {e}")
            continue
    
    # 每个主题只保留失败概率最低的3只
    theme_groups = {}
    for s in ranked_stocks:
        theme = s['所属主题']
        theme_groups.setdefault(theme, []).append(s)
    filtered_stocks = []
    for theme, stocks in theme_groups.items():
        filtered_stocks.extend(sorted(stocks, key=lambda x: x['失败概率'])[:3])
    ranked_stocks = filtered_stocks
    
    # 突破 + 二波信号
    for s in ranked_stocks:
        try:
            current_price = s.get('现价', 0)
            # 筹码数据已移除
            
            # 突破信号
            breakout_result = detect_breakout(s['代码'], pro)
            s['突破信号'] = breakout_result.get('signal', '')
            s['突破评分'] = breakout_result.get('breakout_score', 0)
            
            # 二波形态检测+共振评分
            wave2_result = detect_wave2_reversal(s['代码'], pro)
            s['二波形态'] = wave2_result.get('pattern', '其他')
            s['二波信号'] = wave2_result.get('signal', '')
            s['二波评分'] = wave2_result.get('wave2_score', 0)
            s['入场价'] = wave2_result.get('entry_price', 0)
            s['止损价'] = wave2_result.get('stop_loss', 0)
            s['目标价'] = wave2_result.get('target', 0)
        except Exception:
            s['突破信号'] = ''; s['突破评分'] = 0
            s['二波信号'] = '非二波形态'; s['二波评分'] = 0

    # 过滤掉假突破的股票
    before_filter = len(ranked_stocks)
    ranked_stocks = [s for s in ranked_stocks if '假突破' not in s.get('突破信号', '')]
    after_filter = len(ranked_stocks)
    if before_filter != after_filter:
        print(f"[突破股池] 过滤假突破: {before_filter} -> {after_filter} 只")

    # ====================================================================
    # 强势股池硬过滤优化
    # 1. 近20日涨幅 ≤ 80%  —— 涨幅透支后进场即被套
    # 2. 距MA20 ≤ 30%      —— 远离均线表示追高过度
    # 3. 距MA10 ≥ 0%      —— 跌破MA10表示趋势走弱
    # ====================================================================
    before_strong_filter = len(ranked_stocks)
    strong_pass = []
    strong_filtered_reasons = {'近20日涨幅>80%': 0, '距MA20>30%': 0, '距MA10<0%': 0}
    for s in ranked_stocks:
        pct_20d = s.get('近20日涨幅_pct', 0) or 0
        dist_ma20 = s.get('距MA20_pct', 0) or 0
        dist_ma10 = s.get('距MA10_pct', 0) or 0
        if pct_20d > 80:
            strong_filtered_reasons['近20日涨幅>80%'] += 1
            continue
        if dist_ma20 > 30:
            strong_filtered_reasons['距MA20>30%'] += 1  
            continue
        if dist_ma10 < 0:
            strong_filtered_reasons['距MA10<0%'] += 1
            continue
        strong_pass.append(s)
    ranked_stocks = strong_pass
    after_strong_filter = len(ranked_stocks)
    if before_strong_filter != after_strong_filter:
        reason_str = ' | '.join([f"{k}:{v}只" for k, v in strong_filtered_reasons.items() if v > 0])
        print(f"[强势股池优化] 过滤透支/追高股: {before_strong_filter} -> {after_strong_filter} 只 ({reason_str})")

    # =========================
    # Chip Alpha 注入（突破股池）
    # =========================
    if ranked_stocks:
        print(f"[ChipAlpha-突破股池] 批量计算 {len(ranked_stocks)} 只股票的筹码Alpha...")
        _chip_results = batch_chip_alpha(ranked_stocks, lookback_days=20)
        for s in ranked_stocks:
            _code = s.get('代码', '')
            _chip_r = _chip_results.get(_code)
            _factors = extract_chip_alpha_factors(_chip_r)
            s.update(_factors)
            _sug, _reason = get_chip_alpha_suggestion(s)
            s['ChipSuggestion'] = _sug
            s['ChipSuggestionReason'] = _reason

        # V5 升级（无额外API调用）
        _v5_results = batch_chip_alpha_v5(_chip_results)
        for s in ranked_stocks:
            _code = s.get('代码', '')
            _v5_r = _v5_results.get(_code)
            _v5_factors = extract_chip_alpha_v5_factors(_v5_r)
            s.update(_v5_factors)

    # 按整合评分从高到低排序
    ranked_stocks = sorted(ranked_stocks, key=lambda x: -x.get('整合评分', 0))
    lines = []
    lines.append("")
    lines.append("🔥 突破股池 (按整合评分排序)")
    lines.append("")
    
    top_stocks = ranked_stocks[:10]
    for i, s in enumerate(top_stocks, 1):
        alpha_val = s.get('Alpha评分', 0)
        alpha_sig = s.get('Alpha信号', '')
        alpha_str = f" (Alpha={alpha_val:.1f} {alpha_sig})" if alpha_sig else f" (Alpha={alpha_val:.1f})"
        _chip_score = s.get('ChipTrendScore', 50)
        _cre_score = s.get('CRE_Score', 50)
        _mom_score = s.get('ChipMomentum_Score', 50)
        _chip_str = f" 筹码={_chip_score:.0f}/CRE={_cre_score:.0f}/动量={_mom_score:.0f}"
        lines.append(f"【第{i}名】{s['名称']} ({s['代码']}) 现价={s['现价']:.2f} 涨跌幅={s['涨跌幅']:+.2f}% 成交额={s['成交额']:.2f}亿 量能爆发={s['量能爆发']:.2f}{alpha_str}{_chip_str}")
        lines.append(f"  整合评分: {s['整合评分']:.1f} | 失败概率: {s['失败概率']:.1f}%")
        _det = s.get('评分详情', {})
        if _det and isinstance(_det, dict):
            _mom = _det.get('动量爆发力', 0)
            _cap = _det.get('资金行为', 0)
            _pos = _det.get('位置安全性', 0)
            _hot = _det.get('热度', 0)
            _fun = _det.get('基本面', 0)
            _pen = _det.get('追高惩罚', 0)
            _ldb = _det.get('龙头加分', 0)
            _rec = _det.get('辨识度加分', 0)
            _msl = _det.get('资金斜率', 0)
            _mps = _det.get('资金持续性', 0)
            _md = _det.get('资金扩散率', 0)
            _fl = _det.get('基本面逻辑', [])
            _qbi = _det.get('量比', 0)
            lines.append(f"  V10: 动量={_mom:.0f} 资金={_cap:.0f} 位置={_pos:.0f} 热度={_hot:.0f} 基本面={_fun:.0f}")
            lines.append(f"  资金: 斜率={_msl:.1f}/持续={_mps:.0%}/扩散={_md:.0%} | 量比={_qbi:.1f} | 惩罚={_pen:.0f} 龙头={_ldb:.0f} 辨识={_rec:.0f}")
            if _fl:
                lines.append(f"  基本面因子: {' | '.join(str(x) for x in _fl[:4])}")
        _chip_sug = s.get('ChipSuggestion', '观望等待')
        _chip_sug_reason = s.get('ChipSuggestionReason', '')
        lines.append(f"  筹码建议: {_chip_sug} | {_chip_sug_reason}")
        # V5 Alpha维度
        _v5_s = s.get('Alpha_Structure', 50)
        _v5_f = s.get('Alpha_Flow', 50)
        _v5_m = s.get('Alpha_Momentum', 50)
        _v5_c = s.get('Alpha_Composite', 50)
        _v5_g = s.get('Alpha_Grade', 'C')
        _v5_risk = s.get('Risk_Score', 50)
        _v5_risk_lv = s.get('Risk_Level', 'Medium')
        _v5_state = s.get('Trend_State', 'Unknown')
        _v5_next = s.get('Next_State', '')
        _v5_next_p = s.get('Next_Prob', 0)
        _v5_action = s.get('Action', 'Hold')
        _v5_conf = s.get('Confidence', 50)
        _v5_dim_str = f"V5: 结构={_v5_s:.0f}/资金={_v5_f:.0f}/动量={_v5_m:.0f} | 复合={_v5_c:.0f}({_v5_g})"
        _v5_risk_str = f"风险={_v5_risk:.0f}({_v5_risk_lv})"
        _v5_trend_str = f"{_v5_state}→{_v5_next}({_v5_next_p:.0f}%)" if _v5_next else _v5_state
        _v5_act_str = f"{_v5_action}({_v5_conf:.0f}%)"
        lines.append(f"  V5 Trend: {_v5_dim_str} | {_v5_risk_str} | {_v5_trend_str} | {_v5_act_str}")
        # 决策结论摘要
        _v5_summary = s.get('DecisionSummary', '')
        if _v5_summary:
            lines.append(f"  V5 决策: {s.get('Alpha_Interpret', '')}")
            _v5_pv = s.get('Price_vs_Center', '')
            if _v5_pv:
                lines.append(f"          {_v5_pv}")
            lines.append(f"          {s.get('Risk_Detail', '')}")
            lines.append(f"          {s.get('Trans_Detail', '')}")
            lines.append(f"          {s.get('Act_Detail', '')}")
        # OS 交易机会评分
        _os_val = s.get('Opportunity_Score', 50)
        _os_det = s.get('OS_Details', '')
        lines.append(f"  OS 机会: {_os_val:.0f}/100 | {_os_det}")
        # 主题信息
        cycle = s.get('非一日游阶段', '') or s.get('所属状态', '')
        confirm_days = s.get('确认天数', 0)
        cycle_str = f"非一日游:{cycle}" if cycle else ""
        days_str = f"{confirm_days}天" if confirm_days > 0 else ""
        leader_seq = s.get('龙头序列', '')
        leader_str = f"龙头:{leader_seq}" if leader_seq else ""
        info_parts = [s['所属主题']]
        if cycle_str: info_parts.append(cycle_str)
        if days_str: info_parts.append(days_str)
        if leader_str: info_parts.append(leader_str)
        lines.append(f"  主题: {' | '.join(info_parts)}")
        # 突破
        bs = s.get('突破信号', '')
        bsc = s.get('突破评分', 0)
        lines.append(f"  突破: 评分={bsc} | {bs}" if bs else f"  突破: 评分={bsc}")
        # YRI
        yri_total = s.get('YRI历史总分', 0)
        if yri_total > 0:
            yri_tags = s.get('YRI标签', '')
            yri_lb = s.get('YRI最大连板', 0)
            lines.append(f"  YRI: 总分={yri_total:.0f} 最大连板={yri_lb}板 标签={yri_tags}")
        lines.append("")
    
    hot_money_open_text = "\n".join(lines)
    print(hot_money_open_text)
    
    icpm_top10_list = []
    for s in ranked_stocks[:20]:
        icpm_top10_list.append({
            'code': s.get('代码', ''),
            'name': s.get('名称', ''),
            'theme': s.get('所属主题', ''),
            'open_score': s.get('整合评分', 0),
        })


    # =========================
    # 构建量能爆发+宽幅震荡池文本（直接读取 volume_surge_select.py 每日生成的报告，不重复扫描/生成）
    # =========================
    volume_surge_swing_text = ""
    try:
        _vs_report_path = os.path.join(REPORT_DIR, f"volume_surge_{TRADE_DATE}.md")
        if os.path.exists(_vs_report_path):
            with open(_vs_report_path, encoding='utf-8') as _vf:
                _vs_report_full = _vf.read().strip()
            # 只截取"🎯 算法输出 TOP3"段（到下一个 ## 段落标题为止），避免强买/观察等信号干扰 AI
            _vs_marker = "## 🎯 算法输出 TOP3"
            _vs_idx = _vs_report_full.find(_vs_marker)
            if _vs_idx >= 0:
                _vs_next = _vs_report_full.find("\n## ", _vs_idx + len(_vs_marker))
                volume_surge_swing_text = _vs_report_full[_vs_idx:] if _vs_next < 0 else _vs_report_full[_vs_idx:_vs_next]
            else:
                volume_surge_swing_text = _vs_report_full
            volume_surge_swing_text = volume_surge_swing_text.strip()
            print(f"[量能宽幅震荡] 已读取 {_vs_report_path} 的TOP3段（{len(volume_surge_swing_text)} 字符）")
        else:
            print(f"[量能宽幅震荡] 报告不存在: {_vs_report_path}（请先运行 volume_surge_select.py）")
    except Exception as _e:
        print(f"[量能宽幅震荡] 报告读取失败: {_e}")
    if not volume_surge_swing_text:
        volume_surge_swing_text = ("🎯 算法输出 TOP3（次日开盘买入候选）\n"
                                   "今日无信号或报告未生成（请先运行 volume_surge_select.py 生成当日报告）")
        print(volume_surge_swing_text)

    # =========================
    # 实盘交易建议（直接读取主题评分报告 theme_analysis_v2）
    # =========================
    trade_advice_text = ""
    try:
        theme_report = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "report_daily", f"theme_analysis_v2_{TRADE_DATE}.txt")
        if os.path.exists(theme_report):
            with open(theme_report, 'r', encoding='utf-8') as f:
                trade_advice_text = f.read().strip()
            print(f"[实盘建议] 主题评分报告: {theme_report}")
        else:
            print(f"[实盘建议] 未找到主题评分报告: {theme_report}")
    except Exception as e:
        print(f"[实盘建议] 读取失败: {e}")
        trade_advice_text = ""

    # =========================
    # 回踩买点形态信号（中报优质股池买点）
    # =========================
    def _load_pullback_buy_signals(trade_date: str) -> str:
        """读取增强择时报告中的回踩买点形态信号（原 pullback_buy.py 已合并进 enhanced_timing_bull_all，只读当日文件，缺失直接跳过）"""
        # enhanced 输出在 solo/report_daily，兼容 d:\mystock\report_daily
        cand = [
            os.path.join(r"D:\mystock\solo\report_daily", f"enhanced_timing_bull_all_{trade_date}.csv"),
            os.path.join(REPORT_DIR, f"enhanced_timing_bull_all_{trade_date}.csv"),
        ]
        files = [p for p in cand if os.path.exists(p)]
        if not files:
            return ""
        latest = max(files, key=os.path.getmtime)
        try:
            df = pd.read_csv(latest, encoding="utf-8-sig")
            if df.empty:
                return ""
            # 新旧格式兼容：20260807 起含 形态阶段/回踩买点分 等回踩形态列（pullback_buy 已合并）；
            # 更早的旧格式文件无这些列，降级用 洗盘修复分+评级+无冲击+修正后评分 过滤，避免整表被丢弃
            is_new = "形态阶段" in df.columns and "回踩买点分" in df.columns
            stage_col = "形态阶段" if is_new else "洗盘修复标签"
            score_col = "回踩买点分" if is_new else "洗盘修复分"
            has = df[df.get(stage_col, "").fillna("") != ""].copy()
            if has.empty:
                return ""
            # 双确认过滤（20260808 与 push_washout_recovery.py 同步）:
            #   形态: 回踩买点分>=60（原 PullbackScore 语义；旧格式用洗盘修复分代替）
            #   综合风控: 无兑现冲击 + 修正后评分>0 + (评级S/A/B 或 中报业绩正增长)
            #   注: 评级门槛会误杀"突破前夜"形态——美迪西/奥浦迈 20260806 评级D(未突破)但业绩
            #       +507%/+229%、无冲击，次日放量突破升 S/A。评级不再一票否决，业绩方向+兑现
            #       冲击才是拦截核心（伪信号如中欣氟材: 业绩-48.5%+冲击⚠️+评分清零，仍被拦）。
            score = pd.to_numeric(has.get(score_col, pd.Series(np.nan, index=has.index)), errors="coerce")
            grade = has.get("修正后胜率分级", "").astype(str).str.strip()
            impact = has.get("兑现冲击过滤", "").astype(str)
            corr = pd.to_numeric(has.get("修正后评分", pd.Series(np.nan, index=has.index)), errors="coerce").fillna(0)

            def _parse_growth(v):
                s = str(v).replace("%", "").replace("+", "").strip()
                try:
                    return float(s)
                except Exception:
                    return float("nan")

            if "中报业绩亮点" in has.columns:
                growth = pd.to_numeric(has["中报业绩亮点"].apply(_parse_growth), errors="coerce")
            else:
                growth = pd.Series(float("nan"), index=has.index)
            # is_new 格式要求次日操作=✅可买入（与 push_washout_recovery buy_mask 一致，
            # 排除"回踩完成/首阳确认"等买点分达标但未到买点的观察类票）；旧格式无此列不限制
            if is_new and "次日操作" in has.columns:
                _op_ok = has["次日操作"].astype(str).str.strip() == "✅ 次日可买入"
            else:
                _op_ok = pd.Series(True, index=has.index)
            hi = has[
                (score >= 60) &
                (_op_ok) &
                (impact.str.contains("✅", na=False)) &
                (corr > 0) &
                (grade.isin(["S", "A", "B"]) | (growth.fillna(-1) > 0))
            ].copy()
            if hi.empty:
                return ""
            # 排序：次日可买入 > 观察 > 不买入（旧格式无次日操作列，按评分排序）
            if is_new:
                _op_order = {"✅ 次日可买入": 0, "⚠️ 次日观察等回踩": 1, "⚠️ 观察": 1, "❌ 仅观察不买入": 2, "❌ 等待首阳": 2}
                hi["_oo"] = hi.get("次日操作", "").map(_op_order).fillna(9)
                hi = hi.sort_values(["_oo", score_col], ascending=[True, False])
            else:
                hi = hi.sort_values(["修正后评分", score_col], ascending=[False, False])
            lines = []
            lines.append("【中报优质股池买点】")
            lines.append(f"数据来源：洗盘修复专题形态信号（{score_col}≥60+评级S/A/B+无冲击，双确认共{len(hi)}只），形态=洗盘→放量首阳→缩量回踩不破")
            lines.append("")
            for _, row in hi.iterrows():
                code = str(row.get("代码", ""))
                name = row.get("名称", "")
                stage = row.get(stage_col, "")
                decision = str(row.get("次日操作", "")).strip()
                score_v = float(row.get(score_col, 0) or 0)
                fyd = str(row.get("首阳日期", ""))[:8]
                pdays = row.get("回踩天数", 0)
                grade = str(row.get("修正后胜率分级", "")).strip()
                wr_col = "洗盘修复分" if is_new else "结构增强分"
                wr = row.get(wr_col, np.nan)
                theme = str(row.get("主题", "")).strip()
                trade_dec = str(row.get("交易决策", "")).strip()
                # 20260808 一致化：✅次日可买入 的票若 enhanced 仍标"低胜率规避"（未突破时的
                # 追认措辞，非否决信号），改写为中性描述，避免误导 AI 判 ❌次日不买入；
                # 并附业绩方向（美迪西/奥浦迈 20260806 评级D+业绩+507%/+229% 次日放量突破大涨）
                if decision == "✅ 次日可买入":
                    if "低胜率规避" in trade_dec:
                        trade_dec = "回踩完成待放量突破"
                    _g = str(row.get("中报业绩亮点", "")).strip()
                    if _g and str(_g).lower() != "nan":
                        trade_dec += f"，业绩:{_g}"
                wr_s = f"{float(wr):.0f}" if pd.notna(wr) else "-"
                yang_s = f"首阳:{fyd[4:6]}/{fyd[6:8]}" if fyd and str(fyd) != "nan" else "无首阳"
                pull_s = f"回踩{pdays}天" if pdays and int(pdays) > 0 else "首阳当日"
                theme_s = f" 主题:{theme}" if theme and theme != "nan" else ""
                decision_s = f" 次日操作:{decision}" if decision and decision != "nan" else ""
                grade_s = f" 评级:{grade}" if grade and grade != "nan" else ""
                lines.append(f"【{name}】({code}){decision_s} 形态:{stage} {score_col}:{score_v:.1f} {yang_s} {pull_s} | {wr_col}:{wr_s}{grade_s} 决策:{trade_dec}{theme_s}")
            return "\n".join(lines)
        except Exception as e:
            print(f"[回踩买点] 加载失败: {e}")
            return ""

    pullback_buy_text = _load_pullback_buy_signals(TRADE_DATE)
    if pullback_buy_text:
        print("[回踩买点] 已加载中报优质股池买点信号")

    # =========================
    # ELD 业绩预增买点 TOP3（读取 eld 每日评分报告 V2 前三，追加为报告最后一段）
    # =========================
    def _load_eld_top3(trade_date: str) -> str:
        r"""读取 ELD 报告 CSV（D:\mystock\report_daily\eld_report_YYYYMMDD.csv），按 final_score_v2 取 TOP3"""
        cand = [
            os.path.join(r"D:\mystock\report_daily", f"eld_report_{trade_date}.csv"),
            os.path.join(REPORT_DIR, f"eld_report_{trade_date}.csv"),
            os.path.join(r"D:\mystock\solo\report_daily", f"eld_report_{trade_date}.csv"),
        ]
        files = [p for p in cand if os.path.exists(p)]
        if not files:
            return ""
        latest = max(files, key=os.path.getmtime)
        try:
            df = pd.read_csv(latest, encoding="utf-8-sig")
            if df.empty:
                return ""
            df = df.sort_values("final_score_v2", ascending=False).head(3)
            _sig_map = {"BUY": "买入", "IGNORE": "忽略", "OBSERVE": "观望"}
            lines = [
                "【ELD 业绩预增买点 TOP3】",
                f"数据来源：{os.path.basename(latest)}（业绩预增≥30%池·V2综合评分前三）",
                "",
            ]
            for i, row in df.iterrows():
                _sig = str(row.get("earnings_buy_signal", "")).strip().upper()
                _sig_cn = _sig_map.get(_sig, _sig or "-")
                _bp = float(row.get("reference_buy_price") or 0)
                _sl = float(row.get("stop_loss_price") or 0)
                _bp_s = f"{_bp:.2f}" if _bp > 0 else "-"
                _sl_s = f"{_sl:.2f}" if _sl > 0 else "-"
                lines.append(
                    f"{len(lines)-2}.{row.get('name','')}({row.get('ts_code','')}) "
                    f"V2:{float(row.get('final_score_v2') or 0):.0f} "
                    f"预增+{float(row.get('forecast_pct') or 0):.0f}% "
                    f"行业{float(row.get('industry_score') or 0):.0f} "
                    f"机构:{row.get('institution_state','') or '-'} "
                    f"买点:{_sig_cn} "
                    f"Buy:{float(row.get('buy_score') or 0):.0f}({row.get('buy_score_level','') or '-'}) "
                    f"参考价{_bp_s} 止损{_sl_s}"
                )
            return "\n".join(lines)
        except Exception as e:
            print(f"[ELD TOP3] 加载失败: {e}")
            return ""

    eld_top3_text = _load_eld_top3(TRADE_DATE)
    if eld_top3_text:
        print("[ELD TOP3] 已加载业绩预增买点TOP3")

    # =========================
    # ETF操作提示（读取主线轮动汇总报告的精简版）
    # =========================
    etf_tips_text = ""
    summary_path = rf'D:\mystock\report_daily\etf_mainline_summary_{TRADE_DATE}.txt'
    try:
        if os.path.exists(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as f:
                full = f.read().strip()
            # 截取最简版：只取TOP5 + 持仓 + 执行清单前面的部分
            lines = full.split('\n')
            simple_lines = []
            for line in lines:
                if 'TOP5' in line or '综合分' in line or '持仓' in line or '收益' in line or '调仓' in line or '执行清单' in line:
                    simple_lines.append(line)
                elif '【一、' in line or '【二、' in line or '【三、' in line or '【四、' in line:
                    simple_lines.append(line)
            etf_tips_text = '\n'.join(simple_lines[:30]) if simple_lines else full[:1000]
            print(f"[ETF提示] 汇总报告缩略: {summary_path}")
        else:
            etf_tips_text = ""
            print(f"[ETF提示] 未生成ETF决策报告")
    except Exception as e:
        print(f"[ETF提示] 读取失败: {e}")


    #return

    prompt = f"""
以下是我自己计算的量化分析结果：

**【大盘分析】**

{emotion_text}

**【今日主题分析情况】**
{trade_advice_text}

**【今日突破股池】**
{hot_money_open_text}
**【今日突破股池到此为止】**


请分析并输出内容：
开头以“这是大盘和个股推送微信消息”开头
标题：**每日复盘({TRADE_DATE})**
内容(分成以下部分，输出中也显示序号1、2、3、4、....段落名称及换行)：
1、**大盘分析**：严格按照上述“大盘分析”给定的内容进行分析，【严格按以下固定模板输出，带上适合手机阅读的换行符，禁止自由发挥格式】
** 市场分析
* 上证指数：XXXX | 成交额：XX万亿 | 涨跌比 X%
* 市场：XXXXX | 赚钱：XX 
* 风险：XXXXXX | 节奏：XXXXXX
** 仓位建议
* 当前目标：xx%   可加仓空间：xx%
* 正常区间：xx%～xx%   确认上限：xx%
* 一句话：XXXXX。
** 策略：XXXX
* 最终：XXXXXX
2、**主题分析**
【严格按以下固定模板输出，带上适合手机阅读的换行符，禁止自由发挥格式】
**主线**
** 核心主线1：XXXX（趋势主线/情绪主线/共振）
 **最佳子主题**：存储芯片（推荐理由：资金强力沉淀（迁移分净流入最密集）；涨停0家/最高0连板/资金净流入228320万元）
 **【龙头】标的**：688123.SH 聚辰股份
  - 角色：情绪领涨 / 超短爆发（0连板, 市值205亿, 成交额15.7亿）
  - 匹配动作：打板接力 / 右侧突破追买（建议仓位 10%）
 **【中军】标的**：603986.SH 兆易创新
  - 角色：容量承载 / 趋势慢牛（市值2927亿, 日成交额305.2亿）
  - 匹配动作：回踩5日/10日线分批低吸 / 通道网格做T（建议仓位 15%-20%）

** 核心主线2：XXXX（趋势主线/情绪主线/共振）
 **最佳子主题**：CXO/CRO/CDMO（推荐理由：涨停梯队最齐（含高位连板）；涨停5家/最高1连板/资金净流入282995万元）
 **【龙头】标的**：300363.SZ 博腾股份
  - 角色：情绪领涨 / 超短爆发（1连板, 市值111亿, 成交额13.8亿）
  - 匹配动作：打板接力 / 右侧突破追买（建议仓位 10%）
 **【中军】标的**：603259.SH 药明康德
  - 角色：容量承载 / 趋势慢牛（市值4619亿, 日成交额139.6亿）
  - 匹配动作：回踩5日/10日线分批低吸 / 通道网格做T（建议仓位 15%-20%）

** 核心主线3：XXXX（趋势主线/情绪主线/共振）
 **最佳子主题**：光模块（推荐理由：资金强力沉淀（迁移分净流入最密集）；涨停1家/最高1连板/资金净流入203252万元）
 **【龙头】标的**：002281.SZ 光迅科技
  - 角色：情绪领涨 / 超短爆发（1连板, 市值1598亿, 成交额132.9亿）
  - 匹配动作：打板接力 / 右侧突破追买（建议仓位 10%）
 **【中军】标的**：300308.SZ 中际旭创
  - 角色：容量承载 / 趋势慢牛（市值10760亿, 日成交额546.7亿）
  - 匹配动作：回踩5日/10日线分批低吸 / 通道网格做T（建议仓位 15%-20%）

  核心主线如有多个，依此类推......

** 轮动主题：XXXX/XXXX/XXXX  
** 避免杂毛：XXXX/XXXX/XXXX
3、**【ETF操作建议】**
{etf_tips_text}
输出要求：
- 当前持仓（名称、代码、收益、下次调仓）
- 操作建议的1、2、3(代码和名称、动量)
- 如果建议中有与主线主题一致的ETF，说明共振确认

4、**【今日突破股池分析】**
（综合动量爆发力、资金行为、位置安全性、热度、基本面五个维度评分）
（【最高优先级约束-严格数据边界】本段落只取"**【今日突破股池】**"和"**【今日突破股池到此为止】**"两个标记之间的数据中股票。
 严禁从以下任何其它数据区读取股票进入本段分析：
 - "🔥 量能爆发·强买信号"区（属第5部分量能爆发池，非本突破股池）
 - "📊 ETF操作提示"区及其下方的"ETF Alpha Ranking"、"TOP3 推荐买入"、"TOP10 排名"成份股
 - "📊 中线股池"区的B浪低点信号股
 - "🟢逢低买入"行下的个股
 如突破股池数据区为空，直接提示"今日无突破股池"，不要用其它股池的股票填补。
 本段最多分析前10名，必须严格按整合评分从高到低排序，不得自行增减股票）：  
**【重要】按整合评分从高到低排序分析前10名个股，每个股票内容力求精简：**    
- **【必须】严格用以下格式和要求显示，不要自行添加任何内容，力求精简：**
【第1名】**股票名** (代码)
【第2名】**股票名** (代码)
【第3名】**股票名** (代码)
依此往后
- 对每只股票进行详细分析，包括：
- 整合评分和失败概率
- V5决策的Buy(XX%) | 止损 | 操作建议
- 基本面因子摘要（利润增速/ROE/半年度预告/大宗交易）
- 所属主题和该主题的状态，以及非一日游阶段（含连续确认天数）和龙头序列
主题地位：【必须】直接输出规则判定结果，格式如下：
"主题与地位: 所属主题为XXX（情绪+趋势共振/情绪主线/趋势主线/轮动主题/非主线·质量XX）"
例如："主题与地位: 所属主题为小金属（情绪+趋势共振·质量83）"
例如："主题与地位: 所属主题为创新药（情绪+趋势共振·质量89）"
例如："主题与地位: 所属主题为工业金属（趋势主线·质量74）"
例如："主题与地位: 所属主题为新能源车（趋势主线·质量58）"
- 基本面Alpha评分（0-100分，越高越好）及中长线解读：
【评分标准】
- 80+分：强烈买入（中线目标收益20%+），核心持仓可长期持有
- 70-79分：买入（中线目标收益15%+），优质标的中长线持有
- 50-59分：中性（中线收益5-10%），收息/观望为主
- <40分：减仓/卖出，长线回避
【输出格式】Alpha评分=X分，信号=XXX | 中线建议：XXX | 长线建议：XXX
- <span style="color:red;">【重要提醒】如果主题情绪分持续多天走高，且趋势分也持续走高，说明主题有风险，<span style="color:red;">**突出建议勿追高！**</span></span>
- 如遇个股重大基本面风险，请在分析中标注"【警告】有重大风险"，但仍保留在列表中并说明理由。技术性风险无须提示和输出。
其它要求：
A直接过滤掉有基本面重大风险的个股：
- 近三个月内有定增预案
- 有大额减持公告
- 未来半年有大额解禁压力
- 有重大诉讼风险
- 有重大财务风险（如连续亏损、审计异常等）
- 有其他重大利空消息
B对于无重大风险的前30名个股，保持原有的综合评分排序，不要重新筛选和排序
C【最高优先级】所有技术面分析中的价格（MA均线价格、目标价、买点、止损位、支撑位、阻力位、现价、高点等）必须严格使用上方"【技术价位】"和"【参考位】"中提供的EXACT真实数据，禁止凭空编造任何价格数字或百分比！此项约束优先级高于其他所有分析要求。
C-2【高点定义】技术分析中的"前高/压力位"必须严格基于"【参考位-长线】"中的120/250/全历史高点价格，不能基于当前价格或短线高点随意外推。
C-3【主题地位判断】必须严格按照以下数字规则判断，YRI画像中的文字描述（如"历史级大妖/龙头/市场关注"等）仅供参考，不具有任何权重，绝不能作为突破以下数字阈线的依据：
- 龙头：YRI历史总分≥70 且 日均成交额≥5亿 且 最大连板≥3板（三者必须同时满足，缺一不可；核心是有历史连板基因，才是真正的主题龙头）
- 中军：YRI历史总分≥55 且 日均成交额≥5亿 且 最大连板≤2板（满足此三条的是稳定中军，即使YRI标签写了"龙头/历史大妖"也是中军）
- 补涨弹性：总分30-55，或 成交额<5亿，或 最大连板<2板（满足任一即定为此类）
- 后排跟风：总分<30 或 成交额<5000万
- 【绝对禁止】无论YRI画像如何描述，只要最大连板<3板，绝不能认定为龙头；最大连板≥3板但成交额<5亿，也绝不能认定为龙头
- 【输出格式】主题地位：XXX（如：龙头/中军/补涨弹性/后排跟风），必须严格输出这四个分类之一
- 【非一日游信息】如个股数据中包含"非一日游:XXX(连续X天)"和"龙头:XXX→XXX→XXX"字段，请结合这些信息判断主题的可持续性：
* 连续≥3天的"中期延续"主题更有持续性，龙头切换代表资金在板块内轮动挖掘
* 连续1-2天的"启动确认"主题需观察是否持续；首次进入确认线往往是最佳买点
D【价格错误检测】分析完成后，请核对：如果某只股票上方标注"现价=XXX元 MA20=YYY元"，而你的分析中写成了不同的价格数字，则你的分析错误，请立即修正。
E【禁止编造当日涨跌】绝对禁止说某股票"涨停"、"大涨"、"暴跌"等无依据的形容词。每只股票的"今日涨幅"在"整合评分精选量化股票池"区块中已明确标注为精确数值（如"今日涨幅: 5.32%"），必须直接引用该数值。严禁在未引用真实数据的情况下编造涨跌描述。

5、**【中报优质股池买点】**（回踩买点形态，PullbackScore≥60，形态=急跌洗盘→放量首阳→缩量回踩不破，次日存在二次启动概率）：
{pullback_buy_text}
（【数据边界】本段落只分析上方"【中报优质股池买点】"标记后列出的股票，严禁从其它数据区读取股票填入本段；若该段落为空则提示"今日无中报优质股池买点信号"。
【输出要求-最高优先级】每只股票第一行必须直接给出明确结论：✅次日可买入 / ⚠️次日观察等回踩 / ❌次日不买入，严格引用上方标注的"次日操作:"字段原值，禁止自行改判或美化；禁止把"❌仅观察不买入"或"⚠️观察"的股票描述成"形态健康可买入"。仅对"✅次日可买入"（回踩中）的个股补充次日买点与止损位，每只力求精简。
【买卖结论优先级】凡标注"次日操作:✅次日可买入"的个股，一律判定为✅次日可买入并给出次日买点/止损位；其"决策:"与"评级:"字段仅反映当日趋势确认程度（如"回踩完成待放量突破"=突破前夜，次日存在二次启动概率），不改变"✅次日可买入"的结论，严禁因评级低/决策含"规避"字样而改判"❌次日不买入"）

6、**【今日量能爆发+宽幅震荡池·算法输出 TOP3】**（近60天量能大幅放大+宽幅震荡，MACD即将/刚刚红柱，且非一波游）：
{volume_surge_swing_text}
【输出要求-第6段】本段只输出上方"算法输出 TOP3"的 3 只，禁止输出强买/观察/蓄势等其他信号。严格引用 TOP3 的排序（注意：TOP3排序依据是"距MA20"升序优先，回踩充分者靠前，评分仅作候选资格），每只按下列格式输出，必须保留"距MA20"字段，且**每只股票之间空一行（加一个空行分隔），便于手机阅读**：
TOP1：名称/代码, 评分, 距MA20=+x.x%, 主题, MACD信号

TOP2：名称/代码, 评分, 距MA20=+x.x%, 主题, MACD信号

TOP3：名称/代码, 评分, 距MA20=+x.x%, 主题, MACD信号


7、**【ELD 业绩预增买点 TOP3】**（业绩预增≥30%池·V2综合评分前三）：
{eld_top3_text}
【输出要求-第7段】本段只输出上方"【ELD 业绩预增买点 TOP3】"的 3 只，删除"数据来源"行，每只按下列格式输出，且**每只股票之间空一行（加一个空行分隔），便于手机阅读**：
名称：代码, V2=xx分, 预增+xx%, 行业热度xx分, 机构:xx, 买点:✅可买入/⚠️谨慎/❌禁止, Buy:xx分, 参考价xx, 止损xx
若 Buy<60（禁止）或机构=派发，务必标注风险提示；若该段落无数据则提示"今日无 ELD 业绩预增买点信号"。

------------------
以上全局格式要求：
- **Top10个股分析中，每只股票单独分段，用【股票名+代码】作为小标题，<span style="color:red;">加黑加粗显示</span>**
- 股票分析另起一行，分点说明
- 段落标题（即使以“##”开头的），也只需加粗即可，不用放大字体
- 风格简洁明了，适合手机阅读
- 返回MD格式，字体大小适合手机阅读
- **严格禁止添加本 prompt 中未指定的任何额外章节**（如热点追踪、风险扫描、投资建议书等），只分析 prompt 中已列出的数据（含第 7 段 ELD 业绩预增买点 TOP3）

"""
    if not simple_mode:
        print("\n========== Deepseek AI分析 ==========\n")
        prompt_file = os.path.join(CACHE_DIR, f"prompt_debug_{TRADE_DATE}.txt")
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"[DEBUG] Prompt已保存: {prompt_file}")
        #report = deepseek(prompt)
        report = deepseek(prompt, use_flash=False)
        print(report)

        try:
            ds_file = os.path.join(CACHE_DIR, f"Deepseek_Self_{TRADE_DATE}.md")
            with open(ds_file, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"✅ Deepseek报告已保存: {ds_file}")
        except Exception as e:
            print(f"⚠️ Deepseek报告保存失败: {e}")
        
        # 保存最终报告
        final_report = report
        
        # 先发送微信（即使报告保存失败也要发送）
        send_wechat(
            final_report,
            os.getenv("WECHAT_SCKEY")
        )
        # PushPlus 推送（支持markdown，增强阅读体验）
        send_pushplus(final_report, os.getenv("PUSHPLUS"))

        # 保存报告（带异常处理）
        try:
            report_file = os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.md")
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(final_report)
            print(f"✅ 报告已保存: {report_file}")
        except Exception as e:
            print(f"⚠️ 报告保存失败: {e}")

        try:
            html_file = os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.html")
            markdown_to_html_report(final_report, 
                                    output_file=html_file, 
                                    pdf_file=os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.pdf"), 
                                    title=f"复盘及精选个股({TRADE_DATE})"
                                    )
        except Exception as e:
            print(f"⚠️ HTML报告生成失败: {e}")
    else:
        print(f"\n{'='*60}")
        print(f"[简易模式] 跳过AI分析和微信发送")
        print(f"{'='*60}")

    #result = send_wechat_message(report)

# =========================
# YRI-H 历史辨识度评分（过去252个交易日，约1年）
# =========================
def _is_zt_day(pct_chg, ts_code):
    """判断单日是否涨停（主板9.8%+/双创19.8%+）"""
    if pct_chg is None:
        return False
    IS_CYB_KCB = (ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689'))
    zt_threshold = 19.8 if IS_CYB_KCB else 9.8
    return pct_chg >= zt_threshold


def calc_yri_history(ts_code, debug=False):
    """
    计算历史辨识度指标 YRI-H（满分100，基于过去252个交易日）
    
    返回: dict, 包含各项得分、总分、等级、标签、画像
    """
    ts_code = ts_code.strip()
    
    # 1. 获取历史日线数据（近1年）
    df = get_hist_data(ts_code)
    if df is None or len(df) < 30:
        return {
            "错误": f"历史数据不足（仅{len(df) if df is not None else 0}天）",
            "建议": "请确认股票代码是否正确，或先运行主程序缓存数据"
        }
    
    # 取最近252个交易日
    df = df.sort_values('trade_date').tail(252).reset_index(drop=True)
    n_days = len(df)
    
    # ========== 1. 计算基础指标 ==========
    zt_count = 0  # 涨停次数
    max_consec_zt = 0  # 最大连板
    current_consec = 0
    big_up_days = 0  # 大阳线（+5%以上）天次
    pct_chg_list = []  # 每日涨跌幅
    
    for _, row in df.iterrows():
        pct = row.get('pct_chg', 0)
        pct_chg_list.append(pct)
        
        if _is_zt_day(pct, ts_code):
            zt_count += 1
            current_consec += 1
            max_consec_zt = max(max_consec_zt, current_consec)
        else:
            current_consec = 0
        
        if pct >= 5:  # 大阳线计数（趋势股重要指标）
            big_up_days += 1
    
    # 计算辅助指标
    avg_pct_abs = sum([abs(x) for x in pct_chg_list]) / len(pct_chg_list) if pct_chg_list else 0
    first_close = df['close'].iloc[0]
    last_close = df['close'].iloc[-1]
    stock_return = (last_close / first_close - 1) * 100 if first_close > 0 else 0
    
    # 年化因子（如果实际交易日不足252天，按比例放大阈值分母）
    # 这样短周期数据不会因为观察期短就低估辨识度
    annual_factor = 252 / n_days if n_days > 0 else 1.0
    
    # 年化涨停/大阳次数（用于调整评分阈值）
    zt_count_annual = zt_count * annual_factor
    big_up_days_annual = big_up_days * annual_factor
    
    # 计算近1年最大滚动涨幅（用局部高点法，O(n)复杂度）
    closes = df['close'].values
    max_drawup = 0.0
    min_close_seen = closes[0]
    for c in closes:
        if c < min_close_seen:
            min_close_seen = c
        drawup = (c / min_close_seen - 1) * 100 if min_close_seen > 0 else 0
        max_drawup = max(max_drawup, drawup)
    max_excess = max(max_drawup, stock_return)
    
    # 历史新高检查
    is_new_high = last_close >= df['close'].max() * 0.98
    
    # ========== 2. 涨停基因 G（30分） ==========
    # 趋势股涨停少但大阳线多，组合打分
    # 使用年化涨停次数评估"涨停基因强度"
    G_raw = 0
    if zt_count_annual >= 20:
        G_raw += 30
    elif zt_count_annual >= 15:
        G_raw += 25
    elif zt_count_annual >= 10:
        G_raw += 20
    elif zt_count_annual >= 5:
        G_raw += 15
    elif zt_count_annual >= 2:
        G_raw += 10
    elif zt_count_annual >= 1:
        G_raw += 5
    
    # 大阳线补偿（趋势股没有涨停但有很多5%+阳线时的补偿，最多+25分）
    # 每6天出现一次大阳线给3分
    big_up_bonus = min(int(big_up_days_annual / 6) * 3, 25)
    
    # 趋势股"资金关注"特征：平均日波动>2%也说明有资金在操作
    volatility_bonus = 0
    if avg_pct_abs >= 3.0:
        volatility_bonus = 8
    elif avg_pct_abs >= 2.5:
        volatility_bonus = 6
    elif avg_pct_abs >= 2.0:
        volatility_bonus = 4
    elif avg_pct_abs >= 1.5:
        volatility_bonus = 2
    
    G_score = min(G_raw + big_up_bonus + volatility_bonus, 30)
    
    # ========== 3. 空间记忆 S（25分） ==========
    S_score = 0
    # 连板记忆（15分）
    if max_consec_zt >= 6:
        S_score += 15
    elif max_consec_zt >= 5:
        S_score += 12
    elif max_consec_zt >= 4:
        S_score += 10
    elif max_consec_zt >= 3:
        S_score += 7
    elif max_consec_zt >= 2:
        S_score += 4
    elif max_consec_zt >= 1:
        S_score += 2
    
    # 趋势空间（10分）- 最大涨幅体现"翻倍空间"
    trend_space_score = 0
    if max_excess >= 200:
        trend_space_score = 10
    elif max_excess >= 100:
        trend_space_score = 8
    elif max_excess >= 60:
        trend_space_score = 6
    elif max_excess >= 40:
        trend_space_score = 4
    elif max_excess >= 25:
        trend_space_score = 3
    elif max_excess >= 15:
        trend_space_score = 2
    
    S_score = min(S_score + trend_space_score, 25)
    
    # ========== 4. 历史资金活跃度 A（20分） ==========
    # 4a. 当日真实换手率（从缓存读取）
    today_turnover = get_cached_turnover(ts_code)
    avg_turnover = today_turnover if today_turnover and today_turnover > 0 else 0
    
    # 4b. 若有量数据，vol均值/当日vol的比值估算历史换手
    if 'vol' in df.columns and avg_turnover <= 0:
        vol_1y_avg = df['vol'].mean()
        today_vol = df['vol'].iloc[-1]
        if today_vol > 0:
            if vol_1y_avg / today_vol > 1.5:
                avg_turnover = 5.0
            elif vol_1y_avg / today_vol > 1.0:
                avg_turnover = 3.0
            else:
                avg_turnover = 1.5
    
    # 4c. A评分 - 换手率（12分）
    if avg_turnover >= 15:
        A_turnover = 12
    elif avg_turnover >= 10:
        A_turnover = 11
    elif avg_turnover >= 8:
        A_turnover = 10
    elif avg_turnover >= 6:
        A_turnover = 8
    elif avg_turnover >= 5:
        A_turnover = 7
    elif avg_turnover >= 3:
        A_turnover = 5
    elif avg_turnover >= 2:
        A_turnover = 3
    elif avg_turnover >= 1:
        A_turnover = 2
    else:
        A_turnover = 1
    
    # 4d. 日均成交额百分位（8分）- 用历史数据估算
    # 注意：tushare的amount字段单位是"千元"，需乘以1000转为元，再除以10000转为万
    avg_amount_1y = 0.0
    if 'amount' in df.columns:
        avg_amount_1y = df['amount'].mean() * 1000 / 10000  # = amount.mean() / 10
    elif 'vol' in df.columns and 'close' in df.columns:
        avg_amount_1y = (df['vol'] * df['close']).mean() / 10000
    
    # 成交额分级评分
    A_percentile_bonus = 0
    if avg_amount_1y > 50000:  # 50亿+
        A_percentile_bonus = 8
    elif avg_amount_1y > 20000:  # 20亿+
        A_percentile_bonus = 6
    elif avg_amount_1y > 10000:  # 10亿+
        A_percentile_bonus = 5
    elif avg_amount_1y > 5000:  # 5亿+
        A_percentile_bonus = 4
    elif avg_amount_1y > 2000:  # 2亿+
        A_percentile_bonus = 3
    elif avg_amount_1y > 1000:  # 1亿+
        A_percentile_bonus = 2
    else:
        A_percentile_bonus = 1
    
    A_score = min(A_turnover + A_percentile_bonus, 20)
    
    # ========== 5. 股性弹性 E（15分） ==========
    # 用近1年最大涨幅+累计涨幅综合判断
    E_by_max = 0
    if max_excess >= 200:
        E_by_max = 15
    elif max_excess >= 100:
        E_by_max = 12
    elif max_excess >= 60:
        E_by_max = 10
    elif max_excess >= 40:
        E_by_max = 8
    elif max_excess >= 30:
        E_by_max = 6
    elif max_excess >= 20:
        E_by_max = 4
    elif max_excess >= 10:
        E_by_max = 2
    else:
        E_by_max = 1
    
    # 累计涨幅加分（趋势股累计涨幅大）
    E_by_return = 0
    if stock_return >= 100:
        E_by_return = 5
    elif stock_return >= 50:
        E_by_return = 3
    elif stock_return >= 20:
        E_by_return = 2
    elif stock_return >= 0:
        E_by_return = 1
    
    E_score = min(E_by_max + E_by_return, 15)
    
    # ========== 6. 关注度持续性 C（10分） ==========
    # 6a. 热榜天数
    hot_days = 0
    try:
        if os.path.exists(DC_HOT_CACHE_DIR):
            files = sorted([f for f in os.listdir(DC_HOT_CACHE_DIR) if f.startswith('dc_hot_')])
            for f in files:
                try:
                    fpath = os.path.join(DC_HOT_CACHE_DIR, f)
                    hdf = pd.read_csv(fpath)
                    if '代码' in hdf.columns:
                        if ts_code in hdf['代码'].head(50).values:
                            hot_days += 1
                    elif 'ts_code' in hdf.columns:
                        if ts_code in hdf['ts_code'].head(50).values:
                            hot_days += 1
                except:
                    continue
    except:
        pass
    
    # 6b. 市场波动关注度 proxy - 平均日波动大说明市场反复关注
    market_attention_score = 0
    if avg_pct_abs >= 3.5:
        market_attention_score = 10
    elif avg_pct_abs >= 2.8:
        market_attention_score = 8
    elif avg_pct_abs >= 2.2:
        market_attention_score = 6
    elif avg_pct_abs >= 1.8:
        market_attention_score = 4
    elif avg_pct_abs >= 1.4:
        market_attention_score = 3
    else:
        market_attention_score = 2
    
    # 6c. 热榜天数评分
    hot_score = 0
    if hot_days > 50:
        hot_score = 10
    elif hot_days >= 20:
        hot_score = 8
    elif hot_days >= 10:
        hot_score = 6
    elif hot_days >= 5:
        hot_score = 4
    elif hot_days >= 2:
        hot_score = 2
    else:
        hot_score = 1
    
    C_score = max(hot_score, market_attention_score)
    
    # ========== 7. 总分计算 ==========
    total_score = G_score + S_score + A_score + E_score + C_score
    total_score = round(total_score, 1)
    
    # 等级判定
    if total_score >= 80:
        level = "历史大妖/板块核心中军"
    elif total_score >= 65:
        level = "强股性活跃标的/趋势龙头"
    elif total_score >= 50:
        level = "中等辨识度"
    elif total_score >= 30:
        level = "股性一般/非核心"
    else:
        level = "历史冷门/低辨识度"
    
    # 核心历史标签
    tags = []
    if zt_count >= 10:
        tags.append("涨停常客")
    if max_consec_zt >= 3:
        tags.append("连板记忆")
    if big_up_days >= 30:
        tags.append("大阳趋势")
    if avg_turnover >= 5:
        tags.append("高换手")
    elif avg_turnover >= 3:
        tags.append("中高换手")
    if avg_amount_1y > 10000:
        tags.append("资金深度参与")
    if max_excess >= 100:
        tags.append("高弹性")
    elif max_excess >= 50:
        tags.append("中高弹性")
    if hot_days >= 10 or market_attention_score >= 4:
        tags.append("市场关注")
    if stock_return >= 50:
        tags.append("趋势上涨")
    
    if not tags:
        tags = ["低辨识度"]
    
    # 股性画像
    if total_score >= 80:
        portrait = "板块核心中军/历史级大妖，资金深度参与，股性极度活跃，具有强烈市场记忆点"
    elif total_score >= 65:
        portrait = "趋势龙头/板块活跃股，资金关注度高，具备良好波段和短线价值"
    elif total_score >= 50:
        portrait = "股性中等，有一定表现，主题共振时可参与"
    elif total_score >= 30:
        portrait = "股性一般，非核心标的，适合有明确主题催化时谨慎参与"
    else:
        portrait = "冷门股/低辨识度，缺乏资金关注，非主线行情不建议参与"
    
    result = {
        "股票代码": ts_code,
        "分析天数": n_days,
        "G_涨停基因": {
            "涨停次数": zt_count,
            "大阳线天次(+5%)": big_up_days,
            "涨停得分": G_raw,
            "大阳补偿": big_up_bonus,
            "得分": G_score
        },
        "S_空间记忆": {
            "最大连板": max_consec_zt,
            "最大涨幅(%)": round(max_excess, 1),
            "连板得分": S_score - trend_space_score,
            "趋势空间得分": trend_space_score,
            "得分": S_score
        },
        "A_资金活跃度": {
            "日均换手率(%)": round(avg_turnover, 2),
            "日均成交额(万元)": round(avg_amount_1y, 0),
            "换手得分": A_turnover,
            "成交额百分位": A_percentile_bonus,
            "得分": A_score
        },
        "E_股性弹性": {
            "近1年最大涨幅(%)": round(max_excess, 1),
            "近1年累计涨幅(%)": round(stock_return, 1),
            "得分": E_score
        },
        "C_关注度持续性": {
            "热榜天数": hot_days,
            "平均日波动(%)": round(avg_pct_abs, 2),
            "得分": C_score
        },
        "YRI历史总分": total_score,
        "等级": level,
        "核心历史标签": tags,
        "股性画像": portrait
    }
    
    if debug:
        print(f"\n{'='*60}")
        print(f"  YRI-H 历史辨识度评分 - {ts_code}")
        print(f"{'='*60}")
        print(f"  样本: {n_days}天  涨停{zt_count}次  大阳{big_up_days}次  连板{max_consec_zt}天")
        print(f"  G 涨停基因(30): {G_score}  (涨停{G_raw} + 大阳{big_up_bonus})")
        print(f"  S 空间记忆(25):  {S_score}  (连板{S_score - trend_space_score} + 趋势空间{trend_space_score})")
        print(f"  A 资金活跃(20):  {A_score}  (换手{A_turnover} + 成交{A_percentile_bonus})")
        print(f"  E 股性弹性(15):  {E_score}  (最大涨幅{max_excess:.0f}% + 累计{stock_return:.0f}%)")
        print(f"  C 关注度(10):   {C_score}  (热榜{hot_days}天/波动{avg_pct_abs:.1f}%)")
        print(f"  {'-'*60}")
        print(f"  YRI-H 总分: {total_score}  →【{level}】")
        print(f"  标签: {', '.join(tags)}")
        print(f"{'='*60}\n")
    
    return result


def yri_batch_analysis(codes):
    """批量分析多只股票YRI-H并排序"""
    results = []
    for code in codes:
        r = calc_yri_history(code, debug=False)
        if isinstance(r, dict) and "错误" not in r:
            results.append({
                "代码": r["股票代码"],
                "YRI总分": r["YRI历史总分"],
                "等级": r["等级"],
                "涨停": r["G_涨停基因"]["涨停次数"],
                "大阳+5%": r["G_涨停基因"]["大阳线天次(+5%)"],
                "最大连板": r["S_空间记忆"]["最大连板"],
                "最大涨幅%": r["S_空间记忆"]["最大涨幅(%)"],
                "日均换手%": r["A_资金活跃度"]["日均换手率(%)"],
                "日均成交万": r["A_资金活跃度"]["日均成交额(万元)"],
                "标签": ", ".join(r["核心历史标签"])
            })
    
    if results:
        results_df = pd.DataFrame(results).sort_values("YRI总分", ascending=False).reset_index(drop=True)
        results_df.index = results_df.index + 1
        pd.set_option('display.width', 200)
        print(f"\n{'='*120}")
        print(f"  YRI-H 批量历史辨识度评分（共{len(results)}只，按总分排序）")
        print(f"{'='*120}")
        print(results_df.to_string(index=True))
        print(f"{'='*120}\n")
        return results_df
    return None


# ═══════════════════════════════════════════════════════════
# 主题精华报告（V4.3+）
# ═══════════════════════════════════════════════════════════

def generate_theme_essence_report(trade_date: str = None, prefix: str = None):
    """
    生成主题→子主题→个股 精华报告

    从 cache_daily/theme_stock_map_v2_{trade_date}.json 提取
    主题/子主题/个股的精华内容，输出多格式报告到 report_daily/

    参数:
      trade_date: 日期 YYYYMMDD（None=自动使用最新）
      prefix: 文件名前缀（可选）

    用法:
      from tushare_quant import generate_theme_essence_report
      generate_theme_essence_report('20260724')

    输出:
      report_daily/theme_essence_{trade_date}.txt
      report_daily/theme_essence_{trade_date}.md
    """
    from theme_summary_report import generate_theme_essence_report as _gen
    return _gen(trade_date=trade_date, prefix=prefix)


# =========================
# 启动
# =========================
if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description='A股量化选股分析系统')
    parser.add_argument('-d', '--date', type=str, default=None,
                        help='指定目标日期，格式: YYYYMMDD（如: 20260601）')
    parser.add_argument('--no-send', action='store_true',
                        help='不发送微信消息')
    parser.add_argument('--simple', action='store_true',
                        help='简易模式，只输出个股和评分，不进行AI分析、不发送微信')
    parser.add_argument('--yri', type=str, default=None,
                        help='历史辨识度分析，输入股票代码(如 002426.SZ 或 002426)，多只用逗号分隔')
    parser.add_argument('--yri-json', type=str, default=None,
                        help='同--yri，但仅输出JSON格式结果（便于程序调用）')
    
    args = parser.parse_args()
    
    # --- YRI-H 历史辨识度分析（独立模式） ---
    if args.yri_json:
        codes = [c.strip() for c in args.yri_json.split(',') if c.strip()]
        results = {}
        for code in codes:
            # 自动补全 .SZ/.SH
            if '.' not in code:
                if code.startswith(('6', '9')):
                    code_full = code + '.SH'
                else:
                    code_full = code + '.SZ'
            else:
                code_full = code
            r = calc_yri_history(code_full, debug=False)
            results[code] = r
        print(json.dumps(results, ensure_ascii=False, indent=2))
        exit(0)
    
    if args.yri:
        codes = [c.strip() for c in args.yri.split(',') if c.strip()]
        # 自动补全 .SZ/.SH
        full_codes = []
        for code in codes:
            if '.' not in code:
                if code.startswith(('6', '9')):
                    full_codes.append(code + '.SH')
                else:
                    full_codes.append(code + '.SZ')
            else:
                full_codes.append(code)
        
        if len(full_codes) == 1:
            result = calc_yri_history(full_codes[0], debug=True)
            if "错误" in result:
                print(f"\n❌ {result['错误']} - {result['建议']}")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            yri_batch_analysis(full_codes)
        exit(0)
    
    # 运行
    run(target_date=args.date, simple_mode=args.simple)


