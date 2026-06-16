# -*- coding: utf-8 -*-
"""
行业需求链景气度评分系统 (IndustryDemandScorer)

基于"需求链驱动模型"衡量真实景气度，而非传统行业标签。

核心原则：
- 不使用申万行业涨跌、行业PE均值、历史涨幅
- 必须使用：终端需求数据、产业链订单数据、产品价格变化、产能利用率、资本开支

评分公式：
Industry_Score =
    0.30 × 终端需求指数 (terminal_demand)
  + 0.25 × 产业链订单强度 (order_strength)
  + 0.20 × 产品价格变化 (price_score)
  + 0.15 × 产能利用率 (capacity_score)
  + 0.10 × 资本开支扩张 (capex_score)

输出范围：0~100分
"""
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
import loguru

logger = loguru.logger


@dataclass
class DemandChainScores:
    """需求链各维度得分"""
    ts_code: str
    name: str

    # 各因子原始值
    terminal_demand_raw: float = 0.0   # 终端需求原始值
    order_strength_raw: float = 0.0    # 订单强度原始值
    price_raw: float = 0.0             # 价格得分原始值
    capacity_raw: float = 0.0         # 产能利用率原始值
    capex_raw: float = 0.0            # 资本开支原始值

    # 各因子归一化得分 (0~1)
    terminal_demand_score: float = 0.0
    order_strength_score: float = 0.0
    price_score: float = 0.0
    capacity_score: float = 0.0
    capex_score: float = 0.0

    # 综合得分
    industry_demand_score: float = 0.0

    # 排名
    rank: int = 0

    # 产业链标签（动态识别）
    chain_tag: str = ""  # e.g., "AI算力链", "PCB链", "半导体设备链"


