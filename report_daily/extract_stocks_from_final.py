# -*- coding: utf-8 -*-
"""从最新final文件提取股票代码"""
import os
import re
import glob

def get_latest_final_file(directory):
    """获取最新的final开头文件"""
    pattern = os.path.join(directory, 'final*.md')
    files = glob.glob(pattern)
    
    if not files:
        return None, None
    
    # 按修改时间排序
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0], os.path.getmtime(files[0])

def extract_stocks_from_file(filepath):
    """从文件提取股票代码"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 匹配格式：中文名 (代码) 或 代码
    # 例如：柯力传感 (603662.SH) 或 603662.SH
    pattern = r'\((\d{6}\.(?:SH|SZ|BJ))\)|\b(\d{6}\.(?:SH|SZ|BJ))\b'
    matches = re.findall(pattern, content)
    
    # 去重（matches是元组列表）
    stocks = set()
    for match in matches:
        for g in match:
            if g:
                stocks.add(g)
    
    return sorted(list(stocks)), content

def extract_stocks_by_section(filepath):
    """按板块提取股票"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 匹配所有股票代码
    pattern = r'\((\d{6}\.(?:SH|SZ|BJ))\)|\b(\d{6}\.(?:SH|SZ|BJ))\b'
    matches = re.findall(pattern, content)
    
    stocks = []
    for match in matches:
        for g in match:
            if g:
                stocks.append(g)
    
    return list(dict.fromkeys(stocks))  # 保持顺序去重

if __name__ == '__main__':
    directory = r'D:\mystock\report_daily'
    
    latest_file, mtime = get_latest_final_file(directory)
    if not latest_file:
        print('未找到final文件')
    else:
        print('最新文件:', os.path.basename(latest_file))
        print('修改时间:', mtime)
        
        stocks = extract_stocks_by_section(latest_file)
        print()
        print('提取到的股票 (%d只):' % len(stocks))
        for s in stocks[:20]:
            print(' ', s)
        if len(stocks) > 20:
            print('  ... 还有%d只' % (len(stocks) - 20))
        
        # 保存股票列表
        output_file = os.path.join(directory, 'final_stocks.txt')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(latest_file + '\n')
            f.write(str(mtime) + '\n')
            f.write(str(len(stocks)) + '\n')
            for s in stocks:
                f.write(s + '\n')
        
        print()
        print('已保存到:', output_file)
