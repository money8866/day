"""
主题知识图谱 V3 数据模型

定义系统所有 Pydantic V2 数据模型，用于数据验证、序列化与 API 交互。
"""

import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseSchema(BaseModel):
    """所有模型的基类，启用 ORM 兼容模式"""
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime.date: lambda v: v.isoformat() if v else None,
            datetime.datetime: lambda v: v.isoformat() if v else None,
        },
    )


# ════════════════════════════════════════════════════════════
# 1. 主题 (Theme)
# ════════════════════════════════════════════════════════════

class ThemeBase(BaseSchema):
    """主题基础模型，对应主题表所有字段（层级 2）"""
    id: int = Field(..., description="主题唯一标识")
    code: str = Field(..., description="主题代码，如 T001")
    name_cn: str = Field(..., description="主题中文名称")
    description: str | None = Field(None, description="主题描述")
    level: int = Field(default=2, description="主题层级", ge=1, le=5)
    status: str = Field(default="active", description="主题状态：active / inactive")
    lifecycle_stage: str | None = Field(None, description="生命周期阶段：萌芽 / 成长 / 成熟 / 衰退")
    main_etf_code: str | None = Field(None, description="主要关联 ETF 代码")
    created_at: datetime.datetime | None = Field(None, description="创建时间")
    updated_at: datetime.datetime | None = Field(None, description="更新时间")


class ThemeCreate(BaseSchema):
    """主题创建模型"""
    code: str = Field(..., description="主题代码，如 T001")
    name_cn: str = Field(..., description="主题中文名称")
    description: str | None = Field(None, description="主题描述")
    level: int = Field(default=2, description="主题层级", ge=1, le=5)
    status: str = Field(default="active", description="主题状态：active / inactive")
    lifecycle_stage: str | None = Field(None, description="生命周期阶段")
    main_etf_code: str | None = Field(None, description="主要关联 ETF 代码")


class ThemeResponse(ThemeBase):
    """主题响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 2. 产业链 (IndustryChain)
# ════════════════════════════════════════════════════════════

class IndustryChainBase(BaseSchema):
    """产业链基础模型（层级 3）"""
    id: int = Field(..., description="产业链唯一标识")
    theme_id: int = Field(..., description="关联主题 ID")
    code: str = Field(..., description="产业链代码，如 IC001")
    name_cn: str = Field(..., description="产业链中文名称")
    description: str | None = Field(None, description="产业链描述")
    sort_order: int = Field(default=0, description="排序序号")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class IndustryChainCreate(BaseSchema):
    """产业链创建模型"""
    theme_id: int = Field(..., description="关联主题 ID")
    code: str = Field(..., description="产业链代码")
    name_cn: str = Field(..., description="产业链中文名称")
    description: str | None = Field(None, description="产业链描述")
    sort_order: int = Field(default=0, description="排序序号")


class IndustryChainResponse(IndustryChainBase):
    """产业链响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 3. 概念标签 (ConceptTag)
# ════════════════════════════════════════════════════════════

class ConceptTagBase(BaseSchema):
    """概念标签基础模型（层级 4）"""
    id: int = Field(..., description="概念标签唯一标识")
    code: str = Field(..., description="概念标签代码，如 CT001")
    name_cn: str = Field(..., description="概念标签中文名称")
    category: str = Field(..., description="标签分类：brand / tech / policy / other")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ConceptTagCreate(BaseSchema):
    """概念标签创建模型"""
    code: str = Field(..., description="概念标签代码")
    name_cn: str = Field(..., description="概念标签中文名称")
    category: str = Field(..., description="标签分类：brand / tech / policy / other")


class ConceptTagResponse(ConceptTagBase):
    """概念标签响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 4. 主题-ETF 映射 (ThemeETF)
# ════════════════════════════════════════════════════════════

class ThemeETFBase(BaseSchema):
    """主题-ETF 映射基础模型"""
    id: int = Field(..., description="映射唯一标识")
    theme_id: int = Field(..., description="主题 ID")
    etf_code: str = Field(..., description="ETF 代码")
    etf_name: str = Field(..., description="ETF 名称")
    is_main: bool = Field(default=False, description="是否为主要 ETF")
    weight: float = Field(default=1.0, description="权重", ge=0.0, le=1.0)
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ThemeETFResponse(ThemeETFBase):
    """主题-ETF 映射响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 5. 主题关键词 (ThemeKeyword)
# ════════════════════════════════════════════════════════════

