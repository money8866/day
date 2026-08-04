@echo off
chcp 65001 >nul
cd /d D:\mystock\solo\multi_factor_picker
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set LOGDATE=%%i
set LOG=%TEMP%\washout_push_%LOGDATE%.log

REM only run on trading days, skip holidays/weekends
python -c "import os,importlib.util,sys,datetime; spec=importlib.util.spec_from_file_location('m',os.path.join(os.getcwd(),'main.py')); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); cfg=mod.load_config(); tok=mod.get_token(cfg); from data_fetcher import DataFetcher; f=DataFetcher(tok,cfg); t=datetime.date.today().strftime('%%Y%%m%%d'); df=f.get_trade_cal(t,t); ok=(df is not None and len(df)>0); print('TODAY',t,'IS_TRADE_DAY',ok); sys.exit(0 if ok else 1)" >> %LOG% 2>&1
if errorlevel 1 (
    echo [%date% %time%] Non-trading day %LOGDATE%, skip this run >> %LOG%
    exit /b 0
)

echo [%date% %time%] Start generating washout recovery report... >> %LOG%
python enhanced_timing_bull_all.py >> %LOG% 2>&1
if errorlevel 1 (
    echo [%date% %time%] Report generation failed, code=%errorlevel% >> %LOG%
    exit /b %errorlevel%
)

echo [%date% %time%] Start wechat push... >> %LOG%
python push_washout_recovery.py >> %LOG% 2>&1
if errorlevel 1 (
    echo [%date% %time%] Wechat push failed, code=%errorlevel% >> %LOG%
    exit /b %errorlevel%
)

echo [%date% %time%] All done >> %LOG%
