<#
  ai-workspace installer (Windows PowerShell)

  Скачивает набор инструментов ai-workspace из GitHub и раскладывает его
  в текущую папку (ваш проект). Умеет аккуратно мержить .claude\settings.json.

  Запуск:
    irm https://raw.githubusercontent.com/izzzo108/ai-workspace/main/install.ps1 | iex

  Режим обработки конфликтов можно задать заранее (без вопросов) через env:
    $env:AIWS_MODE = 'merge'      # merge (по умолчанию) | skip | overwrite
    irm .../install.ps1 | iex

  Что установить (по умолчанию — весь набор; иначе спросит):
    $env:AIWS_SCOPE = 'scripts'   # поставить ТОЛЬКО папку scripts\
    $env:AIWS_SCOPE = 'all'       # весь набор без вопроса

  Инициализация git в проекте (по умолчанию — спросит):
    $env:AIWS_GIT = 'yes'   # сразу git init  |  'no' — не предлагать

  Другой репозиторий/ветка:
    $env:AIWS_REPO = 'YOURNAME/ai-workspace'; $env:AIWS_BRANCH = 'main'
#>

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- config
$Repo   = if ($env:AIWS_REPO)   { $env:AIWS_REPO }   else { 'izzzo108/ai-workspace' } # владелец по умолчанию
$Branch = if ($env:AIWS_BRANCH) { $env:AIWS_BRANCH } else { 'main' }
$Mode   = if ($env:AIWS_MODE)   { $env:AIWS_MODE }   else { '' }  # merge|skip|overwrite|'' (спросить)
$Git    = if ($env:AIWS_GIT)    { $env:AIWS_GIT }    else { '' }  # yes|no|'' (спросить)
$Scope  = if ($env:AIWS_SCOPE)  { $env:AIWS_SCOPE }  else { '' }  # all|scripts|'' (спросить)

$Overlay = @(
  '.claude\agents', '.claude\commands', '.claude\rules', '.claude\skills',
  'docs', 'scripts', 'setup.bat', 'user_readme.md'
)
$Protected = @(
  '.claude\settings.json', 'CLAUDE.md', '.gitignore',
  '.claudeignore', 'requirements.txt'
)

# ---------------------------------------------------------------- ui helpers
function Say  ($m) { Write-Host $m }
function Ok   ($m) { Write-Host "  → $m"  -ForegroundColor Green }
function Warn ($m) { Write-Host "  ! $m"  -ForegroundColor Yellow }
function Fail ($m) { Write-Host "  x $m"  -ForegroundColor Red; exit 1 }

# Чтение ответа напрямую с консоли — работает даже при запуске через `irm | iex`.
function Ask ($prompt) {
  Write-Host -NoNewline $prompt
  try { return $Host.UI.ReadLine() } catch { return '' }
}

$Dest = (Get-Location).Path
Say ''
Say "  ai-workspace installer"
Say "  repo: $Repo@$Branch  ->  $Dest"
Say ''

