@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%~dp0"
set "GATEWAY_PYTHON="
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"

if not exist "%ROOT%logs" mkdir "%ROOT%logs" >nul 2>&1
if not exist "%ROOT%state" mkdir "%ROOT%state" >nul 2>&1
if not exist "%ROOT%scripts\workflow_platform.py" goto :IncompletePackage
if not exist "%ROOT%MANIFEST.json" goto :IncompletePackage
if not exist "%ROOT%PACKAGE_METADATA.json" goto :IncompletePackage

where py >nul 2>&1
if not errorlevel 1 (
  if not defined GATEWAY_PYTHON for /f "delims=" %%P in ('py -3.13 -c "import sys;print(sys.executable)" 2^>nul') do if not defined GATEWAY_PYTHON set "GATEWAY_PYTHON=%%P"
  if not defined GATEWAY_PYTHON for /f "delims=" %%P in ('py -3.14 -c "import sys;print(sys.executable)" 2^>nul') do if not defined GATEWAY_PYTHON set "GATEWAY_PYTHON=%%P"
  if not defined GATEWAY_PYTHON for /f "delims=" %%P in ('py -3.12 -c "import sys;print(sys.executable)" 2^>nul') do if not defined GATEWAY_PYTHON set "GATEWAY_PYTHON=%%P"
  if not defined GATEWAY_PYTHON for /f "delims=" %%P in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do if not defined GATEWAY_PYTHON set "GATEWAY_PYTHON=%%P"
  if not defined GATEWAY_PYTHON for /f "delims=" %%P in ('py -3.10 -c "import sys;print(sys.executable)" 2^>nul') do if not defined GATEWAY_PYTHON set "GATEWAY_PYTHON=%%P"
)
if not defined GATEWAY_PYTHON (
  where python >nul 2>&1
  if not errorlevel 1 for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do if not defined GATEWAY_PYTHON set "GATEWAY_PYTHON=%%P"
)
if not defined GATEWAY_PYTHON goto :PythonMissing
"%GATEWAY_PYTHON%" -c "import sys;raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
if errorlevel 1 goto :PythonMissing

"%GATEWAY_PYTHON%" "%ROOT%scripts\workflow_platform.py" %*
set "APP_EXIT=%ERRORLEVEL%"
if "%APP_EXIT%"=="0" exit /b 0

echo.
echo ERROR: Workflow platform action exited with code %APP_EXIT%.
echo Review project-local logs, state, reports, and diagnostics for details.
pause
exit /b %APP_EXIT%

:IncompletePackage
>"%ROOT%logs\launcher_bootstrap_failure.txt" echo %DATE% %TIME% ERROR: The extracted project is incomplete. Required release files are missing.
echo ERROR: The project folder is incomplete or the launcher is being run from a partial copy.
echo Extract the full release ZIP into a normal writable local folder, then run this launcher again.
pause
exit /b 2

:PythonMissing
>"%ROOT%logs\launcher_bootstrap_failure.txt" echo %DATE% %TIME% ERROR: Supported Python 3.10 through 3.14 was not found.
echo ERROR: Python 3.10 through 3.14 was not found.
echo Install a supported 64-bit Python release, then run this launcher again.
pause
exit /b 1