class IndustryDemandScorer:
    """
    需求链驱动行业景气度评分器

    核心方法：
    - calc_terminal_demand(): 终端需求指数
    - calc_order_strength(): 产业链订单强度
    - calc_price_score(): 产品价格变化
    - calc_capacity_score(): 产能利用率
    - calc_capex_score(): 资本开支扩张
    - calc_industry_score(): 综合行业景气度
    - normalize(): 归一化处理
    - rank_stocks(): 输出排序结果
    """

    # 权重配置
    WEIGHTS = {
        'terminal_demand': 0.30,
        'order_strength': 0.25,
        'price': 0.20,
        'capacity': 0.15,
        'capex': 0.10,
    }

    # 产能利用率评分阈值
    CAPACITY_THRESHOLDS = {
        '极度紧张': 0.95,
        '高景气': 0.85,
        '正常': 0.70,
        '过剩': 0.00,
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        初始化评分器

        Args:
            config: 配置字典，包含阈值和权重
        """
        self.config = config or {}
        self.terminal_config = self.config.get('terminal_demand', {})
        self.order_config = self.config.get('order_strength', {})
        self.price_config = self.config.get('price', {})
        self.capacity_config = self.config.get('capacity', {})
        self.capex_config = self.config.get('capex', {})

        # 基准阈值（可配置）
        self.revenue_growth_benchmark = self.terminal_config.get('revenue_growth_benchmark', 0.20)
        self.margin_change_benchmark = self.price_config.get('margin_change_benchmark', 0.02)
        self.capex_growth_benchmark = self.capex_config.get('capex_growth_benchmark', 0.10)

    def calc_terminal_demand(self, df: pd.DataFrame) -> pd.Series:
        """
        计算终端需求指数（30%权重）

        衡量"最终需求是否爆发"，必须来自真实终端行业。

        变量：
        - terminal_ai_growth: AI服务器/算力需求增速
        - terminal_ev_growth: 新能源车销量增速
        - terminal_consumer_growth: 消费电子需求增速
        - terminal_capex_growth: 云厂商CAPEX增速
        - revenue_growth_yoy: 公司收入增速（自身供需验证）

        计算公式：
        terminal_demand = 0.4 × 核心终端增长 + 0.3 × 次级终端增长 + 0.3 × 资本开支增长

        如果宏观数据不可用，则使用：
        - revenue_growth_yoy（收入增速）作为终端需求代理
        - gross_margin_change（毛利率变化）作为需求紧张度代理
        """
        results = pd.Series(dtype=float, index=df.index)

        # 检查是否有宏观终端需求数据
        has_macro = all(col in df.columns for col in [
            'terminal_ai_growth', 'terminal_ev_growth',
            'terminal_consumer_growth', 'terminal_capex_growth'
        ])

        if has_macro:
            # 使用宏观终端需求数据
            ai_weight, ev_weight, consumer_weight, capex_weight = 0.4, 0.2, 0.2, 0.2

            terminal_index = (
                df['terminal_ai_growth'].fillna(0) * ai_weight +
                df['terminal_ev_growth'].fillna(0) * ev_weight +
                df['terminal_consumer_growth'].fillna(0) * consumer_weight +
                df['terminal_capex_growth'].fillna(0) * capex_weight
            )

            # 归一化到0~1范围
            terminal_index = terminal_index.clip(lower=0)
            max_val = terminal_index.quantile(0.95) if terminal_index.max() > 0 else 1.0
            if max_val > 0:
                results = (terminal_index / max_val).clip(upper=1.0)
            else:
                results = pd.Series(0.0, index=df.index)
        else:
            # 使用个股财务数据作为终端需求代理
            # 核心逻辑：毛利率提升 = 需求超过供给 = 终端需求旺盛
            # 次级逻辑：收入增速 > 行业均值表示公司享受终端需求红利

            if 'gross_margin_change' in df.columns and 'revenue_growth_yoy' in df.columns:
                # 毛利率变化作为主要代理（供需紧张度）
                gm_proxy = df['gross_margin_change'].fillna(0).clip(lower=-0.1, upper=0.1)

                # 收入增速作为次级代理
                rev_proxy = df['revenue_growth_yoy'].fillna(0).clip(lower=-0.5, upper=1.0)

                # 综合：0.6×毛利率代理 + 0.4×收入增速代理
                terminal_index = 0.6 * gm_proxy / self.margin_change_benchmark + \
                                 0.4 * rev_proxy / self.revenue_growth_benchmark

                results = terminal_index.clip(lower=0).fillna(0)
                results = (results / results.quantile(0.95)).clip(upper=1.0).fillna(0)
            else:
                results = pd.Series(0.5, index=df.index)  # 默认中间值

        return results

    def calc_order_strength(self, df: pd.DataFrame) -> pd.Series:
        """
        计算产业链订单强度（25%权重）

        衡量需求是否传导到中游/上游。

        变量：
        - contract_liability_yoy: 合同负债增速
        - order_backlog_yoy: 在手订单增速
        - advance_payment_yoy: 预收款增速
        - new_customer_growth: 新客户增长

        计算公式：
        order_strength = 0.5 × 合同负债增速 + 0.3 × 在手订单增速 + 0.2 × 预收款增速

        如果没有直接订单数据，则使用：
        - gross_margin_change: 毛利率变化（需求旺盛→毛利率提升）
        - inventory_turnover_change: 存货周转率变化（需求旺盛→库存加速）
        """
        results = pd.Series(dtype=float, index=df.index)

        has_order_data = all(col in df.columns for col in [
            'contract_liability_yoy', 'order_backlog_yoy', 'advance_payment_yoy'
        ])

        if has_order_data:
            # 使用直接订单数据
            order_index = (
                df['contract_liability_yoy'].fillna(0).clip(lower=-0.5, upper=2.0) * 0.5 +
                df['order_backlog_yoy'].fillna(0).clip(lower=-0.5, upper=2.0) * 0.3 +
                df['advance_payment_yoy'].fillna(0).clip(lower=-0.5, upper=2.0) * 0.2
            )
        else:
            # 使用代理指标
            # 毛利率变化 → 订单旺盛（需求超过供给时厂商才有定价权）
            # 存货周转率变化 → 订单加速（需求旺盛时存货快速去化）

            if 'gross_margin_change' in df.columns and 'inventory_turnover_change' in df.columns:
                gm_proxy = df['gross_margin_change'].fillna(0).clip(lower=-0.1, upper=0.1)
                inv_proxy = df['inventory_turnover_change'].fillna(0).clip(lower=-0.3, upper=0.5)

                # 毛利率变化权重更高（直接反映供需）
                order_index = 0.6 * gm_proxy / max(self.margin_change_benchmark, 0.001) + \
                              0.4 * inv_proxy / 0.2  # 0.2作为基准增速
            elif 'gross_margin_change' in df.columns:
                order_index = df['gross_margin_change'].fillna(0).clip(lower=-0.1, upper=0.1) / self.margin_change_benchmark
            else:
                order_index = pd.Series(0.5, index=df.index)

        results = order_index.clip(lower=0).fillna(0)
        # 归一化
        p95 = results.quantile(0.95) if results.max() > 0 else 1.0
        if p95 > 0:
            results = (results / p95).clip(upper=1.0)

        return results

    def calc_price_score(self, df: pd.DataFrame) -> pd.Series:
        """
        计算产品价格变化（20%权重）

        衡量供需是否紧张。

        变量：
        - price_change: 产品单价变化
        - gross_margin_change: 毛利率变化
        - supply_demand_gap: 供需缺口指数

        计算公式：
        price_score = 0.4 × 产品涨价幅度 + 0.3 × 毛利率提升 + 0.3 × 供需紧张度
        """
        results = pd.Series(dtype=float, index=df.index)

        has_price_data = all(col in df.columns for col in ['price_change', 'gross_margin_change'])

        if has_price_data:
            # 直接使用价格和毛利率变化
            price_component = df['price_change'].fillna(0).clip(lower=-0.2, upper=0.5)
            margin_component = df['gross_margin_change'].fillna(0).clip(lower=-0.1, upper=0.1)

            price_score = (
                price_component / 0.2 * 0.4 +  # 20%涨价=满分
                margin_component / self.margin_change_benchmark * 0.3 +
                df.get('supply_demand_gap', pd.Series(0.5, index=df.index)).fillna(0.5) * 0.3
            )
        elif 'gross_margin_change' in df.columns:
            # 仅用毛利率变化
            margin_component = df['gross_margin_change'].fillna(0).clip(lower=-0.1, upper=0.1)
            price_score = margin_component / self.margin_change_benchmark
        elif 'gross_margin' in df.columns:
            # 用毛利率绝对值作为定价能力代理
            gm = df['gross_margin'].fillna(0.2).clip(lower=0.1, upper=0.8)
            price_score = (gm - 0.2) / 0.4  # 20%~60%毛利率区间归一化
        else:
            price_score = pd.Series(0.5, index=df.index)

        results = price_score.clip(lower=0).fillna(0)
        results = (results / results.quantile(0.95)).clip(upper=1.0).fillna(0)

        return results

    def calc_capacity_score(self, df: pd.DataFrame) -> pd.Series:
        """
        计算产能利用率（15%权重）

        衡量是否进入景气扩张期。

        评分逻辑：
        - >95% = 极度紧张 (1.0分)
        - 85~95% = 高景气 (0.8~1.0分)
        - 70~85% = 正常 (0.5~0.8分)
        - <70% = 过剩 (0~0.5分)

        如果没有直接的产能利用率数据，则使用：
        - inventory_turnover_change: 存货周转率变化
        - fixed_asset_turnover_change: 固定资产周转率变化
        - delivery_cycle_change: 交付周期变化
        """
        results = pd.Series(dtype=float, index=df.index)

        if 'capacity_utilization' in df.columns:
            # 直接使用产能利用率
            cap = df['capacity_utilization'].fillna(0.5).clip(lower=0, upper=1)

            # 评分函数：分段线性
            def cap_score(x):
                if x >= 0.95:
                    return 1.0
                elif x >= 0.85:
                    return 0.8 + (x - 0.85) / 0.10 * 0.2
                elif x >= 0.70:
                    return 0.5 + (x - 0.70) / 0.15 * 0.3
                else:
                    return x / 0.70 * 0.5

            results = cap.apply(cap_score)
        else:
            # 使用周转率变化作为代理
            # 逻辑：需求旺盛 → 存货快速去化 + 固定资产利用率提升

            has_turnover = 'inventory_turnover_change' in df.columns or \
                           'fixed_asset_turnover_change' in df.columns

            if has_turnover:
                inv_change = df.get('inventory_turnover_change', pd.Series(0, index=df.index)).fillna(0)
                fa_change = df.get('fixed_asset_turnover_change', pd.Series(0, index=df.index)).fillna(0)

                # 周转率提升 = 需求旺盛 = 产能紧张
                # 归一化：+20%变化 → 95%产能利用率
                turnover_proxy = (inv_change * 0.6 + fa_change * 0.4).clip(lower=-0.3, upper=0.5)

                def turnover_to_capacity(x):
                    if x >= 0.20:
                        return 0.95 + min((x - 0.20) / 0.30, 1.0) * 0.05
                    elif x >= 0:
                        return 0.70 + x / 0.20 * 0.25
                    elif x >= -0.15:
                        return 0.50 + (x + 0.15) / 0.15 * 0.20
                    else:
                        return max(0.30 + (x + 0.30) / 0.30 * 0.20, 0.1)

                results = turnover_proxy.apply(turnover_to_capacity)
            else:
                # 无法评估，默认中等
                results = pd.Series(0.5, index=df.index)

        return results

    def calc_capex_score(self, df: pd.DataFrame) -> pd.Series:
        """
        计算资本开支扩张（10%权重）

        衡量行业未来供给扩张趋势。

        变量：
        - industry_capex_yoy: 资本开支增速
        - capex_to_revenue: 资本开支占收入比
        - new_line_announcements: 新建产线公告数量（定性数据）

        评分逻辑：
        - 资本开支增速 > 30% = 扩张期 (高分)
        - 资本开支增速 10~30% = 稳健扩张
        - 资本开支增速 0~10% = 维持性投资
        - 资本开支增速 < 0 = 收缩
        """
        results = pd.Series(dtype=float, index=df.index)

        if 'capex_growth_yoy' in df.columns:
            # 直接使用资本开支增速
            capex_growth = df['capex_growth_yoy'].fillna(0).clip(lower=-0.5, upper=1.0)

            def capex_score_func(x):
                if x >= 0.30:
                    return 1.0
                elif x >= 0.10:
                    return 0.7 + (x - 0.10) / 0.20 * 0.3
                elif x >= 0:
                    return 0.4 + x / 0.10 * 0.3
                else:
                    return max(0.4 + x / 0.10 * 0.4, 0.0)  # 负增长但不能低于0

            results = capex_growth.apply(capex_score_func)

        elif 'cap_expend_ratio' in df.columns:
            # 使用资本开支占收入比（扩张意愿）
            capex_ratio = df['cap_expend_ratio'].fillna(0).clip(lower=0, upper=0.3)
            results = (capex_ratio / 0.15).clip(upper=1.0)  # 15%=满分

        elif 'capex_intensity' in df.columns:
            # 资本开支强度（相对行业）
            intensity = df['capex_intensity'].fillna(0.5).clip(lower=0, upper=2.0)
            results = (intensity / 1.5).clip(upper=1.0)
        else:
            # 无法评估，默认中等偏低
            results = pd.Series(0.3, index=df.index)

        return results

    def calc_industry_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算综合行业景气度得分

        Args:
            df: 包含所有需求链指标的DataFrame

        Returns:
            包含各因子得分和综合得分的DataFrame
        """
        result_df = df.copy()

        # 计算各因子得分
        result_df['terminal_demand_raw'] = self.calc_terminal_demand(df)
        result_df['order_strength_raw'] = self.calc_order_strength(df)
        result_df['price_raw'] = self.calc_price_score(df)
        result_df['capacity_raw'] = self.calc_capacity_score(df)
        result_df['capex_raw'] = self.calc_capex_score(df)

        # 归一化（各因子在所有股票间进行百分位排名）
        result_df['terminal_demand_score'] = self.normalize(result_df['terminal_demand_raw'])
        result_df['order_strength_score'] = self.normalize(result_df['order_strength_raw'])
        result_df['price_score'] = self.normalize(result_df['price_raw'])
        result_df['capacity_score'] = self.normalize(result_df['capacity_raw'])
        result_df['capex_score'] = self.normalize(result_df['capex_raw'])

        # 计算综合得分（加权平均 × 100）
        result_df['industry_demand_score'] = (
            result_df['terminal_demand_score'] * self.WEIGHTS['terminal_demand'] +
            result_df['order_strength_score'] * self.WEIGHTS['order_strength'] +
            result_df['price_score'] * self.WEIGHTS['price'] +
            result_df['capacity_score'] * self.WEIGHTS['capacity'] +
            result_df['capex_score'] * self.WEIGHTS['capex']
        ) * 100  # 转换为0~100分

        return result_df

    def normalize(self, series: pd.Series) -> pd.Series:
        """
        归一化处理：使用百分位排名

        将原始值转换为0~1的相对排名分数

        Args:
            series: 原始分数Series

        Returns:
            归一化后的Series（百分位排名）
        """
        if series.max() == series.min():
            return pd.Series(0.5, index=series.index)

        # 使用百分位排名：值越大排名越高
        return series.rank(pct=True, ascending=True).clip(lower=0.01, upper=0.99)

    def identify_chain_tag(self, df: pd.DataFrame) -> pd.Series:
        """
        动态识别个股所属产业链标签

        基于business_dna_tags或行业字段进行映射

        产业链映射：
        - AI算力链: AI服务器、GPU、光模块、PCB载板
        - PCB链: PCB、覆铜板
        - 半导体设备链: 半导体设备、材料
        - 新能源链: 锂电池、锂电设备、电动车
        - 机器人链: 工业机器人、自动化设备
        - 消费电子链: 手机产业链、可穿戴
        """
        chain_tags = pd.Series('', index=df.index)

        # 映射规则（按关键词匹配）
        chain_mappings = {
            'AI算力链': ['AI', 'GPU', '光模块', '算力', '服务器', 'PCB载板', '先进封装', 'HBM'],
            'PCB链': ['PCB', '印制电路板', '覆铜板', ' CCL', '电子级玻纤'],
            '半导体设备链': ['半导体设备', '半导体材料', '硅片', '光刻', '刻蚀', '沉积', 'PVD', 'CVD'],
            '半导体设计链': ['芯片设计', 'IC设计', 'EDA', 'IP'],
            '新能源链': ['锂电池', '锂电设备', '电动车', '新能源汽车', '动力电池', '储能'],
            '机器人链': ['工业机器人', '自动化设备', '伺服', '减速器', '控制器', '机器视觉'],
            '消费电子链': ['手机', '消费电子', '显示屏', '光学', '摄像头', '面板'],
            '低空经济链': ['低空经济', 'eVTOL', '无人机', '通用航空'],
            '军工链': ['军工', '国防', '航空航天', '导弹'],
            '医药链': ['创新药', '生物医药', '医疗器械', 'CXO'],
        }

        # 从business_dna_tags识别
        if 'business_dna_tags' in df.columns:
            for tag, keywords in chain_mappings.items():
                mask = df['business_dna_tags'].fillna('').apply(
                    lambda x: any(kw in str(x) for kw in keywords)
                )
                chain_tags.loc[mask] = tag

        # 从theme_tags识别（如果存在）
        if 'theme_tags' in df.columns:
            for tag, keywords in chain_mappings.items():
                mask = df['theme_tags'].fillna('').apply(
                    lambda x: any(kw in str(x) for kw in keywords)
                )
                chain_tags.loc[chain_tags == ''] = df.loc[chain_tags == '', 'theme_tags'].apply(
                    lambda x: next((tag for tag, kws in chain_mappings.items() if any(kw in str(x) for kw in kws)), '')
                )

        # 从industry字段模糊匹配（最后兜底）
        if 'industry' in df.columns and (chain_tags == '').any():
            for tag, keywords in chain_mappings.items():
                mask = df['industry'].fillna('').apply(
                    lambda x: any(kw in str(x) for kw in keywords)
                )
                chain_tags.loc[mask & (chain_tags == '')] = tag

        return chain_tags

    def rank_stocks(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对股票按行业景气度综合得分排序

        Args:
            df: 包含industry_demand_score的DataFrame

        Returns:
            排序后的DataFrame，包含rank列
        """
        result = df.sort_values('industry_demand_score', ascending=False).copy()
        result['rank'] = range(1, len(result) + 1)
        return result.reset_index(drop=True)

    def to_dataframe(self, scores: List[DemandChainScores]) -> pd.DataFrame:
        """
        将DemandChainScores列表转换为DataFrame

        Args:
            scores: DemandChainScores列表

        Returns:
            格式化DataFrame
        """
        if not scores:
            return pd.DataFrame()

        data = []
        for s in scores:
            data.append({
                '股票代码': s.ts_code,
                '股票名称': s.name,
                '产业链标签': s.chain_tag,
                '终端需求指数': f"{s.terminal_demand_score:.3f}",
                '订单强度指数': f"{s.order_strength_score:.3f}",
                '价格变化指数': f"{s.price_score:.3f}",
                '产能利用率指数': f"{s.capacity_score:.3f}",
                '资本开支指数': f"{s.capex_score:.3f}",
                '行业景气度得分': f"{s.industry_demand_score:.1f}",
                '排名': s.rank,
            })

        return pd.DataFrame(data)

    def get_top_chains(self, df: pd.DataFrame, top_n: int = 5) -> Dict[str, float]:
        """
        获取景气度最高的产业链

        Args:
            df: 评分结果DataFrame
            top_n: 返回前N条产业链

        Returns:
            {chain_tag: avg_score}字典
        """
        if 'chain_tag' not in df.columns or 'industry_demand_score' not in df.columns:
            return {}

        chain_scores = df.groupby('chain_tag')['industry_demand_score'].mean()
        return chain_scores.sort_values(ascending=False).head(top_n).to_dict()

    def generate_summary(self, df: pd.DataFrame) -> str:
        """
        生成需求链景气度分析摘要

        Args:
            df: 评分结果DataFrame

        Returns:
            摘要文本
        """
        lines = ["=" * 60]
        lines.append("行业需求链景气度评分报告")
        lines.append("=" * 60)

        # 基本统计
        total = len(df)
        lines.append(f"\n分析股票总数: {total}")

        # 各因子分布
        if 'industry_demand_score' in df.columns:
            avg_score = df['industry_demand_score'].mean()
            max_score = df['industry_demand_score'].max()
            min_score = df['industry_demand_score'].min()
            lines.append(f"\n综合景气度得分: 平均={avg_score:.1f}, 最高={max_score:.1f}, 最低={min_score:.1f}")

        # 产业链分布
        if 'chain_tag' in df.columns:
            chain_counts = df['chain_tag'].value_counts()
            lines.append(f"\n产业链分布:")
            for chain, count in chain_counts.head(10).items():
                if chain:
                    avg = df[df['chain_tag'] == chain]['industry_demand_score'].mean()
                    lines.append(f"  {chain}: {count}只, 平均得分={avg:.1f}")

        # Top 10股票
        if 'rank' in df.columns:
            top10 = df.nsmallest(10, 'rank')
            lines.append(f"\n景气度Top 10股票:")
            for _, row in top10.iterrows():
                lines.append(f"  {row['股票名称']}({row['ts_code']}) {row['industry_demand_score']:.1f}分 {row.get('chain_tag', '')}")

        return "\n".join(lines)


# ============================================================
# 辅助函数：构建个股的需求链数据
# ============================================================

def build_stock_demand_data(
    stock_financial_data: pd.DataFrame,
    chain_tag_mapping: Optional[Dict[str, str]] = None
) -> pd.DataFrame:
    """
    从个股财务数据构建需求链评分所需的数据格式

    Args:
        stock_financial_data: 包含个股财务指标的DataFrame
            必需字段: ts_code, name
            可选字段: revenue_growth_yoy, profit_growth_yoy, gross_margin,
                     gross_margin_change, contract_liability_yoy,
                     inventory_turnover_change, fixed_asset_turnover_change,
                     capex_growth_yoy, cap_expend_ratio
        chain_tag_mapping: {ts_code: chain_tag} 映射字典

    Returns:
        标准化后的DataFrame，用于输入IndustryDemandScorer
    """
    df = stock_financial_data.copy()

    # 确保必需字段存在
    if 'ts_code' not in df.columns or 'name' not in df.columns:
        raise ValueError("必须包含ts_code和name字段")

    # 设置索引
    df = df.set_index('ts_code')

    # 补充默认值
    default_cols = [
        'revenue_growth_yoy', 'profit_growth_yoy', 'gross_margin', 'gross_margin_change',
        'contract_liability_yoy', 'order_backlog_yoy', 'advance_payment_yoy',
        'inventory_turnover_change', 'fixed_asset_turnover_change',
        'capex_growth_yoy', 'cap_expend_ratio', 'capacity_utilization',
        'price_change', 'supply_demand_gap'
    ]
    for col in default_cols:
        if col not in df.columns:
            df[col] = np.nan

    # 填充默认值
    df['revenue_growth_yoy'] = df['revenue_growth_yoy'].fillna(0)
    df['profit_growth_yoy'] = df['profit_growth_yoy'].fillna(0)
    df['gross_margin'] = df['gross_margin'].fillna(0.2)
    df['gross_margin_change'] = df['gross_margin_change'].fillna(0)
    df['contract_liability_yoy'] = df['contract_liability_yoy'].fillna(0)
    df['order_backlog_yoy'] = df['order_backlog_yoy'].fillna(0)
    df['advance_payment_yoy'] = df['advance_payment_yoy'].fillna(0)
    df['inventory_turnover_change'] = df['inventory_turnover_change'].fillna(0)
    df['fixed_asset_turnover_change'] = df['fixed_asset_turnover_change'].fillna(0)
    df['capex_growth_yoy'] = df['capex_growth_yoy'].fillna(0)
    df['cap_expend_ratio'] = df['cap_expend_ratio'].fillna(0)
    df['capacity_utilization'] = df['capacity_utilization'].fillna(0.5)
    df['price_change'] = df['price_change'].fillna(0)
    df['supply_demand_gap'] = df['supply_demand_gap'].fillna(0.5)

    # 添加产业链标签
    if chain_tag_mapping:
        df['chain_tag'] = df.index.map(lambda x: chain_tag_mapping.get(x, ''))
    elif 'chain_tag' not in df.columns:
        df['chain_tag'] = ''

    # 重置索引
    df = df.reset_index()

    return df


if __name__ == "__main__":
    # 测试代码
    import numpy as np

    # 模拟数据
    test_data = pd.DataFrame({
        'ts_code': ['000001.SZ', '000002.SZ', '300750.SZ'],
        'name': ['平安银行', '万科A', '宁德时代'],
        'revenue_growth_yoy': [0.15, -0.05, 0.35],
        'profit_growth_yoy': [0.10, -0.15, 0.45],
        'gross_margin': [0.30, 0.25, 0.28],
        'gross_margin_change': [0.02, -0.03, 0.05],
        'contract_liability_yoy': [0.20, -0.10, 0.40],
        'inventory_turnover_change': [0.05, -0.08, 0.15],
        'fixed_asset_turnover_change': [0.03, -0.05, 0.12],
        'capex_growth_yoy': [0.10, -0.20, 0.50],
    })

    # 构建需求数据
    demand_df = build_stock_demand_data(test_data)

    # 评分
    scorer = IndustryDemandScorer()
    result_df = scorer.calc_industry_score(demand_df)
    result_df['chain_tag'] = scorer.identify_chain_tag(demand_df)
    result_df = scorer.rank_stocks(result_df)

    # 输出
    print(scorer.generate_summary(result_df))
    print("\n详细得分:")
    print(result_df[['ts_code', 'name', 'terminal_demand_score', 'order_strength_score',
                      'price_score', 'capacity_score', 'capex_score',
                      'industry_demand_score', 'rank']].to_string(index=False))
