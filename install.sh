#!/usr/bin/env bash
#
# ai-workspace installer (macOS / Linux / Git-Bash)
#
# Скачивает набор инструментов ai-workspace из GitHub и раскладывает его
# в текущую папку (ваш проект). Умеет аккуратно мержить .claude/settings.json.
#
# Запуск:
#   curl -fsSL https://raw.githubusercontent.com/izzzo108/ai-workspace/main/install.sh | bash
#
# Режим обработки конфликтов можно задать заранее (без вопросов):
#   ... | bash -s -- --merge        # мержить (по умолчанию)
#   ... | bash -s -- --skip         # не трогать существующие файлы
#   ... | bash -s -- --overwrite    # перезаписать (с бэкапом .bak)
#
# Инициализация git в проекте (по умолчанию — спросит):
#   ... | bash -s -- --git        # сразу git init
#   ... | bash -s -- --no-git     # не предлагать git init
#
# Другой репозиторий/ветка:
#   ... | bash -s -- --repo YOURNAME/ai-workspace --branch main
#
set -euo pipefail

# ---------------------------------------------------------------- config
REPO="${AIWS_REPO:-izzzo108/ai-workspace}"   # владелец репозитория по умолчанию
BRANCH="${AIWS_BRANCH:-main}"
MODE="${AIWS_MODE:-}"                      # merge | skip | overwrite | "" (спросить)
GIT="${AIWS_GIT:-}"                         # yes | no | "" (спросить)

# Что всегда ставим (наши инструменты; при повторном запуске — обновление).
OVERLAY=(
  ".claude/agents"
  ".claude/commands"
  ".claude/rules"
  ".claude/skills"
  "docs"
  "setup.bat"
  "user_readme.md"
)

# Что защищаем от потери при конфликте (обрабатывается по выбранному режиму).
PROTECTED=(
  ".claude/settings.json"
  "CLAUDE.md"
  ".gitignore"
  ".claudeignore"
  "requirements.txt"
  ".python-version"
)

# ---------------------------------------------------------------- ui helpers
if [ -t 1 ]; then
  C_B="\033[1m"; C_G="\033[32m"; C_Y="\033[33m"; C_R="\033[31m"; C_D="\033[2m"; C_0="\033[0m"
else
  C_B=""; C_G=""; C_Y=""; C_R=""; C_D=""; C_0=""
fi
say()  { printf "%b\n" "$*"; }
ok()   { printf "  %b→%b %s\n" "$C_G" "$C_0" "$*"; }
warn() { printf "  %b⚠%b %s\n" "$C_Y" "$C_0" "$*"; }
err()  { printf "  %b✗%b %s\n" "$C_R" "$C_0" "$*" >&2; }

# Чтение ответа напрямую с терминала — работает даже при запуске через `curl | bash`,
# когда stdin занят телом скрипта.
ask() {
  local prompt="$1" ans=""
  if [ -r /dev/tty ]; then
    printf "%b" "$prompt" > /dev/tty
    IFS= read -r ans < /dev/tty || true
  fi
  printf "%s" "$ans"
}

# ---------------------------------------------------------------- args
while [ $# -gt 0 ]; do
  case "$1" in
    --merge)     MODE="merge" ;;
    --skip)      MODE="skip" ;;
    --overwrite) MODE="overwrite" ;;
    --git)       GIT="yes" ;;
    --no-git)    GIT="no" ;;
    --repo)      REPO="${2:?}"; shift ;;
    --branch)    BRANCH="${2:?}"; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "Неизвестный аргумент: $1"; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------- preflight
command -v curl >/dev/null 2>&1 || { err "нужен curl"; exit 1; }
command -v tar  >/dev/null 2>&1 || { err "нужен tar";  exit 1; }
HAVE_PY=""
for c in python3 python py; do
  # проверяем, что интерпретатор реально запускается (на Windows `python3`
  # может быть заглушкой Microsoft Store, которая падает при вызове).
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json,sys' >/dev/null 2>&1; then
    HAVE_PY="$c"; break
  fi
done

say ""
say "${C_B}  ai-workspace installer${C_0}"
say "${C_D}  repo: $REPO@$BRANCH  →  $(pwd)${C_0}"
say ""

# ---------------------------------------------------------------- download
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
if ! curl -fsSL "$URL" | tar -xz -C "$TMP" 2>/dev/null; then
  err "не удалось скачать $URL"
  err "проверьте, что репозиторий и ветка существуют и публичны"
  exit 1
fi
SRC="$TMP/$(ls "$TMP" | head -n1)"
[ -d "$SRC" ] || { err "распаковка не удалась"; exit 1; }

# ---------------------------------------------------------------- conflict mode
have_conflict() {
  local f
  for f in "${PROTECTED[@]}"; do
    [ -e "$SRC/$f" ] && [ -e "./$f" ] && return 0
  done
  return 1
}

if [ -z "$MODE" ]; then
  if have_conflict; then
    say "  У вас уже есть часть конфигурации (например ${C_B}.claude/settings.json${C_0})."
    say "    ${C_B}m${C_0} — смержить (добавить наши ключи, не трогая ваши)  ${C_D}[по умолчанию]${C_0}"
    say "    ${C_B}s${C_0} — пропустить существующие файлы"
    say "    ${C_B}o${C_0} — перезаписать (с бэкапом .bak)"
    a="$(ask "  Выбор [m/s/o]: ")"
    case "$a" in
      s|S) MODE="skip" ;;
      o|O) MODE="overwrite" ;;
      *)   MODE="merge" ;;
    esac
    if [ -z "$a" ] && [ ! -r /dev/tty ]; then
      warn "нет доступа к терминалу — существующие файлы будут пропущены (--skip)"
      MODE="skip"
    fi
  else
    MODE="merge"
  fi
