$ErrorActionPreference = 'Stop'
if (-not (Test-Path .venv)) { py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r requirements_v2.txt
& .\.venv\Scripts\python.exe server_entry.py
