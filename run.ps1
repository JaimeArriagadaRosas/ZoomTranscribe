$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$exitCode = 1
Push-Location -LiteralPath $projectRoot
try {
    $env:PYTHONUTF8 = "1"
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
    & $python -m scripts.pipeline @args
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
