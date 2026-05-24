import tushare as ts
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

# 候选列表 - 覆盖主要行业板块
candidates = [
    # 科技
    ('512480', 'SH', '半导体'),
    ('159995', 'SZ', '芯片'),
    ('159516', 'SZ', '半导体设备'),
    ('159819', 'SZ', '人工智能'),
    ('515230', 'SH', '软件'),
    ('515880', 'SH', '通信'),
    ('159732', 'SZ', '消费电子'),
    ('159851', 'SZ', '金融科技'),
    ('159869', 'SZ', '游戏'),
    # 新能源
    ('516160', 'SH', '新能源'),
    ('515790', 'SH', '光伏'),
    ('159566', 'SZ', '储能'),
    ('159755', 'SZ', '电池'),
    ('515030', 'SH', '新能源车'),
    # 医药
    ('159992', 'SZ', '创新药'),
    ('159883', 'SZ', '医疗器械'),
    ('512010', 'SH', '医药'),
    # 制造/周期
    ('512660', 'SH', '军工'),
    ('159227', 'SZ', '航空航天'),
    ('562500', 'SH', '机器人'),
    ('516650', 'SH', '有色金属'),
    ('159870', 'SZ', '化工'),
    ('515220', 'SH', '煤炭'),
    ('515210', 'SH', '钢铁'),
    ('159611', 'SZ', '电力'),
    ('561380', 'SH', '电网设备'),
    # 消费
    ('159928', 'SZ', '消费'),
    ('159736', 'SZ', '食品饮料'),
    ('512690', 'SH', '酒'),
    ('159996', 'SZ', '家电'),
    # 金融/其他
    ('512880', 'SH', '证券'),
    ('512800', 'SH', '银行'),
    ('515180', 'SH', '红利'),
    ('518880', 'SH', '黄金'),
    # 宽基
    ('510300', 'SH', '沪深300'),
    ('159915', 'SZ', '创业板'),
    ('512560', 'SH', '中证1000'),
    ('510050', 'SH', '上证50'),
]

import os
tdx_path = r"C:\new_tdx\vipdoc"

confirmed = []
for code, market, expected_name in candidates:
    ts_code = code + '.' + market
    df = pro.fund_basic(ts_code=ts_code, market='E')
    if len(df) == 0:
        print(f"  [FAIL] {ts_code} - fund_basic not found")
        continue
    real_name = df.iloc[0]['name']
    
    # check tdx data
    tdx_file = os.path.join(tdx_path, market.lower(), 'lday', f"{market.lower()}{code}.day")
    has_data = os.path.exists(tdx_file)
    
    status = "OK" if has_data else "NO_DATA"
    print(f"  [{status}] {ts_code:>12s}  {real_name:<16s} (expected: {expected_name})  TDX={'Y' if has_data else 'N'}")
    if has_data:
        confirmed.append((code, market.lower(), real_name))

print(f"\n  confirmed: {len(confirmed)} / {len(candidates)}")

# print python dict
print("\n  # Python ETF_POOL:")
print("  ETF_POOL = {")
for code, market, name in confirmed:
    print(f"    '{name}': ('{code}', '{market}'),")
print("  }")
