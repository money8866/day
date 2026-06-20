"""
精确按行号修复 tushare_quant.py 的价格bug
"""
filepath = r'D:\mystock\solo\tushare_quant.py'
backup = filepath + '.bak2'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 备份
with open(backup, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print(f'备份已保存到: {backup}')
print(f'原始行数: {len(lines)}')

# 行号是从1开始的，Python列表是从0开始的，所以需要减1
# 需要删除的行：4981-4999 (共19行) → 索引 4980:4999
# 需要删除的行：5000-5001 (共2行) → 索引 4999:5001
# 注意：删除行之后，行号会变化，所以从后往前删

# 先删除 5000-5001 行（索引 4999:5001）
# 这两行是：
#   except Exception as e:
#       pass  # 获取失败则继续使用原有价格
# 注意：可能不止两行，需要精确判断
# 从索引 4999 开始，删除直到遇到非空行或者缩进减少的行

# 更简单的方案：直接重建文件
# 步骤：
# 1. 复制 1-4980 行（索引 0:4980）
# 2. 插入新代码（从 raw_price_dict 读取未复权价）
# 3. 复制 5002-文件末尾（索引 5001:），跳过 4981-5001 行

new_lines = []

# 1. 复制 1-4980 行（索引 0:4980）
new_lines.extend(lines[0:4980])

# 2. 插入新代码（在 pct_chg = 0.0 之后）
# 注意：第 4980 行（索引 4979）是 "                    pct_chg = 0.0"
# 所以我们在 new_lines 后面追加新代码
indent = '                '  # 16个空格
new_lines.append('\n')
new_lines.append(indent + '# 【BugFix】用批量获取的未复权实际价格覆盖前复权价格\n')
new_lines.append(indent + 'if ts_code in raw_price_dict:\n')
new_lines.append(indent + '    raw = raw_price_dict[ts_code]\n')
new_lines.append(indent + '    latest_close = raw["close"]\n')
new_lines.append(indent + '    pct_chg = raw["pct_chg"]\n')
new_lines.append(indent + 'else:\n')
new_lines.append(indent + '    # 批量未获取到，单独补一次\n')
new_lines.append(indent + '    try:\n')
new_lines.append(indent + '        if pro is not None:\n')
new_lines.append(indent + '            df_raw = pro.daily(ts_code=ts_code, start_date="20250101", end_date=TRADE_DATE)\n')
new_lines.append(indent + '            if df_raw is not None and not df_raw.empty:\n')
new_lines.append(indent + '                df_raw = df_raw.sort_values("trade_date")\n')
new_lines.append(indent + '                latest_close = float(df_raw.iloc[-1]["close"])\n')
new_lines.append(indent + '                pct_chg = float(df_raw.iloc[-1]["pct_chg"])\n')
new_lines.append(indent + '    except Exception as e:\n')
new_lines.append(indent + '        pass\n')
new_lines.append('\n')

# 3. 复制 5002-文件末尾（索引 5001:），跳过 4981-5001 行
# 注意：5002 行（索引 5001）是 "# 过滤条件：只保留今天下跌的个股（洗盘形态）"
new_lines.extend(lines[5001:])

# 写回文件
with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f'修复完成！新行数: {len(new_lines)}')

# 验证：检查关键行
print('\n验证：')
with open(filepath, 'r', encoding='utf-8') as f:
    all_lines = f.readlines()
# 检查 raw_price_dict 相关代码
for i, l in enumerate(all_lines):
    if 'raw_price_dict' in l or 'BugFix' in l or '复权价' in l:
        print(f'  行 {i+1}: {l.rstrip()}')