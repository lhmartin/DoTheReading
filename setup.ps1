<#
.SYNOPSIS
    One-command setup for the paper study pipeline on Windows.

.DESCRIPTION
    Installs (if missing) Python, Ollama and Tesseract via winget, creates a
    .venv next to this script with the Python dependencies, pulls the model,
    creates ~/PaperStudy/{inbox,library,questions}, and registers the nightly
    Task Scheduler job with "Wake the computer to run this task" enabled.

    Safe to re-run: every step checks first, and the scheduled task is
    replaced in place.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Model qwen2.5:14b -Time 03:30
#>
param(
    # Defaults to the MODEL set in process_inbox.py's CONFIG block.
    [string]$Model,
    [string]$Time = "02:00",
    [string]$TaskName = "PaperStudyNightly",
    [switch]$SkipModelPull,
    [switch]$SkipTask
)

$ErrorActionPreference = "Stop"
$RepoDir = $PSScriptRoot
$VenvPython = Join-Path $RepoDir ".venv\Scripts\python.exe"
$PaperStudyDir = Join-Path $HOME "PaperStudy"

# PowerShell turns a native command's stderr into a terminating error while
# $ErrorActionPreference is "Stop" — and plenty of the tools here write normal
# progress to stderr (ollama pull, npm, the Store's python stub). Run every
# external command through this so only real exit codes stop us.
function Invoke-Native {
    param([Parameter(Mandatory)][scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $previous }
}

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Warn($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-WithWinget($id, $name) {
    if (-not (Have winget)) {
        throw "winget isn't available. Install $name manually, then re-run this script."
    }
    Info "Installing $name via winget..."
    Invoke-Native { winget install --id $id --exact --silent --accept-source-agreements --accept-package-agreements }
    if ($LASTEXITCODE -ne 0) { Warn "winget exited with code $LASTEXITCODE while installing $name." }
    Update-SessionPath
}

# Returns the command (as an array) for a Python >= 3.10, or $null. Runs the
# interpreter rather than trusting Get-Command, because of the Microsoft Store
# "python.exe" stub.
function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"))) {
        $command = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        # Skip the Microsoft Store's placeholder, which isn't Python at all.
        if (-not $command -or $command.Source -like "*\WindowsApps\*") { continue }
        $exe = $candidate[0]
        $args_ = @($candidate | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 10))")
        $ok = Invoke-Native { & $exe @args_ 2>$null }
        if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "True") { return ,$candidate }  # "," stops PowerShell unrolling the array
    }
    return $null
}

if ($RepoDir -like "\\*") {
    throw ("This script is running from a network/WSL path ($RepoDir). Clone the repo onto the " +
           "Windows drive (e.g. $HOME\code\DoTheReading) so Task Scheduler and Windows Python can run it.")
}

# ---- Python + dependencies ---------------------------------------------------
Step "Python"
$python = Find-Python
if (-not $python) {
    Install-WithWinget "Python.Python.3.12" "Python 3.12"
    $python = Find-Python
    if (-not $python) { throw "Python 3.10+ still not found. Open a new terminal and re-run this script." }
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)
Info ("Using " + (Invoke-Native { & $pyExe @pyArgs --version }))

Step "Virtual environment + Python packages"
if (-not (Test-Path $VenvPython)) {
    Invoke-Native { & $pyExe @pyArgs -m venv (Join-Path $RepoDir ".venv") }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv" }
}
Invoke-Native { & $VenvPython -m pip install --quiet --upgrade pip }
Invoke-Native { & $VenvPython -m pip install --quiet -r (Join-Path $RepoDir "requirements.txt") }
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Info "Installed into $RepoDir\.venv"

# ---- Tesseract (OCR for scanned PDFs) ----------------------------------------
Step "Tesseract OCR"
$tesseractDefault = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
if ((Have tesseract) -or (Test-Path $tesseractDefault)) {
    Info "Already installed."
} else {
    Install-WithWinget "UB-Mannheim.TesseractOCR" "Tesseract OCR"
    if (-not ((Have tesseract) -or (Test-Path $tesseractDefault))) {
        Warn "Tesseract not found after install. Scanned PDFs will be skipped until it's installed."
    }
}

# ---- Ollama + model ----------------------------------------------------------
Step "Ollama"
if (-not (Have ollama)) {
    Install-WithWinget "Ollama.Ollama" "Ollama"
    if (-not (Have ollama)) {
        $ollamaDefault = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
        if (Test-Path (Join-Path $ollamaDefault "ollama.exe")) { $env:Path += ";$ollamaDefault" }
    }
    if (-not (Have ollama)) { throw "Ollama not found after install. Open a new terminal and re-run this script." }
}

