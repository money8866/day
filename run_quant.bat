@echo off

cd /d C:\Users\kongx\mystock

C:\Users\kongx\AppData\Local\Python\bin\python.exe tushare_quant.py

C:\Users\kongx\AppData\Local\Python\bin\python.exe etf_quant.py

C:\Users\kongx\AppData\Local\Python\bin\python.exe genindex.py
git add .
git commit -m "update all"
git push
