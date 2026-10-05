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

        # V3.1 降级主题（rank_participation=false，如 量子计算 SUBTHEME）：
        # 不再作为一级主题生成映射键，其真成员由 4a.55 改挂到母主题。
        if cfg.get("rank_participation") is False:
            convert_log.append(f"[降级] {cn_name} 不参与一级主题映射（成员改挂，见 4a.55）")
            continue

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
    # 20260928 AI算力「拆旧建新」：原 AI算力 拆为 CPO / 液冷 / AI服务器 / AI应用 四个独立一级主题
    'CPO': ['光模块', 'CPO', '共封装光学', '光电共封装', '硅光', '硅光子', '光芯片', '光器件', '光收发', '光引擎', '高速光互联', '光互联', '光电子器件'],
    '液冷': ['液冷', '冷板', '浸没式', '浸没相变', 'CDU', '冷量分配', '液冷板', '液冷管路', '快接头', '数据中心温控', '机房精密空调', '精密温控', '数据中心散热', '冷却系统', '温控设备'],
    'AI服务器': ['AI服务器', 'GPU服务器', '训练服务器', '推理服务器', '服务器', '高性能计算机', '服务器整机', '整机柜', '超算', '服务器ODM', '服务器OEM', '智算一体机', '算力整机'],
    'AI应用': ['人工智能', 'AI应用', 'AI软件', 'AI Agent', '智能体', '大模型', 'AIGC', 'AI办公', 'AI医疗', 'AI教育', 'AI营销', 'AI客服', '机器视觉', '计算机视觉', '语音识别', '自然语言处理', '模式识别', '智能交互', '办公软件', '医疗信息化', '教育信息化', '金融资讯'],
    # 20260928 新增第 5 个一级主题：算力运营（自持算力资源 + 对客提供算力服务）
    '算力运营': ['算力租赁', 'GPU算力租赁', '智能算力租赁', '算力出租', '智算中心', '智能计算中心', '智算集群', '算力集群', 'AI算力中心', 'GPU云', 'AI云', '算力云', '云算力', '智算云', '算力服务', '算力运营', '算力调度', '算力网络', '算力交易', '算力平台', '算力资源服务', '超算云'],
    '半导体': ['芯片', '半导体', '集成电路', '晶圆', '代工', '封测', '封装', '测试', '设备', '材料'],
    '机器人': ['机器人', '减速器', '丝杠', '电机', '传感器', '执行器', '关节', '驱动器', '控制器', '伺服', '精密减速', '滚珠丝杠', '行星滚柱', '空心杯电机', '无框电机', '力矩电机', '灵巧手', '线性执行器', '旋转执行器', '运动控制', '机电'],
    '创新药': ['创新药', '新药', '原研', '生物药', 'ADC', 'CXO', 'CRO', 'CDMO', '抗体', '双抗', '细胞治疗', '基因', '疫苗', '重组蛋白'],
    '医疗器械': ['医疗器械', '医疗设备', '体外诊断', 'IVD', '高值耗材', '医用耗材', '监护', '呼吸机', '超声', '内窥镜', '医学影像', 'CT', 'MRI', '手术机器人', '介入', '支架', '人工关节', '血糖仪', '化学发光', 'POCT', '诊断试剂', '影像设备', '医用', '器械'],
    '消费电子': ['手机', '消费电子', '终端', '屏幕', '显示屏', '摄像头', '连接器', '耳机', '音箱', '可穿戴', '声学', '电声', '视窗', '玻璃', '精密结构件', '模具', '机壳', '射频前端', 'LCD', 'OLED', '显示模组', '触摸屏', '液晶', '光学镜头', '触控显示'],
    # 20260928 主题体系 V3.1：新能源车 + 智能驾驶 合并为「汽车」，词表取并集
    '汽车': ['汽车', '智能驾驶', '汽车电子', '车载', '自动驾驶', '车联网', 'ADAS', '座舱', '导航',
             '智能座舱', '域控制', '雷达', '汽车配件', '汽车零部件', '线控', '新能源', '电池',
             '电机', '电控', '充电', '整车', '电动', '动力电池', '驱动电机', '电驱', '电控系统',
             'BMS', '热管理'],
    '军工': ['航空', '军用', '军品', '导弹', '战车', '战机', '武器', '舰船', '坦克', '雷达', '军事', '国防', '弹药', '战斗机', '发动机', '军用飞机', '兵器', '军械', '舰艇', '卫星', '仿真测试', '军工电子'],
    # 20260928 主题体系 V3.1：PCB 独立一级主题
    'PCB': ['印制电路板', '印制线路板', '印刷电路板', '线路板', '电路板', '电路样板', '覆铜板',
            '粘结片', '半固化片', 'HDI', '高密度互连', '高多层', '柔性板', '挠性板', '软板',
            '刚性板', 'FPC', '电子装联', 'PCB设备', 'PCB化学品'],
    '黄金': ['金', '黄金', '金矿', '贵金属', '黄金开采', '黄金冶炼'],
    '银行': ['银行', '商业银行', '金融'],
    '证券': ['证券', '券商', '证券公司', '投行', '资本市场'],
    '煤炭': ['煤', '煤炭', '焦煤', '焦炭', '动力煤'],
    # 20260929 电力拆分（用户裁定）：原「电力」按「运营 / 设备」拆为两个一级主题，
    #   电力运营 = 发电运营 + 电网输配售电 + 电力交易/综合能源；电力设备 = 输变电一次设备、
    #   电线电缆、电网二次设备/自动化、电工仪器仪表、电力电子、发电设备（风电整机/光伏设备）。
    #   两者互斥，见 THEME_MUTEX_PAIRS。
    '电力运营': ['电力', '发电', '电源', '火电', '水电', '核电', '风电', '光伏', '电站', '电网',
                 '输电', '变电', '配电', '电力生产', '热力', '新能源发电', '核能', '风力', '太阳能',
                 '储能', '电网运营', '供电', '售电', '绿电', '虚拟电厂'],
    '电力设备': ['电力设备', '输变电', '变压器', '开关柜', '断路器', '成套设备', '配电设备',
                 '电线电缆', '电缆', '线缆', '海缆', '智能电表', '电能表', '电工仪器仪表',
                 '电力电子', '换流阀', '逆变器', '继电保护', '电网自动化', '变电站', '特高压',
                 '智能电网', '配电网', '绝缘子', '避雷器', '风电整机', '风电零部件', '光伏设备',
                 '光伏组件', '发电设备'],
    '信创': ['信创', '国产替代', '自主可控', '信息安全', '国产CPU', '国产GPU', '操作系统', '数据库', '办公软件'],
    '低空经济': ['低空', '无人机', '飞行器', '航空', '直升', 'eVTOL', '飞行汽车', '通航', '通用航空', '航空器', '飞行控制', '航空电子', '导航', '空管'],
    '白酒': ['白酒', '酒', '酿酒', '食品饮料', '酒精饮料'],
    '消费': ['消费', '食品饮料', '家用电器', '零售', '社会服务', '品牌消费'],
    '游戏': ['游戏', '手游', '网游', '游戏研发', '游戏发行', '电竞', '动漫'],
    '数据要素': ['数据', '大数据', '数据要素', '数据资产', '数据交易', '数据安全', '数据治理', '隐私计算'],
    '高端材料': ['化工', '化学', '氟', '制冷剂', '染料', '聚氨酯', 'MDI', '维生素', '工程塑料', '精细化工', '新材料', '特种化学品'],
    '商业航天': ['卫星', '航天', '宇航', '运载火箭', '火箭', '太空', '航天器', '卫星通信', '卫星导航', '卫星应用'],
    '可控核聚变': ['聚变', '超导', '托卡马克', '第一壁', '偏滤器', '核聚变', '人造太阳', '超导磁体'],
    '脑机接口': ['脑机', '神经', '脑科', '神经康复', '疼痛', '康复'],
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
    # 20260928 AI算力拆旧建新：原 AI算力 的粗行业互斥沿用至四个新主题
    'CPO': ['煤炭开采', '造纸', '钢加工', '化学原料'],
    '液冷': ['煤炭开采', '造纸', '钢加工', '化学原料'],
    'AI服务器': ['煤炭开采', '造纸', '钢加工', '化学原料'],
    'AI应用': ['煤炭开采', '造纸', '钢加工', '化学原料'],
    '算力运营': ['煤炭开采', '造纸', '钢加工', '化学原料'],
    '黄金': ['铜', '铅锌', '钢铁'],
    '煤炭': ['化学制品', '化学原料', '化工原料', '化工', '塑料'],
    '军工': ['软件服务', 'IT设备', '互联网', '出版业', '影视音像', '广告包装', '房地产', '银行', '保险'],
    '游戏': ['基建', '勘察', '交通工程', '建筑设计', '化工', '煤炭'],
    '汽车': ['钢加工'],
    '工业金属': ['钢加工'],
    '战略与小金属': ['钢加工'],
}

