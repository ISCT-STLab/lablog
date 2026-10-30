# LabLog をこの PC から外す（setup.ps1 で入れたもの）。日報の AI（Ollama）と、記録したデータも消すかどうかは聞く（初めは残す）。
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\LabLog"),
    [string]$ShortcutDir = "",  # テスト用：ショートカットのあるフォルダ（空ならスタートメニューとスタートアップ）
    [switch]$Yes,               # テスト用：アプリを外すかの確認を出さない（AI と記録は消さない）
    [switch]$DeleteAI,          # 日報の AI も確認なしで消す
    [switch]$DeleteData,        # 記録したデータも確認なしで消す
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA "LabLog"),               # テスト用：記録したデータの場所
    [string]$OllamaDir = (Join-Path $env:LOCALAPPDATA "Programs\Ollama"),   # テスト用：Ollama の場所
    [string]$OllamaHome = (Join-Path $env:USERPROFILE ".ollama"),            # テスト用：Ollama のモデルなどの場所
    [string]$BackupTo = ""      # テスト用：記録を消す前のバックアップの保存先（指定すると確認なしで保存する）
)
$ErrorActionPreference = "Stop"
Set-Location $env:TEMP  # インストール先のフォルダを消せるように、そこから離れておく
$data = $DataDir

if (-not $Yes -and (Read-Host "LabLog をこの PC から外しますか？ [Y/N]") -notmatch '^[Yy]') { return }

# このインストール先から動いている LabLog を止め、終わるまで待つ（止まりきる前に消すと、使用中で消せない）。
# Python の名前は入れ方で違う（pythonw.exe・pythonw3.13.exe など）。パスの大文字・小文字は区別しない
$running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($InstallDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
foreach ($p in $running) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "LabLog を終了しました（PID $($p.ProcessId)）"
}
foreach ($p in $running) { Wait-Process -Id $p.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }

# フォルダを消す。使用中のことがあるので何度か試す。
# keep のファイル（アンインストールのファイル）は、フォルダごと消せると分かってから消す（消せなかったときに、もう一度実行できるように）。
# 消せるかどうかは、フォルダの名前を変えられるかで確かめる（ほかのアプリが使っていると変えられない）
function Remove-Folder($path, $keep = @()) {
    $gone = "$path.removing"
    if (Test-Path $gone) { Remove-Item -Recurse -Force $gone -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Get-ChildItem $path -Force | Where-Object { $keep -notcontains $_.Name } | Remove-Item -Recurse -Force -ErrorAction Stop
            Rename-Item $path (Split-Path $gone -Leaf) -ErrorAction Stop
            Remove-Item -Recurse -Force $gone -ErrorAction Stop
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
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
    if (-not (Remove-Folder $InstallDir @("uninstall.cmd", "uninstall.ps1"))) {
        Write-Host "$InstallDir を消せませんでした（使用中）。"
        Write-Host "PC を再起動してから、もう一度 uninstall.cmd を実行してください。"
        exit 1
    }
    Write-Host "削除しました：$InstallDir"
}

# ---- 日報の AI（Ollama）
# LabLog が入れたもの（Ollama・ダウンロードしたモデル）は ai.json にある（lablog/aisetup.py）。
# ほかで Ollama を使っていることもあるので、消すかどうかは聞く。もとから入っていた Ollama は残し、LabLog が入れたモデルだけを消す
function Stop-InFolder($dir) {
    $procs = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) })
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    $procs | ForEach-Object { $_.WaitForExit(15000) | Out-Null }
}
$ollama = Join-Path $OllamaDir "ollama.exe"
if (Test-Path $ollama) {
    $ai = $null
    try { $ai = [IO.File]::ReadAllText((Join-Path $data "ai.json")) | ConvertFrom-Json } catch {}
    $models = @(if ($ai -and $ai.models) { $ai.models })
    if (-not $ai -or $ai.ollama) {
        # LabLog が入れた Ollama（前のバージョンの LabLog は記録を残さないので、記録が無いときも聞く）
        $what = if ($ai) { "LabLog が入れた日報の AI（Ollama とモデル）" } else { "日報の AI（Ollama とモデル）" }
        if ($DeleteAI -or (-not $Yes -and (Read-Host "$what も消しますか？ ほかで Ollama を使っていれば N [Y/N]") -match '^[Yy]')) {
            Stop-InFolder $OllamaDir
            $unins = Join-Path $OllamaDir "unins000.exe"
            if (Test-Path $unins) {
                Start-Process $unins -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -WindowStyle Hidden
                # アンインストーラーは自分の写しを動かしてすぐ戻るので、消え終わるのを待つ
                for ($i = 0; $i -lt 30 -and (Test-Path $ollama); $i++) { Start-Sleep -Seconds 1 }
            }
            if (Test-Path $OllamaDir) { Remove-Folder $OllamaDir | Out-Null }
            if (Test-Path $OllamaHome) { Remove-Folder $OllamaHome | Out-Null }
            Write-Host "日報の AI（Ollama とモデル）を消しました"
        }
    } elseif ($models.Count) {
        if ($DeleteAI -or (-not $Yes -and (Read-Host "LabLog がダウンロードした AI のモデル（$($models -join '、')）を消しますか？ [Y/N]") -match '^[Yy]')) {
            # モデルを消すには Ollama が動いている必要がある。止まっていれば、消すあいだだけ動かす
            $server = $null
            if (-not (Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($OllamaDir, [StringComparison]::OrdinalIgnoreCase) })) {
                $server = Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden -PassThru
                Start-Sleep -Seconds 5
            }
            foreach ($m in $models) { try { & $ollama rm $m 2>&1 | Out-Null } catch {} }
            if ($server) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
            Write-Host "AI のモデルを消しました：$($models -join '、')"
        }
    }
}

# ---- 記録したデータ
if (Test-Path $data) {
    $delete = $DeleteData -or (-not $Yes -and
        (Read-Host "記録したデータ（$data）も消しますか？ 消すと元に戻せません [Y/N]") -match '^[Yy]')
    if ($delete) {
        # 消す前に、記録を ZIP で残すか聞く（入れ直したときに、初期設定の画面の「バックアップから戻す」で戻せる。lablog/backup.py と同じ形）
        $save = $BackupTo -or (-not $Yes -and
            (Read-Host "消す前に、記録のバックアップをデスクトップに保存しますか？（入れ直したときに戻せます） [Y/N]") -match '^[Yy]')
        if ($save) {
            $dest = if ($BackupTo) { $BackupTo } else {
                Join-Path ([Environment]::GetFolderPath("Desktop")) "LabLog-backup-$(Get-Date -Format yyyyMMdd-HHmm).zip" }
            $items = @("lablog.db", "lablog.db-wal", "settings.json", "ai.json") | ForEach-Object { Join-Path $data $_ } |
                Where-Object { Test-Path $_ }
            Compress-Archive -Path $items -DestinationPath $dest -Force
            Write-Host "バックアップを保存しました：$dest"
        }
        if (-not (Remove-Folder $data)) {
            Write-Host "記録したデータ（$data）を消せませんでした（使用中）。PC を再起動してから消してください。"
            exit 1
        }
        Write-Host "記録したデータを消しました"
    } else {
        Write-Host "記録したデータは残してあります：$data"
    }
}
Write-Host "LabLog を外しました"