class ThemeKeywordBase(BaseSchema):
    """主题关键词基础模型"""
    id: int = Field(..., description="关键词唯一标识")
    theme_id: int = Field(..., description="主题 ID")
    keyword: str = Field(..., description="关键词文本")
    weight: float = Field(default=1.0, description="关键词权重", ge=0.0, le=1.0)
    keyword_type: str = Field(
        ..., description="关键词类型：core / industry / product / concept / brand"
    )
    is_exclude: bool = Field(default=False, description="是否为排除词（反向匹配）")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ThemeKeywordResponse(ThemeKeywordBase):
    """主题关键词响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 6. 置信度分解 (ConfidenceBreakdown) —— 嵌入 StockTheme
# ════════════════════════════════════════════════════════════

class ConfidenceBreakdown(BaseSchema):
    """置信度得分分解，内嵌于 StockTheme 中使用"""
    total_score: float = Field(0.0, description="综合置信度总分", ge=0.0, le=100.0)
    etf_correlation: float = Field(0.0, description="ETF 持仓相关性得分", ge=0.0, le=100.0)
    industry_match: float = Field(0.0, description="行业匹配度得分", ge=0.0, le=100.0)
    revenue_match: float = Field(0.0, description="营收匹配度得分", ge=0.0, le=100.0)
    concept_match: float = Field(0.0, description="概念匹配度得分", ge=0.0, le=100.0)
    institution_report: float = Field(0.0, description="机构研报匹配得分", ge=0.0, le=100.0)
    business_description: float = Field(0.0, description="业务描述匹配得分", ge=0.0, le=100.0)
    supply_chain: float = Field(0.0, description="供应链关联得分", ge=0.0, le=100.0)
    customer: float = Field(0.0, description="客户关联得分", ge=0.0, le=100.0)
    product: float = Field(0.0, description="产品关联得分", ge=0.0, le=100.0)
    keyword_tfidf: float = Field(0.0, description="关键词 TF-IDF 匹配得分", ge=0.0, le=100.0)
    reason: str = Field(default="", description="置信度得分综合说明")


# ════════════════════════════════════════════════════════════
# 7. 个股-主题归属 (StockTheme)
# ════════════════════════════════════════════════════════════

class StockThemeBase(BaseSchema):
    """个股-主题归属基础模型（核心模型）"""
    id: int = Field(..., description="归属记录唯一标识")
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票名称")
    primary_theme_id: int = Field(..., description="主属主题 ID")
    confidence: float = Field(..., description="归属置信度（0-100）", ge=0.0, le=100.0)
    confidence_reason: str = Field(default="", description="置信度判定理由")
    secondary_theme_ids: list[str] = Field(default_factory=list, description="次要主题 ID 列表")
    is_leader: bool = Field(default=False, description="是否为龙头股")
    leader_type: str | None = Field(
        None, description="龙头类型：leader / core / follower / catch_up / eliminated"
    )
    industry_chain_ids: list[str] = Field(default_factory=list, description="关联产业链 ID 列表")
    concept_tag_ids: list[str] = Field(default_factory=list, description="关联概念标签 ID 列表")
    is_active: bool = Field(default=True, description="是否仍属该主题")
    assigned_at: datetime.datetime | None = Field(None, description="首次归属时间")
    updated_at: datetime.datetime | None = Field(None, description="更新时间")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """校验置信度须在 0-100 范围内"""
        if v < 0 or v > 100:
            raise ValueError("confidence 必须在 0 到 100 之间")
        return v

    @field_validator("leader_type")
    @classmethod
    def validate_leader_type(cls, v: str | None) -> str | None:
        """校验 leader_type 取值须在允许范围内"""
        if v is not None and v not in ("leader", "core", "follower", "catch_up", "eliminated"):
            raise ValueError("leader_type 必须为 leader/core/follower/catch_up/eliminated 之一")
        return v


class StockThemeCreate(BaseSchema):
    """个股-主题归属创建模型"""
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票名称")
    primary_theme_id: int = Field(..., description="主属主题 ID")
    confidence: float = Field(..., description="归属置信度（0-100）", ge=0.0, le=100.0)
    confidence_reason: str = Field(default="", description="置信度判定理由")
    secondary_theme_ids: list[str] = Field(default_factory=list, description="次要主题 ID 列表")
    is_leader: bool = Field(default=False, description="是否为龙头股")
    leader_type: str | None = Field(
        None, description="龙头类型：leader / core / follower / catch_up / eliminated"
    )
    industry_chain_ids: list[str] = Field(default_factory=list, description="关联产业链 ID 列表")
    concept_tag_ids: list[str] = Field(default_factory=list, description="关联概念标签 ID 列表")
    is_active: bool = Field(default=True, description="是否仍属该主题")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if v < 0 or v > 100:
            raise ValueError("confidence 必须在 0 到 100 之间")
        return v

    @field_validator("leader_type")
    @classmethod
    def validate_leader_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ("leader", "core", "follower", "catch_up", "eliminated"):
            raise ValueError("leader_type 必须为 leader/core/follower/catch_up/eliminated 之一")
        return v


class StockThemeResponse(StockThemeBase):
    """个股-主题归属响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 8. 主题关系 (ThemeRelation)
