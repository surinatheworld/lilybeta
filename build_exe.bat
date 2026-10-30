@echo off
rem Dong goi Lilyzono thanh dist\Lilyzono\Lilyzono.exe (khong can cai Python tren may chay).
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :err
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller || goto :err

.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed ^
  --name Lilyzono ^
  --icon assets\icon.ico ^
  --collect-submodules keyring.backends ^
  --collect-submodules win32com ^
  --hidden-import fontTools.ttLib ^
  --collect-data psd_tools ^
  Lilyzono.py || goto :err

rem Bieu tuong va model AI dat canh Lilyzono.exe (model co san thi chep sang, khoi phai tai lai).
xcopy /y /i /q assets dist\Lilyzono\assets >nul
if exist models\*.onnx xcopy /y /i /q models\*.onnx dist\Lilyzono\models >nul

echo.
echo Xong: dist\Lilyzono\Lilyzono.exe
echo Gui ca thu muc dist\Lilyzono cho nguoi khac (hoac nen thanh .zip).
exit /b 0
:err
echo Dong goi that bai.
pause
exit /b 1
