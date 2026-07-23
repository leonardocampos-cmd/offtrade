@echo off
:loop
for /f "tokens=1 delims=:" %%a in ("%time%") do set HH=1%%a
set /a HH=%HH% - 100
if %HH% GEQ 9 if %HH% LSS 18 (
    echo [%date% %time%] Iniciando... >> "G:\Meu Drive\offtrade\offtrade.log"
    "g:\Meu Drive\offtrade\.venv\Scripts\python.exe" "g:\Meu Drive\offtrade\main.py" >> "G:\Meu Drive\offtrade\offtrade.log" 2>&1
    if %errorlevel% == 0 (
        echo [%date% %time%] OK >> "G:\Meu Drive\offtrade\offtrade.log"
    ) else (
        echo [%date% %time%] ERRO (codigo %errorlevel%) >> "G:\Meu Drive\offtrade\offtrade.log"
    )
    echo ---------------------------------------- >> "G:\Meu Drive\offtrade\offtrade.log"
    goto loop
) else (
    echo [%date% %time%] Fora do horario comercial (9h-18h) - encerrando ate o proximo gatilho >> "G:\Meu Drive\offtrade\offtrade.log"
)
