# Витрина ACMineevo на сервере: три задачи Windows и правило брандмауэра.
#
#   1) «ACMineevo - витрина, сбор»  - каждый день в 06:00 читает Далион (только чтение) в файл витрины;
#   2) «ACMineevo - витрина, днём»  - в 10, 13, 16 и 19 часов дочитывает свежие звонки, ЗН, задачи
#      и новые карточки (минута-две, только чтение); -Updates "" - не создавать;
#   3) «ACMineevo - витрина, сайт»  - при включении компьютера запускает сайт (без окна);
#   4) брандмауэр: порт сайта открыт только для локальной сети офиса и Tailscale (100.64.0.0/10).
#
# Запускать в PowerShell ОТ АДМИНИСТРАТОРА, но указывать пользователя, под которым
# программа настроена (в окне ACMineevo-Vitrina.exe заданы подключение к 1С и пароли сайта):
#
#   powershell -ExecutionPolicy Bypass -File setup_vitrina.ps1 -Exe "C:\avtootchet\ACMineevo-Vitrina.exe" -User roma
#
# Параметры: -Time "06:00" (время сбора), -Port 8765 (как в настройках программы).
# Пароль Windows этого пользователя спросит окно входа (один раз): он нужен, чтобы задачи работали без входа в систему.
# Убрать всё: schtasks /Delete /TN "ACMineevo - витрина, сбор" /F; schtasks /Delete /TN "ACMineevo - витрина, днём" /F;
#             schtasks /Delete /TN "ACMineevo - витрина, сайт" /F;
#             Remove-NetFirewallRule -DisplayName "ACMineevo - витрина"
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$Time = "06:00",
    [string]$Updates = "10:00,13:00,16:00,19:00",
    [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Exe)) { throw "Не найден файл программы: $Exe" }
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Запустите PowerShell от администратора (нужно для задачи «при включении» и брандмауэра)."
}
$ru = if ($User -match '\\') { $User } else { "$env:COMPUTERNAME\$User" }

# пароль спрашивается один раз в окне входа Windows и в консоль не попадает
$cred = Get-Credential -UserName $ru -Message "Пароль Windows пользователя $ru (чтобы задачи работали без входа в систему)"
$pw = $cred.GetNetworkCredential().Password

$collect = New-ScheduledTaskAction -Execute $Exe -Argument "--collect"
Register-ScheduledTask -TaskName "ACMineevo - витрина, сбор" -Action $collect -Trigger (New-ScheduledTaskTrigger -Daily -At $Time) `
    -User $ru -Password $pw -Force | Out-Null

# дневные обновления: несколько ежедневных триггеров одной задачи; не дольше 30 минут, без наложения
$day = "ACMineevo - витрина, днём"
if ($Updates.Trim()) {
    $triggers = @($Updates -split "[,; ]+" | Where-Object { $_ } | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
    $daySettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $day -Action (New-ScheduledTaskAction -Execute $Exe -Argument "--collect-today") `
        -Trigger $triggers -Settings $daySettings -User $ru -Password $pw -Force | Out-Null
} else {
    Unregister-ScheduledTask -TaskName $day -Confirm:$false -ErrorAction SilentlyContinue
}

# сайт: при включении (через 2 минуты), без ограничения 72 часа, при сбое - перезапуск
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT2M"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "ACMineevo - витрина, сайт" -Action (New-ScheduledTaskAction -Execute $Exe -Argument "--serve") `
    -Trigger $trigger -Settings $settings -User $ru -Password $pw -Force | Out-Null
$pw = $null

Get-NetFirewallRule -DisplayName "ACMineevo - витрина" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "ACMineevo - витрина" -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress LocalSubnet, "100.64.0.0/10" -Action Allow -Profile Any | Out-Null

schtasks /Run /TN "ACMineevo - витрина, сайт" | Out-Null
Write-Output ""
Write-Output "Готово. Сбор - каждый день в $Time, обновления днём - $Updates; сайт запущен и будет запускаться при включении."
Write-Output "Проверить: откройте http://localhost:$Port на сервере, с телефона - http://100.112.23.72:$Port (Tailscale)."
Write-Output "Собрать данные сразу, не дожидаясь утра:  schtasks /Run /TN `"ACMineevo - витрина, сбор`""
Write-Output "Журнал: %APPDATA%\AvtoOtchet\journal_vitrina.log у пользователя $ru"
