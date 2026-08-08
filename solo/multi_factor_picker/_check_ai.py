# -*- coding: utf-8 -*-
"""临时检查：AI 二次分析段是否生成"""
import sys, os
sys.path.insert(0, r'd:\mystock\solo\multi_factor_picker')
import pandas as pd
import push_washout_recovery as pw

df = pd.read_csv(r'd:\mystock\solo\report_daily\enhanced_timing_bull_all_20260807.csv', encoding='utf-8-sig')
msg = pw.build_wechat_msg(df, '20260807')
print(f'消息总长度: {len(msg)} 字符')
print(f'包含 AI 二次分析段: {"🤖 AI 二次分析" in msg}')
if '🤖 AI 二次分析' in msg:
    i = msg.index('🤖 AI 二次分析')
    print('--- AI 段前 500 字 ---')
    print(msg[i:i+500])
else:
    print('!!! AI 段缺失 !!!')
    print('--- 消息末尾 300 字 ---')
    print(msg[-300:])
