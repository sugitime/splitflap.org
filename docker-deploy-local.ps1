# Local Docker deployment for splitflap.org
# Writes full output to docker-deploy-output.log
$ErrorActionPreference = 'Continue'
$logPath = Join-Path $PSScriptRoot 'docker-deploy-output.log'
$projectDir = $PSScriptRoot

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $logPath -Value $line
}

# Clear log
Set-Content -Path $logPath -Value "=== Docker Local Deploy started $(Get-Date) ===`n"

Set-Location $projectDir
Log "Working directory: $projectDir"

# Step 1: Check Docker
Log "--- Step 1: Docker version ---"
try {
    $ver = docker --version 2>&1
    Log "docker --version: $ver"
} catch {
    Log "ERROR: docker --version failed: $_"
}

Log "--- Step 1b: docker info ---"
$info = docker info 2>&1 | Out-String
Log $info
if ($LASTEXITCODE -ne 0 -or $info -match 'Cannot connect|error during connect|daemon is not running') {
    Log "Docker daemon not running. Attempting to start Docker Desktop..."
    $dockerDesktop = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktop) {
        Start-Process $dockerDesktop
        Log "Started Docker Desktop. Waiting up to 120s for daemon..."
        $ready = $false
        for ($i = 0; $i -lt 24; $i++) {
            Start-Sleep -Seconds 5
            docker info 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $ready = $true
                Log "Docker daemon ready after $($i * 5 + 5)s"
                break
            }
        }
        if (-not $ready) {
            Log "ERROR: Docker Desktop did not become ready in time."
            exit 1
        }
    } else {
        Log "ERROR: Docker Desktop not found at $dockerDesktop"
        exit 1
    }
}

# Step 2: Stop/remove existing container if present
Log "--- Step 2: Clean up existing containers ---"
docker compose down 2>&1 | ForEach-Object { Log $_ }
docker rm -f splitflap 2>&1 | ForEach-Object { Log $_ }

# Step 3: docker compose up
Log "--- Step 3: docker compose up --build -d ---"
$composeOut = docker compose up --build -d 2>&1 | Out-String
Log $composeOut

if ($LASTEXITCODE -ne 0) {
    Log "docker compose failed. Trying docker build + docker run fallback..."
    docker build -t splitflap . 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0) {
        Log "ERROR: docker build failed"
        exit 1
    }
    docker run -d --name splitflap -p 3000:3000 splitflap 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0) {
        Log "ERROR: docker run failed"
        exit 1
    }
}

# Step 4: Wait for healthy
Log "--- Step 4: Waiting for container health (up to 90s) ---"
$containerName = $null
$maxWait = 18
for ($i = 0; $i -lt $maxWait; $i++) {
    Start-Sleep -Seconds 5
    $psOut = docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}" 2>&1 | Out-String
    Log "docker ps (attempt $($i+1)):`n$psOut"

    $lines = docker ps --format "{{.Names}}|{{.Status}}" 2>&1
    foreach ($line in $lines) {
        if ($line -match 'splitflap') {
            $containerName = ($line -split '\|')[0]
            $status = ($line -split '\|')[1]
            if ($status -match 'healthy' -or ($status -match 'Up' -and $i -ge 2)) {
                Log "Container ready: $containerName — $status"
                break
            }
        }
    }
    if ($containerName -and ($status -match 'healthy' -or $i -ge 5)) { break }
}

# Step 5: Verify endpoints
Log "--- Step 5: Verify endpoints ---"
$healthUrl = 'http://localhost:3000/api/health'
$boardUrl = 'http://localhost:3000/board.html'

try {
    $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10
    Log "GET $healthUrl => $($health.StatusCode) $($health.Content)"
} catch {
    Log "GET $healthUrl => FAILED: $_"
}

try {
    $board = Invoke-WebRequest -Uri $boardUrl -UseBasicParsing -TimeoutSec 10
    $isHtml = $board.Content -match '<html' -or $board.Headers['Content-Type'] -match 'text/html'
    Log "GET $boardUrl => $($board.StatusCode) Content-Type: $($board.Headers['Content-Type']) IsHtml: $isHtml"
    if (-not $isHtml) { Log "WARNING: board.html response may not be HTML" }
} catch {
    Log "GET $boardUrl => FAILED: $_"
}

# Final summary
Log "`n=== DEPLOYMENT SUMMARY ==="
$finalPs = docker ps -a --filter "name=splitflap" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>&1 | Out-String
Log $finalPs
Log "Health URL:  $healthUrl"
Log "Board URL:   $boardUrl"
Log "Home URL:    http://localhost:3000/"
Log "Log file:    $logPath"
Log "=== Done ==="