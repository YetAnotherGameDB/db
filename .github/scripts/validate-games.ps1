$file = "games.json"

if (-not (Test-Path $file)) {
    Write-Error "File '$file' not found"
    exit 1
}

$json = Get-Content $file -Raw | ConvertFrom-Json

$ids = @()
$instanceIds = [System.Collections.Generic.HashSet[string]]::new()
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

    foreach ($instance in $game.gameInstances) {
        if (-not $instance.PSObject.Properties.Name.Contains("id")) {
            Write-Error "Instance '$($instance.serial)' in game '$($game.name)' has no 'id'"
            $errors++
        }
        elseif ($instance.id -notmatch "^$($game.id)-\d+$") {
            Write-Error "Instance id '$($instance.id)' in game '$($game.name)' does not match game id $($game.id)"
            $errors++
        }
        elseif (-not $instanceIds.Add($instance.id)) {
            Write-Error "Duplicate instance id found: $($instance.id) in game '$($game.name)'"
            $errors++
        }
    }
}

if ($errors -gt 0) {
    Write-Error "Validation failed with $errors error(s)."
    exit 1
}
else {
    Write-Output "Validation passed"
}
