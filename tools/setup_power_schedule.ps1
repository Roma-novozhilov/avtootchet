# Питание сервера по расписанию: с 08:50 до 19:10 - «Высокая производительность»,
# остальное время - «Сбалансированная». Плюс при включении сервера выбирается нужная схема.
# Запуск: PowerShell от имени администратора ->
#     powershell -ExecutionPolicy Bypass -File F:\Diag\setup_power_schedule.ps1
# Отменить: schtasks /Delete /TN "АвтоОтчёт - питание день" /F  (и «ночь», «при включении»),
#           затем powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e

$ErrorActionPreference = 'Stop'
$balanced = '381b4222-f694-41f0-9685-ff5bb260df2e'
$highStd  = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'

# 1. Схема «Высокая производительность»: в Windows 11 она бывает скрыта - тогда создаём копию
$list = powercfg /list | Out-String
if ($list -match $highStd) {
    $high = $highStd
} elseif ($list -match '([0-9a-f-]{36})\s+\((Высокая производительность|High performance)\)') {
    $high = $Matches[1]
} else {
    $res = powercfg -duplicatescheme $highStd | Out-String
    if ($res -notmatch '([0-9a-f]{8}-[0-9a-f-]{27})') { throw "Не удалось создать схему «Высокая производительность»: $res" }
    $high = $Matches[1]
    Write-Host "Создана схема «Высокая производительность»: $high"
}
Write-Host "Дневная схема: $high"
Write-Host "Ночная схема:  $balanced"

# 2. Скрипт выбора схемы по времени (нужен при включении сервера)
$dir = 'C:\avtootchet\power'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$pick = Join-Path $dir 'power_by_time.ps1'
@"
# 09:00-19:00 - высокая производительность, иначе сбалансированная
`$h = (Get-Date).Hour
if (`$h -ge 9 -and `$h -lt 19) { powercfg /setactive $high } else { powercfg /setactive $balanced }
"@ | Set-Content -Path $pick -Encoding UTF8

# 3. Задания планировщика (от SYSTEM, пароль не нужен)
schtasks /Create /TN "АвтоОтчёт - питание день"         /TR "powercfg /setactive $high"     /SC DAILY /ST 08:50 /RU SYSTEM /RL HIGHEST /F | Out-Host
schtasks /Create /TN "АвтоОтчёт - питание ночь"         /TR "powercfg /setactive $balanced" /SC DAILY /ST 19:10 /RU SYSTEM /RL HIGHEST /F | Out-Host
schtasks /Create /TN "АвтоОтчёт - питание при включении" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File $pick" /SC ONSTART        /RU SYSTEM /RL HIGHEST /F | Out-Host

# 4. Сразу включаем нужную схему
& powershell -NoProfile -ExecutionPolicy Bypass -File $pick
Write-Host ''
Write-Host ('Сейчас активна: ' + (powercfg /getactivescheme)) -ForegroundColor Green
