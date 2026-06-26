# -*- coding: utf-8 -*-
"""清理头部docstring的残留v2.2引用"""
with open('D:\\mystock\\solo\\multi_factor_picker\\bull_scorer_v2.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Target the docstring block (lines 2-34 or so)
old_doc_body = '''"""
BullScore v3.0 — 中长线牛股选股系统（超预期持续成长版）

v2.2 核心变更（相比v2.1）：
├── 移除筹码面因子 (ChipScore) — 因子冗余
├── 预期差权重 8%→12% — 提权
├── 机构认可权重 4%→7% — 多维度综合(公募+分析师+北向+评级)
│
v2.1 核心增强：
├── 历史辨识度评分 (YRI) — 从资金活跃度、涨停基因、空间记忆、股性画像、舆情热度五个维度评估
├── Alpha因子评分 — 质量、成长、估值、动量、流动性、情绪六因子模型
├── 龙头/中军识别器 — 自动判定股票类型：龙头、中军、龙二、补涨、普通
└── AI分析集成 — 深度分析单只股票或对比分析多只股票

评分结构（v2.2）：
  BullScore_v3.0 =
    0.14 × IndustryDemandScore    (产业景气)
    0.14 × OrderExplosionScore    (订单爆发)
    0.10 × TechBarrierScore       (技术壁垒)
    0.10 × EarningsQualityScore   (业绩质量)
    0.12 × ExpectationScore       (预期差 — 提权，利润YoY非线性放大)
    0.06 × LeaderScore            (龙头地位)
    0.07 × InstitutionScore       (机构认可 — 多维度:分析师+公募+北向+评级)
    0.04 × MarketCapElasticity    (市值弹性)
    ———————————————————————— 筹码面已移除
    0.07 × SafetyMarginScore      (估值安全)
    ★ v2.1 新增：
    0.08 × RecognitionScore       (历史辨识度 YRI)
    0.08 × AlphaScore             (Alpha因子)

  FinalScore = 0.88 × BullScore_v3.0 + 0.12 × ThemeScore_v2

  ★ 主题权重已降（0.18→0.12），取消非线性放大

'''

new_doc_body = '''"""
BullScore v3.0 — 中长线牛股选股系统（超预期持续成长版）

v3.0 核心变更：
├── 移除 Alpha 因子 (8%) — 与成长/质量/流动性因子高度重叠
├── 预期差权重 12%→14% — 强化超预期增长
├── 业绩质量权重 10%→12% — 强化持续增长+加速度
├── 龙头地位权重 6%→8% — 强化细分产业头部公司
├── 机构认可权重 7%→8% — 多维度机构交叉验证
├── 市值弹性权重 4%→5% — 微调
│
★ 评分理念：从"宽泛多因子渔网"→"超预期驱动的持续成长王者评分"

评分结构（v3.0）：
  核心增长因子 (54%)：产业景气14% | 订单爆发14% | 业绩质量12% | 预期差14%
  护城河因子 (26%)：技术壁垒10% | 龙头地位8% | 机构认可8%
  估值+弹性因子 (12%)：估值安全7% | 市值弹性5%
  历史辨识度 (8%)

  BullScore_v3.0 = 0.14*ind + 0.14*order + 0.10*tech + 0.12*earn
                  + 0.14*expect + 0.08*leader + 0.08*inst + 0.05*mc
                  + 0.07*safety + 0.08*recognition

  FinalScore = 0.88 * BullScore_v3.0 + 0.12 * ThemeScore_v2

'''

content = content.replace(old_doc_body, new_doc_body, 1)

with open('D:\\mystock\\solo\\multi_factor_picker\\bull_scorer_v2.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('✅ 头部docstring已完整更新')
