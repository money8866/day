#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四因子仓位管理系统 - 示例文件
演示如何使用四因子系统计算仓位
"""

import sys
sys.path.insert(0, '/workspace')

from position_optimizer import FourFactorPositionManager


def main():
    print("="*70)
    print("四因子仓位管理系统 - 示例")
    print("="*70)
    
    # 创建仓位管理器
    manager = FourFactorPositionManager(max_position=1.0)
    
    # 场景1：强势市场
    print("\n" + "="*70)
    print("【场景1】强势市场 - 情绪高涨，主线明确")
    print("="*70)
    
    result1 = manager.get_position_suggestion(
        base_position=0.6,
        sentiment_score=75,  # 强势市场
        concept_analysis={
            "avg_change": 4.2,
            "limit_up_count": 12
        },
        index_change=2.5,  # 指数大涨
        drawdown=0.0  # 无回撤
    )
    
    print_position_result(result1)
    
    # 场景2：弱势市场
    print("\n" + "="*70)
    print("【场景2】弱势市场 - 情绪低迷，防御为主")
    print("="*70)
    
    result2 = manager.get_position_suggestion(
        base_position=0.5,
        sentiment_score=-40,  # 弱势市场
        concept_analysis={
            "avg_change": -1.5,
            "limit_up_count": 1
        },
        index_change=-2.1,  # 指数下跌
        drawdown=5.0  # 中等回撤
    )
    
    print_position_result(result2)
    
    # 场景3：震荡市场
    print("\n" + "="*70)
    print("【场景3】震荡市场 - 中等仓位，攻守兼备")
    print("="*70)
    
    result3 = manager.get_position_suggestion(
        base_position=0.5,
        sentiment_score=20,  # 震荡偏强
        concept_analysis={
            "avg_change": 0.8,
            "limit_up_count": 4
        },
        index_change=0.5,  # 指数微涨
        drawdown=2.0  # 小幅回撤
    )
    
    print_position_result(result3)
    
    # 总结
    print("\n" + "="*70)
    print("【使用说明】")
    print("="*70)
    print("""
四因子系统公式：
  总仓位上限 = 情绪系数 × 主线系数 × 指数系数 × 回撤系数 × 最大仓位
  最终建议仓位 = min(盘后建议仓位, 四因子计算仓位上限)

各因子范围：
  情绪系数：0.3-1.2（弱势-强势）
  主线系数：0.5-1.2（无主线-主线明确）
  指数系数：0.3-1.2（大跌-大涨）
  回撤系数：0.3-1.0（超大回撤-无回撤）

实际使用建议：
  1. 每天盘后填写 position_config.csv 中的基础仓位
  2. 开盘30分钟后系统会根据实时行情自动计算四个因子
  3. 根据最终建议仓位调整当天操作策略
  4. 可以根据自己的风险偏好调整因子参数
    """)


def print_position_result(result):
    """格式化输出仓位计算结果"""
    print(f"\n【基础数据】")
    print(f"  盘后建议仓位: {result['base_position'] * 100:.1f}%")
    print(f"  情绪评分: {result['sentiment_score']}")
    
    print(f"\n【四因子分解】")
    factors = result['factors']
    print(f"  情绪系数: {factors['sentiment_factor']:.2f}")
    print(f"  主线系数: {factors['mainline_factor']:.2f}")
    print(f"  指数系数: {factors['index_factor']:.2f}")
    print(f"  回撤系数: {factors['drawdown_factor']:.2f}")
    
    print(f"\n【仓位计算】")
    print(f"  四因子计算仓位上限: {result['position_limit'] * 100:.1f}%")
    print(f"  ★ 最终建议仓位: {result['final_position'] * 100:.1f}%")
    
    print(f"\n【操作建议】")
    print(f"  {result['suggestion']}")


if __name__ == "__main__":
    main()
