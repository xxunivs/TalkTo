@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
rem 프롬프트 비교 실험 전체(run → judge → report). 결과: results\report_*.md
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; & .\.venv\Scripts\python.exe experiments\prompt_compare\compare.py all 2>&1 | Tee-Object -FilePath experiments\prompt_compare\results\run_log.txt"
pause
