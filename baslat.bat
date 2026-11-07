@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title binancebot

echo ============================================
echo   binancebot - kurulum ve baslatma
echo ============================================

where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [HATA] Python bulunamadi. python.org'dan Python 3.12 kurun ^("Add to PATH" isaretli^).
  pause & exit /b 1
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [HATA] Node.js bulunamadi. nodejs.org'dan LTS surumunu kurun.
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Python sanal ortami olusturuluyor...
  %PY% -m venv .venv || (echo [HATA] venv olusturulamadi & pause & exit /b 1)
)

echo [2/3] Python paketleri kontrol ediliyor...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r backend\requirements.txt || (echo [HATA] Paket kurulumu basarisiz & pause & exit /b 1)

rem Kod guncellendiyse (git surumu degistiyse) arayuzu kendiliginden yeniden derle
set "REV="
for /f "usebackq delims=" %%r in (`git rev-parse HEAD 2^>nul`) do set "REV=%%r"
set "BUILT="
if exist "frontend\dist\.build-rev" set /p BUILT=<"frontend\dist\.build-rev"
set "NEED_BUILD="
if not exist "frontend\dist\index.html" set "NEED_BUILD=1"
if defined REV if not "%REV%"=="%BUILT%" set "NEED_BUILD=1"

if defined NEED_BUILD (
  echo [3/3] Arayuz derleniyor ^(ilk seferde birkac dakika surebilir^)...
  pushd frontend
  call npm.cmd ci || (echo [HATA] npm ci basarisiz & popd & pause & exit /b 1)
  call npm.cmd run build || (echo [HATA] Arayuz derlenemedi & popd & pause & exit /b 1)
  popd
  if defined REV (echo %REV%)>"frontend\dist\.build-rev"
) else (
  echo [3/3] Arayuz guncel.
)

rem Arka planda eski bir bot calisiyorsa kapat (yoksa tarayici eski surume baglanir)
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo [UYARI] 8000 portunda eski bir bot calisiyor; kapatiliyor...
  powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
  timeout /t 2 /nobreak >nul
)

echo.
echo Panel aciliyor: http://localhost:8000
echo Kapatmak icin bu pencerede Ctrl+C yapin.
echo.
start "" http://localhost:8000
cd backend
"..\.venv\Scripts\python.exe" -m app
pause
