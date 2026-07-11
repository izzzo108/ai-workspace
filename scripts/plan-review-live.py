#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Запуск ЧЕРЕЗ Git Bash:
#     python scripts/plan-review-live.py "текст задачи"
#
# План-ревью двумя LLM на выбор (Claude / Codex / GLM):
#   1. Для каждого из двух слотов выбираешь движок и модель:
#        - НАЧИНАЮЩИЙ  (составляет план и потом дорабатывает его),
#        - ОТВЕЧАЮЩИЙ  (критикует план).
#      Любой слот — любой из движков. GLM показывается, только если установлен opencode.
#   2. Пайплайн: начинающий составляет план -> отвечающий критикует -> начинающий
#      дорабатывает план с учётом критики.
#   3. В браузере (http://127.0.0.1:8765) видно живой ход работы блоками.
#
# Что сохраняется в docs/Plans/ :
#   <дата>_<время>_plan.md         — исходный план;
#   <дата>_<время>_critique.md     — критика;
#   <дата>_<время>_plan-final.md   — доработанный финальный план;
#   <дата>_<время>_conversation.html — статичная копия всего хода.
#
# Контекст между шагами передаётся моделям ПРЯМО В ПРОМПТЕ (inline), а все файлы
# пишет сам скрипт — поэтому в любом слоте одинаково работают все три движка.
# Общая логика движков и веб-UI — в agents_common.py.
#
# Окно терминала не закрывайте, пока идёт работа. Сервер остановится по Ctrl+C.

import sys
import os
import time
import datetime
from pathlib import Path

import agents_common as ac

PORT = 8765
PAGE_TITLE = "Claude &#8646; Codex &#8646; GLM &mdash; план-ревью"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
os.chdir(PROJECT_ROOT)
ac.enable_utf8_console()

TASK = " ".join(sys.argv[1:]).strip()
if not TASK:
    TASK = ac.ask_line("Текст задачи для планирования: ", required=True)

# --- Два слота -------------------------------------------------------------

SLOT_PLANNER = ac.choose_slot("Слот 1 — НАЧИНАЮЩИЙ (составляет и дорабатывает план)")
SLOT_CRITIC = ac.choose_slot("Слот 2 — ОТВЕЧАЮЩИЙ (критикует план)")

# --- Файлы вывода ----------------------------------------------------------

now = datetime.datetime.now()
DATE = now.strftime("%d.%m.%Y")
TIME = now.strftime("%H.%M")

PLAN_DIR = PROJECT_ROOT / "docs" / "Plans"
PLAN_DIR.mkdir(parents=True, exist_ok=True)

PLAN_FILE = PLAN_DIR / f"{DATE}_{TIME}_plan.md"
CRITIQUE_FILE = PLAN_DIR / f"{DATE}_{TIME}_critique.md"
FINAL_FILE = PLAN_DIR / f"{DATE}_{TIME}_plan-final.md"
CONVO_HTML = PLAN_DIR / f"{DATE}_{TIME}_conversation.html"

MD_SUFFIX = (
    "Отвечай на русском, в markdown, содержательно и по делу. "
    "Не используй инструменты (чтение/запись файлов) — только текстовый ответ. "
    "Не выводи служебный текст — только результат."
)


def plan_prompt():
    return (
        f"Составь подробный, реалистичный план по задаче:\n\n«{TASK}»\n\n"
        f"Разбей на фазы и конкретные шаги, укажи зависимости, риски и критерии готовности. "
        f"Верни только сам план. " + MD_SUFFIX
    )


def critique_prompt(plan_text):
    return (
        f"Вот план по задаче «{TASK}»:\n\n---\n{plan_text}\n---\n\n"
        f"Дай конструктивную критику: слабые места, риски, недостающие шаги, нестыковки, "
        f"более сильные альтернативы. Верни только список замечаний. " + MD_SUFFIX
    )


def final_prompt(plan_text, critique_text):
    return (
        f"Твой план по задаче «{TASK}»:\n\n---\n{plan_text}\n---\n\n"
        f"Критика от другого ИИ:\n\n---\n{critique_text}\n---\n\n"
        f"Реши, какие замечания принять, а какие отклонить — и почему. Выдай доработанный "
        f"финальный план с учётом принятого. " + MD_SUFFIX
    )


def save(path, text):
    path.write_text(text, encoding="utf-8")
    ac.verify_file(path)


# ===========================================================================
# Основной поток
# ===========================================================================

ac.serve(PORT, PAGE_TITLE)

ac.log("system", f"Задача: {TASK}")
ac.log("system", f"Начинающий: {ac.slot_desc(SLOT_PLANNER)} | "
                 f"Отвечающий: {ac.slot_desc(SLOT_CRITIC)}")

# --- Шаг 1: план -----------------------------------------------------------
plan = ac.run_agent_turn(
    SLOT_PLANNER, SLOT_PLANNER["engine"],
    f"Шаг 1: {ac.slot_desc(SLOT_PLANNER)} составляет план",
    plan_prompt(),
)
if plan:
    save(PLAN_FILE, plan)

# --- Шаг 2: критика --------------------------------------------------------
critique = ""
if plan:
    critique = ac.run_agent_turn(
        SLOT_CRITIC, SLOT_CRITIC["engine"],
        f"Шаг 2: {ac.slot_desc(SLOT_CRITIC)} критикует план",
        critique_prompt(plan),
    )
    if critique:
        save(CRITIQUE_FILE, critique)
else:
    ac.log("error", "Шаг 2 пропущен: нет плана для критики.")

# --- Шаг 3: доработка ------------------------------------------------------
if plan and critique:
    final = ac.run_agent_turn(
        SLOT_PLANNER, SLOT_PLANNER["engine"],
        f"Шаг 3: {ac.slot_desc(SLOT_PLANNER)} дорабатывает план",
        final_prompt(plan, critique),
    )
    if final:
        save(FINAL_FILE, final)
elif plan and not critique:
    ac.log("error", "Шаг 3 пропущен: нет критики — сохраняю исходный план как финальный.")
    save(FINAL_FILE, plan)

ac.status["done"] = True
ac.log("system", f"Готово. Файлы в: {PLAN_DIR}")

# Статичная копия всего хода — открыть можно позже, без запущенного сервера.
CONVO_HTML.write_text(ac.build_static_html(ac.snapshot_events(), PAGE_TITLE), encoding="utf-8")
ac.verify_file(CONVO_HTML)

print("Готово. Файлы в:")
print(f"  {PLAN_DIR}")
print("Страница в браузере останется открытой. Чтобы остановить сервер — нажмите Ctrl+C здесь.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
