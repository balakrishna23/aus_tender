$ErrorActionPreference = 'Stop'
$pilotRoot = $PSScriptRoot
$pilotOllama = Join-Path $pilotRoot 'runtime\ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $pilotOllama)) { throw 'Portable Ollama is not installed in runtime\ollama.' }
try { Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null }
catch {
    $env:OLLAMA_HOST = '127.0.0.1:11434'
    $env:OLLAMA_MODELS = Join-Path $pilotRoot 'runtime\models'
    $env:OLLAMA_CONTEXT_LENGTH = '4096'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    Start-Process -FilePath $pilotOllama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $pilotRoot 'runtime\server.log') -RedirectStandardError (Join-Path $pilotRoot 'runtime\server-error.log')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try { Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null; break } catch { }
    }
}
Write-Host 'Open http://127.0.0.1:8765 in your browser. Ctrl+C stops the assistant.'
python (Join-Path $pilotRoot 'assistant.py')
