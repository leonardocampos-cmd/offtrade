@echo off
echo [%date% %time%] Iniciando Canhoto Digital... >> "G:\Meu Drive\offtrade\canhoto.log"
"g:\Meu Drive\offtrade\.venv\Scripts\python.exe" "g:\Meu Drive\offtrade\canhoto_digital.py" >> "G:\Meu Drive\offtrade\canhoto.log" 2>&1
if %errorlevel% == 0 (
    echo [%date% %time%] OK >> "G:\Meu Drive\offtrade\canhoto.log"
) else (
    echo [%date% %time%] ERRO (codigo %errorlevel%) >> "G:\Meu Drive\offtrade\canhoto.log"
)
echo ---------------------------------------- >> "G:\Meu Drive\offtrade\canhoto.log"
