@echo off
setlocal
rem Dong goi Lilyzono thanh dist\Lilyzono\Lilyzono.exe (khong can cai Python tren may chay).
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :err
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller || goto :err

rem Uu tien DLL he thong, tranh dong goi nham icuuc.dll/CRT tu ung dung khac trong PATH.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed ^
  --exclude-module onnxruntime ^
  --name Lilyzono ^
  --icon assets\icon.ico ^
  --collect-submodules keyring.backends ^
  --collect-submodules win32com ^
  --hidden-import fontTools.ttLib ^
  --collect-data psd_tools ^
  Lilyzono.py || goto :err

rem Bieu tuong va model nhan dien chu dat canh Lilyzono.exe (model co san thi chep sang, khoi phai tai lai).
xcopy /y /i /q assets dist\Lilyzono\assets >nul
if exist models\comictextdetector.pt.onnx xcopy /y /i /q models\comictextdetector.pt.onnx dist\Lilyzono\models\ >nul

echo.
echo Xong: dist\Lilyzono\Lilyzono.exe
echo Gui ca thu muc dist\Lilyzono cho nguoi khac (hoac nen thanh .zip).
exit /b 0
:err
echo Dong goi that bai.
pause
exit /b 1
