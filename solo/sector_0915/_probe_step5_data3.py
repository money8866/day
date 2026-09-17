import pandas as pd, sys
sys.stdout.reconfigure(encoding='utf-8')
mb = pd.read_csv(r'd:\mystock\solo\sector_0915\data\sector_membership.csv', encoding='utf-8-sig',
                 dtype={'ts_code': str, 'effective_date': str})
for sid in ['T10', 'T02', 'T12', 'T01']:
    sub = mb[mb.sector_id == sid]
    print(f"\n=== {sid} {sub.sector_name.iloc[0]} | members={len(sub)} ===")
    core = sub[sub.membership_type.isin(['CORE', 'PRIMARY'])]
    print("CORE/PRIMARY:", len(core), "| by board_type:", core.board_type.value_counts().to_dict())
    print("top source_boards (CORE/PRIMARY):")
    for b, n in core.source_board.value_counts().head(12).items():
        print(f"   {b}: {n}")
    print("SECONDARY sample boards:", sub[sub.membership_type == 'SECONDARY'].source_board.value_counts().head(5).to_dict())
print("\nBJ codes in membership:", mb[mb.ts_code.str.endswith('.BJ')].shape[0])
print("ST names:", mb[mb.stock_name.str.contains('ST', na=False)].shape[0])
print("退 names:", mb[mb.stock_name.str.contains('退', na=False)].shape[0])
# name-based role calibration: T10 wind chain stock names
t10 = mb[(mb.sector_id == 'T10') & (mb.membership_type.isin(['CORE', 'PRIMARY']))]
print("\nT10 CORE/PRIMARY stock names sample:", t10.stock_name.head(30).tolist())
