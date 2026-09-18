import io
import sqlite3
import sys

sys.path.insert(0, r"d:\mystock\solo")
conn = sqlite3.connect(r"d:\mystock\solo\sli\db\sli.db")
ds = [r[0] for r in conn.execute("select distinct trade_date from leaderboard_v2 order by trade_date")]
print("leaderboard_v2 现有快照日期:", ds)
conn.close()

from w7_second_wave_engine import load_sli_codes
for d in ("20260825", "20260826", "20260827"):
    codes = load_sli_codes(d)
    print(f"  {d}: SLI 票池 {len(codes) if codes else 'None（未启用过滤）'} 只 | 会稽山在池内: {('601579.SH' in codes) if codes else 'n/a'}")
