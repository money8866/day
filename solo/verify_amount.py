#!/usr/bin/env python
# -*- coding: utf-8 -*-
print("成交额单位转换验证:")
print("=" * 50)
amt = 1365493  # 千元
print(f"amount = {amt} 千元")
print(f"错误: {amt} / 100000000 = {amt / 100000000:.6f} 亿元")
print(f"正确: {amt} / 100000 = {amt / 100000:.2f} 亿元")
print()
print("验证: 1365493千元 = 1365493000元 = 13.65亿元 ✓")