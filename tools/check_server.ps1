# Проверка сервера: почему 1С иногда подвисает. ТОЛЬКО ЧТЕНИЕ - ничего не меняет.
# Запуск: PowerShell от имени администратора ->  powershell -ExecutionPolicy Bypass -File F:\Diag\check_server.ps1
# Результат: на экране и в файле F:\Diag\check_<дата>.txt (пришлите его).

$ErrorActionPreference = 'Continue'
$out = 'F:\Diag'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$file = Join-Path $out ("check_{0:yyyy-MM-dd_HH-mm}.txt" -f (Get-Date))
$lines = New-Object System.Collections.Generic.List[string]
function W($t) { $lines.Add([string]$t); Write-Host $t }
function Section($t) { W ''; W ('== ' + $t) }
function Check($title, [scriptblock]$b) {
    Section $title
    try { & $b | ForEach-Object { W $_ } } catch { W ('ОШИБКА: ' + $_.Exception.Message) }
}

W ("Проверка сервера {0}, {1:dd.MM.yyyy HH:mm}" -f $env:COMPUTERNAME, (Get-Date))
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { W 'ВНИМАНИЕ: запущено не от администратора - часть проверок (антивирус) не прочитается' }

Check '1. Система и время работы' {
    $os = Get-CimInstance Win32_OperatingSystem
    "Windows: $($os.Caption) $($os.Version)"
    "Включён с: {0:dd.MM.yyyy HH:mm} (дней без перезагрузки: {1:N0})" -f $os.LastBootUpTime, ((Get-Date) - $os.LastBootUpTime).TotalDays
    "Память: всего {0:N1} ГБ, свободно {1:N1} ГБ" -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB)
}

Check '2. Процессор и схема питания' {
    Get-CimInstance Win32_Processor | ForEach-Object {
        "$($_.Name.Trim()) | ядер: $($_.NumberOfCores), потоков: $($_.NumberOfLogicalProcessors) | частота сейчас: $($_.CurrentClockSpeed) МГц, макс: $($_.MaxClockSpeed) МГц"
    }
    "Активная схема питания: " + ((powercfg /getactivescheme) -join ' ')
}

Check '3. Диски' {
    Get-PhysicalDisk | Sort-Object DeviceId | ForEach-Object {
        "Диск $($_.DeviceId): $($_.FriendlyName) | $($_.MediaType) | шина: $($_.BusType) | {0:N0} ГБ | состояние: $($_.HealthStatus)" -f ($_.Size/1GB)
    }
    Get-Volume | Where-Object DriveLetter | Sort-Object DriveLetter | ForEach-Object {
        "Том $($_.DriveLetter): {0:N0} ГБ, свободно {1:N0} ГБ ({2:N0}%) | $($_.FileSystem) | $($_.HealthStatus)" -f ($_.Size/1GB), ($_.SizeRemaining/1GB), (100*$_.SizeRemaining/[math]::Max($_.Size,1))
    }
    'Контроллеры дисков (RAID):'
    Get-CimInstance Win32_SCSIController | ForEach-Object { "  $($_.Name) | драйвер: $($_.DriverName) | $($_.Status)" }
}

Check '4. Ошибки дисков и файловой системы в журнале Windows за 14 дней' {
    $from = (Get-Date).AddDays(-14)
    $ev = Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=$from} -ErrorAction SilentlyContinue |
        Where-Object { $_.ProviderName -match 'disk|stor|iaStor|nvme|Ntfs|volmgr|volsnap|RAID|rst' }
    if (-not $ev) { 'ошибок нет' }
    else { $ev | Group-Object ProviderName, Id | Sort-Object Count -Descending | ForEach-Object {
        $last = ($_.Group | Sort-Object TimeCreated -Descending | Select-Object -First 1)
        "{0} раз | {1} | последний {2:dd.MM HH:mm} | {3}" -f $_.Count, $_.Name, $last.TimeCreated, ($last.Message -split "`n")[0] } }
}

