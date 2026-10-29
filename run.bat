@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Dang cai dat lan dau...
  python -m venv .venv || goto :err
)
.venv\Scripts\python -c "import PySide6, cv2, numpy, docx, onnxruntime" 2>nul || (
  echo Dang cai thu vien...
  .venv\Scripts\python -m pip install -r requirements.txt || goto :err
)
start "" .venv\Scripts\pythonw -m app.main %*
exit /b
:err
echo Cai dat that bai. Hay cai Python 3.10+ tu python.org roi chay lai.
pause
