import sys
sys.path.insert(0, '.')
import tushare_quant as tq
import json
import os

# 获取电连技术历史数据
ts_code = '300679.SZ'
df = tq.get_hist_data(ts_code)

if df is not None and not df.empty:
    print(f'获取到 {ts_code} 数据: {len(df)} 条')
    
    # 构建stock_info
    stock_info = {
        "name": "电连技术",
        "industries": ["电子元件", "消费电子"],
        "concepts": ["人形机器人", "小米概念", "华为概念", "5G概念"],
        "business_text": "公司专业从事微型电连接器及互连系统相关产品的研制销售，产品广泛应用于智能手机等消费电子领域，在机器人领域布局高端产品应用。"
    }
    
    # 不指定theme，让系统自动选择
    result = tq.calc_dual_layer_score_v7(df, ts_code=ts_code, stock_info=stock_info)
    
    print('\n========== 电连技术(300679.SZ) V7评分详情 ==========')
    print(f'V7总评分: {result["V7总评分"]}')
    print(f'所属主题: {result["所属主题"]}')
    print()
    print('--- 技术指标 (来自V6) ---')
    print(f'  趋势概率: {result["趋势概率"]}')
    print(f'  失败概率: {result["失败概率"]}')
    print(f'  洗盘概率: {result["洗盘概率"]}')
    print(f'  交易优势: {result["交易优势"]}')
    print(f'  趋势强度: {result["趋势强度"]}')
    print(f'  趋势稳定: {result["趋势稳定"]}')
    print(f'  资金动量: {result["资金动量"]}')
    print(f'  突破强度: {result["突破强度"]}')
    print(f'  压缩度: {result["压缩度"]}')
    print(f'  量能爆发: {result["量能爆发"]}')
    print(f'  风险等级: {result["风险等级"]}')
    print()
    print('--- V7新增维度 ---')
    print(f'  主题纯度: {result["主题纯度"]}')
    print(f'  主题排名加成: {result["主题排名加成"]}')
    
    # 显示各主题纯度
    print()
    print('--- 各主题纯度 ---')
    cfg_path = os.path.join(tq.BASE_DIR, 'theme.json')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        all_themes = json.load(f).get('HOT_THEMES', {})
    
    theme_scores = []
    for t_name in all_themes.keys():
        conf = tq.calc_theme_confidence(stock_info, t_name)
        theme_scores.append((t_name, conf))
    
    # 按纯度排序
    theme_scores.sort(key=lambda x: x[1], reverse=True)
    for t_name, conf in theme_scores:
        marker = " <-- 最高" if t_name == result["所属主题"] else ""
        print(f'  {t_name}: {conf}{marker}')
    
else:
    print('获取数据失败')
