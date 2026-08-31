@echo off
title PaperCraft AI Studio
echo Starting PaperCraft AI Studio...
py -3.13 -m papercraft.ui.app
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%
    pause
)
