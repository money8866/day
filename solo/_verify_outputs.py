import json, os, pandas as pd

BASE = r'd:\mystock\solo\theme_alpha_v6\cache'
TRADE_DATE = "20260721"

# 1. 融合排名
fusion_path = os.path.join(BASE, f'theme_fusion_rank_{TRADE_DATE}.json')
if os.path.exists(fusion_path):
    with open(fusion_path, encoding='utf-8') as f:
        payload = json.load(f)
    meta = payload.get("meta", {})
    data = payload.get("data", payload)
    print(f"[融合排名] {len(data)}个主题 | 大盘={meta.get('大盘状态','?')} | 模式={meta.get('模式','?')}")
    # 检查字段完整性
    fields = ['排名','主题','融合分','V6分','V8分','奖惩','信号','操作建议']
    for r in data[:3]:
        print(f"  #{r['排名']} {r['主题']:16s} 融合={r['融合分']:.1f} V6={r['V6分']:.1f} V8={r['V8分']:.1f} 操作={r['操作建议']}")
    missing = [f for f in fields if f not in data[0]]
    print(f"  字段缺失: {missing if missing else '无'}")
else:
    print(f"[融合排名] 文件不存在: {fusion_path}")

# 2. V8 中军
v8_center_path = os.path.join(BASE, f'theme_alpha_v6_result_v8_center_{TRADE_DATE}.csv')
v8_json_path = os.path.join(BASE, f'theme_alpha_v6_result_v8_{TRADE_DATE}.json')
if os.path.exists(v8_center_path):
    v8c = pd.read_csv(v8_center_path)
    print(f"\n[V8中军] {len(v8c)}只标的 | 列: {list(v8c.columns)}")
    if len(v8c) > 0:
        print(f"  TOP3:")
        for _, r in v8c.head(3).iterrows():
            print(f"  {r.get('ts_code','')} {r.get('主题','')} 确定性={r.get('确定性得分','N/A')}")
else:
    print(f"\n[V8中军] 文件不存在: {v8_center_path}")

# 3. V8 JSON
if os.path.exists(v8_json_path):
    with open(v8_json_path, encoding='utf-8') as f:
        v8 = json.load(f)
    print(f"\n[V8 JSON] {len(v8)}个主题")
    zeros = sum(1 for t in v8 if t.get('V7综合得分', 0) == 0)
    print(f"  V7综合得分为0: {zeros}/{len(v8)}")
    # 检查中军字段是否完整
    center_fields = ['梯队_中军破位比例'] if len(v8) > 0 else []
    has_center_field = center_fields[0] in v8[0] if center_fields else False
    print(f"  含中军破位字段: {has_center_field}")

# 4. 验证 tushare_quant.py 是否读取正确
print(f"\n--- 测试 tushare_quant 读取 ---")
import sys
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import _load_fusion_result, _load_v6_result

fusion_loaded = _load_fusion_result(TRADE_DATE)
if fusion_loaded and '融合分' in (fusion_loaded[0] if fusion_loaded else {}):
    print(f"[OK] tushare_quant._load_fusion_result 返回融合排名: {len(fusion_loaded)}个")
    print(f"  TOP1: {fusion_loaded[0]['主题']} 融合分={fusion_loaded[0]['融合分']}")
else:
    print(f"[!] 回退到V8/V6")
    v6_loaded = _load_v6_result(TRADE_DATE)
    if v6_loaded:
        print(f"  回退读取: {len(v6_loaded)}个主题")
