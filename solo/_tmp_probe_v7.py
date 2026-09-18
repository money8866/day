import io
import re

cfg = io.open(r"d:\mystock\solo\market_regime_v3\config.yaml", encoding="utf-8").read().splitlines()
print("总行数", len(cfg))
cur = ""
for i, l in enumerate(cfg, 1):
    if re.match(r"^[a-zA-Z_]", l):
        cur = l.split(":")[0]
    if re.search(r"rally|right_confirm|breakout|pullback|enabled|version|V7|v7", l, re.I):
        print(f"{i:>5} [{cur}] {l.rstrip()[:150]}")