# 主题-股票黑名单
THEME_STOCK_BLACKLIST = {
    '消费电子': {'禾盛新材', '慧谷新材', '中瑞股份'},
    '创新药': {'利民股份', '富邦科技'},
    '化工': {'山西焦化', '开滦股份', '云煤能源', '兖矿能源', '辉隆股份', '国投丰乐'},
    '建筑装饰': {'博深股份', '金鹰重工', '铁科轨道'},
    # 20260929 电力拆分：原「电力」黑名单归入电力运营；另补 2 只「行业=新型电力但主营为
    #   设备」的误入（林洋能源=智能电表/用电信息采集，九洲集团=成套设备/电力电子）→ 明确
    #   判为电力运营的排除项，交由电力设备承接（否则会靠「环节行业(新型电力)+宽口径词」
    #   双弱证据留在运营侧）。
    '电力运营': {'先导智能', '华阳股份', '理工光科', '华宝新能', '鑫宏业', '国网英大',
                 '林洋能源', '九洲集团'},
    '低空经济': {'永悦科技'},
    # 20260930 §三十三 人工裁定：同花顺(300033.SZ) 主营为金融信息服务，信创业务
    #   证据不足（原凭 core_stocks 人工名单纳入）→ 从信创成员池移出，保留在 AI应用。
    '信创': {'国脉科技', '同花顺'},
    '半导体': {'拉普拉斯'},
    '机器人': {'三瑞智能'},
    # 20260928 脑机接口收权：国际医学(综合医院+百货零售)/盈康生命(肿瘤医院) 仅因
    #   「医药生物」东财行业板整包灌入，主营文本过短(<12字)触发 X3 保留豁免而未被
    #   环节门槛剔除，实为泛医疗、无任何脑机接口业务证据 → 明确排除。
    '脑机接口': {'国际医学', '盈康生命'},
    # 20260928 AI算力拆旧建新：四新主题（CPO/液冷/AI服务器）同样存在"东财板块整包 +
    #   主营文本过短触发 X3 豁免"的漏网，均经用户裁定应判 UNCERTAIN/EXCLUDE，不得
    #   留在四主题成员池内（判定依据见 81 只原 AI算力成员逐只重审表）。
    'CPO': {'北纬科技', '科瑞思', '京东方A', '会畅科技'},
    '液冷': {'亨通光电'},
    'AI服务器': {'润泽科技', '杰创智能', '中兴通讯', '联迪信息'},
    # 20260928 算力运营：禁止「设备/零部件供应商」误入（供服务器/液冷/电源/机柜
    #   ≠ 自持算力运营）。下列公司主营分别为 UPS 电源、液冷设备、机柜微模块、
    #   IT 设备销售运维、精密锻件、PCB、酒类，均无算力服务收入证据。
    '算力运营': {'科华数据', '曙光数创', '朗威股份', '亚康股份', '恒润股份', '广合科技', '群兴玩具'},
}

