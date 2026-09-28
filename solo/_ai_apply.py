# -*- coding: utf-8 -*-
"""AI算力「拆旧建新」配置层改造：
   1) 从 theme_config.json 删除顶层 key AI_COMPUTE（原一级主题「AI算力」）
   2) 原位插入 4 个独立新主题：CPO / 液冷 / AI服务器 / AI应用
   3) 从 _flow.themes 删除 AI_COMPUTE 元数据条目
   采用「文本切片替换」而非 json.dump 整体重写，保证其余内容逐字节不变。
"""
import json, os, shutil

P = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json'
BAK = P + '.bak_20260928_aisplit'

raw = open(P, 'rb').read()
crlf = b'\r\n' in raw
txt = raw.decode('utf-8-sig')
nl = '\r\n' if crlf else '\n'


def jb(key, obj):
    """dict -> 顶层缩进(2空格)的 JSON 块（末行无逗号）"""
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return nl.join('  ' + l if l else l for l in s.split('\n'))


CPO = {
    "name_cn": "CPO",
    "description": "光模块、硅光、光电共封装(CPO)、800G/1.6T高速光互联、光芯片及CPO产业链",
    "level": 2,
    "etf_codes": [],
    "industry_chains": ["光模块", "硅光", "CPO", "共封装光学", "光电共封装", "光芯片",
                        "光器件", "光无源器件", "高速光互联", "光引擎", "光收发模块"],
    "keywords": ["光模块", "CPO", "共封装光学", "光电共封装", "硅光", "硅光子", "光芯片",
                 "光器件", "光无源器件", "无源光器件", "光电子器件", "光收发模块", "收发模块",
                 "光引擎", "高速光互联", "光互联"],
    "exclude_keywords": ["光纤预制棒", "光纤光缆", "光纤陀螺", "通信电缆", "电力电缆",
                         "印制电路", "线路板", "覆铜板", "液冷", "算力租赁", "网络安全",
                         "手机售后", "智能网络摄像机"],
    "segment_gate": True,
    "segments": [
        {"name": "光模块/光收发",
         "keywords": ["光模块", "光收发模块", "收发模块", "光收发", "光引擎", "光互联",
                      "高速光互联", "MPO", "AOC", "光通信"]},
        {"name": "光芯片/光器件",
         "keywords": ["光芯片", "光器件", "光无源器件", "无源光器件", "光电子器件", "光电子",
                      "光学元器件", "光纤器件", "光电子器件"]},
        {"name": "CPO/硅光",
         "keywords": ["CPO", "共封装光学", "光电共封装", "硅光", "硅光子", "光电合封"]},
    ],
    "core_keywords": ["光模块", "CPO", "硅光", "光芯片"],
    "industry_keywords": ["光模块", "光通信", "光器件", "光芯片"],
    "product_keywords": ["光模块", "光芯片", "光器件", "光收发模块"],
    "brand_keywords": [],
    "concept_keywords": ["CPO", "光模块", "硅光", "光通信"],
    "sw_industry_match": ["通信", "电子"],
    "cx_industry_match": ["通信", "电子"],
    "eastmoney_concepts": ["CPO", "光通信", "光模块", "硅光"],
    "ths_concepts": ["CPO", "光模块", "硅光"],
    "leaders": ["300308.SZ", "300502.SZ"],
    "core_stocks": ["002281.SZ", "300394.SZ", "301205.SZ", "920045.BJ", "688313.SH",
                    "688205.SH", "300548.SZ", "300570.SZ", "600498.SH",
                    "000988.SZ", "688498.SH", "300620.SZ", "688195.SH"],
    "secondary": [],
    "is_active": True,
    "extendable": True,
    "purity": 100.0,
    "industry_weight": {},
    "rotation": 0.0,
}