fi
say ""

# ---------------------------------------------------------------- copy helpers
copy_overlay() {
  local rel="$1"
  [ -e "$SRC/$rel" ] || return 0
  if [ -d "$SRC/$rel" ]; then
    mkdir -p "./$rel"
    cp -R "$SRC/$rel/." "./$rel/"
  else
    mkdir -p "$(dirname "./$rel")"
    cp -R "$SRC/$rel" "./$rel"
  fi
  ok "$rel"
}

backup() { [ -e "./$1" ] && cp -R "./$1" "./$1.bak" && say "    ${C_D}бэкап → $1.bak${C_0}"; }

# Слияние JSON (settings.json): объединяем массивы (union), словари — рекурсивно,
# скаляры при конфликте оставляем ПОЛЬЗОВАТЕЛЬСКИЕ.
merge_json() {
  local theirs="$1" ours="$2" out="$3"
  "$HAVE_PY" - "$theirs" "$ours" "$out" <<'PY'
import json, sys
theirs = json.load(open(sys.argv[1], encoding="utf-8"))
ours   = json.load(open(sys.argv[2], encoding="utf-8"))
def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = list(a)
        for item in b:
            if item not in out:
                out.append(item)
        return out
    return a  # скаляр: сохраняем значение пользователя
json.dump(merge(theirs, ours), open(sys.argv[3], "w", encoding="utf-8"),
          indent=2, ensure_ascii=False)
PY
}

# Дописать в текстовый файл только те строки, которых там ещё нет.
append_missing_lines() {
  local ours="$1" target="$2" added=0 line
  while IFS= read -r line || [ -n "$line" ]; do
    if ! grep -qxF -- "$line" "$target" 2>/dev/null; then
      printf "%s\n" "$line" >> "$target"; added=1
    fi
  done < "$ours"
  return $((1 - added))
}

# ---------------------------------------------------------------- install overlay
for item in "${OVERLAY[@]}"; do copy_overlay "$item"; done

# README болванки кладём под отдельным именем — чтобы никогда не затереть README проекта.
if [ -e "$SRC/README.md" ]; then
  cp "$SRC/README.md" "./ai-workspace-README.md"
  ok "ai-workspace-README.md ${C_D}(гайд по инструментам)${C_0}"
fi

# ---------------------------------------------------------------- install protected
for rel in "${PROTECTED[@]}"; do
  [ -e "$SRC/$rel" ] || continue
  if [ ! -e "./$rel" ]; then
    mkdir -p "$(dirname "./$rel")"; cp -R "$SRC/$rel" "./$rel"; ok "$rel ${C_D}(новый)${C_0}"; continue
  fi
  # файл уже есть — действуем по режиму
  case "$MODE" in
    skip) warn "$rel — пропущен (ваш оставлен)"; continue ;;
    overwrite) backup "$rel"; cp -R "$SRC/$rel" "./$rel"; ok "$rel ${C_D}(перезаписан)${C_0}"; continue ;;
  esac
  # MODE=merge
  case "$rel" in
    ".claude/settings.json")
      if [ -n "$HAVE_PY" ]; then
        backup "$rel"
        merge_json "./$rel" "$SRC/$rel" "$TMP/merged.json" && cp "$TMP/merged.json" "./$rel"
        ok "$rel ${C_D}(смержен)${C_0}"
      else
        warn "$rel — нет python для мержа, пропущен. Наш вариант: $rel.new"
        cp "$SRC/$rel" "./$rel.new"
      fi
      ;;
    ".gitignore"|".claudeignore"|"requirements.txt")
      if append_missing_lines "$SRC/$rel" "./$rel"; then
        ok "$rel ${C_D}(дополнен)${C_0}"
      else
        warn "$rel — уже актуален"
      fi
      ;;
    "CLAUDE.md")
      # центральный файл — не трогаем чужой, кладём наш рядом на ревью
      cp "$SRC/$rel" "./CLAUDE.aiworkspace.md"
      warn "CLAUDE.md существует — наш сохранён как CLAUDE.aiworkspace.md (сравните вручную)"
      ;;
    ".python-version")
      warn ".python-version — оставлен ваш"
      ;;
    *)
      warn "$rel — пропущен"
      ;;
  esac
done

# ---------------------------------------------------------------- git init (optional)
say ""
if [ -d ".git" ]; then
  say "  ${C_D}git-репозиторий уже существует — git init пропущен${C_0}"
elif ! command -v git >/dev/null 2>&1; then
  warn "git не установлен — инициализация репозитория пропущена."
  say  "    Чтобы вести историю проекта, установите Git и выполните ${C_B}git init${C_0}:"
  say  "    ${C_B}https://git-scm.com/downloads${C_0}"
  say  "    ${C_D}Windows: https://git-scm.com/install/windows (версия 2.54 и выше)${C_0}"
else
  do_git="$GIT"
  if [ -z "$do_git" ]; then
    a="$(ask "  Инициализировать git-репозиторий здесь (git init)? [y/N]: ")"
    case "$a" in y|Y|д|Д) do_git="yes" ;; *) do_git="no" ;; esac
  fi
  if [ "$do_git" = "yes" ]; then
    if git init -q 2>/dev/null; then ok "git-репозиторий создан (.git/)"; else warn "git init не удался"; fi
  else
    say "  ${C_D}git init пропущен${C_0}"
  fi
fi

# ---------------------------------------------------------------- done
say ""
say "  ${C_G}${C_B}Готово.${C_0} Инструменты ai-workspace установлены в текущий проект."
say "  ${C_D}Начните с user_readme.md · правила — в CLAUDE.md${C_0}"
say ""
