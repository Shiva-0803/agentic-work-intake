@echo off
cd /d "C:\Users\SHIVAS~1\GEMINI~1\ANTIGR~1\scratch\AGENTI~1"
C:\Users\SHIVAS~1\AppData\Local\Programs\Python\Python313\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000 > uvicorn_scheduler.log 2>&1
