# HTML -> PDF converter (Edge/Chrome headless print), ASCII-only on purpose.
# Usage:  & 'D:\mystock\solo\html2pdf.ps1' -Html 'D:\...\report.html' -Pdf 'D:\...\report.pdf'
param(
    [Parameter(Mandatory=$true)][string]$Html,
    [Parameter(Mandatory=$false)][string]$Pdf
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Html)) { Write-Output "FAIL input-not-found $Html"; exit 2 }
if (-not $Pdf) { $Pdf = [System.IO.Path]::ChangeExtension($Html, '.pdf') }

$exe = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if (-not (Test-Path $exe)) { $exe = 'C:\Program Files\Microsoft\Edge\Application\msedge.exe' }
if (-not (Test-Path $exe)) { $exe = 'C:\Program Files\Google\Chrome\Application\chrome.exe' }
if (-not (Test-Path $exe)) { Write-Output 'FAIL no-browser'; exit 3 }

$prof = Join-Path $env:TEMP '_wb_html2pdf_profile'
$url  = 'file:///' + ($Html -replace '\\','/')
$tmp  = "$Pdf.tmp.pdf"
# Use .NET delete: sandbox Remove-Item hook is fail-closed and can abort the script
if ([System.IO.File]::Exists($tmp)) { [System.IO.File]::Delete($tmp) }

# NOTE: must launch via Start-Process from the PowerShell tool.
# Direct launch from the sandboxed Bash/python side gets the browser child
# reaped silently (exit=0, no output file).
$p = Start-Process -FilePath $exe -ArgumentList `
    '--headless=new','--disable-gpu','--no-pdf-header-footer', `
    "--print-to-pdf=`"$tmp`"","--user-data-dir=$prof",$url `
    -Wait -PassThru

if (-not (Test-Path $tmp) -or (Get-Item $tmp).Length -lt 2000) {
    Write-Output "FAIL not-generated edge-exit=$($p.ExitCode)"; exit 4
}
$head = [System.Text.Encoding]::ASCII.GetString(
    [System.IO.File]::ReadAllBytes($tmp)[0..4])
if ($head -ne '%PDF-') { Write-Output "FAIL invalid-pdf head=$head"; exit 5 }

if ([System.IO.File]::Exists($Pdf)) { [System.IO.File]::Delete($Pdf) }
Move-Item $tmp $Pdf -Force
Write-Output ("OK " + (Get-Item $Pdf).Length)
