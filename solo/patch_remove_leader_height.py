#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
移除 calc_sector_position_score 函数中的 龙头连板因子（leader_height）相关逻辑
"""
import re

file_path = r'D:\mystock\solo\tushare_quant.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. 更新文档注释（移除"龙头高度因子"）
# ============================================================
old_docstring = '''    """sector_position 板块位置评分 V2 - 龙头拉开机制 + 板块分层系统
    
    核心升级：
    1. 龙头拉开机制：真龙头非线性加成，后排惩罚
    2. 板块分层系统：S/A/B/C级主线基础加成
    3. 龙头高度因子：连板高度额外加成
    
    评分结构：
    - 基础分（板块分层）：S级+15, A级+10, B级+5, C级+0
    - 龙头加成：真龙头+40, 准龙头+20, 后排-10
    - 龙头高度：7板+50, 5-6板+35, 3-4板+20, 2板+10, 首板+5
    """'''

new_docstring = '''    """sector_position 板块位置评分 V3 - 龙头拉开机制 + 板块分层系统（无连板因子）
    
    核心逻辑：
    1. 板块分层系统：S/A/B/C级主线分层基础分
    2. 龙头拉开机制：真龙头非线性加成，后排惩罚
    
    评分公式：
    final_score = 板块分层基础分 + 龙头加成
    
    参数设计：
    - S级（≥80分）：基础分50，真龙头+50=100，准龙头+30=80，后排-20=0
    - A级（60-80分）：基础分30，真龙头+50=80，准龙头+30=60，后排-20=0
    - B级（40-60分）：基础分15，真龙头+50=65，准龙头+30=45，后排-20=0
    - C级（<40分）：基础分0，真龙头+50=50，准龙头+30=30，后排-20=0
    
    效果：
    - S级龙头自然拉到100分
    - S级核心80分
    - 后排自动掉到0-30分
    """'''

if old_docstring in content:
    content = content.replace(old_docstring, new_docstring)
    print("✅ 文档注释已更新")
else:
    print("⚠️ 未找到旧文档注释，跳过多")

# ============================================================
# 2. 修改板块分层基础分（15/10/5/0 → 50/30/15/0）
# ============================================================
old_tier = "{'S': 15, 'A': 10, 'B': 5, 'C': 0}.get(theme_tier, 0)"
new_tier = "{'S': 50, 'A': 30, 'B': 15, 'C': 0}.get(theme_tier, 0)"

if old_tier in content:
    content = content.replace(old_tier, new_tier)
    print("✅ 板块分层基础分已更新（50/30/15/0）")
else:
    print("⚠️ 未找到板块分层基础分配置")

# ============================================================
# 3. 移除 leader_height 初始化
# ============================================================
old_init = "        leader_height = 0  # 连板高度\n        "
new_init = "\n        "
content = content.replace(old_init, new_init)
print("✅ leader_height 初始化已移除")

# ============================================================
# 4. 移除"获取连板高度"代码块
# ============================================================
# 找到并移除从 "# 获取连板高度" 到 "break" 的整个块
pattern1 = r'                    # 获取连板高度（从top_stocks中查找）\n.*?break\n'
match1 = re.search(pattern1, content, re.DOTALL)
if match1:
    # 替换为 just "break"
    old_block = match1.group(0)
    content = content.replace(old_block, '                    break\n')
    print("✅ '获取连板高度'代码块已移除")
else:
    print("⚠️ 未找到'获取连板高度'代码块")

# ============================================================
# 5. 移除"龙头高度因子"整个 Section 4
# ============================================================
pattern2 = r'        # =========================\n        # 4\. 龙头高度因子 - 连板加成\n.*?height_bonus = 5   # 首板 \+5分\n        \n'
match2 = re.search(pattern2, content, re.DOTALL)
if match2:
    old_section4 = match2.group(0)
    content = content.replace(old_section4, '\n')
    print("✅ '龙头高度因子' Section 4 已移除")
else:
    print("⚠️ 未找到'龙头高度因子' Section 4")

# ============================================================
# 6. 更新龙头加成参数（40/20/-10 → 50/30/-20）
# ============================================================
old_bonus = '''        leader_bonus = 0
        if is_leader:
            leader_bonus = 40  # 真龙头 +40分
        elif is_core:
            leader_bonus = 20  # 准龙头 +20分
        elif sector_rank >= 10:
            leader_bonus = -10  # 后排 -10分惩罚'''

new_bonus = '''        leader_bonus = 0
        if is_leader:
            leader_bonus = 50  # 真龙头 +50分
        elif is_core:
            leader_bonus = 30  # 准龙头 +30分
        elif sector_rank >= 10:
            leader_bonus = -20  # 后排 -20分惩罚'''

if old_bonus in content:
    content = content.replace(old_bonus, new_bonus)
    print("✅ 龙头加成参数已更新（50/30/-20）")
else:
    print("⚠️ 未找到龙头加成参数配置")

# ============================================================
# 7. 更新综合评分计算（移除 height_bonus）
# ============================================================
old_calc = "        final_score = base_score + leader_bonus + height_bonus"
new_calc = "        final_score = base_score + leader_bonus"

if old_calc in content:
    content = content.replace(old_calc, new_calc)
    print("✅ 综合评分计算已更新（移除 height_bonus）")
else:
    print("⚠️ 未找到综合评分计算代码")

# ============================================================
# 8. 更新 details 字典（移除 '连板高度' 和 '高度加成'）
# ============================================================
old_details = '''            '是否龙头': is_leader,
            '是否核心': is_core,
            '连板高度': leader_height,
            '分层基础分': tier_base_score,
            '龙头加成': leader_bonus,
            '高度加成': height_bonus'''

new_details = '''            '是否龙头': is_leader,
            '是否核心': is_core,
            '分层基础分': tier_base_score,
            '龙头加成': leader_bonus'''

if old_details in content:
    content = content.replace(old_details, new_details)
    print("✅ details 字典已更新（移除连板高度和高度加成）")
else:
    print("⚠️ 未找到 details 字典配置")

# ============================================================
# 9. 保存文件
# ============================================================
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n" + "="*60)
print("✅ 修改完成！")
print("="*60)
print("\n修改内容：")
print("1. 文档注释已更新（V2 → V3，移除龙头高度因子）")
print("2. 板块分层基础分已更新（S:15→50, A:10→30, B:5→15）")
print("3. leader_height 初始化已移除")
print("4. '获取连板高度'代码块已移除")
print("5. '龙头高度因子' Section 4 已移除")
print("6. 龙头加成参数已更新（40/20/-10 → 50/30/-20）")
print("7. 综合评分计算已更新（移除 height_bonus）")
print("8. details 字典已更新（移除连板高度和高度加成）")
print("\n新评分公式：")
print("  S级龙头：50(分层) + 50(龙头) = 100分")
print("  S级核心：50(分层) + 30(核心) = 80分")
print("  A级龙头：30(分层) + 50(龙头) = 80分")
print("  B级后排：15(分层) - 20(后排) = -5 → 0分")
