@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Создаю окружение Python...
    py -3 -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :error

echo.
echo === Сборка программы владельца ===
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
    --name AvtoOtchet-Vladelec --hidden-import win32timezone --add-data "demo\export.json;demo" owner_main.py || goto :error

echo.
echo === Сборка программы бухгалтера ===
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
    --name AvtoOtchet-Buhgalter accountant_main.py || goto :error

echo.
echo Готово! Программы лежат в папке dist:
echo   AvtoOtchet-Vladelec.exe  - на ПК владельца
echo   AvtoOtchet-Buhgalter.exe - на ПК бухгалтера
pause
exit /b 0

:error
echo.
echo Сборка не удалась - смотрите сообщения выше.
pause
exit /b 1
