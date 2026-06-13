import sys
sys.path.insert(0, '.')
import json

# 读取主题配置
with open('theme.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 获取主题数据
themes = data.get('HOT_THEMES', data)

# 获取所有主题名称
print('所有主题:', list(themes.keys())[:10])

# 检查物理AI主题
if '物理AI' in themes:
    phys_ai = themes['物理AI']
    print('\n物理AI主题配置:')
    print('行业:', phys_ai.get('industry', []))
    print('概念:', phys_ai.get('concept', []))
    print('核心公司:', phys_ai.get('core_companies', []))
    print('太辰光是否在核心公司中:', '太辰光' in phys_ai.get('core_companies', []))
else:
    print('物理AI主题不存在')