# Выдаёт готовую ссылку на пульт.
#
# Cloudflare на этой сети не работает: провайдер рвёт TLS на порту 7844.
# Поэтому идём через SSH-туннели — они ходят по 443 и 22, которые проходят.
#
# Важная тонкость: ssh завершается, если его стандартный ввод закрыт.
# Поэтому процесс запускаем через .NET и держим поток ввода открытым —
# иначе туннель отваливается сразу после выдачи адреса.

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$host.UI.RawUI.WindowTitle = "Турбопульт - ссылка"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($text, $color = "Gray") { Write-Host "  $text" -ForegroundColor $color }

function ServerAlive {
    try {
        Invoke-WebRequest "http://127.0.0.1:8000/login" -TimeoutSec 2 -UseBasicParsing | Out-Null
        return $true
    } catch { return $false }
}

$script:keepAlive = @()

function TryTunnel($title, $sshArgs, $pattern, $waitSec) {
    Say "    пробую $title..." "DarkGray"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "ssh.exe"
    $psi.Arguments = ($sshArgs -join " ")
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    $buffer = New-Object System.Text.StringBuilder
    $onData = {
        if ($EventArgs.Data) { [void]$Event.MessageData.AppendLine($EventArgs.Data) }
    }
    $subs = @(
        Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action $onData -MessageData $buffer
        Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived  -Action $onData -MessageData $buffer
    )

    try { [void]$proc.Start() } catch {
        Say "    ssh не запустился: $($_.Exception.Message)" "DarkGray"
        return $null
    }
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    # Держим ссылку на поток ввода: закроется — ssh выйдет
    $script:keepAlive += $proc.StandardInput

    for ($i = 0; $i -lt ($waitSec * 2); $i++) {
        Start-Sleep -Milliseconds 500
        $text = $buffer.ToString()
        if ($text -match $pattern) {
            return @{ proc = $proc; link = $Matches[0]; subs = $subs; buffer = $buffer; pattern = $pattern }
        }
        if ($proc.HasExited) { break }
    }

    if (-not $proc.HasExited) { try { $proc.Kill() } catch { } }
    $subs | ForEach-Object { Unregister-Event -SubscriptionId $_.Id -ErrorAction SilentlyContinue }
    Say "    $title не ответил" "DarkGray"
    return $null
}

Clear-Host
Write-Host ""
Say "ТУРБОПУЛЬТ" "Cyan"
Say "готовлю ссылку, подождите..." "DarkGray"
Write-Host ""

# ---------- 1. сервер ----------
if (ServerAlive) {
    Say "[1/2] сервер уже работает" "Green"
} else {
    Say "[1/2] поднимаю сервер..." "Yellow"
    Start-Process -FilePath (Join-Path $here "start.cmd") -WorkingDirectory $here -WindowStyle Minimized
    $up = $false
    foreach ($i in 1..90) {
        Start-Sleep -Milliseconds 700
        if (ServerAlive) { $up = $true; break }
    }
    if (-not $up) {
        Write-Host ""
        Say "Сервер не запустился. Откройте «Турбопульт — запустить» и посмотрите ошибку." "Red"
        Write-Host ""
        Read-Host "  Enter - закрыть"
        exit 1
    }
    Say "[1/2] сервер поднят" "Green"
}

# ---------- 2. туннель ----------
Say "[2/2] открываю доступ..." "Yellow"

$common = @(
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=NUL",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "ExitOnForwardFailure=yes",
    "-T"
)

$result = $null
if (-not $result) {
    $result = TryTunnel "localhost.run" `
        ($common + @("-R", "80:localhost:8000", "nokey@localhost.run")) `
        "https://[a-z0-9.\-]+\.lhr\.life" 35
}
if (-not $result) {
    $result = TryTunnel "pinggy (порт 443)" `
        ($common + @("-p", "443", "-R", "0:localhost:8000", "a.pinggy.io")) `
        "https://[a-z0-9.\-]+\.pinggy\.link" 35
}
if (-not $result) {
    $result = TryTunnel "serveo" `
        ($common + @("-R", "80:localhost:8000", "serveo.net")) `
        "https://[a-z0-9.\-]+\.serveo\.net" 30
}

