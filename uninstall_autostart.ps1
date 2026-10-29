# LabLog の自動起動をやめ、動いている LabLog を終了する。記録したデータは消さない。

$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "LabLog.lnk"
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "LabLog.lnk"
foreach ($p in @($startup, $menu)) {
    if (Test-Path $p) { Remove-Item $p; Write-Host "削除しました: $p" }
}

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*LabLog.pyw*" } |
    ForEach-Object {
        Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null
        Write-Host "LabLog を終了しました（PID $($_.ProcessId)）"
    }

$data = Join-Path $env:LOCALAPPDATA "LabLog"
Write-Host "記録データは残っています: $data （不要なら手動で削除してください）"
