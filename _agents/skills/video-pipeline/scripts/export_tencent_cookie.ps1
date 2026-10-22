# export_tencent_cookie.ps1 — 从 Windows Chrome（--remote-debugging-port=9222）导出视频号 cookie
# 输出 Playwright storage_state 到 WSL 侧 cookies/tencent_diyi.json（经 \\wsl$ 写回由 WSL 脚本完成，本脚本只输出 JSON 到 stdout）
$ErrorActionPreference = "Stop"
$DebugPort = 9222

$pages = Invoke-RestMethod -Uri "http://127.0.0.1:$DebugPort/json" -TimeoutSec 10
$page = $pages | Where-Object { $_.url -match "channels.weixin.qq.com" } | Select-Object -First 1
if (-not $page) {
    Write-Output "NOT_FOUND_PAGE"
    exit 1
}
Write-Output "找到页面: $($page.url)"

$ws = New-Object System.Net.WebSockets.ClientWebSocket
$ct = [System.Threading.CancellationToken]::None
$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, $ct).Wait()

function Send-Cmd($id, $method, $params = $null) {
    $msg = @{ id = $id; method = $method } + $(if ($params) { @{ params = $params } } else { @{} })
    $json = $msg | ConvertTo-Json -Depth 5 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $ws.SendAsync([ArraySegment[byte]]::new($bytes), 'Text', $true, $ct).Wait()
}

function Recv-Msg {
    $buffer = New-Object byte[] (4MB)
    $all = New-Object System.Text.StringBuilder
    while ($true) {
        $seg = [ArraySegment[byte]]::new($buffer)
        $r = $ws.ReceiveAsync($seg, $ct).Result
        [void]$all.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $r.Count))
        if ($r.EndOfMessage) { break }
    }
    return $all.ToString() | ConvertFrom-Json
}

Send-Cmd 1 "Network.getAllCookies"
$cookies = (Recv-Msg).result.cookies | Where-Object { $_.domain -match "weixin\.qq\.com" }
Write-Output "cookies: $($cookies.Count)"

$script = '(function(){var r=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);r.push([k,localStorage.getItem(k)]);}return JSON.stringify(r);})()'
Send-Cmd 2 "Runtime.evaluate" @{ expression = $script; returnByValue = $true }
$eval = Recv-Msg
$raw = $eval.result.result.value
$items = if ($raw) { $raw | ConvertFrom-Json } else { $null }
Write-Output "localStorage items: $(if ($items) { $items.Count } else { 0 })"

try { $ws.CloseAsync([TimeSpan]::FromSeconds(3), $ct).Wait() } catch {}
$ws.Dispose()

$cookieList = @($cookies | ForEach-Object {
    @{ name = $_.name; value = $_.value; domain = $_.domain; path = $_.path;
       expires = $_.expires; httpOnly = $_.httpOnly; secure = $_.secure;
       sameSite = if ($_.sameSite) { $_.sameSite } else { "Lax" } }
})
# localStorage 原始 JSON 直传（WSL 侧组装，避免 PS 数组取值坑）
$rawOut = "[]"
if ($raw) { $rawOut = [string]$raw }
Write-Output "rawlen=$($rawOut.Length)"
$result = @{ cookies = $cookieList; localStorage_raw = $rawOut }
$json = $result | ConvertTo-Json -Depth 6 -Compress
Write-Output "jsonlen=$($json.Length)"
New-Item -ItemType Directory -Force -Path "C:\temp" | Out-Null
$outFile = "C:\temp\tencent_diyi_export.json"
[System.IO.File]::WriteAllText($outFile, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "SAVED: $outFile"
Write-Output "cookie names: $(($cookieList | ForEach-Object { $_.name }) -join ', ')"
