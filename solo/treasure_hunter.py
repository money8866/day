# -*- coding: utf-8 -*-
"""
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃              寻宝策略 — 专精特新高壁垒小市值筛选           ┃
┃                                                          ┃
┃  核心逻辑：在大盘防守期，挖掘具备独立产业 Alpha 的         ┃
┃  "毛细血管"环节高壁垒小市值标的，这类股票由于不受           ┃
┃  大盘抛压影响，往往能走出极其强悍的逆势行情。             ┃
┃                                                          ┃
┃  参考标的：争光股份 (301092.SZ)                           ┃
┃  特征标签：小市值(30~80亿) + 高毛利率(>35%)               ┃
┃           + 高研发占比(>5%) + 专精特新 + 强工业替代       ┃
┃                                                          ┃
┃  数据源：Tushare Pro                                      ┃
┃  评分体系：100分制，综合基本面壁垒 + 技术面动量            ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
"""

import os
import sys
import time
import json
import re
import threading
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
from dotenv import load_dotenv

warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

# ── 环境初始化 ─────────────────────────────────────────────
load_dotenv("d:/mystock/config/.env")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'report_daily')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 强制行缓冲输出（解决后台运行时无输出问题）
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ── 缓存目录（复用项目已有结构） ─────────────────────────────
CACHE_DIR = Path(r"D:\mystock\cache_daily")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── 全局频率控制（严格遵守 Tushare 500次/分钟限制） ──────────
_rate_lock = threading.Lock()
_last_ts = time.time()
_MIN_INTERVAL = 0.13  # 130ms ≈ 461次/分钟，留安全裕度


def _rate_limit():
    """线程安全频率控制"""
    global _last_ts
    with _rate_lock:
        elapsed = time.time() - _last_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_ts = time.time()


# ── 缓存 I/O ───────────────────────────────────────────────
def load_cache_df(key: str, expire_hours: int = 24) -> Optional[pd.DataFrame]:
    """从 parquet 读取缓存"""
    path = CACHE_DIR / f"treasure_{key}.parquet"
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
        if (time.time() - mtime) > expire_hours * 3600:
            return None
        return pd.read_parquet(path)
    except Exception:
        return None


def save_cache_df(df: pd.DataFrame, key: str) -> None:
    """写入 parquet 缓存"""
    try:
        path = CACHE_DIR / f"treasure_{key}.parquet"
        df.to_parquet(path, index=False)
    except Exception:
        pass


def load_cache_dict(key: str, expire_hours: int = 24) -> Optional[dict]:
    """读取 JSON 字典缓存"""
    path = CACHE_DIR / f"treasure_{key}.json"
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
        if (time.time() - mtime) > expire_hours * 3600:
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_cache_dict(data, key: str) -> None:
    """写入 JSON 字典缓存"""
    try:
        path = CACHE_DIR / f"treasure_{key}.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Tushare API 封装 ───────────────────────────────────────
def _ts_call(func, *args, **kwargs):
    """统一API调用（含频率控制+重试）"""
    _rate_limit()
    last_err = None
    for attempt in range(3):
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            last_err = e
            msg = str(e)
            if '频率' in msg or 'frequency' in msg.lower():
                wait = 2.0 + attempt * 2.0
                time.sleep(wait)
            else:
                time.sleep(1.0)
    raise last_err


# ── 核心数据获取 ──────────────────────────────────────────

def get_trade_cal(start_date: str = '20200101', end_date: str = None) -> pd.DataFrame:
    """获取交易日历"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    cache_key = f"trade_cal_{start_date}_{end_date}"
    cached = load_cache_df(cache_key, 168)
    if cached is not None:
        return cached
    df = _ts_call(pro.trade_cal, exchange='SSE', start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        save_cache_df(df, cache_key)
    return df if df is not None else pd.DataFrame()


def get_last_trade_date() -> str:
    """获取最近一个交易日"""
    now = datetime.now()
    today = now.strftime('%Y%m%d')
    try:
        cal = get_trade_cal(
            (now - timedelta(days=10)).strftime('%Y%m%d'),
            today
        )
        if len(cal) > 0:
            trading = cal[cal['is_open'] == 1].sort_values('cal_date')
            if len(trading) > 0:
                if now.hour < 16:
                    # 收盘前：用最近一个完整交易日
                    return str(trading[trading['cal_date'] < today].iloc[-1]['cal_date'])
                else:
                    # 收盘后：用今天（如果是交易日）或前一个
                    if today in trading['cal_date'].values:
                        return today
                    return str(trading.iloc[-1]['cal_date'])
    except Exception:
        pass
    return today


def get_stock_list() -> pd.DataFrame:
    """获取全市场股票列表（上市状态）"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_key = "stock_basic_L"
    cached = load_cache_df(cache_key, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.stock_basic, exchange='', list_status='L',
                  fields='ts_code,symbol,name,area,industry,list_date,market,is_hs')
    if df is not None and len(df) > 0:
        df['list_date'] = df['list_date'].astype(str)
        save_cache_df(df, cache_key)
    return df if df is not None else pd.DataFrame()


def get_daily_basic(trade_date: str) -> pd.DataFrame:
    """获取单日全市场基本面（含市值）"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_key = f"daily_basic_{trade_date}"
    cached = load_cache_df(cache_key, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.daily_basic, trade_date=trade_date,
                  fields='ts_code,trade_date,close,total_mv,circ_mv,pe,pe_ttm,pb,turnover_rate,volume_ratio')
    if df is not None and len(df) > 0:
        save_cache_df(df, cache_key)
    return df if df is not None else pd.DataFrame()


def get_stock_financial(ts_code: str) -> pd.DataFrame:
    """获取个股财务指标（fina_indicator）"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_key = f"fin_ind_{ts_code.replace('.', '_')}"
    cached = load_cache_df(cache_key, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.fina_indicator, ts_code=ts_code)
    if df is not None and len(df) > 0:
        save_cache_df(df, cache_key)
    return df if df is not None else pd.DataFrame()


def get_namechange(ts_code: str) -> pd.DataFrame:
    """获取个股改名记录（用于识别专精特新标签）"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_key = f"namechg_{ts_code.replace('.', '_')}"
    cached = load_cache_df(cache_key, 168)
    if cached is not None:
        return cached
    try:
        df = _ts_call(pro.namechange, ts_code=ts_code)
        if df is not None and len(df) > 0:
            save_cache_df(df, cache_key)
        return df if df is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def get_daily_by_code(ts_code: str, days: int = 150) -> pd.DataFrame:
    """获取个股历史日线（用于动量计算）"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    end_date = get_last_trade_date()
    start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=days + 30)
    start_date = start_dt.strftime('%Y%m%d')
    cache_key = f"daily_{ts_code.replace('.', '_')}_{start_date}_{end_date}"
    cached = load_cache_df(cache_key, 24)
    if cached is not None:
        return cached
    df = _ts_call(pro.daily, ts_code=ts_code, start_date=start_date, end_date=end_date,
                  fields='ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount')
    if df is not None and len(df) > 0:
        save_cache_df(df, cache_key)
    return df if df is not None else pd.DataFrame()


def get_mainbz(ts_code: str) -> List[Dict]:
    """获取主营业务构成"""
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    cache_key = f"mainbz_{ts_code.replace('.', '_')}"
    cached = load_cache_dict(cache_key, 168)
    if cached is not None:
        return cached
    try:
        df = _ts_call(pro.fina_mainbz, ts_code=ts_code)
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            latest_date = df.iloc[0]['end_date']
            df = df[df['end_date'] == latest_date].copy()
            result = df[['bz_item', 'bz_ratio']].to_dict('records')
            save_cache_dict(result, cache_key)
            return result
    except Exception:
        pass
    return []


# ── 专精特新关键词库 ─────────────────────────────────────

# 高壁垒行业白名单（自动加分，继承build_theme_stock_map.py的行业白名单思路）
# 涵盖当前壁垒行业 + 未来高壁垒赛道（生命科学、AI链、航天航空等）
_HIGH_BARRIER_INDUSTRIES = [
    '半导体', '芯片', '集成电路', '生物医药', '创新药', '医疗器械',
    '航天', '航空', '航空航天', '军工', '核电', '核能',
    '机器人', '数控机床', '精密仪器', '智能装备',
    '新材料', '特种材料', '高端材料',
    '功率器件', '传感器',
    # ---- 未来高壁垒赛道 ----
    '生命科学', '生物技术', '基因', '细胞治疗', '合成生物',
    '人工智能', '大模型', 'AI', '智能体',
    '卫星', '商业航天', '低轨卫星',
    '量子', '超导',
    '脑机', '神经科学',
    '核聚变', '可控核聚变',
    '自动驾驶', '无人驾驶',
    '具身智能', '人形机器人',
]

