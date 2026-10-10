@echo off
REM Build GNU Sorter Windows bundle with PyInstaller (run in MSYS2 UCRT64)
setlocal
cd /d "%~dp0\..\.."
if not exist dist mkdir dist
pyinstaller --noconfirm --name gnu-sorter --windowed --add-data "data;data" --icon "data/icons/gnu-sorter.ico" main.py
echo Build complete: dist\gnu-sorter\
endlocal