# 人工补漏映射：match_theme_stocks 未能覆盖的明确成份股（强制纳入对应主题）
# 格式：主题名: [ts_code, ...]（ts_code 带交易所后缀）
THEME_STOCK_OVERRIDES = {
    '汽车': ['000009.SZ'],       # 中国宝安（贝特瑞 锂电负极材料龙头）
    '工业金属': ['601168.SH'],   # 西部矿业（铜铅锌多金属矿）
    # 20260929 电力拆分补漏：原「电力」成员中，被拆分后的候选池错分到「电力运营」池、
    #   再按运营口径判成「证据不足」而掉出的设备股。这些股票用电力设备口径 classify
    #   本可获 CORE/CHAIN（主营命中环节关键词或环节行业归属），掉出纯属池归属错位，
    #   非证据不足 → 人工补漏回电力设备。逐只判定见 _tmp_diag_cls19.py 输出。
    '电力设备': [
        '601615.SH',   # 明阳智能：风电机组制造（发电设备环节强命中）
        '600475.SH',   # 华光环能：电站锅炉/工业锅炉（发电设备环节强命中）
        '002364.SZ',   # 中恒电气：高频开关电源（电力电子环节强命中）
        '300693.SZ',   # 盛弘股份：电力电子设备（电力电子环节强命中）
        '301082.SZ',   # 久盛电气：防火特种电缆/电力电缆（电线电缆环节强命中）
        '300365.SZ',   # 恒华科技：智能电网信息化（电网二次设备/自动化强命中）
        '300513.SZ',   # 恒实科技：电网信息化/智能电网监控（同上）
        '300499.SZ',   # 高澜股份：大功率电力电子装置冷却设备（电力电子强命中）
        '002130.SZ',   # 沃尔核材：热缩冷缩电缆附件材料（环节行业归属）
        '003008.SZ',   # 开普检测：电力系统二次设备检测（环节行业归属）
    ],
    '电力运营': [
        '000027.SZ',   # 深圳能源：常规能源与新能源开发/生产（火电运营，主营文本为样板
                       #   经营范围致运营证据不足；干设备口径会因「成套设备」误判电力设备）
    ],
    # 20261003 人工裁定：以下两只主营确属人工智能应用侧，但均未被任何主题通道匹配到
    #   （全市场独立扫描的双底池里只能落到行业兜底 元器件/IT设备），用户明确归属：
    #   美格智能 = 人工智能 / 端侧AI；鸿合科技 = 人工智能 / AI应用。
    #   现有主题体系中「人工智能」只是 AI应用 的关键词、无独立主题键，故统一并入 AI应用。
    'AI应用': [
        '002881.SZ',   # 美格智能：无线通信模组、高算力智能模组（端侧AI）
        '002955.SZ',   # 鸿合科技：教育信息化/智能交互显示（AI教育，行业AI应用）
    ],
}

# V3.1 降级主题成员改挂表（20260929）
# theme_config.json 中声明 tier=SUBTHEME / rank_participation=false 的主题不再作为
# 一级主题（=可独立炒作的资金交易单元）参与映射与热度排名；其真成员按 V3.1
# 「每只股票唯一 PRIMARY_THEME」改挂到母主题，否则会随主题键一起从映射中消失。
# 依据：subtheme_map_v3_1.json 的 stocks[*].primary（V3.1 裁决）。
#   量子计算（N=5，无独立龙头群）：国盾量子/科大国创/神州信息 → 信创；
#   光迅科技/光库科技 已是 CPO 成员，无需改挂。
# 仅当该主题确实处于降级状态（rank_participation=false）时才生效。
THEME_DEMOTED_REHOME = {
    '量子计算': {'信创': ['688027.SH', '300520.SZ', '000555.SZ']},
}

# 主题主营业务必要词过滤：成份股主营文本必须包含列表中至少一个词，
# 否则视为"行业溢出/概念碰瓷"剔除。
# 背景：量子计算等主题的 sw_industry_match 为一级行业（计算机/通信/电子），
# 粒度过粗——楚天龙(智能卡)凭"通信设备"行业通过 Industry Gate 混入量子计算；
# 东财宽泛概念板块（如"量子科技"含抗量子密码卡片厂商）进一步放大误入。
# 豁免（人工核验名单，不做主营检查）：
#   - leader/core 公司（match 阶段已 force include）
#   - 代码是主题精确概念板块成员（东财概念名与配置 concept 完全一致的直接证据）
#   - manual_override 补漏股票
THEME_MAINBIZ_REQUIRED = {
    '量子计算': ['量子'],
}

# 成分股污染过滤（源头治理，20260924 定版）
# 背景：sw_industry_match 常写一级行业（AI算力=电子/通信/计算机），
# dc_industry_board 因此把整个行业板块灌进主题，制造「行业代替主题」的假广度
# （20260924 全库 POLLUTION 1175/4481 = 26.2%）。
# 只剔除 POLLUTION 类成员，保留 WEAK（无主营文本的数据缺口 / 行业属主题特征行业），
# 判定复用 theme_heat_v24_hc.hc_classify，与热度层、HC 层保持同一口径。
#
# 但 HC 的「特征行业」只由 CORE 成员行业分布（≥15%）定义，异质主题天然覆盖不全，
# 会把真成员误判为污染（军工 CORE 集中航空/电子 → 运输设备/专用机械/元器件 全落网；
# 消费 CORE 分散 → 白酒/食品/软饮料 进不了门槛），直接删会造成军工-68%、消费-74%。
# 故删除前做「主题自有证据」豁免（见 4a.6），其中行业类豁免的判据是：
#   成员所属的申万行业板 ∈ 主题声明的 sw/cx_industry_match，且该板体量 ≤ BOARD_NARROW_MAX
# ——「板≈主题」（食品饮料130/国防军工141/轻工制造170/半导体187）时行业灌入合法；
# 「板⊃主题」（电子522/机械设备629/医药生物512/基础化工457/汽车334）时仍需主营证据。
POLLUTION_FILTER_ENABLED = True
BOARD_NARROW_MAX = 250          # 申万行业板体量上限：≤ 视为「板≈主题」
# 人工复核白名单：确认属于该主题、但被规则误判 POLLUTION 的成员（主题名 → {code}）
POLLUTION_KEEP = {}
# 试运行：只出污染审计清单 + 剔除统计，不写任何映射文件（python build_theme_stock_map_v2.py --dry-run）
POLLUTION_DRY_RUN = '--dry-run' in sys.argv

