# 起点读书 RPC 测试脚本 (Windows PowerShell)
# 使用方法: .\test_rpc.ps1

$ADB = "D:\123pan\Downloads\一加Ace6\adb命令行\adb.exe"
$Device = "-s 3B15BJ00GZL00000"

# 建立 ADB forward
Write-Host "Setting up ADB forward..."
& $ADB $Device forward tcp:12345 localabstract:qdhook_rpc 2>$null

Start-Sleep -Milliseconds 500

# 测试连接
Write-Host "Testing RPC connection..."
try {
    $tcpClient = New-Object System.Net.Sockets.TcpClient
    $tcpClient.Connect("127.0.0.1", 12345)
    $stream = $tcpClient.GetStream()
    $writer = New-Object System.IO.StreamWriter($stream)
    $reader = New-Object System.IO.StreamReader($stream)

    # Ping
    $writer.WriteLine('{"cmd":"ping"}')
    $writer.Flush()
    $response = $reader.ReadLine()
    Write-Host "Ping: $response"

    # Get Status
    $writer.WriteLine('{"cmd":"getStatus"}')
    $writer.Flush()
    $response = $reader.ReadLine()
    Write-Host "Status: $response"

    # Get Recent Decrypted
    $writer.WriteLine('{"cmd":"getRecentDecrypted","limit":3}')
    $writer.Flush()
    $response = $reader.ReadLine()
    Write-Host "Recent Decrypted: $response"

    $tcpClient.Close()
} catch {
    Write-Host "Error: $_"
}

Write-Host ""
Write-Host "Press Enter to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