# 专精特新/高壁垒关键词（匹配股票简称、改名记录、主营业务）
# 继承build_theme_stock_map.py的THEME_MAINBIZ_KEYWORDS精细关键词体系
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
    # 以下从THEME_MAINBIZ_KEYWORDS提炼
    '算力', '光模块', '散热', '液冷', '交换机', 'ICT',
    '人工智能', '大模型', '智能体', 'Agent',
    'GPU', '处理器', '功率', 'IGBT', 'SiC', 'GaN',
    '先进封装', 'TSV', 'Bumping', 'Chiplet', 'CoWoS',
    '硅片', '光刻胶', '抛光液', '抛光垫', '前驱体', '湿电子',
    '刻蚀', '薄膜沉积', 'PVD', 'CVD', 'ALD', '清洗设备', '离子注入',
    '减速器', '丝杠', '执行器', '关节', '驱动器', '控制器',
    '滚珠丝杠', '空心杯电机', '无框电机', '灵巧手',
    '变频器', 'PLC', '工控', '加工中心', '数控系统',
    '工业软件', '智能制造', '数字孪生', '工业互联网',
    '无人机', '飞行器', 'eVTOL', '飞行汽车',
    '卫星', '宇航', '火箭', '太空',
    '脑机', '神经', 'MEMS',
    '超导', '聚变', '托卡马克', '量子',
    '金刚石', '超硬材料', '人造金刚石',
    '电容器', '电感', '电子陶瓷', '石英晶体',
    'PCB', '覆铜板', '印制电路',
    '碳化硅', '氮化镓', '砷化镓', '磷化铟',
    '生物制造', '发酵', '合成生物', '细胞', '基因',
    '电解水', '燃料电池', '加氢', '储氢',
    '固态', '半固态', '凝聚态', '电解质',
    '培育钻石', '金刚石线', '超硬',
    # ════════════════════════════════════════════════════════
    # 未来高壁垒赛道 — 生命科学类
    # ════════════════════════════════════════════════════════
    '基因编辑', 'CRISPR', '基因治疗', '基因药物', '核酸药物',
    'CAR-T', 'CAR-NK', 'TCR-T', '细胞治疗', '干细胞', 'iPSC',
    '合成生物', '生物制造', '生物基', '发酵', '酶催化',
    '脑机接口', '神经接口', '神经调控', '深脑刺激',
    '生物芯片', '微流控', '器官芯片', '类器官',
    'ADC', '双抗', '双特异性', '抗体偶联',
    'mRNA', '环状RNA', 'siRNA', '小核酸',
    '生物反应器', '一次性生物', '连续流',
    '基因测序', '单细胞', '空间转录组',
    '蛋白质设计', '定向进化', 'AI制药',
    # ════════════════════════════════════════════════════════
    # 未来高壁垒赛道 — 人工智能链
    # ════════════════════════════════════════════════════════
    'AI芯片', '神经网络芯片', '存算一体', '类脑芯片', '神经形态',
    '大模型', '预训练', '多模态', '基础模型', 'LLM',
    'AI Agent', '智能体', '自主智能', 'Multi-Agent',
    '具身智能', '人形机器人', '双足机器人', '灵巧手',
    '边缘AI', '端侧AI', 'AI推理', 'AI加速',
    'AI安全', '对齐', '可解释AI',
    'AI算力', '智算中心', 'AI服务器', 'AI集群',
    'AI平台', '模型训练', '模型部署', '推理引擎',
    '自然语言', '计算机视觉', '语音识别', '多模态感知',
    '向量数据库', '知识图谱', 'RAG', '检索增强',
    'AI应用', 'AI编程', 'AI设计', 'AI生成',
    '自动驾驶', '无人驾驶', 'Robotaxi', '感知融合',
    '决策规划', '高精地图', '定位',
    # ════════════════════════════════════════════════════════
    # 未来高壁垒赛道 — 航天航空
    # ════════════════════════════════════════════════════════
    '商业航天', '民营火箭', '火箭回收', '可回收火箭',
    '低轨卫星', '卫星互联网', '星链', '卫星通信',
    '卫星导航', '遥感卫星', '合成孔径', 'SAR',
    '高超声速', '高超音速', '超燃冲压',
    'eVTOL', '飞行汽车', '城市空中交通', 'UAM',
    '工业无人机', '重载无人机', '无人货运',
    '卫星制造', '卫星组网', '地面终端',
    '太空旅游', '空间站', '深空探测',
    '相控阵', '星载', '抗辐射',
    # ════════════════════════════════════════════════════════
    # 未来高壁垒赛道 — 前沿技术
    # ════════════════════════════════════════════════════════
    '量子芯片', '量子比特', '量子计算', '量子通信', '量子加密',
    '核聚变', '可控核聚变', '托卡马克', '仿星器', '聚变堆',
    '6G', '太赫兹', '可见光通信', '智能超表面', 'RIS',
    '空间计算', 'XR', '扩展现实', '数字孪生',
    'SMR', '小型模块化核反应堆', '第四代核电',
    '氢能', '绿氢', '电解水', 'PEM', 'SOEC',
    '固态电池', '半固态', '凝聚态', '锂金属',
    '钙钛矿', '叠层', '异质结', 'HJT',
    '4D打印', '4D打印', '智能材料', '自修复',
    '脑科学', '认知科学', '神经环路',
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
    # 工业替代/精密材料延伸
    '树脂', '吸附', '分离', '膜', '催化', '靶材',
    '超纯', '高纯', '特种气体',
    '智能装备', '精密',
    # 从THEME_MAINBIZ_KEYWORDS补充（精细化行业屏障关键词）
    '功率器件', '传感器', '光电器件', '电子元器件',
    '先进封装', '半导体材料', '半导体设备',
    '航天装备', '航空装备', '军工电子',
    '智能电网', '特高压',
    '生物技术', '生物制品', '基因',
    '新型材料', '复合材料', '功能材料',
    # ---- 未来高壁垒赛道 ----
    '生命科学', '基因治疗', '细胞治疗', '合成生物',
    '人工智能', '大模型', 'AI芯片', '智能体', '机器学习',
    '卫星互联网', '商业航天', '低轨卫星', '火箭',
    '量子计算', '量子通信',
    '脑机接口', '神经科学',
    '核聚变', '聚变能',
    '自动驾驶', '智能驾驶', '无人驾驶',
    '具身智能', '人形机器人',
    '6G', '太赫兹',
    '固态电池', '氢能', '钙钛矿',
]

# ── 评分系统 ──────────────────────────────────────────────

# 未来千亿评分 — 赛道天花板关键词分级
_GIANT_TRACK_TIER1 = [
    'AI芯片', '人工智能', '大模型', '算力', '机器人', '人形机器人', '具身智能',
    '自动驾驶', '无人驾驶', '半导体', '芯片', '先进封装', '光刻',
    '创新药', '生物医药', '基因治疗', '细胞治疗',
    '商业航天', '低轨卫星', '卫星互联网',
]
_GIANT_TRACK_TIER2 = [
    '物联网', '云计算', '大数据', '信创', '工业软件', '操作系统',
    '医疗器械', '新能源', '储能', '光伏', '氢能',
    '新材料', '特种材料', '航空', '航天',
    '量子', '6G', '核聚变',
]
_GIANT_PLATFORM_KEYWORDS = [
    '平台', '生态', '软件', '解决方案', '一站式', '系统集成',
    '开发者', 'IP', 'EDA', '操作系统', '中间件',
    'SaaS', 'PaaS', '云服务', '芯片设计',
]
_GIANT_GLOBAL_KEYWORDS = [
    '出海', '海外', '全球', '国际', '出口', '外资',
    '国产替代', '进口替代', '卡脖子',
    '汽车电子', '消费电子',
]
_GIANT_CHAIN_KEYWORDS = [
    '核心', '关键材料', '关键设备', 'IP', '专利', '标准',
    'EDA', '高速互连', '先进封装', '光刻', '薄膜',
    '衬底', '外延', '靶材', '前驱体', '特种气体',
    '精密制造', '精密加工',
]


def _compute_future_giant_score(row: dict) -> Dict:
    """
    未来千亿评分模块（20分）— 识别具备成长为千亿市值潜力的标的
    
    维度：
      - 赛道天花板   8分 — AI/机器人/半导体等全球级市场
      - 平台属性     6分 — 单一产品→生态/平台扩展性
      - 全球竞争力    4分 — 出海能力+国产替代+全球客户
      - 产业链控制力  2分 — 核心IP/关键材料/关键设备
    """
    result = {}
    total = 0.0
    
    name = str(row.get('name', ''))
    industry = str(row.get('industry', ''))
    bz_items = str(row.get('main_bz', ''))
    is_hs = str(row.get('is_hs', ''))
    board = str(row.get('board', ''))
    
    search_text = f'{industry} {name} {bz_items}'
    
    # ── 1. 赛道天花板 (8分) ──
    track_score = 0.0
    track_details = []
    
    # T1赛道：全球级大市场（AI、机器人、半导体、创新药等）
    t1_matches = [kw for kw in _GIANT_TRACK_TIER1 if kw in search_text]
    if t1_matches:
        track_score += 5.0
        track_details.extend(t1_matches[:3])
    
    # T2赛道：国家级大市场（新能源、新材料、航空等）
    t2_matches = [kw for kw in _GIANT_TRACK_TIER2 if kw in search_text]
    if t2_matches:
        track_score += 3.0
        track_details.extend(t2_matches[:2])
    
    # 双创/科创板加分 — 新兴赛道往往在双创板
    if board in ('创业板', '科创板'):
        track_score += 1.0
        track_details.append(board)
    
    # 有实质赛道匹配才给分，否则不鼓励
    if not t1_matches and not t2_matches:
        track_score = 0.0
    
    track_score = min(8.0, track_score)
    total += track_score
    result['赛道天花板'] = round(track_score, 1)
    result['赛道标签'] = ';'.join(track_details[:4]) if track_details else ''
    
    # ── 2. 平台属性 (6分) — 从单品→平台/生态的扩展潜力 ──
    plat_score = 0.0
    plat_details = []
    
    plat_matches = [kw for kw in _GIANT_PLATFORM_KEYWORDS if kw in search_text]
    if plat_matches:
        # 匹配越多，平台属性越强
        plat_score += min(4.0, len(plat_matches) * 1.0)
        plat_details.extend(plat_matches[:3])
    
    # 主营业务含多个产品方向（>2个）说明已有产品矩阵雏形
    bz_items_list = [b.strip() for b in bz_items.replace('；', ';').split(';') if b.strip()]
    if len(bz_items_list) >= 3:
        plat_score += 1.5
    elif len(bz_items_list) >= 2:
        plat_score += 0.5
    
    # 科创板/创业板科技公司更易形成平台
    if board in ('科创板',):
        plat_score += 0.5
    
    plat_score = min(6.0, plat_score)
    total += plat_score
    result['平台属性'] = round(plat_score, 1)
    result['平台标签'] = ';'.join(plat_details[:3]) if plat_details else ''
    
    # ── 3. 全球竞争力 (4分) ──
    global_score = 0.0
    global_details = []
    
    global_matches = [kw for kw in _GIANT_GLOBAL_KEYWORDS if kw in search_text]
    if global_matches:
        global_score += min(2.0, len(global_matches) * 0.5)
        global_details.extend(global_matches[:2])
    
    # 沪深港通标的有外资参与，说明有一定全球认可度
    if is_hs in ('H', 'S'):
        global_score += 1.0
        global_details.append('沪深港通')
    
    # 科创板/创业板科技公司更容易参与全球竞争
    if board in ('科创板', '创业板'):
        global_score += 0.5
    
    # 赛道为全球级（T1匹配）的加分
    if t1_matches:
        global_score += 0.5
    
    global_score = min(4.0, global_score)
    total += global_score
    result['全球竞争力'] = round(global_score, 1)
    result['全球标签'] = ';'.join(global_details[:2]) if global_details else ''
    
    # ── 4. 产业链控制力 (2分) ──
    chain_score = 0.0
    chain_details = []
    
    chain_matches = [kw for kw in _GIANT_CHAIN_KEYWORDS if kw in search_text]
    if chain_matches:
        chain_score += min(2.0, len(chain_matches) * 0.5)
        chain_details.extend(chain_matches[:3])
    
    chain_score = min(2.0, chain_score)
    total += chain_score
    result['产业链控制力'] = round(chain_score, 1)
    result['产业链标签'] = ';'.join(chain_details[:2]) if chain_details else ''
    
    result['未来千亿总分'] = round(total, 1)
    return result


