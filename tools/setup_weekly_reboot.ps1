# Плановая перезагрузка сервера раз в неделю (по умолчанию воскресенье 05:00).
# Зачем: не копятся зависшие сеансы 1С, сжатая память и утечки драйверов.
#
# Включить:   powershell -ExecutionPolicy Bypass -File F:\Diag\setup_weekly_reboot.ps1
#             другой день/время: -Day Saturday -Time "04:30"
# Выключить:  powershell -ExecutionPolicy Bypass -File F:\Diag\setup_weekly_reboot.ps1 -Remove
# Проверить, что будет сделано, не перезагружая:  ... -Test
#
# Перед перезагрузкой задача:
#   1) ждёт, пока закончится резервная копия (backup.ps1 / 7-Zip), но не дольше 2 часов -
#      если копия так и не закончилась, перезагрузка в этот раз ОТМЕНЯЕТСЯ;
#   2) предупреждает всех вошедших пользователей и ждёт 10 минут;
#   3) перезагружает. Всё пишется в F:\Diag\reboot.log.
#Requires -RunAsAdministrator
param(
    [ValidateSet('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')]
    [string]$Day = 'Sunday',
    [string]$Time = '05:00',
    [switch]$Remove,
    [switch]$Test
)
$ErrorActionPreference = 'Stop'
$TaskName = 'АвтоОтчёт - перезагрузка раз в неделю'
$Dir = 'C:\avtootchet\power'
$Worker = Join-Path $Dir 'weekly_reboot.ps1'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $Worker -ErrorAction SilentlyContinue
    Write-Host 'Плановая перезагрузка выключена.' -ForegroundColor Green
    return
}

New-Item -ItemType Directory -Force -Path $Dir, 'F:\Diag' | Out-Null
$body = @'
param([switch]$Test)
$log = 'F:\Diag\reboot.log'
function Log($t) { $l = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $t"; Add-Content $log $l -Encoding UTF8; Write-Output $l }
function BackupRunning {
    if (Get-Process 7z -ErrorAction SilentlyContinue) { return $true }
    $ps = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue
    return [bool]($ps | Where-Object { $_.CommandLine -match 'backup\.ps1' -and $_.CommandLine -notmatch '-Check' })
}
Log 'Плановая перезагрузка: начало'
$deadline = (Get-Date).AddHours(2)
while (BackupRunning) {
    if ((Get-Date) -gt $deadline) { Log 'Резервная копия идёт больше 2 часов - перезагрузка ОТМЕНЕНА'; exit 1 }
    Log 'Идёт резервная копия - жду 5 минут'
    if ($Test) { break }
    Start-Sleep -Seconds 300
}
$c1 = @(Get-Process 1cv8, 1cv8c -ErrorAction SilentlyContinue).Count
Log "Открыто процессов 1С: $c1. Предупреждаю пользователей"
if ($Test) { Log 'ПРОВЕРКА: дальше было бы предупреждение, 10 минут ожидания и перезагрузка'; exit 0 }
msg * /TIME:600 "Сервер перезагрузится через 10 минут (плановое обслуживание). Сохраните работу и выйдите из 1С." 2>$null
Start-Sleep -Seconds 600
Log 'Перезагрузка'
Restart-Computer -Force
'@
Set-Content -Path $Worker -Value $body -Encoding UTF8

if ($Test) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Worker -Test
    return
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Worker`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable:$false -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$next = (Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo).NextRunTime
Write-Host "Готово: перезагрузка каждую неделю, $Day в $Time. Ближайшая: $next" -ForegroundColor Green
Write-Host 'Журнал: F:\Diag\reboot.log. Выключить: тот же скрипт с -Remove.'
