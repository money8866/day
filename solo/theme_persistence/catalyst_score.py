# -*- coding: utf-8 -*-
"""
催化剂持续时间评分 (Catalyst Duration Score) — 权重 15%

评估主题驱动因素能持续多久。

分类:
  A. 结构性产业周期 (6-24个月)   → 100分
     例: AI基础设施, 半导体周期, 能源转型, 人形机器人
  B. 政策周期 (3-12个月)         → 80分
     例: 国产替代, 数据要素, 统一大市场
  C. 订单周期 (1-6个月)          → 60分
     例: 军工订单, 基建项目
  D. 事件投机 (<1个月)           → 30分
     例: 突发事件, 季节性炒作
"""
import os
import json

# 主题催化剂分类映射表
# 可通过外部JSON文件覆盖配置
CATALYST_MAPPING = {
    # === A. 结构性产业周期 (100分) ===
    '半导体': 'A', '芯片': 'A', '半导体设备': 'A', '科创半导体': 'A',
    '人工智能': 'A', '云计算': 'A', '软件': 'A',
    '通信': 'A', '消费电子': 'A',
    '新能源': 'A', '光伏': 'A', '储能': 'A', '电池': 'A', '新能源车': 'A',
    '机器人': 'A', '工业母机': 'A',
    '创新药': 'A', '医疗器械': 'A', '医药': 'A',

    # === B. 政策周期 (80分) ===
    '金融科技': 'B',
    '航空航天': 'B', '军工': 'B',
    '有色金属': 'B',

    # === C. 订单周期 (60分) ===
    '电网设备': 'C', '电力': 'C',
    '化工': 'C', '煤炭': 'C', '钢铁': 'C',

    # === D. 事件投机 (30分) ===
    '游戏': 'D',
    '消费': 'D', '食品饮料': 'D', '酒': 'D', '家电': 'D',
    '证券': 'D', '银行': 'D', '红利': 'D',
    '黄金': 'D',
}

CATALYST_SCORE = {
    'A': 100,  # 结构性产业周期
    'B': 80,   # 政策周期
    'C': 60,   # 订单周期
    'D': 30,   # 事件投机
}

CATALYST_DURATION = {
    'A': '6-24个月',
    'B': '3-12个月',
    'C': '1-6个月',
    'D': '<1个月',
}

# 外部配置文件路径 (可选)
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'config', 'catalyst_config.json')


def _load_external_mapping():
    """加载外部催化剂配置 (如果存在)"""
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


def calculate_catalyst_duration(theme_name: str) -> dict:
    """
    计算催化剂持续时间评分

    Args:
        theme_name: 主题名称 (如 '半导体', '人工智能', '创新药')

    Returns:
        {'score': 0-100, 'catalyst_type': 'A/B/C/D',
         'duration': '6-24个月', 'details': {...}}
    """
    # 尝试从外部配置加载
    ext_mapping = _load_external_mapping()
    mapping = ext_mapping or CATALYST_MAPPING

    # 匹配主题
    catalyst_type = mapping.get(theme_name, 'D')  # 默认事件投机

    # 关键词模糊匹配
    if catalyst_type == 'D' and theme_name:
        for key, cat in mapping.items():
            if key in theme_name or theme_name in key:
                catalyst_type = cat
                break

    score = CATALYST_SCORE.get(catalyst_type, 30)
    duration = CATALYST_DURATION.get(catalyst_type, '<1个月')

    return {
        'score': float(score),
        'catalyst_type': catalyst_type,
        'duration': duration,
        'details': {
            'theme_name': theme_name,
            'catalyst_category': f'{catalyst_type}. {duration}',
        }
    }