def compute_score(row: dict) -> Tuple[float, dict]:
    """
    多维度评分（100+20分制）

    基础维度（100分）：
      - 市值分           15分  — 30~80亿最优
      - 毛利率分         20分  — >45%满分
      - 净利率扎实度      15分  — 净利率>12%且毛利率-净利率<40pp
      - 研发管理效率分     8分  — adminexp_of_gr代理
      - ROE分           10分  — >15%满分
      - 动量分            5分  — 接近120日新高（降权，长线策略不依赖短期动量）
      - 板块加分          5分  — 双创/科创板
      - 标签/关键词分     15分  — 专精特新+工业替代关键词+行业壁垒
      - 行业排除调整      2分  — 非消费/非金融行业奖励
      - 未来赛道分        5分  — 生命科学/AI链/航天航空/前沿技术布局
    
    未来千亿加分（20分）：
      - 赛道天花板   8分
      - 平台属性     6分
      - 全球竞争力    4分
      - 产业链控制力  2分
    """
    details = {}
    total = 0.0

    # ── 1. 市值分（0~15分）：30~80亿最优区间 ──
    # Tushare daily_basic 的 total_mv 单位为万元
    mv = row.get('total_mv', 0) / 10000  # 转亿
    if 30 <= mv <= 80:
        mv_score = 15.0
    elif 20 <= mv < 30:
        mv_score = 12.0 + (mv - 20) / 10 * 3  # 12~15
    elif 80 < mv <= 120:
        mv_score = 10.0 + (120 - mv) / 40 * 5  # 10~15
    elif 120 < mv <= 200:
        mv_score = 5.0 + (200 - mv) / 80 * 5  # 5~10
    elif 200 < mv <= 300:
        mv_score = 2.0
    else:
        mv_score = 0.0
    total += mv_score
    details['市值分'] = round(mv_score, 1)
    details['总市值(亿)'] = round(mv, 1)

    # ── 2. 毛利率分（0~20分） ──
    gm = row.get('gross_margin', 0)
    if gm >= 55:
        gm_score = 20.0
    elif gm >= 45:
        gm_score = 17.0 + (gm - 45) / 10 * 3
    elif gm >= 35:
        gm_score = 13.0 + (gm - 35) / 10 * 4
    elif gm >= 25:
        gm_score = 8.0 + (gm - 25) / 10 * 5
    elif gm >= 15:
        gm_score = 3.0 + (gm - 15) / 10 * 5
    else:
        gm_score = 0.0
    total += gm_score
    details['毛利率分'] = round(gm_score, 1)
    details['毛利率(%)'] = round(gm, 1)

    # ── 3. 净利率扎实度（0~15分）：净利率高 + 毛利率-净利率差距小 ──
    nm = row.get('net_margin', 0)
    gm_nm_gap = gm - nm  # 毛利率与净利率的差距
    # 基础净利率分
    if nm >= 20:
        nm_base = 10.0
    elif nm >= 15:
        nm_base = 8.0 + (nm - 15) / 5 * 2
    elif nm >= 10:
        nm_base = 6.0 + (nm - 10) / 5 * 2
    elif nm >= 5:
        nm_base = 3.0 + (nm - 5) / 5 * 3
    elif nm > 0:
        nm_base = 1.0
    else:
        nm_base = 0.0
    # 差距惩罚：差距>40pp开始惩罚
    gap_penalty = max(0, (gm_nm_gap - 40) / 10 * 3) if gm_nm_gap > 40 else 0
    # 净利率绝对值过低惩罚
    if nm < 3:
        nm_base *= 0.3
    nm_score = max(0, min(15, nm_base + 5 - gap_penalty))
    total += nm_score
    details['净利率分'] = round(nm_score, 1)
    details['净利率(%)'] = round(nm, 1)
    details['毛-净差距(pp)'] = round(gm_nm_gap, 1)

    # ── 4. 研发管理效率分（0~8分）— adminexp_of_gr 含研发+管理 ──
    rd = row.get('rd_ratio', 0)
    if rd >= 20:
        rd_score = 8.0
    elif rd >= 12:
        rd_score = 6.0 + (rd - 12) / 8 * 2
    elif rd >= 7:
        rd_score = 3.0 + (rd - 7) / 5 * 3
    elif rd >= 3:
        rd_score = 1.0 + (rd - 3) / 4 * 2
    else:
        rd_score = 0.0
    total += rd_score
    details['研发分'] = round(rd_score, 1)
    details['研发占比(%)'] = round(rd, 1)

    # ── 5. ROE分（0~10分） ──
    roe = row.get('roe', 0)
    if roe >= 20:
        roe_score = 10.0
    elif roe >= 15:
        roe_score = 8.0 + (roe - 15) / 5 * 2
    elif roe >= 10:
        roe_score = 5.0 + (roe - 10) / 5 * 3
    elif roe >= 5:
        roe_score = 2.0 + (roe - 5) / 5 * 3
    else:
        roe_score = 0.0
    total += roe_score
    details['ROE分'] = round(roe_score, 1)
    details['ROE(%)'] = round(roe, 1)

    # ── 6. 动量分（0~5分）：接近120日新高 ──
    pct_from_120d_high = row.get('pct_from_120d_high', 100)
    # pct_from_120d_high = (high_120 - current) / high_120 * 100
    if pct_from_120d_high <= 2:
        mom_score = 5.0
    elif pct_from_120d_high <= 5:
        mom_score = 4.0 + (5 - pct_from_120d_high) / 3 * 1
    elif pct_from_120d_high <= 10:
        mom_score = 2.5 + (10 - pct_from_120d_high) / 5 * 1.5
    elif pct_from_120d_high <= 20:
        mom_score = 1.0 + (20 - pct_from_120d_high) / 10 * 1.5
    else:
        mom_score = 0.0
    total += mom_score
    details['动量分'] = round(mom_score, 1)
    details['距120日高(%)'] = round(pct_from_120d_high, 1)

    # ── 7. 板块加分（0~5分） ──
    board = row.get('board', '')
    if board in ('创业板', '科创板'):
        board_score = 5.0
    elif board == '主板':
        board_score = 2.0
    else:
        board_score = 0.0
    total += board_score
    details['板块分'] = round(board_score, 1)

    # ── 8. 标签/关键词分（0~15分） ──
    tag_score = 0.0
    tag_details = []

    # 8a. 专精特新标签
    if row.get('is_specialized', False):
        tag_score += 3.0
        tag_details.append('专精特新')

    # 8b. 名称含关键词（匹配专精特新关键词集）
    name = row.get('name', '')
    matched_name_kw = [kw for kw in _SPECIALIZED_KEYWORDS if kw in name]
    if matched_name_kw:
        # 名称匹配权重提高：每匹配1个关键词给1.0分，上限2.5分
        tag_score += min(2.5, len(matched_name_kw) * 1.0)
        tag_details.extend(matched_name_kw[:3])

    # 8c. 主营业务含壁垒关键词（匹配两个关键词集）
    bz_items = row.get('main_bz', '')
    matched_bz_kw = [kw for kw in _INDUSTRY_BARRIER_KEYWORDS if kw in bz_items]
    matched_bz_kw2 = [kw for kw in _SPECIALIZED_KEYWORDS if kw in bz_items and kw not in matched_bz_kw]
    all_matched_bz = list(set(matched_bz_kw + matched_bz_kw2))
    if all_matched_bz:
        tag_score += min(6.0, len(all_matched_bz) * 1.0)
        tag_details.extend(all_matched_bz[:5])

    # 8d. 行业含壁垒关键词（阶梯式评分 + 高壁垒白名单加分）
    industry = row.get('industry', '')
    # 高壁垒行业白名单自动加分（继承build_theme_stock_map.py思路）
    if any(hw in industry for hw in _HIGH_BARRIER_INDUSTRIES):
        tag_score += 3.0  # 核心高壁垒行业：半导体/生物医药/航空航天等
        tag_details.append(f'高壁垒行业:{industry}')
    elif any(kw in industry for kw in _INDUSTRY_BARRIER_KEYWORDS):
        tag_score += 2.0  # 一般壁垒行业
        tag_details.append(f'行业:{industry}')

    # 排除惩罚：没有任何壁垒关键词匹配（属于消费/非工业股）
    has_any_match = bool(all_matched_bz) or bool(matched_name_kw) or \
                    any(hw in industry for hw in _HIGH_BARRIER_INDUSTRIES) or \
                    any(kw in industry for kw in _INDUSTRY_BARRIER_KEYWORDS)
    if not has_any_match:
        tag_score *= 0.3  # 大幅降权

    tag_score = min(15.0, tag_score)
    total += tag_score
    details['标签分'] = round(tag_score, 1)
    details['标签'] = ';'.join(tag_details[:5]) if tag_details else ''
    details['主营业务'] = bz_items[:80] if bz_items else ''

    # ── 9. 行业排除调整（0~2分）：非消费/非金融/非服务行业奖励 ──
    industry = row.get('industry', '')
    _EXCLUDED_INDUSTRIES = ['食品', '饮料', '纺织', '服装', '家具', '造纸', '印刷', '文娱',
                            '体育', '教育', '旅游', '酒店', '餐饮', '零售', '贸易', '经纪',
                            '银行', '保险', '证券', '信托', '房地产', '租赁', '物业',
                            '传媒', '广告', '影视', '游戏', '互联网',
                            '交通运输', '仓储', '物流', '公路', '港口', '机场',
                            '公用事业', '电力', '水务', '燃气', '环保']
    if not any(excl in industry for excl in _EXCLUDED_INDUSTRIES):
        ind_adj = 2.0
    else:
        ind_adj = 0.0
    total += ind_adj
    details['行业排除调整'] = ind_adj

    # ── 10. 未来赛道分（0~5分）：识别未来高壁垒赛道布局 ──
    future_score = 0.0
    future_track = ''
    name = str(row.get('name', ''))
    bz_items = str(row.get('main_bz', ''))

    # 定义未来赛道及其关键词映射
    _FUTURE_TRACKS = {
        '生命科学': ['基因', '细胞', '合成生物', '脑机', 'ADC', 'mRNA',
                    '抗体', 'CAR-T', '干细胞', '测序', '蛋白质设计',
                    'AI制药', '生物芯片', '类器官', '微流控',
                    '生命科学', '生物技术',
                    ],
        '人工智能链': ['AI芯片', '大模型', '多模态', '智能体', '具身智能',
                     '人形机器人', '边缘AI', '存算一体', '类脑',
                     '自动驾驶', '无人驾驶', 'Robotaxi',
                     '人工智能', '机器人', '机器学习', '深度学习',
                     ],
        '航天航空': ['商业航天', '低轨卫星', '卫星互联网', '火箭回收',
                    'eVTOL', '飞行汽车', '高超声速', '星载', '相控阵',
                    '航天', '航空', '卫星', '火箭', '无人机',
                    '太空', '低空',
                    ],
        '前沿技术': ['量子', '核聚变', '托卡马克', '6G', '太赫兹',
                    '固态电池', '钙钛矿', '氢能', 'SMR',
                    '超导', '聚变',
                    ],
    }

    # 在行业、名称、主营中查找未来赛道线索
    for track_name, keywords in _FUTURE_TRACKS.items():
        ind_match = any(kw in industry for kw in keywords)
        name_match = any(kw in name for kw in keywords)
        bz_match = any(kw in bz_items for kw in keywords)
        if ind_match or name_match or bz_match:
            future_score += 2.0  # 每个赛道2分
            if future_track:
                future_track += f'|{track_name}'
            else:
                future_track = track_name

            # 名称/主营高度匹配再加分（行业匹配已经通过白名单加分了）
            if name_match or bz_match:
                future_score += 0.5

    # 多个赛道叠加奖励（跨领域布局更有弹性）
    if '|' in future_track:
        future_score += 1.0

    future_score = min(5.0, future_score)
    total += future_score
    details['未来赛道分'] = round(future_score, 1)
    details['未来赛道'] = future_track if future_track else ''

    # ── 未来千亿加分（20分）— 识别具备成长为千亿市值潜力的标的 ──
    giant_score = _compute_future_giant_score(row)
    details.update(giant_score)
    total += giant_score['未来千亿总分']

    # ── 总分 ──
    total = round(total, 1)
    details['总分'] = total

    return total, details


