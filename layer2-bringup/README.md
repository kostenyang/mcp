# Layer 2 — VCF Installer Bring-up

把 VCF Installer 餵 JSON 自動帶起 management domain (vCenter / NSX / SDDC Manager).

## 預計實作

- `vcf-installer-bringup.json` — VCF Installer 的 bring-up spec
- `submit-bringup.ps1` — 用 `Invoke-RestMethod` 推 JSON 進 VCF Installer 並 poll 狀態
- 參考 William Lam 的 VCF 9.1 lab workaround 那篇 (跳過 HCL 等)

## TODO

- [ ] 寫 bringup JSON template (從你現在 lab 反推)
- [ ] 寫 submit script
- [ ] 加 lab workaround patch (`bypass HCL`, `nested CPU`, etc.)
