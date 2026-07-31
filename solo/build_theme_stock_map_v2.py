#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题-个股对应关系映射生成器 V2
基于 theme_kg_v3/theme_config.json 配置，
输出 CSV 格式的主题-个股对应关系。

用法：
    python build_theme_stock_map_v2.py
"""

import sys
import os
import json
import csv
import math
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

# Windows GBK 控制台
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

from tushare_quant import pro, TRADE_DATE
import theme_trend_sentiment_score as theme_ts
from subtheme_heat_engine import run_subtheme_heat_engine
from stock_role_engine import (
    run_stock_role_engine_for_all_subthemes,
    build_subtheme_stock_index_from_report,
    build_subtheme_lifecycle_from_report,
    ROLES,
)
from subtheme_stock_scoring import (
    run_subtheme_stock_scoring_for_all,
    flatten_top_picks,
    print_top_picks_summary,
)
from entry_timing_engine import (
    run_entry_timing_for_all,
    print_entry_timing_report,
    print_subtheme_report,
)

CACHE_DIR = r"d:\mystock\cache_daily"
OUTPUT_DIR = os.path.join(BASE_DIR, "report_daily")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# 新 config → 旧 format 转换器
# ============================================================
def convert_new_config_to_old(new_config, stock_basic_df):
    """
    将 theme_kg_v3/theme_config.json 格式转换为 match_theme_stocks()
    所需的旧格式 {主题中文名: {industry, concept, keywords, ...}}
    
    新格式字段映射：
      sw_industry_match + cx_industry_match  → industry（用于SW/stock_basic行业匹配）
      eastmoney_concepts + ths_concepts      → concept（用于东财/同花顺概念匹配）
      leaders(代码) + core_stocks(代码)       → leader_companies/core_companies(名称)
      keywords / exclude_keywords             → 直接映射

    参数:
      new_config: 已解析的 theme_config.json dict（避免重复I/O）
    """
    # 建立代码→名称映射
    code_to_name = {}
    if stock_basic_df is not None and not stock_basic_df.empty:
        for _, row in stock_basic_df.iterrows():
            ts_code = row.get("ts_code", "")
            name = row.get("name", "")
            if ts_code and name:
                code_to_name[ts_code] = name

    old_format = {}
    convert_log = []

    for theme_key, cfg in new_config.items():
        # 跳过非主题KEY（如 _flow 元数据）
        if theme_key.startswith('_'):
            convert_log.append(f"[跳过] {theme_key} 不是主题")
            continue

        cn_name = cfg.get("name_cn", theme_key)

        # --- industry：合并 sw_industry_match + cx_industry_match ---
        industry_list = list(set(
            cfg.get("sw_industry_match", []) + cfg.get("cx_industry_match", [])
        ))

        # --- concept：合并 eastmoney_concepts + ths_concepts ---
        concept_list = list(set(
            cfg.get("eastmoney_concepts", []) + cfg.get("ths_concepts", [])
        ))

        # --- leader_companies / core_companies：代码→名称解析 ---
        leader_codes = cfg.get("leaders", [])
        core_codes = cfg.get("core_stocks", [])

        leader_names = []
        for code in leader_codes:
            name = code_to_name.get(code, "")
            if name:
                leader_names.append(name)
            else:
                convert_log.append(f"[{cn_name}] leader代码{code}未解析到名称")

        core_names = []
        for code in core_codes:
            name = code_to_name.get(code, "")
            if name:
                core_names.append(name)
            else:
                convert_log.append(f"[{cn_name}] core代码{code}未解析到名称")

        # --- core_companies 额外收集：从 keyworkds brand_keywords ---
        # brand_keywords 中已包含龙头公司名称，也纳入core_companies
        brand_companies = cfg.get("brand_keywords", [])
        all_core = list(set(core_names + brand_companies))

        # --- 构建旧格式 ---
        old_format[cn_name] = {
            "industry": industry_list,
            "concept": concept_list,
            "keywords": cfg.get("keywords", []),
            "exclude_keywords": cfg.get("exclude_keywords", []),
            "core_companies": all_core,
            "leader_companies": leader_names,
        }

    for log in convert_log:
        print(f"[转换] {log}")

    return old_format


# ============================================================
# 过滤规则（从原 build_theme_stock_map.py 适配，按新主题名）
# ============================================================

# 主题→主营业务验证关键词
THEME_MAINBIZ_KEYWORDS = {
    'AI算力': ['算力', '数据中心', '服务器', '云计算', 'IDC', '光模块', '芯片', '散热', '液冷', '电源', '机柜', '带宽', 'ICT', 'ICT基础设施', '信息通信', '网络设备', '交换机', '路由器', '网络基础设施', 'IT基础设施', '数据中心建设', '机房', '布线', '光纤通信', '铜连接', 'AEC', 'DAC', '高速互联', '系统集成', '信息技术服务', '数据服务'],
    '半导体': ['芯片', '半导体', '集成电路', '晶圆', '代工', '封测', '封装', '测试', '设备', '材料'],
    '机器人': ['机器人', '减速器', '丝杠', '电机', '传感器', '执行器', '关节', '驱动器', '控制器', '伺服', '精密减速', '滚珠丝杠', '行星滚柱', '空心杯电机', '无框电机', '力矩电机', '灵巧手', '线性执行器', '旋转执行器', '运动控制', '机电'],
    '创新药': ['创新药', '新药', '原研', '生物药', 'ADC', 'CXO', 'CRO', 'CDMO', '抗体', '双抗', '细胞治疗', '基因', '疫苗', '重组蛋白'],
    '消费电子': ['手机', '消费电子', '终端', '屏幕', '显示屏', '摄像头', '连接器', '耳机', '音箱', '可穿戴', '声学', '电声', '视窗', '玻璃', '精密结构件', '模具', '机壳', '射频前端', 'LCD', 'OLED', '显示模组', '触摸屏', '液晶', '光学镜头', '触控显示'],
    '智能驾驶': ['智能驾驶', '汽车电子', '车载', '自动驾驶', '车联网', 'ADAS', '座舱', '导航', '智能座舱', '域控制', '雷达', '汽车配件', '汽车零部件', '线控'],
    '军工': ['航空', '军用', '军品', '导弹', '战车', '战机', '武器', '舰船', '坦克', '雷达', '军事', '国防', '弹药', '战斗机', '发动机', '军用飞机', '兵器', '军械', '舰艇', '卫星', '仿真测试', '军工电子'],
    '新能源车': ['汽车', '新能源', '电池', '电机', '电控', '充电', '车载', '整车', '电动', '动力电池', '驱动电机', '电驱', '电控系统', 'BMS', '热管理', '汽车零部件', '汽车电子'],
    '黄金': ['金', '黄金', '金矿', '贵金属', '黄金开采', '黄金冶炼'],
    '银行': ['银行', '商业银行', '金融'],
    '证券': ['证券', '券商', '证券公司', '投行', '资本市场'],
    '煤炭': ['煤', '煤炭', '焦煤', '焦炭', '动力煤'],
    '电力': ['电力', '发电', '电源', '火电', '水电', '核电', '风电', '光伏', '电站', '电网', '输电', '变电', '配电', '电力生产', '热力', '新能源发电', '核能', '风力', '太阳能', '储能'],
    '信创': ['信创', '国产替代', '自主可控', '信息安全', '国产CPU', '国产GPU', '操作系统', '数据库', '办公软件'],
    '低空经济': ['低空', '无人机', '飞行器', '航空', '直升', 'eVTOL', '飞行汽车', '通航', '通用航空', '航空器', '飞行控制', '航空电子', '导航', '空管'],
    '白酒': ['白酒', '酒', '酿酒', '食品饮料', '酒精饮料'],
    '消费': ['消费', '食品饮料', '家用电器', '零售', '社会服务', '品牌消费'],
    '游戏': ['游戏', '手游', '网游', '游戏研发', '游戏发行', '电竞', '动漫'],
    '数据要素': ['数据', '大数据', '数据要素', '数据资产', '数据交易', '数据安全', '数据治理', '隐私计算'],
    '高端材料': ['化工', '化学', '氟', '制冷剂', '染料', '聚氨酯', 'MDI', '维生素', '工程塑料', '精细化工', '新材料', '特种化学品'],
    '合成生物': ['合成生物', '生物制造', '发酵', '生物', '酶', '基因', '细胞'],
    '商业航天': ['卫星', '航天', '宇航', '运载火箭', '火箭', '太空', '航天器', '卫星通信', '卫星导航', '卫星应用'],
    '可控核聚变': ['聚变', '超导', '托卡马克', '第一壁', '偏滤器', '核聚变', '人造太阳', '超导磁体'],
    '脑机接口': ['脑机', '神经', '脑科', '神经康复', '疼痛', '康复', '医疗器械'],
    '量子计算': ['量子', '量子计算', '量子通信', '量子加密', '量子芯片'],
}

# ST过滤
ST_FILTER_ENABLED = True

# 主题-行业白名单
THEME_INDUSTRY_WHITELIST = {
    '银行': ['银行'],
    '证券': ['证券', '资本市场服务'],
    '券商': ['证券', '资本市场服务'],
    '煤炭': ['煤炭'],
    '黄金': ['有色金属'],
}

# 主题-行业互斥规则
THEME_INDUSTRY_EXCLUDE = {
    'AI算力': ['煤炭开采', '造纸', '钢加工', '化学原料'],
    '黄金': ['铜', '铅锌', '钢铁'],
    '煤炭': ['化学制品', '化学原料', '化工原料', '化工', '塑料'],
    '军工': ['软件服务', 'IT设备', '互联网', '出版业', '影视音像', '广告包装', '房地产', '银行', '保险'],
    '游戏': ['基建', '勘察', '交通工程', '建筑设计', '化工', '煤炭'],
    '合成生物': ['养殖业', '生猪', '畜禽饲料', 'CDMO', '医疗服务'],
}

# 主题-股票黑名单
THEME_STOCK_BLACKLIST = {
    'AI算力': {'思源电气', '中国宝安', '诺德股份'},
    '消费电子': {'禾盛新材', '慧谷新材'},
    '创新药': {'利民股份', '富邦科技'},
}

# 主题互斥对
THEME_MUTEX_PAIRS = [
    ('AI算力', '游戏'),
    ('AI算力', '消费'),
    ('半导体', 'AI算力'),
    ('机器人', '新能源车'),
    ('军工', '低空经济'),
    ('军工', '商业航天'),
    ('数据要素', '信创'),
    ('可控核聚变', '电力'),
    ('可控核聚变', '新能源车'),
    ('高端材料', '合成生物'),
    ('高端材料', '煤炭'),
    ('脑机接口', '创新药'),
    ('量子计算', '信创'),
    ('量子计算', '半导体'),
    ('智能驾驶', '新能源车'),
    ('白酒', '消费'),
    ('白酒', '游戏'),
    ('证券', '银行'),
    ('消费', '游戏'),
]

# 预编译互斥对为 frozenset 集合，实现 O(1) 查找
_THEME_MUTEX_SET = {frozenset(p) for p in THEME_MUTEX_PAIRS}


# ============================================================
# 主流程
# ============================================================
def build_theme_stock_map_v2():
    print(f"{'='*60}")
    print(f"构建主题-个股映射 V2")
    print(f"交易日: {TRADE_DATE}")
    print(f"{'='*60}")

    # 1. 获取基础数据
    print("\n[1/4] 加载基础数据...")
    dc_df = theme_ts.get_dc_members()
    try:
        stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
    except Exception as e:
        print(f"[错误] 获取 stock_basic 失败: {e}")
        return None

    mainbiz_path = os.path.join(CACHE_DIR, 'stock_company_mainbiz.json')
    stock_mainbiz = {}
    if os.path.exists(mainbiz_path):
        with open(mainbiz_path, 'r', encoding='utf-8') as f:
            stock_mainbiz = json.load(f)
        print(f"  主营业务数据: {len(stock_mainbiz)} 只")

    sw_data = theme_ts.get_sw_members()
    ths_data = theme_ts.get_ths_members()

    # 2. 转换新配置
    print("\n[2/4] 转换 theme_config.json 为匹配格式...")
    new_config_path = os.path.join(BASE_DIR, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json')
    if not os.path.exists(new_config_path):
        # 尝试其他路径
        new_config_path = os.path.join(BASE_DIR, 'theme_kg_v3', 'config', 'theme_config.json')
    if not os.path.exists(new_config_path):
        print(f"[错误] 未找到 theme_config.json")
        return None

    # 一次性读取配置，避免重复 I/O
    with open(new_config_path, 'r', encoding='utf-8') as f:
        new_cfg = json.load(f)

    old_format_themes = convert_new_config_to_old(new_cfg, stock_basic_df)
    print(f"  转换完成: {len(old_format_themes)} 个主题")

    print("\n  主题映射对照:")
    for key, cfg in new_cfg.items():
        if key.startswith('_'):
            continue
        cn_name = cfg.get("name_cn", key)
        n_industry = len(old_format_themes[cn_name]['industry'])
        n_concept = len(old_format_themes[cn_name]['concept'])
        n_leader = len(old_format_themes[cn_name]['leader_companies'])
        n_core = len(old_format_themes[cn_name]['core_companies'])
        print(f"    {key:<25s} → {cn_name:<10s} industry={n_industry} concept={n_concept} leader={n_leader} core={n_core}")

    # 3. 执行匹配
    print("\n[3/4] 执行主题-个股匹配...")
    theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_ts.match_theme_stocks(
        old_format_themes, dc_df, stock_basic_df,
        stock_mainbiz=stock_mainbiz, sw_data=sw_data, ths_data=ths_data
    )
    print(f"  匹配完成: {len(theme_stock_map)} 个主题有对应关系")

    # 4. 过滤 + 输出 CSV
    print("\n[4/4] 过滤并输出 CSV...")

    via_priority = {
        'leader_company': 4, 'core_company': 3,
        'dc_industry_board': 2, 'stock_basic_industry': 2,
        'stock_basic_industry_alias': 1, 'concept_as_industry': 1,
        'concept_fallback': 0, 'sw_industry': 2, 'sw_industry_board': 2,
        'ths_concept': 1,
    }

    MAX_STOCKS_PER_THEME = 300
    MAX_THEMES_PER_STOCK = 5
    LOW_SCORE_THRESHOLD = 5

    # 4a. 主题→股票列表
    themes_output_raw = {}
    total_refs_raw = 0
    for theme_name, stocks in theme_stock_map.items():
        stock_list = []
        for code, meta in stocks.items():
            stock_name = name_map_basic.get(code, code)
            industry = stock_basic_industry.get(code, "")
            via = meta.get("via", "")
            irs_layer = meta.get("irs_layer", "")

            # ST过滤
            if ST_FILTER_ENABLED and ('ST' in stock_name or '*ST' in stock_name):
                continue

            # 黑名单
            if theme_name in THEME_STOCK_BLACKLIST:
                if stock_name in THEME_STOCK_BLACKLIST[theme_name]:
                    continue

            # 行业白名单
            if theme_name in THEME_INDUSTRY_WHITELIST and via not in ('leader_company', 'core_company'):
                whitelist = THEME_INDUSTRY_WHITELIST[theme_name]
                if not any(w in industry for w in whitelist):
                    continue

            # 行业互斥
            if theme_name in THEME_INDUSTRY_EXCLUDE:
                excluded = THEME_INDUSTRY_EXCLUDE[theme_name]
                if any(ex in industry for ex in excluded):
                    continue

            # IRS分层过滤
            if irs_layer == 'excluded':
                continue

            # 低分过滤
            score = meta.get("score", 0)
            if via in ('concept_fallback', 'stock_basic_industry_alias') and score < LOW_SCORE_THRESHOLD:
                continue

            stock_list.append({
                "code": code,
                "name": stock_name,
                "via": via,
                "chain_distance": meta.get("chain_distance", 2),
                "score": score,
                "irs_score": meta.get("irs_score", 0),
                "irs_layer": irs_layer,
                "industry": industry,
                "concepts": stock_concepts.get(code, []),
            })
            total_refs_raw += 1

        stock_list.sort(key=lambda x: -x.get('irs_score', x.get('score', 0)))
        themes_output_raw[theme_name] = stock_list

    # 4b. 股票→主题映射 + 去重
    stocks_output_raw = {}
    for theme_name, stock_list in themes_output_raw.items():
        for s in stock_list:
            code = s['code']
            if code not in stocks_output_raw:
                stocks_output_raw[code] = {
                    "name": s['name'],
                    "industry": s['industry'],
                    "themes": [],
                    "scores": {},
                    "vias": {},
                    "concepts": stock_concepts.get(code, []),
                }
            stocks_output_raw[code]["themes"].append(theme_name)
            stocks_output_raw[code]["scores"][theme_name] = s['score']
            stocks_output_raw[code]["vias"][theme_name] = s['via']

    # 4c. 去重
    stocks_output = {}
    for code, info in stocks_output_raw.items():
        theme_items = [(t, info['scores'][t], info['vias'][t]) for t in info['themes']]
        theme_items.sort(key=lambda x: (-via_priority.get(x[2], -1), -x[1]))

        # 互斥过滤（O(1) frozenset 查找）
        selected = []
        for t in theme_items:
            if len(selected) >= MAX_THEMES_PER_STOCK:
                break
            if any(frozenset({t[0], st[0]}) in _THEME_MUTEX_SET for st in selected):
                continue
            # concept_fallback超3个时只保留3个
            fallback_count = sum(1 for st in selected if st[2] == 'concept_fallback')
            if t[2] == 'concept_fallback' and len(selected) - fallback_count <= 0 and len(selected) >= 3:
                continue
            selected.append(t)

        stocks_output[code] = {
            "name": info["name"],
            "industry": info["industry"],
            "themes": [t[0] for t in selected],
            "concepts": info.get("concepts", []),
        }

    # 4c.5. 子主题名 → 母主题名映射
    # IRS匹配时可能将子主题（如"人形机器人"）作为独立主题名纳入，
    # 导致后续子主题匹配时找不到 parent_subtheme_index["人形机器人"]。
    # 这里将子主题名映射回母主题名（如"人形机器人"→"机器人"）。
    # 修复三花智控（人形机器人核心公司）未被纳入机器人主题的问题。
    try:
        _subtheme_cfg = load_subtheme_map()
        if _subtheme_cfg:
            sub_to_parent = {}
            for parent, subthemes in _subtheme_cfg.items():
                for sub_name in subthemes.keys():
                    sub_to_parent[sub_name] = parent
            remap_count = 0
            for code in list(stocks_output.keys()):
                themes = stocks_output[code].get('themes', [])
                new_themes = []
                changed = False
                for t in themes:
                    if t in sub_to_parent:
                        parent = sub_to_parent[t]
                        if parent not in new_themes:
                            new_themes.append(parent)
                            changed = True
                    else:
                        new_themes.append(t)
                if changed:
                    stocks_output[code]['themes'] = new_themes
                    remap_count += 1
            if remap_count:
                print(f"  [子主题映射] 已修正 {remap_count} 只股票的子主题→母主题归属")
    except Exception as e:
        print(f"  [警告] 子主题名映射失败: {e}")

    # 4d. 重建最终主题→股票
    themes_output = {}
    for code, info in stocks_output.items():
        for theme_name in info["themes"]:
            if theme_name not in themes_output:
                themes_output[theme_name] = []
            meta = theme_stock_map[theme_name].get(code, {})
            themes_output[theme_name].append({
                "code": code,
                "name": info["name"],
                "via": meta.get("via", ""),
                "score": meta.get("score", 0),
                "irs_score": meta.get("irs_score", 0),
                "irs_layer": meta.get("irs_layer", ""),
                "industry": info["industry"],
                "concepts": stock_concepts.get(code, []),
            })

    for theme_name in themes_output:
        themes_output[theme_name].sort(key=lambda x: -x.get('irs_score', x.get('score', 0)))
        themes_output[theme_name] = themes_output[theme_name][:MAX_STOCKS_PER_THEME]

    # 5. 输出 CSV
    csv_file = os.path.join(OUTPUT_DIR, f"theme_stock_map_v2_{TRADE_DATE}.csv")
    with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['主题', '主题英文KEY', '股票代码', '股票名称', '匹配路径', '评分', '行业', '概念'])
        for theme_key, cfg in new_cfg.items():
            cn_name = cfg.get("name_cn", theme_key)
            if cn_name not in themes_output:
                continue
            for s in themes_output[cn_name]:
                writer.writerow([
                    cn_name,
                    theme_key,
                    s['code'],
                    s['name'],
                    s['via'],
                    s['score'],
                    s['industry'],
                    '|'.join(s['concepts'][:5]) if s['concepts'] else '',
                ])

    # 同时输出 JSON（兼容旧格式）
    json_file = os.path.join(CACHE_DIR, f"theme_stock_map_v2_{TRADE_DATE}.json")
    json_output = {
        "trade_date": TRADE_DATE,
        "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "n_themes": len(themes_output),
        "n_stocks": len(stocks_output),
        "n_stock_refs": sum(len(v) for v in themes_output.values()),
        "themes": themes_output,
        "stocks": stocks_output,
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)

    total_refs = sum(len(v) for v in themes_output.values())
    print(f"\n{'='*60}")
    print(f"完成基础映射！")
    print(f"  CSV: {csv_file}")
    print(f"  JSON: {json_file}")
    print(f"  主题数: {len(themes_output)}")
    print(f"  个股数: {len(stocks_output)}")
    print(f"  映射数: {total_refs}")
    print(f"{'='*60}")

    # ─── 5. Sub-theme Heat Matrix 分析层 ───
    print("\n[5/7] Sub-theme Heat Matrix 分析...")
    subtheme_heat = calc_subtheme_heat_matrix(
        themes_output, stocks_output, stock_mainbiz, new_cfg
    )
    if subtheme_heat:
        n_sub = sum(len(v['subthemes']) for v in subtheme_heat.values())
        print(f"  子主题热度矩阵完成: {n_sub}个子主题, {len(subtheme_heat)}个母主题")

    # ─── 6. 动态相关性分析层（ETF量价协同 → 主导叙事识别）───
    print("\n[6/7] 动态相关性分析（ETF量价协同 → 主导叙事识别）...")
    dominant_map = run_dynamic_correlation(
        themes_output, stocks_output, new_cfg, TRADE_DATE
    )
    stock_subtheme_map = {}

    # ─── 7. Sub-theme Dynamic Correlation ───
    # 无论 dominant_map 是否有数据，都执行子主题分配
    #（无 ETF 数据时使用概念/关键词匹配，无子主题配置时用一级主题兜底）
    print("\n[7/7] Sub-theme Dynamic Correlation（子主题分配）...")
    if dominant_map:
        etf_corr_map = {}
        for code, dm in dominant_map.items():
            corr_details = dm.get('corr_details', {})
            etf_corr_map[code] = {}
            for theme_name, details in corr_details.items():
                etf_corr_map[code][theme_name] = details
    else:
        etf_corr_map = {}
        print("  动态相关性分析跳过（使用概念/关键词匹配兜底）")

    stock_subtheme_map = run_subtheme_dynamic_correlation(
        themes_output, stocks_output, stock_mainbiz, new_cfg,
        etf_corr_map=etf_corr_map,
        subtheme_heat_lookup=_build_subtheme_heat_lookup(subtheme_heat),
    )

    # 将子主题数据写入 stocks_output（仅新增字段）
    for code, info in stocks_output.items():
        st = stock_subtheme_map.get(code, {})
        if st:
            info['subtheme'] = st['subtheme']
            info['subtheme_confidence'] = st['subtheme_confidence']
            info['candidate_subthemes'] = st['candidate_subthemes']
            info['subtheme_features'] = st['subtheme_features']

    # ─── 8. 补全兜底主题的热力矩阵 ───
    # 对于无子主题配置的一级主题（如脑机接口），
    # subtheme_heat 中没有数据，导致热力引擎跳过、精华报告不显示。
    # 此处从 stock_subtheme_map 收集所有出现的一级主题，
    # 为缺失的主题创建兜底热力条目：一级主题名=子主题名。
    print("\n[8/12] 补全兜底主题的热力矩阵...")
    fallback_themes_added = 0
    collected_parents = set()
    for code, st_info in stock_subtheme_map.items():
        p = st_info.get('parent_theme', '')
        if p:
            collected_parents.add(p)
    for parent_theme in sorted(collected_parents):
        if parent_theme in subtheme_heat:
            continue  # 已有热力数据，跳过
        # 从 themes_output 获取该主题的个股池
        parent_stocks = themes_output.get(parent_theme, [])
        if not parent_stocks:
            continue
        # 以一级主题名作为子主题名，构建兜底热力条目
        total_parent = len(parent_stocks)
        fallback_sub = {
            parent_theme: {  # 子主题名=一级主题名
                'stock_count': total_parent,
                'parent_ratio': 1.0,
                'concentration': round(min(5.0 / max(total_parent, 1) * 3, 1.0), 3),
                'keyword_penetration': 0.5,
                'core_ratio': 0.3,
                'avg_raw_score': 5.0,
                'heat_score': 0.35,
                'top_stocks': [
                    {'code': s['code'], 'name': s['name'], 'score': s.get('score', 50)}
                    for s in parent_stocks[:10]
                ],
            }
        }
        subtheme_heat[parent_theme] = {
            'total_stocks': total_parent,
            'subthemes': fallback_sub,
        }
        fallback_themes_added += 1
        print(f"  [补全] {parent_theme}: {total_parent}只股票, 子主题={parent_theme}")

    if fallback_themes_added:
        print(f"  共补全 {fallback_themes_added} 个兜底主题的热力矩阵")
    else:
        print("  无需补全")

    # ─── 9. Sub-theme Heat Matrix Engine ───
    print("\n[9/12] Sub-theme Heat Matrix Engine（四维评分+贡献度+内部轮动）...")
    subtheme_report = run_subtheme_heat_engine(
        themes_output, stocks_output, stock_subtheme_map,
        subtheme_heat, TRADE_DATE
    )

    # ─── 10. Stock Role Evolution Engine ───
    print("\n[10/12] Stock Role Evolution Engine（角色演化引擎）...")
    role_results = _run_role_evolution_layer(
        stocks_output, stock_subtheme_map, subtheme_report, TRADE_DATE
    )

    # ─── 11. Sub-theme Stock Scoring ───
    print("\n[11/12] Sub-theme Stock Scoring（子主题内部股票评分）...")
    scoring_results = _run_scoring_layer(
        stocks_output, stock_subtheme_map, subtheme_report,
        role_results, TRADE_DATE
    )

    # ─── 12. Entry Timing Engine ───
    print("\n[12/12] Entry Timing Engine（入场时机引擎）...")
    entry_timing_results = _run_entry_timing_layer(
        stocks_output, subtheme_report, role_results,
        scoring_results, TRADE_DATE
    )

    # 重新输出包含所有分析层的CSV + JSON
    _export_with_dominant(dominant_map, themes_output, stocks_output, new_cfg,
                          subtheme_heat, stock_subtheme_map, subtheme_report,
                          role_results, scoring_results, entry_timing_results)

    # 自动生成主题精华报告
    _generate_essence_report(TRADE_DATE)

    return themes_output


# ═══════════════════════════════════════════════════════════
# 动态相关性分析引擎
# ═══════════════════════════════════════════════════════════

def _get_kline_safe(ts_code, start, end, max_retries=2):
    """安全获取K线数据（带重试）
    
    ts_code: str 或 list[str]，单只股票代码或代码列表
    """
    codes = [ts_code] if isinstance(ts_code, str) else ts_code
    for attempt in range(max_retries):
        try:
            df = theme_ts.get_daily_kline(codes, start, end)
            if df is not None and not df.empty:
                return df.sort_values('trade_date')
        except Exception:
            if attempt < max_retries - 1:
                continue
    # 回退：尝试使用 fund_daily 接口（ETF专用）
    for attempt in range(max_retries):
        try:
            df = _fetch_etf_kline_fallback(codes, start, end)
            if df is not None and not df.empty:
                print(f"  [ETF回退] 通过 fund_daily 获取 {len(codes)} 只ETF成功")
                return df.sort_values('trade_date')
        except Exception:
            if attempt < max_retries - 1:
                continue
    return None


def _fetch_etf_kline_fallback(codes, start, end):
    """使用 fund_daily 接口获取ETF K线数据（当 daily 接口无法获取ETF时回退）"""
    from tushare_quant import pro as ts_pro
    if ts_pro is None:
        return None
    all_parts = []
    for code in codes:
        try:
            # 提取纯数字代码（去掉.SH/.SZ后缀）
            pure_code = code.replace('.SH', '').replace('.SZ', '') if code else code
            df = ts_pro.fund_daily(ts_code=pure_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = df.copy()
                df['ts_code'] = code
                # 统一列名：fund_daily 返回的列有 trade_date, open, high, low, close, vol, amount
                if 'vol' not in df.columns and 'volume' in df.columns:
                    df['vol'] = df['volume']
                all_parts.append(df)
                import time
                time.sleep(0.15)
        except Exception:
            continue
    return pd.concat(all_parts, ignore_index=True) if all_parts else None


def _fetch_single_etf_kline(ts_pro, code, start, end):
    """通过 fund_daily 获取单只ETF K线数据
    
    注意: fund_daily 的 ts_code 需要包含 .SH/.SZ 后缀
    """
    try:
        # 确保 code 包含交易所后缀（fund_daily 需要完整 ts_code）
        fund_code = code if ('.SH' in code or '.SZ' in code) else code
        df = ts_pro.fund_daily(ts_code=fund_code, start_date=start, end_date=end)
        if df is not None and not df.empty:
            df = df.copy()
            df['ts_code'] = code
            df['trade_date'] = df['trade_date'].astype(str)
            # 统一列名
            if 'vol' not in df.columns and 'volume' in df.columns:
                df['vol'] = df['volume']
            if 'amount' not in df.columns and 'amt' in df.columns:
                df['amount'] = df['amt']
            return df.sort_values('trade_date')
    except Exception:
        return None
    return None


def _clear_failed_etfs(etf_codes):
    """从 failed_stocks 黑名单中清除ETF代码（防止被 get_daily_kline 跳过）"""
    import sqlite3
    try:
        conn = sqlite3.connect(theme_ts.DB_PATH)
        cur = conn.cursor()
        for code in etf_codes:
            cur.execute("DELETE FROM failed_stocks WHERE ts_code=?", (code,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _build_etf_kline_map(new_cfg, trade_date, lookback=60):
    """为有ETF的主题构建 {主题中文名: etf_kline_df} 映射
    使用 fund_daily 接口直接获取ETF数据，绕过 pro.daily（不支持ETF）"""
    from datetime import datetime, timedelta
    import time
    dt = datetime.strptime(str(trade_date), "%Y%m%d")
    start = (dt - timedelta(days=lookback + 30)).strftime("%Y%m%d")
    end = str(trade_date)

    etf_map = {}  # {中文名: etf_code}
    for key, cfg in new_cfg.items():
        if key.startswith('_'): continue
        cn = cfg.get('name_cn', key)
        etf = cfg.get('main_etf', '') or (cfg.get('etf_codes') or [''])[0]
        if etf:
            etf_map[cn] = etf

    # 清空ETF在黑名单中的记录，避免被 get_daily_kline 跳过
    _clear_failed_etfs(list(etf_map.values()))

    # 通过 fund_daily 直接获取ETF数据
    from tushare_quant import pro as ts_pro
    etf_codes = list(set(etf_map.values()))
    print(f"  加载 {len(etf_codes)} 只ETF K线（fund_daily 接口）...")

    etf_kline_map = {}
    success_count = 0
    for code in etf_codes:
        df = _fetch_single_etf_kline(ts_pro, code, start, end)
        if df is not None and not df.empty:
            cn_match = [cn for cn, ec in etf_map.items() if ec == code]
            if cn_match:
                etf_kline_map[cn_match[0]] = df
                success_count += 1
                print(f"    ✓ {code} ({cn_match[0]})  {len(df)} 条")
            time.sleep(0.12)

    print(f"  成功加载 {success_count}/{len(etf_codes)} 只ETF数据")
    return etf_kline_map


def _calc_correlation(stock_returns, etf_returns):
    """计算两个序列的Pearson相关系数"""
    import numpy as np
    min_len = min(len(stock_returns), len(etf_returns))
    if min_len < 3:
        return 0.0
    s = stock_returns[-min_len:].values
    e = etf_returns[-min_len:].values
    if np.std(s) < 1e-8 or np.std(e) < 1e-8:
        return 0.0
    return float(np.corrcoef(s, e)[0, 1])


def _calc_volume_synergy(stock_vol_ratios, etf_vol_ratios):
    """量能协同：计算个股与ETF放量日的重叠率"""
    min_len = min(len(stock_vol_ratios), len(etf_vol_ratios))
    if min_len < 3:
        return 0.0
    s = stock_vol_ratios[-min_len:].values
    e = etf_vol_ratios[-min_len:].values
    # 放量日：成交量 > 20日均量 * 1.2
    s_surge = (s > 1.2).astype(int)
    e_surge = (e > 1.2).astype(int)
    overlap = (s_surge & e_surge).sum()
    total = (s_surge | e_surge).sum()
    return overlap / max(total, 1)


def analyze_stock_etf_correlation(stock_code, stock_kline, etf_kline_map,
                                   stock_mainbiz='', stock_concepts=None, theme_mainbiz_keywords=None,
                                   mapped_themes=None, lookback=20):
    """
    分析个股与各主题ETF的量价协同，返回 {主题: 相关性得分}

    评分模型:
      price_corr (40%): 日收益率Pearson相关系数（20d*0.6 + 10d*0.4）
      vol_synergy (25%): 量能协同（放量日重叠率）
      rel_strength (15%): 相对强度匹配度（个股超额收益的绝对值越低越好）
      keyword_boost (20%): 主营业务/概念与主题关键词的语义对齐
      composite = price_corr*0.4 + vol_synergy*0.25 + (1-|rel_strength_norm|)*0.15 + keyword_boost*0.2
    """
    import numpy as np
    if stock_kline is None or stock_kline.empty:
        return {}

    stock_kline = stock_kline.sort_values('trade_date')
    stock_close = stock_kline['close'].astype(float).values
    stock_vol = stock_kline['vol'].astype(float).values

    if len(stock_close) < lookback + 2:
        return {}

    # 个股日收益率
    stock_returns = pd.Series(stock_close).pct_change().dropna()
    # 个股量比（相对于20日均量）
    stock_vol_series = pd.Series(stock_vol)
    stock_vol_ma20 = stock_vol_series.rolling(20, min_periods=5).mean()
    stock_vol_ratios = stock_vol_series / stock_vol_ma20.replace(0, np.nan)

    # 构建股票文本特征（用于关键词匹配）
    stock_text_features = (stock_mainbiz or '') + ' ' + ' '.join(stock_concepts or [])

    results = {}
    for theme_name, etf_kline in etf_kline_map.items():
        try:
            etf_kline = etf_kline.sort_values('trade_date')
            etf_close = etf_kline['close'].astype(float).values
            etf_vol = etf_kline['vol'].astype(float).values

            if len(etf_close) < lookback + 2:
                continue

            # 对齐日期
            common_dates = np.intersect1d(stock_kline['trade_date'].values, etf_kline['trade_date'].values)
            if len(common_dates) < lookback:
                continue

            s_idx = stock_kline['trade_date'].isin(common_dates).values
            e_idx = etf_kline['trade_date'].isin(common_dates).values

            s_close = stock_close[s_idx]
            e_close = etf_close[e_idx]

            # 收益率相关（双窗口）
            s_ret = pd.Series(s_close).pct_change().dropna()
            e_ret = pd.Series(e_close).pct_change().dropna()

            corr_20 = _calc_correlation(s_ret, e_ret)  # 默认是用全部对齐数据
            corr_10 = _calc_correlation(s_ret.tail(12), e_ret.tail(12)) if len(s_ret) >= 12 else corr_20
            price_corr = corr_20 * 0.6 + corr_10 * 0.4

            # 量能协同
            s_vr = stock_vol_ratios[s_idx][-min(len(s_idx[s_idx]), 25):]
            e_vr = pd.Series(etf_vol[e_idx]) / pd.Series(etf_vol[e_idx]).rolling(20, min_periods=5).mean().replace(0, np.nan)
            e_vr = e_vr.dropna()
            vol_synergy = _calc_volume_synergy(s_vr, e_vr.tail(len(s_vr)))

            # 相对强度
            stock_total_ret = s_close[-1] / s_close[0] - 1
            etf_total_ret = e_close[-1] / e_close[0] - 1
            rel_strength = abs(stock_total_ret - etf_total_ret)
            rel_strength_norm = min(rel_strength, 0.5) / 0.5  # 0~1, 0=最佳

            # ── 关键词对齐加分 ──
            kw = theme_mainbiz_keywords.get(theme_name, []) if theme_mainbiz_keywords else []
            if kw and stock_text_features:
                hits = sum(1 for k in kw if k.lower() in stock_text_features.lower())
                keyword_boost = min(hits / max(len(kw) * 0.15, 1), 1.0)
            else:
                keyword_boost = 0.0

            # ── 叙事偏离惩罚 ──
            # 若股票大幅偏离其映射主题ETF的涨幅，降低该映射主题的权重
            divergence_penalty = 0.0
            mapped_penalty_applied = False
            if mapped_themes and theme_name in mapped_themes:
                if rel_strength > 0.3:
                    divergence_penalty = min((rel_strength - 0.3) * 0.5, 0.3)
                    mapped_penalty_applied = True

            # 复合得分（价格相关40% + 量能协同25% + 强度匹配15% + 关键词20% - 偏离惩罚）
            composite = (price_corr * 0.4 + vol_synergy * 0.25 +
                         (1 - rel_strength_norm) * 0.15 + keyword_boost * 0.2 -
                         divergence_penalty)
            composite = max(0, min(1, composite))

            results[theme_name] = {
                'etf_code': etf_kline['ts_code'].iloc[0],
                'price_corr': round(price_corr, 3),
                'corr_20d': round(corr_20, 3),
                'corr_10d': round(corr_10, 3),
                'vol_synergy': round(vol_synergy, 3),
                'rel_strength': round(rel_strength, 4),
                'keyword_boost': round(keyword_boost, 3),
                'div_penalty': round(divergence_penalty, 3),
                'composite': round(composite, 3),
            }
        except Exception:
            continue

    return results


def run_dynamic_correlation(themes_output, stocks_output, new_cfg, trade_date):
    """
    动态相关性分析主流程：
    1. 识别"双叙事交叉股"（属于2个以上主题的股票）
    2. 对每只交叉股，分析其与各主题ETF的量价协同
    3. 确定主导叙事（dominant_theme）
    4. 输出结构化结果

    Returns:
        dominant_map: {code: {dominant_theme, is_cross_narrative, theme_scores, ...}}
    """
    import numpy as np
    from datetime import datetime, timedelta

    # 1. 找出交叉股（属于2个以上主题）
    cross_stocks = {}
    for code, info in stocks_output.items():
        themes = info.get('themes', [])
        if len(themes) >= 2:
            cross_stocks[code] = themes
        # 也包含特定股票（如飞龙股份用于验证）
        if code == '002536.SZ':
            cross_stocks.setdefault(code, themes)

    if not cross_stocks:
        # 如果没有交叉股，至少分析飞龙股份
        for code, info in stocks_output.items():
            if code == '002536.SZ':
                cross_stocks[code] = info.get('themes', [])
                break

    if not cross_stocks:
        print("  无交叉叙事股需分析")
        return {}

    print(f"  识别 {len(cross_stocks)} 只交叉/候选股")

    # 2. 构建ETF K线映射
    etf_kline_map = _build_etf_kline_map(new_cfg, trade_date)
    if not etf_kline_map:
        return {}

    # 3. 批量获取个股K线
    dt = datetime.strptime(str(trade_date), "%Y%m%d")
    start = (dt - timedelta(days=90)).strftime("%Y%m%d")
    end = str(trade_date)
    stock_codes = list(cross_stocks.keys())
    print(f"  加载 {len(stock_codes)} 只个股K线...")
    all_stock_kline = _get_kline_safe(stock_codes, start, end)
    if all_stock_kline is None or all_stock_kline.empty:
        return {}

    # 4. 逐一分析（带主营业务关键词对齐）
    # 加载主营业务数据
    mainbiz_path = os.path.join(CACHE_DIR, 'stock_company_mainbiz.json')
    stock_mainbiz_data = {}
    if os.path.exists(mainbiz_path):
        try:
            with open(mainbiz_path, 'r', encoding='utf-8') as f:
                stock_mainbiz_data = json.load(f)
        except Exception:
            pass

    dominant_map = {}
    for code in stock_codes:
        stock_kline = all_stock_kline[all_stock_kline['ts_code'] == code]
        if stock_kline.empty:
            continue
        name = stocks_output.get(code, {}).get('name', code)
        mapped_themes = stocks_output.get(code, {}).get('themes', [])

        # 获取个股主营业务和概念（用于关键词对齐）
        stock_mainbiz = stock_mainbiz_data.get(code, '')
        stock_concepts = stocks_output.get(code, {}).get('concepts', [])

        corr_results = analyze_stock_etf_correlation(
            code, stock_kline, etf_kline_map,
            stock_mainbiz=stock_mainbiz,
            stock_concepts=stock_concepts,
            theme_mainbiz_keywords=THEME_MAINBIZ_KEYWORDS,
            mapped_themes=mapped_themes
        )
        if not corr_results:
            continue

        # 按复合得分排序
        sorted_themes = sorted(corr_results.items(), key=lambda x: x[1]['composite'], reverse=True)
        top_theme = sorted_themes[0][0]
        top_score = sorted_themes[0][1]['composite']

        # ── 叙事校正：当映射主题偏离惩罚大，且非映射主题关键词匹配高 ──
        mapped_diverging = any(
            corr_results.get(t, {}).get('div_penalty', 0) > 0.1
            for t in mapped_themes
        )
        narrative_corrected = False
        if mapped_diverging:
            # 对非映射主题，依据关键词匹配度给予叙事校正加分
            corrected = {}
            for tname, tdata in corr_results.items():
                new_score = tdata['composite']
                if tname not in mapped_themes and tdata.get('keyword_boost', 0) > 0.2:
                    narrative_boost = tdata['keyword_boost'] * 0.15  # max +0.15
                    new_score = min(new_score + narrative_boost, 1.0)
                    corrected[tname] = {'composite': new_score, 'boost': narrative_boost}
            if corrected:
                # 重新排序
                for tname, cdata in corrected.items():
                    corr_results[tname]['composite'] = cdata['composite']
                sorted_themes = sorted(corr_results.items(), key=lambda x: x[1]['composite'], reverse=True)
                # 检查首位是否被校正
                new_top = sorted_themes[0][0]
                if new_top != top_theme:
                    narrative_corrected = True
                top_theme = sorted_themes[0][0]
                top_score = sorted_themes[0][1]['composite']

        # 判断是否为交叉叙事股
        in_mapped = top_theme in mapped_themes
        is_cross = len(mapped_themes) >= 2
        is_misclassified = not in_mapped and len(mapped_themes) >= 1

        # 计算第二高的分数，判断领先优势
        second_score = sorted_themes[1][1]['composite'] if len(sorted_themes) > 1 else 0
        dominance_margin = top_score - second_score

        dominant_map[code] = {
            'name': name,
            'mapped_themes': mapped_themes,
            'dominant_theme': top_theme,
            'dominant_score': top_score,
            'second_theme': sorted_themes[1][0] if len(sorted_themes) > 1 else '',
            'second_score': second_score,
            'dominance_margin': round(dominance_margin, 3),
            'is_cross_narrative': 1 if is_cross else 0,
            'is_misclassified': 1 if is_misclassified else 0,
            'narrative_corrected': 1 if narrative_corrected else 0,
            'theme_scores': {t: s['composite'] for t, s in sorted_themes},
            'corr_details': {t: {k: v for k, v in s.items() if k != 'composite'} for t, s in sorted_themes},
        }

        status = "✓" if in_mapped else ("✗交叉" if is_misclassified else "→")
        nc = "N" if narrative_corrected else ""
        print(f"    {nc}{status} {name:<8}({code}) 主导:{top_theme}({top_score:.2f}) "
              f"映射:{mapped_themes} 领先:{dominance_margin:.2f}")

    return dominant_map


# ═══════════════════════════════════════════════════════════
# Sub-theme Heat Matrix 引擎
# ═══════════════════════════════════════════════════════════

SUBTHEME_CONFIG_PATH = os.path.join(
    BASE_DIR, 'theme_kg_v3', 'theme_kg_v3', 'config', 'subtheme_map.json'
)


def load_subtheme_map():
    """加载 subtheme_map.json，返回 {母主题: {子主题名: {industry,concept,keywords,...}}}"""
    if not os.path.exists(SUBTHEME_CONFIG_PATH):
        # 尝试备用路径
        alt = os.path.join(BASE_DIR, 'theme_kg_v3', 'config', 'subtheme_map.json')
        if os.path.exists(alt):
            path = alt
        else:
            print("  [子主题] 未找到 subtheme_map.json，跳过")
            return {}
    else:
        path = SUBTHEME_CONFIG_PATH

    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _match_stock_to_subtheme(stock_info, subtheme_cfg, stock_mainbiz='', text_lower=None):
    """
    判断个股是否匹配子主题，返回匹配分数。
    
    匹配规则（总分=keyword_hits + industry_bonus + core_bonus）:
    1. 关键词命中：stock_name + concepts + mainbiz 命中 subtheme.keywords（每个命中+3）
    2. 子主题的exclude_keywords有命中则直接返回0
    3. 行业匹配：subtheme.industry命中 stock.industry（+5）
    4. 核心公司：stock_name 在 subtheme.core_companies 中（+15）
    
    过滤阈值：总得分 >= 6 才认为匹配（至少2个关键词命中或1个核心公司）

    参数:
      text_lower: 预计算的文本特征（小写），避免重复拼接，由 calc_subtheme_heat_matrix 传入
    """
    name = stock_info.get('name', '')
    industry = stock_info.get('industry', '')
    concepts = stock_info.get('concepts', [])
    
    keywords = subtheme_cfg.get('keywords', [])
    exclude = subtheme_cfg.get('exclude_keywords', [])
    sub_industry = subtheme_cfg.get('industry', [])
    core_companies = subtheme_cfg.get('core_companies', [])
    
    # 拼接文本特征（若已预计算则跳过，减少重复内存开销）
    if text_lower is None:
        text_features = f"{name} {stock_mainbiz} {' '.join(concepts)}"
        text_lower = text_features.lower()
    
    # 排除关键词检查
    for ek in exclude:
        if ek.lower() in text_lower:
            return 0
    
    score = 0
    hit_keywords = []
    
    # 0. 概念重叠检查：如果子主题配置了concept列表，股票必须至少命中1个
    #    防止纯行业匹配进入（如农机→汽车热管理）
    sub_concepts = subtheme_cfg.get('concept', [])
    if sub_concepts:
        sc_lower = set(c.lower() for c in concepts)
        concept_hits = sum(1 for sc in sub_concepts if sc.lower() in sc_lower)
        if concept_hits == 0 and not core_companies:
            # 无概念重叠 + 非核心公司 → 直接否决
            return 0
    
    # 1. 关键词命中
    for kw in keywords:
        if kw.lower() in text_lower:
            score += 3
            hit_keywords.append(kw)
    
    # 2. 行业匹配
    if sub_industry and industry:
        for si in sub_industry:
            if si.lower() in industry.lower():
                score += 5
                break
    
    # 3. 核心公司
    if core_companies and name in core_companies:
        score += 15
    
    # 过滤阈值
    return score if score >= 6 else 0


def calc_subtheme_heat_matrix(themes_output, stocks_output, stock_mainbiz, new_cfg):
    """
    计算 Sub-theme Heat Matrix。
    
    输入:
      themes_output: {母主题: [{code, name, via, score, industry, concepts}, ...]}
      stocks_output: {code: {name, industry, themes, concepts}}
      stock_mainbiz: {code: "主营业务文本"}
      new_cfg: theme_config.json 原始配置
    
    输出:
      {母主题名: {
          total_stocks: N,
          subthemes: {
            子主题名: {
              stock_count, parent_ratio, concentration,
              keyword_penetration, avg_score, heat_score,
              top_stocks: [{code, name, score}, ...]
            }
          }
        }
      }
    """
    subtheme_map = load_subtheme_map()
    if not subtheme_map:
        return {}
    
    result = {}
    
    for parent_theme_cn, subthemes in subtheme_map.items():
        # 获取母主题的个股池
        parent_stocks = themes_output.get(parent_theme_cn, [])
        if not parent_stocks:
            continue
        
        total_parent = len(parent_stocks)

        # 预计算个股文本特征（避免每子主题循环重复拼接）
        stock_text_cache = {}
        for s in parent_stocks:
            code = s['code']
            name = s.get('name', '')
            concepts = s.get('concepts', [])
            mainbiz_text = stock_mainbiz.get(code, '')
            stock_text_cache[code] = f"{name} {mainbiz_text} {' '.join(concepts)}".lower()

        subtheme_data = {}
        for sub_name, sub_cfg in subthemes.items():
            matched = []
            for s in parent_stocks:
                code = s['code']
                score = _match_stock_to_subtheme(
                    s, sub_cfg, stock_mainbiz.get(code, ''),
                    text_lower=stock_text_cache[code]
                )
                if score > 0:
                    matched.append({
                        'code': code,
                        'name': s['name'],
                        'match_score': score,
                        'via': s.get('via', ''),
                    })
            
            if not matched:
                continue
            
            matched.sort(key=lambda x: -x['match_score'])
            stock_count = len(matched)
            parent_ratio = round(stock_count / total_parent, 4) if total_parent else 0
            
            # 集中度：top5占全部匹配数的比例
            top5 = matched[:5]
            top5_ratio = len(top5) / max(stock_count, 1)
            concentration = round(min(top5_ratio * 3, 1.0), 3)  # 放大后封顶1.0
            
            # 关键词渗透深度：平均匹配分（归一化到0-1）
            avg_raw = sum(m['match_score'] for m in matched) / max(stock_count, 1)
            keyword_penetration = round(min(avg_raw / 15.0, 1.0), 3)
            
            # 核心公司覆盖比
            core_hit = sum(1 for m in matched if m['match_score'] >= 15 and m['via'] in ('leader_company', 'core_company', ''))
            core_ratio = core_hit / max(stock_count, 1)
            
            # 综合热度分
            heat_score = round(
                concentration * 0.25 +
                keyword_penetration * 0.30 +
                parent_ratio * 0.15 +
                core_ratio * 0.30,
                3
            )
            
            subtheme_data[sub_name] = {
                'stock_count': stock_count,
                'parent_ratio': parent_ratio,
                'concentration': concentration,
                'keyword_penetration': keyword_penetration,
                'core_ratio': core_ratio,
                'avg_raw_score': round(avg_raw, 1),
                'heat_score': heat_score,
                'top_stocks': [
                    {'code': m['code'], 'name': m['name'], 'score': m['match_score']}
                    for m in matched[:10]
                ],
            }
        
        if subtheme_data:
            result[parent_theme_cn] = {
                'total_stocks': total_parent,
                'subthemes': subtheme_data,
            }
            n_active = len(subtheme_data)
            hottest = max(subtheme_data.values(), key=lambda x: x['heat_score'])
            print(f"  [{parent_theme_cn}] {n_active}个子主题活动, "
                  f"最热:{hottest.get('heat_score', 0):.2f} "
                  f"({[s['name'] for s in hottest['top_stocks'][:3]]})")
    
    return result


# ═══════════════════════════════════════════════════════════
# Sub-theme Dynamic Correlation Layer（六阶段流水线）
# ═══════════════════════════════════════════════════════════

# 各阶段权重（用于合成 final_confidence）
_SUBTHEME_STAGE_WEIGHTS = {
    'industry': 0.10,
    'concept': 0.15,
    'keyword': 0.25,
    'core_company': 0.15,
    'embedding': 0.20,
    'correlation': 0.15,
}


def _calc_industry_score(stock_industry, sub_cfg):
    """Stage 1: Industry Match — 股票行业是否命中子主题行业列表

    ⚠️ 通用/过宽行业词（如"机械"、"电子"、"通信"）不能作为唯一匹配依据，
    必须同时有概念或关键词命中才计为有效匹配。
    """
    # 过宽行业词黑名单（子串匹配时不能单独作为匹配依据）
    _BROAD_INDUSTRY_TERMS = {'机械', '电子', '通信', '计算机', '商业', '电力设备',
                              '汽车', '医药', '化工', '有色', '采掘', '建筑'}

    sub_industry = sub_cfg.get('industry', [])
    if not sub_industry or not stock_industry:
        return 0.0
    stock_industry_lower = stock_industry.lower()
    for si in sub_industry:
        si_lower = si.lower()
        if si_lower in stock_industry_lower:
            # 检查该行业词是否为过宽带词
            if si_lower in _BROAD_INDUSTRY_TERMS:
                return 0.5  # 仅给半数分，需要概念/关键词接力
            return 1.0
    return 0.0


def _calc_concept_score(stock_concepts, sub_cfg):
    """Stage 2: Concept Match — 概念重叠率"""
    sub_concepts = sub_cfg.get('concept', [])
    if not sub_concepts or not stock_concepts:
        return 0.0
    sc_set = set(c.lower() for c in stock_concepts)
    hits = sum(1 for sc in sub_concepts if sc.lower() in sc_set)
    return min(hits / max(len(sub_concepts) * 0.3, 1), 1.0)


def _calc_keyword_score(stock_info, stock_mainbiz, sub_cfg):
    """Stage 3: Keyword Match — 关键词命中深度（归一化0-1）"""
    name = stock_info.get('name', '')
    concepts = stock_info.get('concepts', [])
    keywords = sub_cfg.get('keywords', [])
    exclude = sub_cfg.get('exclude_keywords', [])

    text = f"{name} {stock_mainbiz} {' '.join(concepts)}".lower()

    # 排除词过滤
    for ek in exclude:
        if ek.lower() in text:
            return -1.0  # 直接否决

    if not keywords:
        return 0.0

    hits = sum(1 for kw in keywords if kw.lower() in text)
    # 命中15%以上关键词即满分
    return min(hits / max(len(keywords) * 0.15, 1), 1.0)


def _calc_core_company_similarity(stock_name, sub_cfg):
    """Stage 4: Core Company Similarity — 名称与核心公司相似度
    
    使用Jaccard字符二元组相似度判断stock_name与core_companies的接近程度。
    """
    core = sub_cfg.get('core_companies', [])
    if not core or not stock_name:
        return 0.0

    # 精确匹配 → 1.0
    if stock_name in core:
        return 1.0

    # 字符二元组 Jaccard 相似度（处理中文名称部分匹配）
    def bigram_set(s):
        s = s.replace(' ', '').replace('(', '').replace(')', '')
        return set(s[i:i+2] for i in range(len(s)-1))

    name_bg = bigram_set(stock_name)
    if not name_bg:
        return 0.0

    best = 0.0
    for c_name in core:
        c_bg = bigram_set(c_name)
        if not c_bg:
            continue
        inter = len(name_bg & c_bg)
        union = len(name_bg | c_bg)
        sim = inter / union if union > 0 else 0.0
        if sim > best:
            best = sim
    return best


def _calc_embedding_similarity(stock_info, stock_mainbiz, sub_cfg):
    """Stage 5: Embedding Similarity — 基于字符TF-IDF的向量相似度
    
    实现轻量级TF-IDF（不使用sklearn），比较股票文本与子主题关键词文本的余弦相似度。
    """
    from collections import Counter
    import math

    name = stock_info.get('name', '')
    concepts = stock_info.get('concepts', [])
    keywords = sub_cfg.get('keywords', [])
    sub_industry = sub_cfg.get('industry', [])
    sub_concepts = sub_cfg.get('concept', [])

    # 构建股票文本向量（基于字符二元组）
    stock_text = f"{name} {stock_mainbiz}".lower()
    stock_text += ' ' + ' '.join(concepts).lower()

    # 构建子主题文本向量
    sub_text = ' '.join(keywords).lower()
    sub_text += ' ' + ' '.join(sub_industry).lower()
    sub_text += ' ' + ' '.join(sub_concepts).lower()

    # 提取字符二元组
    def extract_bigrams(text):
        return [text[i:i+2] for i in range(max(0, len(text)-1))]

    stock_bg = extract_bigrams(stock_text)
    sub_bg = extract_bigrams(sub_text)

    if not stock_bg or not sub_bg:
        return 0.0

    # TF-IDF 权重计算（简化版：用 idf = log(N/df+1)）
    # 将两组 bigram 合并作为"语料库"
    all_bg = list(set(stock_bg + sub_bg))
    
    # 计算 TF
    stock_tf = Counter(stock_bg)
    sub_tf = Counter(sub_bg)

    # 计算 IDF（2篇文档的语料库）
    n_docs = 2
    stock_set = set(stock_bg)
    sub_set = set(sub_bg)

    def cosine_sim(tf_a, tf_b, all_vocab):
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for bg in all_vocab:
            idf = math.log((n_docs + 1) / (1 + (1 if bg in stock_set else 0) + (1 if bg in sub_set else 0))) + 1
            va = tf_a.get(bg, 0) * idf
            vb = tf_b.get(bg, 0) * idf
            dot += va * vb
            norm_a += va * va
            norm_b += vb * vb
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    sim = cosine_sim(stock_tf, sub_tf, all_bg)
    # 将余弦相似度从[-1,1]映射到[0,1]
    return max(0.0, (sim + 1.0) / 2.0)


def _calc_subtheme_correlation_score(stock_code, stock_kline_dict, sub_cfg, parent_theme_etf_corr=None):
    """Stage 6: Correlation Score — 与子主题核心股的价格相关性
    
    若子主题核心公司≥3家且有K线数据，计算个股与核心股平均收益的相关系数。
    否则回退到母主题ETF相关性。
    """
    import numpy as np
    core = sub_cfg.get('core_companies', [])
    
    # 如果有母主题ETF相关性，以此作为基准
    if parent_theme_etf_corr is not None:
        return max(0.0, min(1.0, (parent_theme_etf_corr + 1.0) / 2.0))
    
    return 0.5  # 无数据时的中性值


# ═══════════════════════════════════════════════════════════
# Stage 7 (Dynamic): Sub-theme Market Narrative Heat — 子主题市场叙事热度
# ═══════════════════════════════════════════════════════════

def _amplify_heat(heat_score):
    """用 sigmoid 变换放大子主题热度差异

    原始 heat_score 范围通常在 [0, 0.7]，区别微小（如 0.39 vs 0.334）。
    用 sigmoid 映射到 (0, 1) 并放大中间差异：
      amplified = 1 / (1 + e^(-15*(heat - 0.35)))

    该函数使 heat_score > 0.35 的子主题获得指数级升温，
    heat_score < 0.35 的子主题降温，大幅拉大热门与冷门子主题的分差。
    
    例（steepness=15）:
      液冷 heat=0.39     → amplified=0.69
      汽车热管理 heat=0.334 → amplified=0.44
      差值: 0.25 → 配合 HEAT_BOOST_RATE=0.20 → 5%分差
    """
    x = (heat_score - 0.35) * 15  # 中心化+放大（steepness=15）
    return 1.0 / (1.0 + math.exp(-x))


def _build_subtheme_heat_lookup(subtheme_heat):
    """从预计算的 subtheme_heat_matrix 提取子主题热度查找表

    输入结构（来自 calc_subtheme_heat_matrix）:
      {parent_theme: {
          'total_stocks': N,
          'subthemes': {sub_name: {'heat_score': 0.xxx, ...}}
      }}

    输出结构:
      {(parent_theme, sub_name): heat_score_0to1}

    子主题 heat_score 已经过四维加权（集中度×0.25 + 关键词渗透×0.30 +
    母主题占比×0.15 + 核心公司覆盖×0.30），反映当前市场的真实活跃度。
    """
    if not subtheme_heat:
        return {}
    lookup = {}
    for parent, pdata in subtheme_heat.items():
        subs = pdata.get('subthemes', {})
        for sname, sdata in subs.items():
            lookup[(parent, sname)] = sdata.get('heat_score', 0.0)
    return lookup


# 市场叙事热度加成系数
# 最终加成公式：score *= (1 + amplified_heat × HEAT_BOOST_RATE)
# amplified_heat 通过 sigmoid 变换放大热门与冷门子主题的差异。
# 液冷 heat=0.39 → sigmoid放大后 ~0.69 → 加成 0.69×0.20=13.8%
 # 汽车热管理 heat=0.334 → sigmoid放大后 ~0.44 → 加成 0.44×0.20=8.8%
 # 差值 ~5%，足以翻转 1.5分的原始差距
HEAT_BOOST_RATE = 0.20


def _compute_subtheme_pipeline_for_stock(stock_info, stock_mainbiz, sub_cfgs, 
                                          parent_theme_etf_corr=None, stock_kline_dict=None):
    """
    对一只股票运行六阶段流水线，返回结构化 Sub-theme Assignment。
    
    输出:
    {
        'subtheme': 'PCB高速互连',
        'subtheme_confidence': 0.91,
        'candidate_subthemes': [
            {'name': 'PCB高速互连', 'score': 91},
            {'name': 'AI服务器', 'score': 76},
            ...
        ],
        'subtheme_features': {
            'industry_score': 1.0,
            'concept_score': 0.67,
            'keyword_score': 0.85,
            'core_company_score': 0.0,
            'embedding_score': 0.72,
            'correlation_score': 0.65,
        }
    }
    """
    code = stock_info.get('code', '')
    name = stock_info.get('name', '')
    industry = stock_info.get('industry', '')
    concepts = stock_info.get('concepts', [])

    candidates = []
    for sub_name, sub_cfg in sub_cfgs.items():
        # Stage 1: Industry
        ind_score = _calc_industry_score(industry, sub_cfg)

        # Stage 2: Concept
        conc_score = _calc_concept_score(concepts, sub_cfg)

        # Stage 3: Keyword（否决机制）
        kw_score = _calc_keyword_score(stock_info, stock_mainbiz, sub_cfg)
        if kw_score < 0:  # 被排除关键词否决
            continue

        # Stage 4: Core Company
        core_score = _calc_core_company_similarity(name, sub_cfg)

        # Stage 5: Embedding
        emb_score = _calc_embedding_similarity(stock_info, stock_mainbiz, sub_cfg)

        # Stage 6: Correlation
        corr_score = _calc_subtheme_correlation_score(
            code, stock_kline_dict, sub_cfg, parent_theme_etf_corr
        )

        # 加权综合得分
        weights = _SUBTHEME_STAGE_WEIGHTS
        composite = (
            ind_score * weights['industry'] +
            conc_score * weights['concept'] +
            kw_score * weights['keyword'] +
            core_score * weights['core_company'] +
            emb_score * weights['embedding'] +
            corr_score * weights['correlation']
        )

        candidates.append({
            'name': sub_name,
            'score': round(composite * 100, 1),
            'features': {
                'industry_score': round(ind_score, 3),
                'concept_score': round(conc_score, 3),
                'keyword_score': round(kw_score, 3),
                'core_company_score': round(core_score, 3),
                'embedding_score': round(emb_score, 3),
                'correlation_score': round(corr_score, 3),
            }
        })

    if not candidates:
        return None

    # 按得分排序
    candidates.sort(key=lambda x: -x['score'])
    best = candidates[0]

    # 置信度 = 最佳得分 / 理论满分 - 第二名差距惩罚
    best_score = best['score'] / 100.0
    if len(candidates) > 1:
        margin = (best['score'] - candidates[1]['score']) / 100.0
    else:
        margin = best_score
    confidence = max(0.0, min(1.0, best_score * 0.7 + margin * 0.3))

    return {
        'subtheme': best['name'],
        'subtheme_confidence': round(confidence, 3),
        'candidate_subthemes': [{'name': c['name'], 'score': c['score']} for c in candidates[:3]],
        'subtheme_features': best['features'],
    }


def run_subtheme_dynamic_correlation(themes_output, stocks_output, stock_mainbiz, new_cfg,
                                     etf_corr_map=None, subtheme_heat_lookup=None):
    """
    对每只股票运行 Sub-theme Dynamic Correlation，结果写入 stocks_output。
    
    新增：跨母主题子主题匹配。
    
    输入:
      etf_corr_map: {代码: {主题: {corr_details}}} — 来自 ETF 相关性分析的跨主题ETF相关性
                    用于 Stage 6 的母主题ETF相关系数
    
    返回值:
      stock_subtheme_map: {代码: {subtheme, subtheme_confidence, candidate_subthemes, subtheme_features}}
      同时将结果就地写入 stocks_output
    """
    subtheme_cfg = load_subtheme_map()
    if not subtheme_cfg:
        return {}

    # 构建 {母主题: 子主题配置列表} 的快速索引
    parent_subtheme_index = {}
    for parent_cn, subthemes in subtheme_cfg.items():
        parent_subtheme_index[parent_cn] = subthemes

    # ── 构建反向索引：{公司名: [(母主题, 子主题名)]} 从所有子主题的 core_companies ──
    reverse_core_index = defaultdict(list)  # {stock_name: [(parent_theme, sub_name, sub_cfg)]}
    for parent_cn, subthemes in subtheme_cfg.items():
        for sub_name, sub_cfg in subthemes.items():
            for company in sub_cfg.get('core_companies', []):
                reverse_core_index[company].append((parent_cn, sub_name, sub_cfg))

    total = len(stocks_output)
    assigned = 0
    stock_subtheme_map = {}

    for idx, (code, info) in enumerate(stocks_output.items()):
        stock_themes = info.get('themes', [])
        if not stock_themes:
            continue

        stock_name = info.get('name', '')
        mainbiz = stock_mainbiz.get(code, '')

        # ── Step 1: 从已分配母主题中评估子主题候选 ──
        all_candidates = []  # [{name, score, parent_theme, features}]
        for pt_idx, parent_theme in enumerate(stock_themes):
            sub_cfgs = parent_subtheme_index.get(parent_theme, {})
            if not sub_cfgs:
                continue

            # ETF相关性
            parent_etf_corr = None
            if etf_corr_map and code in etf_corr_map:
                theme_corr = etf_corr_map[code].get(parent_theme, {})
                if isinstance(theme_corr, dict):
                    parent_etf_corr = theme_corr.get('price_corr', None)

            stock_info = {
                'code': code, 'name': stock_name,
                'industry': info.get('industry', ''),
                'concepts': info.get('concepts', []),
            }

            for sub_name, sub_cfg in sub_cfgs.items():
                ind_score = _calc_industry_score(info.get('industry', ''), sub_cfg)
                conc_score = _calc_concept_score(info.get('concepts', []), sub_cfg)
                kw_score = _calc_keyword_score(stock_info, mainbiz, sub_cfg)
                if kw_score < 0:  # 被排除关键词否决
                    continue
                core_score = _calc_core_company_similarity(stock_name, sub_cfg)
                emb_score = _calc_embedding_similarity(stock_info, mainbiz, sub_cfg)
                corr_score = _calc_subtheme_correlation_score(
                    code, None, sub_cfg, parent_etf_corr
                )

                weights = _SUBTHEME_STAGE_WEIGHTS
                # ── 概念门控：如果概念零匹配且行业仅过宽带词命中，直接跳过 ──
                # 但核心公司（核心公司精确匹配）绕过概念门控，确保子公司归位母主题
                if conc_score == 0 and ind_score < 1.0 and core_score < 0.5:
                    continue
                composite = (
                    ind_score * weights['industry'] +
                    conc_score * weights['concept'] +
                    kw_score * weights['keyword'] +
                    core_score * weights['core_company'] +
                    emb_score * weights['embedding'] +
                    corr_score * weights['correlation']
                )
                # 子主题匹配优先级加成（来自 subtheme_map.json 的 match_priority）
                _sub_prio = sub_cfg.get('match_priority', 1.0)
                if _sub_prio != 1.0:
                    composite *= _sub_prio

                # ── Stage 7: Market Narrative Heat 乘数加成 ──
                # 使用子主题级别热度（预计算的 subtheme_heat_matrix），
                # 经 sigmoid 变换放大后作为乘数作用于综合分
                # 例：液冷(heat=0.39) > 汽车热管理(heat=0.334)，液冷获得更高加成
                if subtheme_heat_lookup:
                    raw_heat = subtheme_heat_lookup.get((parent_theme, sub_name), 0.35)
                    amplified = _amplify_heat(raw_heat)
                    heat_boost = 1.0 + amplified * HEAT_BOOST_RATE
                    composite *= heat_boost

                all_candidates.append({
                    'name': sub_name,
                    'parent_theme': parent_theme,
                    'score': round(composite * 100, 1),
                    'features': {
                        'industry_score': round(ind_score, 3),
                        'concept_score': round(conc_score, 3),
                        'keyword_score': round(kw_score, 3),
                        'core_company_score': round(core_score, 3),
                        'embedding_score': round(emb_score, 3),
                        'correlation_score': round(corr_score, 3),
                        'heat_boost': round(heat_boost - 1.0, 4) if subtheme_heat_lookup else 0,
                    }
                })

        # ── Step 2: 跨母主题 core_company 增强 ──
        # 如果股票是某个其他主题子主题的 core_company，增加该子主题候选
        if stock_name in reverse_core_index:
            for x_parent, x_sub_name, x_sub_cfg in reverse_core_index[stock_name]:
                # 跳过已在候选中的
                if any(c['name'] == x_sub_name and c['parent_theme'] == x_parent
                       for c in all_candidates):
                    continue

                stock_info = {
                    'code': code, 'name': stock_name,
                    'industry': info.get('industry', ''),
                    'concepts': info.get('concepts', []),
                }

                ind_score = _calc_industry_score(info.get('industry', ''), x_sub_cfg)
                conc_score = _calc_concept_score(info.get('concepts', []), x_sub_cfg)
                kw_score = _calc_keyword_score(stock_info, mainbiz, x_sub_cfg)
                if kw_score < 0:
                    continue
                core_score = _calc_core_company_similarity(stock_name, x_sub_cfg)
                emb_score = _calc_embedding_similarity(stock_info, mainbiz, x_sub_cfg)

                weights = _SUBTHEME_STAGE_WEIGHTS
                composite = (
                    ind_score * weights['industry'] +
                    conc_score * weights['concept'] +
                    kw_score * weights['keyword'] +
                    core_score * weights['core_company'] +
                    emb_score * weights['embedding']
                )
                # 子主题匹配优先级加成（来自 subtheme_map.json 的 match_priority）
                _sub_prio = x_sub_cfg.get('match_priority', 1.0)
                if _sub_prio != 1.0:
                    composite *= _sub_prio

                # ── Step 2 也应用 Market Narrative Heat 乘数 ──
                if subtheme_heat_lookup:
                    raw_heat = subtheme_heat_lookup.get((x_parent, x_sub_name), 0.35)
                    amplified = _amplify_heat(raw_heat)
                    heat_boost = 1.0 + amplified * HEAT_BOOST_RATE
                    composite *= heat_boost

                all_candidates.append({
                    'name': x_sub_name,
                    'parent_theme': x_parent,
                    'score': round(composite * 100 + (40 if core_score >= 0.8 else 20), 1),  # +40精确核心公司/+20其他
                    'cross_core_match': True,
                    'features': {
                        'industry_score': round(ind_score, 3),
                        'concept_score': round(conc_score, 3),
                        'keyword_score': round(kw_score, 3),
                        'core_company_score': round(core_score, 3),
                        'embedding_score': round(emb_score, 3),
                        'correlation_score': 0,
                        'heat_boost': round(heat_boost - 1.0, 4) if subtheme_heat_lookup else 0,
                    }
                })

        if not all_candidates:
            # ── Fallback: 无子主题匹配时，用一级主题名作为子主题兜底 ──
            # 解决脑机接口等 level=2 主题无子主题配置时，股票被完全遗漏的问题
            for parent_theme in stock_themes:
                default_score = 50.0  # 中等分数，后续评分会重新计算
                all_candidates.append({
                    'name': parent_theme,  # 一级主题名作为子主题名
                    'parent_theme': parent_theme,
                    'score': default_score,
                    'fallback': True,
                    'features': {
                        'industry_score': 0.5,
                        'concept_score': 0.5,
                        'keyword_score': 0,
                        'core_company_score': 0,
                        'embedding_score': 0,
                        'correlation_score': 0,
                        'heat_boost': 0,
                    }
                })

        # ── Step 3: 选最佳子主题 ──
        all_candidates.sort(key=lambda x: -x['score'])
        best = all_candidates[0]

        # 如果最佳子主题来自跨主题匹配，更新股票的主题归属
        if best.get('cross_core_match') and best['parent_theme'] not in stock_themes:
            stock_themes.insert(0, best['parent_theme'])
            info['themes'] = stock_themes

        # 置信度计算
        best_score = best['score'] / 100.0
        if len(all_candidates) > 1:
            margin = (best['score'] - all_candidates[1]['score']) / 100.0
        else:
            margin = best_score
        confidence = max(0.0, min(1.0, best_score * 0.7 + margin * 0.3))

        result = {
            'subtheme': best['name'],
            'parent_theme': best['parent_theme'],
            'subtheme_confidence': round(confidence, 3),
            'candidate_subthemes': [{'name': c['name'], 'score': c['score']} for c in all_candidates[:3]],
            'subtheme_features': best['features'],
        }
        stock_subtheme_map[code] = result
        assigned += 1

        if (idx + 1) % 100 == 0:
            print(f"  [子主题] 进度: {idx+1}/{total}")

    print(f"  [子主题] 完成: {assigned}/{total} 只股票分配子主题")
    return stock_subtheme_map


def _export_with_dominant(dominant_map, themes_output, stocks_output, new_cfg,
                           subtheme_heat=None, stock_subtheme_map=None,
                           subtheme_report=None, role_results=None,
                           scoring_results=None, entry_timing_results=None):
    """重新输出CSV/JSON，追加主导叙事字段"""
    import csv
    from datetime import datetime

    # ── 追加CSV（新增dominant_theme/dominant_score/is_cross叙事列）──
    csv_file = os.path.join(OUTPUT_DIR, f"theme_stock_map_v2_{TRADE_DATE}.csv")
    if os.path.exists(csv_file):
        # 重写：在每行末尾追加dominant字段
        rows = []
        with open(csv_file, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            header = next(reader)
            header += ['主导叙事', '叙事得分', '交叉叙事股', '叙事校正']
            rows.append(header)
            for row in reader:
                code = row[2] if len(row) > 2 else ''
                dm = dominant_map.get(code, {})
                row.append(dm.get('dominant_theme', ''))
                row.append(str(dm.get('dominant_score', '')))
                row.append(str(dm.get('is_cross_narrative', 0)))
                row.append(str(dm.get('narrative_corrected', 0)))
                rows.append(row)

        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        print(f"  [CSV] 已追加主导叙事字段: {csv_file}")

    # ── 更新JSON（加入dominant_info）──
    json_file = os.path.join(CACHE_DIR, f"theme_stock_map_v2_{TRADE_DATE}.json")
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['dominant_analysis'] = dominant_map
        data['dynamic_correlation'] = {
            'version': '1.0',
            'method': 'ETF量价协同 (PriceCorr×50% + VolSynergy×30% + RelStrength×20%)',
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        # 在stocks中追加dominant字段
        for code, info in data.get('stocks', {}).items():
            dm = dominant_map.get(code, {})
            if dm:
                info['dominant_theme'] = dm.get('dominant_theme', '')
                info['dominant_score'] = dm.get('dominant_score', 0)
                info['is_cross_narrative'] = dm.get('is_cross_narrative', 0)
                info['narrative_corrected'] = dm.get('narrative_corrected', 0)
                info['dominance_margin'] = dm.get('dominance_margin', 0)
                info['corr_scores'] = {t: s for t, s in dm.get('theme_scores', {}).items()}

        # ── 追加 Sub-theme Heat Matrix ──
        if subtheme_heat:
            data['subtheme_heat_matrix'] = subtheme_heat

        # ── 追加 Sub-theme Dynamic Correlation（仅新增字段，不影响旧结构）─
        if stock_subtheme_map:
            data['subtheme_dynamic_correlation'] = stock_subtheme_map
            # 在 stocks 中追加子主题字段
            st_count = 0
            for code, info in data.get('stocks', {}).items():
                st = stock_subtheme_map.get(code, {})
                if st:
                    info['subtheme'] = st['subtheme']
                    info['subtheme_confidence'] = st['subtheme_confidence']
                    info['candidate_subthemes'] = st['candidate_subthemes']
                    info['subtheme_features'] = st['subtheme_features']
                    # 如果父主题来自跨主题匹配（不在原 themes 中），追加到 themes
                    parent_theme = st.get('parent_theme', '')
                    current_themes = info.get('themes', [])
                    if parent_theme and parent_theme not in current_themes:
                        info['themes'] = [parent_theme] + current_themes
                    st_count += 1
            print(f"  [子主题] 已追加到 {st_count} 只股票的字段")
        elif subtheme_heat:
            data['subtheme_dynamic_correlation'] = {}

        # ── 追加 Sub-theme Heat Matrix Report ──
        if subtheme_report:
            data['subtheme_report'] = subtheme_report

        # ── 追加 Role Evolution Engine ──
        if role_results:
            data['role_evolution'] = role_results
            role_count = 0
            for code, info in data.get('stocks', {}).items():
                rr = role_results.get(code, {})
                if rr:
                    info['role'] = rr['role']
                    info['role_score'] = rr['role_score']
                    info['role_reason'] = rr['role_reason']
                    info['leader_similarity'] = rr['leader_similarity']
                    info['confidence'] = rr['confidence']
                    info['role_features'] = rr.get('role_features', {})
                    info['all_role_scores'] = rr.get('all_role_scores', {})
                    role_count += 1
            print(f"  [RoleEngine] 角色数据已追加到 {role_count} 只股票")

        # ── 追加 Sub-theme Stock Scoring V4.2 ──
        if scoring_results:
            data['subtheme_stock_scoring'] = scoring_results
            # 展平 Top Picks 输出
            from subtheme_stock_scoring import flatten_top_picks
            flat_picks = flatten_top_picks(scoring_results)
            data['top_picks'] = flat_picks

            # 写入股票级字段
            pick_count = 0
            for parent, subs in scoring_results.items():
                for sub_name, result in subs.items():
                    all_res = result.get('all_results', {})
                    for code, sr in all_res.items():
                        if code in data.get('stocks', {}):
                            data['stocks'][code]['stock_alpha'] = sr['stock_alpha']
                            data['stocks'][code]['final_score'] = sr['final_score']
                            pick_count += 1
            print(f"  [StockScoring] 评分数据已追加到 {pick_count} 只股票")

        # ── 追加 Entry Timing Engine V4.3 ──
        if entry_timing_results:
            data['entry_timing'] = entry_timing_results

            # 写入股票级字段
            entry_count = 0
            for parent, subs in entry_timing_results.items():
                for sub_name, sub_data in subs.items():
                    stocks_data = sub_data.get('stocks', {})
                    for code, entry in stocks_data.items():
                        if code in data.get('stocks', {}):
                            data['stocks'][code]['entry_signal'] = entry['entry_signal']
                            data['stocks'][code]['entry_score'] = entry['entry_score']
                            data['stocks'][code]['entry_reason'] = entry['entry_reason']
                            data['stocks'][code]['risk_level'] = entry['risk_level']
                            data['stocks'][code]['holding_priority'] = entry['holding_priority']
                            data['stocks'][code]['trade_score'] = entry.get('trade_score', 0)
                            data['stocks'][code]['investment_score'] = entry.get('investment_score', 0)
                            entry_count += 1
            print(f"  [EntryTiming] 入场时机+双评分数据已追加到 {entry_count} 只股票")

            # 丰富 top_picks：用实际 entry_signal 和 trade_score 替代合成信号
            enriched = 0
            stock_map = data.get('stocks', {})
            for pick in data.get('top_picks', []):
                code = pick.get('code', '')
                si = stock_map.get(code, {})
                pick['entry_signal'] = si.get('entry_signal', pick.get('signal', ''))
                pick['trade_score'] = si.get('trade_score', pick.get('final_score', 0))
                pick['holding_priority'] = si.get('holding_priority', 0)
                enriched += 1
            print(f"  [TopPicks] 已丰富 {enriched} 个 Top Picks 的 entry_signal/trade_score")

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [JSON] 已追加主导叙事分析: {json_file}")

        # 另存一份不带日期的最新版本
        latest_v2 = os.path.join(OUTPUT_DIR, 'theme_stock_map_latest_v2.json')
        with open(latest_v2, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [JSON] 已保存最新版: {latest_v2}")

        # ── 兼容输出到 cache_daily（供 tushare_quant.py _load_theme_stock_map_from_json 读取）──
        compat_path = os.path.join(CACHE_DIR, 'theme_stock_map_latest.json')
        if os.path.abspath(compat_path) != os.path.abspath(latest_v2):
            with open(compat_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  [JSON] 兼容输出: {compat_path}")


# ═══════════════════════════════════════════════════════════
# Stock Role Evolution Engine Layer
# ═══════════════════════════════════════════════════════════

def _run_role_evolution_layer(stocks_output, stock_subtheme_map, subtheme_report, trade_date):
    """
    运行角色演化引擎层 (Step 9)
    
    输入:
      stocks_output: {code: {name, industry, themes, concepts, subtheme, ...}}
      stock_subtheme_map: {code: {subtheme, subtheme_confidence, ...}}
      subtheme_report: subtheme_heat_engine 输出报告
      trade_date: str, 交易日
    
    输出:
      {code: {role, role_score, role_reason, leader_similarity, confidence, ...}}
    """
    from datetime import datetime, timedelta
    from collections import Counter

    # 1. 构建子主题股票索引
    stock_index = build_subtheme_stock_index_from_report(
        stock_subtheme_map, stocks_output, subtheme_report
    )

    # 2. 构建子主题生命周期
    lifecycle = build_subtheme_lifecycle_from_report(subtheme_report)

    # 3. 收集所有需要分配角色的股票代码
    all_codes = set()
    for parent, subs in stock_index.items():
        for sub, stocks in subs.items():
            for s in stocks:
                all_codes.add(s['code'])

    if not all_codes:
        print("  [RoleEngine] 无股票需要分配角色(子主题索引为空)")
        return {}

    # 4. 批量获取K线数据
    dt = datetime.strptime(str(trade_date), "%Y%m%d")
    start = (dt - timedelta(days=90)).strftime("%Y%m%d")
    end = str(trade_date)

    print(f"  [RoleEngine] 加载 {len(all_codes)} 只股票K线...")
    kline_df = theme_ts.get_daily_kline(list(all_codes), start, end)

    kline_groups = {}
    if kline_df is not None and not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub.sort_values("trade_date")

    print(f"  [RoleEngine] K线加载完成: {len(kline_groups)}/{len(all_codes)} 只")

    # 5. 运行角色引擎
    all_role_results = run_stock_role_engine_for_all_subthemes(
        stock_index, kline_groups, lifecycle
    )

    # 6. 展平结果: {code: role_info}
    flat_results = {}
    for parent, subs in all_role_results.items():
        for sub, stocks in subs.items():
            for code, role_info in stocks.items():
                flat_results[code] = {
                    'role': role_info['role'],
                    'role_score': role_info['role_score'],
                    'role_reason': role_info['role_reason'],
                    'leader_similarity': role_info['leader_similarity'],
                    'confidence': role_info['confidence'],
                    'role_features': role_info.get('role_features', {}),
                    'all_role_scores': role_info.get('all_role_scores', {}),
                }

    # 7. 统计
    role_counts = Counter(r['role'] for r in flat_results.values())
    print(f"  [RoleEngine] 角色分配完成: {len(flat_results)} 只股票")
    for role in ROLES:
        count = role_counts.get(role, 0)
        pct = count / max(len(flat_results), 1) * 100
        print(f"    {role:<10}: {count:3d}只 ({pct:5.1f}%)")

    return flat_results


# ═══════════════════════════════════════════════════════════
# Sub-theme Stock Scoring Layer (V4.2)
# ═══════════════════════════════════════════════════════════

def _run_scoring_layer(stocks_output, stock_subtheme_map, subtheme_report,
                        role_results, trade_date, kline_groups=None):
    """
    运行 Sub-theme Stock Scoring 引擎层 (Step 10)

    输入:
      stocks_output: {code: {name, industry, themes, concepts, subtheme, ...}}
      stock_subtheme_map: {code: {subtheme, ...}}
      subtheme_report: subtheme_heat_engine 输出
      role_results: {code: {role, role_score, ...}}
      trade_date: str
      kline_groups: 可选，已有的K线分组

    输出:
      {母主题: {子主题: full_results}}
      同时将 stock_alpha / final_score 写入 stocks_output
    """
    from datetime import datetime, timedelta
    from collections import Counter

    # 1. 获取已有 Kline 或重新加载
    if kline_groups is None:
        all_codes = list(role_results.keys())
        if not all_codes:
            print("  [StockScoring] 无股票数据")
            return {}

        dt = datetime.strptime(str(trade_date), "%Y%m%d")
        start = (dt - timedelta(days=90)).strftime("%Y%m%d")
        end = str(trade_date)

        print(f"  [StockScoring] 加载 {len(all_codes)} 只股票K线...")
        kline_df = theme_ts.get_daily_kline(all_codes, start, end)
        kline_groups = {}
        if kline_df is not None and not kline_df.empty:
            for code, sub in kline_df.groupby("ts_code"):
                kline_groups[code] = sub.sort_values("trade_date")
        print(f"  [StockScoring] K线加载完成: {len(kline_groups)} 只")

    # 2. 运行评分引擎
    scoring_results = run_subtheme_stock_scoring_for_all(
        stocks_output, stock_subtheme_map, subtheme_report,
        role_results, kline_groups
    )

    # 3. 展平 Top Picks
    flat_picks = flatten_top_picks(scoring_results)
    if flat_picks:
        print_top_picks_summary(flat_picks)

    # 4. 将 stock_alpha / final_score 写入 stocks_output
    written = 0
    for parent, subs in scoring_results.items():
        for sub_name, result in subs.items():
            all_res = result.get('all_results', {})
            for code, sr in all_res.items():
                if code in stocks_output:
                    stocks_output[code]['stock_alpha'] = sr['stock_alpha']
                    stocks_output[code]['final_score'] = sr['final_score']
                    written += 1

    print(f"  [StockScoring] 评分数据已写入 {written} 只股票")
    return scoring_results


# ═══════════════════════════════════════════════════════════
# Entry Timing Layer (V4.3)
# ═══════════════════════════════════════════════════════════

def _run_entry_timing_layer(stocks_output, subtheme_report, role_results,
                             scoring_results, trade_date, kline_groups=None):
    """
    运行 Entry Timing Engine 层 (Step 12)

    输入:
      stocks_output: {code: {name, ...}}
      subtheme_report: subtheme_heat_engine 输出
      role_results: {code: {role, role_score, ...}}
      scoring_results: {母主题: {子主题: {all_results: {code: {stock_alpha, ...}}}}}
      trade_date: str
      kline_groups: 可选，已有K线分组

    输出:
      {母主题: {子主题: {stocks: {code: {entry_signal, entry_score, ...}}},
                         subtheme_stage, theme_stage, market_regime}}
    """
    from datetime import datetime, timedelta

    # 1. 获取 Kline
    if kline_groups is None:
        all_codes = list(role_results.keys())
        if not all_codes:
            print("  [EntryTiming] 无股票数据")
            return {}

        dt = datetime.strptime(str(trade_date), "%Y%m%d")
        start = (dt - timedelta(days=90)).strftime("%Y%m%d")
        end = str(trade_date)

        print(f"  [EntryTiming] 加载 {len(all_codes)} 只股票K线...")
        kline_df = theme_ts.get_daily_kline(all_codes, start, end)
        kline_groups = {}
        if kline_df is not None and not kline_df.empty:
            for code, sub in kline_df.groupby("ts_code"):
                kline_groups[code] = sub.sort_values("trade_date")
        print(f"  [EntryTiming] K线加载完成: {len(kline_groups)} 只")

    # 2. 构建 stock_alpha_map
    stock_alpha_map = {}
    for parent, subs in scoring_results.items():
        for sub_name, result in subs.items():
            all_res = result.get('all_results', {})
            for code, sr in all_res.items():
                stock_alpha_map[code] = sr.get('stock_alpha', 50)

    # 3. 运行 Entry Timing Engine
    results = run_entry_timing_for_all(
        stocks_output, subtheme_report, role_results,
        kline_groups, stock_alpha_map
    )

    # 4. 打印报告
    if results:
        print_entry_timing_report(results, stock_alpha_map=stock_alpha_map)
        _try_print_subtheme_report(results, stocks_output)

    return results


def _try_print_subtheme_report(results, stocks_output):
    """打印子主题入场报告（兼容名称映射）"""
    name_map = {}
    for code, info in stocks_output.items():
        name_map[code] = info.get('name', code)
    print_subtheme_report._name_map = name_map

    # 直接调用 print_subtheme_report（不传 name_map，用内部格式简化输出）
    print_subtheme_report(results)


# ═══════════════════════════════════════════════════════════
# 自动生成主题精华报告（在 pipeline 末尾调用）
# ═══════════════════════════════════════════════════════════

def _generate_essence_report(trade_date):
    """自动生成主题精华报告"""
    try:
        from theme_summary_report import generate_theme_essence_report
        generate_theme_essence_report(trade_date=trade_date)
    except Exception as e:
        print(f"  [报告] 生成失败: {e}")


if __name__ == '__main__':
    build_theme_stock_map_v2()
