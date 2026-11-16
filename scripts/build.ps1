[CmdletBinding()]
param(
    [string]$Python = '',
    [string]$Iscc = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Python) { $Python = Join-Path $root '.venv\Scripts\python.exe' }
$Python = (Get-Command -Name $Python -CommandType Application).Source

if (-not $Iscc) {
    $command = Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($command) {
        $Iscc = $command.Source
    } else {
        $Iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    }
}
$Iscc = (Get-Command -Name $Iscc -CommandType Application).Source
$innoDir = Split-Path -Parent $Iscc
if (-not (Test-Path -LiteralPath (Join-Path $innoDir 'Languages\Korean.isl') -PathType Leaf)) {
    throw 'Inno Setup 6 with the Korean language file is required. Pass its compiler path with -Iscc.'
}

Push-Location -LiteralPath $root
try {
    $version = & $Python -c 'import struct, sys, tomllib; assert sys.version_info[:2] == (3, 12) and struct.calcsize("P") == 8; print(tomllib.loads(open("pyproject.toml", encoding="utf-8").read())["project"]["version"])'
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') {
        throw 'A 64-bit Python 3.12 interpreter and a numeric major.minor.patch version are required.'
    }
    if ($env:GITHUB_REF_TYPE -eq 'tag' -and $env:GITHUB_REF_NAME -ne "v$version") {
        throw "The Git tag $env:GITHUB_REF_NAME does not match application version $version."
    }

    & $Python scripts/fetch_dependencies.py --sources
    if ($LASTEXITCODE -ne 0) { throw 'Release dependency verification failed.' }

    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
        --name JjalMaker --paths . --specpath build --workpath build/pyinstaller --distpath dist `
        packaging/launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

    & $Python scripts/stage_release.py (Join-Path $innoDir 'license.txt')
    if ($LASTEXITCODE -ne 0) { throw 'The release payload and notices could not be staged.' }

    & $Iscc "/DAppVersion=$version" '/Qp' 'packaging/jjal-maker.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }

    $releaseDir = Join-Path $root 'dist\release'
    $installer = Join-Path $releaseDir "JjalMaker-$version-win-x64-setup.exe"
    $assets = @(Get-Item -LiteralPath $installer) + @(Get-ChildItem -LiteralPath $releaseDir -Filter '*.tar.xz' -File)
    $lines = foreach ($asset in $assets) {
        $hash = (Get-FileHash -LiteralPath $asset.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($asset.Name)"
    }
    $lines | Set-Content -LiteralPath (Join-Path $releaseDir 'SHA256SUMS') -Encoding ascii
    Write-Host "Built $installer"
} finally {
    Pop-Location
}
