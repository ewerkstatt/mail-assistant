@echo off
REM Build Mail-Assistant.exe (Windows, Python 3.10+)
REM Usage: build.bat            -> default Python via py launcher
REM        build.bat -3.13      -> specific version via py launcher
cd /d "%~dp0"

set PY=py %1
where py >nul 2>nul || set PY=python

%PY% --version || (echo Python nicht gefunden & pause & exit /b 1)
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt pyinstaller || (echo Paketinstallation fehlgeschlagen & pause & exit /b 1)
%PY% -m PyInstaller --noconfirm --onefile --windowed --name Mail-Assistant ^
  --hidden-import keyring.backends.Windows ^
  --hidden-import win32com.client ^
  main.py || (echo Build fehlgeschlagen & pause & exit /b 1)
echo.
echo Fertig: dist\Mail-Assistant.exe
pause
