# Витрина ACMineevo на сервере: две задачи Windows и правило брандмауэра.
#
#   1) «ACMineevo - витрина, сбор»  - каждый день в 06:00 читает Далион (только чтение) в файл витрины;
#   2) «ACMineevo - витрина, сайт»  - при включении компьютера запускает сайт (без окна);
#   3) брандмауэр: порт сайта открыт только для локальной сети офиса и Tailscale (100.64.0.0/10).
#
# Запускать в PowerShell ОТ АДМИНИСТРАТОРА, но указывать пользователя, под которым
# программа настроена (в окне ACMineevo-Vitrina.exe заданы подключение к 1С и пароли сайта):
#
#   powershell -ExecutionPolicy Bypass -File setup_vitrina.ps1 -Exe "C:\avtootchet\ACMineevo-Vitrina.exe" -User roma
#
# Параметры: -Time "06:00" (время сбора), -Port 8765 (как в настройках программы).
# Пароль Windows этого пользователя спросит schtasks: он нужен, чтобы задачи работали без входа в систему.
# Убрать всё: schtasks /Delete /TN "ACMineevo - витрина, сбор" /F; schtasks /Delete /TN "ACMineevo - витрина, сайт" /F;
#             Remove-NetFirewallRule -DisplayName "ACMineevo - витрина"
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$Time = "06:00",
    [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Exe)) { throw "Не найден файл программы: $Exe" }
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Запустите PowerShell от администратора (нужно для задачи «при включении» и брандмауэра)."
}
$ru = if ($User -match '\\') { $User } else { "$env:COMPUTERNAME\$User" }

Write-Output "Задачи будут работать от пользователя $ru. Введите его пароль Windows (два раза - для каждой задачи):"
schtasks /Create /TN "ACMineevo - витрина, сбор" /TR "`"$Exe`" --collect" /SC DAILY /ST $Time /RU $ru /RP * /F
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать задачу сбора (код $LASTEXITCODE)" }
schtasks /Create /TN "ACMineevo - витрина, сайт" /TR "`"$Exe`" --serve" /SC ONSTART /DELAY 0002:00 /RU $ru /RP * /F
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать задачу сайта (код $LASTEXITCODE)" }

# сайт не должен останавливаться через 72 часа (так Планировщик делает по умолчанию)
$t = Get-ScheduledTask -TaskName "ACMineevo - витрина, сайт"
$t.Settings.ExecutionTimeLimit = "PT0S"
$t.Settings.RestartCount = 3
$t.Settings.RestartInterval = "PT5M"
Set-ScheduledTask -InputObject $t | Out-Null

Get-NetFirewallRule -DisplayName "ACMineevo - витрина" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "ACMineevo - витрина" -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress LocalSubnet, "100.64.0.0/10" -Action Allow -Profile Any | Out-Null

schtasks /Run /TN "ACMineevo - витрина, сайт" | Out-Null
Write-Output ""
Write-Output "Готово. Сбор - каждый день в $Time, сайт запущен и будет запускаться при включении."
Write-Output "Проверить: откройте http://localhost:$Port на сервере, с телефона - http://100.112.23.72:$Port (Tailscale)."
Write-Output "Собрать данные сразу, не дожидаясь утра:  schtasks /Run /TN `"ACMineevo - витрина, сбор`""
Write-Output "Журнал: %APPDATA%\AvtoOtchet\journal_vitrina.log у пользователя $ru"
