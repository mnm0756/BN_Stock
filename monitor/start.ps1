param(
    [int]$Port = 8765,
    [string]$HostAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$env:PYTHONNOUSERSITE = "1"
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

conda run --no-capture-output -n bn-stock-monitor `
    python -m uvicorn app.main:app --host $HostAddress --port $Port --http h11
