@echo off
REM ---------------------------------------------------------------------------
REM Build TrackKeys Mirror into a single windowed .exe (dist\TrackKeysMirror.exe)
REM
REM Prerequisites (in THIS Python environment):
REM   the VirtualBox SDK's vboxapi must already be installed
REM     (python -c "import vboxapi" must succeed)
REM   PyInstaller/pystray/pillow are auto-installed below if missing.
REM
REM Note: the resulting .exe still requires VirtualBox to be installed on the
REM machine it runs on -- it talks to VirtualBox's COM server. Packaging removes
REM the Python requirement, not the VirtualBox one.
REM ---------------------------------------------------------------------------
setlocal

REM Use "python -m" so we don't depend on Scripts\ being on PATH.
echo Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo PyInstaller not found; installing it...
  python -m pip install pyinstaller pystray pillow || goto :err
)

echo Generating icon...
python -c "import icon; icon.save_ico('icon.ico')" || goto :err

echo Building executable...
python -m PyInstaller --noconfirm --onefile --windowed --name TrackKeysMirror ^
  --icon icon.ico ^
  --hidden-import vboxapi ^
  --hidden-import win32com ^
  --hidden-import win32com.client ^
  --hidden-import pythoncom ^
  --hidden-import pywintypes ^
  --collect-submodules vboxapi ^
  gui.pyw || goto :err

echo.
echo Done: dist\TrackKeysMirror.exe
goto :eof

:err
echo.
echo Build failed. See the output above.
exit /b 1
