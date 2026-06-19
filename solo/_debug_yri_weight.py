import sys
sys.path.insert(0, '.')
from tushare_quant import calc_yri_history, calc_unified_stock_score, get_hist_data
import pandas as pd

codes = ['002426.SZ', '000988.SZ', '002384.SZ', '603936.SH', '002463.SZ',
         '688809.SH', '300252.SZ', '688536.SH', '603063.SH', '002119.SZ',
         '002409.SZ', '301205.SZ', '300503.SZ', '688733.SH', '688362.SH',
         '300623.SZ', '600703.SH', '002079.SZ', '300814.SZ', '688102.SH']

results = []
for code in codes:
    yri = calc_yri_history(code, debug=False)
    yri_score = yri.get('YRI历史总分', 0) if isinstance(yri, dict) and '错误' not in yri else 0

    df = get_hist_data(code)
    if df is not None and len(df) >= 20:
        try:
            final, rec, details, fp = calc_unified_stock_score(df, ts_code=code)
            results.append({
                '代码': code,
                'YRI-H总分': yri_score,
                'YRI加分_v1': round(yri_score / 100 * 5, 2),
                'YRI加分_v2': round(yri_score / 100 * 10, 2),
                'YRI加分_v3': round(yri_score / 100 * 15, 2),
                '整合评分': final,
                '龙头加分': details.get('龙头加分', 0),
                '二波加分': details.get('二波加分', 0),
                '辨识度加分': details.get('辨识度加分', 0),
                '趋势强度': details.get('趋势强度', 0),
                '资金健康度': details.get('资金健康度', 0),
                '位置安全': details.get('位置安全性', 0),
                '热度持续': details.get('热度持续性', 0),
                '基本面': details.get('基本面', 0),
            })
        except Exception as e:
            print(f'{code} error: {e}')

rdf = pd.DataFrame(results).sort_values('YRI-H总分', ascending=False).reset_index(drop=True)
rdf.index = rdf.index + 1

pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 20)
print(rdf.to_string(index=True))
print()
print(f"YRI-H总分  范围: {rdf['YRI-H总分'].min():.0f} - {rdf['YRI-H总分'].max():.0f}  均值: {rdf['YRI-H总分'].mean():.1f}")
print(f"YRI加分v1  范围: {rdf['YRI加分_v1'].min():.1f} - {rdf['YRI加分_v1'].max():.1f}  (当前 0-5分)")
print(f"YRI加分v2  范围: {rdf['YRI加分_v2'].min():.1f} - {rdf['YRI加分_v2'].max():.1f}  (建议 0-10分)")
print(f"YRI加分v3  范围: {rdf['YRI加分_v3'].min():.1f} - {rdf['YRI加分_v3'].max():.1f}  (建议 0-15分)")
print(f"龙头加分    范围: {rdf['龙头加分'].min():.0f} - {rdf['龙头加分'].max():.0f}")
print(f"二波加分    范围: {rdf['二波加分'].min():.1f} - {rdf['二波加分'].max():.1f}")
print(f"整合评分    范围: {rdf['整合评分'].min():.1f} - {rdf['整合评分'].max():.1f}")

# 统计各项加分占总分比例
print()
avg_final = rdf['整合评分'].mean()
avg_leader = rdf['龙头加分'].mean()
avg_second = rdf['二波加分'].mean()
avg_yri_v1 = rdf['YRI加分_v1'].mean()
avg_yri_v2 = rdf['YRI加分_v2'].mean()
avg_yri_v3 = rdf['YRI加分_v3'].mean()

print(f"平均值对比:")
print(f"  龙头加分: {avg_leader:.1f}  占总分: {avg_leader/avg_final*100:.1f}%")
print(f"  二波加分: {avg_second:.1f}  占总分: {avg_second/avg_final*100:.1f}%")
print(f"  YRI-H v1: {avg_yri_v1:.1f}  占总分: {avg_yri_v1/avg_final*100:.1f}% (当前)")
print(f"  YRI-H v2: {avg_yri_v2:.1f}  占总分: {avg_yri_v2/avg_final*100:.1f}% (建议1)")
print(f"  YRI-H v3: {avg_yri_v3:.1f}  占总分: {avg_yri_v3/avg_final*100:.1f}% (建议2)")
