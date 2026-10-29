# LabLog をログオン時に自動で起動するよう設定し、いま起動する。
# スタートアップとスタートメニューにショートカットを作るだけなので、管理者権限は不要。
# 元に戻すときは uninstall_autostart.ps1 を実行する。

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "LabLog.pyw"

$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $py = (Get-Command python.exe -ErrorAction Stop).Source
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
}
if (-not (Test-Path $pyw)) { throw "pythonw.exe が見つかりません。Python をインストールしてください。" }

$shell = New-Object -ComObject WScript.Shell

# ログオン時：ブラウザは開かずに裏で起動
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "LabLog.lnk"
$lnk = $shell.CreateShortcut($startup)
$lnk.TargetPath = $pyw
$lnk.Arguments = "`"$script`" --no-browser"
$lnk.WorkingDirectory = $here
$lnk.Description = "LabLog（研究室の入退室と作業の記録）"
$lnk.Save()

# スタートメニュー：開くとダッシュボードが表示される（起動していなければ起動もする）
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "LabLog.lnk"
$lnk = $shell.CreateShortcut($menu)
$lnk.TargetPath = $pyw
$lnk.Arguments = "`"$script`""
$lnk.WorkingDirectory = $here
$lnk.Description = "LabLog のダッシュボードを開く"
$lnk.Save()

Start-Process -FilePath $pyw -ArgumentList "`"$script`"" -WorkingDirectory $here
Write-Host "自動起動を設定しました。"
Write-Host "  スタートアップ: $startup"
Write-Host "  スタートメニュー: $menu"
Write-Host "ダッシュボード: http://127.0.0.1:8765/"