# 环节清单门槛（主题定义重构，20260927）
# theme_config.json 中带 "segment_gate": true 的主题（消费/AI算力/智能驾驶/军工/信创/电力）
# 改为「环节清单」定义：成员主营文本必须命中该主题 "segments" 里至少一个环节关键词。
# 背景：sw_industry_match 常写一级行业（AI算力=电子/通信/计算机、消费=7 个行业、
# 智能驾驶=汽车/电子、军工=国防军工/机械设备/电子、信创=计算机/电子/软件服务/IT设备、
# 电力=运营+设备），dc_industry_board 因此把整个行业板块灌进主题（HC V1.0 实测：
# dc_industry_board 占 85.0% 成员对、贡献 97.3% 的 BAD）。环节清单即「先定允许纳入的
# 环节，再按主营逐只判定」，取代「行业口径」。
SEGMENT_GATE_ENABLED = True

# 硬排除否决（20260927）：对所有主题（含未启用环节门槛的宽主题）执行一步否决 ——
# 审计规则引擎判定 EXCLUDE 且 evidence_type='主营'（即命中 theme_config.json 的
# exclude_keywords，配置写明的明确反证）者，从映射中剔除。
# 背景：26 个未启用门槛的主题 segments 为空，不受 4a.47 约束，实测有 47 条硬排除残留
#   仍在生产映射（创新药·原料药 30、节能环保 4、高端材料 3、医疗器械 3、地产链 2…）。
# 边界：只做「否决」，不新增/升级任何成员，不影响正向纳入口径；对已开门槛的 6 个
#   优先主题零影响（其 EXCLUDE 残留本为 0）。
HARD_EXCLUDE_VETO_ENABLED = True

# 主营文本「不可用」启发式（20260927 精修）：经营范围样板 / 过短 / 缺失
# 这类文本即使命中不了环节关键词，也属数据缺口而非业务不符，按 HC V1.0 §十三保留待补录。
SEG_TEXT_BOILERPLATE = (
    '投资兴办实业', '以上同类商品', '凭许可证', '依法须经批准', '法律法规',
    '国家有专项规定', '佣金代理', '自有房屋租赁', '企业管理咨询',
)


def seg_text_unusable(txt):
    t = (txt or '').strip()
    if not t:
        return 'EMPTY'
    if len(t) < 12:
        return 'TOOSHORT'
    if any(b in t for b in SEG_TEXT_BOILERPLATE):
        return 'BOILERPLATE'
    return ''

