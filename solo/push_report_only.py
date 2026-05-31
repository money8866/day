#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直接推送已生成的报告到微信

import os

def main():
    try:
        from push_review import push_daily_review
        
        trade_date = "20260529"
        report_file = os.path.join("cache_backbone_tushare", f"daily_review_{trade_date}.txt")
        risk_file = os.path.join("cache_backbone_tushare", f"recession_risk_report_{trade_date}.txt")
        
        print("="*60)
        print("📱 开始推送微信通知")
        print("="*60)
        
        # 读取风险报告
        recession_risk_report = None
        if os.path.exists(risk_file):
            try:
                import json
                with open(risk_file, 'r', encoding='utf-8') as f:
                    recession_risk_report = json.load(f)
            except Exception as e:
                print(f"⚠️ 读取风险报告失败: {e}")
        
        if os.path.exists(report_file):
            success = push_daily_review(report_file, trade_date, recession_risk_report)
            
            if success:
                print("✅ 微信推送成功！")
            else:
                print("⚠️ 微信推送失败，请检查配置")
        else:
            print(f"⚠️ 复盘报告不存在: {report_file}")
    except ImportError as e:
        print(f"⚠️ 未找到 push_review 模块: {e}")
    except Exception as e:
        print(f"⚠️ 微信推送异常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

