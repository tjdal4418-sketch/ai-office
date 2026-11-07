@echo off
chcp 65001 >nul
rem Start the AI Office server (if not running yet) and open the office view. Close this window to stop.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [오류] .venv 가 없어요. 먼저 설치하세요:  python -m venv .venv  그리고  .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
rem If port 8000 is already listening, the server is already running: just open the browser
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo 서버가 이미 켜져 있어요. 브라우저만 엽니다: http://localhost:8000/
  start "" http://localhost:8000/
  timeout /t 3 /nobreak >nul
  exit /b 0
)
rem Wait 3 seconds for the server, then open the browser
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000/"
echo AI 오피스 서버를 켭니다...  주소: http://localhost:8000/
echo 끝내려면 이 창을 닫으세요.
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
pause
