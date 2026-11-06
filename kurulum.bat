@echo off
setlocal EnableExtensions
title binancebot - kurulum ve calistirma
chcp 65001 >nul

rem ============================================================
rem  Masaustunde "binancebot2026" klasorunu olusturur, projeyi
rem  GitHub'dan ceker (varsa gunceller) ve botu baslatir.
rem  Git, Python 3.12 ve Node.js yoksa winget ile kurar.
rem ============================================================

set "REPO=https://github.com/ahmetnebii/binancebot.git"
set "BRANCH=claude/friendly-clarke-kvk57p"
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%d"
set "HEDEF=%DESK%\binancebot2026"

echo.
echo === 1/3 Gerekli programlar kontrol ediliyor ===
where winget >nul 2>nul
if errorlevel 1 (
  echo [UYARI] winget bulunamadi. Eksik program varsa elle kurun:
  echo         Git: git-scm.com  /  Python 3.12: python.org  /  Node.js LTS: nodejs.org
)

git --version >nul 2>nul
if errorlevel 1 (
  echo Git kuruluyor...
  winget install -e --id Git.Git --silent --accept-source-agreements --accept-package-agreements
)

set "PYOK="
py -3 --version >nul 2>nul && set "PYOK=1"
if not defined PYOK python --version >nul 2>nul && set "PYOK=1"
if not defined PYOK (
  echo Python 3.12 kuruluyor...
  winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo Node.js kuruluyor...
  winget install -e --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
)

rem Yeni kurulan programlarin bu pencerede taninmasi icin PATH'i yenile
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%p"

git --version >nul 2>nul
if errorlevel 1 (
  echo [HATA] Git hala bulunamadi. Bilgisayari yeniden baslatip bu dosyayi tekrar calistirin.
  pause & exit /b 1
)

echo.
echo === 2/3 Proje hazirlaniyor: %HEDEF% ===
if exist "%HEDEF%\.git" (
  echo Proje zaten var, guncelleniyor...
  git -C "%HEDEF%" fetch origin %BRANCH% || (echo [HATA] Guncelleme alinamadi & pause & exit /b 1)
  git -C "%HEDEF%" checkout %BRANCH% >nul 2>nul
  git -C "%HEDEF%" pull --ff-only origin %BRANCH% || (echo [HATA] Guncelleme uygulanamadi & pause & exit /b 1)
  if exist "%HEDEF%\frontend\dist" rmdir /s /q "%HEDEF%\frontend\dist"
) else (
  if not exist "%HEDEF%" mkdir "%HEDEF%"
  git clone -b %BRANCH% "%REPO%" "%HEDEF%" || (echo [HATA] Proje indirilemedi. Klasor bos degilse icini temizleyin. & pause & exit /b 1)
)

echo.
echo === 3/3 Bot baslatiliyor ===
cd /d "%HEDEF%"
call baslat.bat