# ── 择时模块 — 超跌评分（0~100分） ─────────────────────────


def _compute_oversold_score(row: dict) -> Dict:
    """
    超跌评分（0~100分）— 识别超跌反弹潜力最大的标的

    五维评分：
      - 距高幅度   30分  — 距120日高点越远，超跌越充分
      - RSI超卖    25分  — RSI越低，短期超卖越严重
      - MA20偏离   20分  — 低于MA20越多，偏离修复空间越大
      - 缩量枯竭   15分  — 成交量萎缩越厉害，卖压越枯竭
      - 60日低位   10分  — 接近60日低点，支撑临近
    """
    scores = {}
    total = 0.0

    # ── 1. 距120日高幅度（30分） ──
    pct_high = row.get('pct_from_120d_high', 999)
    if pct_high >= 50:
        score1 = 30.0
    elif pct_high >= 35:
        score1 = 25.0 + (pct_high - 35) / 15 * 5
    elif pct_high >= 20:
        score1 = 18.0 + (pct_high - 20) / 15 * 7
    elif pct_high >= 10:
        score1 = 10.0 + (pct_high - 10) / 10 * 8
    elif pct_high >= 5:
        score1 = 4.0 + (pct_high - 5) / 5 * 6
    else:
        score1 = 0.0
    total += score1
    scores['距高得分'] = round(score1, 1)
    scores['距120日高(%)'] = round(pct_high, 1)

    # ── 2. RSI超卖（25分） ──
    rsi = row.get('rsi_14', 50)
    if rsi <= 20:
        score2 = 25.0
    elif rsi <= 30:
        score2 = 20.0 + (30 - rsi) / 10 * 5
    elif rsi <= 40:
        score2 = 12.0 + (40 - rsi) / 10 * 8
    elif rsi <= 50:
        score2 = 5.0 + (50 - rsi) / 10 * 7
    else:
        score2 = 0.0
    total += score2
    scores['RSI得分'] = round(score2, 1)
    scores['RSI_14'] = round(rsi, 1)

    # ── 3. MA20偏离（20分）— 低于MA20越远分越高 ──
    pct_ma20 = row.get('pct_below_ma20', 0)  # 负值表示在MA20下方
    below_ma20 = -pct_ma20 if pct_ma20 < 0 else 0
    if below_ma20 >= 15:
        score3 = 20.0
    elif below_ma20 >= 10:
        score3 = 15.0 + (below_ma20 - 10) / 5 * 5
    elif below_ma20 >= 5:
        score3 = 10.0 + (below_ma20 - 5) / 5 * 5
    elif below_ma20 >= 3:
        score3 = 5.0 + (below_ma20 - 3) / 2 * 5
    elif below_ma20 > 0:
        score3 = 2.0
    else:
        score3 = 0.0  # 在MA20上方，不超跌
    total += score3
    scores['MA20偏离得分'] = round(score3, 1)
    scores['距MA20(%)'] = round(pct_ma20, 1)

    # ── 4. 缩量枯竭（15分）— 量比越小，卖压越枯竭 ──
    vol_ratio = row.get('volume_ratio', 1.0)
    if vol_ratio <= 0.5:
        score4 = 15.0
    elif vol_ratio <= 0.7:
        score4 = 10.0 + (0.7 - vol_ratio) / 0.2 * 5
    elif vol_ratio <= 0.9:
        score4 = 5.0 + (0.9 - vol_ratio) / 0.2 * 5
    elif vol_ratio <= 1.1:
        score4 = 2.0
    else:
        score4 = 0.0  # 放量下跌不超跌
    total += score4
    scores['缩量得分'] = round(score4, 1)
    scores['量比'] = round(vol_ratio, 2)

    # ── 5. 60日低位（10分）— 接近60日低点，支撑临近 ──
    pct_60d_low = row.get('pct_from_60d_low', 999)
    if pct_60d_low <= 3:
        score5 = 10.0
    elif pct_60d_low <= 8:
        score5 = 7.0 + (8 - pct_60d_low) / 5 * 3
    elif pct_60d_low <= 15:
        score5 = 4.0 + (15 - pct_60d_low) / 7 * 3
    elif pct_60d_low <= 25:
        score5 = 1.0
    else:
        score5 = 0.0
    total += score5
    scores['60日低位得分'] = round(score5, 1)
    scores['距60日低(%)'] = round(pct_60d_low, 1)

    total = min(100.0, total)
    scores['超跌总分'] = round(total, 1)
    return scores


# ── 择时模块 — 中线右侧买点评分（0~100分） ─────────────────


def _compute_midterm_buy_score(row: dict) -> Dict:
    """
    中线右侧买点评分（0~100分）— 识别趋势反转确认后的中线入场机会

    与左侧抄底（超跌评分）不同，右侧买点要求"底部确认后买入"：
    股价已站上均线、趋势转强、出现MACD金叉等确认信号。

    五维评分：
      - 站上均线   25分  — 股价站上MA20/MA60，多头排列
      - 趋势转强   20分  — MA20斜率上翘 + 脱离底部区域
      - MACD金叉   25分  — DIF上穿DEA + 零轴上方 + 红柱
      - 量能配合   15分  — 温和放量确认（非暴量追高）
      - 右侧确认   15分  — 脱离60日低点 + 未过度透支
    """
    scores = {}
    total = 0.0

    current_close = row.get('current_close', 0)
    ma20 = row.get('ma20', 0)
    ma60 = row.get('ma60', 0)
    pct_below_ma20 = row.get('pct_below_ma20', 0)   # 正=在上方，负=在下方
    pct_below_ma60 = row.get('pct_below_ma60', 0)
    ma20_slope = row.get('ma20_slope', 0)
    pct_from_60d_low = row.get('pct_from_60d_low', 999)
    pct_from_high = row.get('pct_from_120d_high', 999)
    volume_ratio = row.get('volume_ratio', 1.0)
    macd_dif = row.get('macd_dif', 0)
    macd_dea = row.get('macd_dea', 0)
    macd_hist = row.get('macd_hist', 0)
    golden_cross = row.get('macd_golden_cross', False)

    # ── 1. 站上均线（25分） ──
    score1 = 0.0
    # 1a. 股价站上MA20（10分）
    if pct_below_ma20 > 0:
        score1 += 10.0
    # 1b. 股价站上MA60（8分）
    if pct_below_ma60 > 0:
        score1 += 8.0
    # 1c. MA20 > MA60 多头排列（7分）
    if ma20 > 0 and ma60 > 0 and ma20 > ma60:
        score1 += 7.0
    total += score1
    scores['站上均线得分'] = round(score1, 1)

    # ── 2. 趋势转强（20分） ──
    score2 = 0.0
    # 2a. MA20斜率上翘（10分）
    if ma20_slope > 0.5:
        score2 += 10.0
    elif ma20_slope > 0:
        score2 += 5.0
    elif ma20_slope > -0.5:
        score2 += 2.0  # 走平
    # 2b. 脱离底部区域（10分）：距60日低点5%~35%为右侧启动区
    if 5 <= pct_from_60d_low <= 35:
        score2 += 10.0
    elif 35 < pct_from_60d_low <= 50:
        score2 += 5.0
    elif 2 <= pct_from_60d_low < 5:
        score2 += 4.0  # 刚起步，接近底部
    total += score2
    scores['趋势转强得分'] = round(score2, 1)

    # ── 3. MACD金叉（25分） ──
    score3 = 0.0
    # 3a. DIF上穿DEA金叉（12分）
    if golden_cross:
        score3 += 12.0
    elif macd_dif > macd_dea:
        score3 += 7.0  # 已在多头状态（金叉后延续）
    # 3b. DIF在零轴上方（5分）
    if macd_dif > 0:
        score3 += 5.0
    # 3c. MACD柱为正（红柱）（8分）
    if macd_hist > 0:
        score3 += 8.0
    total += score3
    scores['MACD得分'] = round(score3, 1)

    # ── 4. 量能配合（15分）— 温和放量确认，暴量视为透支 ──
    score4 = 0.0
    if 1.0 <= volume_ratio <= 1.8:
        score4 += 10.0  # 温和放量
    elif 0.8 <= volume_ratio < 1.0:
        score4 += 5.0   # 平量
    elif 1.8 < volume_ratio <= 2.5:
        score4 += 4.0   # 偏大，警惕
    # 底部区域放量（距60日低<15%且量比>1.2）加分（5分）
    if pct_from_60d_low <= 15 and volume_ratio > 1.2:
        score4 += 5.0
    total += score4
    scores['量能配合得分'] = round(score4, 1)

    # ── 5. 右侧确认（15分） ──
    score5 = 0.0
    # 5a. 脱离60日低点>5%（8分）— 确认底部成立，非下跌中继
    if pct_from_60d_low > 5:
        score5 += 8.0
    elif pct_from_60d_low > 2:
        score5 += 3.0
    # 5b. 距120日高<30%（7分）— 未过度透支，仍在中线安全区
    if pct_from_high <= 15:
        score5 += 7.0
    elif pct_from_high <= 30:
        score5 += 4.0
    elif pct_from_high <= 45:
        score5 += 1.0
    total += score5
    scores['右侧确认得分'] = round(score5, 1)

    # ── 6. 形态健康度惩罚（-0~20分）— 防止高位放量回落被误判为右侧买点 ──
    penalty = 0.0
    recent_chg_1d = row.get('recent_chg_1d', 0)
    recent_chg_2d = row.get('recent_chg_2d', 0)
    is_negative_day = row.get('is_negative_day', False)

    # 6a. 近2日大跌（-12分）— 形态已坏，右侧买点不成立
    if recent_chg_2d <= -8:
        penalty += 12.0
    elif recent_chg_2d <= -5:
        penalty += 8.0
    # 6b. 单日大跌（-6分）
    if recent_chg_1d <= -5:
        penalty += 6.0
    # 6c. 放量阴线（-5分）— 量比>1.3且收阴=抛压
    if is_negative_day and volume_ratio > 1.3:
        penalty += 5.0
    # 6d. 高位放量阴线（-5分）— 距高>20%+阴线+放量=高位出货
    if pct_from_high > 20 and is_negative_day and volume_ratio > 1.2:
        penalty += 5.0

    penalty = min(20.0, penalty)
    total -= penalty
    total = max(0.0, min(100.0, total))
    scores['形态惩罚'] = round(penalty, 1)

    total = min(100.0, total)
    scores['中线买点总分'] = round(total, 1)
    return scores


