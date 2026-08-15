# -*- coding: utf-8 -*-
"""BTS 扫描器根目录 shim：python bts_scanner.py / python -m bts_scanner"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bts.scanner import main

if __name__ == '__main__':
    main()
