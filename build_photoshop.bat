@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv || exit /b 1
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller || exit /b 1
set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --collect-binaries onnxruntime --name LilyzonoPhotoshop --icon assets\icon.ico --hidden-import fontTools.ttLib --collect-data psd_tools --collect-data rapidocr --collect-submodules rapidocr.inference_engine.onnxruntime LilyzonoPhotoshop.py || exit /b 1
copy /y photoshop\Lilyzono.jsx dist\LilyzonoPhotoshop\Lilyzono.jsx >nul
copy /y photoshop\README.md dist\LilyzonoPhotoshop\README.md >nul
if exist models\comictextdetector.pt.onnx xcopy /y /i /q models\comictextdetector.pt.onnx dist\LilyzonoPhotoshop\models\ >nul
if exist models\lama_fp32.onnx xcopy /y /i /q models\lama_fp32.onnx dist\LilyzonoPhotoshop\models\ >nul
if exist models\manga-ocr\encoder_model.onnx xcopy /y /i /q models\manga-ocr dist\LilyzonoPhotoshop\models\manga-ocr\ >nul
echo Xong: dist\LilyzonoPhotoshop\Lilyzono.jsx
echo Trong Photoshop: File ^> Scripts ^> Browse, chon Lilyzono.jsx.
