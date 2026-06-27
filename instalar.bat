@echo off
title Ondas Binaurais - Instalador
color 0B
echo.
echo  ================================================
echo    Ondas Binaurais - App Completo (Instalador)
echo  ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERRO] Python nao encontrado!
    echo.
    echo  Baixe em: https://www.python.org/downloads/
    echo  IMPORTANTE: marque "Add Python to PATH" ao instalar.
    echo.
    pause & exit /b 1
)
python --version
echo  [OK] Python encontrado!
echo.

echo  Instalando dependencias (numpy, sounddevice, pystray, Pillow)...
echo  Pode levar 1-2 minutos na primeira vez. Aguarde...
echo.
python -m pip install --upgrade pip --quiet
python -m pip install numpy sounddevice pystray Pillow

if errorlevel 1 (
    echo.
    echo  [ERRO] Falha ao instalar dependencias.
    echo  Tente executar este arquivo como Administrador.
    pause & exit /b 1
)
echo.
echo  [OK] Dependencias instaladas!
echo.

set DEST=%APPDATA%\OndaBinaural
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%~dp0main.py"         "%DEST%\main.py"         >nul
copy /Y "%~dp0database.py"     "%DEST%\database.py"     >nul
copy /Y "%~dp0audio_engine.py" "%DEST%\audio_engine.py" >nul
copy /Y "%~dp0profiles.py"     "%DEST%\profiles.py"     >nul
echo  [OK] Arquivos copiados para %DEST%

set LNK=%USERPROFILE%\Desktop\Ondas Binaurais.lnk
set VBS=%TEMP%\ob_atalho.vbs
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS%"
echo Set oLink = oWS.CreateShortcut("%LNK%") >> "%VBS%"
echo oLink.TargetPath = "pythonw.exe" >> "%VBS%"
echo oLink.Arguments = """%DEST%\main.py""" >> "%VBS%"
echo oLink.WorkingDirectory = "%DEST%" >> "%VBS%"
echo oLink.Description = "Ondas Binaurais" >> "%VBS%"
echo oLink.Save >> "%VBS%"
cscript //nologo "%VBS%" & del "%VBS%"
echo  [OK] Atalho criado na area de trabalho!

echo.
echo  ================================================
echo    Pronto! Abrindo o app...
echo.
echo    - Seus dados ficam salvos em:
echo      %USERPROFILE%\.ondabinaural\data.db
echo    - Fechar a janela NAO fecha o app (vai pra bandeja)
echo    - Use fones de ouvido!
echo  ================================================
echo.
start "" pythonw "%DEST%\main.py"
timeout /t 4 >nul
