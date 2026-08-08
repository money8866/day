#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题-个股成份股映射生成器 V2
基于 theme_kg_v3/theme_config.json 配置，
只负责生成主题-个股对应关系（成份股映射），不做主题分析，
输出 CSV + JSON 格式（JSON 写入 cache_daily/theme_stock_map_v2_*.json）。

用法：
    python build_theme_stock_map_v2.py
"""

import sys
import os
import json
import csv
from datetime import datetime

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


def _cleanup_old_theme_maps(cache_dir, keep_days=5):
    """清理历史 theme_stock_map 版本（保留 latest + 最近 keep_days 天）"""
    import glob
    import time
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in glob.glob(os.path.join(cache_dir, 'theme_stock_map_v*_*.json')):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
                removed += 1
        except Exception:
            pass
    if removed > 0:
        print(f"  [清理] 删除 {removed} 个过期 theme_stock_map 历史版本")


from tushare_quant import pro, TRADE_DATE
import theme_trend_sentiment_score as theme_ts

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
            "mainbiz_exclude": cfg.get("mainbiz_exclude", []),
            "dna_concept_required": cfg.get("dna_concept_required", []),
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
    '地产链': ['地产', '房地产', '住宅', '商品房', '物业', '园区开发', '商业地产', '保障房', '城市更新', '房产服务'],
    '节能环保': ['环保', '环境治理', '污水处理', '水务', '固废', '垃圾焚烧', '危废', '大气治理', '水处理', '节能减排', '供气供热'],
    '钢铁': ['钢铁', '钢材', '普钢', '特钢', '特种钢', '板材', '线材', '型钢', '钢管', '钢加工', '金属制品', '冶炼'],
    '传媒': ['传媒', '广告', '影视', '电影', '电视剧', '出版', '图书', '报刊', '广播电视', '视频', '短剧', '文化传媒'],
    '建筑装饰': ['建筑', '基建', '基础建设', '工程', '装修', '装饰', '建筑施工', '房建', '公路', '桥梁', '隧道', '园林'],
}

# ST过滤
ST_FILTER_ENABLED = True

# 主题-行业白名单
THEME_INDUSTRY_WHITELIST = {
    '银行': ['银行'],
    '证券': ['证券', '资本市场服务'],
    '券商': ['证券', '资本市场服务'],
    '煤炭': ['煤炭'],
    # 黄金矿企的 stock_basic 行业标签是"黄金"（有色金属的子类），
    # 仅白名单"有色金属"会把赤峰黄金/恒邦股份等真黄金股全部误杀
    '黄金': ['有色金属', '黄金'],
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

# 人工补漏映射：match_theme_stocks 未能覆盖的明确成份股（强制纳入对应主题）
# 格式：主题名: [ts_code, ...]（ts_code 带交易所后缀）
THEME_STOCK_OVERRIDES = {
    '新能源车': ['000009.SZ'],   # 中国宝安（贝特瑞 锂电负极材料龙头）
    '工业金属': ['601168.SH'],   # 西部矿业（铜铅锌多金属矿）
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
        'manual_override': 10,  # 人工补漏映射最高优先级，互斥过滤时优先保留
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

            # B股过滤（900xxx.SH / 200xxx.SZ / 201xxx.SZ / 202xxx.SZ）及名称未解析代码
            if code.startswith(('900', '200', '201', '202')) or stock_name == code:
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

            # 行业互斥（强制纳入公司豁免：leader/core 是人工核验的名单，
            # 不应被行业标签误杀——如紫金矿业 stock_basic 行业为"铜"，
            # 却被黄金主题的"铜"互斥规则挡在门外）
            if theme_name in THEME_INDUSTRY_EXCLUDE:
                excluded = THEME_INDUSTRY_EXCLUDE[theme_name]
                if via not in ('leader_company', 'core_company') and any(ex in industry for ex in excluded):
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

    # 4a.5 人工补漏映射：match_theme_stocks 未能覆盖的明确成份股强制纳入对应主题
    if THEME_STOCK_OVERRIDES:
        _override_name_ind = {}
        for _, _r in stock_basic_df.iterrows():
            _override_name_ind[_r['ts_code']] = (_r.get('name', ''), _r.get('industry', ''))
        for theme_name, codes in THEME_STOCK_OVERRIDES.items():
            if theme_name not in themes_output_raw:
                themes_output_raw[theme_name] = []
            for code in codes:
                if any(s['code'] == code for s in themes_output_raw[theme_name]):
                    continue
                nm, ind = _override_name_ind.get(code, (code, ''))
                themes_output_raw[theme_name].append({
                    "code": code,
                    "name": nm,
                    "via": "manual_override",
                    "chain_distance": 0,
                    "score": 90,
                    "irs_score": 90,
                    "irs_layer": "core",
                    "industry": ind,
                    "concepts": stock_concepts.get(code, []),
                })
                total_refs_raw += 1
                print(f"  [补漏] {theme_name} + {code} {nm} (manual_override)")

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
    # 优先从 themes_output_raw 取 meta（覆盖 manual_override 等人工补漏条目，
    # 其不在此处 raw match 结果 theme_stock_map 中，score 为 0 会被 300 上限截断）
    _raw_meta_index = {}
    for _tname, _slist in themes_output_raw.items():
        for _s in _slist:
            _raw_meta_index.setdefault(_tname, {})[_s['code']] = _s
    themes_output = {}
    for code, info in stocks_output.items():
        for theme_name in info["themes"]:
            if theme_name not in themes_output:
                themes_output[theme_name] = []
            meta = _raw_meta_index.get(theme_name, {}).get(code) or theme_stock_map[theme_name].get(code, {})
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

    # 同步写 latest.json（filter_by_top_themes 等固定文件名读取方，保证补漏映射即时生效）
    latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)

    # 清理历史 theme_stock_map 版本（保留最近 5 天 + latest）
    _cleanup_old_theme_maps(CACHE_DIR, keep_days=5)

    total_refs = sum(len(v) for v in themes_output.values())
    print(f"\n{'='*60}")
    print(f"完成基础映射！")
    print(f"  CSV: {csv_file}")
    print(f"  JSON: {json_file}")
    print(f"  LATEST: {latest_file}")
    print(f"  主题数: {len(themes_output)}")
    print(f"  个股数: {len(stocks_output)}")
    print(f"  映射数: {total_refs}")
    print(f"{'='*60}")


    return themes_output


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


if __name__ == '__main__':
    build_theme_stock_map_v2()