# ── 择时模块 — 右侧回踩确认强度因子（0~100分） ────────────
# 回测验证结论（2026-01~2026-07，7017个样本）：
#   单独使用"中线买点总分"排序与未来收益负相关（20日 rho≈-0.09，滞后确认）
#   必须先做"超跌过滤"，再在池内按右侧强度排序 → 20日 Spearman rho≈+0.38
#   强度最高十分位：20日上涨概率≈76%，平均收益≈+12.5%


def _compute_rightside_strength(row: dict) -> Dict:
    """
    右侧回踩确认强度因子（满分100）— 组合择时逻辑中的排序因子

    经典右侧买点：突破站上MA20后，回踩MA20不破（缩量+趋势支撑）
    1. 站上MA20新鲜度  20分 — 站上1-10天内有效，越久越衰减
    2. 回踩到位       30分 — 收盘距MA20 0~+4%为最优回踩区，离得远=追高
    3. 趋势支撑       25分 — MA20斜率刚转正(0~2%)最优；MA20>MA60加分
    4. 缩量回调       15分 — 量比0.5~1.0（缩量回调），放量回调减分
    5. 空间未透支     10分 — 距120日高>15%才有向上空间
    """
    scores = {}
    total = 0.0

    days_above_ma20 = row.get('days_above_ma20', 0)
    pct_ma20 = row.get('pct_below_ma20', 0)      # 正=在MA20上方
    ma20_slope = row.get('ma20_slope', 0)
    ma20 = row.get('ma20', 0)
    ma60 = row.get('ma60', 0)
    vol_ratio = row.get('volume_ratio', 1.0)
    pct_high = row.get('pct_from_120d_high', 999)

    # ── 1. 站上MA20新鲜度（20分） ──
    if days_above_ma20 >= 1:
        if days_above_ma20 <= 5:
            score1 = 20.0
        elif days_above_ma20 <= 10:
            score1 = 15.0
        elif days_above_ma20 <= 20:
            score1 = 8.0
        else:
            score1 = 2.0
    else:
        score1 = 0.0
    total += score1
    scores['启动新鲜度'] = round(score1, 1)
    scores['站上MA20天数'] = days_above_ma20

    # ── 2. 回踩到位（30分）— 收盘距MA20在0~+4%为最优回踩区 ──
    if pct_ma20 >= 0:
        if pct_ma20 <= 1.5:
            score2 = 30.0       # 贴着MA20（回踩到位）
        elif pct_ma20 <= 4:
            score2 = 24.0       # 回踩区上沿
        elif pct_ma20 <= 8:
            score2 = 14.0       # 离MA20偏远，追高
        elif pct_ma20 <= 15:
            score2 = 6.0
        else:
            score2 = 2.0        # 远离均线，透支
    else:
        score2 = 0.0            # 仍在MA20下方，右侧不成立
    total += score2
    scores['回踩到位'] = round(score2, 1)

    # ── 3. 趋势支撑（25分）— MA20斜率刚转正最优 ──
    if 0 < ma20_slope <= 1.0:
        score3 = 18.0
    elif 1.0 < ma20_slope <= 2.5:
        score3 = 13.0
    elif ma20_slope <= 0:
        score3 = 4.0            # 走平/向下
    else:
        score3 = 6.0            # 陡峭
    if ma20 > 0 and ma60 > 0 and ma20 > ma60:
        score3 += 7.0           # MA20>MA60 多头排列
    total += score3
    scores['趋势支撑'] = round(score3, 1)

    # ── 4. 缩量回调（15分） ──
    if 0.5 <= vol_ratio < 1.0:
        score4 = 15.0           # 缩量回调，卖压枯竭
    elif 1.0 <= vol_ratio < 1.3:
        score4 = 8.0
    elif vol_ratio >= 1.5:
        score4 = 0.0            # 放量回调，警惕出货
    elif vol_ratio < 0.5:
        score4 = 6.0            # 极度缩量（可能流动性枯竭）
    else:
        score4 = 2.0
    total += score4
    scores['缩量回调'] = round(score4, 1)

    # ── 5. 空间未透支（10分） ──
    if pct_high > 15:
        score5 = 10.0
    elif pct_high > 8:
        score5 = 5.0
    else:
        score5 = 0.0
    total += score5
    scores['空间未透支'] = round(score5, 1)

    total = min(100.0, total)
    scores['右侧强度总分'] = round(total, 1)
    return scores


# ── 主筛选流程 ──────────────────────────────────────────


