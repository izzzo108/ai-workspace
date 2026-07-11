#!/bin/bash
# ============================================================================
# Запуск ЧЕРЕЗ Git Bash:
#     ./scripts/plan-review.sh "текст задачи"
#     (или: bash scripts/plan-review.sh "текст задачи")
#
# План-ревью двумя LLM на выбор (Claude / Codex / GLM), вывод прямо в терминал:
#   начинающий составляет план -> отвечающий критикует -> начинающий дорабатывает.
#
# Для каждой роли выбираешь движок и модель. GLM берётся через opencode
# (провайдер zai-coding-plan) и показывается в выборе, ТОЛЬКО если opencode установлен.
#
# Контекст между шагами передаётся моделям прямо в промпте, а все файлы пишет сам
# скрипт — так в любом слоте одинаково работают все три движка.
#
# Требует: CLI claude, codex и (опц.) opencode в PATH — те, что доступны в Git Bash.
# Есть также live-версия с просмотром в браузере: scripts/plan-review-live.py
# ============================================================================

set -o pipefail

# UTF-8 в консоли Windows (Git Bash), иначе кириллица может испортиться.
chcp.com 65001 > /dev/null 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT" || exit 1

TASK="$1"
if [ -z "$TASK" ]; then
  echo "Использование (через Git Bash): ./scripts/plan-review.sh \"текст задачи\""
  exit 1
fi

# GLM доступен только если установлен opencode.
GLM_AVAILABLE=0
if command -v opencode > /dev/null 2>&1; then GLM_AVAILABLE=1; fi

# --- Выбор движка и модели для роли: заполняет SLOT_ENGINE и SLOT_MODEL ------
SLOT_ENGINE=""
SLOT_MODEL=""
choose_slot() {
  local role="$1"
  echo ""
  echo "=== $role ==="
  echo "Выберите движок:"
  echo "  1) Claude"
  echo "  2) Codex"
  local emax=2
  if [ "$GLM_AVAILABLE" = "1" ]; then echo "  3) GLM (через opencode)"; emax=3; fi
  while true; do
    read -r -p "Число (1-$emax): " n
    case "$n" in
      1) SLOT_ENGINE="claude"; break ;;
      2) SLOT_ENGINE="codex"; break ;;
      3) if [ "$GLM_AVAILABLE" = "1" ]; then SLOT_ENGINE="glm"; break; fi ;;
    esac
    echo "Введите число от 1 до $emax"
  done

  echo "Выберите модель:"
  if [ "$SLOT_ENGINE" = "claude" ]; then
    echo "  1) sonnet   2) opus   3) haiku   4) fable"
    while true; do read -r -p "Число (1-4): " m; case "$m" in
      1) SLOT_MODEL="sonnet"; break ;; 2) SLOT_MODEL="opus"; break ;;
      3) SLOT_MODEL="haiku"; break ;; 4) SLOT_MODEL="fable"; break ;;
      *) echo "Введите 1-4" ;; esac; done
  elif [ "$SLOT_ENGINE" = "codex" ]; then
    echo "  1) gpt-5.6-sol (Codex 5.6)   2) gpt-5.5   3) gpt-5.4-mini   4) gpt-5.3-codex"
    while true; do read -r -p "Число (1-4): " m; case "$m" in
      1) SLOT_MODEL="gpt-5.6-sol"; break ;; 2) SLOT_MODEL="gpt-5.5"; break ;;
      3) SLOT_MODEL="gpt-5.4-mini"; break ;; 4) SLOT_MODEL="gpt-5.3-codex"; break ;;
      *) echo "Введите 1-4" ;; esac; done
  else
    echo "  1) glm-5.2   2) glm-4.7   3) glm-5-turbo   4) glm-4.5-air"
    while true; do read -r -p "Число (1-4): " m; case "$m" in
      1) SLOT_MODEL="zai-coding-plan/glm-5.2"; break ;;
      2) SLOT_MODEL="zai-coding-plan/glm-4.7"; break ;;
      3) SLOT_MODEL="zai-coding-plan/glm-5-turbo"; break ;;
      4) SLOT_MODEL="zai-coding-plan/glm-4.5-air"; break ;;
      *) echo "Введите 1-4" ;; esac; done
  fi
}

