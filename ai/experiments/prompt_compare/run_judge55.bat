@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
rem 이미 만든 묶음의 답을 주 판정기(.env OPENAI_JUDGE_MODEL)로 다시 판정(새 생성 없음, 반복 1회차만). 사용법: run_judge55.bat <묶음 id>
if "%~1"=="" (echo 묶음 id 를 적는다. 예: run_judge55.bat b1013 & exit /b 1)
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; & .\.venv\Scripts\python.exe -u experiments\prompt_compare\batch.py all --batch %~1 --workers 20 --judge55 r1 2>&1 | Tee-Object -Append -FilePath experiments\prompt_compare\results\batch_%~1_log.txt"
pause