def run_screening(trade_date: str = None, min_score: float = 50.0) -> pd.DataFrame:
    """
    执行寻宝策略全市场扫描

    Args:
        trade_date: 交易日（默认最近交易日）
        min_score: 最低入围分数（默认50分）

    Returns:
        DataFrame: 排名结果
    """
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)

    if trade_date is None:
        trade_date = get_last_trade_date()

    print(f"{'='*70}")
    print(f"  寻宝策略 — 专精特新高壁垒标的扫描")
    print(f"  交易日: {trade_date}")
    print(f"  最低入围分数: {min_score}")
    print(f"{'='*70}")

    # ── Phase 1: 全市场基础数据 ──
    print("\n[Phase 1] 获取全市场股票数据...")
    stocks = get_stock_list()
    print(f"  → 上市股票总数: {len(stocks)}")

    # 过滤北交所（用户规则：跳过北交所）
    stocks = stocks[~stocks['ts_code'].str.match(r'^(8|4)\d{5}\.')]
    print(f"  → 排除北交所后: {len(stocks)}")

    # 标记板块
    def _detect_board(ts_code: str, market: str) -> str:
        if market == '科创板':
            return '科创板'
        if ts_code.startswith('30'):
            return '创业板'
        if ts_code.startswith('688'):
            return '科创板'
        return '主板'

    stocks['board'] = stocks.apply(
        lambda r: _detect_board(r['ts_code'], str(r.get('market', ''))), axis=1
    )

    # ── Phase 2: 获取市值数据 ──
    print("\n[Phase 2] 获取全市场市值数据...")
    basic = get_daily_basic(trade_date)
    if basic is None or len(basic) == 0:
        print("  ✗ 无法获取市值数据！")
        return pd.DataFrame()
    print(f"  → 获取 {len(basic)} 条记录")

    # 合并市值
    df = stocks.merge(basic[['ts_code', 'total_mv', 'circ_mv', 'pe_ttm', 'pb']],
                      on='ts_code', how='inner')

    # 筛选市值范围：25~300亿（宽松初筛，后续评分中精细调整）
    # Tushare daily_basic 的 total_mv 单位是万元，/10000 转亿
    df['mv_yi'] = df['total_mv'] / 10000
    candidates = df[(df['mv_yi'] >= 20) & (df['mv_yi'] <= 300)].copy()
    print(f"\n[Phase 2a] 市值20~300亿候选: {len(candidates)} 只")

    # ── Phase 3: 获取财务指标 ──
    print(f"\n[Phase 3] 获取个股财务指标...")
    print(f"  → 共 {len(candidates)} 只股票需要查询，逐个获取中...")

    fin_data = {}  # ts_code -> dict of indicators

    for idx, (_, row) in enumerate(candidates.iterrows()):
        code = row['ts_code']
        name = row['name']
        if (idx + 1) % 50 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(candidates)}] 处理中...当前: {name}({code})")

        try:
            fin_df = get_stock_financial(code)
            if fin_df is not None and len(fin_df) >= 2:
                fin_df = fin_df.sort_values('end_date', ascending=False).head(8)

                # 取近4期均值（有数据则用，不足则用可用期数）
                gm = fin_df['grossprofit_margin'].dropna().head(4).mean() if 'grossprofit_margin' in fin_df.columns else 0
                nm = fin_df['netprofit_margin'].dropna().head(4).mean() if 'netprofit_margin' in fin_df.columns else 0
                rd = fin_df['adminexp_of_gr'].dropna().head(4).mean() if 'adminexp_of_gr' in fin_df.columns else 0
                roe = fin_df['roe'].dropna().head(4).mean() if 'roe' in fin_df.columns else 0

                # 统一为百分比（Tushare fina_indicator 返回的是百分比数值如 42.5）
                gm = float(gm) if pd.notna(gm) else 0
                nm = float(nm) if pd.notna(nm) else 0
                rd = float(rd) if pd.notna(rd) else 0
                roe = float(roe) if pd.notna(roe) else 0

                fin_data[code] = {
                    'gross_margin': gm,
                    'net_margin': nm,
                    'rd_ratio': rd,
                    'roe': roe,
                }
            else:
                fin_data[code] = {
                    'gross_margin': 0,
                    'net_margin': 0,
                    'rd_ratio': 0,
                    'roe': 0,
                }
        except Exception:
            fin_data[code] = {
                'gross_margin': 0,
                'net_margin': 0,
                'rd_ratio': 0,
                'roe': 0,
            }

    # 合并财务数据
    fin_records = []
    for _, row in candidates.iterrows():
        code = row['ts_code']
        fd = fin_data.get(code, {})
        fin_records.append({
            **row.to_dict(),
            'gross_margin': fd.get('gross_margin', 0),
            'net_margin': fd.get('net_margin', 0),
            'rd_ratio': fd.get('rd_ratio', 0),
            'roe': fd.get('roe', 0),
        })
    candidates = pd.DataFrame(fin_records)

    # ── Phase 3a: 毛利率/研发初筛（加速：不满足条件的提前排除） ──
    candidates = candidates[
        (candidates['gross_margin'] >= 20) | (candidates['rd_ratio'] >= 3)
    ].copy()
    print(f"\n[Phase 3a] 毛利率>=20%或研发>=3%: {len(candidates)} 只")

    # ── Phase 4: 获取动量数据（120日新高 + 超跌指标） ──
    print(f"\n[Phase 4] 技术面动量 & 超跌指标计算...")
    print(f"  → 需查询 {len(candidates)} 只股票的日线数据")

    momentum_data = {}

    for idx, (_, row) in enumerate(candidates.iterrows()):
        code = row['ts_code']
        name = row['name']
        if (idx + 1) % 30 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(candidates)}] 动量查询中...{name}({code})")

        try:
            daily = get_daily_by_code(code, days=180)
            if daily is not None and len(daily) > 60:
                daily = daily.sort_values('trade_date').reset_index(drop=True)

                # ── 基础数据 ──
                closes = daily['close'].astype(float).values
                highs = daily['high'].astype(float).values
                lows = daily['low'].astype(float).values
                volumes = daily['vol'].astype(float).values
                current_close = float(closes[-1])

                # ── 120日最高价距幅 ──
                recent_120 = daily.tail(120)
                high_120 = float(recent_120['high'].max())
                pct_from_high = (high_120 - current_close) / high_120 * 100 if high_120 > 0 else 999

                # ── 60日最低价距幅 ──
                recent_60 = daily.tail(60)
                low_60 = float(recent_60['low'].min())
                pct_from_60d_low = (current_close - low_60) / low_60 * 100 if low_60 > 0 else 999

                # ── MA20 / MA60 ──
                daily['ma20'] = daily['close'].rolling(20).mean()
                daily['ma60'] = daily['close'].rolling(60).mean()
                ma20_val = float(daily['ma20'].dropna().iloc[-1]) if len(daily['ma20'].dropna()) > 0 else current_close
                ma60_val = float(daily['ma60'].dropna().iloc[-1]) if len(daily['ma60'].dropna()) > 0 else current_close
                pct_below_ma20 = (current_close - ma20_val) / ma20_val * 100 if ma20_val > 0 else 0  # 负值=在MA20下方
                pct_below_ma60 = (current_close - ma60_val) / ma60_val * 100 if ma60_val > 0 else 0

                # ── MA20斜率 ──
                if len(daily['ma20'].dropna()) >= 6:
                    ma20_latest = ma20_val
                    ma20_prev = float(daily['ma20'].dropna().iloc[-6])
                    ma20_slope = (ma20_latest - ma20_prev) / ma20_prev * 100 if ma20_prev > 0 else 0
                else:
                    ma20_slope = 0

                # ── RSI-14 ──
                rsi_14 = 50.0
                if len(closes) >= 15:
                    deltas = np.diff(closes[-15:])
                    gains = np.where(deltas > 0, deltas, 0)
                    losses = np.where(deltas < 0, -deltas, 0)
                    avg_gain = gains.mean()
                    avg_loss = losses.mean()
                    if avg_loss > 0:
                        rs = avg_gain / avg_loss
                        rsi_14 = 100.0 - (100.0 / (1.0 + rs))
                    elif avg_gain > 0:
                        rsi_14 = 100.0
                    else:
                        rsi_14 = 50.0

                # ── 量比（近5日均量 vs 近20日均量） ──
                if len(volumes) >= 20:
                    vol_5d = np.mean(volumes[-5:])
                    vol_20d = np.mean(volumes[-20:])
                    volume_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
                else:
                    volume_ratio = 1.0

                # ── MACD (12, 26, 9) ──
                close_s = pd.Series(closes)
                ema12 = close_s.ewm(span=12, adjust=False).mean()
                ema26 = close_s.ewm(span=26, adjust=False).mean()
                dif = ema12 - ema26
                dea = dif.ewm(span=9, adjust=False).mean()
                macd_hist = (dif - dea) * 2
                macd_dif = float(dif.iloc[-1])
                macd_dea = float(dea.iloc[-1])
                macd_hist_val = float(macd_hist.iloc[-1])
                # 金叉检测：DIF上穿DEA
                if len(dif) >= 2:
                    golden_cross = bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])
                else:
                    golden_cross = bool(macd_dif > macd_dea)

                # ── 距60日低点天数（右侧确认：底部确认后回升时间） ──
                low_idx_60 = int(recent_60['low'].idxmin())
                days_since_60d_low = len(daily) - 1 - low_idx_60 if low_idx_60 >= 0 else 99

                # ── 近期走势（形态健康度） ──
                recent_chg_1d = 0.0   # 最新日涨跌幅
                recent_chg_2d = 0.0   # 最近2日累计涨跌幅
                if len(daily) >= 3:
                    recent_chg_1d = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] > 0 else 0
                    recent_chg_2d = (closes[-1] / closes[-3] - 1) * 100 if closes[-3] > 0 else 0
                # 当日是否阴线（收<开）
                last_row = daily.iloc[-1]
                is_negative_day = bool(last_row['close'] < last_row['open'])

                # ── 连续站上MA20天数（右侧启动新鲜度） ──
                days_above_ma20 = 0
                ma20_arr = daily['ma20'].values
                for j in range(len(daily) - 1, -1, -1):
                    if np.isnan(ma20_arr[j]) or closes[j] <= ma20_arr[j]:
                        break
                    days_above_ma20 += 1

                momentum_data[code] = {
                    'pct_from_120d_high': round(pct_from_high, 2),
                    'ma20_slope': round(ma20_slope, 2),
                    'current_close': round(current_close, 2),
                    # ── 超跌指标 ──
                    'rsi_14': round(rsi_14, 2),
                    'pct_below_ma20': round(pct_below_ma20, 2),
                    'pct_below_ma60': round(pct_below_ma60, 2),
                    'volume_ratio': round(volume_ratio, 2),
                    'pct_from_60d_low': round(pct_from_60d_low, 2),
                    'ma20': round(ma20_val, 2),
                    'ma60': round(ma60_val, 2),
                    # ── 中线右侧指标 ──
                    'macd_dif': round(macd_dif, 3),
                    'macd_dea': round(macd_dea, 3),
                    'macd_hist': round(macd_hist_val, 3),
                    'macd_golden_cross': golden_cross,
                    'days_since_60d_low': days_since_60d_low,
                    # ── 形态健康度 ──
                    'recent_chg_1d': round(recent_chg_1d, 2),
                    'recent_chg_2d': round(recent_chg_2d, 2),
                    'is_negative_day': is_negative_day,
                    # ── 右侧启动新鲜度 ──
                    'days_above_ma20': days_above_ma20,
                }
            else:
                momentum_data[code] = {
                    'pct_from_120d_high': 999,
                    'ma20_slope': 0,
                    'current_close': 0,
                    'rsi_14': 50, 'pct_below_ma20': 0, 'pct_below_ma60': 0,
                    'volume_ratio': 1.0, 'pct_from_60d_low': 999,
                    'ma20': 0, 'ma60': 0,
                    'macd_dif': 0, 'macd_dea': 0, 'macd_hist': 0,
                    'macd_golden_cross': False, 'days_since_60d_low': 99,
                    'recent_chg_1d': 0, 'recent_chg_2d': 0, 'is_negative_day': False,
                    'days_above_ma20': 0,
                }
        except Exception:
            momentum_data[code] = {
                'pct_from_120d_high': 999,
                'ma20_slope': 0,
                'current_close': 0,
                'rsi_14': 50, 'pct_below_ma20': 0, 'pct_below_ma60': 0,
                'volume_ratio': 1.0, 'pct_from_60d_low': 999,
                'ma20': 0, 'ma60': 0,
                'macd_dif': 0, 'macd_dea': 0, 'macd_hist': 0,
                'macd_golden_cross': False, 'days_since_60d_low': 99,
                'recent_chg_1d': 0, 'recent_chg_2d': 0, 'is_negative_day': False,
                'days_above_ma20': 0,
            }

    # 合并动量 & 超跌数据
    mom_records = []
    for _, row in candidates.iterrows():
        code = row['ts_code']
        md = momentum_data.get(code, {})
        mom_records.append({
            **row.to_dict(),
            'pct_from_120d_high': md.get('pct_from_120d_high', 999),
            'ma20_slope': md.get('ma20_slope', 0),
            'current_close': md.get('current_close', 0),
            'rsi_14': md.get('rsi_14', 50),
            'pct_below_ma20': md.get('pct_below_ma20', 0),
            'pct_below_ma60': md.get('pct_below_ma60', 0),
            'volume_ratio': md.get('volume_ratio', 1.0),
            'pct_from_60d_low': md.get('pct_from_60d_low', 999),
            'ma20': md.get('ma20', 0),
            'ma60': md.get('ma60', 0),
            'macd_dif': md.get('macd_dif', 0),
            'macd_dea': md.get('macd_dea', 0),
            'macd_hist': md.get('macd_hist', 0),
            'macd_golden_cross': md.get('macd_golden_cross', False),
            'days_since_60d_low': md.get('days_since_60d_low', 99),
            'recent_chg_1d': md.get('recent_chg_1d', 0),
            'recent_chg_2d': md.get('recent_chg_2d', 0),
            'is_negative_day': md.get('is_negative_day', False),
            'days_above_ma20': md.get('days_above_ma20', 0),
        })
    candidates = pd.DataFrame(mom_records)

    # ── Phase 5: 获取主营业务 + 专精特新标签 ──
    print(f"\n[Phase 5] 主营业务识别 & 专精特新标签...")

    bz_data = {}
    nchg_data = {}

    for idx, (_, row) in enumerate(candidates.iterrows()):
        code = row['ts_code']
        if (idx + 1) % 50 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(candidates)}] 标签分析中...")

        # 主营业务
        bz = get_mainbz(code)
        bz_items = '; '.join([b['bz_item'] for b in bz[:5]]) if bz else ''
        bz_data[code] = bz_items

        # 改名记录（是否曾更名为'专精特新'相关）
        try:
            nchg = get_namechange(code)
            is_spec = False
            if nchg is not None and len(nchg) > 0:
                all_names = ' '.join(nchg['name'].dropna().astype(str).tolist())
                if '专精特新' in all_names:
                    is_spec = True
            nchg_data[code] = is_spec
        except Exception:
            nchg_data[code] = False

    # 检查名称中是否有专精特新关键词
    for _, row in candidates.iterrows():
        code = row['ts_code']
        name = str(row.get('name', ''))
        is_spec = nchg_data.get(code, False)
        if not is_spec:
            # 检查当前名称
            if any(kw in name for kw in ['专精特新', '小巨人', '微球', '吸附']):
                is_spec = True
        nchg_data[code] = is_spec

    # 合并主营业务和标签
    final_records = []
    for _, row in candidates.iterrows():
        code = row['ts_code']
        final_records.append({
            **row.to_dict(),
            'main_bz': bz_data.get(code, ''),
            'is_specialized': nchg_data.get(code, False),
        })
    candidates = pd.DataFrame(final_records)

    # ── Phase 6: 评分 ──
    print(f"\n[Phase 6] 综合评分...")
    score_results = []
    for _, row in candidates.iterrows():
        total_score, details = compute_score(row.to_dict())
        # 超跌择时评分
        oversold = _compute_oversold_score(row.to_dict())
        details.update(oversold)
        # 中线右侧买点评分
        midterm = _compute_midterm_buy_score(row.to_dict())
        details.update(midterm)
        # 右侧回踩确认强度因子（组合择时：超跌过滤 → 强度排序）
        rightside = _compute_rightside_strength(row.to_dict())
        details.update(rightside)
        score_results.append({
            'ts_code': row['ts_code'],
            'name': row['name'],
            'total_score': total_score,
            **details,
        })

    result_df = pd.DataFrame(score_results)

    # ── Phase 7: 排序 & 输出 ──
    result_df = result_df.sort_values('总分', ascending=False).reset_index(drop=True)
    result_df['排名'] = range(1, len(result_df) + 1)

    # 入围筛选（总分达标 + 至少有一定壁垒标签匹配）
    passed = result_df[result_df['总分'] >= min_score].copy()
    # 壁垒过滤：高壁垒行业(3分)或一般壁垒行业(2分)或主营/名称有匹配
    passed = passed[passed['标签分'] >= 2.0].copy()
    passed = passed.sort_values('总分', ascending=False).reset_index(drop=True)
    print(f"\n{'='*70}")
    print(f"  扫描完成！")
    print(f"  总评分数: {len(result_df)} 只")
    print(f"  入围(≥{min_score}分): {len(passed)} 只")
    print(f"{'='*70}")

    return passed, result_df