# 主题互斥对
THEME_MUTEX_PAIRS = [
    # 20260928 AI算力拆旧建新：四个新一级主题之间互斥，
    #   禁止同一股票复制进入多个新主题（避免重复计数）。
    ('CPO', '液冷'),
    ('CPO', 'AI服务器'),
    ('CPO', 'AI应用'),
    ('液冷', 'AI服务器'),
    ('液冷', 'AI应用'),
    ('AI服务器', 'AI应用'),
    # 20260928 新增「算力运营」与其余四个新主题两两互斥（设备制造 ≠ 算力运营）
    ('CPO', '算力运营'),
    ('液冷', '算力运营'),
    ('AI服务器', '算力运营'),
    ('AI应用', '算力运营'),
    # 光芯片与半导体口径重叠，避免同一标的在两个主题重复计数
    ('半导体', 'CPO'),
    ('机器人', '汽车'),
    ('军工', '低空经济'),
    ('军工', '商业航天'),
    ('数据要素', '信创'),
    # 20260929 电力拆分：运营 / 设备互斥，禁止同一股票在两主题重复计数
    ('电力运营', '电力设备'),
    ('可控核聚变', '电力运营'),
    ('可控核聚变', '电力设备'),
    ('可控核聚变', '汽车'),
    ('高端材料', '煤炭'),
    ('脑机接口', '创新药'),
    ('医疗器械', '创新药'),
    ('医疗器械', '机器人'),
    ('量子计算', '信创'),
    ('量子计算', '半导体'),
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

    # 精确概念板块成员索引（概念名 -> {code,...}）：主营必要词过滤的豁免依据
    # 行业板成员索引（申万层级板名 -> {code,...}）：污染过滤的「板≈主题」豁免依据
    dc_concept_members_exact = {}
    dc_industry_members_exact = {}
    if dc_df is not None and not dc_df.empty:
        for _, _r in dc_df.iterrows():
            try:
                _b = str(_r['concept_name']).strip()
                if not _b:
                    continue
                if bool(_r.get('is_industry', False)):
                    dc_industry_members_exact.setdefault(_b, set()).add(_r['con_code'])
                else:
                    dc_concept_members_exact.setdefault(_b, set()).add(_r['con_code'])
            except Exception:
                continue

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
        if cn_name not in old_format_themes:      # V3.1 降级主题已跳过，不打对照
            continue
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

    # 4a.45 主营业务必要词过滤（成份股纯度治理）
    # 行业Gate基于一级行业时粒度过粗，会造成行业溢出（如智能卡厂商凭
    # "通信设备"混入量子计算）。此处要求主营文本含必要词，否则剔除。
    if THEME_MAINBIZ_REQUIRED:
        _force_via = ('leader_company', 'core_company', 'manual_override')
        for _theme, _req_words in THEME_MAINBIZ_REQUIRED.items():
            if _theme not in themes_output_raw:
                continue
            _cfg = old_format_themes.get(_theme, {})
            _exempt_names = set(_cfg.get('core_companies', [])) | set(_cfg.get('leader_companies', []))
            _concept_boards = [b.strip() for b in _cfg.get('concept', [])]
            _kept, _dropped = [], []
            for s in themes_output_raw[_theme]:
                code, name = s['code'], s['name']
                via = s.get('via', '')
                # 豁免1：人工核验名单
                if via in _force_via or name in _exempt_names:
                    _kept.append(s)
                    continue
                # 豁免2：主题精确概念板块直接成员（如"量子通信"板块成员，
                # 区别于宽泛的"量子科技"板块）
                if any(code in dc_concept_members_exact.get(b, set())
                       for b in _concept_boards):
                    _kept.append(s)
                    continue
                mb_text = stock_mainbiz.get(code, '')
                if mb_text and any(w in mb_text for w in _req_words):
                    _kept.append(s)
                    continue
                _dropped.append(f"{name}({via}, 主营无[{','.join(_req_words)}])")
            if _dropped:
                print(f"  [主营必要词] {_theme}: 剔除 {len(_dropped)} 只 -> {'; '.join(_dropped[:8])}{'...' if len(_dropped) > 8 else ''}")
            themes_output_raw[_theme] = _kept

    # 4a.46 门槛前成员池快照（仅 --dry-run，供 Mapping V3 审计核对「原N」用）
    # 不写任何生产映射文件；正式运行完全不受影响。
    if POLLUTION_DRY_RUN:
        _pre_path = os.path.join(OUTPUT_DIR, f'theme_pregate_pool_{TRADE_DATE}.csv')
        with open(_pre_path, 'w', encoding='utf-8-sig', newline='') as f:
            _w = csv.writer(f)
            _w.writerow(['theme', 'code', 'name', 'via', 'industry', 'irs_score', 'mainbiz'])
            _n_pre = 0
            for _t, _sl in themes_output_raw.items():
                for _s in _sl:
                    _w.writerow([_t, _s['code'], _s['name'], _s.get('via', ''),
                                 _s.get('industry', ''), _s.get('irs_score', 0),
                                 (stock_mainbiz.get(_s['code'], '') or '')[:200]])
                    _n_pre += 1
        print(f"  [dry-run] 门槛前成员池（4a.45 后 / 4a.47 前）-> {_pre_path}（{_n_pre} 条）")

    # 4a.47 环节清单门槛（主题定义重构，20260927）
    # 只作用于 theme_config.json 中声明 "segment_gate": true 的 6 个优先主题。
    # 20260927 口径同步（报告表 5「MINOR_RULE_FIX」落地）：
    #   保留/剔除判定改为复用 Mapping V3 审计规则引擎 theme_mapping_v3_audit.classify，
    #   生产侧与审计侧同源，不再各自维护环节词表（消除口径分叉）。
    #   相较旧实现（P1/P2/P3 任一命中即保留）的变化：
    #     · 黑名单 SEG_KW_GENERIC 泛化词（数据中心/PCB/航天/发电/电器…）不再单独构成强证据
    #     · 公司简称命中 / 宽口径产业链证据 / 环节行业归属 三者均降为「弱证据」，
    #       需两条**独立**弱证据互证才保留 —— 环节行业不再单独构成成员资格
    #       （修复 电力环节行业豁免过宽、智能驾驶车联网口径过泛、军工宽口径词无证据入链）
    #     · 白名单补充词已写入 theme_config.json 的 segments[*].keywords
    #       （消费 家居与文娱/食品、AI算力 算力租赁 → 修复 海伦钢琴/恒林股份/汤臣倍健 误剔）
    #     · 保守兜底：主营无主题业务证据时按行业口径落 UNCERTAIN（生产侧同样不保留）
    #   保留的生产侧策略（有意与审计口径不同，勿误改）：
    #     X1 配置人工名单（leaders / core_stocks，按代码）
    #     X2 名单类来源（leader_company / core_company / manual_override）
    #     X3 主营文本缺失/过短/经营范围样板 —— 数据缺口，按 HC V1.0 §十三保留待补录
    if SEGMENT_GATE_ENABLED:
        import theme_mapping_v3_audit as _maud
        _seg_by_cn = {}
        for _k, _c in new_cfg.items():
            if _k.startswith('_') or not _c.get('segment_gate'):
                continue
            if _c.get('segments'):
                _seg_by_cn[_c.get('name_cn', _k)] = (_k, _c)
        if _seg_by_cn:
            _force_via_seg = ('leader_company', 'core_company', 'manual_override')
            _seg_audit, _seg_before, _seg_after = [], 0, 0
            _seg_x3, _seg_weak = 0, 0
            for _theme, (_key, _cfg) in _seg_by_cn.items():
                if _theme not in themes_output_raw:
                    continue
                _named = set(_cfg.get('leaders') or []) | set(_cfg.get('core_stocks') or [])
                _slist = themes_output_raw[_theme]
                _seg_before += len(_slist)
                _kept, _no_text, _by_weak = [], 0, 0
                for s in _slist:
                    _code, _name, _via = s['code'], s['name'], s.get('via', '')
                    if _code in _named or _via in _force_via_seg:   # X1/X2
                        _kept.append(s)
                        continue
                    _mb = stock_mainbiz.get(_code, '')
                    if seg_text_unusable(_mb):                      # X3 数据缺口：保留，不判
                        _kept.append(s)
                        _no_text += 1
                        continue
                    _ind = s.get('industry') or ''
                    _res = _maud.classify(_key, _cfg, _code, _name, _via, _ind, _mb)
                    if _res['classification'] in ('CORE', 'CHAIN'):
                        _kept.append(s)
                        if _res['evidence_type'] in ('弱证据', '弱证据交叉', '环节行业'):
                            _by_weak += 1
                        continue
                    _seg_audit.append({'theme': _theme, 'code': _code, 'name': _name,
                                       'industry': _ind, 'via': _via,
                                       'irs_score': s.get('irs_score', 0),
                                       'mainbiz': _mb[:120],
                                       'why': f"{_res['classification']}|"
                                              f"{_res['evidence_type']}|{_res['evidence']}"})
                _seg_after += len(_kept)
                _seg_x3 += _no_text
                _seg_weak += _by_weak
                if len(_kept) != len(_slist):
                    print(f"  [环节清单] {_theme}: 剔除 {len(_slist)-len(_kept)}/{len(_slist)}"
                          f" → 保留 {len(_kept)}（弱证据互证 {_by_weak} / 文本不可用豁免 {_no_text}）")
                themes_output_raw[_theme] = _kept
            print(f"  [环节清单] {len(_seg_by_cn)} 个门槛主题合计剔除 {_seg_before-_seg_after}/{_seg_before} 条，"
                  f"保留 {_seg_after}（弱证据互证 {_seg_weak}、文本不可用豁免 {_seg_x3}）")
            if _seg_audit:
                _seg_path = os.path.join(OUTPUT_DIR, f'theme_segment_dropped_{TRADE_DATE}.csv')
                with open(_seg_path, 'w', encoding='utf-8-sig', newline='') as f:
                    _seg_w = csv.DictWriter(f, fieldnames=['theme', 'code', 'name', 'industry',
                                                           'via', 'irs_score', 'mainbiz', 'why'])
                    _seg_w.writeheader()
                    _seg_w.writerows(_seg_audit)
                print(f"  [环节清单] 审计清单 -> {_seg_path}")

    # 4a.48 硬排除否决（20260927 全局口径统一）
    # 4a.47 只作用于 6 个声明 segment_gate 的主题；26 个未启用门槛的主题 segments 为空，
    # 完全不经过环节审查，实测 47 条「配置写明的排除项」成员仍留在生产映射
    # （创新药·原料药 30、节能环保 4、高端材料 3、医疗器械 3、地产链 2…）。
    # 此处对所有声明了 exclude_keywords 的主题做一步否决：审计引擎判 EXCLUDE 且
    # evidence_type='主营' 者剔除；其余 UNCERTAIN 与兜底类 EXCLUDE（行业+主营双无证据）
    # 一律不动 —— 后者需先做该主题的人工抽样验证，避免未验证误杀。
    # 豁免口径与 4a.47 完全一致：X1 配置人工名单 / X2 名单类来源 / X3 文本不可用。
    if HARD_EXCLUDE_VETO_ENABLED:
        import theme_mapping_v3_audit as _maud_v
        _veto_cfg = {}
        for _k, _c in new_cfg.items():
            if _k.startswith('_') or not (_c.get('exclude_keywords') or []):
                continue
            _veto_cfg[_c.get('name_cn', _k)] = (_k, _c)
        if _veto_cfg:
            _veto_via = ('leader_company', 'core_company', 'manual_override')
            _veto_out, _veto_before, _veto_after = [], 0, 0
            for _theme, (_key, _cfg) in _veto_cfg.items():
                if _theme not in themes_output_raw:
                    continue
                _named = set(_cfg.get('leaders') or []) | set(_cfg.get('core_stocks') or [])
                _slist = themes_output_raw[_theme]
                _veto_before += len(_slist)
                _kept = []
                for s in _slist:
                    _code, _name, _via = s['code'], s['name'], s.get('via', '')
                    if _code in _named or _via in _veto_via:        # X1/X2
                        _kept.append(s)
                        continue
                    _mb = stock_mainbiz.get(_code, '')
                    if seg_text_unusable(_mb):                      # X3 数据缺口：保留，不判
                        _kept.append(s)
                        continue
                    _ind = s.get('industry') or ''
                    _res = _maud_v.classify(_key, _cfg, _code, _name, _via, _ind, _mb)
                    if _res['classification'] == 'EXCLUDE' and _res['evidence_type'] == '主营':
                        _veto_out.append({'theme': _theme, 'code': _code, 'name': _name,
                                          'industry': _ind, 'via': _via,
                                          'irs_score': s.get('irs_score', 0),
                                          'mainbiz': _mb[:120],
                                          'why': f"{_res['evidence_type']}|{_res['evidence']}"})
                        continue
                    _kept.append(s)
                themes_output_raw[_theme] = _kept
                _veto_after += len(_kept)
                if len(_kept) != len(_slist):
                    print(f"  [硬排除否决] {_theme}: 剔除 {len(_slist)-len(_kept)}/{len(_slist)}"
                          f" → 保留 {len(_kept)}")
            print(f"  [硬排除否决] 声明排除词的主题合计剔除 {_veto_before-_veto_after}/{_veto_before} 条")
            if _veto_out:
                _veto_path = os.path.join(OUTPUT_DIR, f'theme_hard_exclude_dropped_{TRADE_DATE}.csv')
                with open(_veto_path, 'w', encoding='utf-8-sig', newline='') as f:
                    _vw = csv.DictWriter(f, fieldnames=['theme', 'code', 'name', 'industry',
                                                        'via', 'irs_score', 'mainbiz', 'why'])
                    _vw.writeheader()
                    _vw.writerows(_veto_out)
                print(f"  [硬排除否决] 审计清单 -> {_veto_path}")

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

    # 4a.55 V3.1 降级主题成员改挂（20260929）
    # 降级主题（rank_participation=false）已在「转换」阶段跳过、不生成主题键，
    # 此处把其真成员按 V3.1 PRIMARY 独占归属补回母主题，避免成员随主题键一起消失。
    # 依据见 THEME_DEMOTED_REHOME（源：subtheme_map_v3_1.json 的 stocks[*].primary）。
    if THEME_DEMOTED_REHOME:
        _demoted_cn = {cfg.get('name_cn', k) for k, cfg in new_cfg.items()
                       if not k.startswith('_') and cfg.get('rank_participation') is False}
        _rehome_ind = {}
        for _, _r in stock_basic_df.iterrows():
            _rehome_ind[_r['ts_code']] = (_r.get('name', ''), _r.get('industry', ''))
        for _dtheme, _targets in THEME_DEMOTED_REHOME.items():
            if _dtheme not in _demoted_cn:
                continue
            for _tgt, _codes in _targets.items():
                if _tgt not in themes_output_raw:
                    themes_output_raw[_tgt] = []
                for _code in _codes:
                    if any(s['code'] == _code for s in themes_output_raw[_tgt]):
                        print(f"  [降级改挂] {_dtheme} → {_tgt} {_code} 已在池，跳过")
                        continue
                    _nm, _ind = _rehome_ind.get(_code, (_code, ''))
                    themes_output_raw[_tgt].append({
                        "code": _code,
                        "name": _nm,
                        "via": "manual_override",
                        "chain_distance": 0,
                        "score": 90,
                        "irs_score": 90,
                        "irs_layer": "core",
                        "industry": _ind,
                        "concepts": stock_concepts.get(_code, []),
                    })
                    total_refs_raw += 1
                    print(f"  [降级改挂] {_dtheme} → {_tgt} + {_code} {_nm} (manual_override)")

    # 4a.6 成分股污染过滤（§源头治理，判定函数复用 V2.4-HC）
    # 判定复用 theme_heat_v24_hc.hc_classify：POLLUTION = 命中排除词 /
    # 仅概念标签来源 / 无正面主营证据且行业不属于该主题「特征行业」。
    # 注意：本处比 hc_classify 多一层 E1~E6 豁免，故滤镜后的成员池仍可能被
    # HC 层判为 POLLUTION（HC 的 purity 不含豁免）——两者口径有意不同，勿误改。
    #
    # 但「特征行业」只由 CORE 成员行业分布（占比≥15%）定义，异质主题天然覆盖
    # 不全——军工 CORE 集中在航空/电子，运输设备(内蒙一机)、专用机械(长城军工)、
    # 元器件(景嘉微) 全部落网；消费 CORE 分散，白酒/食品/软饮料 都进不了门槛。
    # 这类多为词表缺口而非污染，直接删会造成军工-68%、消费-74% 的破坏。
    # 故删除前先做「主题自有证据」豁免，成员只要在主题自身通道留下正面痕迹即保留：
    #   E1 配置人工名单命中（leaders / core_stocks，按代码）
    #   E2 名单类来源（leader_company / core_company / manual_override）
    #   E3 行业命中主题声明（industry_chains / sw_industry_match / cx_industry_match）
    #   E4 该主题精确概念板块成员（东财概念名与配置 concept 完全一致）
    #   E5 主营文本命中 THEME_MAINBIZ_KEYWORDS（32 主题业务词表，此前未启用）
    if POLLUTION_FILTER_ENABLED:
        import theme_heat_v22 as _th
        import theme_heat_v24_hc as _hcmod
        _cfg_by_cn = _th.load_theme_config_v3()
        _NAME_VIA = ('leader_company', 'core_company', 'manual_override')
        _audit, _n_before, _n_after, _n_exempt = [], 0, 0, 0
        for _theme in list(themes_output_raw):
            _cfg = _cfg_by_cn.get(_theme)
            if _cfg is None:                       # 子主题名等无配置 → 不判污染
                continue
            _slist = themes_output_raw[_theme]
            _n_before += len(_slist)
            _lay = {s['code']: {'layer': _th.classify_member(
                s['code'], s.get('via', ''), stock_mainbiz.get(s['code'], ''), _cfg)[0]}
                for s in _slist}
            _char_ind = _hcmod.characteristic_industries(
                _lay, {s['code']: {'industry': s.get('industry') or ''} for s in _slist})
            # 豁免集
            _named = set(_cfg.get('leaders') or []) | set(_cfg.get('core_stocks') or [])
            _ind_ok = (set(_cfg.get('industry_chains') or [])
                       | set(_cfg.get('sw_industry_match') or [])
                       | set(_cfg.get('cx_industry_match') or []))
            _mb_kw = THEME_MAINBIZ_KEYWORDS.get(_theme) or []
            _cpt_boards = [b.strip() for b in
                           (old_format_themes.get(_theme, {}).get('concept') or [])]
            # E6：声明行业里体量 ≤ BOARD_NARROW_MAX 的「板≈主题」成员（行业灌入合法）
            _narrow_members = set()
            for _b in (set(_cfg.get('sw_industry_match') or [])
                       | set(_cfg.get('cx_industry_match') or [])):
                _bm = dc_industry_members_exact.get(_b)
                if _bm and len(_bm) <= BOARD_NARROW_MAX:
                    _narrow_members |= _bm
            _keep_codes = POLLUTION_KEEP.get(_theme, set())
            _kept = []
            for s in _slist:
                _code = s['code']
                _mb = stock_mainbiz.get(_code, '')
                if (_code in _keep_codes or _code in _named
                        or s.get('via', '') in _NAME_VIA
                        or (s.get('industry') and s['industry'] in _ind_ok)
                        or _code in _narrow_members
                        or (_mb_kw and _mb and any(w in _mb for w in _mb_kw))
                        or any(_code in dc_concept_members_exact.get(b, set())
                               for b in _cpt_boards)):
                    _kept.append(s)
                    _n_exempt += 1
                    continue
                _cls, _why = _hcmod.hc_classify(
                    _code, s.get('via', ''), _mb, _cfg,
                    s.get('industry') or '', _char_ind)
                if _cls == 'POLLUTION':
                    _audit.append({'theme': _theme, 'code': _code, 'name': s['name'],
                                   'industry': s.get('industry') or '',
                                   'via': s.get('via', ''),
                                   'irs_score': s.get('irs_score', 0), 'why': _why})
                else:
                    _kept.append(s)
            if len(_kept) != len(_slist):
                print(f"  [污染过滤] {_theme}: 剔除 {len(_slist)-len(_kept)}/{len(_slist)}"
                      f" → 保留 {len(_kept)}")
            themes_output_raw[_theme] = _kept
            _n_after += len(_kept)
        print(f"  [污染过滤] 合计剔除 {_n_before-_n_after}/{_n_before} 条，"
              f"保留 {_n_after}（豁免 {_n_exempt} 条主题自有证据，保留 WEAK，仅剔 POLLUTION）")
        if _audit:
            _audit_path = os.path.join(OUTPUT_DIR, f'theme_pollution_dropped_{TRADE_DATE}.csv')
            with open(_audit_path, 'w', encoding='utf-8-sig', newline='') as f:
                _w = csv.DictWriter(f, fieldnames=['theme', 'code', 'name', 'industry',
                                                   'via', 'irs_score', 'why'])
                _w.writeheader()
                _w.writerows(_audit)
            print(f"  [污染过滤] 审计清单 -> {_audit_path}")
        if POLLUTION_DRY_RUN:
            print(f"\n[试运行] 未写入任何映射文件（去掉 --dry-run 正式生效）")
            return None

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
    # 20260928 AI算力「拆旧建新」：母主题键以 "_" 开头（_UNCERTAIN）的子主题
    # 表示"无法归入任一正式主题"，直接丢弃该主题名，既不映射到任何母主题，
    # 也不作为独立主题名残留（否则会以"PCB高速互连"等名义进入排名 universe）。
    try:
        _subtheme_cfg = load_subtheme_map()
        if _subtheme_cfg:
            sub_to_parent = {}
            sub_drop = set()
            for parent, subthemes in _subtheme_cfg.items():
                for sub_name in subthemes.keys():
                    if parent.startswith('_'):
                        sub_drop.add(sub_name)
                    else:
                        sub_to_parent[sub_name] = parent
            remap_count = 0
            for code in list(stocks_output.keys()):
                themes = stocks_output[code].get('themes', [])
                new_themes = []
                changed = False
                for t in themes:
                    if t in sub_drop:
                        changed = True
                        continue
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

    # 4c.6 重映射后行业互斥复核
    # 4a 过滤以 theme_stock_map 的键（含子主题名）为单位，子主题名不在
    # THEME_INDUSTRY_EXCLUDE 中，漏网股票经 4c.5 重映射回母主题后"复活"
    # （4d 的 meta 兜底还会从未过滤的 theme_stock_map 取回原始 via/score）。
    # 此处以最终母主题名补跑同样的互斥过滤，豁免口径与 4a 一致。
    _recheck_dropped = []
    for code in list(stocks_output.keys()):
        info = stocks_output[code]
        industry = info.get('industry', '')
        kept_themes = []
        for theme_name in info.get('themes', []):
            if theme_name in THEME_INDUSTRY_EXCLUDE:
                meta = theme_stock_map.get(theme_name, {}).get(code, {})
                via = meta.get('via', '')
                if via not in ('leader_company', 'core_company') and any(
                        ex in industry for ex in THEME_INDUSTRY_EXCLUDE[theme_name]):
                    _recheck_dropped.append(f"{info['name']}({industry})@{theme_name}")
                    continue
            kept_themes.append(theme_name)
        if kept_themes != info.get('themes', []):
            info['themes'] = kept_themes
    if _recheck_dropped:
        print(f"  [重映射复核] 行业互斥补剔 {len(_recheck_dropped)} 条 -> {'; '.join(_recheck_dropped[:8])}{'...' if len(_recheck_dropped) > 8 else ''}")

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
        # 20260927 确定性 tie-break：原排序键只有分数，而同分并列在 300 截断线上
        # 由 set 迭代顺序（dc_industry_board_members 为 set，受 PYTHONHASHSEED 随机化）
        # 决定，导致同代码两次运行约 80 条边界成员漂移（实测消费 50 / 新能源车 18 /
        # 化工 12 / 机器人 3）。加入 ts_code 作稳定次序后结果可复现，规则本身不变。
        themes_output[theme_name].sort(
            key=lambda x: (-x.get('irs_score', x.get('score', 0)), x['code']))
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

    # 同步写 report_daily/theme_stock_map_latest_v2.json
    # （market_regime_v3 引擎 resolve_theme_stock_map_path 优先级第 1 位，
    #   必须保持与 cache_daily 同源，否则旧快照会让医疗器械等新增主题缺失）
    v2_latest_file = os.path.join(OUTPUT_DIR, "theme_stock_map_latest_v2.json")
    with open(v2_latest_file, 'w', encoding='utf-8') as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)

    # 清理历史 theme_stock_map 版本（保留最近 5 天 + latest）
    _cleanup_old_theme_maps(CACHE_DIR, keep_days=5)

    total_refs = sum(len(v) for v in themes_output.values())
    print(f"\n{'='*60}")
    print(f"完成基础映射！")
    print(f"  CSV: {csv_file}")
    print(f"  JSON: {json_file}")
    print(f"  LATEST: {latest_file}")
    print(f"  V2-LATEST: {v2_latest_file}")
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
