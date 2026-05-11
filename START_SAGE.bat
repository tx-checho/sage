@echo off
title S.A.G.E. Launcher
echo Installing/checking dependencies...
pip install -r requirements.txt -q
echo Starting S.A.G.E...
python sage.py
pause