# ---------------------------------------------------------------- download
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("aiws_" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
  $zip = Join-Path $tmp 'repo.zip'
  $url = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
  try {
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  } catch {
    Fail "не удалось скачать $url  (репозиторий/ветка существуют и публичны?)"
  }
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
  if (-not $src) { Fail 'распаковка не удалась' }
  $Src = $src.FullName

  # ---------------------------------------------------------------- scope (что ставим)
  if (-not $Scope) {
    Say '  Что установить?'
    Say '    a - весь набор ai-workspace (агенты, скиллы, доки, scripts)  [по умолчанию]'
    Say '    s - только папку scripts (план-ревью и мозговой штурм)'
    $sc = (Ask '  Выбор [a/s]: ').Trim().ToLower()
    $Scope = if ($sc -eq 's') { 'scripts' } else { 'all' }
    Say ''
  }

  # ---------------------------------------------------------------- conflict mode
  function Test-Conflict {
    foreach ($f in $Protected) {
      if ((Test-Path (Join-Path $Src $f)) -and (Test-Path (Join-Path $Dest $f))) { return $true }
    }
    return $false
  }

  if (($Scope -ne 'scripts') -and (-not $Mode)) {
    if (Test-Conflict) {
      Say "  У вас уже есть часть конфигурации (например .claude\settings.json)."
      Say "    m - смержить (добавить наши ключи, не трогая ваши)  [по умолчанию]"
      Say "    s - пропустить существующие файлы"
      Say "    o - перезаписать (с бэкапом .bak)"
      switch ((Ask '  Выбор [m/s/o]: ').Trim().ToLower()) {
        's'     { $Mode = 'skip' }
        'o'     { $Mode = 'overwrite' }
        default { $Mode = 'merge' }
      }
    } else { $Mode = 'merge' }
  }
  Say ''

  # ---------------------------------------------------------------- helpers
  function Copy-Overlay ($rel) {
    $s = Join-Path $Src $rel; $d = Join-Path $Dest $rel
    if (-not (Test-Path $s)) { return }
    if (Test-Path $s -PathType Container) {
      New-Item -ItemType Directory -Path $d -Force | Out-Null
      Copy-Item -Path (Join-Path $s '*') -Destination $d -Recurse -Force
    } else {
      $parent = Split-Path $d -Parent
      if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
      Copy-Item -Path $s -Destination $d -Force
    }
    Ok $rel
  }

  function Backup ($rel) {
    $d = Join-Path $Dest $rel
    if (Test-Path $d) { Copy-Item $d "$d.bak" -Recurse -Force; Say "    backup -> $rel.bak" }
  }

  # Установка папки scripts\ со СВОИМ запросом на замену, если она уже есть.
  function Install-Scripts {
    $s = Join-Path $Src 'scripts'; $d = Join-Path $Dest 'scripts'
    if (-not (Test-Path $s)) { Warn 'в источнике нет scripts/'; return }
    if (-not (Test-Path $d)) { Copy-Item $s $d -Recurse -Force; Ok 'scripts\ (новый)'; return }
    $decision = ''
    if     ($Mode -eq 'skip')      { $decision = 'no' }
    elseif ($Mode -eq 'overwrite') { $decision = 'yes' }
    if (-not $decision) {
      $ans = (Ask '  Папка scripts\ уже есть. Заменить нашими (старую сохраним в scripts.bak)? [y/N]: ').Trim().ToLower()
      $decision = if ($ans -in 'y','yes','д','да') { 'yes' } else { 'no' }
    }
    if ($decision -eq 'yes') {
      Backup 'scripts'; Remove-Item $d -Recurse -Force; Copy-Item $s $d -Recurse -Force; Ok 'scripts\ (заменён)'
    } else {
      Warn 'scripts\ - оставлен ваш'
    }
  }

  # Рекурсивное слияние: массивы — union, объекты — рекурсивно,
  # скаляры при конфликте сохраняем ПОЛЬЗОВАТЕЛЬСКИЕ (theirs).
  function Merge-Json ($theirs, $ours) {
    if ($theirs -is [System.Management.Automation.PSCustomObject] -and
        $ours   -is [System.Management.Automation.PSCustomObject]) {
      $out = [ordered]@{}
      foreach ($p in $theirs.PSObject.Properties) { $out[$p.Name] = $p.Value }
      foreach ($p in $ours.PSObject.Properties) {
        if ($out.Contains($p.Name)) { $out[$p.Name] = Merge-Json $out[$p.Name] $p.Value }
        else { $out[$p.Name] = $p.Value }
      }
      return [PSCustomObject]$out
    }
    if ($theirs -is [System.Array] -and $ours -is [System.Array]) {
      $out = New-Object System.Collections.ArrayList
      foreach ($i in $theirs) { [void]$out.Add($i) }
      foreach ($i in $ours) {
        $j = ($i | ConvertTo-Json -Depth 40 -Compress)
        $dup = $false
        foreach ($e in $out) { if (($e | ConvertTo-Json -Depth 40 -Compress) -eq $j) { $dup = $true; break } }
        if (-not $dup) { [void]$out.Add($i) }
      }
      return ,$out.ToArray()
    }
    return $theirs  # скаляр: значение пользователя
  }

  function Append-MissingLines ($srcFile, $dstFile) {
    $existing = @(Get-Content $dstFile -ErrorAction SilentlyContinue)
    $added = $false
    foreach ($line in (Get-Content $srcFile)) {
      if ($existing -notcontains $line) {
        Add-Content -Path $dstFile -Value $line; $added = $true
      }
    }
    return $added
  }

  # ---------------------------------------------------------------- scripts-only
  if ($Scope -eq 'scripts') {
    Install-Scripts
    Say ''
    Write-Host '  Готово. Установлена только папка scripts\.' -ForegroundColor Green
    Say '  Как пользоваться - docs\harness\brainstorm-guide.md и scripts.md'
    Say ''
  }
  else {

  # ---------------------------------------------------------------- install overlay
  foreach ($item in $Overlay) { Copy-Overlay $item }

  # README болванки кладём под отдельным именем — чтобы никогда не затереть README проекта.
  $srcReadme = Join-Path $Src 'README.md'
  if (Test-Path $srcReadme) {
    Copy-Item $srcReadme (Join-Path $Dest 'ai-workspace-README.md') -Force
    Ok 'ai-workspace-README.md (гайд по инструментам)'
  }

  # ---------------------------------------------------------------- install protected
  foreach ($rel in $Protected) {
    $s = Join-Path $Src $rel; $d = Join-Path $Dest $rel
    if (-not (Test-Path $s)) { continue }
    if (-not (Test-Path $d)) {
      $parent = Split-Path $d -Parent
      if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
      Copy-Item $s $d -Force; Ok "$rel (новый)"; continue
    }
    if ($Mode -eq 'skip') {
      Warn "$rel - пропущен (ваш оставлен)"
    }
    elseif ($Mode -eq 'overwrite') {
      Backup $rel; Copy-Item $s $d -Force; Ok "$rel (перезаписан)"
    }
    elseif ($rel -eq '.claude\settings.json') {
      Backup $rel
      $t = Get-Content $d -Raw | ConvertFrom-Json
      $o = Get-Content $s -Raw | ConvertFrom-Json
      (Merge-Json $t $o) | ConvertTo-Json -Depth 40 | Out-File $d -Encoding utf8
      Ok "$rel (смержен)"
    }
    elseif ($rel -in '.gitignore', '.claudeignore', 'requirements.txt') {
      if (Append-MissingLines $s $d) { Ok "$rel (дополнен)" } else { Warn "$rel - уже актуален" }
    }
    elseif ($rel -eq 'CLAUDE.md') {
      Copy-Item $s (Join-Path $Dest 'CLAUDE.aiworkspace.md') -Force
      Warn 'CLAUDE.md существует - наш сохранён как CLAUDE.aiworkspace.md (сравните вручную)'
    }
    else {
      Warn "$rel - пропущен"
    }
  }

  # ---------------------------------------------------------------- git init (optional)
  Say ''
  if (Test-Path (Join-Path $Dest '.git')) {
    Say '  git-репозиторий уже существует - git init пропущен'
  }
  elseif (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Warn 'git не установлен - инициализация репозитория пропущена.'
    Say  '    Чтобы вести историю проекта, установите Git и выполните git init:'
    Say  '    https://git-scm.com/install/windows  (версия 2.54 и выше)'
  }
  else {
    $doGit = $Git
    if (-not $doGit) {
      $ans = (Ask '  Инициализировать git-репозиторий здесь (git init)? [y/N]: ').Trim().ToLower()
      $doGit = if ($ans -in 'y','yes','д','да') { 'yes' } else { 'no' }
    }
    if ($doGit -eq 'yes') {
      git init -q 2>$null
      if (Test-Path (Join-Path $Dest '.git')) { Ok 'git-репозиторий создан (.git\)' } else { Warn 'git init не удался' }
    } else {
      Say '  git init пропущен'
    }
  }

  Say ''
  Write-Host "  Готово. Инструменты ai-workspace установлены в текущий проект." -ForegroundColor Green
  Say "  Начните с user_readme.md - правила в CLAUDE.md"
  Say ''
  }
}
finally {
  Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
