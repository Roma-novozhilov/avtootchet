# Технологический журнал 1С: какие операции в 1С идут дольше 1 секунды и где именно (модуль, процедура).
# Это журнал самой платформы 1С - базы не меняются. Пишется только то, что дольше 1 с,
# хранится 48 часов (старое удаляется само), папка F:\Diag\techlog.
#
# Включить:   powershell -ExecutionPolicy Bypass -File F:\Diag\setup_techlog.ps1
# Выключить:  powershell -ExecutionPolicy Bypass -File F:\Diag\setup_techlog.ps1 -Remove
# Новые настройки 1С подхватывает сама примерно через минуту, перезапускать 1С не нужно.
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$confDirs = @('C:\Program Files\1cv8\conf', 'C:\Program Files (x86)\1cv8\conf') | Where-Object { Test-Path (Split-Path $_) }
$logDir = 'F:\Diag\techlog'

if ($Remove) {
    foreach ($d in $confDirs) {
        $f = Join-Path $d 'logcfg.xml'
        if (Test-Path $f) { Remove-Item $f; Write-Host "Удалено: $f" }
        if (Test-Path "$f.bak") { Move-Item "$f.bak" $f; Write-Host "Возвращён прежний: $f" }
    }
    Write-Host 'Технологический журнал выключен. Папку F:\Diag\techlog можно удалить вручную.' -ForegroundColor Green
    return
}

New-Item -ItemType Directory -Force -Path $logDir, 'F:\Diag\dumps' | Out-Null
# аварийные дампы 1С (их включала сама 1С для отчётов об ошибках) сохраняем - теперь в F:\Diag\dumps
# 1С у каждого пользователя пишет журнал от его имени - даём пользователям право записи в папку
icacls $logDir /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null
icacls 'F:\Diag\dumps' /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null

# длительность в микросекундах: 1000000 = 1 секунда
$xml = @"
<?xml version="1.0" encoding="UTF-8"?>
<config xmlns="http://v8.1c.ru/v8/tech-log">
  <dump location="F:\Diag\dumps" create="1" type="0" externaldump="1"/>
  <log location="$logDir" history="48">
    <event>
      <eq property="name" value="CALL"/>
      <ge property="duration" value="1000000"/>
    </event>
    <event>
      <eq property="name" value="DBV8DBENG"/>
      <ge property="duration" value="1000000"/>
    </event>
    <event>
      <eq property="name" value="EXCP"/>
    </event>
    <property name="all"/>
  </log>
</config>
"@
foreach ($d in $confDirs) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    $f = Join-Path $d 'logcfg.xml'
    if ((Test-Path $f) -and -not (Test-Path "$f.bak")) { Copy-Item $f "$f.bak"; Write-Host "Старый $f сохранён как logcfg.xml.bak" }
    [IO.File]::WriteAllText($f, $xml, (New-Object Text.UTF8Encoding($true)))
    Write-Host "Записано: $f"
}
Write-Host ''
Write-Host "Технологический журнал включён, пишет в $logDir (через минуту появятся папки rphost/1cv8_...)." -ForegroundColor Green
Write-Host 'Через день: заархивируйте папку F:\Diag\techlog в ZIP и пришлите вместе с примерным временем зависаний.'
