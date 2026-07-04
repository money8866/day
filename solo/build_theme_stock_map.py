#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日主题-个股对应关系映射生成器
使用 theme_pattern_stock_picker.py 中的 match_theme_stocks 算法，
生成所有主题与个股的对应关系 JSON 文件。

输出文件：d:/mystock/cache_daily/theme_stock_map_{TRADE_DATE}.json

JSON 结构：
{
    "trade_date": "20260618",
    "update_time": "2026-06-18T15:30:00",
    "themes": {
        "光通信": [
            {"code": "300308.SZ", "name": "中际旭创", "via": "leader_company", "chain_distance": 0, "score": 35},
            ...
        ]
    },
    "stocks": {
        "300308.SZ": {
            "name": "中际旭创",
            "themes": ["光通信", "AI算力链"]
        }
    }
}
"""

import sys
import os
import json
from datetime import datetime

# Windows GBK 控制台输出修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 添加项目路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

# 导入所需模块
from tushare_quant import pro, TRADE_DATE
import theme_trend_sentiment_score as theme_ts

CACHE_DIR = r"d:\mystock\cache_daily"
os.makedirs(CACHE_DIR, exist_ok=True)


def build_theme_stock_map():
    """
    构建主题-个股对应关系映射
    """
    print(f"[开始] 构建主题-个股映射: {TRADE_DATE}")
    
    # 1. 加载主题配置
    theme_path = os.path.join(BASE_DIR, 'theme.json')
    with open(theme_path, 'r', encoding='utf-8') as f:
        hot_themes = json.load(f)['HOT_THEMES']
    print(f"[加载] 共 {len(hot_themes)} 个主题配置")
    
    # 2. 获取东财成分股数据和股票基本信息
    dc_df = theme_ts.get_dc_members()
    try:
        stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
    except Exception as e:
        print(f"[错误] 获取 stock_basic 失败: {e}")
        return None
    
    # 3. 调用 match_theme_stocks 进行匹配
    # 返回: theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts
    theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_ts.match_theme_stocks(
        hot_themes, dc_df, stock_basic_df
    )
    
    print(f"[匹配] 共 {len(theme_stock_map)} 个主题匹配到成份股")
    
    # 4. 构建正向映射: theme -> [stock, ...]
    MAX_STOCKS_PER_THEME = 300
    MAX_THEMES_PER_STOCK = 5
    # 超低分过滤阈值（分数低于此值的concept_fallback/industry_alias股票被清理）
    LOW_SCORE_THRESHOLD = 5

    # 加载主营业务数据用于基本面验证
    mainbiz_path = os.path.join(CACHE_DIR, 'stock_company_mainbiz.json')
    stock_mainbiz = {}
    if os.path.exists(mainbiz_path):
        with open(mainbiz_path, 'r', encoding='utf-8') as f:
            stock_mainbiz = json.load(f)
        print(f"[加载] 主营业务数据: {len(stock_mainbiz)} 只")

    # 主题→主营业务验证关键词（用于过滤行业泛化误判）
    # 对于 concept_fallback / industry_alias 匹配的股票，main_business 必须包含至少1个关键词
    THEME_MAINBIZ_KEYWORDS = {
        'AI算力基建': ['算力', '数据中心', '服务器', '云计算', 'IDC', '光模块', '芯片', '散热', '液冷', '电源', '机柜', '带宽'],
        'AI应用与模型': ['人工智能', 'AI', '大模型', '机器学习', '自然语言', '智能', '软件', '算法', '数据', '云计算'],
        'AI芯片': ['芯片', '半导体', 'GPU', '处理器', '集成电路', '算力', '设计'],
        'AI文娱内容': ['游戏', '影视', '文娱', '传媒', '直播', '短视频', '内容', '文化', '娱乐', '动漫', '数字', '出版'],
        'AI新消费': ['营销', '广告', '传媒', '直播', '电商', '品牌', 'IP', '潮玩', '谷子'],
        '金融科技': ['金融', '银行', '支付', '证券', '保险', '信贷', '区块链', '数字货币', ' fintech', '征信', '清算'],
        '光通信': ['光', '光纤', '光模块', '通信', '激光', '光电', '网络'],
        '特高压': ['特高压', '输电', '变电', '变压器', '换流阀', '电网设备', '电力设备'],
        '电网智能化': ['智能电网', '电网', '虚拟电厂', '配电', '电力软件', '电表'],
        '充电桩': ['充电桩', '充电', '换电', '高压快充', '充电模块', 'V2G'],
        '半导体制造': ['半导体', '芯片', '晶圆', '代工', '集成电路', '制造'],
        '半导体封测与先进封装': ['封装', '封测', '测试', '半导体', '芯片', '集成电路'],
        '半导体材料': ['半导体', '硅片', '靶材', '特气', '电子化学', '光刻胶', '材料'],
        '半导体设备': ['半导体', '设备', '刻蚀', '薄膜', '沉积', '光刻', '检测'],
        '功率半导体': ['半导体', 'IGBT', 'MOSFET', '功率', '芯片', '模块'],
        '存储芯片': ['存储', '内存', '闪存', 'DRAM', 'NAND', '芯片'],
        'IC设计': ['芯片', '设计', '集成电路', '模拟', '射频', 'MCU', 'SoC'],
        '化工链': ['化工', '化学', '原料', '材料', '肥料', '农药', '塑料', '橡胶', '涂料', '染料'],
        '氢能': ['氢', '燃料电池', '电解水', '储氢', '加氢'],
        '煤炭链': ['煤', '煤炭', '焦煤', '焦炭', '动力煤'],
        '合成生物': ['合成生物', '生物制造', '发酵', '生物', '酶', '基因', '细胞'],
        '人形机器人': ['人形机器人', '机器人', '减速器', '丝杠', '电机', '传感器', '执行器', '关节'],
        '工业母机与自动化': ['机床', '数控', '工控', '自动化', '伺服', '变频器', 'PLC', '工业控制', '机器人', '减速机'],
        '工程机械与重型装备': ['工程机械', '挖掘机', '起重机', '矿山机械', '重型装备'],
        '低空经济': ['低空', '无人机', '飞行器', '航空', '直升', 'eVTOL'],
        '固态电池': ['电池', '固态', '锂', '正极', '负极', '电解质', '电芯'],
        '新型储能': ['储能', '电池', '锂电', '逆变', 'PCS', '充放'],
        '新能源汽车链': ['汽车', '新能源', '电池', '电机', '电控', '充电', '车载'],
        '贵金属': ['金', '银', '铂', '贵金属'],
        '工业金属': ['铜', '铝', '铅', '锌', '锡', '镍', '金属'],
        '小金属': ['稀土', '钨', '钼', '锂', '钴', '镍', '钛', '锆', '小金属'],
    }

    # ST股票灰名单过滤
    ST_FILTER_ENABLED = True
    # 主题-行业互斥规则：主题名 -> 不应包含的行业列表
    THEME_INDUSTRY_EXCLUDE = {
        '工业金属': ['煤炭开采', '造纸', '钢加工', '化学原料'],
        '小金属': ['煤炭开采', '造纸', '钢加工'],
        '贵金属': ['铜', '铅锌'],
        '券商': [],
        '钢铁': [],
        '银行': [],
        '保险': [],
        '合成生物': ['养殖业', '生猪', '畜禽饲料', 'CDMO', '医疗服务'],
        '功率半导体': ['汽车零部件', '燃油', '内燃机'],
        'AI应用与模型': ['教育', '职业教育', '培训'],
        '金融科技': ['游戏', '移动游戏', '网红经济', '营销服务'],
        'AI文娱内容': ['基建', '勘察', '交通工程', '建筑设计'],
        'AI算力基建': ['电力设备', '输变电', '配电设备'],
        '煤炭链': ['化学制品', '化学原料', '化工原料', '化工', '塑料'],
        # 特高压/充电桩排除软件IT类（归入电网智能化），保留电气设备/汽车配件类
        '特高压': ['软件服务', 'IT设备', '互联网', '出版业', '影视音像', '广告包装'],
        '充电桩': ['软件服务', 'IT设备', '互联网', '出版业', '影视音像', '广告包装'],
    }
    # 主题-行业白名单：主题只允许特定行业的股票
    THEME_INDUSTRY_WHITELIST = {
        '券商': ['证券', '资本市场服务'],
        '银行': ['银行'],
        '保险': ['保险'],
        '钢铁': ['普钢', '特钢', '钢铁'],
        # 电网智能化强制只保留软件/IT类公司，与电气设备类（特高压/充电桩）分离
        # 注：tushare东财行业分类中"电气设备"涵盖电网设备+电源设备+自动化设备，
        # 无法用行业白名单区分特高压和充电桩，改用跨主题互斥规则处理
        '电网智能化': ['软件服务', 'IT设备'],
    }
    # 主题-股票黑名单：明确不应归入该主题的股票（基于AI分析）
    THEME_STOCK_BLACKLIST = {
        'AI算力基建': ['思源电气', '中国宝安', '诺德股份'],
        '消费电子与AI终端': ['禾盛新材', '慧谷新材'],
        'AI应用与模型': ['中公教育', '霍莱沃'],
        '金融科技': ['汤姆猫', '天下秀'],
        'AI文娱内容': ['华设集团'],
        '合成生物': ['牧原股份', '凯莱英', '双成药业'],
        '功率半导体': ['威孚高科'],
    }

    themes_output_raw = {}
    total_stock_refs_raw = 0
    for theme_name, stocks in theme_stock_map.items():
        stock_list = []
        for code, meta in stocks.items():
            stock_name = name_map_basic.get(code, code)
            stock_industry = stock_basic_industry.get(code, "")
            if not isinstance(stock_industry, str):
                stock_industry = ""
            stock_via = meta.get("via", "")

            # ST股票过滤
            if ST_FILTER_ENABLED and ('ST' in stock_name or '*ST' in stock_name or 'ST' in stock_name.upper()):
                continue

            # 主题-股票黑名单过滤
            if theme_name in THEME_STOCK_BLACKLIST:
                if stock_name in THEME_STOCK_BLACKLIST[theme_name]:
                    continue

            # 主题-行业白名单强制约束（core/leader公司豁免，保留主题龙头）
            if theme_name in THEME_INDUSTRY_WHITELIST and stock_via not in ('core_company', 'leader_company'):
                whitelist = THEME_INDUSTRY_WHITELIST[theme_name]
                if not any(w in stock_industry for w in whitelist):
                    continue

            # 主题-行业互斥规则
            if theme_name in THEME_INDUSTRY_EXCLUDE:
                excluded = THEME_INDUSTRY_EXCLUDE[theme_name]
                if any(ex in stock_industry for ex in excluded):
                    continue

            # 超低分过滤：concept_fallback和industry_alias来源的低分股票清理
            stock_score = meta.get("score", 0)
            if stock_via in ('concept_fallback', 'stock_basic_industry_alias') and stock_score < LOW_SCORE_THRESHOLD:
                continue

            # 主营业务验证：对非核心来源的股票，用主营业务文本验证主题相关性
            if stock_via in ('concept_fallback', 'stock_basic_industry_alias', 'concept_as_industry'):
                if theme_name in THEME_MAINBIZ_KEYWORDS:
                    mainbiz_text = stock_mainbiz.get(code, '')
                    if mainbiz_text:
                        keywords = THEME_MAINBIZ_KEYWORDS[theme_name]
                        if not any(kw in mainbiz_text for kw in keywords):
                            continue

            stock_list.append({
                "code": code,
                "name": stock_name,
                "via": stock_via,
                "chain_distance": meta.get("chain_distance", 2),
                "industry_match": meta.get("industry_match", False),
                "score": stock_score,
                "industry": stock_industry,
                "concepts": stock_concepts.get(code, []),
            })
            total_stock_refs_raw += 1
        stock_list.sort(key=lambda x: -x['score'])
        themes_output_raw[theme_name] = stock_list

    stocks_output_raw = {}
    for theme_name, stock_list in themes_output_raw.items():
        for s in stock_list:
            code = s['code']
            if code not in stocks_output_raw:
                stocks_output_raw[code] = {
                    "name": s['name'],
                    "industry": s['industry'],
                    "concepts": s['concepts'],
                    "themes": [],
                    "scores": {},
                    "vias": {},
                }
            stocks_output_raw[code]["themes"].append(theme_name)
            stocks_output_raw[code]["scores"][theme_name] = s['score']
            stocks_output_raw[code]["vias"][theme_name] = s['via']

    # 限制每只股票最多保留 MAX_THEMES_PER_STOCK 个主题，按优先级排序
    # 跨主题审核：归属3+主题且多为concept_fallback的股票，限制最多3个主题
    # 跨主题互斥：某些主题对不应同时出现在同一股票上
    THEME_MUTEX_PAIRS = [
        ('AI文娱内容', '特高压'),
        ('AI文娱内容', '电网智能化'),
        ('AI文娱内容', '充电桩'),
        ('AI文娱内容', '发电与电源设备'),
        ('AI文娱内容', '基建地产链'),
        ('AI文娱内容', '交通运输物流'),
        ('金融科技', 'AI文娱内容'),
        ('金融科技', 'AI新消费'),
        ('合成生物', '大农业'),
        ('AI算力基建', 'AI文娱内容'),
        ('AI算力基建', 'AI新消费'),
        ('AI应用与模型', '金融科技'),
        ('人形机器人', '工业母机与自动化'),
        # 电力三主题互斥：按主导业务强制归属单一主题
        ('特高压', '电网智能化'),
        ('特高压', '充电桩'),
        ('电网智能化', '充电桩'),
    ]
    
    stocks_output = {}
    via_priority = {'leader_company': 4, 'core_company': 3, 'dc_industry_board': 2, 'stock_basic_industry': 2, 'stock_basic_industry_alias': 1, 'concept_as_industry': 1, 'concept_fallback': 0}
    for code, info in stocks_output_raw.items():
        theme_items = [(t, info['scores'][t], info['vias'][t]) for t in info['themes']]
        theme_items.sort(key=lambda x: (-via_priority.get(x[2], -1), -x[1]))
        
        # 跨主题审核：如果前5个主题中concept_fallback占比≥60%，则限制最多3个主题
        top_candidates = theme_items[:MAX_THEMES_PER_STOCK]
        fallback_count = sum(1 for t in top_candidates if t[2] == 'concept_fallback')
        if len(top_candidates) >= 3 and fallback_count / len(top_candidates) >= 0.6:
            max_for_this_stock = 3
        else:
            max_for_this_stock = MAX_THEMES_PER_STOCK
        
        # 跨主题互斥：按优先级依次选主题，若与已选主题互斥则跳过
        selected_themes = []
        for t in theme_items:
            if len(selected_themes) >= max_for_this_stock:
                break
            is_mutex = False
            for existing_theme, _, _ in selected_themes:
                for pair in THEME_MUTEX_PAIRS:
                    if (t[0] == pair[0] and existing_theme == pair[1]) or \
                       (t[0] == pair[1] and existing_theme == pair[0]):
                        is_mutex = True
                        break
                if is_mutex:
                    break
            if not is_mutex:
                selected_themes.append(t)
        
        stocks_output[code] = {
            "name": info["name"],
            "industry": info["industry"],
            "concepts": info["concepts"],
            "themes": [t[0] for t in selected_themes],
        }

    # 根据过滤后的股票→主题映射，重新构建主题→股票映射，并限制最大成份股数
    themes_output = {}
    total_stock_refs = 0
    for code, info in stocks_output.items():
        for theme_name in info["themes"]:
            if theme_name not in themes_output:
                themes_output[theme_name] = []
            meta = theme_stock_map[theme_name].get(code, {})
            stock_name = name_map_basic.get(code, code)
            themes_output[theme_name].append({
                "code": code,
                "name": stock_name,
                "via": meta.get("via", ""),
                "chain_distance": meta.get("chain_distance", 2),
                "industry_match": meta.get("industry_match", False),
                "score": meta.get("score", 0),
                "industry": stock_basic_industry.get(code, ""),
                "concepts": stock_concepts.get(code, []),
            })

    for theme_name in themes_output:
        themes_output[theme_name].sort(key=lambda x: -x['score'])
        themes_output[theme_name] = themes_output[theme_name][:MAX_STOCKS_PER_THEME]

    # 重新构建股票→主题映射（确保一致性）
    stocks_output = {}
    for theme_name, stocks in themes_output.items():
        for s in stocks:
            code = s["code"]
            if code not in stocks_output:
                stocks_output[code] = {
                    "name": s["name"],
                    "industry": s["industry"],
                    "concepts": s["concepts"],
                    "themes": [],
                }
            stocks_output[code]["themes"].append(theme_name)

    for code in stocks_output:
        theme_list = stocks_output[code]["themes"]
        theme_with_score = []
        for t in theme_list:
            if t in theme_stock_map and code in theme_stock_map[t]:
                theme_with_score.append((t, theme_stock_map[t][code].get("score", 0)))
            else:
                theme_with_score.append((t, 0))
        theme_with_score.sort(key=lambda x: -x[1])
        stocks_output[code]["themes"] = [t[0] for t in theme_with_score]

    total_stock_refs = sum(len(stocks) for stocks in themes_output.values())
    
    # 6. 组装最终 JSON
    output = {
        "trade_date": TRADE_DATE,
        "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "n_themes": len(themes_output),
        "n_stocks": len(stocks_output),
        "n_stock_refs": total_stock_refs,
        "themes": themes_output,
        "stocks": stocks_output,
    }
    
    # 7. 保存到缓存目录
    output_file = os.path.join(CACHE_DIR, f"theme_stock_map_{TRADE_DATE}.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # 同时更新最新版本（无日期后缀，方便引用）
    latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"[保存] {output_file}")
    print(f"[保存] {latest_file}")
    print(f"[统计] {len(themes_output)} 个主题, {len(stocks_output)} 只个股, {total_stock_refs} 条映射关系")
    
    return output


def load_theme_stock_map(trade_date=None):
    """加载指定日期的主题-个股映射"""
    if trade_date is None:
        latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
        if os.path.exists(latest_file):
            with open(latest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    cache_file = os.path.join(CACHE_DIR, f"theme_stock_map_{trade_date}.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def get_stock_themes(ts_code, trade_date=None):
    """查询某只股票所属的所有主题"""
    data = load_theme_stock_map(trade_date)
    if data and ts_code in data.get("stocks", {}):
        return data["stocks"][ts_code]
    return None


def get_theme_stocks(theme_name, trade_date=None):
    """查询某个主题的所有成份股"""
    data = load_theme_stock_map(trade_date)
    if data and theme_name in data.get("themes", {}):
        return data["themes"][theme_name]
    return None


if __name__ == '__main__':
    build_theme_stock_map()
    
    # 测试查询
    print("\n=== 测试查询 ===")
    data = load_theme_stock_map()
    if data:
        # 测试个股查询
        test_codes = ['600487.SH', '300308.SZ']
        for code in test_codes:
            info = get_stock_themes(code)
            if info:
                print(f"{info['name']}({code}): 主题={info['themes']}")
        
        # 测试主题查询
        test_themes = ['光通信', '人形机器人']
        for theme in test_themes:
            stocks = get_theme_stocks(theme)
            if stocks:
                print(f"{theme}: {len(stocks)} 只成份股")
                for s in stocks[:5]:
                    print(f"  {s['code']} {s['name']} via={s['via']} score={s['score']}")