# ── 报告输出 ──────────────────────────────────────────


def print_report(passed: pd.DataFrame, all_df: pd.DataFrame, trade_date: str, min_score: float = 60.0, timing: str = 'oversold'):
    """打印格式化报告"""

    # 输出文件
    output_csv = os.path.join(OUTPUT_DIR, f'treasure_hunt_{trade_date}.csv')
    passed.to_csv(output_csv, index=False, encoding='utf-8-sig')
    print(f"\n完整CSV已保存: {output_csv}")

    all_csv = os.path.join(OUTPUT_DIR, f'treasure_hunt_all_{trade_date}.csv')
    all_df.to_csv(all_csv, index=False, encoding='utf-8-sig')

    if len(passed) == 0:
        print("\n⚠ 未找到符合条件的标的。可能原因：")
        print("  - 当前市场处于极低估状态，小市值高壁垒标的被错杀严重")
        print("  - 可尝试降低 min_score 参数重新扫描")
        return

    # 按分数段分类
    tier1 = passed[passed['总分'] >= 80].sort_values('总分', ascending=False)
    tier2 = passed[(passed['总分'] >= 70) & (passed['总分'] < 80)].sort_values('总分', ascending=False)
    tier3 = passed[(passed['总分'] >= min_score) & (passed['总分'] < 70)].sort_values('总分', ascending=False)

    print(f"\n{'━'*70}")
    print(f"  寻宝策略结果报告 — {trade_date}")
    print(f"{'━'*70}")

    # ── 第一梯队（≥80分） ──
    print(f"\n{'█'*70}")
    print(f"  ★★★ 第一梯队（总分≥80，强烈关注）★★★")
    print(f"{'█'*70}")
    if len(tier1) > 0:
        for _, r in tier1.iterrows():
            _print_stock_card(r)
    else:
        print("  （无）")

    # ── 第二梯队（70~80分） ──
    print(f"\n{'▌'*35}")
    print(f"  ★★ 第二梯队（总分70~80，重点关注）")
    print(f"{'▌'*35}")
    if len(tier2) > 0:
        for _, r in tier2.iterrows():
            _print_stock_card(r)
    else:
        print("  （无）")

    # ── 第三梯队 ──
    print(f"\n{'▌'*35}")
    print(f"  ★ 第三梯队（总分{min_score}~70，纳入观察）")
    print(f"{'▌'*35}")
    if len(tier3) > 0:
        for _, r in tier3.iterrows():
            _print_stock_mini(r)
    else:
        print("  （无）")

    # ── 统计摘要 ──
    print(f"\n{'─'*70}")
    print(f"  统计摘要")
    print(f"{'─'*70}")
    print(f"  入围标的总数: {len(passed)}")
    print(f"  平均总分: {passed['总分'].mean():.1f}")
    print(f"  平均毛利率: {passed['毛利率(%)'].mean():.1f}%")
    print(f"  平均净利率: {passed['净利率(%)'].mean():.1f}%")
    print(f"  平均研发占比: {passed['研发占比(%)'].mean():.1f}%")
    print(f"  平均市值: {passed['总市值(亿)'].mean():.1f}亿")
    if '距120日高(%)' in passed.columns:
        near_high = len(passed[passed['距120日高(%)'] <= 5])
        print(f"  接近120日新高(≤5%): {near_high} 只")
    if '未来千亿总分' in passed.columns:
        giant_mean = passed['未来千亿总分'].mean()
        giant_high = len(passed[passed['未来千亿总分'] >= 10])
        print(f"\n  未来千亿评分:")
        print(f"    平均分: {giant_mean:.1f}")
        print(f"    高分(≥10分): {giant_high} 只")
        for dim in ['赛道天花板', '平台属性', '全球竞争力', '产业链控制力']:
            if dim in passed.columns:
                avg_dim = passed[dim].mean()
                print(f"    {dim}: {avg_dim:.2f}分(平均)")
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

    # ── 择时评分 ──
    # 组合择时（回测验证最优）：先超跌过滤，再按右侧强度排序
    if timing == 'combo':
        _print_combo_timing(passed)
        if '超跌总分' in passed.columns:
            _print_oversold_timing(passed, brief=True)
        if '中线买点总分' in passed.columns:
            _print_midterm_timing(passed, brief=True)
    elif timing == 'midterm' and '中线买点总分' in passed.columns:
        _print_midterm_timing(passed)
        if '右侧强度总分' in passed.columns:
            _print_combo_timing(passed, brief=True)
        if '超跌总分' in passed.columns:
            _print_oversold_timing(passed, brief=True)
    else:
        if '超跌总分' in passed.columns:
            _print_oversold_timing(passed)
        if '右侧强度总分' in passed.columns:
            _print_combo_timing(passed, brief=True)
        if '中线买点总分' in passed.columns:
            _print_midterm_timing(passed, brief=(timing == 'oversold'))

    # ── 操作建议 ──
    print(f"\n{'═'*70}")
    print(f"  操作建议（基于用户交易规则）")
    print(f"{'═'*70}")
    print(f"  • 大盘Risk OFF时：重点关注第一/第二梯队标的的低吸机会")
    print(f"  • 入场条件（双创）：等待15天严格回踩，MA10容忍度±4%")
    print(f"  • 入场条件（主板）：等待10天快速回踩，MA10容忍度±5%")
    print(f"  • 趋势确认：站上MA20+大阳线≥4%+量比≥1.3+KDJ多头")
    print(f"  • 仓位控制：单只≤日均成交额的10%（流动性风控）")
    print(f"  • 北交所标的已自动排除")
    print(f"  • 未来高壁垒赛道已扩展至：生命科学、人工智能链、")
    print(f"    航天航空、前沿技术（量子/核聚变/6G等）")
    print(f"  • 争光股份对标标的聚焦于：吸附分离、高纯材料、")
    print(f"    工业卡脖子配套等细分领域")
    if timing == 'midterm':
        print(f"  • 今日择时算法: 中线右侧买点（站上均线+趋势转强+MACD金叉+量能配合）")
        print(f"    → 适合基本面壁垒高、趋势刚反转确认的中线标的")
    elif timing == 'combo':
        print(f"  • 今日择时算法: 组合逻辑（超跌过滤≥50 → 右侧强度排序）")
        print(f"    → 回测验证(2026-01~07, 7017样本): 排名越前20日上涨概率越高")
        print(f"      超跌≥50池内强度Spearman rho≈+0.38；强度最高十分位20日涨率≈76%")
    else:
        print(f"  • 今日择时算法: 最大超跌（五维评分：距高+RSI+MA20偏离+缩量+60日低位）")
        print(f"    → 适合左侧低吸、超跌反弹的短线标的")
    print(f"{'═'*70}")


