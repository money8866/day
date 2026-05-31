from datetime import datetime, time
from typing import Dict, List, Optional


class FactorCalculator:
    """
    因子计算器
    计算四个关键因子：情绪系数、主线系数、指数系数、回撤系数
    """
    
    @staticmethod
    def calculate_sentiment_factor(sentiment_score: int) -> float:
        """
        计算情绪系数
        
        Args:
            sentiment_score: 情绪评分 (-100到100)
            
        Returns:
            情绪系数 (0到1.2)
        """
        if sentiment_score >= 60:  # 强势市场
            return 1.2
        elif sentiment_score >= 30:  # 偏强市场
            return 1.0
        elif sentiment_score >= 0:  # 震荡市场
            return 0.8
        elif sentiment_score >= -30:  # 偏弱市场
            return 0.5
        else:  # 弱势市场
            return 0.3
    
    @staticmethod
    def calculate_mainline_factor(concept_analysis: Optional[Dict] = None) -> float:
        """
        计算主线系数
        
        Args:
            concept_analysis: 板块分析结果
            
        Returns:
            主线系数 (0.5到1.2)
        """
        if not concept_analysis:
            return 0.7
        
        avg_change = concept_analysis.get("avg_change", 0)
        limit_up = concept_analysis.get("limit_up_count", 0)
        
        if avg_change >= 3 and limit_up >= 5:  # 主线明确
            return 1.2
        elif avg_change >= 2 and limit_up >= 3:  # 主线初步形成
            return 1.0
        elif avg_change >= 1 and limit_up >= 1:  # 板块有异动
            return 0.8
        elif avg_change >= 0:  # 板块持平
            return 0.7
        else:  # 板块下跌
            return 0.5
    
    @staticmethod
    def calculate_index_factor(index_change: float) -> float:
        """
        计算指数系数
        
        Args:
            index_change: 指数涨跌幅（百分比）
            
        Returns:
            指数系数 (0.3到1.2)
        """
        if index_change >= 2:  # 指数大涨
            return 1.2
        elif index_change >= 1:  # 指数上涨
            return 1.0
        elif index_change >= 0:  # 指数持平
            return 0.8
        elif index_change >= -1:  # 指数小跌
            return 0.6
        elif index_change >= -2:  # 指数下跌
            return 0.4
        else:  # 指数大跌
            return 0.3
    
    @staticmethod
    def calculate_drawdown_factor(drawdown: float = 0.0) -> float:
        """
        计算回撤系数
        
        Args:
            drawdown: 当前回撤率（百分比，正数表示回撤）
            
        Returns:
            回撤系数 (0.5到1.0)
        """
        if drawdown <= 0:  # 无回撤，创新高
            return 1.0
        elif drawdown <= 3:  # 小回撤
            return 0.9
        elif drawdown <= 5:  # 中等回撤
            return 0.75
        elif drawdown <= 8:  # 较大回撤
            return 0.6
        elif drawdown <= 10:  # 大回撤
            return 0.5
        else:  # 超回撤
            return 0.3


class MarketSentiment:
    """市场情绪分析"""
    
    def __init__(self):
        self.market_history = []
    
    def analyze_opening_30min(self, quotes: List[Dict]) -> Dict:
        """
        分析开盘30分钟市场情绪
        
        Args:
            quotes: 股票行情列表
            
        Returns:
            情绪分析结果
        """
        if not quotes:
            return {"sentiment_score": 0, "error": "没有行情数据"}
        
        # 统计涨跌
        up_count = sum(1 for q in quotes if q.get("change_pct", 0) > 0)
        down_count = sum(1 for q in quotes if q.get("change_pct", 0) < 0)
        limit_up_count = sum(1 for q in quotes if q.get("change_pct", 0) >= 9.9)
        limit_down_count = sum(1 for q in quotes if q.get("change_pct", 0) <= -9.9)
        
        # 计算平均涨跌幅
        changes = [q.get("change_pct", 0) for q in quotes]
        avg_change = sum(changes) / len(changes) if changes else 0
        
        # 情绪评分 (-100到100)
        sentiment_score = 0
        
        # 涨跌比
        if down_count > 0:
            up_down_ratio = up_count / down_count
            if up_down_ratio > 3:
                sentiment_score += 40
            elif up_down_ratio > 2:
                sentiment_score += 30
            elif up_down_ratio > 1.5:
                sentiment_score += 20
            elif up_down_ratio > 1:
                sentiment_score += 10
            elif up_down_ratio < 0.5:
                sentiment_score -= 40
            elif up_down_ratio < 0.67:
                sentiment_score -= 30
            elif up_down_ratio < 0.8:
                sentiment_score -= 20
        else:
            sentiment_score += 50
        
        # 涨停跌停比
        if limit_down_count > 0:
            lu_ld_ratio = limit_up_count / limit_down_count
            if lu_ld_ratio > 5:
                sentiment_score += 30
            elif lu_ld_ratio > 3:
                sentiment_score += 20
            elif lu_ld_ratio > 1.5:
                sentiment_score += 10
            elif lu_ld_ratio < 0.5:
                sentiment_score -= 30
            elif lu_ld_ratio < 0.7:
                sentiment_score -= 20
        elif limit_up_count > 0:
            sentiment_score += 20
        
        # 平均涨跌幅
        if avg_change > 2:
            sentiment_score += 30
        elif avg_change > 1:
            sentiment_score += 20
        elif avg_change > 0.5:
            sentiment_score += 10
        elif avg_change < -2:
            sentiment_score -= 30
        elif avg_change < -1:
            sentiment_score -= 20
        elif avg_change < -0.5:
            sentiment_score -= 10
        
        # 限制分数范围
        sentiment_score = max(-100, min(100, sentiment_score))
        
        return {
            "sentiment_score": sentiment_score,
            "up_count": up_count,
            "down_count": down_count,
            "up_down_ratio": up_count / max(down_count, 1),
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "avg_change": avg_change,
            "market_type": self._judge_market_type(sentiment_score)
        }
    
    def _judge_market_type(self, sentiment_score: int) -> str:
        """根据情绪评分判断市场类型"""
        if sentiment_score >= 60:
            return "强势市场"
        elif sentiment_score >= 30:
            return "偏强市场"
        elif sentiment_score >= 0:
            return "震荡市场"
        elif sentiment_score >= -30:
            return "偏弱市场"
        else:
            return "弱势市场"


