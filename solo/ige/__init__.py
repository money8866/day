# -*- coding: utf-8 -*-
"""
IGE v1.1｜Industry Growth Elasticity
申万三级行业增长弹性因子计算引擎
行业景气改善后，收入 → 利润 → 股价超额收益 的非线性扩张能力

v1.1 关键变化：
- igemix（基础行业弹性）与 IGE_ADJ（当前有效弹性）同时输出；igemix 不再做二次横截面 PercentileRank
- 生命周期调整严格作用于 igemix，先算基础分 → 调整 → 最后 clip
- 新增 IndustryElasticityType / IndustryElasticityState / IndustryConfidence / ACCELERATION_CONFIRM
- F5 增补 盈利加速→行业超额收益 传导β；F4 标记 PRICE_DATA_MISSING
- 行业 Top 排序链：IGE_ADJ → igemix → Acceleration → ProfitElasticity
- 金融/周期/科技/医药 使用不同解释模板
"""
__version__ = "1.1.0"
