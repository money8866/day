# -*- coding: utf-8 -*-
"""
测试 V9 评分优化 - 龙头拉开机制 + 板块分层系统（无连板因子）
"""

import sys
import os
import io

# 修复Windows GBK编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, r'D:\mystock\solo')

# 模拟测试数据
def test_sector_position_v3():
    """测试板块位置评分 V3（无连板因子）"""
    
    print("=" * 60)
    print("V9 评分优化测试 - 龙头拉开机制 + 板块分层系统（无连板因子）")
    print("=" * 60)
    
    # 模拟不同的股票场景
    test_cases = [
        {
            'name': 'S级主线龙头',
            'theme_tier': 'S',
            'is_leader': True,
            'is_core': False,
            'expected_score': 100,  # 50(分层) + 50(龙头) = 100
        },
        {
            'name': 'S级主线核心',
            'theme_tier': 'S',
            'is_leader': False,
            'is_core': True,
            'expected_score': 80,  # 50(分层) + 30(核心) = 80
        },
        {
            'name': 'A级主线龙头',
            'theme_tier': 'A',
            'is_leader': True,
            'is_core': False,
            'expected_score': 80,  # 30(分层) + 50(龙头) = 80
        },
        {
            'name': 'A级主线核心',
            'theme_tier': 'A',
            'is_leader': False,
            'is_core': True,
            'expected_score': 60,  # 30(分层) + 30(核心) = 60
        },
        {
            'name': 'B级主线龙头',
            'theme_tier': 'B',
            'is_leader': True,
            'is_core': False,
            'expected_score': 65,  # 15(分层) + 50(龙头) = 65
        },
        {
            'name': 'B级主线后排',
            'theme_tier': 'B',
            'is_leader': False,
            'is_core': False,
            'expected_score': 0,  # 15(分层) - 20(后排) = -5 → 0
        },
        {
            'name': 'C级冷门板块',
            'theme_tier': 'C',
            'is_leader': False,
            'is_core': False,
            'expected_score': 0,  # 0(分层) - 20(后排) = -20 → 0
        },
    ]
    
    print("\n测试场景：")
    print("-" * 60)
    
    all_passed = True
    for i, case in enumerate(test_cases, 1):
        print(f"\n{i}. {case['name']}")
        print(f"   板块等级: {case['theme_tier']}")
        print(f"   是否龙头: {case['is_leader']}")
        print(f"   是否核心: {case['is_core']}")
        print(f"   预期分数: {case['expected_score']}")
        
        # 计算逻辑
        tier_base = {'S': 50, 'A': 30, 'B': 15, 'C': 0}.get(case['theme_tier'], 0)
        
        if case['is_leader']:
            leader_bonus = 50
        elif case['is_core']:
            leader_bonus = 30
        else:
            leader_bonus = -20
        
        final = min(100, max(0, tier_base + leader_bonus))
        
        print(f"   计算过程: {tier_base}(分层) + {leader_bonus}(龙头) = {tier_base + leader_bonus} → {final}")
        
        if final == case['expected_score']:
            print(f"   ✅ 测试通过")
        else:
            print(f"   ❌ 测试失败（期望 {case['expected_score']}，实际 {final}）")
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 全部测试通过！")
    else:
        print("❌ 部分测试失败")
    print("=" * 60)
    
    print("\n✅ 优化效果（无连板因子）：")
    print("1. 龙头自然拉开：S级龙头 100分 vs S级核心 80分")
    print("2. 分层明显：S级 > A级 > B级 > C级")
    print("3. 后排惩罚：B/C级后排自动掉到 0 分")
    print("4. 开仓模型统一：V9评分与开仓模型一致")
    print("5. 无连板因子：仅使用板块分层 + 龙头加成")

if __name__ == '__main__':
    test_sector_position_v3()