class FourFactorPositionManager:
    """
    四因子仓位管理系统
    总仓位上限 = 情绪系数 × 主线系数 × 指数系数 × 回撤系数
    """
    
    def __init__(self, max_position: float = 1.0):
        """
        Args:
            max_position: 最大仓位上限 (默认100%)
        """
        self.max_position = max_position
        self.sentiment_analyzer = MarketSentiment()
        self.factor_calculator = FactorCalculator()
        self.historical_positions = []
    
    def calculate_position_limit(self, 
                                sentiment_score: int,
                                concept_analysis: Optional[Dict] = None,
                                index_change: float = 0.0,
                                drawdown: float = 0.0) -> Dict:
        """
        根据四个因子计算仓位上限
        
        Args:
            sentiment_score: 情绪评分
            concept_analysis: 主线板块分析
            index_change: 指数涨跌幅
            drawdown: 当前回撤
            
        Returns:
            仓位计算结果字典
        """
        # 计算各因子
        sentiment_factor = self.factor_calculator.calculate_sentiment_factor(sentiment_score)
        mainline_factor = self.factor_calculator.calculate_mainline_factor(concept_analysis)
        index_factor = self.factor_calculator.calculate_index_factor(index_change)
        drawdown_factor = self.factor_calculator.calculate_drawdown_factor(drawdown)
        
        # 计算总仓位上限
        position_limit = sentiment_factor * mainline_factor * index_factor * drawdown_factor * self.max_position
        
        # 确保仓位在合理范围
        position_limit = max(0, min(position_limit, self.max_position))
        
        return {
            "position_limit": position_limit,
            "sentiment_factor": sentiment_factor,
            "mainline_factor": mainline_factor,
            "index_factor": index_factor,
            "drawdown_factor": drawdown_factor,
            "max_position": self.max_position
        }
    
    def get_position_suggestion(self, base_position: float,
                                sentiment_score: int,
                                concept_analysis: Optional[Dict] = None,
                                index_change: float = 0.0,
                                drawdown: float = 0.0) -> Dict:
        """
        结合基础仓位和因子系统，给出最终仓位建议
        
        Args:
            base_position: 盘后建议基础仓位
            sentiment_score: 情绪评分
            concept_analysis: 主线板块分析
            index_change: 指数涨跌幅
            drawdown: 当前回撤
            
        Returns:
            完整的仓位建议
        """
        # 计算仓位上限
        position_data = self.calculate_position_limit(
            sentiment_score, concept_analysis, index_change, drawdown
        )
        
        position_limit = position_data["position_limit"]
        
        # 最终仓位取基础仓位和仓位上限的较小值
        final_position = min(base_position, position_limit)
        
        return {
            "base_position": base_position,
            "position_limit": position_limit,
            "final_position": final_position,
            "factors": {
                "sentiment_factor": position_data["sentiment_factor"],
                "mainline_factor": position_data["mainline_factor"],
                "index_factor": position_data["index_factor"],
                "drawdown_factor": position_data["drawdown_factor"]
            },
            "sentiment_score": sentiment_score,
            "suggestion": self._generate_suggestion(final_position, position_limit)
        }
    
    def _generate_suggestion(self, final_position: float, position_limit: float) -> str:
        """生成操作建议"""
        if final_position >= 0.8:
            return "激进型：可以重仓操作，把握强势机会"
        elif final_position >= 0.6:
            return "积极型：建议中等仓位，精选个股"
        elif final_position >= 0.4:
            return "稳健型：半仓操作，攻守兼备"
        elif final_position >= 0.2:
            return "谨慎型：轻仓观望，控制风险"
        else:
            return "防御型：建议空仓或极轻仓，耐心等待"


class TradingTimeHelper:
    """交易时间助手"""
    
    @staticmethod
    def is_trading_time() -> bool:
        """判断是否在交易时间"""
        now = datetime.now().time()
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)
        
        return (morning_start <= now <= morning_end or 
                afternoon_start <= now <= afternoon_end)
    
    @staticmethod
    def is_opening_30min() -> bool:
        """判断是否在开盘30分钟内"""
        now = datetime.now().time()
        return time(9, 30) <= now <= time(10, 0)
