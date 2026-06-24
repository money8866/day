"""Quick test: scan 600183, 603929, 603678"""
import sys, os
sys.path.insert(0, r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo\multi_factor_picker')
os.chdir(r'd:\mystock\solo\multi_factor_picker')

from wave2_pattern_scanner import WavePatternDetector, ResonanceScorer
from cache_backbone_tushare import TushareDataCache

cache = TushareDataCache()
detector = WavePatternDetector(cache)
scorer = ResonanceScorer(cache)

codes = ['600183.SH', '603929.SH', '603678.SH']
for code in codes:
    try:
        r = detector.detect(code, today_mode=True)
        if r:
            score = scorer.score(r)
            print(f"  {code} {r.get('name','')} {r['pattern']} score={score}")
        else:
            print(f"  {code}: NO SIGNAL")
    except Exception as e:
        print(f"  {code}: ERROR {e}")