LIQUID_COOLING = {
    "name_cn": "液冷",
    "description": "AI数据中心液冷、冷板液冷、浸没式液冷、CDU、液冷管路/连接件、数据中心散热系统",
    "level": 2,
    "etf_codes": [],
    "industry_chains": ["液冷", "冷板液冷", "浸没式液冷", "CDU", "冷量分配单元", "液冷管路",
                        "液冷板", "快接头", "数据中心温控", "机房精密空调", "数据中心散热",
                        "冷却系统"],
    "keywords": ["液冷", "冷板", "浸没式", "浸没相变", "CDU", "冷量分配", "液冷板",
                 "液冷管路", "快接头", "数据中心温控", "机房精密空调", "精密温控",
                 "冷却系统", "温控设备"],
    "exclude_keywords": ["汽车零部件", "汽车热管理", "车用", "内燃机", "摩托车",
                         "家用空调", "冰箱", "家电", "制冷剂", "印制电路", "光模块",
                         "算力租赁", "网络安全", "手机售后"],
    "segment_gate": True,
    "segments": [
        {"name": "液冷设备/系统",
         "keywords": ["液冷", "冷板液冷", "浸没式液冷", "浸没相变", "液冷系统", "液冷设备",
                      "液冷机组", "冷板", "液冷板", "CDU", "冷量分配单元", "冷却液分配",
                      "纯水冷却"]},
        {"name": "数据中心温控/散热",
         "keywords": ["数据中心温控", "机房精密空调", "精密温控", "数据中心散热",
                      "数据中心冷却", "机房空调", "温控设备", "精密环境控制",
                      "人工环境调控"]},
        {"name": "液冷管路/连接件",
         "keywords": ["液冷管路", "液冷管", "快接头", "液冷连接器", "冷却管路", "液冷软管",
                      "专用性空调"]},
    ],
    "core_keywords": ["液冷", "冷板", "浸没式液冷", "CDU"],
    "industry_keywords": ["液冷", "数据中心温控", "精密温控"],
    "product_keywords": ["液冷设备", "冷板", "CDU", "液冷管路"],
    "brand_keywords": [],
    "concept_keywords": ["液冷", "数据中心温控", "液冷服务器"],
    "sw_industry_match": ["机械", "电力设备", "电气设备", "电子", "通信"],
    "cx_industry_match": ["机械", "电力设备", "电气设备", "电子", "通信"],
    "eastmoney_concepts": ["液冷", "数据中心", "温控"],
    "ths_concepts": ["液冷", "液冷服务器"],
    "leaders": ["002837.SZ"],
    "core_stocks": ["920808.BJ", "301202.SZ", "300499.SZ", "301018.SZ", "300990.SZ",
                    "603912.SH"],
    "secondary": [],
    "is_active": True,
    "extendable": True,
    "purity": 100.0,
    "industry_weight": {},
    "rotation": 0.0,
}

AI_SERVER = {
    "name_cn": "AI服务器",
    "description": "AI GPU服务器、AI训练/推理服务器、服务器整机、AI算力基础设施、服务器ODM/OEM及核心配套",
    "level": 2,
    "etf_codes": [],
    "industry_chains": ["AI服务器", "GPU服务器", "训练服务器", "推理服务器", "服务器整机",
                        "高性能计算机", "超算", "整机柜", "服务器ODM", "服务器OEM",
                        "智算一体机"],
    "keywords": ["AI服务器", "GPU服务器", "服务器", "高性能计算机", "通用服务器",
                 "服务器整机", "整机柜", "超算", "服务器ODM", "服务器OEM", "智算一体机",
                 "算力整机"],
    "exclude_keywords": ["印制电路", "线路板", "覆铜板", "光模块", "光纤光缆", "液冷",
                         "算力租赁", "网络安全", "手机售后", "智能网络摄像机"],
    "segment_gate": True,
    "segments": [
        {"name": "AI服务器整机",
         "keywords": ["AI服务器", "GPU服务器", "训练服务器", "推理服务器", "服务器整机",
                      "整机柜", "智算一体机", "算力整机", "AI一体机"]},
        {"name": "服务器/整机",
         "keywords": ["服务器", "高性能计算机", "通用服务器", "超算", "超级计算机"]},
        {"name": "服务器配套",
         "keywords": ["服务器电源", "服务器主板", "服务器机箱", "服务器ODM", "服务器OEM",
                      "服务器代工"]},
    ],
    "core_keywords": ["AI服务器", "服务器", "高性能计算机"],
    "industry_keywords": ["服务器", "高性能计算机", "AI服务器"],
    "product_keywords": ["AI服务器", "服务器整机", "GPU服务器"],
    "brand_keywords": [],
    "concept_keywords": ["服务器", "AI服务器", "算力"],
    "sw_industry_match": ["计算机", "IT设备", "电子", "通信"],
    "cx_industry_match": ["计算机", "IT设备", "电子", "通信"],
    "eastmoney_concepts": ["服务器", "算力", "数据中心"],
    "ths_concepts": ["服务器", "算力"],
    "leaders": ["000977.SZ"],
    "core_stocks": ["603019.SH", "601138.SH"],
    "secondary": [],
    "is_active": True,
    "extendable": True,
    "purity": 100.0,
    "industry_weight": {},
    "rotation": 0.0,
}

