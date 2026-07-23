$ErrorActionPreference = "Stop"
$env:PYTHONNOUSERSITE = "1"
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$Existing = conda env list | Select-String -Pattern "^bn-stock-monitor\s"
if ($Existing) {
    conda env update -n bn-stock-monitor -f environment.yml --prune --solver libmamba
} else {
    conda env create -f environment.yml --solver libmamba
}

if ($LASTEXITCODE -ne 0) {
    throw "Conda environment setup failed with exit code $LASTEXITCODE"
}

Write-Host "Environment ready. Start with: .\start.ps1"