if (-not $result) {
    Write-Host ""
    Say "Ни один туннель не поднялся." "Red"
    Say "Остаётся проброс порта или сервер." "Red"
    Write-Host ""
    Read-Host "  Enter - закрыть"
    exit 1
}

$link = $result.link
$proc = $result.proc

$pass = ""
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern "^ACCESS_PASSWORD=(.*)$" | Select-Object -First 1
    if ($line) { $pass = $line.Matches[0].Groups[1].Value.Trim() }
}

# Отвечает не просто «какой-то сайт», а именно наш пульт.
# Бесплатные туннели раздают адреса повторно: старая ссылка через час
# может вести на чужую страницу. Метка /tp-ping есть только у Турбопульта.
function IsOurs($url) {
    try {
        $r = Invoke-WebRequest "$url/tp-ping" -TimeoutSec 8 -UseBasicParsing
        return ($r.Content -match "turbopult-ok")
    } catch { return $false }
}

function ShowLink($url, $alive, $changed) {
    try { Set-Clipboard -Value $url } catch { }
    try { Set-Content -Path (Join-Path $here "last_link.txt") -Value $url -Encoding UTF8 } catch { }

    Clear-Host
    Write-Host ""
    Write-Host "  ==========================================================" -ForegroundColor DarkCyan
    Write-Host ""
    if ($changed) {
        Write-Host "   ВНИМАНИЕ: СЕРВИС ТУННЕЛЯ СМЕНИЛ АДРЕС." -ForegroundColor Red
        Write-Host "   Старую ссылку больше не давайте - она может вести на чужой сайт." -ForegroundColor Red
        Write-Host ""
        [Console]::Beep(880, 250)
    }
    Write-Host "   ССЫЛКА ДЛЯ КОЛЛЕГ" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   $url" -ForegroundColor White -BackgroundColor DarkBlue
    Write-Host ""
    if ($pass) { Write-Host "   Пароль:  $pass" -ForegroundColor Yellow }
    Write-Host ""
    if ($alive) {
        Write-Host "   Проверено: по адресу открывается именно Турбопульт." -ForegroundColor Green
    } else {
        Write-Host "   Адрес пока не отвечает - подождите полминуты и обновите." -ForegroundColor Yellow
    }
    Write-Host "   Ссылка скопирована в буфер и лежит в last_link.txt" -ForegroundColor DarkGray
    Write-Host "   Обновлено: $(Get-Date -Format 'HH:mm')" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  ==========================================================" -ForegroundColor DarkCyan
    Write-Host ""
    Write-Host "   НЕ ЗАКРЫВАЙТЕ ЭТО ОКНО - пока оно открыто, ссылка работает." -ForegroundColor Red
    Write-Host "   Если адрес сменится, окно само покажет новый." -ForegroundColor DarkGray
    Write-Host ""
}

Say "    проверяю ссылку..." "DarkGray"
$alive = $false
foreach ($i in 1..10) {
    if (IsOurs $link) { $alive = $true; break }
    Start-Sleep -Seconds 2
}
ShowLink $link $alive $false

# Следим за туннелем: localhost.run на бесплатном тарифе время от времени
# выдаёт новый адрес в том же соединении — ловим его и сразу показываем
$tick = 0
while (-not $proc.HasExited) {
    Start-Sleep -Seconds 2
    $tick++
    $all = [regex]::Matches($result.buffer.ToString(), $result.pattern)
    if ($all.Count -gt 0) {
        $latest = $all[$all.Count - 1].Value
        if ($latest -ne $link) {
            $link = $latest
            ShowLink $link (IsOurs $link) $true
            continue
        }
    }
    # Раз в минуту убеждаемся, что адрес всё ещё наш
    if ($tick % 30 -eq 0 -and -not (IsOurs $link)) {
        Write-Host "   $(Get-Date -Format 'HH:mm')  адрес не отвечает как Турбопульт - проверяю дальше..." -ForegroundColor Yellow
    }
}

Write-Host ""
Say "Туннель закрылся. Запустите ярлык заново - будет новая ссылка." "DarkGray"
Read-Host "  Enter - закрыть"