AI_APPLICATION = {
    "name_cn": "AI应用",
    "description": "AI软件、AI Agent、AI办公、AI医疗、AI教育、AI营销、AI客服、AI内容生成等直接面向应用层的商业化业务",
    "level": 2,
    "etf_codes": [],
    "industry_chains": ["AI软件", "AI Agent", "AI办公", "AI医疗", "AI教育", "AI营销",
                        "AI客服", "AI内容生成", "AI应用平台", "大模型应用", "行业大模型",
                        "机器视觉", "语音识别", "自然语言处理"],
    "keywords": ["人工智能", "AI应用", "AI软件", "AI Agent", "智能体", "大模型", "AIGC",
                 "AI办公", "AI医疗", "AI教育", "AI营销", "AI客服", "AI内容生成",
                 "机器视觉", "计算机视觉", "语音识别", "自然语言处理", "模式识别",
                 "智能交互", "人工智能解决方案"],
    "exclude_keywords": ["光模块", "光纤光缆", "印制电路", "线路板", "覆铜板", "液冷",
                         "服务器制造", "芯片设计", "IDC", "算力租赁", "房地产开发",
                         "酒类", "半导体制造"],
    "segment_gate": True,
    "segments": [
        {"name": "AI软件/平台",
         "keywords": ["人工智能", "AI应用", "AI软件", "AI Agent", "智能体", "大模型",
                      "AIGC", "AI平台", "人工智能解决方案", "视觉人工智能"]},
        {"name": "AI办公/企业应用",
         "keywords": ["办公软件", "协同管理软件", "企业管理软件", "AI办公", "智能办公",
                      "文档处理"]},
        {"name": "行业AI应用",
         "keywords": ["AI医疗", "医疗信息化", "医疗软件", "AI教育", "教育信息化",
                      "考试信息化", "AI营销", "AI客服", "智能客服", "金融资讯",
                      "金融软件", "消费类软件", "模式识别", "智能交互",
                      "非结构化信息处理", "文本挖掘"]},
    ],
    "core_keywords": ["人工智能", "AI应用", "大模型", "机器视觉"],
    "industry_keywords": ["人工智能", "软件", "计算机应用"],
    "product_keywords": ["AI应用", "大模型", "AI Agent"],
    "brand_keywords": [],
    "concept_keywords": ["人工智能", "AI应用", "大模型", "AIGC"],
    # AI应用 采用「人工核验名单」口径：不设行业/概念自动通道，避免关键词机械扩张
    "sw_industry_match": [],
    "cx_industry_match": [],
    "eastmoney_concepts": [],
    "ths_concepts": [],
    "leaders": ["002230.SZ"],
    "core_stocks": ["688088.SH", "300229.SZ", "002362.SZ", "300624.SZ", "688111.SH",
                    "300559.SZ", "300418.SZ", "300033.SZ"],
    "secondary": [],
    "is_active": True,
    "extendable": True,
    "purity": 100.0,
    "industry_weight": {},
    "rotation": 0.0,
}

NEW = [("CPO", CPO), ("LIQUID_COOLING", LIQUID_COOLING),
       ("AI_SERVER", AI_SERVER), ("AI_APPLICATION", AI_APPLICATION)]

# --- 1) 备份 ---
shutil.copy2(P, BAK)
print('备份 ->', os.path.basename(BAK))

# --- 2) 替换顶层 AI_COMPUTE 块 ---
s = txt.index('  "AI_COMPUTE": {')
e = txt.index('  "SEMICONDUCTOR": {')
assert s < e, '定位失败'
blocks = []
for k, obj in NEW:
    body = jb(k, obj)          # 已按 2 空格缩进的多行块，以 '  {' 开头
    lines = body.split(nl)
    lines[0] = '  "%s": %s' % (k, lines[0].lstrip())   # 首行 '  {' -> '  "KEY": {'
    lines[-1] = lines[-1] + ','                        # 顶层条目间需逗号
    blocks.append(nl.join(lines))
new_text = nl.join(blocks) + nl
txt = txt[:s] + new_text + txt[e:]

# --- 3) 删除 _flow.themes 内的 AI_COMPUTE 条目 ---
fs = txt.index('      "AI_COMPUTE": {')
fe = txt.index('      },', fs) + len('      },')
# 连同其后换行一并删除
if txt[fe:fe + len(nl)] == nl:
    fe += len(nl)
txt = txt[:fs] + txt[fe:]

# --- 4) 先校验再写回 ---
chk = json.loads(txt)
raw2 = txt.encode('utf-8')
crlf2 = b'\r\n' in raw2
with open(P, 'w', encoding='utf-8', newline='') as f:
    f.write(txt)
print('CRLF 保持: %s -> %s' % (crlf, crlf2))
print('顶层 key 数: %d' % len(chk))
print('AI_COMPUTE 残留: %s' % ('AI_COMPUTE' in chk))
print('_flow.themes 残留 AI_COMPUTE: %s' % ('AI_COMPUTE' in chk.get('_flow', {}).get('themes', {})))
print('新主题存在: %s' % [k for k, _ in NEW if k in chk])
print('主题名: %s' % [chk[k]['name_cn'] for k, _ in NEW])
print('行数: %d' % txt.count(nl))
