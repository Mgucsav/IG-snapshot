# Windows Görev Zamanlayıcı'ya günlük snapshot görevi ekler.
# Kullanım (PowerShell):  .\scripts\register_task.ps1            -> her gün 23:30
#                         .\scripts\register_task.ps1 -Time 22:00
#                         .\scripts\register_task.ps1 -Remove     -> görevi kaldır
param(
    [string]$Time = "23:30",
    [string]$TaskName = "IG Snapshot",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "scripts\run_snapshot.bat"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Görev kaldırıldı: $TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable: bilgisayar o saatte kapalıysa açılınca kaçırılan çalışmayı telafi eder
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Instagram rakip takibi: günlük snapshot + ay raporu ($root)" -Force | Out-Null

Write-Host "Görev kaydedildi: '$TaskName' her gün $Time"
Write-Host "Hemen denemek için:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Log:                 $root\logs\task.log"
