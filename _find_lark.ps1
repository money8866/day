Get-ChildItem -Recurse $env:APPDATA\lark-cli -ErrorAction SilentlyContinue | Select-Object FullName
Get-ChildItem -Recurse $env:LOCALAPPDATA\lark-cli -ErrorAction SilentlyContinue | Select-Object FullName
