"""
创建精选股票池 - 基于BullScore高分股
"""
import os

# 基于之前BullScore结果，选择有数据且活跃的股票
selected_codes = [
    # BullScore TOP20 (已知有数据)
    '600460.SH',  # 士兰微
    '688002.SH',  # 睿创微纳
    '688525.SH',  # 佰维存储
    '603256.SH',  # 宏和科技
    '688498.SH',  # 蜂助手
    '688519.SH',  # 南亚新材
    '603629.SH',  # 利扬芯片
    # 趋势股 (已知活跃)
    '300661.SZ',  # 圣邦股份
    '688187.SH',  # 时代电气
    '002049.SZ',  # 紫光国微
    # 补充一些
    '600584.SH',  # 长电科技
    '688008.SH',  # 澜起科技
    '002185.SZ',  # 华天科技
    '300223.SZ',  # 北京君正
    '688012.SH',  # 中微公司
    '688396.SH',  # 华润微
    '600667.SH',  # 太极实业
    '002151.SZ',  # 深天马A
    '300327.SZ',  # 中颖电子
    '688099.SH',  # 晶晨股份
]

print('精选股票池:')
for i, code in enumerate(selected_codes, 1):
    print(f'{i:2}. {code}')

print(f'\n总共: {len(selected_codes)}只股票')
print()

# 保存到文件
output_path = r'D:\mystock\solo\selected_pool.txt'
with open(output_path, 'w', encoding='utf-8') as f:
    for code in selected_codes:
        f.write(code + '\n')

print(f'✅ 已保存到: {output_path}')
