#!/bin/bash
# ============================================================================
# Запуск ЧЕРЕЗ Git Bash:
#     ./scripts/plan-review.sh "текст задачи"
#     (или: bash scripts/plan-review.sh "текст задачи")
#
# План-ревью двумя LLM на выбор (Claude / Codex / GLM), вывод прямо в терминал:
#   начинающий составляет план -> отвечающий критикует -> начинающий дорабатывает.
#
# Для каждой роли выбираешь движок, модель и глубину размышления (в том виде, как её
# даёт движок: Claude --effort, Codex model_reasoning_effort, GLM --variant).
# GLM берётся через opencode (провайдер zai-coding-plan) и показывается в выборе,
# ТОЛЬКО если opencode установлен.
#
# Контекст между шагами передаётся моделям прямо в промпте, а все файлы пишет сам
# скрипт — так в любом слоте одинаково работают все три движка.
#
# Требует: CLI claude, codex и (опц.) opencode в PATH — те, что доступны в Git Bash.
# Живая версия с просмотром в браузере (и полным списком уровней): plan-review-live.py
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

# --- Меню глубины размышления: заполняет SLOT_EFFORT (пусто = не задавать) ---
SLOT_EFFORT=""
choose_effort() {
  local engine="$1" model="$2"
  SLOT_EFFORT=""
  local levels=()
  if [ "$engine" = "claude" ]; then
    levels=(low medium high xhigh max)
  elif [ "$engine" = "codex" ]; then
    # уровни зависят от модели (как их даёт codex): у 5.6 глубже, у 5.5/5.4 — до xhigh
    case "$model" in
      gpt-5.6-sol|gpt-5.6-terra) levels=(low medium high xhigh max ultra) ;;
      gpt-5.6-luna)              levels=(low medium high xhigh max) ;;
      *)                         levels=(low medium high xhigh) ;;
    esac
  else  # glm: градации есть только у моделей с reasoning_options=effort (напр. glm-5.2)
    if [ "$model" = "zai-coding-plan/glm-5.2" ]; then
      levels=("обычная (по умолчанию)" high max)
    else
      return  # у модели нет градаций — размышление по умолчанию
    fi
  fi
  echo "Глубина размышления:"
  local i=1
  for l in "${levels[@]}"; do echo "  $i) $l"; i=$((i + 1)); done
  local n
  while true; do
    read -r -p "Число (1-${#levels[@]}): " n
    if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "${#levels[@]}" ]; then
      SLOT_EFFORT="${levels[$((n - 1))]}"
      break
    fi
    echo "Введите число 1-${#levels[@]}"
  done
  # «обычная …» для GLM = не передавать --variant
  case "$SLOT_EFFORT" in "обычная"*) SLOT_EFFORT="" ;; esac
}

# --- Выбор движка/модели/глубины для роли: SLOT_ENGINE, SLOT_MODEL, SLOT_EFFORT
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
    echo "  1) gpt-5.6-sol (флагман)   2) gpt-5.6-terra (баланс)   3) gpt-5.6-luna (быстрая)"
    echo "  4) gpt-5.5   5) gpt-5.4   6) gpt-5.4-mini"
    while true; do read -r -p "Число (1-6): " m; case "$m" in
      1) SLOT_MODEL="gpt-5.6-sol"; break ;;   2) SLOT_MODEL="gpt-5.6-terra"; break ;;
      3) SLOT_MODEL="gpt-5.6-luna"; break ;;  4) SLOT_MODEL="gpt-5.5"; break ;;
      5) SLOT_MODEL="gpt-5.4"; break ;;       6) SLOT_MODEL="gpt-5.4-mini"; break ;;
      *) echo "Введите 1-6" ;; esac; done
  else
    echo "  1) glm-5.2 (уровни high/max)   2) glm-5.1   3) glm-4.7   4) glm-5-turbo   5) glm-4.5-air"
    while true; do read -r -p "Число (1-5): " m; case "$m" in
      1) SLOT_MODEL="zai-coding-plan/glm-5.2"; break ;;
      2) SLOT_MODEL="zai-coding-plan/glm-5.1"; break ;;
      3) SLOT_MODEL="zai-coding-plan/glm-4.7"; break ;;
      4) SLOT_MODEL="zai-coding-plan/glm-5-turbo"; break ;;
      5) SLOT_MODEL="zai-coding-plan/glm-4.5-air"; break ;;
      *) echo "Введите 1-5" ;; esac; done
  fi

  choose_effort "$SLOT_ENGINE" "$SLOT_MODEL"
  echo "→ выбрано: $(label "$SLOT_ENGINE" "$SLOT_MODEL" "$SLOT_EFFORT")"
}

