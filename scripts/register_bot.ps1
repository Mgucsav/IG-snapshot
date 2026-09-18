# Telegram sohbet botunu Görev Zamanlayıcı'ya ekler: oturum açılınca başlar, sürekli çalışır,
# çökerse 1 dk sonra yeniden başlatılır, pencere açmaz (pythonw).
# Kullanım:  .\scripts\register_bot.ps1            -> kaydet ve hemen başlat
#            .\scripts\register_bot.ps1 -Remove    -> durdur ve kaldır
#            .\scripts\register_bot.ps1 -Restart   -> yeniden başlat (kod güncellemesinden sonra)
param(
    [string]$TaskName = "IG Bot",
    [switch]$Remove,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { throw "Sanal ortam bulunamadı: $pyw" }

if ($Remove -or $Restart) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        if ($Remove) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Bot görevi kaldırıldı."
            exit 0
        }
    }
}

$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m ig_snapshot bot" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "IG Snapshot Telegram sohbet botu ($root)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host "Bot görevi kaydedildi ve başlatıldı: '$TaskName' → durum: $state"
Write-Host "Log: $root\logs\ig_snapshot.log   (Telegram'da 'dün feed' yazarak dene)"
