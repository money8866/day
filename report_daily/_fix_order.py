p = r'D:\mystock\report_daily\_final_pdf_fixed.py'
s = open(p, encoding='utf-8').read()

old = (
    "    data = [['指标','数值','指标','数值'],\n"
    "            [idx,'上证指数',amt,'成交额'],\n"
    "            [ratio,'涨跌比',mkt,'市场状态'],\n"
    "            [earn,'赚钱效应',risk,'风险'],\n"
    "            [rhythm,'节奏',pos,'目标仓位'],\n"
    "            [normal,'正常区间',cap,'确认上限']]"
)

new = (
    "    data = [['指标','数值','指标','数值'],\n"
    "            ['上证指数',idx,'成交额',amt],\n"
    "            ['涨跌比',ratio,'市场状态',mkt],\n"
    "            ['赚钱效应',earn,'风险',risk],\n"
    "            ['节奏',rhythm,'目标仓位',pos],\n"
    "            ['正常区间',normal,'确认上限',cap]]"
)

assert old in s, 'NOT FOUND - try reading file manually'
s = s.replace(old, new)
open(p, 'w', encoding='utf-8').write(s)
print('patched OK')
