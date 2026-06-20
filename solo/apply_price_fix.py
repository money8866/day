import re

filepath = r'D:\mystock\solo\tushare_quant.py'
backup = filepath + '.bak'

# 先备份
with open(filepath, 'r', encoding='utf-8') as f:
    original = f.read()
with open(backup, 'w', encoding='utf-8') as f:
    f.write(original)
print('备份已保存到:', backup)

lines = original.split('\n')
output = []
i = 0
skipping_bugfix = False

while i < len(lines):
    line = lines[i]

    # 检测旧 BugFix 代码块开始
    if '# 【BugFix】缓存文件存的是前复权价格' in line:
        skipping_bugfix = True
        i += 1
        # 跳过直到遇到独立的 "except Exception as e:" 行（获取失败那种）
        while i < len(lines):
            if lines[i].strip() == 'except Exception as e:' and i+1 < len(lines) and '获取失败则继续使用原有价格' in lines[i+1]:
                i += 2  # 跳过 except 和 pass 两行
                break
            i += 1
        continue

    # 检测多余的重复 except 块（在过滤条件之前，连续出现两个 except）
    # 简单处理：如果这行是 except 且下一行是 pass，再下一行又是 except，则跳过第一个
    if line.strip() == 'except Exception as e:' and i+2 < len(lines):
        if 'pass' in lines[i+1] and 'except Exception as e:' in lines[i+2]:
            # 跳过第一个 except+pass
            i += 2
            continue

    output.append(line)
    i += 1

result = '\n'.join(output)

# 现在在 "pct_chg = 0.0" 之后插入新代码（从 raw_price_dict 读取未复权价）
anchor = '                    pct_chg = 0.0\n                \n                # 过滤条件'
new_code = '''                    pct_chg = 0.0
                \n                # 【BugFix】用批量获取的未复权实际价格覆盖前复权价格\n                if ts_code in raw_price_dict:\n                    raw = raw_price_dict[ts_code]\n                    latest_close = raw["close"]\n                    pct_chg = raw["pct_chg"]\n                else:\n                    # 批量未获取到，单独补一次\n                    try:\n                        if pro is not None:\n                            df_raw = pro.daily(ts_code=ts_code, start_date="20250101", end_date=TRADE_DATE)\n                            if df_raw is not None and not df_raw.empty:\n                                df_raw = df_raw.sort_values("trade_date")\n                                latest_close = float(df_raw.iloc[-1]["close"])\n                                pct_chg = float(df_raw.iloc[-1]["pct_chg"])\n                    except Exception as e:\n                        pass\n                \n                # 过滤条件'''

# 注意：上面用 \n 代替换行，实际需要插入多行
# 更可靠的方式：找到锚点行号，然后插入
result_lines = result.split('\n')
final_output = []
j = 0
while j < len(result_lines):
    rl = result_lines[j]
    final_output.append(rl)
    # 在 "pct_chg = 0.0" 行之后插入新代码
    if 'pct_chg = 0.0' in rl and j+1 < len(result_lines) and '过滤条件' not in result_lines[j+1]:
        final_output.append('                ')
        final_output.append('                # 【BugFix】用批量获取的未复权实际价格覆盖前复权价格')
        final_output.append('                if ts_code in raw_price_dict:')
        final_output.append('                    raw = raw_price_dict[ts_code]')
        final_output.append('                    latest_close = raw["close"]')
        final_output.append('                    pct_chg = raw["pct_chg"]')
        final_output.append('                else:')
        final_output.append('                    # 批量未获取到，单独补一次')
        final_output.append('                    try:')
        final_output.append('                        if pro is not None:')
        final_output.append('                            df_raw = pro.daily(ts_code=ts_code, start_date="20250101", end_date=TRADE_DATE)')
        final_output.append('                            if df_raw is not None and not df_raw.empty:')
        final_output.append('                                df_raw = df_raw.sort_values("trade_date")')
        final_output.append('                                latest_close = float(df_raw.iloc[-1]["close"])')
        final_output.append('                                pct_chg = float(df_raw.iloc[-1]["pct_chg"])')
        final_output.append('                    except Exception as e:')
        final_output.append('                        pass')
        final_output.append('                ')
    j += 1

final = '\n'.join(final_output)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(final)

print('修复完成！')
print(f'原始行数: {len(lines)}')
print(f'中间行数: {len(output)}')
print(f'最终行数: {len(final_output)}')
