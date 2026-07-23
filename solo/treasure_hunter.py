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


def compute_score(row: dict) -> Tuple[float, dict]:
    """
    多维度评分（100分制）

    维度：
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

    # ── 总分 ──
    total = round(total, 1)
    details['总分'] = total

    return total, details


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

    # ── Phase 4: 获取动量数据（120日新高） ──
    print(f"\n[Phase 4] 技术面动量检查...")
    print(f"  → 需查询 {len(candidates)} 只股票的日线数据")

    momentum_data = {}

    for idx, (_, row) in enumerate(candidates.iterrows()):
        code = row['ts_code']
        name = row['name']
        if (idx + 1) % 30 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(candidates)}] 动量查询中...{name}({code})")

        try:
            daily = get_daily_by_code(code, days=150)
            if daily is not None and len(daily) > 20:
                daily = daily.sort_values('trade_date')
                current_close = float(daily.iloc[-1]['close'])
                # 120日最高价
                recent_120 = daily.tail(120)
                high_120 = float(recent_120['high'].max())
                pct_from_high = (high_120 - current_close) / high_120 * 100 if high_120 > 0 else 999

                # 20日均线斜率
                if len(daily) >= 25:
                    daily['ma20'] = daily['close'].rolling(20).mean()
                    ma20_latest = float(daily['ma20'].dropna().iloc[-1])
                    ma20_prev = float(daily['ma20'].dropna().iloc[-6]) if len(daily['ma20'].dropna()) >= 6 else ma20_latest
                    ma20_slope = (ma20_latest - ma20_prev) / ma20_prev * 100 if ma20_prev > 0 else 0
                else:
                    ma20_slope = 0

                momentum_data[code] = {
                    'pct_from_120d_high': round(pct_from_high, 2),
                    'ma20_slope': round(ma20_slope, 2),
                    'current_close': round(current_close, 2),
                }
            else:
                momentum_data[code] = {
                    'pct_from_120d_high': 999,
                    'ma20_slope': 0,
                    'current_close': 0,
                }
        except Exception:
            momentum_data[code] = {
                'pct_from_120d_high': 999,
                'ma20_slope': 0,
                'current_close': 0,
            }

    # 合并动量数据
    mom_records = []
    for _, row in candidates.iterrows():
        code = row['ts_code']
        md = momentum_data.get(code, {})
        mom_records.append({
            **row.to_dict(),
            'pct_from_120d_high': md.get('pct_from_120d_high', 999),
            'ma20_slope': md.get('ma20_slope', 0),
            'current_close': md.get('current_close', 0),
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


def print_report(passed: pd.DataFrame, all_df: pd.DataFrame, trade_date: str, min_score: float = 60.0):
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
    print(f"{'═'*70}")


def _print_stock_card(r):
    """打印个股详情卡片"""
    tags = str(r.get('标签', ''))
    bz = str(r.get('主营业务', ''))
    future_track = str(r.get('未来赛道', ''))
    print(f"\n  ┌─────────────────────────────────────────────────────┐")
    print(f"  │ {r['name']} ({r['ts_code']})  ┃  总分: {r['总分']}")
    print(f"  ├─────────────────────────────────────────────────────┤")
    print(f"  │ 市值 {r.get('总市值(亿)', 'N/A'):>8}亿  │ 毛利率 {r.get('毛利率(%)', 'N/A'):>5}%  │ 净利率 {r.get('净利率(%)', 'N/A'):>5}%")
    print(f"  │ ROE {r.get('ROE(%)', 'N/A'):>6}%  │ 研发 {r.get('研发占比(%)', 'N/A'):>5}%  │ 距120日高 {r.get('距120日高(%)', 'N/A'):>5}%")
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
    track_info = f' [{future_track}]' if future_track else ''
    print(f"  {r['name']:>8}({r['ts_code']})  "
          f"总分{r['总分']:>5.1f}  "
          f"市值{r.get('总市值(亿)', 0):>5.1f}亿  "
          f"毛利率{r.get('毛利率(%)', 0):>5.1f}%  "
          f"距120日高{r.get('距120日高(%)', 0):>5.1f}%"
          f"{track_info}")


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

    args = parser.parse_args()
    min_score = args.min_score

    t0 = time.time()
    passed, all_df = run_screening(
        trade_date=args.trade_date,
        min_score=min_score,
    )

    trade_date = args.trade_date or get_last_trade_date()
    print_report(passed, all_df, trade_date, min_score=min_score)

    elapsed = time.time() - t0
    print(f"\n⏱ 总耗时: {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")
    print(f"{'='*70}")
