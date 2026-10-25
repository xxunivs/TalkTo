@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
rem 비교 실험 묶음: 생성 + 판정. 사용법: run_batch.bat <묶음 id>  (같은 id 로 다시 실행하면 끝난 것은 건너뛰고 이어서)
if "%~1"=="" (echo 묶음 id 를 적는다. 예: run_batch.bat b1013 & exit /b 1)
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; & .\.venv\Scripts\python.exe -u experiments\prompt_compare\batch.py all --batch %~1 --workers 20 2>&1 | Tee-Object -Append -FilePath experiments\prompt_compare\results\batch_%~1_log.txt"
pause
