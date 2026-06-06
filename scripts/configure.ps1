<#
.SYNOPSIS
    Configure DeepSeek Vision Bridge — setup Vision API credentials.

.DESCRIPTION
    Stores the Vision API key using Windows DPAPI encryption and saves
    the API URL + model name as user-level environment variables.

.PARAMETER Language
    UI language: "zh" (Chinese) or "en" (English). Default: auto-detect.
#>

param(
    [ValidateSet("auto", "zh", "en")]
    [string]$Language = "auto"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Resolve language
function Resolve-Lang($req) {
    if ($req -in "zh", "en") { return $req }
    if ([System.Globalization.CultureInfo]::CurrentUICulture.Name -like "zh*") { return "zh" }
    return "en"
}
$lang = Resolve-Lang $Language

# Language strings
$m = @{
    zh = @{
        Title        = "=== DeepSeek Vision Bridge 配置 ==="
        KeyPrompt    = "Vision API Key（任意支持视觉的大模型 Key，OpenAI/Claude/中转均可）"
        KeyNote1     = "输入隐藏，Key 将用 Windows DPAPI 加密保存"
        UrlPrompt    = "Vision API Base URL"
        UrlNote      = "支持 OpenAI 兼容格式。例: https://api.openai.com 或中转地址"
        ModelPrompt  = "Vision Model 名称"
        ModelNote    = "任何视觉模型。例: gpt-4o, qwen-vl-max, claude-sonnet-4.6, gemini-2.5-flash"
        EnterKey     = "请输入 Vision API Key（输入隐藏）"
        Saved        = "配置完成！"
        VerifyTitle  = "=== 验证配置 ==="
        VerifyKey    = "Key: DPAPI 加密存储 ✓"
        VerifyUrl    = "URL: {0}"
        VerifyModel  = "Model: {0}"
        VerifyPass   = "全部通过 ✓"
        Failed       = "失败：{0}"
    }
    en = @{
        Title        = "=== DeepSeek Vision Bridge Configuration ==="
        KeyPrompt    = "Vision API Key (OpenAI/Claude/proxy key with vision support)"
        KeyNote1     = "Input hidden, stored with Windows DPAPI encryption"
        UrlPrompt    = "Vision API Base URL"
        UrlNote      = "OpenAI-compatible endpoint. e.g., https://api.openai.com"
        ModelPrompt  = "Vision Model Name"
        ModelNote    = "Any vision-capable model. e.g., gpt-4o, qwen-vl-max, claude-sonnet-4.6"
        EnterKey     = "Enter Vision API Key (input hidden)"
        Saved        = "Configuration saved!"
        VerifyTitle  = "=== Verification ==="
        VerifyKey    = "Key: DPAPI encrypted ✓"
        VerifyUrl    = "URL: {0}"
        VerifyModel  = "Model: {0}"
        VerifyPass   = "All checks passed ✓"
        Failed       = "Failed: {0}"
    }
}

Write-Host ""
Write-Host $m[$lang].Title -ForegroundColor Cyan
Write-Host ""

# 1. Vision API Key
Write-Host $m[$lang].KeyPrompt -ForegroundColor Yellow
Write-Host "  $($m[$lang].KeyNote1)" -ForegroundColor Gray
$keySecure = Read-Host -Prompt "API Key" -AsSecureString
$plainKey = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($keySecure)
)
$encrypted = ConvertFrom-SecureString -SecureString $keySecure
$keyFile = Join-Path $scriptDir "vision-key.dpapi.txt"
$encrypted | Out-File -FilePath $keyFile -Encoding ASCII -NoNewline
Write-Host "  Key saved (DPAPI encrypted)" -ForegroundColor Green
Write-Host ""

# 2. Vision API URL
Write-Host $m[$lang].UrlPrompt -ForegroundColor Yellow
Write-Host "  $($m[$lang].UrlNote)" -ForegroundColor Gray
$visionUrl = (Read-Host "URL").TrimEnd("/")
setx DEEPSEEK_VISION_BRIDGE_BASE_URL "$visionUrl" 2>&1 | Out-Null
$env:DEEPSEEK_VISION_BRIDGE_BASE_URL = $visionUrl
Write-Host "  URL: $visionUrl" -ForegroundColor Green
Write-Host ""

# 3. Vision Model
Write-Host $m[$lang].ModelPrompt -ForegroundColor Yellow
Write-Host "  $($m[$lang].ModelNote)" -ForegroundColor Gray
$visionModel = Read-Host "Model"
if ([string]::IsNullOrWhiteSpace($visionModel)) { $visionModel = "gpt-4o" }
setx DEEPSEEK_VISION_BRIDGE_MODEL "$visionModel" 2>&1 | Out-Null
$env:DEEPSEEK_VISION_BRIDGE_MODEL = $visionModel
Write-Host "  Model: $visionModel" -ForegroundColor Green
Write-Host ""

# Verify
Write-Host $m[$lang].VerifyTitle -ForegroundColor Cyan
$ok = $true
if (Test-Path $keyFile) {
    try {
        $enc2 = Get-Content $keyFile -Raw
        $sec2 = ConvertTo-SecureString -String $enc2
        $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec2)
        $dec = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr)
        if ($dec.Length -gt 0) { Write-Host "  $($m[$lang].VerifyKey)" -ForegroundColor Green }
        else { Write-Host "  $($m[$lang].Failed -f 'key empty')" -ForegroundColor Red; $ok = $false }
    } catch { Write-Host "  $($m[$lang].Failed -f $_)" -ForegroundColor Red; $ok = $false }
}
if ($visionUrl) { Write-Host "  $($m[$lang].VerifyUrl -f $visionUrl)" -ForegroundColor Green }
else { Write-Host "  $($m[$lang].Failed -f 'URL missing')" -ForegroundColor Red; $ok = $false }

if ($visionModel) { Write-Host "  $($m[$lang].VerifyModel -f $visionModel)" -ForegroundColor Green }
else { Write-Host "  $($m[$lang].Failed -f 'model missing')" -ForegroundColor Red; $ok = $false }

if ($ok) {
    Write-Host "  $($m[$lang].VerifyPass)" -ForegroundColor Green
} else {
    Write-Host "  配置不完整，请重新运行" -ForegroundColor Red
}

Write-Host ""
