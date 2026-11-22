param(
    [Parameter(Mandatory=$true)][string]$Png,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [switch]$Grayscale
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw 'Choose a new icon output directory.' }
$source = (Resolve-Path -LiteralPath $Png).Path
$original = [Drawing.Bitmap]::FromFile($source)
try {
    if ($original.Width -ne $original.Height -or $original.GetPixel(0, 0).A -ne 0) {
        throw 'The supplied square Staging artwork must have a transparent background.'
    }
    $sizes = @(16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
    $frames = [ordered]@{}
    foreach ($size in $sizes) {
        $bitmap = [Drawing.Bitmap]::new($size, $size, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $g = [Drawing.Graphics]::FromImage($bitmap)
        $data = [IO.MemoryStream]::new()
        try {
            $g.CompositingMode = [Drawing.Drawing2D.CompositingMode]::SourceCopy
            $g.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
            $g.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::Half
            $g.DrawImage($original, [Drawing.Rectangle]::new(0, 0, $size, $size))
            if ($bitmap.GetPixel(0, 0).A -ne 0) { throw "Staging icon lost transparency at $size pixels." }
            if ($Grayscale) {
                for ($y = 0; $y -lt $size; $y++) {
                    for ($x = 0; $x -lt $size; $x++) {
                        $pixel = $bitmap.GetPixel($x, $y)
                        if ($pixel.A -eq 0) { continue }
                        $gray = [int][Math]::Floor((299*$pixel.R + 587*$pixel.G + 114*$pixel.B + 500)/1000)
                        $bitmap.SetPixel($x, $y, [Drawing.Color]::FromArgb($pixel.A, $gray, $gray, $gray))
                    }
                }
            }
            $bitmap.Save($data, [Drawing.Imaging.ImageFormat]::Png)
            $frames.Add($size.ToString(), $data.ToArray())
        } finally {
            $data.Dispose()
            $g.Dispose()
            $bitmap.Dispose()
        }
    }
    [IO.Directory]::CreateDirectory($output) | Out-Null
    [IO.File]::Copy($source, (Join-Path $output 'coopplus-staging.png'))
    [IO.File]::WriteAllBytes((Join-Path $output 'window.png'), $frames['64'])
    $icon = [IO.File]::Open((Join-Path $output 'icon.ico'), [IO.FileMode]::CreateNew)
    $writer = [IO.BinaryWriter]::new($icon)
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]$sizes.Count)
        $offset = 6 + 16 * $sizes.Count
        foreach ($entry in $frames.GetEnumerator()) {
            $dimension = [byte]([int]$entry.Key % 256)
            $writer.Write($dimension)
            $writer.Write($dimension)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$entry.Value.Length)
            $writer.Write([uint32]$offset)
            $offset += $entry.Value.Length
        }
        foreach ($entry in $frames.GetEnumerator()) { $writer.Write([byte[]]$entry.Value) }
    } finally { $writer.Dispose() }
    Write-Output ('Prepared transparent Windows icon sizes ' + ($sizes -join ', ') + ' and the 64-pixel window PNG.')
} finally { $original.Dispose() }
