# 画像（BGRA、上から下）を標準入力で受け取り、Windows 標準の文字認識（OCR）で読んだ行を JSON で返す。
# 入力：1 行目「幅 高さ」、2 行目以降に画素の base64。画像はファイルに保存しない。
# 出力：[{"t": 行の文字, "x": 行の中心の横位置（0〜1）, "y": 行の上端（0〜1）}, ...]
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1

$size = [Console]::In.ReadLine() -split ' '
$w = [int]$size[0]; $h = [int]$size[1]
$bytes = [Convert]::FromBase64String([Console]::In.ReadToEnd())
$buffer = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::AsBuffer($bytes)
$bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::CreateCopyFromBuffer(
    $buffer, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, $w, $h,
    [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ja'))
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
$task = $asTask.MakeGenericMethod([Windows.Media.Ocr.OcrResult]).Invoke($null, @($engine.RecognizeAsync($bitmap)))
$null = $task.Wait(60000)

$lines = foreach ($line in $task.Result.Lines) {
    $rects = $line.Words | ForEach-Object { $_.BoundingRect }
    $left = ($rects | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
    $right = ($rects | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
    $top = ($rects | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
    [ordered]@{ t = $line.Text; x = [math]::Round(($left + $right) / 2 / $w, 3); y = [math]::Round($top / $h, 3) }
}
ConvertTo-Json -InputObject @($lines) -Compress
