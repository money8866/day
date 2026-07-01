"""
测试Tushare公告接口
"""
import tushare as ts
import os

TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

print('设置Tushare token...')
ts.set_token(TOKEN)
print('Tushare token设置成功')

print('创建Pro API...')
pro = ts.pro_api()
print('Pro API创建成功')

print()
print('测试公告接口 (pro.anns)...')
try:
    # 测试中天科技(600522.SH)
    df = pro.anns(ts_code='600522.SH', start_date='20260601', end_date='20260701')
    
    if df is not None and len(df) > 0:
        print(f'✅ 公告接口成功！获取到 {len(df)} 条公告')
        print()
        print('前5条公告:')
        print(df[['datetime', 'type', 'title']].head())
    else:
        print('⚠️ 公告接口返回空')
        
except Exception as e:
    print(f'❌ 公告接口失败: {e}')
    print()
    print('可能原因:')
    print('1. Token积分不足（需要500积分）')
    print('2. 接口名称错误')
    print('3. 网络问题')

print()
print('测试完成')