# ════════════════════════════════════════════════════════════

class ThemeRelationBase(BaseSchema):
    """主题关系基础模型"""
    id: int = Field(..., description="关系唯一标识")
    source_theme_id: int = Field(..., description="源主题 ID")
    target_theme_id: int = Field(..., description="目标主题 ID")
    relation_type: str = Field(
        ..., description="关系类型：correlation / conflict / chain_upstream / chain_downstream"
    )
    strength: float = Field(..., description="关系强度（0-1）", ge=0.0, le=1.0)
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ThemeRelationResponse(ThemeRelationBase):
    """主题关系响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 9. 主题历史 (ThemeHistory)
# ════════════════════════════════════════════════════════════

class ThemeHistoryBase(BaseSchema):
    """主题历史数据基础模型"""
    id: int = Field(..., description="历史记录唯一标识")
    theme_id: int = Field(..., description="主题 ID")
    trade_date: datetime.date = Field(..., description="交易日")
    lifecycle_stage: str | None = Field(None, description="当日所处生命周期阶段")
    momentum_5d: float | None = Field(None, description="5 日动量")
    momentum_20d: float | None = Field(None, description="20 日动量")
    momentum_60d: float | None = Field(None, description="60 日动量")
    volume_ratio: float | None = Field(None, description="成交量比率")
    leader_count: int | None = Field(None, description="龙头股数量", ge=0)
    total_market_cap_billion: float | None = Field(None, description="总市值（十亿）", ge=0.0)
    avg_return_5d: float | None = Field(None, description="5 日平均收益率")
    avg_return_20d: float | None = Field(None, description="20 日平均收益率")
    turnover_rate: float | None = Field(None, description="换手率", ge=0.0)
    sentiment_score: float | None = Field(None, description="情绪评分", ge=0.0, le=100.0)
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ThemeHistoryResponse(ThemeHistoryBase):
    """主题历史数据响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 10. 主题阶段变更 (ThemeStage)
# ════════════════════════════════════════════════════════════

class ThemeStageBase(BaseSchema):
    """主题生命周期阶段变更记录基础模型"""
    id: int = Field(..., description="阶段记录唯一标识")
    theme_id: int = Field(..., description="主题 ID")
    trade_date: datetime.date = Field(..., description="变更交易日")
    stage_before: str = Field(..., description="变更前阶段")
    stage_after: str = Field(..., description="变更后阶段")
    reason: str | None = Field(None, description="阶段变更原因")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ThemeStageResponse(ThemeStageBase):
    """主题生命周期阶段变更记录响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 11. 龙头股追踪 (LeaderStock)
# ════════════════════════════════════════════════════════════

class LeaderStockBase(BaseSchema):
    """龙头股追踪基础模型"""
    id: int = Field(..., description="追踪记录唯一标识")
    theme_id: int = Field(..., description="主题 ID")
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票名称")
    leader_type: str = Field(
        ..., description="龙头类型：leader / core / follower / catch_up / eliminated"
    )
    assigned_date: datetime.date | None = Field(None, description="认定日期")
    consecutive_limit_up: int = Field(default=0, description="连续涨停天数", ge=0)
    cumulative_return: float | None = Field(None, description="累计收益率")
    market_cap_billion: float | None = Field(None, description="总市值（十亿）", ge=0.0)
    is_active: bool = Field(default=True, description="是否仍为龙头状态")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class LeaderStockResponse(LeaderStockBase):
    """龙头股追踪响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 12. 个股关系 (StockRelation)
# ════════════════════════════════════════════════════════════