Check '5. Антивирус Windows (Defender): исключения для 1С' {
    $s = Get-MpComputerStatus
    "Защита в реальном времени: $($s.RealTimeProtectionEnabled) | антивирус включён: $($s.AntivirusEnabled)"
    $p = Get-MpPreference
    "Исключены папки: " + ($(if ($p.ExclusionPath) { $p.ExclusionPath -join '; ' } else { '(нет)' }))
    "Исключены процессы: " + ($(if ($p.ExclusionProcess) { $p.ExclusionProcess -join '; ' } else { '(нет)' }))
    "Исключены расширения: " + ($(if ($p.ExclusionExtension) { $p.ExclusionExtension -join '; ' } else { '(нет)' }))
    'Другие антивирусы:'
    Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction SilentlyContinue | ForEach-Object { "  $($_.displayName)" }
}

Check '6. Индексация поиска Windows' {
    $svc = Get-Service WSearch -ErrorAction SilentlyContinue
    if ($svc) { "Служба поиска (WSearch): $($svc.Status), запуск: $($svc.StartType)" } else { 'служба поиска не установлена' }
}

Check '7. Базы 1С: размер файла базы и журнала регистрации' {
    Get-ChildItem 'C:\1C' -Recurse -Depth 3 -Filter '1Cv8.1CD' -ErrorAction SilentlyContinue | ForEach-Object {
        $dir = $_.DirectoryName
        $log = Join-Path $dir '1Cv8Log'
        $logSize = if (Test-Path $log) { (Get-ChildItem $log -Recurse -File | Measure-Object Length -Sum).Sum } else { 0 }
        "{0} | база: {1:N2} ГБ | журнал регистрации: {2:N2} ГБ | изменена: {3:dd.MM HH:mm}" -f $dir, ($_.Length/1GB), ($logSize/1GB), $_.LastWriteTime
    }
}

Check '8. Платформа 1С на сервере' {
    Get-ChildItem 'C:\Program Files\1cv8', 'C:\Program Files (x86)\1cv8' -Directory -ErrorAction SilentlyContinue |
        Where-Object Name -match '^\d+\.\d+' | ForEach-Object { $_.FullName }
    $conf = 'C:\Program Files\1cv8\conf\logcfg.xml'
    "Технологический журнал (logcfg.xml): " + $(if (Test-Path $conf) { 'настроен' } else { 'не настроен' })
}

Check '9. Сеансы и процессы 1С сейчас' {
    $q = (quser 2>$null)
    "Сеансов на сервере: " + [math]::Max(0, ($q | Measure-Object).Count - 1)
    $p = Get-Process 1cv8* -ErrorAction SilentlyContinue
    "Процессов 1С: {0}, память всего: {1:N1} ГБ" -f ($p | Measure-Object).Count, (($p | Measure-Object WorkingSet64 -Sum).Sum/1GB)
}

Check '10. Самые прожорливые процессы (память и процессорное время с запуска)' {
    'по памяти:'
    Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 | ForEach-Object { "  {0,-28} {1,8:N0} МБ" -f $_.ProcessName, ($_.WorkingSet64/1MB) }
    'по процессорному времени:'
    Get-Process | Where-Object CPU | Sort-Object CPU -Descending | Select-Object -First 10 | ForEach-Object { "  {0,-28} {1,10:N0} с" -f $_.ProcessName, $_.CPU }
}

Check '11. Задания планировщика, которые работают днём (08:00-20:00)' {
    Get-ScheduledTask | Where-Object State -ne 'Disabled' | ForEach-Object {
        $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
        if ($i -and $i.NextRunTime -and $i.NextRunTime.Hour -ge 8 -and $i.NextRunTime.Hour -lt 20 -and $_.TaskPath -notlike '\Microsoft\*') {
            "  {0}{1} | следующий запуск {2:dd.MM HH:mm}" -f $_.TaskPath, $_.TaskName, $i.NextRunTime }
    }
}

$lines | Set-Content -Path $file -Encoding UTF8
W ''
W "Готово. Файл: $file"
