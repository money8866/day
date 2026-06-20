"""
修复 tushare_quant.py 中 get_tracking_stocks() 的价格bug：
1. 删除循环内旧的单个 API 调用代码（BugFix注释块）
2. 删除多余的重复 except 块
3. 插入从 raw_price_dict 读取未复权实际价格的代码
"""
import re

filepath = r'D:\mystock\solo\tushare_quant.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# ---- 步骤1：删除旧的单个API调用代码块 ----
# 匹配从 "# 【BugFix】缓存文件存的是前复权价格..." 到下一个 "except Exception as e:" 之前的代码
old_block_pattern = r'\s*# 【BugFix】缓存文件存的是前复权价格，需用未复权实际价格覆盖\s*\n\s*# 判断依据：若 last_close 与数据库记录的 close 差异巨大（>10%），说明是复权价\s*\n\s*try:\s*\n\s*db_close = float\(row\[\'close\'\]\) if str\(row\[\'close\'\]\)\.strip\(\) not in \[\'\', \'None\'\] else 0\.0\s*\n\s*if db_close > 0 and abs\(latest_close - db_close\) / db_close > 0\.1:\s*\n\s*# 用 Tushare 获取最新未复权实际价格\s*\n\s*if pro is not None:\s*\n\s*df_raw = pro\.daily\(ts_code=ts_code, start_date=\'20250101\', end_date=TRADE_DATE\)\s*\n\s*if df_raw is not None and not df_raw\.empty:\s*\n\s*df_raw = df_raw\.sort_values\(\'trade_date\'\)\s*\n\s*raw_close = float\(df_raw\.iloc\[-1\]\[\'close\'\]\)\s*\n\s*raw_pct = float\(df_raw\.iloc\[-1\]\[\'pct_chg\'\]\)\s*\n\s*print\(f\'\[价格修正\] \{ts_code\} 复权价\{latest_close:\.2f\} → 实际价\{raw_close:\.2f\}\'\)\s*\n\s*latest_close = raw_close\s*\n\s*pct_chg = raw_pct\s*\n\s*except Exception as e:\s*\n\s*pass  # 获取失败则继续使用原有价格'

# 先尝试用更宽松的方式查找并删除
# 实际代码中可能有换行符差异，改用逐行扫描方式
lines = content.split('\n')
new_lines = []
i = 0
skipping = False
skip_until_except = False

while i < len(lines):
    line = lines[i]
    
    # 检测旧 BugFix 代码块的开始
    if '# 【BugFix】缓存文件存的是前复权价格' in line:
        skipping = True
        i += 1
        continue
    
    # 如果正在跳过旧代码块，继续跳过直到遇到 'except Exception as e:'
    if skipping:
        if 'except Exception as e:' in line and '计算失败' not in line and '获取失败' in line:
            # 找到旧的 except 块，停止跳过
            skipping = False
            i += 1
            continue
        else:
            i += 1
            continue
    
    # 检测多余的重复 except 块（在过滤条件之前）
    # 这种情况：连续两个 "except Exception as e: / pass" 块
    new_lines.append(line)
    i += 1

# 重新拼接
content = '\n'.join(new_lines)

# 更可靠的方式：直接用字符串替换删除已知的问题代码块
# 读取原始文件，用精确的行扫描来修复
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # 检测旧 BugFix 代码块开始
    if '# 【BugFix】缓存文件存的是前复权价格' in line:
        # 跳过这个块，直到遇到只包含 '                except Exception as e:' 的行
        i += 1
        while i < len(lines):
            if "except Exception as e:" in lines[i] and "获取失败则继续使用原有价格" in lines[i+1] if i+1 < len(lines) else False:
                # 跳过 except 和 pass 两行
                i += 2
                break
            i += 1
        continue
    
    # 检测多余的重复 except 块（两个连续的 except pass）
    # 检查当前行和后面几行
    if 'except Exception as e:' in line and i+1 < len(lines) and 'pass' in lines[i+1]:
        # 看看前面几行是否已经有 except 了
        # 简单策略：如果这一行前面有空白，且前一行也是 except，则跳过
        # 实际上，我们直接跳过这个重复的块
        # 检查下一行是否也是 except
        if i+2 < len(lines) and 'except Exception as e:' in lines[i+2]:
            # 跳过这两个重复的 except 块
            i += 4  # 跳过两个 except+pass
            continue
    
    new_lines.append(line)
    i += 1

# 写回文件（先不写，先打印看看效果）
print(f'原始行数: {len(lines)}')
print(f'新行数: {len(new_lines)}')

# 现在用正确的方式：重新读取，用状态机精确修复
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 方案：找到 "优先从缓存文件获取今日价格" 块，在其后插入新代码，并删除旧代码
output = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # 检测 "优先从缓存文件获取今日价格和涨跌幅" 块
    if '优先从缓存文件获取今日价格和涨跌幅' in line:
        # 保留这个 if-else 块
        output.append(line)  # if cache_close > 0:
        i += 1
        # 复制整个 if-else 块
        indent = '                '
        while i < len(lines) and ('if cache_close > 0:' in lines[i] or 'latest_close = cache_close' in lines[i] or 'else:' in lines[i] or 'latest_close = float(row' in lines[i] or 'pct_chg = 0.0' in lines[i] or lines[i].strip() == ''):
            output.append(lines[i])
            i += 1
            if 'pct_chg = 0.0' in lines[i-1]:
                break
        # 现在 i 指向 if-else 块之后的行
        # 插入新代码：从 raw_price_dict 读取未复权实际价格
        output.append('\n')
        output.append('                # 【BugFix】用批量获取的未复权实际价格覆盖前复权价格\n')
        output.append('                if ts_code in raw_price_dict:\n')
        output.append('                    raw = raw_price_dict[ts_code]\n')
        output.append('                    latest_close = raw["close"]\n')
        output.append('                    pct_chg = raw["pct_chg"]\n')
        output.append('                else:\n')
        output.append('                    # 批量未获取到，单独补一次\n')
        output.append('                    try:\n')
        output.append('                        if pro is not None:\n')
        output.append('                            df_raw = pro.daily(ts_code=ts_code, start_date="20250101", end_date=TRADE_DATE)\n')
        output.append('                            if df_raw is not None and not df_raw.empty:\n')
        output.append('                                df_raw = df_raw.sort_values("trade_date")\n')
        output.append('                                latest_close = float(df_raw.iloc[-1]["close"])\n')
        output.append('                                pct_chg = float(df_raw.iloc[-1]["pct_chg"])\n')
        output.append('                    except Exception as e:\n')
        output.append('                        pass\n')
        output.append('\n')
        continue
    
    output.append(line)
    i += 1

# 写回文件
with open(filepath + '.fixed', 'w', encoding='utf-8') as f:
    f.writelines(output)

print('修复完成，新文件已保存为: ' + filepath + '.fixed')
print(f'新文件行数: {len(output)}')
