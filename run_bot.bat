@echo off
cd /d C:\Users\USER\Desktop\discordAI-main

:loop
python main.py

echo Bot crashed. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop