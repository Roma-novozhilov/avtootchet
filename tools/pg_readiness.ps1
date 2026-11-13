# Готовность сервера к переходу 1С на клиент-серверный режим (сервер 1С + PostgreSQL).
# ТОЛЬКО ЧИТАЕТ: ничего не устанавливает, не меняет и не открывает базы 1С.
# Пароли, ключи и содержимое файлов лицензий не выводятся - только их количество и имена.
#
# Запуск (PowerShell от имени администратора):
#   powershell -ExecutionPolicy Bypass -File F:\Diag\pg_readiness.ps1
# Результат: F:\Diag\pg_readiness.txt - его и прислать.
$ErrorActionPreference = 'Continue'
$out = 'F:\Diag\pg_readiness.txt'
$lines = New-Object System.Collections.Generic.List[string]
function Add([string]$s) { $lines.Add($s) }
function Section([string]$t) { Add ''; Add "=== $t ===" }
function Check([string]$name, [scriptblock]$b) {
    try { & $b } catch { Add "  (${name}: не удалось - $($_.Exception.Message))" }
}

Add "Проверка готовности к PostgreSQL, $(Get-Date -Format 'dd.MM.yyyy HH:mm'), компьютер $env:COMPUTERNAME"

Section 'Windows'
Check 'ОС' {
    $os = Get-CimInstance Win32_OperatingSystem
    Add "  $($os.Caption), версия $($os.Version), сборка $($os.BuildNumber), $($os.OSArchitecture)"
    Add ("  Включён: {0:dd.MM.yyyy HH:mm}" -f $os.LastBootUpTime)
}

Section 'Процессор и память'
Check 'CPU' {
    Get-CimInstance Win32_Processor | ForEach-Object {
        Add "  $($_.Name.Trim()): ядер $($_.NumberOfCores), потоков $($_.NumberOfLogicalProcessors)"
    }
    $cs = Get-CimInstance Win32_ComputerSystem
    $os = Get-CimInstance Win32_OperatingSystem
    Add ("  Память: всего {0:N1} ГБ, свободно {1:N1} ГБ" -f ($cs.TotalPhysicalMemory/1GB), ($os.FreePhysicalMemory*1KB/1GB))
}

Section 'Диски'
Check 'физические диски' {
    Get-PhysicalDisk | ForEach-Object {
        Add ("  {0}: {1}, {2}, {3:N0} ГБ" -f $_.FriendlyName, $_.MediaType, $_.BusType, ($_.Size/1GB))
    }
}
Check 'тома' {
    Get-Volume | Where-Object DriveLetter | ForEach-Object {
        Add ("  {0}: {1}, всего {2:N0} ГБ, свободно {3:N0} ГБ" -f $_.DriveLetter, $_.FileSystem, ($_.Size/1GB), ($_.SizeRemaining/1GB))
    }
}

Section '1С: установленные версии и службы'
Check 'версии' {
    foreach ($root in 'C:\Program Files\1cv8', 'C:\Program Files (x86)\1cv8') {
        if (Test-Path $root) {
            $v = Get-ChildItem $root -Directory | Where-Object Name -match '^\d+\.\d+\.\d+\.\d+$' | ForEach-Object Name
            Add "  $root : $($v -join ', ')"
            foreach ($ver in $v) {
                $parts = @()
                if (Test-Path "$root\$ver\bin\ragent.exe") { $parts += 'сервер 1С (ragent)' }
                if (Test-Path "$root\$ver\bin\comcntr.dll") { $parts += 'COM-соединитель' }
                if ($parts) { Add "    $ver : $($parts -join ', ')" }
            }
        }
    }
}
Check 'службы' {
    $s = Get-Service | Where-Object { $_.Name -match '1C|postgres|pgsql|hasp|MSSQL' -or $_.DisplayName -match '1C|PostgreSQL|HASP|SQL Server' }
    if ($s) { $s | ForEach-Object { Add "  $($_.DisplayName) [$($_.Name)]: $($_.Status), $($_.StartType)" } }
    else { Add '  служб сервера 1С / PostgreSQL / HASP / SQL нет' }
}