# --- Запуск одного хода: печатает чистый текст ответа в stdout ---------------
# У всех трёх CLI осмысленный ответ идёт в stdout, а служебный вывод — в stderr,
# поэтому stderr глушим (2>/dev/null) и забираем только текст ответа.
run_engine() {
  local engine="$1" model="$2" prompt="$3"
  case "$engine" in
    claude) claude -p "$prompt" --model "$model" 2>/dev/null ;;
    codex)  codex exec "$prompt" --model "$model" --dangerously-bypass-approvals-and-sandbox 2>/dev/null ;;
    glm)    opencode run "$prompt" -m "$model" --format default 2>/dev/null ;;
  esac
}

# --- Короткая подпись роли для вывода: "Claude/opus", "GLM/glm-5.2" ----------
label() {
  local e="$1" m="$2" short="$2"
  [ "$e" = "glm" ] && short="${m##*/}"
  case "$e" in
    claude) echo "Claude/$short" ;;
    codex)  echo "Codex/$short" ;;
    glm)    echo "GLM/$short" ;;
  esac
}

verify_file() {
  if [ -f "$1" ]; then
    printf '\033[92m💾 Файл сохранён: %s\033[0m\n' "$1"
  else
    printf '\033[91m⚠ Файл НЕ найден: %s\033[0m\n' "$1"
  fi
}

# --- Выбор слотов ----------------------------------------------------------
choose_slot "Начинающий (составляет и дорабатывает план)"
P_ENGINE="$SLOT_ENGINE"; P_MODEL="$SLOT_MODEL"
choose_slot "Отвечающий (критикует план)"
C_ENGINE="$SLOT_ENGINE"; C_MODEL="$SLOT_MODEL"

# --- Файлы вывода ----------------------------------------------------------
DATE=$(date +'%d.%m.%Y'); TIME=$(date +'%H.%M')
PLAN_DIR="$PROJECT_ROOT/docs/Plans"; mkdir -p "$PLAN_DIR"
PLAN_FILE="$PLAN_DIR/${DATE}_${TIME}_plan.md"
CRITIQUE_FILE="$PLAN_DIR/${DATE}_${TIME}_critique.md"
FINAL_FILE="$PLAN_DIR/${DATE}_${TIME}_plan-final.md"

C_MD='Отвечай на русском, в markdown, по делу. Не используй инструменты — только текстовый ответ.'

# --- Шаг 1: план -----------------------------------------------------------
echo ""
printf '\033[1m== Шаг 1: %s составляет план ==\033[0m\n' "$(label "$P_ENGINE" "$P_MODEL")"
PLAN=$(run_engine "$P_ENGINE" "$P_MODEL" "Составь подробный, реалистичный план по задаче: $TASK. Разбей на фазы и конкретные шаги, укажи зависимости, риски и критерии готовности. Верни только сам план. $C_MD")
printf '%s\n' "$PLAN"
printf '%s\n' "$PLAN" > "$PLAN_FILE"
verify_file "$PLAN_FILE"

# --- Шаг 2: критика --------------------------------------------------------
echo ""
printf '\033[1m== Шаг 2: %s критикует ==\033[0m\n' "$(label "$C_ENGINE" "$C_MODEL")"
CRITIQUE=$(run_engine "$C_ENGINE" "$C_MODEL" "Вот план по задаче «$TASK»:

$PLAN

Дай конструктивную критику: слабые места, риски, недостающие шаги, нестыковки, более сильные альтернативы. Верни только список замечаний. $C_MD")
printf '%s\n' "$CRITIQUE"
printf '%s\n' "$CRITIQUE" > "$CRITIQUE_FILE"
verify_file "$CRITIQUE_FILE"

# --- Шаг 3: доработка ------------------------------------------------------
echo ""
printf '\033[1m== Шаг 3: %s дорабатывает план ==\033[0m\n' "$(label "$P_ENGINE" "$P_MODEL")"
FINAL=$(run_engine "$P_ENGINE" "$P_MODEL" "Твой план по задаче «$TASK»:

$PLAN

Критика от другого ИИ:

$CRITIQUE

Реши, какие замечания принять, а какие отклонить — и почему. Выдай доработанный финальный план с учётом принятого. $C_MD")
printf '%s\n' "$FINAL"
printf '%s\n' "$FINAL" > "$FINAL_FILE"
verify_file "$FINAL_FILE"

echo ""
echo "Готово. Файлы в: $PLAN_DIR"
