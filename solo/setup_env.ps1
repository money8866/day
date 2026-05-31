# 主线板块 + 中军分析系统 - 环境配置脚本 (PowerShell)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "主线板块 + 中军分析系统 - 环境配置" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host "[1/3] 检查 Python 环境..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host $pythonVersion -ForegroundColor Green
} catch {
    Write-Host "未找到 Python，请先安装 Python 3.8+" -ForegroundColor Red
    Write-Host "下载地址: https://www.python.org/downloads/" -ForegroundColor Red
    Read-Host "按 Enter 键退出"
    exit 1
}
Write-Host ""

Write-Host "[2/3] 安装项目依赖..." -ForegroundColor Yellow
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "依赖安装失败，尝试使用官方源..." -ForegroundColor Yellow
    pip install -r requirements.txt
}
Write-Host ""

Write-Host "[3/3] 检查配置文件..." -ForegroundColor Yellow
$envPath = Join-Path (Split-Path -Parent $scriptPath) "TUSHARE.env"
if (Test-Path $envPath) {
    Write-Host "找到 TUSHARE.env 配置文件" -ForegroundColor Green
} else {
    Write-Host "警告: 未找到 TUSHARE.env 配置文件" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "环境配置完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "现在可以运行:"
Write-Host "  1. .\run_中军分析.bat  - 一键运行分析"
Write-Host "  2. python main_with_backbone.py - 运行整合版"
Write-Host ""
Read-Host "按 Enter 键退出"
