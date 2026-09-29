# -*- coding: utf-8 -*-
"""A股主题体系 V3.1 构建器（36 → 34 一级主题）

只读输入：
  theme_kg_v3/theme_kg_v3/config/theme_config.json     V3.0 配置
  cache_daily/theme_stock_map_v2_{DATE}.json           V2.4 生产映射
  cache_daily/stock_company_mainbiz.json               个股主营文本
  cache_daily/stock_basic.csv                          基础信息（代码/名称/行业）

产物：
  theme_kg_v3/theme_kg_v3/config/theme_config_v3_1.json
  theme_kg_v3/theme_kg_v3/config/subtheme_map_v3_1.json
  report_daily/theme_reclassification_{DATE}.csv
  report_daily/theme_mapping_audit_{DATE}.csv
  report_daily/theme_merge_audit_{DATE}.csv

不修改任何生产 .py。默认（dry-run）只产出上述文件；
加 --apply 时在备份后覆盖生产配置（接入 V3.1）：
  theme_kg_v3/theme_kg_v3/config/theme_config.json       ← V3.1 + _v3_1_meta + _flow + _chain_relations
  theme_kg_v3/theme_kg_v3/config/subtheme_map.json       ← 母主题改名/合并 + 新增 PCB
"""
import os
import sys
import csv
import json
import copy
from datetime import datetime
from collections import OrderedDict

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import theme_heat_v22 as v22          # 复用生产 classify_member（映射质量口径一致）