class StockRelationBase(BaseSchema):
    """个股间关系基础模型"""
    id: int = Field(..., description="关系唯一标识")
    source_stock_code: str = Field(..., description="源股票代码")
    target_stock_code: str = Field(..., description="目标股票代码")
    relation_type: str = Field(..., description="关系类型：correlation / supply_chain / competition / cooperation")
    strength: float = Field(..., description="关系强度（0-1）", ge=0.0, le=1.0)
    theme_id: int | None = Field(None, description="关联主题 ID（可选）")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class StockRelationResponse(StockRelationBase):
    """个股间关系响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 13. 主题日评分 (ThemeScoreDaily)
# ════════════════════════════════════════════════════════════

class ThemeScoreDailyBase(BaseSchema):
    """主题日评分基础模型"""
    id: int = Field(..., description="评分记录唯一标识")
    theme_id: int = Field(..., description="主题 ID")
    trade_date: datetime.date = Field(..., description="交易日")
    total_score: float = Field(..., description="综合评分", ge=0.0, le=100.0)
    momentum_score: float = Field(0.0, description="动量维度评分", ge=0.0, le=100.0)
    volume_score: float = Field(0.0, description="成交量维度评分", ge=0.0, le=100.0)
    breadth_score: float = Field(0.0, description="宽度维度评分", ge=0.0, le=100.0)
    sentiment_score: float = Field(0.0, description="情绪维度评分", ge=0.0, le=100.0)
    leader_score: float = Field(0.0, description="龙头维度评分", ge=0.0, le=100.0)
    etf_corr_score: float = Field(0.0, description="ETF 相关性评分", ge=0.0, le=100.0)
    capital_flow_score: float = Field(0.0, description="资金流评分", ge=0.0, le=100.0)
    detail_json: dict | None = Field(None, description="评分明细 JSON")
    created_at: datetime.datetime | None = Field(None, description="创建时间")


class ThemeScoreDailyResponse(ThemeScoreDailyBase):
    """主题日评分响应模型"""
    pass


# ════════════════════════════════════════════════════════════
# 14. 分类结果 (ClassificationResult) —— 非 DB 模型
# ════════════════════════════════════════════════════════════

class ClassificationResult(BaseSchema):
    """自动分类结果，非数据库模型，用于分类器输出"""
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票名称")
    primary_theme_code: str = Field(..., description="主属主题代码")
    primary_theme_name: str = Field(..., description="主属主题名称")
    confidence: float = Field(..., description="分类置信度", ge=0.0, le=100.0)
    confidence_breakdown: ConfidenceBreakdown = Field(
        ..., description="置信度得分分解详情"
    )
    secondary_theme_codes: list[str] = Field(
        default_factory=list, description="次要主题代码列表"
    )
    industry_chain_codes: list[str] = Field(
        default_factory=list, description="关联产业链代码列表"
    )
    concept_tag_codes: list[str] = Field(
        default_factory=list, description="关联概念标签代码列表"
    )
    leader_type: str | None = Field(
        None, description="龙头类型：leader / core / follower / catch_up / eliminated"
    )


# ════════════════════════════════════════════════════════════
# 15. 生命周期分析结果 (LifecycleResult) —— 非 DB 模型
# ════════════════════════════════════════════════════════════

class LifecycleResult(BaseSchema):
    """生命周期分析结果，非数据库模型，用于分析器输出"""
    theme_code: str = Field(..., description="主题代码")
    theme_name: str = Field(..., description="主题名称")
    current_stage: str = Field(..., description="当前生命周期阶段")
    stage_confidence: float = Field(..., description="阶段判定置信度", ge=0.0, le=100.0)
    indicators: dict = Field(..., description="阶段判定依据的各项指标")
    next_stage_prediction: str | None = Field(None, description="下一阶段预测")
    days_in_stage: int = Field(..., description="当前阶段持续天数", ge=0)


# ════════════════════════════════════════════════════════════
# 16. 龙头分析结果 (LeaderAnalysisResult) —— 非 DB 模型
# ════════════════════════════════════════════════════════════

class LeaderAnalysisResult(BaseSchema):
    """龙头分析结果，非数据库模型，用于分析器输出"""
    theme_code: str = Field(..., description="主题代码")
    theme_name: str = Field(..., description="主题名称")
    leaders: list[dict] = Field(default_factory=list, description="龙头股列表")
    cores: list[dict] = Field(default_factory=list, description="核心股列表")
    followers: list[dict] = Field(default_factory=list, description="跟风股列表")
    catch_up_candidates: list[dict] = Field(default_factory=list, description="补涨候选股列表")
    eliminated: list[dict] = Field(default_factory=list, description="淘汰股列表")
    analysis_date: datetime.date = Field(..., description="分析日期")
