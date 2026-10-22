#requires -Version 5.1
<#
  DSH LAN autostart watcher.

  Keeps a LAN-bound Web GUI alive alongside the DeepSeek Harness desktop app:
    - desktop app running, no LAN host  -> start it on the current LAN IPv4
    - LAN host up but not listening on the LAN address -> restart it
    - desktop app gone                 -> stop the LAN host
  The desktop profile is never modified; this only drives a second profile
  ('desktop-lan') whose node_modules is a junction to the desktop one.

  -Once : run a single poll cycle (for testing) instead of looping forever.
#>
param([switch]$Once)

$ErrorActionPreference = 'SilentlyContinue'

# NOTE: do NOT name this variable $Profile - that is the read-only automatic
# variable $PROFILE; the assignment is silently dropped and the process matcher
# then matches nothing, starting a duplicate host on every poll.
$LanProfile  = 'desktop-lan'
$Port        = 3097
$DshCmd      = 'E:\AI\deepseek-harness\resources\runtime\cli\bin\dsh.cmd'
$StateDir    = 'C:\Users\LMR\.dsh\lan-autostart'
$LogFile     = Join-Path $StateDir 'watch.log'
$UrlFile     = Join-Path $StateDir 'lan-url.txt'
$RunLog      = 'C:\Users\LMR\.dsh\profiles\desktop-lan\lan-run.log'

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Write-Log([string]$Message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# Single instance guard: one watcher per machine.
# NOTE: WaitOne() THROWS AbandonedMutexException when a previous watcher was
# force-killed while holding the mutex. With $ErrorActionPreference='SilentlyContinue'
# that terminating .NET exception killed the script before its first log line, so
# every relaunch died silently. Catch it: on 'abandoned' we HAVE acquired the mutex.
$mutex = New-Object System.Threading.Mutex($false, 'Global\DshLanAutostartWatcher')
$acquired = $false
try {
    $acquired = $mutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $acquired = $true
} catch {
    $acquired = $false
}
if (-not $acquired) { return }

# Real LAN IPv4: skip loopback, link-local, and the WSL/Hyper-V/VMware/Meta
# adapters (the default route on this box resolves to the Meta adapter, so it
# must not be used).
function Get-LanIp {
    $exclude = '^127\.', '^169\.254\.', '^198\.18\.', '^192\.168\.56\.', '^192\.168\.137\.', '^172\.(1[6-9]|2[0-9]|3[01])\.'
    $ip = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $addr = $_.IPAddress
            -not ($exclude | Where-Object { $addr -match $_ })
        } |
        Sort-Object InterfaceMetric |
        Select-Object -First 1
    if ($ip) { return $ip.IPAddress }
    return $null
}

# The desktop app runs the host as dsh-desktop-host/lib/index.js.
function Test-DesktopHostRunning {
    foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='DeepSeek Harness.exe'")) {
        if ($p.CommandLine -and $p.CommandLine.Contains('dsh-desktop-host\lib\index.js')) { return $true }
        if ($p.CommandLine -and $p.CommandLine.Contains('dsh-desktop-host/lib/index.js')) { return $true }
    }
    return $false
}

# Match the exact CLI shape. A bare 'desktop-lan' substring also matches this
# script's own pwsh command line, so require the host cli entry plus the
# '--profile desktop-lan' argument.
function Get-LanHostProcs {
    $needle = '--profile ' + $LanProfile
    Get-CimInstance Win32_Process -Filter "Name='DeepSeek Harness.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.Contains('dsh-desktop-host') -and
            $_.CommandLine.Contains('cli.js') -and
            $_.CommandLine.Contains($needle)
        }
}

function Test-LanListening([string]$HostIp) {
    foreach ($c in (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
        if ($c.LocalAddress -eq $HostIp -or $c.LocalAddress -eq '0.0.0.0' -or $c.LocalAddress -eq '::') { return $true }
    }
    return $false
}

function Start-LanHost([string]$HostIp) {
    Remove-Item $RunLog -Force -ErrorAction SilentlyContinue
    Remove-Item ($RunLog + '.err') -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $DshCmd -ArgumentList '--profile', $LanProfile, '--host', $HostIp, '--port', $Port, '--no-open' -WindowStyle Hidden -RedirectStandardOutput $RunLog -RedirectStandardError ($RunLog + '.err') | Out-Null
    Write-Log ('started {0} on {1}:{2}' -f $LanProfile, $HostIp, $Port)
}

function Stop-LanHost {
    foreach ($p in (Get-LanHostProcs)) {
        Write-Log ('stopping {0} pid {1}' -f $LanProfile, $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains('--profile ' + $LanProfile) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# The boot token changes on every start; publish the current URL so the user can
# open the LAN GUI without digging through logs.
function Publish-LanUrl([int]$TimeoutSeconds = 60) {
    for ($i = 0; $i -lt ($TimeoutSeconds * 2); $i++) {
        $line = Get-Content $RunLog -ErrorAction SilentlyContinue |
            Where-Object { $_ -match 'token=' } |
            Select-Object -First 1
        if ($line) {
            $url = $line.Substring($line.IndexOf('http'))
            Set-Content -Path $UrlFile -Value $url -Encoding UTF8
            Write-Log ('LAN GUI: ' + $url)
            return $url
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Log 'timed out waiting for the boot token; check lan-run.log'
    return $null
}

function Invoke-Poll {
    $desktop  = Test-DesktopHostRunning
    $lanProcs = @(Get-LanHostProcs)
    $lanIp    = Get-LanIp

    if (-not $desktop) {
        if ($lanProcs.Count -gt 0) { Stop-LanHost }
        return $false
    }

    if ($lanProcs.Count -eq 0) {
        if (-not $lanIp) { Write-Log 'no LAN IPv4 found; not starting'; return $false }
        Start-LanHost $lanIp
        Publish-LanUrl | Out-Null
        return $true
    }

    if ($lanIp -and -not (Test-LanListening $lanIp)) {
        # Process is up but not bound to the LAN address (wrong NIC, failed bind,
        # or the IP changed): restart it pinned to the current address.
        Write-Log ('{0} pid {1} not listening on {2}:{3}; restarting' -f $LanProfile, $lanProcs[0].ProcessId, $lanIp, $Port)
        Stop-LanHost
        Start-Sleep -Seconds 2
        Start-LanHost $lanIp
        Publish-LanUrl | Out-Null
        return $true
    }

    # Host was already up (e.g. started by hand before this watcher): make sure the
    # current boot-token URL is published too, so lan-url.txt is never stale/absent.
    if (-not (Test-Path $UrlFile)) { Publish-LanUrl | Out-Null }

    return $true
}

Write-Log 'watcher started'

if ($Once) {
    Invoke-Poll | Out-Null
    Write-Log 'single poll done'
    return
}

# Poll fast while the desktop app is up, slowly while idle.
while ($true) {
    $active = Invoke-Poll
    if ($active) { Start-Sleep -Seconds 5 } else { Start-Sleep -Seconds 20 }
}
