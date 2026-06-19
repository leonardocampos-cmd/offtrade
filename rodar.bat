@echo off
echo [%date% %time%] Iniciando... >> "G:\Meu Drive\offtrade\offtrade.log"
"g:\Meu Drive\offtrade\.venv\Scripts\python.exe" "g:\Meu Drive\offtrade\main.py" >> "G:\Meu Drive\offtrade\offtrade.log" 2>&1
if %errorlevel% == 0 (
    echo [%date% %time%] OK >> "G:\Meu Drive\offtrade\offtrade.log"
) else (
    echo [%date% %time%] ERRO (codigo %errorlevel%) >> "G:\Meu Drive\offtrade\offtrade.log"
)
echo ---------------------------------------- >> "G:\Meu Drive\offtrade\offtrade.log"
