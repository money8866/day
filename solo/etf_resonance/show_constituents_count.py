"""显示每个ETF的成份股数量"""
import json

ETF_THEME_MAP = {
    '512480.SH': '半导体', '159995.SZ': '芯片', '159516.SZ': '半导体设备',
    '159819.SZ': '人工智能', '515230.SH': '软件', '515880.SH': '通信',
    '159732.SZ': '消费电子', '159851.SZ': '金融科技', '159869.SZ': '游戏',
    '516160.SH': '新能源', '515790.SH': '光伏', '159566.SZ': '储能',
    '159755.SZ': '电池', '515030.SH': '新能源车', '159992.SZ': '创新药',
    '159883.SZ': '医疗器械', '512010.SH': '医药', '512660.SH': '军工',
    '159227.SZ': '航空航天', '562500.SH': '机器人', '516650.SH': '有色金属',
    '159870.SZ': '化工', '515220.SH': '煤炭', '515210.SH': '钢铁',
    '159611.SZ': '电力', '561380.SH': '电网设备', '159928.SZ': '消费',
    '159736.SZ': '食品饮料', '512690.SH': '酒', '159996.SZ': '家电',
    '512880.SH': '证券', '512800.SH': '银行', '515180.SH': '红利',
    '518880.SH': '黄金', '159667.SZ': '工业母机',
}

json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"{'ETF代码':<14}{'名称':<12}{'成份股数量':<10}{'取前50':<8}")
print("-" * 44)
total = 0
total_50 = 0
for code, name in ETF_THEME_MAP.items():
    count = len(data.get(code, []))
    count_50 = min(count, 50)
    total += count
    total_50 += count_50
    print(f"{code:<14}{name:<12}{count:<10}{count_50:<8}")

print("-" * 44)
print(f"{'合计':<14}{'':<12}{total:<10}{total_50:<8}")
