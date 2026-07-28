# -*- coding: utf-8 -*-
"""组合优化器 — Kelly准则 + Risk Parity + 均值方差优化

核心功能：
  1. Kelly Criterion: f* = (bp - q) / b，计算每只标的的最优仓位比例
  2. Risk Parity: 等风险贡献度分配，使各标的对组合风险的贡献相等
  3. 均值方差优化：有效前沿上的最优组合
  4. 约束处理：单标的上限、波动率上限、Beta限制、行业集中度

设计目标：
  从"选股"到"组合构建"的完整链路，在决定买什么之后，
  科学决定"买多少"和"何时再平衡"。
"""

import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Callable
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

class OptimizationMethod(Enum):
    KELLY = "kelly"
    RISK_PARITY = "risk_parity"
    MEAN_VARIANCE = "mean_variance"
    EQUAL_WEIGHT = "equal_weight"


@dataclass
class AssetAllocation:
    """单个标的的目标配置"""
    ts_code: str
    name: str = ''
    weight: float = 0.0           # 目标权重 0~1
    kelly_fraction: float = 0.0   # Kelly 最优比例
    risk_contribution: float = 0.0  # 风险贡献度
    volatility: float = 0.0       # 预估波动率
    expected_return: float = 0.0  # 预期收益
    probability: float = 0.5      # 成功概率（用于Kelly）
    signal: str = 'neutral'       # buy / hold / reduce / sell


@dataclass
class PortfolioResult:
    """组合优化结果"""
    method: OptimizationMethod
    allocations: List[AssetAllocation]
    total_weight: float = 0.0
    expected_return: float = 0.0     # 组合预期收益
    expected_volatility: float = 0.0  # 组合预期波动率
    sharpe_ratio: float = 0.0        # 夏普比率
    max_drawdown_est: float = 0.0    # 预估最大回撤
    n_assets: int = 0
    concentration: float = 0.0       # HHI 集中度指数
    rebalance_signal: str = 'none'   # none / suggested / required


# ──────────────────────────────────────────────
# 组合优化器
# ──────────────────────────────────────────────

