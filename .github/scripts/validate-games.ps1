$file = "games.json"

if (-not (Test-Path $file)) {
    Write-Error "File '$file' not found"
    exit 1
}

$json = Get-Content $file -Raw | ConvertFrom-Json

$ids = @()
$errors = 0

foreach ($game in $json.games) {
    if (-not $game.PSObject.Properties.Name.Contains("id")) {
        Write-Error "Game '$($game.name)' has no 'id'"
        $errors++
    }
    elseif ($ids -contains $game.id) {
        Write-Error "Duplicate id found: $($game.id) in game '$($game.name)'"
        $errors++
    }
    else {
        $ids += $game.id
    }
}

if ($errors -gt 0) {
    Write-Error "Validation failed with $errors error(s)."
    exit 1
}
else {
    Write-Output "Validation passed"
}
