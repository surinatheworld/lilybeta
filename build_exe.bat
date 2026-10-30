@echo off
rem Dong goi EditManga thanh dist\EditManga\EditManga.exe (khong can cai Python tren may chay).
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :err
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller || goto :err

.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed ^
  --name EditManga ^
  --icon assets\icon.ico ^
  --collect-submodules keyring.backends ^
  EditManga.py || goto :err

rem Bieu tuong va model AI dat canh EditManga.exe (model co san thi chep sang, khoi phai tai lai).
xcopy /y /i /q assets dist\EditManga\assets >nul
if exist models\*.onnx xcopy /y /i /q models\*.onnx dist\EditManga\models >nul

echo.
echo Xong: dist\EditManga\EditManga.exe
echo Gui ca thu muc dist\EditManga cho nguoi khac (hoac nen thanh .zip).
exit /b 0
:err
echo Dong goi that bai.
pause
exit /b 1
