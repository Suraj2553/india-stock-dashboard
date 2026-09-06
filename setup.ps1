<#
  setup.ps1 — one-shot installer for Market Monitor (Windows 10/11)

  Installs / verifies:  Python 3.12, Node.js LTS (>=20), Ollama + local model,
  the Python virtual-env with all dependencies, config files (.env, holdings,
  AI provider), the .mcp.json for AI editors, and optionally the twice-daily
  Task Scheduler jobs.

  Run:   setup.bat            (double-click)      or
         powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1 [-SkipOllama] [-Scheduler] [-Model llama3.2]
#>
param(
    [switch]$SkipOllama,          # do not install Ollama / download the local model
    [switch]$Scheduler,           # also register the 08:45 / 15:45 Task Scheduler jobs
    [string]$Model = "llama3.2"   # base Ollama model to pull (tool-calling capable)
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   [!!] $m" -ForegroundColor Yellow }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function RefreshPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function WingetInstall($id, $label) {
    if (-not (Have winget)) { throw "winget is not available. Install 'App Installer' from the Microsoft Store, then re-run setup." }
    Write-Host "   installing $label via winget ($id) ..."
    winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent | Out-Null
    RefreshPath
}

Write-Host ""
Write-Host "  +------------------------------------------------+" -ForegroundColor White
Write-Host "  |   MARKET MONITOR v4  --  automatic setup       |" -ForegroundColor White
Write-Host "  +------------------------------------------------+" -ForegroundColor White

# ── 1. Python ───────────────────────────────────────────────────────────
Step "Python 3.10+"
$py = $null
foreach ($cand in @("py -3.12", "py -3.11", "py -3.10", "python", "python3")) {
    try {
        $v = & cmd /c "$cand -c `"import sys;print(sys.version_info[0]*100+sys.version_info[1])`"" 2>$null
        if ($v -and [int]$v -ge 310) { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    WingetInstall "Python.Python.3.12" "Python 3.12"
    $py = "py -3.12"
    try { & cmd /c "$py --version" | Out-Null } catch { $py = "python" }
}
Ok "using: $py  ($(& cmd /c "$py --version"))"

# ── 2. Node.js (>= 20, needed for Groww MCP / mcp-remote) ───────────────
Step "Node.js LTS (>= 20)"
$nodeOk = $false
if (Have node) {
    $nv = (node --version) -replace "v", ""
    if ([int]($nv.Split(".")[0]) -ge 20) { $nodeOk = $true; Ok "node v$nv" }
    else { Warn "node v$nv is too old for mcp-remote (needs 20+); upgrading" }
}
if (-not $nodeOk) {
    WingetInstall "OpenJS.NodeJS.LTS" "Node.js LTS"
    if (Have node) { Ok "node $(node --version)" } else { Warn "Node installed - open a NEW terminal for it to be on PATH" }
}

# ── 3. Virtual environment + dependencies ──────────────────────────────
Step "Python virtual-env + dependencies"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & cmd /c "$py -m venv `"$Root\.venv`""
    Ok "created .venv"
}
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $Root "backend\requirements.txt") tradingview-mcp-server --quiet
Ok "dependencies installed"

# ── 4. Config files ────────────────────────────────────────────────────
Step "Config files"
if (-not (Test-Path "$Root\.env")) { Copy-Item "$Root\.env.example" "$Root\.env"; Ok "created .env (edit later for SMTP)" } else { Ok ".env exists" }
if (-not (Test-Path "$Root\data\holdings.json")) { Copy-Item "$Root\data\holdings.sample.json" "$Root\data\holdings.json"; Ok "created data\holdings.json from sample - edit it or use the Holdings Editor" } else { Ok "data\holdings.json exists" }
New-Item -ItemType Directory -Force "$Root\data\scans" | Out-Null
New-Item -ItemType Directory -Force "$Root\imports" | Out-Null

# .mcp.json for AI editors (absolute path to the venv's TradingView MCP server)
$tvExe = (Join-Path $Root ".venv\Scripts\tradingview-mcp.exe") -replace "\\", "/"
$mcpJson = (Get-Content "$Root\.mcp.template.json" -Raw) -replace "__TRADINGVIEW_MCP__", $tvExe
[IO.File]::WriteAllText("$Root\.mcp.json", $mcpJson, (New-Object System.Text.UTF8Encoding $false))   # no BOM
Ok ".mcp.json generated (Groww / Kite / TradingView connectors for Claude Code & Cursor)"

# ── 5. Ollama — private, local AI for the chat tab ─────────────────────
if (-not $SkipOllama) {
    Step "Ollama (local AI - your portfolio never leaves this PC)"
    $ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (-not (Have ollama) -and -not (Test-Path $ollamaExe)) {
        WingetInstall "Ollama.Ollama" "Ollama"
    }
    if (-not (Have ollama) -and (Test-Path $ollamaExe)) { $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama" }
    if (Have ollama) {
        # make sure the server is up
        $up = $false
        try { $up = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://localhost:11434/api/tags).StatusCode -eq 200 } catch {}
        if (-not $up) {
            Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
            for ($i = 0; $i -lt 30; $i++) { Start-Sleep 1; try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://localhost:11434/api/tags).StatusCode -eq 200) { $up = $true; break } } catch {} }
        }
        if ($up) {
            Ok "Ollama server running"
            Write-Host "   pulling model '$Model' (about 2 GB, one time) ..."
            ollama pull $Model
            $mf = "$Root\tools\ollama\Modelfile.$Model"
            if (-not (Test-Path $mf)) { "FROM $Model`nPARAMETER num_ctx 8192`nPARAMETER temperature 0.4`n" | Set-Content $mf -Encoding ascii }
            ollama create "mm-$Model" -f $mf | Out-Null
            Ok "local model 'mm-$Model' ready (8K context)"
            if (-not (Test-Path "$Root\data\llm_config.json")) {
                $cfgJson = @{ preset = "ollama"; base_url = "http://localhost:11434/v1"; api_key = ""; model = "mm-$Model" } | ConvertTo-Json
                [IO.File]::WriteAllText("$Root\data\llm_config.json", $cfgJson, (New-Object System.Text.UTF8Encoding $false))
                Ok "AI chat set to local Ollama (change in Settings -> AI Chat provider)"
            }
        } else { Warn "Ollama did not start - open the Ollama app once, then run: ollama pull $Model" }
    } else { Warn "Ollama not found on PATH - open a new terminal and re-run setup, or install from https://ollama.com/download" }
} else { Warn "Skipped Ollama (-SkipOllama). Configure a provider in Settings -> AI Chat provider." }

# ── 6. Optional Task Scheduler jobs ────────────────────────────────────
if ($Scheduler) {
    Step "Task Scheduler (08:45 & 15:45 scans + e-mail)"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\install_scheduler.ps1"
}

# ── Done ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host "  1. Double-click start.bat  ->  http://localhost:8080"
Write-Host "  2. Settings tab: add your holdings, e-mail (Gmail App Password) and check the AI provider"
Write-Host "  3. Buy Ideas tab: Run scan now"
if (-not $Scheduler) { Write-Host "  (optional) scans without the dashboard open:  setup.bat -Scheduler" }
Write-Host ""