# These let the 32B model and its context cache fit in 16GB of VRAM. Without
# them the KV cache is twice the size and part of the model runs on the CPU
# (~3x slower). Ollama reads them at startup, so a running server is
# restarted when they change.
function Set-OllamaMemorySettings {
    $wanted = @{ OLLAMA_FLASH_ATTENTION = "1"; OLLAMA_KV_CACHE_TYPE = "q8_0" }
    $changed = $false
    foreach ($name in $wanted.Keys) {
        if ([Environment]::GetEnvironmentVariable($name, "User") -ne $wanted[$name]) {
            [Environment]::SetEnvironmentVariable($name, $wanted[$name], "User")
            $changed = $true
        }
        Set-Item "env:$name" $wanted[$name]
    }
    if (-not $changed) {
        Info "Memory settings already set."
        return
    }
    Info "Set OLLAMA_FLASH_ATTENTION=1 and OLLAMA_KV_CACHE_TYPE=q8_0."
    $running = Get-Process -Name "ollama", "ollama app" -ErrorAction SilentlyContinue
    if (-not $running) { return }
    Info "Restarting Ollama so they take effect..."
    try {
        $running | Stop-Process -Force -ErrorAction Stop
        Start-Sleep -Seconds 3
        $app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
        if (Test-Path $app) { Start-Process $app } else { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden }
    } catch {
        Warn "Couldn't restart Ollama automatically. Restart it (or reboot) before the next run."
    }
}


function Test-OllamaUp {
    try { Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 3 } catch { $null }
}
Step "Ollama memory settings"
Set-OllamaMemorySettings

$version = Test-OllamaUp
if (-not $version) {
    Info "Starting the Ollama server..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not $version; $i++) {
        Start-Sleep -Seconds 1
        $version = Test-OllamaUp
    }
    if (-not $version) { throw "Ollama server didn't come up on localhost:11434." }
}
Info "Ollama $($version.version) is running."
# Structured (JSON schema) output, used for question generation, needs 0.5+.
if ([version]($version.version -replace '[^0-9.].*$', '') -lt [version]"0.5.0") {
    Warn "Ollama 0.5.0+ is needed for structured output. Update with: winget upgrade Ollama.Ollama"
}

if (-not $Model) {
    # Ask the pipeline itself which model it uses, rather than scraping the source.
    $Model = (Invoke-Native { & $VenvPython -c "import process_inbox; print(process_inbox.MODEL)" }).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Model) { throw "Couldn't read MODEL from process_inbox.py; pass -Model explicitly." }
    Info "Model from process_inbox.py: $Model"
}

if ($SkipModelPull) {
    Info "Skipping model pull (-SkipModelPull)."
} else {
    Step "Pulling model $Model (large download on first run)"
    Invoke-Native { ollama pull $Model }
    if ($LASTEXITCODE -ne 0) { throw "ollama pull $Model failed" }
}

# ---- Desktop app -------------------------------------------------------------
Step "Desktop app (app/)"
if (-not (Have node)) {
    Install-WithWinget "OpenJS.NodeJS.LTS" "Node.js LTS"
}
if (Have npm) {
    Push-Location (Join-Path $RepoDir "app")
    Info "Installing Electron (first run downloads ~100MB)..."
    Invoke-Native { npm install --no-fund --no-audit }
    if ($LASTEXITCODE -ne 0) { Warn "npm install failed; the app won't start until it succeeds." }
    Pop-Location
    Info "Start it with DoTheReading.cmd (or: npm start --prefix app)."
} else {
    Warn "Node.js not found. Install it and run 'npm install' in app\ to use the desktop app."
}

# ---- Folders -----------------------------------------------------------------
Step "Folders"
foreach ($sub in "inbox", "library", "questions") {
    New-Item -ItemType Directory -Force -Path (Join-Path $PaperStudyDir $sub) | Out-Null
}
Info "$PaperStudyDir\{inbox,library,questions}"

# ---- Scheduled task ----------------------------------------------------------
if ($SkipTask) {
    Step "Skipping scheduled task (-SkipTask)."
} else {
    Step "Scheduled task '$TaskName' (daily at $Time, wakes the computer)"
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        $old = $existing.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }
        Info "Replacing existing task, which ran: $old"
    }
    $action = New-ScheduledTaskAction -Execute $VenvPython `
        -Argument ('"' + (Join-Path $RepoDir "process_inbox.py") + '"') -WorkingDirectory $RepoDir
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    # -WakeToRun is the "Wake the computer to run this task" checkbox that
    # schtasks can't set.
    $settings = New-ScheduledTaskSettingsSet -WakeToRun
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Generate study questions for new PDFs in ~/PaperStudy/inbox" -Force | Out-Null
    Info "Registered."
}

Step "Done"
Info "Drop PDFs in:   $PaperStudyDir\inbox"
Info "Run now:        & `"$VenvPython`" `"$RepoDir\process_inbox.py`""
Info "Morning quiz:   DoTheReading.cmd  (or & `"$VenvPython`" `"$RepoDir\quiz_me.py`" for the CLI)"
