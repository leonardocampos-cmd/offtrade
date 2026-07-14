@echo off
echo [%date% %time%] Iniciando report diario vendedor... >> "G:\Meu Drive\offtrade\report_diario.log"
"g:\Meu Drive\offtrade\.venv\Scripts\python.exe" "g:\Meu Drive\offtrade\report_diario_vendedor.py" >> "G:\Meu Drive\offtrade\report_diario.log" 2>&1
if %errorlevel% == 0 (
    echo [%date% %time%] OK >> "G:\Meu Drive\offtrade\report_diario.log"
) else (
    echo [%date% %time%] ERRO (codigo %errorlevel%) >> "G:\Meu Drive\offtrade\report_diario.log"
)
echo ---------------------------------------- >> "G:\Meu Drive\offtrade\report_diario.log"