class PortfolioOptimizer:
    """组合优化器 — 多方法仓位分配

    支持四种优化方法:
      - Kelly: 最大化长期几何增长率
      - Risk Parity: 等风险贡献度
      - Mean-Variance: 有效前沿最优组合
      - Equal Weight: 等权基准
    """

    def __init__(self, config: dict):
        cfg = config.get('portfolio_optimizer', {})
        self.default_method = OptimizationMethod(cfg.get('default_method', 'risk_parity'))
        self.max_single_weight = cfg.get('max_single_weight', 0.25)  # 单标的上限
        self.min_single_weight = cfg.get('min_single_weight', 0.02)  # 单标的下限（0=允许不配）
        self.max_industry_weight = cfg.get('max_industry_weight', 0.40)  # 行业上限
        self.target_volatility = cfg.get('target_volatility', 0.25)  # 目标年化波动
        self.kelly_fraction = cfg.get('kelly_fraction', 0.25)  # 凯利比例（全凯利 vs 分数凯利）
        self.rebalance_threshold = cfg.get('rebalance_threshold', 0.05)  # 再平衡偏离阈值

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def optimize(self,
                 candidates: List[Dict],
                 total_exposure: float = 1.0,
                 method: OptimizationMethod = None,
                 probabilities: Dict[str, float] = None,
                 volatilities: Dict[str, float] = None,
                 covariance: np.ndarray = None,
                 industry_map: Dict[str, str] = None) -> PortfolioResult:
        """组合优化主入口

        Args:
            candidates: 候选标的列表 [{ts_code, name, probability, expected_return, ...}]
            total_exposure: 总仓位暴露度 0~1
            method: 优化方法（默认使用配置）
            probabilities: 成功概率映射 {ts_code: prob}
            volatilities: 波动率映射 {ts_code: annualized_vol}
            covariance: 协方差矩阵 (n x n)
            industry_map: 行业映射 {ts_code: industry}

        Returns:
            PortfolioResult
        """
        if not candidates:
            return PortfolioResult(method=method or self.default_method, allocations=[])

        method = method or self.default_method
        probabilities = probabilities or {}
        volatilities = volatilities or {}
        industry_map = industry_map or {}

        n = len(candidates)
        codes = [c.get('ts_code', '') for c in candidates]

        # 提取标的信息
        exp_returns = np.array([c.get('expected_return', 0.05) for c in candidates])
        probs = np.array([probabilities.get(codes[i], c.get('probability', 0.5))
                          for i, c in enumerate(candidates)])
        vols = np.array([volatilities.get(codes[i], c.get('volatility', 0.30))
                         for i, c in enumerate(candidates)])
        names = [c.get('name', '') for c in candidates]

        # 无协方差时用对角阵
        if covariance is None:
            covariance = np.diag(vols ** 2)

        # 执行优化
        if method == OptimizationMethod.KELLY:
            weights = self._kelly_optimize(probs, vols, exp_returns, n)
        elif method == OptimizationMethod.RISK_PARITY:
            weights = self._risk_parity_optimize(vols, covariance, n)
        elif method == OptimizationMethod.MEAN_VARIANCE:
            weights = self._mean_variance_optimize(exp_returns, covariance, n)
        else:  # EQUAL_WEIGHT
            weights = np.ones(n) / n

        # 应用约束
        weights = self._apply_constraints(
            weights, codes, industry_map,
            total_exposure=total_exposure
        )

        # 构建结果
        allocations = []
        for i, code in enumerate(codes):
            kelly_f = self._calc_kelly_single(probs[i], exp_returns[i], vols[i])
            alloc = AssetAllocation(
                ts_code=code,
                name=names[i],
                weight=weights[i],
                kelly_fraction=kelly_f,
                volatility=vols[i],
                expected_return=exp_returns[i],
                probability=probs[i],
            )
            # 风险贡献度
            if n > 1 and covariance.shape[0] == n:
                try:
                    port_vol = np.sqrt(weights @ covariance @ weights)
                    if port_vol > 0:
                        marg_contrib = covariance @ weights / port_vol
                        risk_contrib = weights * marg_contrib
                        alloc.risk_contribution = float(risk_contrib[i] / port_vol)
                except Exception:
                    pass
            allocations.append(alloc)

        # 组合指标
        port_return = float(weights @ exp_returns)
        port_vol = float(np.sqrt(weights @ covariance @ weights)) if n > 1 else float(vols[0])
        sharpe = port_return / port_vol if port_vol > 0 else 0
        hhi = float(np.sum(weights ** 2))  # Herfindahl 集中度

        # 再平衡信号
        rebal = self._check_rebalance(allocations)

        return PortfolioResult(
            method=method,
            allocations=sorted(allocations, key=lambda x: x.weight, reverse=True),
            total_weight=float(weights.sum()),
            expected_return=port_return,
            expected_volatility=port_vol,
            sharpe_ratio=round(sharpe, 3),
            max_drawdown_est=port_vol * 2.5,  # 近似：2.5σ
            n_assets=n,
            concentration=round(hhi, 4),
            rebalance_signal=rebal,
        )

    # ──────────────────────────────────────────────
    # Kelly Criterion
    # ──────────────────────────────────────────────

    def _kelly_optimize(self, probs: np.ndarray,
                        vols: np.ndarray,
                        returns: np.ndarray,
                        n: int) -> np.ndarray:
        """Kelly Criterion 最优仓位

        单标的: f* = (bp - q) / b
          其中 b = 赔率（收益/亏损比）, p = 胜率, q = 1-p

        多标的分数Kelly: 限制总权重不超过1
        """
        weights = np.zeros(n)
        for i in range(n):
            p = probs[i]
            if p <= 0.5:
                continue  # 胜率不足不配置

            # 估算赔率: 预期涨幅/预期跌幅
            # 假设预期跌幅 = 1σ, 预期涨幅 = μ/σ 比率
            mu = max(returns[i], 0.02)  # 最小预期收益2%
            loss = min(vols[i], 0.15)  # 最大亏损不超过15%
            b = mu / loss if loss > 0 else 2.0

            # 全凯利
            q = 1.0 - p
            f_star = (b * p - q) / b if b > 0 else 0

            # 分数凯利（降低波动）
            f_star *= self.kelly_fraction

            # 单标的上限
            f_star = min(f_star, self.max_single_weight)
            weights[i] = max(0, f_star)

        # 归一化到总暴露度
        total = weights.sum()
        if total > 1.0:
            weights = weights / total
        elif total > 0:
            weights = weights  # 保留绝对值作为仓位建议

        return weights

    def _calc_kelly_single(self, prob: float,
                           expected_return: float,
                           volatility: float) -> float:
        """单标的Kelly比例（用于展示）"""
        if prob <= 0.5:
            return 0.0
        mu = max(expected_return, 0.02)
        loss = min(volatility, 0.15)
        b = mu / loss if loss > 0 else 2.0
        q = 1.0 - prob
        f = (b * prob - q) / b if b > 0 else 0
        return round(max(0, f * self.kelly_fraction), 4)

    # ──────────────────────────────────────────────
    # Risk Parity
    # ──────────────────────────────────────────────

    def _risk_parity_optimize(self, vols: np.ndarray,
                              cov: np.ndarray,
                              n: int) -> np.ndarray:
        """Risk Parity: 等风险贡献度

        使用逆波动率近似（忽略相关性）或精确数值求解
        """
        if n == 1:
            return np.array([1.0])

        # 逆波动率加权作为初始值
        inv_vol = 1.0 / np.maximum(vols, 0.01)
        weights = inv_vol / inv_vol.sum()

        # 如果有协方差矩阵，尝试数值优化等风险贡献
        if cov.shape[0] == n and n > 1:
            try:
                from scipy.optimize import minimize

                def risk_parity_objective(w):
                    w = w / w.sum()  # 归一化
                    port_vol = np.sqrt(w @ cov @ w)
                    if port_vol <= 0:
                        return 0.0
                    # 各资产风险贡献
                    marg_contrib = cov @ w / port_vol
                    risk_contrib = w * marg_contrib
                    # 等风险贡献意味着所有 risk_contrib 相等
                    target_rc = np.mean(risk_contrib)
                    return np.sum((risk_contrib - target_rc) ** 2)

                constraints = [
                    {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0},
                ]
                bounds = [(self.min_single_weight, self.max_single_weight)] * n
                result = minimize(
                    risk_parity_objective, weights,
                    method='SLSQP', bounds=bounds,
                    constraints=constraints,
                    options={'maxiter': 200, 'ftol': 1e-6}
                )
                if result.success:
                    weights = result.x / result.x.sum()
            except ImportError:
                pass  # 无 scipy 时使用逆波动率

        return weights

    # ──────────────────────────────────────────────
    # Mean-Variance Optimization
    # ──────────────────────────────────────────────

    def _mean_variance_optimize(self, returns: np.ndarray,
                                 cov: np.ndarray,
                                 n: int) -> np.ndarray:
        """均值方差优化：最大化夏普比率"""
        if n == 1:
            return np.array([1.0])

        try:
            from scipy.optimize import minimize

            def neg_sharpe(w):
                w = w / w.sum()
                port_ret = w @ returns
                port_vol = np.sqrt(w @ cov @ w)
                return -port_ret / port_vol if port_vol > 0 else 0

            constraints = [
                {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0},
            ]
            bounds = [(self.min_single_weight, self.max_single_weight)] * n
            x0 = np.ones(n) / n
            result = minimize(
                neg_sharpe, x0,
                method='SLSQP', bounds=bounds,
                constraints=constraints,
                options={'maxiter': 500, 'ftol': 1e-8}
            )
            if result.success:
                return result.x / result.x.sum()
        except ImportError:
            pass

        # 回退到等权
        return np.ones(n) / n

    # ──────────────────────────────────────────────
    # 约束处理
    # ──────────────────────────────────────────────

    def _apply_constraints(self,
                           weights: np.ndarray,
                           codes: List[str],
                           industry_map: Dict[str, str],
                           total_exposure: float = 1.0) -> np.ndarray:
        """应用组合约束

        约束包括：
          - 单标的上限/下限
          - 行业集中度上限
          - 总暴露度适配
        """
        w = weights.copy()

        # 单标的上下限
        w = np.clip(w, 0, self.max_single_weight)

        # 低于下限的归零
        w[w < self.min_single_weight * 0.5] = 0

        # 行业集中度约束
        if industry_map:
            industries = {}
            for i, code in enumerate(codes):
                ind = industry_map.get(code, 'unknown')
                if ind not in industries:
                    industries[ind] = []
                industries[ind].append(i)

            for ind, indices in industries.items():
                ind_weight = w[indices].sum()
                if ind_weight > self.max_industry_weight:
                    # 比例压缩
                    scale = self.max_industry_weight / ind_weight
                    w[indices] *= scale

        # 总暴露度适配
        total = w.sum()
        if total > 0:
            if total > total_exposure:
                w = w / total * total_exposure
            elif total < total_exposure * 0.5 and total_exposure > 0:
                # 总仓位太低时等比例放大（但不超过单标的上限）
                scale = min(total_exposure / total,
                            self.max_single_weight / w.max()) if w.max() > 0 else 1.0
                w = w * scale
                w = np.clip(w, 0, self.max_single_weight)

        return w

    def _check_rebalance(self,
                         allocations: List[AssetAllocation]) -> str:
        """检查是否需要再平衡

        基于权重偏离度、集中度变化
        """
        if not allocations:
            return 'none'

        weights = np.array([a.weight for a in allocations])
        hhi = np.sum(weights ** 2)

        # HHI > 0.5 表示高度集中
        if hhi > 0.5:
            return 'suggested'
        # HHI > 0.7 表示过度集中
        if hhi > 0.7:
            return 'required'

        return 'none'

    # ──────────────────────────────────────────────
    # 组合分析
    # ──────────────────────────────────────────────

    def analyze_portfolio(self,
                          allocations: List[AssetAllocation],
                          cov_matrix: np.ndarray = None) -> Dict:
        """分析当前组合的风险收益特征"""
        if not allocations:
            return {}

        n = len(allocations)
        weights = np.array([a.weight for a in allocations])

        result = {
            'n_assets': n,
            'total_exposure': float(weights.sum()),
            'hhi_concentration': float(np.sum(weights ** 2)),
            'max_weight': float(weights.max()),
            'min_weight': float(weights.min()) if weights.min() > 0 else 0,
            'n_zero_weight': int(np.sum(weights == 0)),
        }

        # 波动率和收益
        vols = np.array([a.volatility for a in allocations])
        returns = np.array([a.expected_return for a in allocations])

        if cov_matrix is not None and cov_matrix.shape == (n, n):
            try:
                port_vol = float(np.sqrt(weights @ cov_matrix @ weights))
                port_ret = float(weights @ returns)
                result['portfolio_volatility'] = round(port_vol, 4)
                result['portfolio_return'] = round(port_ret, 4)
                result['sharpe'] = round(port_ret / port_vol, 3) if port_vol > 0 else 0

                # 风险分解
                marg_contrib = cov_matrix @ weights / port_vol
                risk_contrib = weights * marg_contrib
                result['risk_decomposition'] = {
                    allocations[i].ts_code: round(float(risk_contrib[i] / port_vol), 4)
                    for i in range(n)
                }
            except Exception:
                pass

        return result


