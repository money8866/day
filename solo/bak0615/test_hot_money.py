
# -*- coding: utf-8 -*-
"""测试游资开仓信号的涨跌幅数据"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, calc_dual_layer_score_v75, calc_hot_money_open_score


def test_data():
    print("测试埃斯顿数据")
    print("=" * 80)
    
    code = '002747.SZ'
    df = get_hist_data(code)
    if df is None or len(df) &lt; 20:
        print(f"数据不足: {len(df)}")
        return
    
    # 显示K线最近2天
    print(f"\n最近2天K线:")
    print(df[['close']].tail(2))
    
    # 计算涨跌幅
    if len(df) &gt;= 2:
        today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100
        print(f"\n计算涨跌幅: {today_pct:.2f}%")
    
    # 计算V7.5评分
    v7_result = calc_dual_layer_score_v75(df, code, None, '人形机器人')
    
    # 设置主题纯度为100
    v7_result['主题纯度'] = 100.0
    v7_result['所属主题'] = '人形机器人'
    
    # 计算开仓评分
    open_score, structure_type, recommendation = calc_hot_money_open_score(
        v7_result, df, None, '人形机器人'
    )
    
    print(f"\n【开仓信号】")
    print(f"代码: {code}")
    print(f"名称: {v7_result['名称']}")
    print(f"涨跌幅: {v7_result.get('涨跌幅', 'N/A')}")
    print(f"开仓评分: {open_score}")
    print(f"结构类型: {structure_type}")
    print(f"推荐理由: {recommendation}")
    
    # 打印完整的v7_result
    print(f"\n【完整数据】")
    for k, v in v7_result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    test_data()
