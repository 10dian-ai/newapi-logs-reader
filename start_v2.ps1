$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
if (Test-Path .env) { Get-Content .env | ForEach-Object { if ($_ -match "^\s*([^#][^=]*)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim().Trim('"'), "Process") } } }
python server_v2.py
