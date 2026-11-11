# Запись производительности сервера каждые 5 секунд. При каждом запуске (в т.ч. после
# перезагрузки сервера) - новый файл с датой и временем в имени, старые не затираются.
#, чтобы поймать зависания 1С.
# Встроенное средство Windows (logman), нагрузка ничтожная. Пишет по кругу: старое
# затирается, файл не больше 1 ГБ (это примерно 1-2 недели).
# Запуск: PowerShell от имени администратора ->
#     powershell -ExecutionPolicy Bypass -File F:\Diag\setup_perf_log.ps1
# Остановить и убрать:  logman stop AvtoOtchet-Perf; logman delete AvtoOtchet-Perf;
#     schtasks /Delete /TN "АвтоОтчёт - журнал производительности" /F

$ErrorActionPreference = 'Stop'
$name = 'AvtoOtchet-Perf'
$dir  = 'F:\Diag\perf'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# Счётчики называем по-английски и переводим на язык системы (в русской Windows имена русские)
$english = @(
    '\Processor Information(_Total)\% Processor Time',
    '\Processor Information(_Total)\% Processor Performance',
    '\Processor Information(_Total)\Processor Frequency',
    '\System\Processor Queue Length',
    '\Memory\Available MBytes',
    '\Memory\Pages/sec',
    '\PhysicalDisk(*)\% Idle Time',
    '\PhysicalDisk(*)\Avg. Disk sec/Read',
    '\PhysicalDisk(*)\Avg. Disk sec/Write',
    '\PhysicalDisk(*)\Current Disk Queue Length',
    '\PhysicalDisk(*)\Disk Bytes/sec',
    '\Network Interface(*)\Bytes Total/sec',
    '\Process(1cv8*)\% Processor Time',
    '\Process(1cv8*)\IO Data Bytes/sec',
    '\Process(1cv8*)\Working Set',
    '\Process(MsMpEng)\% Processor Time',
    '\Process(MsMpEng)\IO Data Bytes/sec',
    '\Process(SearchIndexer)\% Processor Time'
)

$perflib = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Perflib'
$en  = (Get-ItemProperty "$perflib\009").Counter
$loc = (Get-ItemProperty "$perflib\CurrentLanguage").Counter
$enIdx = @{}; for ($i = 0; $i -lt $en.Count - 1; $i += 2) { if (-not $enIdx.ContainsKey($en[$i+1])) { $enIdx[$en[$i+1]] = @() }; $enIdx[$en[$i+1]] += $en[$i] }
$locName = @{}; for ($i = 0; $i -lt $loc.Count - 1; $i += 2) { $locName[$loc[$i]] = $loc[$i+1] }
function L($n) { foreach ($idx in $enIdx[$n]) { if ($locName.ContainsKey($idx)) { return $locName[$idx] } }; return $n }

$paths = @()
foreach ($p in $english) {
    if ($p -notmatch '^\\([^\\(]+)(\(([^)]*)\))?\\(.+)$') { continue }
    $obj, $inst, $ctr = $Matches[1], $Matches[3], $Matches[4]
    $local = '\' + (L $obj) + $(if ($inst) { "($inst)" } else { '' }) + '\' + (L $ctr)
    # проверяем объект и счётчик (экземпляр берём «все», процесса может сейчас не быть)
    $test = '\' + (L $obj) + $(if ($inst) { '(*)' } else { '' }) + '\' + (L $ctr)
    try { Get-Counter -Counter $test -MaxSamples 1 -ErrorAction Stop | Out-Null; $paths += $local; Write-Host "ok   $local" }
    catch { Write-Host "нет  $p ($local) - пропускаю" -ForegroundColor Yellow }
}
if (-not $paths) { throw 'Ни один счётчик не найден' }

$cfg = Join-Path $dir 'counters.txt'
$paths | Set-Content -Path $cfg -Encoding Unicode

logman stop $name 2>$null | Out-Null
logman delete $name 2>$null | Out-Null
logman create counter $name -cf $cfg -si 00:00:05 -f bincirc -max 1024 -o (Join-Path $dir 'perf') -v mmddhhmm | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'logman create не сработал' }
logman start $name | Out-Host

# после перезагрузки сервера запись включается сама
$task = 'АвтоОтчёт - журнал производительности'
schtasks /Create /TN $task /TR "logman start $name" /SC ONSTART /RU SYSTEM /RL HIGHEST /F | Out-Host

Write-Host ''
Write-Host "Запись идёт. Файл: $dir\perf_*.blg" -ForegroundColor Green
Write-Host 'Когда сотрудники скажут, что зависло, достаточно запомнить примерное время.'
