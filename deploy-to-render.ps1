# Deploy splitflap.org to Render.com
# Run from PowerShell:  cd C:\Users\ksugi\splitflap.org; .\deploy-to-render.ps1

$ErrorActionPreference = "Stop"

$RENDER_API_KEY = $env:RENDER_API_KEY
if (-not $RENDER_API_KEY) {
    Write-Error "Set RENDER_API_KEY before running this script."
    exit 1
}
$RENDER_OWNER_ID = "tea-d8m251i8qa3s73b06upg"
$SERVICE_NAME = "splitflap"
$GITHUB_REPO = "sugitime/splitflap.org"
$GITHUB_REPO_URL = "https://github.com/$GITHUB_REPO"
$BRANCH = "main"
$PROJECT_DIR = $PSScriptRoot

$RenderHeaders = @{
    Authorization = "Bearer $RENDER_API_KEY"
    Accept        = "application/json"
    "Content-Type" = "application/json"
}

function Write-Step($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

Set-Location $PROJECT_DIR

# --- Step 1: Git status ---
Write-Step "1. Git status"
git status
Write-Host ""
git log --oneline -3
Write-Host ""
git remote -v

# --- Step 2: List existing Render services ---
Write-Step "2. List Render services"
$servicesResponse = Invoke-RestMethod -Uri "https://api.render.com/v1/services?ownerId=$RENDER_OWNER_ID&limit=50" -Headers $RenderHeaders -Method Get
$existingService = $null
foreach ($item in $servicesResponse) {
    $svc = $item.service
    if ($svc.name -eq $SERVICE_NAME) {
        $existingService = $svc
        Write-Host "Found existing service: $($svc.name) (id: $($svc.id))" -ForegroundColor Yellow
        Write-Host "  URL: $($svc.serviceDetails.url)"
        Write-Host "  Dashboard: $($svc.dashboardUrl)"
    }
}
if (-not $existingService) {
    Write-Host "No existing '$SERVICE_NAME' service found." -ForegroundColor Green
}

# --- Step 3: Ensure GitHub repo exists and remote is set ---
Write-Step "3. GitHub repo setup"
$ghAvailable = $null -ne (Get-Command gh -ErrorAction SilentlyContinue)
if ($ghAvailable) {
    gh auth status 2>&1 | Out-Host
    $repoExists = $false
    try {
        gh repo view $GITHUB_REPO 2>$null | Out-Null
        $repoExists = $true
        Write-Host "Repo $GITHUB_REPO already exists."
    } catch {
        Write-Host "Repo $GITHUB_REPO not found. Creating..."
        gh repo create $GITHUB_REPO --public --source . --remote fork --push=false --description "Split-flap display board (Docker + Render deploy)"
        $repoExists = $true
    }
} else {
    Write-Host "gh CLI not found. Ensure $GITHUB_REPO_URL exists on GitHub." -ForegroundColor Yellow
}

$currentRemote = (git remote get-url origin 2>$null)
if ($currentRemote -notmatch "sugitime/splitflap") {
    Write-Host "Updating origin remote to $GITHUB_REPO_URL"
    if ($currentRemote) {
        git remote set-url origin $GITHUB_REPO_URL
    } else {
        git remote add origin $GITHUB_REPO_URL
    }
}
git remote -v

# --- Step 4: Commit local Docker changes ---
Write-Step "4. Commit local changes"
$filesToCommit = @(
    "Dockerfile",
    "render.yaml",
    ".dockerignore",
    "docker-compose.yml",
    "server.js",
    "package.json"
)
$hasChanges = $false
foreach ($f in $filesToCommit) {
    if (Test-Path $f) {
        git add $f
    }
}
$status = git status --porcelain
if ($status) {
    git commit -m "Add Docker and Render deployment configuration

- Dockerfile for Node 20 Alpine production build
- render.yaml blueprint for Render
- docker-compose.yml for local testing
- server.js binds to 0.0.0.0 for container hosting
- package.json start script and Node engine requirement"
    $hasChanges = $true
    Write-Host "Committed changes." -ForegroundColor Green
} else {
    Write-Host "No uncommitted changes." -ForegroundColor Yellow
}

# --- Step 5: Push to GitHub ---
Write-Step "5. Push to GitHub"
git push -u origin $BRANCH
Write-Host "Pushed to $GITHUB_REPO_URL ($BRANCH)" -ForegroundColor Green

# --- Step 6: Create Render web service (if needed) ---
Write-Step "6. Create Render web service"
$serviceId = $null
$deployId = $null
$serviceUrl = $null
$dashboardUrl = $null

if ($existingService) {
    $serviceId = $existingService.id
    $serviceUrl = $existingService.serviceDetails.url
    $dashboardUrl = $existingService.dashboardUrl
    Write-Host "Using existing service $serviceId"
} else {
    $createBody = @{
        type       = "web_service"
        name       = $SERVICE_NAME
        ownerId    = $RENDER_OWNER_ID
        repo       = $GITHUB_REPO_URL
        branch     = $BRANCH
        autoDeploy = "yes"
        envVars    = @(
            @{ key = "NODE_ENV"; value = "production" }
        )
        serviceDetails = @{
            env                = "docker"
            plan               = "free"
            region             = "oregon"
            healthCheckPath    = "/api/health"
            envSpecificDetails = @{
                dockerContext   = "."
                dockerfilePath  = "./Dockerfile"
            }
        }
    } | ConvertTo-Json -Depth 10

    Write-Host "Creating service with payload:"
    Write-Host $createBody

    try {
        $createResponse = Invoke-RestMethod -Uri "https://api.render.com/v1/services" -Headers $RenderHeaders -Method Post -Body $createBody
        $serviceId = $createResponse.service.id
        $deployId = $createResponse.deployId
        $serviceUrl = $createResponse.service.serviceDetails.url
        $dashboardUrl = $createResponse.service.dashboardUrl
        Write-Host "Service created!" -ForegroundColor Green
        Write-Host "  Service ID: $serviceId"
        Write-Host "  Deploy ID:  $deployId"
    } catch {
        $errBody = $_.ErrorDetails.Message
        Write-Host "Create service failed: $errBody" -ForegroundColor Red
        if ($errBody -match "already exists" -or $_.Exception.Response.StatusCode.value__ -eq 409) {
            Write-Host "Service may already exist. Re-fetching..." -ForegroundColor Yellow
            $servicesResponse = Invoke-RestMethod -Uri "https://api.render.com/v1/services?ownerId=$RENDER_OWNER_ID&limit=50" -Headers $RenderHeaders -Method Get
            foreach ($item in $servicesResponse) {
                if ($item.service.name -eq $SERVICE_NAME) {
                    $serviceId = $item.service.id
                    $serviceUrl = $item.service.serviceDetails.url
                    $dashboardUrl = $item.service.dashboardUrl
                }
            }
        } else {
            throw
        }
    }
}

# --- Step 7: Trigger deploy if no deploy was auto-started ---
Write-Step "7. Trigger deploy"
if (-not $deployId -and $serviceId) {
    try {
        $deployResponse = Invoke-RestMethod -Uri "https://api.render.com/v1/services/$serviceId/deploys" -Headers $RenderHeaders -Method Post -Body "{}"
        $deployId = $deployResponse.id
        Write-Host "Triggered deploy: $deployId"
    } catch {
        Write-Host "Deploy trigger note: $($_.ErrorDetails.Message)" -ForegroundColor Yellow
        $deploys = Invoke-RestMethod -Uri "https://api.render.com/v1/services/$serviceId/deploys?limit=1" -Headers $RenderHeaders -Method Get
        if ($deploys.Count -gt 0) {
            $deployId = $deploys[0].deploy.id
            Write-Host "Using latest deploy: $deployId"
        }
    }
}

# --- Step 8: Poll deploy status ---
Write-Step "8. Poll deploy status"
if ($deployId -and $serviceId) {
    $maxAttempts = 60
    $attempt = 0
    $finalStatus = $null
    while ($attempt -lt $maxAttempts) {
        $attempt++
        $deploy = Invoke-RestMethod -Uri "https://api.render.com/v1/services/$serviceId/deploys/$deployId" -Headers $RenderHeaders -Method Get
        $status = $deploy.status
        $finalStatus = $status
        Write-Host "[$attempt/$maxAttempts] Deploy status: $status"
        if ($status -in @("live", "deactivated", "build_failed", "update_failed", "canceled", "pre_deploy_failed")) {
            break
        }
        Start-Sleep -Seconds 15
    }

    if ($finalStatus -eq "live") {
        Write-Host "Deploy is LIVE!" -ForegroundColor Green
        try {
            $health = Invoke-RestMethod -Uri "$serviceUrl/api/health" -Method Get -TimeoutSec 30
            Write-Host "Health check OK: $($health | ConvertTo-Json -Compress)"
        } catch {
            Write-Host "Health check pending (service may still be warming up): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Deploy finished with status: $finalStatus" -ForegroundColor $(if ($finalStatus -eq "live") { "Green" } else { "Red" })
    }
} else {
    Write-Host "No deploy ID available to poll." -ForegroundColor Yellow
}

# --- Summary ---
Write-Step "DEPLOYMENT SUMMARY"
Write-Host "GitHub repo:     $GITHUB_REPO_URL"
Write-Host "Service URL:     $serviceUrl"
Write-Host "Dashboard URL:   $dashboardUrl"
Write-Host "Service ID:      $serviceId"
Write-Host "Deploy ID:       $deployId"
Write-Host "Deploy status:   $finalStatus"
Write-Host ""
Write-Host "Done." -ForegroundColor Green