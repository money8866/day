"""
批量扫描 bull_stocks_qualified.csv 的筹码Alpha
输出"可逢低介入"和"积极参与"的股票
"""
import sys
import os
import pandas as pd
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载 .env 中的 TUSHARE_TOKEN
from dotenv import load_dotenv
load_dotenv("d:/mystock/config/.env")

from tushare_quant import batch_chip_alpha, extract_chip_alpha_factors, get_chip_alpha_suggestion

CSV_PATH = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'

def code_to_ts_code(code):
    """数字code -> ts_code"""
    sc = str(code).zfill(6)
    if sc.startswith('6') or sc.startswith('8') or sc.startswith('9'):
        return sc + '.SH'
    return sc + '.SZ'

def main():
    # 加载合格股池
    df = pd.read_csv(CSV_PATH)
    print(f"加载合格股池: {len(df)} 只")
    print(f"列名: {list(df.columns)[:10]}...")

    # 构建股票列表
    stocks = []
    for _, row in df.iterrows():
        ts_code = code_to_ts_code(row['code'])
        stocks.append({
            '代码': ts_code,
            '名称': row.get('name', ''),
            '所属主题': row.get('theme', ''),
        })

    # 批量计算筹码Alpha
    print(f"\n开始批量计算 {len(stocks)} 只股票的筹码Alpha...")
    t0 = time.time()
    chip_results = batch_chip_alpha(stocks, lookback_days=20)
    elapsed = time.time() - t0
    print(f"计算完成，耗时 {elapsed:.1f} 秒，成功 {len(chip_results)} 只\n")

    # 提取因子 + 建议
    all_results = []
    for s in stocks:
        ts_code = s['代码']
        chip_r = chip_results.get(ts_code)
        if not chip_r:
            continue
        factors = extract_chip_alpha_factors(chip_r)
        s.update(factors)
        sug, reason = get_chip_alpha_suggestion(s)
        s['ChipSuggestion'] = sug
        s['ChipSuggestionReason'] = reason
        all_results.append(s)

    # 筛选目标建议
    target_suggestions = {'积极参与', '可逢低介入'}
    filtered = [s for s in all_results if s['ChipSuggestion'] in target_suggestions]

    # 按ChipTrendScore排序
    filtered.sort(key=lambda x: -x.get('ChipTrendScore', 0))

    # 输出结果
    print("=" * 80)
    print(f"筹码Alpha扫描结果：可逢低介入 + 积极参与（共 {len(filtered)} 只）")
    print("=" * 80)

    if not filtered:
        print("今日无符合条件的股票")
        return

    for i, s in enumerate(filtered, 1):
        print(f"\n【{i}】{s['名称']} ({s['代码']})")
        print(f"  筹码趋势分: {s['ChipTrendScore']:.1f} | 等级: {s['ChipGrade']} | 阶段: {s['ChipStage']}")
        print(f"  CRE={s['CRE_Score']:.0f} | 动量={s['ChipMomentum_Score']:.0f} | 压力衰减={s['PressureDecay_Score']:.0f} | 吸筹={s['Absorption_Score']:.0f} | 质心={s['CenterVelocity_Score']:.0f}")
        print(f"  建议: {s['ChipSuggestion']}")
        print(f"  理由: {s['ChipSuggestionReason']}")
        theme = s.get('所属主题', '')
        if theme:
            print(f"  主题: {theme}")

    # 汇总
    print("\n" + "=" * 80)
    print("汇总")
    print("=" * 80)
    by_sug = {}
    for s in filtered:
        by_sug.setdefault(s['ChipSuggestion'], []).append(s)
    for sug in ['积极参与', '可逢低介入']:
        items = by_sug.get(sug, [])
        print(f"\n{ sug }（{len(items)}只）:")
        for s in items:
            print(f"  {s['名称']:8s} ({s['代码']}) 趋势分={s['ChipTrendScore']:.1f} CRE={s['CRE_Score']:.0f} 动量={s['ChipMomentum_Score']:.0f} | {s['ChipSuggestionReason']}")

    # 保存到CSV
    out_path = r'D:\mystock\solo\report_daily\chip_alpha_scan_result.csv'
    out_df = pd.DataFrame(filtered)
    out_df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存: {out_path}")


if __name__ == '__main__':
    main()