# --- Запуск одного хода: печатает чистый текст ответа в stdout ---------------
# У всех трёх CLI осмысленный ответ идёт в stdout, а служебный вывод — в stderr,
# поэтому stderr глушим (2>/dev/null). Глубина размышления передаётся так, как её
# принимает каждый движок.
run_engine() {
  local engine="$1" model="$2" prompt="$3" effort="$4"
  case "$engine" in
    claude)
      if [ -n "$effort" ]; then claude -p "$prompt" --model "$model" --effort "$effort" 2>/dev/null
      else claude -p "$prompt" --model "$model" 2>/dev/null; fi ;;
    codex)
      if [ -n "$effort" ]; then codex exec "$prompt" --model "$model" -c model_reasoning_effort="$effort" --dangerously-bypass-approvals-and-sandbox 2>/dev/null
      else codex exec "$prompt" --model "$model" --dangerously-bypass-approvals-and-sandbox 2>/dev/null; fi ;;
    glm)
      if [ -n "$effort" ]; then opencode run "$prompt" -m "$model" --format default --variant "$effort" 2>/dev/null
      else opencode run "$prompt" -m "$model" --format default 2>/dev/null; fi ;;
  esac
}

# --- Короткая подпись роли: "Claude/opus (high)", "GLM/glm-5.2 (max)" --------
label() {
  local e="$1" m="$2" eff="$3" short="$2" name=""
  [ "$e" = "glm" ] && short="${m##*/}"
  case "$e" in
    claude) name="Claude/$short" ;;
    codex)  name="Codex/$short" ;;
    glm)    name="GLM/$short" ;;
  esac
  [ -n "$eff" ] && name="$name ($eff)"
  echo "$name"
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
P_ENGINE="$SLOT_ENGINE"; P_MODEL="$SLOT_MODEL"; P_EFFORT="$SLOT_EFFORT"
choose_slot "Отвечающий (критикует план)"
C_ENGINE="$SLOT_ENGINE"; C_MODEL="$SLOT_MODEL"; C_EFFORT="$SLOT_EFFORT"

# --- Файлы вывода ----------------------------------------------------------
DATE=$(date +'%d.%m.%Y'); TIME=$(date +'%H.%M')
PLAN_DIR="$PROJECT_ROOT/docs/Plans"; mkdir -p "$PLAN_DIR"
PLAN_FILE="$PLAN_DIR/${DATE}_${TIME}_plan.md"
CRITIQUE_FILE="$PLAN_DIR/${DATE}_${TIME}_critique.md"
FINAL_FILE="$PLAN_DIR/${DATE}_${TIME}_plan-final.md"

C_MD='Отвечай на русском, в markdown, по делу. Не используй инструменты — только текстовый ответ.'

# --- Шаг 1: план -----------------------------------------------------------
echo ""
printf '\033[1m== Шаг 1: %s составляет план ==\033[0m\n' "$(label "$P_ENGINE" "$P_MODEL" "$P_EFFORT")"
PLAN=$(run_engine "$P_ENGINE" "$P_MODEL" "Составь подробный, реалистичный план по задаче: $TASK. Разбей на фазы и конкретные шаги, укажи зависимости, риски и критерии готовности. Верни только сам план. $C_MD" "$P_EFFORT")
printf '%s\n' "$PLAN"
printf '%s\n' "$PLAN" > "$PLAN_FILE"
verify_file "$PLAN_FILE"

# --- Шаг 2: критика --------------------------------------------------------
echo ""
printf '\033[1m== Шаг 2: %s критикует ==\033[0m\n' "$(label "$C_ENGINE" "$C_MODEL" "$C_EFFORT")"
CRITIQUE=$(run_engine "$C_ENGINE" "$C_MODEL" "Вот план по задаче «$TASK»:

$PLAN

Дай конструктивную критику: слабые места, риски, недостающие шаги, нестыковки, более сильные альтернативы. Верни только список замечаний. $C_MD" "$C_EFFORT")
printf '%s\n' "$CRITIQUE"
printf '%s\n' "$CRITIQUE" > "$CRITIQUE_FILE"
verify_file "$CRITIQUE_FILE"

# --- Шаг 3: доработка ------------------------------------------------------
echo ""
printf '\033[1m== Шаг 3: %s дорабатывает план ==\033[0m\n' "$(label "$P_ENGINE" "$P_MODEL" "$P_EFFORT")"
FINAL=$(run_engine "$P_ENGINE" "$P_MODEL" "Твой план по задаче «$TASK»:

$PLAN

Критика от другого ИИ:

$CRITIQUE

Реши, какие замечания принять, а какие отклонить — и почему. Выдай доработанный финальный план с учётом принятого. $C_MD" "$P_EFFORT")
printf '%s\n' "$FINAL"
printf '%s\n' "$FINAL" > "$FINAL_FILE"
verify_file "$FINAL_FILE"

echo ""
echo "Готово. Файлы в: $PLAN_DIR"
