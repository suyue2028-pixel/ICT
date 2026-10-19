@echo off
setlocal
cd /d "%~dp0"
where node.exe >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js is not available in this terminal.
  echo Open a terminal with Node.js configured, then run npm.cmd run dev.
  exit /b 1
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm.cmd is not available in this terminal.
  exit /b 1
)
if not exist "node_modules\.bin\vite.cmd" (
  echo [ERROR] Project dependencies are missing. Run npm.cmd ci first.
  exit /b 1
)
call npm.cmd run dev %*
exit /b %errorlevel%
