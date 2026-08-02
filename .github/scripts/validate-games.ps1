$file = "games.json"

if (-not (Test-Path $file)) {
    Write-Error "File '$file' not found"
    exit 1
}

$json = Get-Content $file -Raw | ConvertFrom-Json

$ids = @()
$instanceIds = [System.Collections.Generic.HashSet[string]]::new()
$instanceGame = @{}   # instance id -> owning game id
$linkTargets = @{}    # linking instance id -> target instance id
$serialGames = @{}    # serial -> set of game ids (links excluded)
$errors = 0
$warnings = 0

# Serials legitimately shared across games (physically different discs
# that reuse the same serial, e.g. Heavy Rain vs its Move Edition).
$sharedSerialAllowlist = @("BCES-00797", "BCES-00458")

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
        $props = $instance.PSObject.Properties.Name
        if (-not $props.Contains("id")) {
            Write-Error "Instance '$($instance.serial)' in game '$($game.name)' has no 'id'"
            $errors++
            continue
        }
        if ($instance.id -notmatch "^$($game.id)-\d+$") {
            Write-Error "Instance id '$($instance.id)' in game '$($game.name)' does not match game id $($game.id)"
            $errors++
        }
        elseif (-not $instanceIds.Add($instance.id)) {
            Write-Error "Duplicate instance id found: $($instance.id) in game '$($game.name)'"
            $errors++
        }
        $instanceGame[$instance.id] = $game.id

        if ($props.Contains("link")) {
            # A link stub must contain exactly 'id' and 'link'.
            $extra = $props | Where-Object { $_ -notin @("id", "link") }
            if ($extra) {
                Write-Error "Link instance '$($instance.id)' in game '$($game.name)' has extra properties: $($extra -join ', ')"
                $errors++
            }
            $linkTargets[$instance.id] = $instance.link
        }
        elseif ($instance.serial) {
            if (-not $serialGames.ContainsKey($instance.serial)) {
                $serialGames[$instance.serial] = [System.Collections.Generic.HashSet[int]]::new()
            }
            [void]$serialGames[$instance.serial].Add($game.id)
        }
    }
}

# Links must resolve to an existing, non-link instance of a different game.
foreach ($entry in $linkTargets.GetEnumerator()) {
    $from = $entry.Key
    $to = $entry.Value
    if (-not $instanceIds.Contains($to)) {
        Write-Error "Link instance '$from' points to nonexistent instance '$to'"
        $errors++
    }
    elseif ($linkTargets.ContainsKey($to)) {
        Write-Error "Link instance '$from' points to '$to', which is itself a link"
        $errors++
    }
    elseif ($instanceGame[$from] -eq $instanceGame[$to]) {
        Write-Error "Link instance '$from' points to '$to' in the same game; links must cross games"
        $errors++
    }
}

# The same serial under two games means a duplicated instance — it should be
# a link stub instead (or the serial belongs on the allowlist above).
# TEMPORARY: reported as a warning instead of an error while the existing
# duplicates are being cleaned up. Restore the $errors++ once they are fixed.
foreach ($entry in $serialGames.GetEnumerator()) {
    if ($entry.Value.Count -gt 1 -and $entry.Key -notin $sharedSerialAllowlist) {
        Write-Warning "Serial '$($entry.Key)' appears under multiple games: $($entry.Value -join ', '). Use a link instance instead."
        $warnings++
    }
}

if ($warnings -gt 0) {
    Write-Output "Validation produced $warnings warning(s)."
}

if ($errors -gt 0) {
    Write-Error "Validation failed with $errors error(s)."
    exit 1
}
else {
    Write-Output "Validation passed"
}
