<#
.SYNOPSIS
    Describe an image for DeepSeek - decrypts DPAPI key, calls Vision API.

.PARAMETER Image
    Image source: file path, HTTP URL, or data: URL.
.PARAMETER Detail
    Vision detail level: "auto" (default), "low", or "high".
.PARAMETER Quiet
    Suppress progress messages on stderr.
.PARAMETER Model
    Override the configured vision model.
.PARAMETER Locate
    Search for recent image files.
.PARAMETER Check
    Validate image readability without analysis.
.PARAMETER Test
    Diagnose environment without API calls.
.PARAMETER Convert
    Convert image to PNG format.
.EXAMPLE
    .\invoke-describe.ps1 -Image "screenshot.png"
    .\invoke-describe.ps1 -Locate
    .\invoke-describe.ps1 -Test
#>

param(
    [Parameter(Position = 0)]
    [string]$Image,
    [ValidateSet("auto", "low", "high")]
    [string]$Detail = "auto",
    [switch]$Quiet,
    [string]$Model,
    [switch]$Locate,
    [string]$Check,
    [switch]$Test,
    [string]$Question,
    [switch]$Convert
)

$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$pythonCmd = $null
foreach ($c in @("python","python3","py")) {
    $f = Get-Command $c -ErrorAction SilentlyContinue
    if ($f) { $pythonCmd = $f.Source; break }
}
if (-not $pythonCmd) {
    Write-Error "Python not found! Install Python 3.9+"
    exit 1
}

if ($Convert) {
    if (-not $Image) { Write-Error "Please provide -Image for conversion"; exit 1 }
    $ps = Join-Path $scriptDir "describe_image.py"
    & $pythonCmd $ps --convert $Image; exit $LASTEXITCODE
}

if ($Locate) {
    $ps = Join-Path $scriptDir "describe_image.py"
    & $pythonCmd $ps --locate; exit $LASTEXITCODE
}

if ($Check) {
    $ps = Join-Path $scriptDir "describe_image.py"
    & $pythonCmd $ps --check $Check; exit $LASTEXITCODE
}

if ($Test) {
    Write-Host "=== DeepSeek Vision Bridge - Diagnostic ===" -ForegroundColor Cyan
    Write-Host "[Check] Python ..." -NoNewline
    if ($pythonCmd) {
        Write-Host " OK ($pythonCmd)" -ForegroundColor Green
    } else {
        Write-Host " MISSING" -ForegroundColor Red
    }
    Write-Host "[Check] Pillow ..." -NoNewline
    $pilCheck = & $pythonCmd -c "from PIL import Image; print('OK')"
    if ($pilCheck.Trim() -eq "OK") {
        Write-Host " OK" -ForegroundColor Green
    } else {
        Write-Host " MISSING (pip install Pillow)" -ForegroundColor Red
    }
    Write-Host "[Check] httpx ..." -NoNewline
    $httpxCheck = & $pythonCmd -c "import httpx; print('OK')"
    if ($httpxCheck.Trim() -eq "OK") {
        Write-Host " OK" -ForegroundColor Green
    } else {
        Write-Host " MISSING (pip install httpx)" -ForegroundColor Red
    }
    Write-Host "[Check] API Key ..." -NoNewline
    $kf = Join-Path $scriptDir "vision-key.dpapi.txt"
    if (Test-Path $kf) {
        try {
            $enc = Get-Content $kf -Raw
            $sec = ConvertTo-SecureString -String $enc
            $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            $dec = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr)
            if ($dec.Length -gt 0) { Write-Host " OK (DPAPI)" -ForegroundColor Green }
            else { Write-Host " EMPTY" -ForegroundColor Red }
        } catch {
            Write-Host " DECRYPT FAILED" -ForegroundColor Red
        }
    } else {
        Write-Host " NOT FOUND" -ForegroundColor Red
    }
    Write-Host "[Check] Base URL ..." -NoNewline
    $url = $env:DEEPSEEK_VISION_BRIDGE_BASE_URL
    if (-not $url) { $url = [Environment]::GetEnvironmentVariable("DEEPSEEK_VISION_BRIDGE_BASE_URL","User") }
    if ($url) { Write-Host " $url" -ForegroundColor Green }
    else { Write-Host " NOT SET" -ForegroundColor Red }
    Write-Host "[Check] Model ..." -NoNewline
    $mdl = $env:DEEPSEEK_VISION_BRIDGE_MODEL
    if (-not $mdl) { $mdl = [Environment]::GetEnvironmentVariable("DEEPSEEK_VISION_BRIDGE_MODEL","User") }
    if ($mdl) { Write-Host " $mdl" -ForegroundColor Green }
    else { Write-Host " DEFAULT: qwen-vl-max" -ForegroundColor Yellow }
    Write-Host "Diagnostic complete." -ForegroundColor Cyan
    exit 0
}

