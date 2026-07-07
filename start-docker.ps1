$log = Join-Path $PSScriptRoot "docker-start.log"
function Log($msg) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $msg"
    Add-Content $log $line
}

Set-Location $PSScriptRoot
Log "Waiting for Docker daemon..."

for ($i = 0; $i -lt 48; $i++) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Log "Docker ready after $($i * 5)s"
        break
    }
    Start-Sleep -Seconds 5
}

if ($LASTEXITCODE -ne 0) {
    Log "ERROR: Docker daemon not available"
    exit 1
}

Log "Running docker compose up --build -d"
docker compose up --build -d 2>&1 | ForEach-Object { Log $_ }

if ($LASTEXITCODE -eq 0) {
    Log "SUCCESS: http://localhost:3000/board.html"
    docker ps --filter name=splitflap 2>&1 | ForEach-Object { Log $_ }
} else {
    Log "ERROR: docker compose failed with exit $LASTEXITCODE"
    exit 1
}