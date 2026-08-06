# Deploy the srm-mcp Docker host VM from Ubuntu cloud OVA + cloud-init seed ISO.
# Adapted from rtolab/scripts/_deploy_sftp_vm.ps1. Run Build-SeedIso.ps1 first.
param(
  [string]$VmName   = 'rtolab-mcp-webui',
  [string]$Ip       = '172.16.10.60',
  [string]$SeedIso  = 'C:\Users\Administrator\srm-mcp\deploy\provision\_seed_mcpvm.iso',
  [string]$DsName   = 'esxi-vol3',
  [string]$PgName   = 'selab-sswitch-pg-management',
  [int]   $MemGB    = 4,
  [int]   $Cpu      = 2,
  [int]   $DiskGB   = 60
)
$ProgressPreference='SilentlyContinue'
Import-Module powershell-yaml
Import-Module VMware.VimAutomation.Core -ErrorAction Stop
Set-PowerCLIConfiguration -InvalidCertificateAction Ignore -Confirm:$false -Scope Session | Out-Null
$repoRoot = 'C:\Users\Administrator\rtolab'
$inv     = Get-Content -Raw (Join-Path $repoRoot 'inventory/lab.yaml') | ConvertFrom-Yaml
$secrets = Get-Content -Raw (Join-Path $repoRoot 'inventory/secrets/lab.yaml') | ConvertFrom-Yaml
Connect-VIServer $inv.infra.outer_vcenter.fqdn -User $inv.infra.outer_vcenter.user -Password $secrets.outer_vcenter.sso_admin_pw | Out-Null

$ds = Get-Datastore -Name $DsName
Write-Host ("datastore {0}: {1:N0} GB free / {2:N0} GB" -f $DsName, $ds.FreeSpaceGB, $ds.CapacityGB)

# pick connected/powered-on host that sees the datastore, with the MOST free RAM
$vmhost = $ds | Get-VMHost |
  Where-Object { $_.ConnectionState -eq 'Connected' -and $_.PowerState -eq 'PoweredOn' } |
  Sort-Object { $_.MemoryTotalGB - $_.MemoryUsageGB } -Descending | Select-Object -First 1
if (-not $vmhost) { throw "no connected host sees $DsName" }
$freeRam = [math]::Round($vmhost.MemoryTotalGB - $vmhost.MemoryUsageGB, 1)
Write-Host "target host: $($vmhost.Name)  (free RAM ~${freeRam} GB)"
if ($freeRam -lt ($MemGB + 1)) { throw "host $($vmhost.Name) has only ${freeRam} GB free; need >= $($MemGB+1) GB. Free some outer RAM first." }

# delete stale VM
$old = Get-VM -Name $VmName -ErrorAction SilentlyContinue
if ($old) {
  Write-Host "deleting stale $VmName ..."
  if ($old.PowerState -eq 'PoweredOn') { Stop-VM -VM $old -Kill -Confirm:$false | Out-Null; Start-Sleep 3 }
  Remove-VM -VM $old -DeletePermanently -Confirm:$false
}

# upload seed ISO
Write-Host "uploading seed ISO to $DsName ..."
$psd = New-PSDrive -Name dsseed -PSProvider VimDatastore -Root '\' -Location $ds -ErrorAction Stop
if (-not (Test-Path 'dsseed:\iso')) { New-Item -Path 'dsseed:\iso' -ItemType Directory | Out-Null }
Copy-DatastoreItem -Item $SeedIso -Destination 'dsseed:\iso\rtolab-mcp-webui-seed.iso' -Force
Remove-PSDrive dsseed -Force
$isoDsPath = "[$DsName] iso/rtolab-mcp-webui-seed.iso"

# import Ubuntu OVA
$ova = 'E:\ubuntu-2004-cloud.ova'
$cfg = Get-OvfConfiguration -Ovf $ova
$pg  = Get-VirtualPortGroup -VMHost $vmhost -Name $PgName -Standard
foreach ($k in ($cfg.ToHashTable().Keys | Where-Object { $_ -like 'NetworkMapping*' })) {
  try { $cfg.$k.Value = $pg } catch {}
}
Write-Host "importing Ubuntu OVA ..."
$vm = Import-VApp -Source $ova -OvfConfiguration $cfg -Name $VmName -VMHost $vmhost -Datastore $ds -DiskStorageFormat Thin -Force -ErrorAction Stop

# move to folder (best effort)
$folder = Get-Folder -Name 'rtolab-vcf91' -Type VM -ErrorAction SilentlyContinue | Select-Object -First 1
if ($folder) { Move-VM -VM (Get-VM -Id $vm.Id) -Destination $folder -Confirm:$false | Out-Null }

# NIC -> mgmt portgroup ; sizing ; disk grow ; attach seed ISO
Get-NetworkAdapter -VM $vm | Set-NetworkAdapter -Portgroup $pg -Confirm:$false | Out-Null
Set-VM -VM $vm -MemoryGB $MemGB -NumCpu $Cpu -Confirm:$false | Out-Null
$hd = Get-HardDisk -VM (Get-VM -Id $vm.Id) | Select-Object -First 1
if ($hd.CapacityGB -lt $DiskGB) { $hd | Set-HardDisk -CapacityGB $DiskGB -Confirm:$false | Out-Null }
$cd = Get-CDDrive -VM (Get-VM -Id $vm.Id)
if (-not $cd) { $cd = New-CDDrive -VM (Get-VM -Id $vm.Id) -Confirm:$false }
Set-CDDrive -CD $cd -IsoPath $isoDsPath -StartConnected:$true -Confirm:$false | Out-Null

Start-VM -VM (Get-VM -Id $vm.Id) -Confirm:$false | Out-Null
Write-Host "$VmName powered on -> cloud-init installing Docker; will come up at $Ip (login ubuntu / the password you set in Build-SeedIso.ps1)"
Disconnect-VIServer * -Confirm:$false -Force | Out-Null
