param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeSourcePath,
    [switch]$SkipRuntimeValidation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Stop-RunningUxPlayProcesses {
    $names = @("uxplay-windows", "uxplay")
    foreach ($name in $names) {
        $procs = Get-Process -Name $name -ErrorAction SilentlyContinue
        if ($procs) {
            Write-Host "Stopping running process: $name"
            $procs | Stop-Process -Force
        }
    }
}

function Find-Iscc {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

function Copy-RuntimePayload {
    param(
        [string]$Source,
        [string]$Destination
    )

    $srcBin = Join-Path $Source "bin"
    $srcLib = Join-Path $Source "lib"
    $srcUx = Join-Path $srcBin "uxplay.exe"

    if (-not (Test-Path $srcUx)) {
        throw "Runtime source is missing '$srcUx'."
    }

    if (-not (Test-Path (Join-Path $Source "uxplay.ico"))) {
        throw "Runtime source is missing 'uxplay.ico'."
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $Destination "bin") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Destination "lib") | Out-Null

    robocopy $srcBin (Join-Path $Destination "bin") /E /R:1 /W:1 | Out-Null
    robocopy $srcLib (Join-Path $Destination "lib") /E /R:1 /W:1 | Out-Null
    Copy-Item (Join-Path $Source "uxplay.ico") (Join-Path $Destination "uxplay.ico") -Force
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Assert-Command "python"
$iscc = Find-Iscc
if (-not $iscc) {
    throw "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6 first."
}

if (-not (Test-Path (Join-Path $repoRoot "uxplay.ico"))) {
    throw "uxplay.ico was not found in repository root."
}

Stop-RunningUxPlayProcesses

Write-Host "Installing Python build dependencies..."
python -m pip install --upgrade pip
python -m pip install pyinstaller pystray pillow

Write-Host "Cleaning build artifacts..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\build
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\dist
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\Output
Remove-Item -Force -ErrorAction SilentlyContinue .\uxplay-windows.spec

Write-Host "Building tray executable..."
python -m PyInstaller -y --noconsole --onedir --clean --name uxplay-windows --icon=uxplay.ico tray.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$internalDir = Join-Path $repoRoot "dist\uxplay-windows\_internal"
Write-Host "Copying runtime payload from: $RuntimeSourcePath"
Copy-RuntimePayload -Source $RuntimeSourcePath -Destination $internalDir

if (-not $SkipRuntimeValidation) {
    $required = @(
        (Join-Path $internalDir "bin\uxplay.exe"),
        (Join-Path $internalDir "uxplay.ico")
    )
    foreach ($path in $required) {
        if (-not (Test-Path $path)) {
            throw "Runtime validation failed. Missing: $path"
        }
    }
}

Write-Host "Compiling installer with Inno Setup..."
& $iscc (Join-Path $repoRoot "script.iss")
if ($LASTEXITCODE -ne 0) {
    throw "ISCC failed with exit code $LASTEXITCODE"
}

$setupPath = Join-Path $repoRoot "Output\uxplay-windows.exe"
if (-not (Test-Path $setupPath)) {
    throw "Expected setup output not found: $setupPath"
}

Write-Host "Setup build completed: $setupPath"
