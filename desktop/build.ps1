# Build and release j6.exe — all local, no CI.
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

python -m pip install -r requirements.txt --quiet
pyinstaller --clean --noconfirm j6.spec

$exe = Join-Path $PSScriptRoot "dist\j6.exe"
if (-not (Test-Path $exe)) { throw "No dist\j6.exe — the build failed." }

$size = "{0:N1} MB" -f ((Get-Item $exe).Length / 1MB)
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
Write-Host ""
Write-Host "Built dist\j6.exe  ($size)"
Write-Host "SHA256 $hash"
"$hash  j6.exe" | Out-File -Encoding utf8 (Join-Path $PSScriptRoot "dist\SHA256SUMS.txt")

if ($Tag) {
    gh release create $Tag $exe (Join-Path $PSScriptRoot "dist\SHA256SUMS.txt") `
        --title "J6 for Windows $Tag" `
        --notes "Portable. Double-click for the window, ``j6.exe --cli`` for the terminal. Settings live in %APPDATA%\J6\config.json. Unsigned, so SmartScreen will warn the first few times: More info -> Run anyway. SHA256 $hash"
    Write-Host ""
    Write-Host "Released. Permanent link:"
    Write-Host "  https://github.com/agani-ag/AG-PROJ03/releases/latest/download/j6.exe"
}
Pop-Location
