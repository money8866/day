"""调试多个关键主题的匹配结果"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import json
import pandas as pd

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

# 测试多个关键主题
test_themes = ['券商', '电力设备', '银行', '军工', 'PCB产业链', 'AI芯片', '半导体制造',
               '医药产业链', '创新药', '医药产业链', '消费白马', '红利公用事业',
               '玻璃建材', '化工链', '工业金属', '钢铁', '煤炭链', '石油石化',
               '汽车零部件', '新能源汽车链', '工程机械与重型装备', '交通运输物流',
               '基建地产链', '家电家居链', '商超零售链', '餐饮食品链', 'AI文娱内容',
               '光学光电子', '被动元件', '半导体设备', '半导体材料', '功率半导体',
               '先进封装', '保险', '多元金融', '电信运营商', '医疗服务', '数据要素',
               '华为产业链', '金融科技', '化工新材料', '必选消费红利链', '能源金属',
               '贵金属', '小金属', '氟化工制冷剂', '人形机器人', '智能驾驶']

for tname in test_themes:
    if tname not in themes:
        print(f'{tname}: 主题不存在')
        continue
    test = {tname: themes[tname]}
    result = theme_ts.match_theme_stocks(test, dc_df, stock_basic)
    matched_dict = result[0] if isinstance(result, tuple) else result
    stocks = matched_dict.get(tname, {})
    print(f'{tname}: {len(stocks)}只')
