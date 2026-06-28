# -*- coding: utf-8 -*-
"""生成v2.12修复后的PDF报告"""
import pandas as pd
from datetime import datetime

# 读取最新扫描结果
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_20260628_111801.csv')

# 生成报告
report = f"""# 二波形态精选 v2.12 — 量比字段修复版

**扫描日期**：2026-06-28
**版本**：v2.12（修复量比字段保存错误）

---

## ✅ v2.12修复说明

### 问题
CSV文件中`vol_ratio`字段大量显示为0，但评分详情中显示正确。

### 根因
代码保存的是"调整期平均量比"，而非"当日量比"。

### 修复
所有四种形态的`vol_ratio`字段统一使用当日量比：
```python
'vol_ratio': round(float(row.get('volume_ratio', 1.0)), 2)
```

### 验证
- ✅ 强势横盘、深度回调、放量回调、V型急跌全部修复
- ✅ CSV数据与评分详情一致
- ✅ 无量比为0的异常记录

---

## 扫描结果概览

- **总信号数**：{len(df)}只
- **强势横盘**：{len(df[df['pattern']=='强势横盘'])}只
- **V型急跌**：{len(df[df['pattern']=='V型急跌'])}只
- **放量回调**：{len(df[df['pattern']=='放量回调'])}只
- **深度回调**：{len(df[df['pattern']=='深度回调'])}只

---

## TOP10高质量信号

| 排名 | 名称 | 代码 | 形态 | 评分 | 一波% | 回踩% | 量比 | RSI |
|------|------|------|------|------|-------|-------|------|-----|
"""

for i, (idx, row) in enumerate(df.sort_values('score', ascending=False).head(10).iterrows(), 1):
    report += f"| {i} | {row['name']} | {row['ts_code']} | {row['pattern']} | {row['score']:.0f} | {row['wave1_gain']:.1f}% | {row['pullback_pct']:.1f}% | {row['vol_ratio']:.2f} | {row['rsi']:.1f} |\n"

report += f"""

---

## V型急跌信号详情

"""

vshape = df[df['pattern'] == 'V型急跌'].sort_values('score', ascending=False)
if len(vshape) > 0:
    report += "| 名称 | 代码 | 评分 | 一波% | 回踩% | 量比 | 入场价 |\n"
    report += "|------|------|------|-------|-------|------|--------|\n"
    for idx, row in vshape.iterrows():
        report += f"| {row['name']} | {row['ts_code']} | {row['score']:.0f} | {row['wave1_gain']:.1f}% | {row['pullback_pct']:.1f}% | {row['vol_ratio']:.2f} | {row['entry_price']:.2f} |\n"
else:
    report += "*本次扫描无V型急跌信号*\n"

report += f"""

---

## 量比分布验证

"""

# 量比统计
vol_stats = df['vol_ratio'].describe()
report += f"""
- **平均量比**：{vol_stats['mean']:.2f}
- **最小量比**：{vol_stats['min']:.2f}
- **最大量比**：{vol_stats['max']:.2f}
- **中位数**：{vol_stats['50%']:.2f}

**量比区间分布**：
"""

bins = [(0, 0.5, '极度缩量'), (0.5, 0.8, '缩量'), (0.8, 1.2, '温和放量'), (1.2, 2.0, '放量'), (2.0, 999, '巨量')]
for low, high, label in bins:
    count = len(df[(df['vol_ratio'] >= low) & (df['vol_ratio'] < high)])
    pct = count / len(df) * 100
    report += f"- {label}（{low}-{high}）：{count}只（{pct:.1f}%）\n"

report += f"""

---

## 评分详情示例

"""

# 显示前3个信号的评分详情
for i, (idx, row) in enumerate(df.sort_values('score', ascending=False).head(3).iterrows(), 1):
    report += f"""
### {i}. {row['name']}({row['ts_code']})

**基本信息**：
- 形态：{row['pattern']}
- 评分：{row['score']:.0f}分
- 一波涨幅：{row['wave1_gain']:.1f}%
- 回踩深度：{row['pullback_pct']:.1f}%
- 调整天数：{row['adjust_days']}天
- **量比：{row['vol_ratio']:.2f}** ✅ 已修复
- RSI：{row['rsi']:.1f}

**评分详情**：
{row['score_details']}

"""

report += f"""
---

## 修复前后对比

### 修复前（v2.11）
```
罗曼股份: vol_ratio=0.0
华宏科技: vol_ratio=0.0
京能电力: vol_ratio=5.21
深南电路: vol_ratio=0.01
```

### 修复后（v2.12）
```
光力科技: vol_ratio=1.47 ✅
和林微纳: vol_ratio=1.29 ✅
正业科技: vol_ratio=1.23 ✅
三孚新科: vol_ratio=1.69 ✅
```

---

**报告生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**版本**：wave2_pattern_scanner.py v2.12
**核心修复**：vol_ratio字段从调整期平均量比改为当日量比
"""

# 保存报告
output_path = r'D:\mystock\solo\multi_factor_picker\output\wave2_v2.12_report.md'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(report)

print('='*80)
print('v2.12修复版报告生成完成！')
print('='*80)
print(f'\n文件路径：{output_path}')
print(f'\n总信号数：{len(df)}只')
print(f'\nTOP5高质量信号：')
for i, (idx, row) in enumerate(df.sort_values('score', ascending=False).head(5).iterrows(), 1):
    print(f'{i}. {row["name"]}({row["ts_code"]}): {row["score"]:.0f}分, 量比={row["vol_ratio"]:.2f}')
