param(
    [string]$RuntimeSourcePath = "",
    [switch]$SkipRuntimeCopy
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

function Copy-Runtime {
    param(
        [string]$Source,
        [string]$Destination
    )

    $srcBin = Join-Path $Source "bin"
    $srcLib = Join-Path $Source "lib"
    $srcUx = Join-Path $srcBin "uxplay.exe"

    if (-not (Test-Path $srcUx)) {
        throw "Runtime source is missing '$srcUx'. Provide a valid -RuntimeSourcePath."
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $Destination "bin") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Destination "lib") | Out-Null

    Write-Host "Copying runtime bin/..."
    robocopy $srcBin (Join-Path $Destination "bin") /E /R:1 /W:1 | Out-Null

    Write-Host "Copying runtime lib/..."
    robocopy $srcLib (Join-Path $Destination "lib") /E /R:1 /W:1 | Out-Null

    $srcUxplayIcon = Join-Path $Source "uxplay.ico"
    if (Test-Path $srcUxplayIcon) {
        Copy-Item $srcUxplayIcon (Join-Path $Destination "uxplay.ico") -Force
    } else {
        Write-Warning "Runtime source does not contain uxplay.ico."
    }
}

function Resolve-IconPath {
    param([string]$RepoRoot)
    $candidates = @(
        (Join-Path $RepoRoot "uxplay.ico")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Assert-Command "python"
Stop-RunningUxPlayProcesses

Write-Host "Ensuring PyInstaller dependencies..."
python -m pip install --upgrade pip
python -m pip install pyinstaller pystray pillow

Write-Host "Cleaning previous artifacts..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\build
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\dist
Remove-Item -Force -ErrorAction SilentlyContinue .\uxplay-windows.spec

Write-Host "Building tray executable..."
$iconPath = Resolve-IconPath -RepoRoot $repoRoot

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "-y",
    "--noconsole",
    "--onedir",
    "--clean",
    "--name", "uxplay-windows"
)

if ($iconPath) {
    $pyinstallerArgs += @("--icon", $iconPath)
} else {
    throw "uxplay.ico was not found in repository root."
}

$pyinstallerArgs += "tray.py"

& python @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$internalDir = Join-Path $repoRoot "dist\uxplay-windows\_internal"
if (-not $SkipRuntimeCopy) {
    if ([string]::IsNullOrWhiteSpace($RuntimeSourcePath)) {
        Write-Warning "No -RuntimeSourcePath provided. Runtime copy skipped."
        Write-Host "Use -RuntimeSourcePath '<path-to-_internal>' to add uxplay.exe + libs."
        Write-Host "Note: FDH2/UxPlay latest release currently does not provide ready-to-run Windows runtime assets."
    } else {
        Write-Host "Adding runtime from: $RuntimeSourcePath"
        Copy-Runtime -Source $RuntimeSourcePath -Destination $internalDir
    }
} else {
    Write-Warning "Runtime copy skipped. AirPlay will not work unless uxplay.exe + libs are provided."
}

$bonjour = Get-Service -Name "Bonjour Service" -ErrorAction SilentlyContinue
if (-not $bonjour) {
    Write-Warning "Bonjour Service is not installed. Download: https://download.info.apple.com/Mac_OS_X/061-8098.20100603.gthyu/BonjourPSSetup.exe"
}

$uxplayExe = Join-Path $internalDir "bin\uxplay.exe"
if (Test-Path $uxplayExe) {
    Write-Host "Build complete. Portable app: dist\uxplay-windows\uxplay-windows.exe"
    Write-Host "Runtime found: $uxplayExe"
} else {
    Write-Warning "Build complete, but runtime missing at: $uxplayExe"
}

Write-Host "Optional firewall rule (run in elevated PowerShell):"
Write-Host ("netsh advfirewall firewall add rule name=\"uxplay-windows-local-dist\" dir=in action=allow program=\"{0}\" enable=yes profile=any" -f $uxplayExe)
