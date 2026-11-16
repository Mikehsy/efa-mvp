param([string]$Label = 'manual')
. (Join-Path $PSScriptRoot 'common.ps1')

if ($Label -notmatch '^[A-Za-z0-9_-]+$') {
    throw 'Label must contain only letters, digits, dash or underscore.'
}
Ensure-Dirs
Invoke-ProjectPython "import sys; sys.path.insert(0, r'$ProjectRoot'); from utils import create_backup; print('BACKUP=' + str(create_backup('$Label')))"
