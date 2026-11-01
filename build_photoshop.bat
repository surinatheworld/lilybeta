@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay run.bat de cai moi truong Python truoc.
  exit /b 1
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller || exit /b 1
set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --exclude-module onnxruntime --name LilyzonoPhotoshop --icon assets\icon.ico --hidden-import fontTools.ttLib --collect-data psd_tools LilyzonoPhotoshop.py || exit /b 1
copy /y photoshop\Lilyzono.jsx dist\LilyzonoPhotoshop\Lilyzono.jsx >nul
copy /y photoshop\README.md dist\LilyzonoPhotoshop\README.md >nul
if exist models\comictextdetector.pt.onnx xcopy /y /i /q models\comictextdetector.pt.onnx dist\LilyzonoPhotoshop\models\ >nul
echo Xong: dist\LilyzonoPhotoshop\Lilyzono.jsx
echo Trong Photoshop: File ^> Scripts ^> Browse, chon Lilyzono.jsx.