Section '1С: лицензии (только количество и имена файлов)'
Check 'лицензии' {
    foreach ($d in 'C:\ProgramData\1C\licenses', "$env:APPDATA\1C\1cv8\conf", 'C:\Program Files\1cv8\conf') {
        if (Test-Path $d) {
            $f = Get-ChildItem $d -Filter *.lic -ErrorAction SilentlyContinue
            Add "  $d : файлов .lic - $($f.Count)$(if ($f) { ' (' + (($f | ForEach-Object Name) -join ', ') + ')' })"
        }
    }
    $hasp = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object FriendlyName -match 'HASP|Sentinel|Aladdin'
    Add "  USB-ключи HASP: $(if ($hasp) { ($hasp | ForEach-Object FriendlyName) -join ', ' } else { 'не найдены' })"
}

Section 'Информационные базы (из списков баз пользователей) и их размер'
Check 'базы' {
    $seen = @{}
    Get-ChildItem 'C:\Users\*\AppData\Roaming\1C\1CEStart\ibases.v8i' -ErrorAction SilentlyContinue | ForEach-Object {
        $name = ''
        foreach ($l in Get-Content $_.FullName -Encoding UTF8) {
            if ($l -match '^\[(.+)\]$') { $name = $Matches[1] }
            elseif ($l -match '^Connect=File="([^"]+)"') {
                $p = $Matches[1].TrimEnd('\')
                if (-not $seen.ContainsKey($p)) {
                    $seen[$p] = $true
                    $db = Join-Path $p '1Cv8.1CD'
                    $sz = if (Test-Path $db) { '{0:N1} ГБ' -f ((Get-Item $db).Length/1GB) } else { 'файла нет' }
                    $logDir = Join-Path $p '1Cv8Log'
                    $lg = if (Test-Path $logDir) { '{0:N1} ГБ' -f ((Get-ChildItem $logDir -Recurse -File | Measure-Object Length -Sum).Sum/1GB) } else { '-' }
                    Add "  [$name] $p : база $sz, журнал регистрации $lg"
                }
            }
            elseif ($l -match '^Connect=Srvr=') { Add "  [$name] серверная база: $l" }
        }
    }
    if (-not $seen.Count) { Add '  списков баз не найдено' }
}

Section 'Пользователи Windows и сеансы'
Check 'пользователи' {
    $u = Get-LocalUser | Where-Object Enabled
    Add "  Включённых локальных пользователей: $($u.Count)"
    $q = (quser 2>$null)
    if ($q) { Add "  Сейчас вошли (quser):"; $q | ForEach-Object { Add "    $_" } }
    $rdp = Get-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -ErrorAction SilentlyContinue
    Add "  Удалённый рабочий стол: $(if ($rdp.fDenyTSConnections -eq 0) { 'включён' } else { 'выключен' })"
    if (Test-Path 'C:\Program Files\RDP Wrapper') { Add '  Найден RDP Wrapper (несколько RDP-сеансов на Windows Pro)' }
}
Check 'процессы 1С' {
    $p = Get-Process 1cv8*, AvtoOtchet* -ErrorAction SilentlyContinue
    Add "  Сейчас запущено процессов 1С и АвтоОтчёта: $($p.Count)"
    $p | Group-Object ProcessName | ForEach-Object { Add "    $($_.Name): $($_.Count)" }
}

Section 'Антивирус (проверка файлов базы на лету тормозит файловую 1С)'
Check 'Defender' {
    $st = Get-MpComputerStatus
    Add "  Защитник Windows: защита в реальном времени $(if ($st.RealTimeProtectionEnabled) { 'ВКЛ' } else { 'выкл' })"
    $pr = Get-MpPreference
    Add "  Исключения (папки): $(if ($pr.ExclusionPath) { $pr.ExclusionPath -join '; ' } else { 'нет' })"
    Add "  Исключения (процессы): $(if ($pr.ExclusionProcess) { $pr.ExclusionProcess -join '; ' } else { 'нет' })"
}
Check 'другие антивирусы' {
    $av = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction SilentlyContinue
    if ($av) { $av | ForEach-Object { Add "  $($_.displayName)" } }
}

Section 'Сеть и порты (для сервера 1С нужны 1540-1541, 1560-1591; PostgreSQL - 5432)'
Check 'порты' {
    $busy = Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 1540,1541,5432 -or ($_.LocalPort -ge 1560 -and $_.LocalPort -le 1591) }
    if ($busy) { $busy | ForEach-Object { Add "  занят порт $($_.LocalPort) (процесс $((Get-Process -Id $_.OwningProcess).ProcessName))" } }
    else { Add '  порты свободны' }
}

$lines | Set-Content -Path $out -Encoding UTF8
Write-Host "Готово: $out - пришлите этот файл." -ForegroundColor Green
