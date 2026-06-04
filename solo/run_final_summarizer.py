#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
运行完整的 daily_analysis_summarizer
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 先导入 daily_analysis_summarizer 模块
import daily_analysis_summarizer

# 临时修改 send_to_wechat 函数，让它不实际发送
original_send = daily_analysis_summarizer.send_to_wechat

def mock_send_to_wechat(text):
    print("🧪 模拟微信推送（不实际发送）")
    print("📄 推送内容（前500字符）：")
    print("=" * 70)
    print(text[:500])
    print("=" * 70)
    return True

daily_analysis_summarizer.send_to_wechat = mock_send_to_wechat

print("=" * 70)
print("运行完整的 daily_analysis_summarizer")
print("=" * 70)

# 运行 main 函数
daily_analysis_summarizer.main()
