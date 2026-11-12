# Manual preview: no task registration and no email delivery.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$bdmPython = if ($env:ILLOURA_BDM_PYTHON) { $env:ILLOURA_BDM_PYTHON } else { Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe' }
if (-not (Test-Path $bdmPython)) { throw "Python 3.11 not found at $bdmPython; set ILLOURA_BDM_PYTHON to its path." }
& $bdmPython bdm_monitor.py poll --refresh
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $bdmPython bdm_monitor.py preview
exit $LASTEXITCODE
