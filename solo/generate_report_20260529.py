import pandas as pd

# 读取主题排名
df = pd.read_csv('cache_backbone_tushare/theme_ranking_final_20260529.csv')

# 读取退潮风险报告
with open('cache_backbone_tushare/recession_risk_report_20260529.txt', 'r', encoding='utf-8') as f:
    risk_content = f.read()

# 生成简单报告
report = '''====================================================================================================
每日盘后复盘报告
日期: 20260529
====================================================================================================

【今日主题评分与轮动分析】
====================================================================================================

'''

# 添加主题排名
for idx, row in df.iterrows():
    report += f"第{int(row['排名'])}名: 【{row['主题']}】\n"
    report += f"  今日评分: {row['今日评分']:.1f} | 近10日平均分: {row['近10日平均分']:.1f}\n"
    report += f"  近10日平均排名: {row['近10日平均排名']:.1f} | 趋势: {row['趋势']} | 排名变化: {int(row['排名变化']):+d}\n\n"

report += '\n\n' + risk_content

# 保存报告
with open('cache_backbone_tushare/daily_review_20260529.txt', 'w', encoding='utf-8') as f:
    f.write(report)

print('报告文件已生成: daily_review_20260529.txt')
