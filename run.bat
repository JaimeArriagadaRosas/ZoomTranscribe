@echo off
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PIPELINE_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PIPELINE_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PIPELINE_PYTHON%" -m scripts.pipeline %*
set "PIPELINE_EXIT=%ERRORLEVEL%"
popd
exit /b %PIPELINE_EXIT%
