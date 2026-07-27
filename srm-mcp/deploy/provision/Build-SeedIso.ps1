# Build a NoCloud cloud-init seed ISO (label CIDATA) for the srm-mcp Docker host VM.
# Adapted from rtolab/scripts/_build_sftp_seed.ps1 (the proven seed-ISO path).
# Bakes: static IP, ubuntu user, ssh pw auth, and Docker (via get.docker.com) into runcmd.
param(
  [string]$Hostname = 'rtolab-mcp-webui',
  [string]$Ip       = '172.16.10.60',
  [int]   $Prefix   = 24,
  [string]$Gateway  = '172.16.10.254',
  [string]$Password = 'CHANGE_ME',   # OS password for root + ubuntu; override on the CLI
  [string]$IsoOut   = 'C:\Users\Administrator\srm-mcp\deploy\provision\_seed_mcpvm.iso'
)
$ErrorActionPreference = 'Stop'
$work = Join-Path $env:TEMP "_seed_mcpvm"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null

$metaData = "instance-id: $Hostname-$(Get-Random)`nlocal-hostname: $Hostname`n"
$userData = @"
#cloud-config
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  list: |
    root:$Password
    ubuntu:$Password
write_files:
  - path: /etc/netplan/99-static.yaml
    permissions: '0600'
    content: |
      network:
        version: 2
        ethernets:
          alleth:
            match:
              name: "e*"
            dhcp4: false
            addresses: [$Ip/$Prefix]
            gateway4: $Gateway
            nameservers:
              addresses: [192.168.114.200, 8.8.8.8]
runcmd:
  - rm -f /etc/netplan/50-cloud-init.yaml /etc/netplan/00-installer-config.yaml
  - netplan apply
  - sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  - systemctl restart ssh
  - export DEBIAN_FRONTEND=noninteractive
  - curl -fsSL https://get.docker.com -o /root/get-docker.sh
  - sh /root/get-docker.sh || true   # sets up the docker apt repo (its bundled install fails on focal: docker-model-plugin not packaged)
  - DEBIAN_FRONTEND=noninteractive apt-get -y -qq install docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
  - groupadd -f docker
  - usermod -aG docker ubuntu
  - systemctl enable --now docker
  - docker --version > /root/docker-version.txt 2>&1
  - touch /root/cloud-init-done
"@
[IO.File]::WriteAllText("$work\meta-data", ($metaData -replace "`r`n","`n"), (New-Object Text.UTF8Encoding $false))
[IO.File]::WriteAllText("$work\user-data", ($userData -replace "`r`n","`n"), (New-Object Text.UTF8Encoding $false))

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class IsoWriter {
  public static void Write(object comStream, string path) {
    IStream stream = (IStream)comStream;
    using (FileStream fs = File.Create(path)) {
      byte[] buf = new byte[1048576];
      IntPtr read = Marshal.AllocHGlobal(4);
      try {
        while (true) {
          stream.Read(buf, buf.Length, read);
          int n = Marshal.ReadInt32(read);
          if (n <= 0) break;
          fs.Write(buf, 0, n);
        }
      } finally { Marshal.FreeHGlobal(read); }
    }
  }
}
'@

if (Test-Path $IsoOut) { Remove-Item $IsoOut -Force }
$fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$fsi.VolumeName = 'CIDATA'
$fsi.FileSystemsToCreate = 3
$fsi.Root.AddTree($work, $false)
$result = $fsi.CreateResultImage()
[IsoWriter]::Write($result.ImageStream, $IsoOut)
Write-Host "seed ISO built: $IsoOut  ($((Get-Item $IsoOut).Length) bytes)  host=$Hostname ip=$Ip"
