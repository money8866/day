"""python -m market_regime_v3 入口（无warning）"""
import sys
from market_regime_v3.main import main

if __name__ == '__main__':
    # 透传命令行参数，如: python -m market_regime_v3 --date 20260724 --push
    sys.argv[0] = 'market_regime_v3'
    main()
