# LabLog をこの PC に入れる（管理者の権限は要らない）。ZIP を展開したフォルダの setup.cmd から実行する。
#   1. Python 3.11 以降があるか確かめる（無ければ winget で入れるかを聞く）
#   2. %LOCALAPPDATA%\Programs\LabLog\versions\<バージョン> にアプリを写し、起動用の LabLog.pyw と version.json を置く
#   3. スタートメニューとスタートアップにショートカットを作る（ログオンしたときに自動で起動する）
#   4. LabLog を起動する（初めてなら初期設定の画面が開く）
# すでに入っていれば、新しいバージョンに入れ替える。記録と設定は %LOCALAPPDATA%\LabLog にあり、そのまま引き継ぐ。
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\LabLog"),
    [string]$ShortcutDir = "",  # テスト用：ショートカットを作るフォルダ（空ならスタートメニューとスタートアップ）
    [switch]$NoStart,           # テスト用：最後に LabLog を起動しない
    [switch]$Yes                # テスト用：確認を出さずに進める
)
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path

function Ask($question) {
    if ($Yes) { return $true }
    return (Read-Host "$question [Y/N]") -match '^[Yy]'
}

function Get-PythonVersion($exe) {
    try {
        $out = & $exe -c "import sys, tkinter; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out" -match '^(\d+)\.(\d+)$') { return [version]"$out" }
    } catch {}
    return $null
}

# 使える Python を探す（3.11 以降で、フォルダ選択の画面に使う tkinter があるもの）。
# Microsoft Store を開くだけの python.exe（WindowsApps）は除く
function Find-Python {
    $candidates = @()
    $candidates += Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $candidates += (& $py.Source -3 -c "import sys; print(sys.executable)" 2>$null) }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
    $candidates += Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
    foreach ($exe in ($candidates | Where-Object { $_ -and $_ -notlike "*\WindowsApps\*" } | Select-Object -Unique)) {
        $v = Get-PythonVersion $exe
        $w = Join-Path (Split-Path $exe) "pythonw.exe"
        if ($v -and $v -ge [version]"3.11" -and (Test-Path $w)) { return @{ Python = $exe; Pythonw = $w; Version = $v } }
    }
    return $null
}

$init = [IO.File]::ReadAllText((Join-Path $src "lablog\__init__.py"))
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "lablog\__init__.py にバージョン番号が見つかりません。ZIP を展開したフォルダの setup.cmd から実行してください。"
}
$version = $Matches[1]
Write-Host "LabLog $version をインストールします（$InstallDir）"
Write-Host ""
Write-Host "ご注意：LabLog は AI で作成したアプリです。動作や安全性は保証できません。"
if (-not (Ask "インストールを続けますか？")) { return }

$found = Find-Python
if (-not $found) {
    Write-Host "Python 3.11 以降が見つかりません。"
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "winget が無いため、https://www.python.org から Python 3.13 を入れてから、もう一度実行してください。"
    }
    if (-not (Ask "Python 3.13（Python Software Foundation のライセンス）を winget でこのユーザーにインストールしますか？")) {
        throw "Python を入れてから、もう一度実行してください。"
    }
    winget install --id Python.Python.3.13 --exact --scope user --silent --accept-source-agreements --accept-package-agreements
    $found = Find-Python
    if (-not $found) {
        throw "Python をインストールできませんでした。https://www.python.org から入れてから、もう一度実行してください。"
    }
}
Write-Host "Python $($found.Version)：$($found.Python)"

# このインストール先から動いている LabLog を止め、終わるまで待つ（ファイルを入れ替えるため）。
# Python の名前は入れ方で違う（pythonw.exe・pythonw3.13.exe など）。パスの大文字・小文字は区別しない
$running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($InstallDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
foreach ($p in $running) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "動いている LabLog を止めました（PID $($p.ProcessId)）"
}
foreach ($p in $running) { Wait-Process -Id $p.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }

# アプリをバージョンごとのフォルダに写す（いったん .tmp に写してから名前を変える）
$versions = Join-Path $InstallDir "versions"
$dest = Join-Path $versions $version
$tmp = "$dest.tmp"
New-Item -ItemType Directory -Force $versions | Out-Null
foreach ($p in @($tmp, $dest)) { if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
New-Item -ItemType Directory $tmp | Out-Null
foreach ($name in @("LabLog.pyw", "README.md", "LICENSE", "lablog")) {
    $p = Join-Path $src $name
    if (Test-Path $p) { Copy-Item -Recurse -Force $p $tmp }
}
Get-ChildItem $tmp -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Rename-Item $tmp (Split-Path $dest -Leaf)
Copy-Item -Force (Join-Path $src "launcher.pyw") (Join-Path $InstallDir "LabLog.pyw")
foreach ($name in @("uninstall.cmd", "uninstall.ps1")) { Copy-Item -Force (Join-Path $src $name) $InstallDir }
Get-ChildItem $InstallDir -Recurse -File | Unblock-File  # インターネットから取った印を外す

# 次に起動するバージョンを決める（いま入っているバージョンは「前のバージョン」として残し、更新に失敗したときに戻せるようにする）
$vfile = Join-Path $InstallDir "version.json"
$previous = $null
if (Test-Path $vfile) {
    try {
        $old = [IO.File]::ReadAllText($vfile) | ConvertFrom-Json
        if ($old.current -and $old.current -ne $version) { $previous = $old.current }
    } catch {}
}
$json = @{ current = $version; previous = $previous } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($vfile, $json, (New-Object Text.UTF8Encoding $false))  # Python が読めるよう BOM なしで
Get-ChildItem $versions -Directory | Where-Object { $_.Name -ne $version -and $_.Name -ne $previous } |
    Remove-Item -Recurse -Force

# ショートカット：ログオン時はブラウザを開かずに裏で起動、スタートメニューからはダッシュボードを開く
$launcher = Join-Path $InstallDir "LabLog.pyw"
$shell = New-Object -ComObject WScript.Shell
if ($ShortcutDir) {
    New-Item -ItemType Directory -Force $ShortcutDir | Out-Null
    $startup = Join-Path $ShortcutDir "LabLog (startup).lnk"
    $menu = Join-Path $ShortcutDir "LabLog.lnk"
} else {
    $startup = Join-Path ([Environment]::GetFolderPath("Startup")) "LabLog.lnk"
    $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "LabLog.lnk"
}
foreach ($item in @(@($startup, "`"$launcher`" --no-browser", "LabLog（研究室の入退室と作業の記録）"),
                    @($menu, "`"$launcher`"", "LabLog のダッシュボードを開く"))) {
    $lnk = $shell.CreateShortcut($item[0])
    $lnk.TargetPath = $found.Pythonw
    $lnk.Arguments = $item[1]
    $lnk.WorkingDirectory = $env:USERPROFILE
    $lnk.Description = $item[2]
    $lnk.Save()
}

if (-not $NoStart) {
    Start-Process -FilePath $found.Pythonw -ArgumentList "`"$launcher`"" -WorkingDirectory $env:USERPROFILE
    Write-Host "LabLog を起動しました。ブラウザで画面が開きます（初めてなら初期設定の画面）。"
}
Write-Host "インストールしました：LabLog $version"
Write-Host "  スタートメニュー：$menu"
Write-Host "  自動起動：$startup"
Write-Host "  アンインストール：$InstallDir\uninstall.cmd"
