param(
    [string]$BackupDir,
    [switch]$List
)
. (Join-Path $PSScriptRoot 'common.ps1')

if ($List -or [string]::IsNullOrWhiteSpace($BackupDir)) {
    Invoke-ProjectPython "import sys; sys.path.insert(0, r'$ProjectRoot'); from utils import list_backups; [print(b['name'], 'complete' if b['complete'] else 'incomplete') for b in list_backups()]"
    return
}
if (-not (Test-Path -LiteralPath $BackupDir)) {
    throw "Backup directory not found: $BackupDir"
}
Invoke-ProjectPython "import sys; sys.path.insert(0, r'$ProjectRoot'); from utils import restore_backup; print('RESTORED_FILES=' + str(restore_backup(r'$BackupDir')))"
