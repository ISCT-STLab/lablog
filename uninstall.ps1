# LabLog をこの PC から外す（setup.ps1 で入れたもの）。記録したデータも消すかどうかは聞く（初めは残す）。
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\LabLog"),
    [string]$ShortcutDir = "",  # テスト用：ショートカットのあるフォルダ（空ならスタートメニューとスタートアップ）
    [switch]$Yes,               # テスト用：アプリを外すかの確認を出さない（記録は消さない）
    [switch]$DeleteData,        # 記録したデータも確認なしで消す
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA "LabLog")  # テスト用：記録したデータの場所
)
$ErrorActionPreference = "Stop"
Set-Location $env:TEMP  # インストール先のフォルダを消せるように、そこから離れておく
$data = $DataDir

if (-not $Yes -and (Read-Host "LabLog をこの PC から外しますか？ [Y/N]") -notmatch '^[Yy]') { return }

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($InstallDir) } |
    ForEach-Object {
        Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null
        Write-Host "LabLog を終了しました（PID $($_.ProcessId)）"
    }

# このインストール先を起動するショートカットだけを消す（開発用に別の場所から動かしているものは残す）
$shell = New-Object -ComObject WScript.Shell
$dirs = if ($ShortcutDir) { @($ShortcutDir) } else {
    @([Environment]::GetFolderPath("Startup"), [Environment]::GetFolderPath("Programs"))
}
foreach ($dir in $dirs) {
    Get-ChildItem $dir -Filter "LabLog*.lnk" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($shell.CreateShortcut($_.FullName).Arguments.Contains($InstallDir)) {
            Remove-Item $_.FullName
            Write-Host "削除しました：$($_.FullName)"
        }
    }
}

if (Test-Path $InstallDir) {
    Remove-Item -Recurse -Force $InstallDir
    Write-Host "削除しました：$InstallDir"
}

if (Test-Path $data) {
    $delete = $DeleteData -or (-not $Yes -and
        (Read-Host "記録したデータ（$data）も消しますか？ 消すと元に戻せません [Y/N]") -match '^[Yy]')
    if ($delete) {
        Remove-Item -Recurse -Force $data
        Write-Host "記録したデータを消しました"
    } else {
        Write-Host "記録したデータは残してあります：$data"
    }
}
Write-Host "LabLog を外しました"