def _print_combo_timing(passed: pd.DataFrame, brief: bool = False):
    """打印组合择时TOP10（超跌过滤 → 右侧强度排序，回测验证单调性最优）"""
    print(f"\n{'─'*70}")
    title = "择时: 组合逻辑（超跌过滤≥50 → 右侧强度排序）（辅助）" if brief else \
            "择时: 组合逻辑（超跌过滤≥50 → 右侧强度排序）"
    print(f"  {title}")
    print(f"{'─'*70}")
    if '超跌总分' not in passed.columns or '右侧强度总分' not in passed.columns:
        print("  缺少超跌/右侧强度字段，跳过")
        return
    os_thr = 50.0
    pool = passed[passed['超跌总分'] >= os_thr].copy()
    pool = pool.sort_values('右侧强度总分', ascending=False)
    print(f"  超跌≥{os_thr:.0f}分池: {len(pool)} 只  |  全池: {len(passed)} 只")
    if '站上MA20天数' in passed.columns:
        print(f"  池内已站上MA20: {len(pool[pool['站上MA20天数'] >= 1])} 只"
              f"（右侧买点要求突破站上MA20后回踩不破）")
    print(f"\n  ═══ 组合择时 TOP10 ═══")
    if len(pool) == 0:
        print("  （当前无超跌≥50标的，可临时降低超跌过滤门槛）")
        return
    for idx, (_, r) in enumerate(pool.head(10).iterrows()):
        st_score = r.get('右侧强度总分', 0)
        os_score = r.get('超跌总分', 0)
        total_s = r.get('总分', 0)
        ma20_pct = r.get('距MA20(%)', 0)
        vol_r = r.get('量比', 1.0)
        days_up = r.get('站上MA20天数', 0)
        pct_high = r.get('距120日高(%)', 0)
        bar = '█' * int(st_score / 10) + '░' * (10 - int(st_score / 10))
        fresh_tag = '未站上' if days_up <= 0 else ('刚启动' if days_up <= 5 else ('回踩中' if days_up <= 20 else '走久'))
        print(f"  {idx+1:>2}. {r['name']:>8}({r['ts_code']})  "
              f"强度{st_score:>5.1f} {bar}  "
              f"超跌{os_score:>5.1f}  "
              f"总分{total_s:>5.1f}  "
              f"MA20{ma20_pct:>+6.1f}%  "
              f"站上{days_up:>2}日{fresh_tag}  "
              f"量比{vol_r:>.2f}  "
              f"距高{pct_high:>5.1f}%")


def _print_oversold_timing(passed: pd.DataFrame, brief: bool = False):
    """打印超跌择时TOP10"""
    print(f"\n{'─'*70}")
    title = "择时: 最大超跌评分（辅助）" if brief else "择时: 最大超跌评分"
    print(f"  {title}")
    print(f"{'─'*70}")
    oversold_sorted = passed.sort_values('超跌总分', ascending=False).head(10)
    avg_oversold = passed['超跌总分'].mean()
    high_oversold = len(passed[passed['超跌总分'] >= 60])
    print(f"  平均超跌分: {avg_oversold:.1f}  |  高分超跌(≥60分): {high_oversold} 只")
    print(f"\n  ═══ 超跌择时 TOP10 ═══")
    for idx, (_, r) in enumerate(oversold_sorted.iterrows()):
        os_score = r.get('超跌总分', 0)
        rsi_val = r.get('RSI_14', 50)
        pct_ma20 = r.get('距MA20(%)', 0)
        pct_high = r.get('距120日高(%)', 0)
        vol_r = r.get('量比', 1.0)
        bar = '█' * int(os_score / 10) + '░' * (10 - int(os_score / 10))
        print(f"  {idx+1:>2}. {r['name']:>8}({r['ts_code']})  "
              f"超跌{os_score:>5.1f} {bar}  "
              f"RSI{rsi_val:>5.1f}  "
              f"MA20{pct_ma20:>+6.1f}%  "
              f"距高{pct_high:>5.1f}%  "
              f"量比{vol_r:>.2f}")


def _print_midterm_timing(passed: pd.DataFrame, brief: bool = False):
    """打印中线右侧买点TOP10"""
    print(f"\n{'─'*70}")
    title = "择时: 中线右侧买点（辅助）" if brief else "择时: 中线右侧买点"
    print(f"  {title}")
    print(f"{'─'*70}")
    midterm_sorted = passed.sort_values('中线买点总分', ascending=False).head(10)
    avg_midterm = passed['中线买点总分'].mean()
    high_midterm = len(passed[passed['中线买点总分'] >= 60])
    print(f"  平均中线买点分: {avg_midterm:.1f}  |  强买点(≥60分): {high_midterm} 只")
    print(f"\n  ═══ 中线右侧买点 TOP10 ═══")
    for idx, (_, r) in enumerate(midterm_sorted.iterrows()):
        mid_score = r.get('中线买点总分', 0)
        penalty = r.get('形态惩罚', 0)
        ma20_pct = r.get('距MA20(%)', 0)
        ma60_pct = r.get('距MA60(%)', 0)
        macd_hist = r.get('macd_hist', 0)
        pct_high = r.get('距120日高(%)', 0)
        vol_r = r.get('量比', 1.0)
        bar = '█' * int(mid_score / 10) + '░' * (10 - int(mid_score / 10))
        macd_tag = '金叉' if r.get('macd_golden_cross', False) else ('红柱' if macd_hist > 0 else '绿柱')
        pen_tag = f"  ⚠形态罚{penalty:.0f}" if penalty > 0 else ""
        print(f"  {idx+1:>2}. {r['name']:>8}({r['ts_code']})  "
              f"买点{mid_score:>5.1f} {bar}  "
              f"MA20{ma20_pct:>+6.1f}%  "
              f"MA60{ma60_pct:>+6.1f}%  "
              f"{macd_tag}  "
              f"距高{pct_high:>5.1f}%  "
              f"量比{vol_r:>.2f}{pen_tag}")


def _print_stock_card(r):
    """打印个股详情卡片"""
    tags = str(r.get('标签', ''))
    bz = str(r.get('主营业务', ''))
    future_track = str(r.get('未来赛道', ''))
    giant_score = r.get('未来千亿总分', 0)
    oversold_score = r.get('超跌总分', 0)
    midterm_score = r.get('中线买点总分', 0)
    print(f"  ┌─────────────────────────────────────────────────────┐")
    print(f"  │ {r['name']} ({r['ts_code']})  ┃  总分: {r['总分']}")
    print(f"  ├─────────────────────────────────────────────────────┤")
    print(f"  │ 市值 {r.get('总市值(亿)', 'N/A'):>8}亿  │ 毛利率 {r.get('毛利率(%)', 'N/A'):>5}%  │ 净利率 {r.get('净利率(%)', 'N/A'):>5}%")
    print(f"  │ ROE {r.get('ROE(%)', 'N/A'):>6}%  │ 研发 {r.get('研发占比(%)', 'N/A'):>5}%  │ 距120日高 {r.get('距120日高(%)', 'N/A'):>5}%")
    if oversold_score > 0 or midterm_score > 0 or r.get('右侧强度总分', 0) > 0:
        rsi = r.get('RSI_14', 50)
        ma20 = r.get('距MA20(%)', 0)
        vol_r = r.get('量比', 1.0)
        macd_tag = '金叉' if r.get('macd_golden_cross', False) else ('红柱' if r.get('macd_hist', 0) > 0 else '绿柱')
        _parts = []
        if oversold_score > 0:
            _parts.append(f"超跌{oversold_score:.1f}")
        if midterm_score > 0:
            _parts.append(f"买点{midterm_score:.1f}")
        if r.get('右侧强度总分', 0) > 0:
            _parts.append(f"强度{r['右侧强度总分']:.1f}")
        print(f"  │ {' '.join(_parts)}  │ RSI {rsi:>5.1f}  │ 距MA20 {ma20:>+6.1f}%  │ 量比 {vol_r:>.2f}  │ {macd_tag}")
    if giant_score > 0:
        track = r.get('赛道天花板', 0)
        plat = r.get('平台属性', 0)
        glob = r.get('全球竞争力', 0)
        chain = r.get('产业链控制力', 0)
        print(f"  │ 千亿评分 {giant_score:>4.1f}分  │ 赛道{track} 平台{plat} 全球{glob} 产业链{chain}")
    if tags:
        print(f"  │ 标签: {tags}")
    if future_track:
        print(f"  │ 未来赛道: {future_track}  [{r.get('未来赛道分', 0):.0f}分]")
    if bz:
        _bz_short = bz if len(bz) <= 78 else bz[:75] + '...'
        print(f"  │ 主营: {_bz_short}")
    print(f"  └─────────────────────────────────────────────────────┘")


def _print_stock_mini(r):
    """打印个股简略信息"""
    future_track = str(r.get('未来赛道', ''))
    giant_score = r.get('未来千亿总分', 0)
    track_info = f' [{future_track}]' if future_track else ''
    giant_info = f' 千亿{giant_score:.1f}分' if giant_score > 0 else ''
    print(f"  {r['name']:>8}({r['ts_code']})  "
          f"总分{r['总分']:>5.1f}  "
          f"市值{r.get('总市值(亿)', 0):>5.1f}亿  "
          f"毛利率{r.get('毛利率(%)', 0):>5.1f}%  "
          f"距120日高{r.get('距120日高(%)', 0):>5.1f}%"
          f"{giant_info}{track_info}")


# ── 主入口 ──────────────────────────────────────────────


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='寻宝策略 — 专精特新小市值高壁垒标的筛选')
    parser.add_argument('--trade_date', type=str, default=None,
                        help='交易日 YYYYMMDD（默认最近交易日）')
    parser.add_argument('--min_score', type=float, default=60.0,
                        help='最低入围分数（默认60分）')
    parser.add_argument('--quick', action='store_true',
                        help='快速模式：跳过主营/标签查询（仅基于财务+动量筛选）')
    parser.add_argument('--timing', type=str, default='combo',
                        choices=['oversold', 'midterm', 'combo'],
                        help='择时算法（combo=超跌过滤+右侧强度排序[回测最优], oversold=最大超跌, midterm=中线右侧买点）')

    args = parser.parse_args()
    min_score = args.min_score

    t0 = time.time()
    passed, all_df = run_screening(
        trade_date=args.trade_date,
        min_score=min_score,
    )

    trade_date = args.trade_date or get_last_trade_date()
    print_report(passed, all_df, trade_date, min_score=min_score,
                 timing=args.timing)

    elapsed = time.time() - t0
    print(f"\n⏱ 总耗时: {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")
    print(f"{'='*70}")
