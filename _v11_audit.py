# -*- coding: utf-8 -*-
"""V11 评分模型数值审计（只读分析，不修改业务逻辑）"""
import math

W = {'突破质量': .25, '资金行为': .24, '位置安全': .18, '基本面': .15, '动量爆发': .10, '热度持续': .08}

print("=" * 64)
print("A. 加权平均的『分散化收缩』效应")
print("=" * 64)
l2 = math.sqrt(sum(v * v for v in W.values()))
print(f"  权重平方和开根 sqrt(sum wi^2) = {l2:.4f}")
print(f"  → 若各维独立且波动相同, base_score 标准差仅为单维的 {l2*100:.1f}%")
print(f"  → 维度越相关收缩越少, 但『相关』本身即说明信息冗余")

print()
print("=" * 64)
print("B. 各维度有效话语权（假设实际波动区间 35~75，跨度 40 分）")
print("=" * 64)
span_real = 40.0
total_span = 60.0
print(f"  {'维度':<10}{'权重':>7}{'满量程贡献':>12}{'实际贡献':>11}{'占总跨度':>10}")
for k, v in W.items():
    print(f"  {k:<10}{v*100:>6.0f}%{100*v:>11.1f}分{span_real*v:>10.1f}分{span_real*v/total_span*100:>9.1f}%")
print(f"\n  → 热度维度实际仅影响 {span_real*0.08/total_span*100:.1f}% 的排序，已是噪声量级")

print()
print("=" * 64)
print("C. 软天花板函数连续性检验（tier_cap=97）")
print("=" * 64)


def soft(x, cap=97.0):
    if x > 70:
        return cap - cap * 0.31 / (1.0 + math.exp(0.14 * (x - 80.0)))
    return x


print(f"  {'输入x':>8}{'输出':>10}{'局部斜率':>10}")
for x in [50, 60, 68, 69, 70, 70.01, 71, 75, 80, 85, 90, 95, 100, 110, 120]:
    slope = (soft(x + 0.5) - soft(x - 0.5)) / 1.0
    print(f"  {x:>8.2f}{soft(x):>10.2f}{slope:>10.3f}")

print()
print(f"  ★ x=70 左极限 70.00 → 右极限 {soft(70.0001):.2f}，起点瞬间跳升 {soft(70.0001)-70:.2f} 分（不连续）")
print(f"  ★ x=100→{soft(100):.1f}，x=120→{soft(120):.1f}：高分区 20 分输入差仅拉开 {soft(120)-soft(100):.1f} 分")
print(f"  ★ 90 分以上区间总宽度 = {soft(120)-soft(90):.1f} 分 ← 高分扎堆未被真正解决")
print(f"  ★ 70 分以下完全线性（斜率 1.0），压缩全部集中在 70~95，但那里斜率仍达 {0.5*(soft(80.5)-soft(79.5))+0.5:.2f} 左右")

print()
print("=" * 64)
print("D. 三重计价检查：ret20 / 追高 同一风险被扣几次")
print("=" * 64)
print("  1) breakout_quality: ret20>35 → -15      (权重25% → 总分 -3.75)")
print("  2) penalty         : ret_10>35 → -10     (直接 -10)")
print("  3) failure_prob    : ret20>35 → 风险+8   (再经 risk_penalty 放大)")
print("  4) risk_penalty    : 失败概率>35 部分 ×0.55，>50 部分再 ×0.45")
print("  → 同一『涨幅透支』风险被计价 3~4 次，且各自系数都是独立拍定的")

print()
print("=" * 64)
print("E. 建议的替代映射（严格单调、连续、跨日可比）")
print("=" * 64)
print("  方案1 池内分位:  score = (rank - 0.5) / N * 100")
print("  方案2 连续压缩:  score = cap * (1 - exp(-x / s))，s 由池内均值标定")
for x in [40, 55, 70, 85, 100, 115]:
    s = 55.0
    print(f"     cap*(1-exp(-x/55)) : x={x:>5.0f} → {97*(1-math.exp(-x/s)):>6.2f}")
