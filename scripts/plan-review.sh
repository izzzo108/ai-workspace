#!/bin/bash
# Запуск: ./scripts/plan-review.sh "описание задачи"
# Требует: установленные и залогиненные claude и codex CLI.
# Файл лежит в scripts/, скрипт сам переходит в корень проекта.
#
# Перед первым запуском один раз выполните:
#   chmod +x scripts/plan-review.sh
# Это даёт файлу право "исполняемый" — без этого Git Bash/Linux
# откажется его запускать (ошибка "Permission denied").
# Само содержимое файла при этом не меняется, разрешение нужно один раз.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT" || exit 1

# Переключаем кодировку консоли Windows на UTF-8. Без этого кириллица от
# claude/codex может испортиться (кракозябры) при сохранении в файлы.
chcp.com 65001 > /dev/null 2>&1

TASK="$1"

if [ -z "$TASK" ]; then
  echo "Использование: ./scripts/plan-review.sh \"текст задачи\""
  exit 1
fi

# --- Выбор модели Claude ---
echo "Выберите модель Claude:"
select choice in \
  "sonnet — Claude Sonnet 5 (по умолчанию)" \
  "opus — Claude Opus 4.8 (мощнее, медленнее)" \
  "haiku — Claude Haiku 4.5 (быстрее и дешевле)" \
  "fable — Claude Fable 5 (самая мощная)"; do
  case $REPLY in
    1) CLAUDE_MODEL="sonnet"; break ;;
    2) CLAUDE_MODEL="opus"; break ;;
    3) CLAUDE_MODEL="haiku"; break ;;
    4) CLAUDE_MODEL="fable"; break ;;
    *) echo "Введите число от 1 до 4" ;;
  esac
done
echo "Выбрана модель Claude: $CLAUDE_MODEL"

# --- Выбор модели Codex ---
echo "Выберите модель Codex:"
select choice in \
  "gpt-5.5 — основная (по умолчанию)" \
  "gpt-5.4-mini — быстрее и дешевле" \
  "gpt-5.3-codex — максимальная глубина кода"; do
  case $REPLY in
    1) CODEX_MODEL="gpt-5.5"; break ;;
    2) CODEX_MODEL="gpt-5.4-mini"; break ;;
    3) CODEX_MODEL="gpt-5.3-codex"; break ;;
    *) echo "Введите число от 1 до 3" ;;
  esac
done
echo "Выбрана модель Codex: $CODEX_MODEL"

# Дата в формате DD.MM.YYYY и время в формате HH.MM (часы.минуты)
DATE=$(date +'%d.%m.%Y')
TIME=$(date +'%H.%M')

PLAN_DIR="docs/Plans"
mkdir -p "$PLAN_DIR"

PLAN_FILE="$PLAN_DIR/${DATE}_${TIME}_plan.md"
CRITIQUE_FILE="$PLAN_DIR/${DATE}_${TIME}_critique.md"
FINAL_FILE="$PLAN_DIR/${DATE}_${TIME}_plan-final.md"

# Печать короткой тезисной сводки фиолетовым цветом (доп. трата токенов, но видно суть)
print_summary() {
  local label="$1"
  local text="$2"
  printf "\033[38;5;141m[ИТОГ %s]\n%s\033[0m\n" "$label" "$text"
}

# Проверка, что файл реально появился на диске (а не просто "модель сказала, что сохранила")
verify_file() {
  local path="$1"
  if [ -f "$path" ]; then
    printf "\033[92m💾 Файл сохранён: %s\033[0m\n" "$path"
    return 0
  else
    printf "\033[91m⚠ Файл НЕ найден: %s\033[0m\n" "$path"
    return 1
  fi
}

echo "== Шаг 1: Claude ($CLAUDE_MODEL) составляет план =="
claude -p "Используй planner.md как инструкцию. Составь план по задаче: $TASK. Сохрани план в файл $PLAN_FILE." \
  --model "$CLAUDE_MODEL" > /dev/null 2>&1

if verify_file "$PLAN_FILE"; then
  SUMMARY1=$(claude -p "Прочитай $PLAN_FILE. Одним списком из 3-5 пунктов коротко перечисли, что ты запланировал (главные фазы и решения). Только список, без вступления и заключения." --model "$CLAUDE_MODEL")
  print_summary "CLAUDE" "$SUMMARY1"
else
  echo "Шаг 2 и 3 пропущены: план не был создан."
  exit 1
fi

# --- Шаг 2: Codex критикует план ---
# Важно: НЕ просим Codex сохранять файл сам — он часто работает в "песочнице"
# без прав на запись в проект, и файл может тихо не появиться. Вместо этого
# забираем его ответ текстом и сохраняем critique.md сами.
# --dangerously-bypass-approvals-and-sandbox: на Windows у Codex CLI известный
# баг — его "хелпер" песочницы не запускается (orchestrator_helper_launch_failed),
# и без этого флага codex exec падает на старте, ничего не читая. Здесь это
# безопасно — Codex только читает файл и отвечает текстом, ничего не выполняет.
echo "== Шаг 2: Codex ($CODEX_MODEL) критикует план =="
CODEX_CRITIQUE=$(codex exec "Прочитай файл $PLAN_FILE. Дай критику: найди слабые места, риски, недостающие шаги, нестыковки. Выведи только список замечаний текстом прямо в ответе — сохранять файлы не нужно, об этом позаботится скрипт." --model "$CODEX_MODEL" --dangerously-bypass-approvals-and-sandbox)

CRITIQUE_OK=1
if [ -n "$CODEX_CRITIQUE" ]; then
  printf '%s\n' "$CODEX_CRITIQUE" > "$CRITIQUE_FILE"
  if verify_file "$CRITIQUE_FILE"; then
    CRITIQUE_OK=0
    SUMMARY2=$(codex exec "Вот твоя критика плана:\n\n$CODEX_CRITIQUE\n\nОдним списком из 3-5 пунктов коротко перечисли главные замечания. Только список, без вступления." --model "$CODEX_MODEL" --dangerously-bypass-approvals-and-sandbox)
    print_summary "CODEX" "$SUMMARY2"
  fi
else
  echo "⚠ Codex не вернул текст критики — файл не создан."
fi

# --- Шаг 3: Claude учитывает критику ---
echo "== Шаг 3: Claude ($CLAUDE_MODEL) учитывает критику и дорабатывает план =="
if [ "$CRITIQUE_OK" -eq 0 ]; then
  FINAL_PROMPT="Прочитай $PLAN_FILE (твой план) и $CRITIQUE_FILE (критика от другого ИИ, Codex). Реши, какие замечания принять, а какие отклонить — и почему. Дополни и допиши план с учётом принятых замечаний. Сохрани финальную версию в $FINAL_FILE."
else
  FINAL_PROMPT="Критика от Codex недоступна (шаг критики не удался). Прочитай $PLAN_FILE и просто сохрани его копию как финальную версию в $FINAL_FILE, без изменений."
fi

claude -p "$FINAL_PROMPT" \
  --model "$CLAUDE_MODEL" > /dev/null 2>&1

if verify_file "$FINAL_FILE" && [ "$CRITIQUE_OK" -eq 0 ]; then
  SUMMARY3=$(claude -p "Прочитай $FINAL_FILE и $CRITIQUE_FILE. Одним списком из 3-5 пунктов коротко перечисли: что из критики Codex ты принял, а что отклонил и почему. Только список, без вступления." --model "$CLAUDE_MODEL")
  print_summary "CLAUDE" "$SUMMARY3"
fi

echo "Готово. Итог: $FINAL_FILE"
