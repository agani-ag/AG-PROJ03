# Build and release j6.exe - all local, no CI.
#
#   cd desktop
#   .\build.ps1                 # just build
#   .\build.ps1 -Tag j6-v1.0.0  # build, hash and create the GitHub release
#
# The EXE is never committed: release assets live outside git history, so the repository stays
# the size it is however many builds you cut.
param([string]$Tag = "")

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot

# The build runs in desktop\.venv rather than whatever python is first on PATH. pywebview's
# Windows backend needs pythonnet, and the project itself is written against a newer Python than
# pythonnet has wheels for, so the client is built on 3.13. The venv is gitignored.
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Making the build venv (Python 3.13)..."
    py -3.13 -m venv (Join-Path $PSScriptRoot ".venv")
}
# pip and PyInstaller both log to stderr, and under Windows PowerShell a stderr line from a
# native program with ErrorActionPreference = Stop kills the script mid-build. So the exit code
# is what decides, not the noise.
$ErrorActionPreference = "Continue"
& $py -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)." }
& $py -m PyInstaller --clean --noconfirm j6.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)." }
$ErrorActionPreference = "Stop"

$exe = Join-Path $PSScriptRoot "dist\j6.exe"
if (-not (Test-Path $exe)) { throw "No dist\j6.exe - the build failed." }

$size = "{0:N1} MB" -f ((Get-Item $exe).Length / 1MB)
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
Write-Host ""
Write-Host "Built dist\j6.exe  ($size)"
Write-Host "SHA256 $hash"
"$hash  j6.exe" | Out-File -Encoding utf8 (Join-Path $PSScriptRoot "dist\SHA256SUMS.txt")

if ($Tag) {
    # Say out loud which account is about to publish: `gh` remembers a login, and a release is
    # public and attributed. Check the name before it goes out, not after.
    $ErrorActionPreference = "Continue"
    $who = (gh api user --jq .login)
    Write-Host ""
    Write-Host "Publishing $Tag to $(gh repo view --json nameWithOwner --jq .nameWithOwner) as $who"
    gh release create $Tag $exe (Join-Path $PSScriptRoot "dist\SHA256SUMS.txt") `
        --title "J6 for Windows $Tag" `
        --notes "Portable. Double-click for the window, ``j6.exe --cli`` for the terminal. Settings live in %APPDATA%\J6\config.json. Unsigned, so SmartScreen will warn the first few times: More info -> Run anyway. SHA256 $hash"
    Write-Host ""
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed ($LASTEXITCODE)." }
    Write-Host "Released. Permanent link:"
    Write-Host "  https://github.com/agani-ag/AG-PROJ03/releases/latest/download/j6.exe"
}
Pop-Location
