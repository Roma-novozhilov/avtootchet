# Утренний отчёт «Залежавшиеся запчасти»: задача Windows на каждый день + ярлык на папку.
#
# Запускать в PowerShell ОТ ТОГО ПОЛЬЗОВАТЕЛЯ WINDOWS, под которым настроена программа
# АвтоОтчёт (у него сохранены подключение к 1С и пароль для отчётов):
#
#   powershell -ExecutionPolicy Bypass -File setup_stock_task.ps1 -Exe "C:\...\ACMineevo-Vladelec.exe"
#
# Параметры: -Time "07:30" (время запуска; до прихода сотрудников - в 9:00 отчёт мешал работе в Далионе), -ShortcutFor "Администратор" (на чьём рабочем столе ярлык).
# Пароль Windows спросит сама команда schtasks: он нужен, чтобы отчёт строился, даже когда
# никто не вошёл в систему. В скрипте и в чате пароль не хранится.
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Time = "07:30",
    [string]$ShortcutFor = "Администратор"
)
$ErrorActionPreference = "Stop"
$TaskName = "АвтоОтчёт - залежавшиеся запчасти"

if (-not (Test-Path -LiteralPath $Exe)) { throw "Не найден файл программы: $Exe" }

# папка отчёта - из настроек программы этого пользователя
$folder = "F:\Отчёты\Склад"
$cfg = Join-Path $env:APPDATA "AvtoOtchet\owner_settings.json"
if (Test-Path -LiteralPath $cfg) {
    $s = Get-Content -LiteralPath $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($s.stock -and $s.stock.folder) { $folder = $s.stock.folder }
}
New-Item -ItemType Directory -Path $folder -Force | Out-Null
Write-Output "Папка отчёта: $folder"

# задача: каждый день в $Time, от этого пользователя, даже если он не вошёл
$user = "$env:COMPUTERNAME\$env:USERNAME"
Write-Output "Задача будет запускаться от пользователя $user. Введите его пароль Windows:"
schtasks /Create /TN $TaskName /TR "`"$Exe`" --stock-report" /SC DAILY /ST $Time /RU $user /RP * /F
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать задачу (код $LASTEXITCODE)" }

# ярлык на папку на рабочем столе
$desk = "C:\Users\$ShortcutFor\Desktop"
if (Test-Path -LiteralPath $desk) {
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk "Склад - залежавшиеся.lnk"))
    $lnk.TargetPath = $folder
    $lnk.Save()
    Write-Output "Ярлык создан: $desk\Склад - залежавшиеся.lnk"
} else {
    Write-Output "Рабочий стол $desk не найден - ярлык не создан"
}
Write-Output ""
Write-Output "Готово. Проверить прямо сейчас:  schtasks /Run /TN `"$TaskName`""
Write-Output "Через минуту-две в папке появится отчёт (или файл с текстом ошибки)."
