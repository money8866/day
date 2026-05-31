#!/usr/bin/env python3
# tushare_quant_patch.py - 内存优化补丁
# 用法: python tushare_quant_patch.py

import sys
import gc
sys.path.insert(0, r'D:\mystock')

import tushare_quant as tq

# 补丁1: save_result 改为 executemany 批量写入
import sqlite3

def patched_save_result(df):
    conn = sqlite3.connect(tq.DB_PATH, timeout=30)
    today = tq.TRADE_DATE
    conn.execute("DELETE FROM stock_result WHERE date=?", (today,))
    
    rows = []
    for i, row in enumerate(df.itertuples()):
        rows.append((
            today,
            i + 1,
            getattr(row, "代码", ""),
            getattr(row, "名称", ""),
            getattr(row, "现价", 0),
            getattr(row, "成交额", ""),
            getattr(row, "最终评分", "")
        ))
    
    conn.executemany(
        "INSERT INTO stock_result (date, rank, code, name, close, amount, score) VALUES (?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()
    del rows
    gc.collect()
    print("[补丁] save_result 批量写入完成")

tq.save_result = patched_save_result

# 补丁2: run() 中加 gc.collect()
original_run = tq.run

def patched_run():
    import pandas as pd
    # 原 run 逻辑在这里重写（简化版）
    # 只处理前50只股票，避免内存爆炸
    print("[补丁] 使用内存优化版 run()")
    
    # 板块分析
    sector_df = tq.block.analyze_hot_sectors()
    if not sector_df.empty:
        print("\n========== 最强主线板块 ==========\n")
        print(sector_df.head(20).to_string(index=False))
    
    # 市场情绪
    emotion_result = tq.emotion.analyze_market_emotion(sector_df)
    emotion_text = str(emotion_result) if emotion_result else ""
    print(emotion_text)
    
    # 获取市场数据（分批处理）
    market = tq.get_market()
    result = []
    total = len(market)
    
    print(f"[补丁] 全市场共 {total} 只股票，开始分批处理...")
    
    BATCH = 50  # 每批50只
    for batch_start in range(0, min(total, 200), BATCH):  # 先只处理前200只测试
        batch_end = min(batch_start + BATCH, total)
        print(f"[补丁] 处理 {batch_start+1}-{batch_end}/{total}")
        
        batch = market.iloc[batch_start:batch_end]
        for idx, row in batch.iterrows():
            ts_code = row['ts_code']
            try:
                hist = tq.get_hist_data(ts_code)
                if hist is None or len(hist) < 80:
                    continue
                ok = tq.strategy(hist, ts_code, "弱")
                if ok and row['total_mv']/10000 >= 80:
                    result.append({
                        '代码': ts_code,
                        '名称': row['name'],
                        '现价': row['close'],
                        '涨跌幅': row['pct_chg'],
                        '成交额': row['amount'],
                        '总市值（亿元）': row['total_mv']/10000,
                    })
                    print(f"✅ 命中: {ts_code} {row['name']}")
            except Exception as e:
                print(f"{ts_code} {e}")
                continue
        
        # 每批结束后释放内存
        del batch
        gc.collect()
    
    if not result:
        print("无结果")
        return
    
    result_df = pd.DataFrame(result)
    del result, market
    gc.collect()
    
    # 多因子评分（分批）
    factor_list = []
    for idx, row in result_df.iterrows():
        ts_code = row['代码']
        hist = tq.get_hist_data(ts_code)
        if hist is None:
            continue
        factor = tq.calc_dual_layer_score_v4(hist)
        factor_list.append(factor)
    
    factor_df = pd.DataFrame(factor_list)
    result_df = pd.concat([result_df.reset_index(drop=True), factor_df.reset_index(drop=True)], axis=1)
    
    del factor_list, factor_df
    gc.collect()
    
    # 保存结果
    tq.save_result(result_df)
    
    # 生成报告
    tq.generate_report(result_df, sector_df, emotion_result)
    
    print("[补丁] 运行完成！")

tq.run = patched_run

if __name__ == '__main__':
    tq.run()
