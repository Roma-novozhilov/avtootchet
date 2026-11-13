# Право записи в общую папку настроек клиента 1С для всех пользователей Windows.
# Без него COM-подключение (АвтоОтчёт) у пользователя без прав администратора тысячи раз в секунду
# пытается переписать C:\ProgramData\1C\1cv8\1cv8conn.pfl и получает «Отказано в доступе».
# Запускать от имени администратора. Ничего в базах 1С не меняет.
#Requires -RunAsAdministrator
$dir = 'C:\ProgramData\1C\1cv8'
if (-not (Test-Path $dir)) { Write-Host "Нет папки $dir"; exit 1 }
# *S-1-5-32-545 - группа «Пользователи» (на русской и английской Windows одинаково)
icacls $dir /grant '*S-1-5-32-545:(OI)(CI)M'
Get-ChildItem $dir -Filter '1cv8conn*.pfl' | ForEach-Object { icacls $_.FullName /grant '*S-1-5-32-545:M' }
Write-Host "Готово: пользователи могут записывать настройки клиента 1С в $dir"
