param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$Root = Split-Path -Parent $PSScriptRoot
$PythonCmd = if ($env:VIME_PYTHON) { $env:VIME_PYTHON } else { "python" }
$Host = if ($env:VIME_HTTP_HOST) { $env:VIME_HTTP_HOST } else { "127.0.0.1" }
$Port = if ($env:VIME_HTTP_PORT) { $env:VIME_HTTP_PORT } else { "51789" }
$Server = Join-Path $Root "python\vime_server.py"
$HealthUrl = "http://$Host`:$Port/health"
$ShutdownUrl = "http://$Host`:$Port/shutdown"

$StartedByWrapper = $false
$ServerPid = $null

try {
    Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 1 | Out-Null
} catch {
    $StartedByWrapper = $true
    $proc = Start-Process -FilePath $PythonCmd -ArgumentList @($Server, "--host", $Host, "--port", $Port) -PassThru -WindowStyle Hidden
    $ServerPid = $proc.Id
    for ($i = 0; $i -lt 20; $i++) {
        try {
            Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 1 | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
}

& vim @Args

if ($StartedByWrapper) {
    try {
        Invoke-WebRequest -Method Post -Uri $ShutdownUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
    } catch {
    }
    if ($ServerPid) {
        Start-Sleep -Milliseconds 500
        try {
            Stop-Process -Id $ServerPid -ErrorAction SilentlyContinue
        } catch {
        }
    }
}