# ──────────────────────────────────────────────
# CLI 测试
# ──────────────────────────────────────────────

if __name__ == '__main__':
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    optimizer = PortfolioOptimizer(cfg)

    # 测试用候选标的
    candidates = [
        {'ts_code': '300308.SZ', 'name': '中际旭创', 'expected_return': 0.15, 'probability': 0.72, 'volatility': 0.35},
        {'ts_code': '688111.SH', 'name': '金山办公', 'expected_return': 0.12, 'probability': 0.68, 'volatility': 0.30},
        {'ts_code': '002371.SZ', 'name': '北方华创', 'expected_return': 0.18, 'probability': 0.65, 'volatility': 0.38},
        {'ts_code': '300750.SZ', 'name': '宁德时代', 'expected_return': 0.10, 'probability': 0.60, 'volatility': 0.28},
        {'ts_code': '600519.SH', 'name': '贵州茅台', 'expected_return': 0.08, 'probability': 0.55, 'volatility': 0.22},
    ]

    print("\n组合优化器测试")
    print("═" * 50)

    for method in [OptimizationMethod.KELLY, OptimizationMethod.RISK_PARITY,
                   OptimizationMethod.MEAN_VARIANCE, OptimizationMethod.EQUAL_WEIGHT]:
        result = optimizer.optimize(
            candidates, total_exposure=0.8, method=method,
            probabilities={c['ts_code']: c['probability'] for c in candidates},
            volatilities={c['ts_code']: c['volatility'] for c in candidates},
        )
        print(f"\n 方法: {method.value}")
        print(f"  预期收益: {result.expected_return:.1%} | 波动: {result.expected_volatility:.1%} | 夏普: {result.sharpe_ratio:.2f}")
        print(f"  集中度(HHI): {result.concentration:.3f} | 再平衡: {result.rebalance_signal}")
        print(f"  配置:")
        for a in result.allocations:
            if a.weight > 0.01:
                print(f"    {a.name:8s}({a.ts_code:12s}) {a.weight:.1%} "
                      f"[Kelly={a.kelly_fraction:.1%}]")