if (-not $Image) {
    Write-Error 'Please provide -Image <path> or use -Locate'
    exit 1
}

$kf = Join-Path $scriptDir "vision-key.dpapi.txt"
if (-not (Test-Path $kf)) {
    Write-Error 'Vision API Key not configured! Run configure.ps1 first.'
    exit 1
}
try {
    $enc = Get-Content $kf -Raw
    $sec = ConvertTo-SecureString -String $enc
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $env:DEEPSEEK_VISION_BRIDGE_API_KEY = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr)
} catch {
    Write-Error 'Failed to decrypt API key. Re-run configure.ps1.'
    exit 1
}

if (-not $env:DEEPSEEK_VISION_BRIDGE_BASE_URL) {
    $env:DEEPSEEK_VISION_BRIDGE_BASE_URL = [Environment]::GetEnvironmentVariable("DEEPSEEK_VISION_BRIDGE_BASE_URL","User")
}
if (-not $env:DEEPSEEK_VISION_BRIDGE_MODEL) {
    $env:DEEPSEEK_VISION_BRIDGE_MODEL = [Environment]::GetEnvironmentVariable("DEEPSEEK_VISION_BRIDGE_MODEL","User")
}
if (-not $env:DEEPSEEK_VISION_BRIDGE_BASE_URL) {
    Write-Error 'Vision API URL not configured! Run configure.ps1 first.'
    exit 1
}

if ($Model) {
    $env:DEEPSEEK_VISION_BRIDGE_MODEL = $Model
    Write-Host "[info] Model: $Model" -ForegroundColor DarkGray
}
if (-not $env:DEEPSEEK_VISION_BRIDGE_MODEL) {
    $env:DEEPSEEK_VISION_BRIDGE_MODEL = "qwen-vl-max"  # must match DEFAULT_MODEL in describe_image.py
    Write-Host "[info] Model: qwen-vl-max (default)" -ForegroundColor DarkGray
}

$imagePath = $Image
if ($Image -notmatch '^(https?://|data:image/)') {
    if (Test-Path $Image) {
        $imagePath = (Resolve-Path $Image).Path
    } else {
        $found = $false
        $searchDirs = @("$env:USERPROFILE\Pictures\Screenshots","$env:USERPROFILE\Downloads","$env:USERPROFILE\Desktop",$env:TEMP,"$env:LOCALAPPDATA\Temp")
        foreach ($dir in $searchDirs) {
            $c = Join-Path $dir $Image
            if (Test-Path $c) {
                $imagePath = (Resolve-Path $c).Path
                $found = $true
                Write-Host "[info] Found at: $imagePath" -ForegroundColor DarkGray
                break
            }
        }
        if (-not $found) { Write-Error "Image not found: $Image"; exit 1 }
    }
}

$env:PYTHONIOENCODING = "utf-8"

$ps = Join-Path $scriptDir "describe_image.py"
if (-not (Test-Path $ps)) {
    Write-Error 'Script not found. Skill corrupted.'
    exit 1
}
$pyArgs = @($ps, "--image", $imagePath, "--detail", $Detail)
if ($Quiet) { $pyArgs += "--quiet" }
if ($Question) { $pyArgs += "--question"; $pyArgs += $Question }

if (-not $Quiet) {
    Write-Host "[info] Model: $env:DEEPSEEK_VISION_BRIDGE_MODEL" -ForegroundColor DarkGray
}
$rawOutput = & $pythonCmd $pyArgs
$exitCode = $LASTEXITCODE
if ($rawOutput -is [array]) { $result = $rawOutput -join "`n" }
else { $result = "$rawOutput" }

if ($exitCode -ne 0) { Write-Error "Image description failed (exit: $exitCode)"; if ($result) { Write-Error $result.Trim() }; exit $exitCode }
if ($result -match '^\[ERROR\]') { Write-Error $result.Trim(); exit 1 }
[Console]::WriteLine($result.Trim())
