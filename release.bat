@echo off
chcp 65001 >nul
rem Выпустить новую версию:  release.bat 0.2.0 "Что нового в этой версии"
cd /d "%~dp0"
if "%~1"=="" (
    echo Использование: release.bat 0.2.0 "Что нового"
    exit /b 1
)
if "%~2"=="" (
    echo Напишите, что нового, вторым параметром в кавычках.
    exit /b 1
)
git diff --quiet && git diff --cached --quiet || (
    echo Есть незакоммиченные изменения. Сначала: git add . ^&^& git commit -m "..."
    exit /b 1
)
git push || exit /b 1
git tag -a "v%~1" -m "%~2" || exit /b 1
git push origin "v%~1" || exit /b 1
echo.
echo Тег v%~1 отправлен. Сборка идёт на GitHub: вкладка Actions репозитория.
echo Через 5-10 минут версия появится в Releases, программы предложат обновиться.