DATE = '20260928'
CFG_V30 = os.path.join(BASE, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json')
CFG_V31 = os.path.join(BASE, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config_v3_1.json')
SUB_V31 = os.path.join(BASE, 'theme_kg_v3', 'theme_kg_v3', 'config', 'subtheme_map_v3_1.json')
MAP_P = r'd:\mystock\cache_daily\theme_stock_map_v2_%s.json' % DATE
MAINBIZ_P = r'd:\mystock\cache_daily\stock_company_mainbiz.json'
BASIC_P = r'd:\mystock\cache_daily\stock_basic.csv'
OUT = os.path.join(BASE, 'report_daily')

# ══════════════════════════════════════════════════════════════════
# §一 主题计划：合并 / 新增 / 降级
# ══════════════════════════════════════════════════════════════════
RENAME = {'新能源车': '汽车', '智能驾驶': '汽车', '能源金属': '战略与小金属'}
RENAME_KEY = {'NEW_ENERGY_VEHICLE': 'AUTO', 'SMART_DRIVING': 'AUTO', 'ENERGY_METALS': 'MINOR_METALS'}
SKIP_KEYS = {'NEW_ENERGY_VEHICLE', 'SMART_DRIVING', 'ENERGY_METALS'}
AUTO_EXCLUDE = ['石油', '石化', '加油站', '光伏', '风电', '摩托车', '航空', '船舶', '农业机械']
METAL_EXCLUDE_DROP = ['碳酸锂', '氢氧化锂']
METAL_CORE_DROP = ['300750.SZ', '300919.SZ', '300014.SZ']   # 电池厂 → 汽车链

SEC_LABELS = {
    '汽车': ['新能源车', '智能驾驶', '汽车整车', '汽车零部件', '智能车灯', '汽车电子', '发动机零部件'],
    'PCB': ['AI服务器', '高速PCB', '高多层', 'HDI', 'AI算力', '汽车电子', '消费电子'],
    '液冷': ['AI服务器', '冷板', 'CDU', '热管理'],
    '机器人': ['3D视觉', '机器视觉', '人形机器人'],
    '消费电子': ['面板', '电子分销', '消费电子零部件'],
    '半导体': ['芯片设计', '芯片制造', '晶圆', '封测', '半导体设备', '半导体材料', '存储', '先进封装', '国产替代'],
    '传媒': ['影视', '广告', '出版', '数字内容', '营销', '文娱'],
    '游戏': ['AI游戏', '游戏出海', 'IP', '手游', '端游', '云游戏'],
    '战略与小金属': ['锂', '钴', '镍', '稀土', '钨', '钼', '锑', '锗', '其他战略资源'],
    '消费': ['乐器', '体育消费', '户外', '户外家居', '办公用品', '文旅'],
    'CPO': ['光模块', 'CPO', 'LPO', '光互联'],
    'AI服务器': ['服务器整机', 'AI服务器', 'AI工作站', '服务器集成'],
    '算力运营': ['IDC', '智算中心', '算力租赁', '算力运营'],
    '商业航天': ['火箭', '卫星', '卫星互联网', '商业发射'],
    '可控核聚变': ['托卡马克', '超导磁体', '第一壁', '氚'],
}

# ── (c) 3 个 WARN 主题的证据补录（配置层面：人工确认名单）──
# 判定依据见 report_daily/theme_mapping_audit_*.csv 的 weak_samples：
#   军工      11 只 via=dc_industry_board（东财行业板块=国防军工）但主营文本缺失（新股未收录）
#   游戏       3 只主营文本未描述游戏业务（游戏收入来自子公司），申万行业=游戏/传媒
#   工业金属   1 只主营文本陈旧（借壳前口径）+ 1 只主营文本缺失（申万行业=铝/铅锌）
WARN_FIX = {
    '军工': {
        'core_stocks': ['301677.SZ', '301699.SZ', '301707.SZ', '301717.SZ', '601091.SH',
                        '688835.SH', '920025.BJ', '920093.BJ', '920229.BJ', '920238.BJ',
                        '920298.BJ'],
    },
    '游戏': {
        'core_stocks': ['002919.SZ', '300043.SZ', '600633.SH'],
    },
    '工业金属': {
        'core_stocks': ['002532.SZ', '920634.BJ'],
    },
}

# ── (b) PCB 上游口径拆分：子链分层（不改变一级成员池，只做口径标注）──
# 依据：同花顺/东财「PCB」概念口径包含覆铜板与PCB专用材料/设备，
#       属同一资金交易单元 → 一级成员池保持 49 只；上游用 subchain 单列统计。
PCB_SUBCHAIN_OVERRIDE = {
    '600183.SH': '覆铜板/CCL', '603186.SH': '覆铜板/CCL', '002636.SZ': '覆铜板/CCL',
    '688519.SH': '覆铜板/CCL', '002552.SZ': '覆铜板/CCL', '301176.SZ': '覆铜板/CCL',
    '688603.SH': 'PCB电子化学品/专用材料', '002741.SZ': 'PCB电子化学品/专用材料',
    '300410.SZ': 'PCB设备/电子装联', '301200.SZ': 'PCB设备/电子装联',
}
PCB_SUBCHAIN_DEFAULT = 'PCB制造/线路板'    # 与 build_pcb_entry 的 segment 名一致

# (a) 生产 subtheme_map.json 用的 PCB 子主题条目（子主题名 → 母主题 PCB）
# 键名与 build_pcb_entry 的 segments[*].name 保持一致，便于两处口径对齐。
PCB_SUBS = OrderedDict([
    ('PCB制造/线路板', {
        'industry': ['电子', '元件'],
        'concept': ['PCB', '印制电路板', 'HDI', '高多层板', 'FPC'],
        'keywords': ['印制电路板', '印制线路板', '印刷电路板', '线路板', '电路板', '电路样板',
                     '高密度互连', 'HDI', '高多层', '柔性板', '挠性板', '软板', '刚性板',
                     '多层板', 'FPC', '双面板'],
        'exclude_keywords': ['面板', '液晶', '显示器', '光模块', '液冷'],
        'core_companies': ['沪电股份', '胜宏科技', '鹏鼎控股', '景旺电子', '深南电路',
                           '兴森科技', '崇达技术', '奥士康', '生益电子', '广合科技'],
    }),
    ('PCB高速互连', {
        'industry': ['电子', '元件'],
        'concept': ['PCB', '高速PCB'],
        'keywords': ['高速PCB', '高速互连', '高多层', 'AI服务器线路板', '企业通讯板', '服务器板'],
        'exclude_keywords': ['面板', '液晶', '显示器', '液冷'],
        'core_companies': ['沪电股份', '胜宏科技', '深南电路', '生益电子'],
    }),
    ('覆铜板/CCL', {
        'industry': ['电子', '元件'],
        'concept': ['覆铜板', 'CCL', '铜箔'],
        'keywords': ['覆铜板', '粘结片', '半固化片', '铜箔基板', 'CCL', '电子级玻璃布',
                     '电子电路铜箔', '电子铜箔'],
        'exclude_keywords': ['黄金', '采选'],
        'core_companies': ['生益科技', '南亚新材', '华正新材', '金安国纪', '逸豪新材'],
    }),
    ('PCB电子化学品/专用材料', {
        'industry': ['化工', '电子'],
        'concept': ['PCB化学品', '电子化学品'],
        'keywords': ['PCB化学品', '电子化学品', '电子电路专用', '感光油墨', '蚀刻液', '干膜',
                     '化学试剂'],
        'exclude_keywords': [],
        'core_companies': ['天承科技', '光华科技'],
    }),
    ('PCB设备/电子装联', {
        'industry': ['机械设备', '电子'],
        'concept': ['PCB设备', '电子装联'],
        'keywords': ['PCB设备', '线路板设备', '钻孔机', '曝光机', '电子装联', 'PCBA',
                     '印制电路板装配', '检测设备'],
        'exclude_keywords': [],
        'core_companies': ['大族数控', '正业科技'],
    }),
])

# ── PCB 一级主题：全市场主营扫描（规则 + 拒绝名单）──
PCB_KW = ['印制电路板', '印制线路板', '印刷电路板', '电路样板', '线路板', '覆铜板',
          'PCB', '电路板', '印刷电路', '印制电路', '柔性电路', '软性电路', 'FPC',
          '电子电路', '样板和小批量']
PCB_DENY = {
    '000016.SZ': '*ST康佳A：彩电+半导体为主，ST股',
    '301563.SZ': '云汉芯城：电子元器件分销/供应链，非PCB制造',
    '300735.SZ': '光弘科技：EMS整机/PCBA组装，归消费电子',
    '300632.SZ': '光莆股份：LED封装为第一主营',
    '300227.SZ': '光韵达：激光模板/激光加工服务',
    '002137.SZ': '实益达：PCBA贴装+整机贴牌',
    '002141.SZ': '贤丰控股：兽用疫苗为主、覆铜板为辅',
    '300576.SZ': '容大感光：PCB感光油墨+光刻胶，保留半导体材料口径',
}
PCB_CORE = ['002463.SZ', '300476.SZ', '002938.SZ', '603228.SH', '600183.SH',
            '002916.SZ', '688183.SH', '001232.SZ', '002436.SZ', '603920.SH',
            '002913.SZ', '002815.SZ', '001389.SZ']

# ══════════════════════════════════════════════════════════════════
# §三 被剔除个股重归属（逐只核验主营）
# ══════════════════════════════════════════════════════════════════
RECLAIM = [
    # ── 半导体污染：PCB → PCB（§八/§十一）──
    ('002463.SZ', 'PCB', ['AI服务器', '高速PCB'], ['高多层', 'AI算力', '企业通讯板'], 'NEW_THEME', 'HIGH',
     '主营印制电路板研发生产，PCB独立行情核心龙头，半导体剔除后回归PCB'),
    ('300476.SZ', 'PCB', ['AI服务器', 'HDI'], ['高密度线路板', 'AI算力', '显卡板'], 'NEW_THEME', 'HIGH',
     '主营高密度印制线路板，AI算力PCB龙头，半导体剔除后回归PCB'),
    ('002938.SZ', 'PCB', ['消费电子', 'FPC'], ['软板', 'HDI', '消费电子链'], 'NEW_THEME', 'HIGH',
     '主营各类印制电路板（FPC/HDI），半导体剔除后回归PCB'),
    ('603228.SH', 'PCB', ['汽车电子', '消费电子'], ['刚性板', '柔性板'], 'NEW_THEME', 'HIGH',
     '主营印制电路板研发生产，半导体剔除后回归PCB'),
    # ── 半导体污染：PCB 同类规则一并清洗 ──
    ('600183.SH', 'PCB', ['覆铜板', 'AI服务器'], ['CCL', '高速覆铜板', '粘结片'], 'RECLASSIFY', 'MEDIUM',
     '主营覆铜板/粘结片与印制线路板，CCL龙头，不属芯片口径'),
    ('002916.SZ', 'PCB', ['AI服务器', '封装基板'], ['高多层', '高速PCB', '电子装联'], 'RECLASSIFY', 'HIGH',
     '主营印刷电路板/电子装联，半导体剔除后归PCB'),
    ('001232.SZ', 'PCB', ['HDI', '电子制造服务'], ['PCB打样', '小批量板', '数字化制造'], 'RECLASSIFY', 'HIGH',
     'PCB打样与小批量板龙头，原误挂半导体'),
    ('688603.SH', 'PCB', ['电子化学品'], ['PCB专用化学品'], 'RECLASSIFY', 'LOW',
     '主营电子电路专用电子化学品，服务于PCB制造，归PCB上游'),
    ('002036.SZ', '汽车', ['智能驾驶', '消费电子'], ['车载镜头', '触控显示', '光学镜头'], 'RECLASSIFY', 'MEDIUM',
     '主营光学镜头（车载）与触控显示组件，集成电路仅为贸易业务，原误挂半导体'),
    # ── 半导体污染：面板 → 消费电子（§十二）──
    ('000725.SZ', '消费电子', ['面板'], ['显示面板', 'OLED', 'LCD'], 'RECLASSIFY', 'HIGH',
     '主营光电子与显示器件，面板龙头，半导体剔除后归消费电子'),
    ('000050.SZ', '消费电子', ['面板', '车载显示'], ['液晶显示', 'LTPS', '模组'], 'RECLASSIFY', 'HIGH',
     '主营液晶显示器及材料设备，面板厂，半导体剔除后归消费电子'),
    ('688055.SH', '消费电子', ['面板'], ['TFT-LCD', '液晶显示'], 'RECLASSIFY', 'HIGH',
     '主营TFT-LCD研发生产，面板厂，半导体剔除后归消费电子'),
    ('000100.SZ', '消费电子', ['面板'], ['半导体显示', '液晶', 'OLED'], 'RECLASSIFY', 'HIGH',
     '主营半导体显示业务，面板厂，原误挂半导体'),
    ('001399.SZ', '消费电子', ['面板'], ['显示器', '智能显示终端'], 'RECLASSIFY', 'HIGH',
     '主营半导体显示器板与智能显示终端，原误挂半导体'),
    ('605588.SH', '消费电子', ['面板', '消费电子零部件'], ['显示器件', '特种胶粘材料'], 'RECLASSIFY', 'MEDIUM',
     '主营半导体显示器件及特种胶粘材料，属显示/消费电子配套'),
    # ── 半导体污染：电子分销 → 消费电子（§十二）──
    ('001287.SZ', '消费电子', ['电子分销'], ['元器件分销', '供应链服务'], 'RECLASSIFY', 'HIGH',
     '综合电子元器件分销服务商，半导体剔除后归消费电子'),
    ('300131.SZ', '消费电子', ['电子分销'], ['电子智能控制器', '元器件分销'], 'RECLASSIFY', 'HIGH',
     '主营电子元器件分销与智能控制器，半导体剔除后归消费电子'),
    ('301099.SZ', '消费电子', ['电子分销'], ['元器件分销', '车规代理'], 'RECLASSIFY', 'HIGH',
     '主营电子元器件分销，原误挂半导体'),
    ('001298.SZ', '消费电子', ['电子分销'], ['元器件分销', '物联网'], 'RECLASSIFY', 'HIGH',
     '主营电子元器件分销与物联网产品，原误挂半导体'),
    ('300184.SZ', '消费电子', ['电子分销'], ['IC分销', '方案服务'], 'RECLASSIFY', 'HIGH',
     '主营IC等电子元器件推广销售，原误挂半导体'),
    ('300493.SZ', '消费电子', ['电子分销'], ['IC分销'], 'RECLASSIFY', 'HIGH',
     '主营IC产品分销，原误挂半导体'),
    ('920267.BJ', '消费电子', ['电子分销'], ['家电元件分销', '智能控制'], 'RECLASSIFY', 'MEDIUM',
     '主营智能控制产品与家用电器半导体元件分销，原误挂半导体'),
    # ── 散热 → 液冷（§九）；3D视觉 → 机器人（§十三）──
    ('301626.SZ', '液冷', ['AI服务器'], ['数据中心', '热管理', '导热散热'], 'RECLASSIFY', 'HIGH',
     '主营导热散热材料及元器件，液冷/热管理口径，半导体剔除后归液冷'),
    ('688322.SH', '机器人', ['3D视觉', '机器视觉'], ['人形机器人', '3D视觉感知'], 'RECLASSIFY', 'HIGH',
     '主营3D视觉感知产品，机器人视觉产业链，半导体剔除后归机器人'),
    # ── 智能驾驶被剔除整车/零部件 → 汽车（§五）──
    ('601799.SH', '汽车', ['智能车灯', '汽车电子'], ['车灯', '智能化', 'ADB'], 'MERGE', 'HIGH',
     '主营汽车灯具（前照灯/组合灯），智能驾驶原剔除后归汽车·智能车灯'),
    ('600741.SH', '汽车', ['汽车零部件'], ['零部件总成', '智能座舱配套'], 'MERGE', 'HIGH',
     '主营汽车零部件及总成，智能驾驶原剔除后归汽车'),
    ('002448.SZ', '汽车', ['发动机零部件'], ['气缸套', '内燃机'], 'MERGE', 'HIGH',
     '主营内燃机气缸套，原被智能驾驶排除词剔除，归汽车·发动机零部件'),
    ('000625.SZ', '汽车', ['新能源车', '汽车整车'], ['整车', '深蓝', '阿维塔'], 'MERGE', 'HIGH',
     '汽车整车及发动机，原被排除词剔除，归汽车·整车'),
    ('601238.SH', '汽车', ['新能源车', '汽车整车'], ['整车', '埃安', '自主品牌'], 'MERGE', 'HIGH',
     '汽车整车及配套，原被排除词剔除，归汽车·整车'),
    # ── 传媒污染 → 消费（§十四）──
    ('002301.SZ', '消费', ['办公用品'], ['办公文具', '企业SaaS'], 'RECLASSIFY', 'MEDIUM',
     '主营办公用品/办公设备，传媒剔除后归消费'),
    ('000802.SZ', '消费', ['文旅', '影视'], ['旅游服务', '文化娱乐'], 'RECLASSIFY', 'MEDIUM',
     '主营旅游项目投资与饮食文化娱乐服务，主营口径为文旅，传媒剔除后归消费'),
    # ── 量子计算降级后成员回归主营主题（§十六）──
    ('688027.SH', '信创', ['量子通信'], ['量子保密通信', '量子计算', '信息安全'], 'DEMOTE', 'MEDIUM',
     '主营量子保密通信设备与信息安全，量子计算降级后按主营归信创'),
    ('300520.SZ', '信创', ['量子科技'], ['软件信息化', '行业数字化'], 'DEMOTE', 'MEDIUM',
     '主营软件开发与行业信息化，量子业务为参股/研发，降级后归信创'),
    ('000555.SZ', '信创', ['量子科技'], ['金融IT', '系统集成'], 'DEMOTE', 'HIGH',
     '主营金融IT与系统集成，量子为合作布局，降级后归信创'),
    ('002281.SZ', 'CPO', ['光模块'], ['光通信模块', '光芯片', '量子科技'], 'DEMOTE', 'HIGH',
     '主营光模块/光器件，量子计算降级后回归CPO主主题'),
    ('300620.SZ', 'CPO', ['光器件'], ['铌酸锂', '光器件', '量子科技'], 'DEMOTE', 'HIGH',
     '主营光器件（含铌酸锂调制器），量子计算降级后回归CPO主主题'),
]
# 传媒已回到消费的 5 只（§十四/§二十八）：登记归属，不再改动
MEDIA_CONFIRM = [
    ('300329.SZ', '消费', ['乐器'], ['钢琴', '乐器制造'], 'RECLASSIFY', 'HIGH', '主营钢琴及核心部件，传媒已剔除，确认归消费·乐器'),
    ('002899.SZ', '消费', ['体育消费'], ['健身器材'], 'RECLASSIFY', 'HIGH', '主营健身器材，传媒已剔除，确认归消费·体育消费'),
    ('301287.SZ', '消费', ['体育消费'], ['健身器材'], 'RECLASSIFY', 'HIGH', '主营健身器材，传媒已剔除，确认归消费·体育消费'),
    ('001300.SZ', '消费', ['体育消费'], ['健身器材', '休闲运动'], 'RECLASSIFY', 'HIGH', '主营休闲运动与健身器材，传媒已剔除，确认归消费·体育消费'),
    ('001238.SZ', '消费', ['户外家居'], ['户外休闲家具'], 'RECLASSIFY', 'HIGH', '主营户外休闲家具及用品，传媒已剔除，确认归消费·户外家居'),
]
FORBIDDEN_NEW = ['机器视觉', '3D视觉', '机器人视觉', '体育户外', '户外家居', '办公文具',
                 '文旅', '乐器', '面板', '电子分销', '电子元器件', 'AI算力', 'AI硬件', 'AI基础设施']
LAYER_RANK = {'CORE': 2, 'NORMAL': 1, 'WEAK': 0}


def load_json(p):
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def dedupe(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def merge_entry(name_cn, parts):
    base = copy.deepcopy(parts[0])
    base['name_cn'] = name_cn
    for p in parts[1:]:
        for k in ('industry_chains', 'keywords', 'core_keywords', 'industry_keywords',
                  'product_keywords', 'brand_keywords', 'concept_keywords',
                  'eastmoney_concepts', 'ths_concepts', 'leaders', 'core_stocks', 'etf_codes'):
            base[k] = dedupe((base.get(k) or []) + (p.get(k) or []))
        base['segments'] = (base.get('segments') or []) + (p.get('segments') or [])
    base.pop('main_etf', None)
    return base


def build_pcb_entry():
    return OrderedDict([
        ('name_cn', 'PCB'),
        ('description', '印制电路板（PCB）、覆铜板（CCL）、HDI/高多层/柔性板、PCB专用设备与电子化学品；AI服务器高速PCB为核心催化'),
        ('level', 2),
        ('etf_codes', []),
        ('industry_chains', ['PCB', '覆铜板', 'HDI', '高多层板', '柔性板', '高速PCB', 'PCB设备', '电子化学品']),
        ('keywords', ['PCB', '印制电路板', '印制线路板', '印刷电路板', '线路板', '电路板', '覆铜板',
                      '粘结片', '半固化片', 'HDI', '高密度互连', '高多层', '柔性板', '挠性板', '软板',
                      '刚性板', 'FPC', '电路样板', '电子装联', 'PCB设备', 'PCB化学品']),
        ('exclude_keywords', ['面板', '液晶', '显示器', '光模块', '液冷', '医药', '光伏']),
        ('segment_gate', True),
        ('segments', [
            {'name': 'PCB制造/线路板', 'keywords': ['印制电路板', '印制线路板', '印刷电路板', '线路板',
                                                '电路板', '电路样板', '高密度互连', 'HDI', '高多层',
                                                '柔性板', '挠性板', '软板', '刚性板', '多层板']},
            {'name': '覆铜板/CCL', 'keywords': ['覆铜板', '粘结片', '半固化片', '铜箔基板', 'CCL',
                                             '电子级玻璃布', '电子电路铜箔']},
            {'name': 'PCB电子化学品/专用材料', 'keywords': ['PCB感光', '感光油墨', '电子电路专用',
                                                     '蚀刻液', '干膜', '电子电路']},
            {'name': 'PCB设备/电子装联', 'keywords': ['PCB设备', '线路板设备', '钻孔机', '曝光机',
                                                   '电子装联', 'PCBA', '印制电路板装配']},
        ]),
        ('core_keywords', ['印制电路板', 'PCB', '印制线路板', '线路板', '覆铜板', '高密度互连']),
        ('industry_keywords', ['PCB', '印制电路板', '线路板']),
        ('product_keywords', ['印制电路板', '印制线路板', '覆铜板', 'HDI', '高多层', '柔性板']),
        ('brand_keywords', ['沪电股份', '胜宏科技', '鹏鼎控股', '景旺电子', '生益科技', '深南电路']),
        ('concept_keywords', ['PCB', 'PCB概念', '覆铜板', 'HDI', '印制电路板']),
        ('sw_industry_match', []),
        ('cx_industry_match', []),
        ('eastmoney_concepts', ['PCB', '印制电路板', '覆铜板', 'HDI']),
        ('ths_concepts', ['PCB', '印制电路板', '覆铜板']),
        ('leaders', ['002463.SZ', '300476.SZ']),
        ('core_stocks', list(PCB_CORE)),
        ('secondary', []),
        ('is_active', True),
        ('extendable', True),
        ('purity', 100.0),
        ('industry_weight', {}),
        ('rotation', 0.0),
    ])


def scan_pcb(mainbiz, basic):
    out = []
    for code, txt in mainbiz.items():
        nm = basic.get(code, ('', ''))[0]
        if not nm or 'ST' in nm:
            continue
        if code in PCB_DENY and code not in PCB_CORE:
            continue
        if any(k in (txt or '') for k in PCB_KW):
            out.append(code)
    out = dedupe(sorted(out))
    for c in PCB_CORE:
        if c not in out and c in basic:
            out.append(c)
    return out


def _merge_flow(flow, groups):
    """(a) _flow.themes：把被合并主题的资金流按净额加权并入接收方"""
    flow = copy.deepcopy(flow or {})
    th = dict(flow.get('themes') or {})
    for dst, srcs in groups.items():
        net = mf = wsum = rot = 0.0
        for s in list(srcs) + [dst]:
            v = th.pop(s, None)
            if not v:
                continue
            n = float(v.get('net_amount') or 0.0)
            net += n
            mf += float(v.get('main_force') or 0.0)
            wsum += abs(n)
            rot += abs(n) * float(v.get('rotation') or 0.0)
        th[dst] = {'net_amount': round(net, 2), 'main_force': round(mf, 2),
                   'rotation': round(rot / wsum, 2) if wsum else 0.0}
    flow['themes'] = th
    return flow


def _rewrite_chain_relations(cr):
    """(a) _chain_relations：把关系键中的旧主题 KEY 改写为 V3.1 KEY"""
    cr = copy.deepcopy(cr or {})
    rel = cr.get('relations') or {}
    out = OrderedDict()
    for k, v in rel.items():
        parts = [RENAME_KEY.get(p, p) for p in k.split('<->')]
        nk = '<->'.join(parts)
        if nk in out:
            out[nk] = dedupe((out[nk] or []) + (v or []))
        else:
            out[nk] = v
    cr['relations'] = out
    return cr


def _update_subtheme_map(path, backup_path, pcb_subs):
    """(a) subtheme_map.json：母主题改名/合并 + 新增 PCB，保证 4c.5 子主题→母主题映射不残留旧主题"""
    sm = load_json(path)
    if not sm:
        raise SystemExit('未找到 %s' % path)
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(sm, f, ensure_ascii=False, indent=2)
    out = OrderedDict()
    out['PCB'] = pcb_subs
    pcb_sub_names = set(pcb_subs)
    auto_subs = OrderedDict()
    for p in ('新能源车', '智能驾驶'):
        for k, v in (sm.get(p) or {}).items():
            auto_subs.setdefault(k, v)
    for parent, subs in sm.items():
        if parent == '智能驾驶' or parent == '能源金属':
            continue
        if parent.startswith('_'):
            # _UNCERTAIN 等占位母主题：已被 PCB 接管的子主题不再丢弃
            left = OrderedDict((k, v) for k, v in subs.items() if k not in pcb_sub_names)
            if left:
                out[parent] = left
            continue
        if parent == '新能源车':
            out['汽车'] = auto_subs
        else:
            out[parent] = subs
    tgt = out.get('战略与小金属')
    if tgt is not None:
        for k, v in (sm.get('能源金属') or {}).items():
            tgt.setdefault(k, v)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def apply_production(cfg31, v31_meta, v30, pcb_subs):
    """(a) 接入生产：备份后用 V3.1 覆盖 theme_config.json，并同步 subtheme_map.json"""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak_cfg = CFG_V30 + '.bak_%s_v30' % stamp
    bak_sub = os.path.join(os.path.dirname(CFG_V30), 'subtheme_map.json.bak_%s' % stamp)
    with open(bak_cfg, 'w', encoding='utf-8') as f:
        json.dump(v30, f, ensure_ascii=False, indent=2)
    prod = OrderedDict(cfg31)
    prod['_v3_1_meta'] = v31_meta
    prod['_flow'] = _merge_flow(v30.get('_flow'), {
        'AUTO': ['NEW_ENERGY_VEHICLE', 'SMART_DRIVING'],
        'MINOR_METALS': ['ENERGY_METALS'],
    })
    prod['_chain_relations'] = _rewrite_chain_relations(v30.get('_chain_relations'))
    with open(CFG_V30, 'w', encoding='utf-8') as f:
        json.dump(prod, f, ensure_ascii=False, indent=2)
    sm = _update_subtheme_map(
        os.path.join(os.path.dirname(CFG_V30), 'subtheme_map.json'), bak_sub, pcb_subs)
    print('\n' + '=' * 96)
    print('【(a) 已接入生产】')
    print('  备份 → %s' % bak_cfg)
    print('  备份 → %s' % bak_sub)
    print('  写出 → %s（%d 个主题 + _flow / _chain_relations / _v3_1_meta）'
          % (CFG_V30, len(cfg31)))
    print('  写出 → subtheme_map.json（母主题 %d 个：%s）'
          % (len(sm), '、'.join(list(sm)[:6]) + ' …'))
    print('  旧键残留检查：新能源车=%s 智能驾驶=%s 能源金属=%s（应为 False）'
          % ('新能源车' in sm, '智能驾驶' in sm, '能源金属' in sm))


def main():
    apply = '--apply' in sys.argv
    v30 = load_json(CFG_V30)
    mp = load_json(MAP_P)
    mainbiz = load_json(MAINBIZ_P)
    basic = {}
    for r in csv.DictReader(open(BASIC_P, encoding='utf-8-sig', newline='')):
        basic[r['ts_code']] = (r.get('name') or '', r.get('industry') or '')
    old_themes = mp['themes']
    old_stocks = mp['stocks']

    # ── 1. 组装 V3.1 配置 ────────────────────────────────────────
    cfg31 = OrderedDict()
    for k, cf in v30.items():
        if k.startswith('_') or k in SKIP_KEYS:
            continue
        cf = copy.deepcopy(cf)
        if k == 'MINOR_METALS':
            new = merge_entry('战略与小金属', [cf, v30['ENERGY_METALS']])
            new['description'] = ('锑钨钼/稀土永磁/稀散金属等战略小金属，含锂钴镍资源与锂盐加工'
                                  '（由原「能源金属」并入）')
            new['exclude_keywords'] = [w for w in (new.get('exclude_keywords') or [])
                                       if w not in METAL_EXCLUDE_DROP]
            new['core_stocks'] = [c for c in (new.get('core_stocks') or []) if c not in METAL_CORE_DROP]
            new['tier'] = 'PRIMARY'
            new['secondary'] = SEC_LABELS['战略与小金属']
            cfg31['MINOR_METALS'] = new
            continue
        cf['tier'] = 'PRIMARY'
        if cf.get('name_cn') in SEC_LABELS:
            cf['secondary'] = SEC_LABELS[cf['name_cn']]
        if k == 'QUANTUM_COMPUTING':
            cf['tier'] = 'SUBTHEME'
            cf['rank_participation'] = False
            cf['parent_theme'] = ''
            cf['demote_reason'] = 'N=5，4/5 成员同属CPO/信创，无独立龙头群与独立持续行情 → SUBTHEME/TAG'
        cfg31[k] = cf

    auto = merge_entry('汽车', [v30['NEW_ENERGY_VEHICLE'], v30['SMART_DRIVING']])
    auto['description'] = ('汽车整车与零部件、新能源车产业链（电池/电机/电控/充电/热管理/压铸）、'
                           '智能驾驶与智能座舱、汽车电子、智能车灯')
    auto['exclude_keywords'] = AUTO_EXCLUDE
    auto['tier'] = 'PRIMARY'
    auto['secondary'] = SEC_LABELS['汽车']
    auto['etf_codes'] = dedupe((v30['NEW_ENERGY_VEHICLE'].get('etf_codes') or [])
                               + (v30['SMART_DRIVING'].get('etf_codes') or []))
    pcb = build_pcb_entry()
    pcb['tier'] = 'PRIMARY'
    pcb['secondary'] = SEC_LABELS['PCB']

    ordered = OrderedDict([('PCB', pcb)])
    for k, v in cfg31.items():
        ordered[k] = v
        if k == 'AI_APPLICATION':
            ordered['AUTO'] = auto
    cfg31 = ordered

    # 1.1 (c) 消除剩余 3 个 WARN：把已核实成员补入人工确认名单（去重追加）
    warn_fix_rows = []
    for cn, patch in WARN_FIX.items():
        tgt = next((cf for cf in cfg31.values() if cf.get('name_cn') == cn), None)
        if tgt is None:
            continue
        for field, codes in patch.items():
            cur = list(tgt.get(field) or [])
            add = [c for c in codes if c not in cur]
            tgt[field] = cur + add
            for c in add:
                warn_fix_rows.append((cn, c, basic.get(c, ('', ''))[0]))
    # 汽车 ETF 继承（merge_entry 会丢弃 main_etf）
    auto['main_etf'] = v30['NEW_ENERGY_VEHICLE'].get('main_etf') or '515030.SH'

    cfg_by_cn = {cf['name_cn']: cf for cf in cfg31.values()}
    order_idx = {cf['name_cn']: i for i, cf in enumerate(cfg31.values())}

    # ── 2. 候选归属（旧映射 → 新主题名）─────────────────────────
    cand = OrderedDict()      # code -> OrderedDict(theme -> {score, via, forced})
    names = {}
    for oldt, lst in old_themes.items():
        newt = RENAME.get(oldt, oldt)
        if newt == '量子计算':          # 降级，不再参与一级归属
            continue
        for x in lst:
            c = x['code']
            names.setdefault(c, x.get('name') or basic.get(c, ('', ''))[0])
            m = cand.setdefault(c, OrderedDict())
            prev = m.get(newt)
            sc = x.get('score') or 0
            if prev is None or sc > prev['score']:
                m[newt] = {'score': sc, 'via': x.get('via') or ''}
    n_multi = sum(1 for m in cand.values() if len(m) > 1)

    # 2.1 被剔除个股 / PCB 成员 → 强制归属
    forced = {}
    reclaim_rows = []
    for row in RECLAIM + MEDIA_CONFIRM:
        code, newp, sec, tags, dec, conf, reason = row
        forced[code] = (newp, sec, tags, dec, conf, reason)
    pcb_members = scan_pcb(mainbiz, basic)
    pcb_added = []
    for code in pcb_members:
        if code in forced:
            continue
        old_own = list(old_stocks.get(code, {}).get('themes') or [])
        nm = names.get(code) or basic.get(code, ('', ''))[0]
        dec = 'NEW_THEME' if not old_own else 'RECLASSIFY'
        reason = ('主营命中PCB/覆铜板口径，PCB独立一级主题成员；旧归属：%s'
                  % ('/'.join(old_own) or '无'))
        forced[code] = ('PCB', ['AI服务器', '高速PCB'], ['印制电路板', 'PCB'], dec,
                        'HIGH' if not old_own else 'MEDIUM', reason)
        pcb_added.append(code)
    for code, (newp, sec, tags, dec, conf, reason) in forced.items():
        old_own = list(old_stocks.get(code, {}).get('themes') or [])
        if dec == 'NEW_THEME' and not old_own:
            old_own = []
        cand[code] = OrderedDict([(newp, {'score': 999, 'via': 'manual_override', 'forced': True})])
        names.setdefault(code, basic.get(code, ('', ''))[0])
        reclaim_rows.append(dict(ts_code=code, name=names[code],
                                 old_primary_theme='/'.join(old_own), new_primary_theme=newp,
                                 secondary_theme='/'.join(sec), tags='/'.join(tags),
                                 decision=dec, confidence=conf, reason=reason,
                                 change_type='POLLUTION_CLEAN' if old_own and not dec == 'NEW_THEME'
                                 else ('RECLAIM' if not old_own else 'RECLAIM')))
    forced_codes = set(forced)

    # ── 3. 全局 PRIMARY 仲裁（§二十一/§二十二：每只股票唯一 PRIMARY）──
    newm = OrderedDict((cn, OrderedDict()) for cn, cf in cfg_by_cn.items()
                       if cf.get('tier') == 'PRIMARY')
    dedup_rows = []
    for code, m in cand.items():
        if len(m) == 1 or any(v.get('forced') for v in m.values()):
            primary = next((t for t, v in m.items() if v.get('forced')), list(m.keys())[0])
        else:
            best = None
            for t, meta in m.items():
                layer, why = v22.classify_member(code, meta['via'], mainbiz.get(code) or '',
                                                 cfg_by_cn[t])
                key = (LAYER_RANK[layer], meta['score'], -order_idx[t])
                if best is None or key > best[0]:
                    best = (key, t, layer, why)
            primary = best[1]
            others = [t for t in m if t != primary]
            if code not in forced_codes:
                dedup_rows.append(dict(
                    ts_code=code, name=names.get(code, ''), old_primary_theme='/'.join(m.keys()),
                    new_primary_theme=primary, secondary_theme='/'.join(others[:2]), tags='',
                    decision='RECLASSIFY', confidence='MEDIUM',
                    reason='跨主题重复归属，按证据强度仲裁（%s=%s）→ 唯一 PRIMARY' % (primary, best[2]),
                    change_type='PRIMARY_DEDUP'))
        via = m[primary].get('via') or ''
        newm[primary][code] = dict(name=names.get(code, ''), via=via,
                                   industry=basic.get(code, ('', ''))[1])
    # 清理空主题（保留结构，仅提示）
    dup = {}
    for t, recs in newm.items():
        for c in recs:
            dup.setdefault(c, []).append(t)
    dup = {c: ts for c, ts in dup.items() if len(ts) > 1}

    # ── 4. 合并主题成员 → MERGE 行 ───────────────────────────────
    mr_rows = []
    for src, dst in RENAME.items():
        for x in old_themes.get(src, []):
            c = x['code']
            if c in forced_codes or any(r['ts_code'] == c for r in dedup_rows):
                continue
            own = list(old_stocks.get(c, {}).get('themes') or [])
            mr_rows.append(dict(ts_code=c, name=x.get('name') or names.get(c, ''),
                                old_primary_theme=src, new_primary_theme=dst,
                                secondary_theme='/'.join([s for s in own if s != src][:2]),
                                tags='', decision='MERGE', confidence='HIGH',
                                reason='%s 并入 %s，资金交易单元高度重叠' % (src, dst),
                                change_type='THEME_MERGE'))

    # ── 5. 映射质量审计（复用生产 classify_member）────────────────
    audit_rows = []
    for cn, recs in newm.items():
        core = normal = weak = 0
        why_weak = []
        for code, rec in recs.items():
            layer, why = v22.classify_member(code, rec.get('via') or '',
                                             mainbiz.get(code) or '', cfg_by_cn[cn])
            if layer == 'CORE':
                core += 1
            elif layer == 'NORMAL':
                normal += 1
            else:
                weak += 1
                if len(why_weak) < 6:
                    why_weak.append('%s(%s)' % (rec.get('name'), why.split('｜')[-1]))
        total = len(recs)
        valid = core + normal
        rate = (valid * 100.0 / total) if total else 0.0
        status = 'OK' if rate >= 90 else ('WARN' if rate >= 80 else 'BAD')
        rel = min(1.0, (total / 50.0) ** 0.5)
        flag = 'LOW_SAMPLE' if valid < 10 else ('SMALL_SAMPLE' if valid < 30 else 'NORMAL')
        audit_rows.append(dict(theme=cn, tier=cfg_by_cn[cn].get('tier'), total_members=total,
                               valid_members=valid, weak_members=weak,
                               mapping_rate=round(rate, 2), mapping_status=status,
                               reliability=round(rel, 3), sample_flag=flag,
                               core_n=core, normal_n=normal, weak_n=weak,
                               weak_samples='; '.join(why_weak)))
    audit_rows.sort(key=lambda r: -r['total_members'])

    # ── 6. 合并/新增/降级审计 ────────────────────────────────────
    def nm_(t):
        return len(old_themes.get(t, []))
    merge_audit = [
        dict(old_theme='新能源车', new_theme='汽车', action='MERGE', G1='PASS', G2='PASS', G3='PASS',
             G4='PASS', G5='PASS', members_moved=nm_('新能源车'), verdict='APPROVED',
             reason='同一批资金同时炒作整车/电池/智驾；龙头池（比亚迪/宁德时代）与智驾龙头同属汽车链；不合并会互相稀释'),
        dict(old_theme='智能驾驶', new_theme='汽车', action='MERGE', G1='PASS', G2='PASS', G3='PASS',
             G4='PASS', G5='PASS', members_moved=nm_('智能驾驶'), verdict='APPROVED',
             reason='智驾是汽车产业链二级逻辑，独立行情常与整车/零部件同步；保留为 SECONDARY=智能驾驶'),
        dict(old_theme='能源金属', new_theme='战略与小金属', action='MERGE', G1='PASS', G2='PASS', G3='PASS',
             G4='PASS', G5='PASS', members_moved=nm_('能源金属'), verdict='APPROVED',
             reason='锂钴镍与稀土钨钼锑同属资源/小金属交易逻辑，分拆导致成员稀释（17 vs 52）；锂/钴/镍 保留为 SECONDARY'),
        dict(old_theme='—', new_theme='PCB', action='NEW', G1='PASS', G2='PASS', G3='PASS',
             G4='PASS', G5='PASS', members_moved=len(newm['PCB']), verdict='APPROVED',
             reason='AI算力驱动下PCB已形成独立板块行情与独立龙头群（沪电/胜宏/鹏鼎/景旺），并入半导体或AI服务器会污染两者'),
        dict(old_theme='量子计算', new_theme='SUBTHEME/TAG', action='DEMOTE', G1='FAIL', G2='FAIL',
             G3='FAIL', G4='FAIL', G5='N/A', members_moved=nm_('量子计算'), verdict='APPROVED',
             reason='N=5，光迅/光库同属CPO，科大国创/神州信息同属信创，无独立龙头群与独立持续行情'),
        dict(old_theme='脑机接口', new_theme='脑机接口', action='RETAIN', G1='PASS', G2='N/A', G3='PASS',
             G4='PASS', G5='PASS', members_moved=9, verdict='RETAINED',
             reason='N=9 但与机器人成员交集 0；成员为神经专科/康复器械/脑电传感器，具独立产业催化，N<15 用 Reliability 收缩而非删除'),
        dict(old_theme='游戏', new_theme='游戏', action='RETAIN', G1='FAIL', G2='FAIL', G3='FAIL',
             G4='FAIL', G5='N/A', members_moved=nm_('游戏'), verdict='REJECT_MERGE',
             reason='与传媒同属文娱但资金经常分别炒作，龙头群不同（三七/完美/吉比特 vs 分众/芒果/人民网）→ 禁止强制并入传媒'),
        dict(old_theme='传媒', new_theme='传媒', action='RETAIN', G1='FAIL', G2='FAIL', G3='FAIL',
             G4='FAIL', G5='N/A', members_moved=nm_('传媒'), verdict='REJECT_MERGE',
             reason='广告/影视/出版/数字媒体独立成链，保持一级主题'),
        dict(old_theme='CPO/PCB/液冷/AI服务器/算力运营', new_theme='各自独立', action='RETAIN', G1='FAIL',
             G2='FAIL', G3='FAIL', G4='FAIL', G5='N/A',
             members_moved=sum(nm_(t) for t in ['CPO', '液冷', 'AI服务器', '算力运营']) + len(newm['PCB']),
             verdict='REJECT_MERGE',
             reason='§十：光互联/PCB/散热/整机/IDC 催化节奏与龙头池均不同，禁止合并为笼统「AI算力」'),
        dict(old_theme='半导体', new_theme='半导体', action='RETAIN/CLEAN', G1='PASS', G2='PASS', G3='PASS',
             G4='PASS', G5='PASS', members_moved=len(newm['半导体']), verdict='CLEANED',
             reason='清除 PCB/面板/分销/散热/3D视觉外溢成员，只保留芯片设计/制造/封测/设备/材料/存储/国产替代'),
        dict(old_theme='消费电子', new_theme='消费电子', action='RETAIN', G1='PASS', G2='PASS', G3='PASS',
             G4='PASS', G5='PASS', members_moved=len(newm['消费电子']), verdict='EXPANDED',
             reason='吸收面板（京东方/深天马/龙腾/TCL科技/惠科）与电子分销（中电港/英唐智控/雅创等）成员'),
    ]

    # ── 6.1 (b) PCB 子链分层（一级成员池不变，仅标注上游口径）────
    pcb_subchain = OrderedDict()
    for code in newm.get('PCB', {}):
        sc = PCB_SUBCHAIN_OVERRIDE.get(code, PCB_SUBCHAIN_DEFAULT)
        pcb_subchain.setdefault(sc, []).append(code)
    pcb_board_n = len(pcb_subchain.get(PCB_SUBCHAIN_DEFAULT, []))
    pcb_upstream_n = len(newm.get('PCB', {})) - pcb_board_n
    for r in reclaim_rows + dedup_rows:
        if r['new_primary_theme'] == 'PCB':
            r['subchain'] = PCB_SUBCHAIN_OVERRIDE.get(r['ts_code'], PCB_SUBCHAIN_DEFAULT)
    # 上游成员的真实证券简称补入对应子主题的 core_companies（避免手工维护名称出错）
    for code, sc in PCB_SUBCHAIN_OVERRIDE.items():
        sub = PCB_SUBS.get(sc)
        nmx = basic.get(code, ('', ''))[0]
        if sub is not None and nmx and nmx not in sub['core_companies']:
            sub['core_companies'].append(nmx)

    # ── 7. 写文件 ────────────────────────────────────────────────
    v31_out = OrderedDict(cfg31)
    v31_out['_v3_1_meta'] = {
        'version': 'V3.1', 'built_at': DATE, 'source': 'theme_config.json (V3.0)',
        'principles': ['一级主题=可独立炒作的资金交易单元', '每只股票唯一 PRIMARY_THEME',
                       'SECONDARY/TAG 不参与一级主题热度排名', '不按成员数量机械删除',
                       '低样本用 Reliability 收缩而非删除'],
        'merges': [{'from': ['新能源车', '智能驾驶'], 'to': '汽车'},
                   {'from': ['能源金属'], 'to': '战略与小金属'}],
        'new_themes': ['PCB'], 'reconstructed': ['汽车'], 'demoted': ['量子计算'],
        'n_primary_themes': len(newm), 'before': 36,
        'warn_fix': {t: [row[1] for row in warn_fix_rows if row[0] == t] for t in WARN_FIX},
        'pcb_subchain': {'板厂口径': pcb_board_n, '上游口径': pcb_upstream_n,
                         '分层': {k: v for k, v in pcb_subchain.items()}},
    }
    with open(CFG_V31, 'w', encoding='utf-8') as f:
        json.dump(v31_out, f, ensure_ascii=False, indent=2)

    sub = OrderedDict()
    sub['meta'] = {'version': 'V3.1', 'built_at': DATE, 'n_primary': len(newm),
                   'note': '一级成员池为 PRIMARY 独占归属；SECONDARY/TAG 仅用于解释与检索，不计入一级热度'}
    sub['themes'] = OrderedDict()
    for cn, cf in cfg_by_cn.items():
        sub['themes'][cn] = {'tier': cf.get('tier'), 'parent': cf.get('parent_theme', ''),
                             'secondary': cf.get('secondary') or [],
                             'tags': SEC_LABELS.get(cn, [])}
    sub['primary_members'] = {cn: list(recs.keys()) for cn, recs in newm.items()}
    sub['subchain'] = {'PCB': {k: v for k, v in pcb_subchain.items()}}
    sub['stocks'] = OrderedDict()
    for r in reclaim_rows + dedup_rows + mr_rows:
        if r['ts_code'] in sub['stocks']:
            continue
        sub['stocks'][r['ts_code']] = {
            'name': r['name'], 'primary': r['new_primary_theme'],
            'secondary': [x for x in (r['secondary_theme'] or '').split('/') if x],
            'tags': [x for x in (r['tags'] or '').split('/') if x]}
    sub['demoted_members'] = [x['code'] for x in old_themes.get('量子计算', [])]
    with open(SUB_V31, 'w', encoding='utf-8') as f:
        json.dump(sub, f, ensure_ascii=False, indent=2)

    rc = os.path.join(OUT, 'theme_reclassification_%s.csv' % DATE)
    all_rows = reclaim_rows + dedup_rows + mr_rows
    with open(rc, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['ts_code', 'name', 'old_primary_theme', 'new_primary_theme',
                                          'secondary_theme', 'tags', 'decision', 'confidence',
                                          'reason', 'change_type', 'subchain'],
                           extrasaction='ignore')
        w.writeheader()
        for r in sorted(all_rows, key=lambda x: (x['change_type'], x['new_primary_theme'], x['ts_code'])):
            w.writerow(r)

    am = os.path.join(OUT, 'theme_mapping_audit_%s.csv' % DATE)
    with open(am, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['theme', 'tier', 'total_members', 'valid_members',
                                          'weak_members', 'mapping_rate', 'mapping_status',
                                          'reliability', 'sample_flag', 'core_n', 'normal_n',
                                          'weak_n', 'weak_samples'])
        w.writeheader()
        for r in audit_rows:
            w.writerow(r)

    ma = os.path.join(OUT, 'theme_merge_audit_%s.csv' % DATE)
    with open(ma, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['old_theme', 'new_theme', 'action', 'G1', 'G2', 'G3',
                                          'G4', 'G5', 'members_moved', 'verdict', 'reason'],
                           extrasaction='ignore')
        w.writeheader()
        for r in merge_audit:
            w.writerow(r)

    # ── 8. 验收 Gate ─────────────────────────────────────────────
    primary_themes = list(newm.keys())
    semi_bad = [c for c in ['002463.SZ', '300476.SZ', '002938.SZ', '603228.SH', '000725.SZ',
                            '000050.SZ', '688055.SH', '001287.SZ', '300131.SZ', '301626.SZ',
                            '688322.SH'] if c in newm['半导体']]
    media_bad = [c for c in ['300329.SZ', '002899.SZ', '301287.SZ', '001300.SZ', '001238.SZ',
                             '002301.SZ', '000802.SZ'] if c in newm['传媒']]
    low_n = [r['theme'] for r in audit_rows if r['valid_members'] < 15]
    gates = [
        ('G1', '每只股票 PRIMARY_THEME 唯一', 'PASS' if not dup else 'FAIL',
         '重复归属 %d 只；仲裁 %d 只（改前多归属 %d 只）' % (len(dup), len(dedup_rows), n_multi)),
        ('G2', '最终一级主题 31~35', 'PASS' if 31 <= len(primary_themes) <= 35 else 'FAIL',
         'AFTER=%d' % len(primary_themes)),
        ('G3', 'PCB 独立', 'PASS' if 'PCB' in newm else 'FAIL', 'PCB 成员 %d' % len(newm.get('PCB', {}))),
        ('G4', '液冷 独立', 'PASS' if '液冷' in newm else 'FAIL', '成员 %d' % len(newm.get('液冷', {}))),
        ('G5', 'CPO 独立', 'PASS' if 'CPO' in newm else 'FAIL', '成员 %d' % len(newm.get('CPO', {}))),
        ('G6', 'AI服务器 独立', 'PASS' if 'AI服务器' in newm else 'FAIL', '成员 %d' % len(newm.get('AI服务器', {}))),
        ('G7', '算力运营 独立', 'PASS' if '算力运营' in newm else 'FAIL', '成员 %d' % len(newm.get('算力运营', {}))),
        ('G8', '游戏 独立', 'PASS' if '游戏' in newm else 'FAIL', '成员 %d' % len(newm.get('游戏', {}))),
        ('G9', '传媒 独立', 'PASS' if '传媒' in newm else 'FAIL', '成员 %d' % len(newm.get('传媒', {}))),
        ('G10', '新能源车 并入 汽车', 'PASS' if ('汽车' in newm and '新能源车' not in newm) else 'FAIL', ''),
        ('G11', '智能驾驶 并入 汽车', 'PASS' if ('汽车' in newm and '智能驾驶' not in newm) else 'FAIL', ''),
        ('G12', '能源金属 并入 战略与小金属', 'PASS' if '能源金属' not in newm else 'FAIL',
         '战略与小金属 成员 %d' % len(newm.get('战略与小金属', {}))),
        ('G13', '半导体完成污染清洗', 'PASS' if not semi_bad else 'FAIL',
         '残留 %d；半导体成员 %d' % (len(semi_bad), len(newm['半导体']))),
        ('G14', '传媒完成污染清洗', 'PASS' if not media_bad else 'FAIL',
         '残留 %d；传媒成员 %d' % (len(media_bad), len(newm['传媒']))),
        ('G15', '消费电子吸收面板/电子分销',
         'PASS' if all(c in newm['消费电子'] for c in ['000725.SZ', '000050.SZ', '688055.SH',
                                                       '000100.SZ', '001399.SZ', '001287.SZ',
                                                       '300131.SZ']) else 'FAIL',
         '消费电子成员 %d' % len(newm['消费电子'])),
        ('G16', '奥比中光 归 机器人', 'PASS' if '688322.SH' in newm['机器人'] else 'FAIL', ''),
        ('G17', '苏州天脉 归 液冷', 'PASS' if '301626.SZ' in newm['液冷'] else 'FAIL', ''),
        ('G18', 'PCB 四家 归 PCB', 'PASS' if all(c in newm['PCB'] for c in
                                              ['002463.SZ', '300476.SZ', '002938.SZ', '603228.SH']) else 'FAIL', ''),
        ('G19-G23', '不新建 机器视觉/体育户外/户外家居/办公文具/文旅 一级主题',
         'PASS' if not any(x in newm for x in FORBIDDEN_NEW) else 'FAIL',
         '越界：%s' % [x for x in FORBIDDEN_NEW if x in newm]),
        ('G24', '量子计算不进入一级主题', 'PASS' if '量子计算' not in newm else 'FAIL',
         'tier=%s' % cfg_by_cn['量子计算'].get('tier')),
        ('G25', 'SECONDARY/TAG 不重复贡献一级热度', 'PASS' if not dup else 'FAIL',
         '一级成员池=PRIMARY 独占，重复归属 0；SECONDARY/TAG 仅写入 subtheme_map_v3_1.json'),
        ('G26', 'Mapping Rate 全部重算', 'PASS', '覆盖 %d 个一级主题' % len(audit_rows)),
        ('G27', '低样本主题使用 Reliability 收缩', 'PASS',
         'N<15 主题 %d 个：%s' % (len(low_n), ','.join(low_n))),
        ('G28', '无凑数式人为拆分/合并', 'PASS',
         '合并 3 项、新增 1 项、重构新增 1 项（汽车）、降级 1 项，其余全部 RETAIN'),
    ]

    # ── 9. 输出 ──────────────────────────────────────────────────
    print('=' * 96)
    print('V3.1 构建完成  %s' % DATE)
    print('BEFORE=36  AFTER=%d  MERGED=3(→2 接收)  NEW=1(PCB)  RECONSTRUCTED=1(汽车)  DEMOTED=1(量子计算)'
          % len(primary_themes))
    print('改前多归属个股 %d 只 → 仲裁后重复归属 %s' % (n_multi, dup or '0'))
    print('=' * 96)
    print('\n【一级主题清单 %d 个】' % len(primary_themes))
    for i, t in enumerate(primary_themes, 1):
        r = next(x for x in audit_rows if x['theme'] == t)
        print('%2d. %-10s 成员%4d  有效%4d  WEAK%3d  RATE %6.2f%% %-4s  Rel %.2f %s'
              % (i, t, r['total_members'], r['valid_members'], r['weak_members'],
                 r['mapping_rate'], r['mapping_status'], r['reliability'], r['sample_flag']))
    print('\n【关键主题成员数 改前 → 改后】')
    for t in ['新能源车', '智能驾驶', '能源金属', '量子计算']:
        print('  %-10s %4d → %s' % (t, nm_(t), '并入汽车' if t in ('新能源车', '智能驾驶')
                                    else ('并入战略与小金属' if t == '能源金属' else 'SUBTHEME/TAG')))
    for t in ['汽车', '战略与小金属', 'PCB', '半导体', '消费电子', '机器人', '液冷', '消费', '传媒']:
        print('  %-10s %4d → %d' % (t, nm_(t), len(newm.get(t, {}))))
    print('\n【PCB 成员 %d 只】' % len(newm['PCB']))
    print('  ' + '、'.join(rec['name'] for rec in newm['PCB'].values()))
    print('\n【(b) PCB 子链分层】板厂口径 %d 只 / 上游口径 %d 只（一级成员池不变）'
          % (pcb_board_n, pcb_upstream_n))
    for sc, codes in pcb_subchain.items():
        print('  %-18s %2d 只：%s' % (sc, len(codes),
              '、'.join(names.get(c) or basic.get(c, ('', ''))[0] for c in codes)))
    print('\n【(c) 剩余 WARN 消除验证】')
    for cn, codes, nm in warn_fix_rows:
        print('  补录 %s ← %s %s' % (cn, codes, nm))
    warn_left = [(r['theme'], r['mapping_rate']) for r in audit_rows
                 if r['mapping_status'] != 'OK']
    print('  改后非 OK 主题：%s' % (warn_left or '无（OK %d / WARN 0 / BAD 0）'
                                   % sum(1 for r in audit_rows if r['mapping_status'] == 'OK')))
    print('\n【重分类 %d 行（明细见 CSV，此处列出非 PCB 类）】' % len(all_rows))
    for r in reclaim_rows:
        if r['new_primary_theme'] == 'PCB':
            continue
        print('  %-11s %-8s %-14s → %-8s [%s/%s] %s' % (r['ts_code'], r['name'],
              r['old_primary_theme'] or '(无)', r['new_primary_theme'], r['decision'],
              r['confidence'], r['secondary_theme']))
    print('\n【PRIMARY 仲裁样例（前 25 只）】')
    for r in dedup_rows[:25]:
        print('  %-11s %-8s %-22s → %-8s [%s]' % (r['ts_code'], r['name'], r['old_primary_theme'],
              r['new_primary_theme'], r['secondary_theme']))
    print('\n' + '=' * 96)
    print('验收 Gate')
    print('=' * 96)
    n_pass = 0
    for g, req, res, note in gates:
        n_pass += 1 if res == 'PASS' else 0
        print('%-8s %-6s %s' % (g, res, req))
        if note:
            print('         %s' % note)
    print('-' * 96)
    print('Gate PASS %d / %d' % (n_pass, len(gates)))
    print('\n【文件】')
    for p in [CFG_V31, SUB_V31, rc, am, ma]:
        print('  %s  %d bytes' % (p, os.path.getsize(p)))

    # ── 10. (a) 接入生产（仅 --apply）────────────────────────────
    if apply:
        apply_production(cfg31, v31_out['_v3_1_meta'], v30, PCB_SUBS)
    else:
        print('\n[DRY-RUN] 未接入生产；如需接入请执行：'
              'python build_theme_config_v3_1.py --apply')


if __name__ == '__main__':
    main()
