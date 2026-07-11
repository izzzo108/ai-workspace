#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Запуск ЧЕРЕЗ Git Bash:
#     python scripts/brainstorm-live.py "тема (необязательно)"
#
# Мозговой штурм ТРЁХ LLM на выбор (Claude / Codex / GLM) с подведением итогов:
#   1. Заполняешь БРИФ. Два обязательных поля: сама задача и «какой финальный
#      результат хочешь» (план / список идей / HTML-страница / …).
#   2. Для каждого из трёх слотов выбираешь движок и модель:
#        - НАЧИНАЮЩИЙ  (предлагает идеи),
#        - ОТВЕЧАЮЩИЙ  (критикует / оппонирует),
#        - ПОДВОДЯЩИЙ ИТОГИ (собирает финальный результат в нужном виде).
#      Любой слот — любой из движков. GLM показывается, только если установлен opencode.
#   3. Задаёшь число раундов (1 раунд = «предложение + критика»).
#   4. В браузере (http://127.0.0.1:8765) идёт живая читаемая переписка блоками.
#   5. Подводящий итоги выдаёт финальный результат ровно в запрошенном виде.
#
# Что сохраняется — в папку docs/brainstorm/<тема>/<дата>_<время>/ :
#   - transcript.md        — бриф + все раунды + финал;
#   - conversation.html    — статичная копия всего диалога (открыть можно позже);
#   - result.md / result.html — финальный результат в запрошенном виде.
#
# Требует: CLI claude, codex и (для GLM) opencode в PATH — те, что доступны в Git Bash.
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
PAGE_TITLE = "Claude &#8646; Codex &#8646; GLM &mdash; мозговой штурм"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
os.chdir(PROJECT_ROOT)
ac.enable_utf8_console()


# ---------------------------------------------------------------------------
# 1. Бриф
# ---------------------------------------------------------------------------

TOPIC_PRESET = " ".join(sys.argv[1:]).strip() or None

# (key, подпись, обязательный)
BRIEF_POINTS = [
    ("topic", "Задача / тема (о чём думаем)", True),
    ("output", "Какой финальный результат хочешь (план / список идей / HTML-страница / сравнение …)", True),
    ("context", "Контекст и ограничения (стек, платформа, бюджет, сроки)", False),
    ("known", "Что уже известно / что пробовали", False),
    ("success", "Критерии успеха / на какие вопросы нужен ответ", False),
    ("out_of_scope", "Что НЕ рассматриваем (вне рамок)", False),
]

print("=" * 66)
print("МОЗГОВОЙ ШТУРМ (Claude / Codex / GLM) — заполните бриф")
print("Enter = пропустить необязательный пункт")
print("=" * 66)

brief = {}
brief_lines = []
for key, label, required in BRIEF_POINTS:
    preset = TOPIC_PRESET if key == "topic" else None
    if preset:
        print(f"{label}: {preset}   (из аргумента запуска)")
    value = ac.ask_line(f"{label}: ", required=required, preset=preset)
    if value:
        brief[key] = value
        brief_lines.append((label, value))

TOPIC = brief.get("topic", "(без темы)")
DESIRED_OUTPUT = brief.get("output", "").strip()
BRIEF_MD = "## БРИФ\n" + "\n".join(f"- **{label}:** {v}" for label, v in brief_lines)

# ---------------------------------------------------------------------------
# 2. Три слота
# ---------------------------------------------------------------------------

SLOT_PROPOSER = ac.choose_slot("Слот 1 — НАЧИНАЮЩИЙ (предлагает идеи)")
SLOT_CRITIC = ac.choose_slot("Слот 2 — ОТВЕЧАЮЩИЙ (критикует / оппонирует)")
SLOT_ARBITER = ac.choose_slot("Слот 3 — ПОДВОДЯЩИЙ ИТОГИ (готовит финальный результат)")

# ---------------------------------------------------------------------------
# 3. Раунды
# ---------------------------------------------------------------------------


def ask_rounds():
    while True:
        raw = ac.ask_line("\nСколько раундов обсуждения? (1 раунд = предложение + критика): ", required=True)
        if raw.isdigit() and int(raw) >= 1:
            n = int(raw)
            if n > 10:
                confirm = input(f"{n} раундов — это много токенов и времени. Продолжить? (y/n): ").strip().lower()
                if confirm not in ("y", "yes", "д", "да"):
                    continue
            return n
        print("Введите целое число ≥ 1.")


ROUNDS = ask_rounds()

# ---------------------------------------------------------------------------
# Папка-сессия: docs/brainstorm/<тема>/<дата>_<время>/
# ---------------------------------------------------------------------------

now = datetime.datetime.now()
DATE = now.strftime("%d.%m.%Y")
TIME = now.strftime("%H.%M")

TOPIC_DIR = PROJECT_ROOT / "docs" / "brainstorm" / ac.slugify(TOPIC)
SESSION_DIR = TOPIC_DIR / f"{DATE}_{TIME}"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

TRANSCRIPT_FILE = SESSION_DIR / "transcript.md"
CONVO_HTML = SESSION_DIR / "conversation.html"
# путь result-файла (md/html) определим после ответа подводящего итоги


# ---------------------------------------------------------------------------
# Транскрипт (владелец записи — только этот скрипт)
# ---------------------------------------------------------------------------

turns = []  # список (заголовок, текст) — источник для inline-контекста и для файла


def write_transcript_header():
    participants = (
        "**Участники:**\n"
        f"- Начинающий: {ac.slot_desc(SLOT_PROPOSER)}\n"
        f"- Отвечающий: {ac.slot_desc(SLOT_CRITIC)}\n"
        f"- Подводящий итоги: {ac.slot_desc(SLOT_ARBITER)}\n"
        f"\nРаундов: {ROUNDS}"
    )
    header = f"# Мозговой штурм — {DATE} {TIME}\n\n{BRIEF_MD}\n\n{participants}\n"
    TRANSCRIPT_FILE.write_text(header, encoding="utf-8")
    ac.verify_file(TRANSCRIPT_FILE)


def record_turn(heading, text):
    turns.append((heading, text))
    with open(TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n\n## {heading}\n\n{text}\n")


def transcript_inline(max_chars=ac.INLINE_TRANSCRIPT_LIMIT):
    """Собирает переписку для вставки в промпт. Держит самые свежие ходы в пределах
    лимита; более ранние подрезает (с пометкой), чтобы не превысить длину команды."""
    chunks = [f"### {h}\n{t}\n" for h, t in turns]
    kept, total = [], 0
    for chunk in reversed(chunks):
        if total + len(chunk) > max_chars and kept:
            kept.append("### (более ранние ходы опущены для краткости)\n")
            break
        kept.append(chunk)
        total += len(chunk)
    return "\n".join(reversed(kept))


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------

DISCUSS_SUFFIX = (
    "Отвечай на русском, содержательно и по делу, markdown-списками. "
    "Не используй инструменты (чтение/запись файлов, запуск команд) — только текстовый ответ. "
    "Не выводи служебный текст — только сам ответ."
)


def proposer_prompt(r):
    task = (
        "Предложи конкретную идею/подход по теме брифа."
        if r == 1 else
        "Учти последнюю критику: доработай своё предложение, ответь на замечания, "
        "при необходимости предложи более сильную альтернативу."
    )
    context = "" if r == 1 else f"Переписка на данный момент:\n\n{transcript_inline()}\n\n"
    return (
        f"Идёт мозговой штурм. Бриф:\n\n{BRIEF_MD}\n\n"
        f"{context}"
        f"Твоя роль — генератор идей. Раунд {r} из {ROUNDS}. {task} "
        + DISCUSS_SUFFIX
    )


def critic_prompt(r):
    return (
        f"Идёт мозговой штурм. Бриф:\n\n{BRIEF_MD}\n\n"
        f"Переписка на данный момент:\n\n{transcript_inline()}\n\n"
        f"Твоя роль — критик/оппонент. Раунд {r} из {ROUNDS}. Разбери последнее предложение "
        f"генератора: слабые места, риски, скрытые допущения, пропущенные шаги, более сильные "
        f"альтернативы. Будь конкретным и конструктивным. "
        + DISCUSS_SUFFIX
    )


def arbiter_prompt():
    return (
        f"Заверши мозговой штурм и подведи итоги. Бриф:\n\n{BRIEF_MD}\n\n"
        f"Вся переписка участников:\n\n{transcript_inline(max_chars=40000)}\n\n"
        f"Твоя роль — подвести итоги и выдать ФИНАЛЬНЫЙ результат ровно в том виде, "
        f"который запросили в брифе (поле «Какой финальный результат хочешь»):\n"
        f"  → {DESIRED_OUTPUT or 'структурированный итог обсуждения'}\n\n"
        f"Собери лучшие идеи из обсуждения, отбрось слабые. "
        f"Если просили HTML-страницу — верни ПОЛНЫЙ валидный HTML-документ "
        f"(начиная с <!DOCTYPE html>, с минимальным читаемым оформлением). "
        f"Если просили план / список / текст — верни markdown. "
        f"Если уместно для формата, в начале кратко отметь, какие идеи принял, а какие отклонил. "
        f"Верни ТОЛЬКО финальный результат, без рассказа о процессе. "
        f"Отвечай на русском. Не используй инструменты — только сам результат в ответе."
    )


def looks_html(text, desired):
    if "html" in (desired or "").lower():
        return True
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


# ===========================================================================
# Основной поток
# ===========================================================================

ac.serve(PORT, PAGE_TITLE)

ac.log("system", f"Задача: {TOPIC}")
ac.log("system", f"Хочу на выходе: {DESIRED_OUTPUT or '(не указано)'}")
ac.log("system", f"Начинающий: {ac.slot_desc(SLOT_PROPOSER)} | "
                 f"Отвечающий: {ac.slot_desc(SLOT_CRITIC)} | "
                 f"Итоги: {ac.slot_desc(SLOT_ARBITER)}")
ac.log("system", f"Раундов: {ROUNDS}")

write_transcript_header()

# --- Раунды: начинающий -> отвечающий --------------------------------------
for r in range(1, ROUNDS + 1):
    reply1 = ac.run_agent_turn(
        SLOT_PROPOSER, SLOT_PROPOSER["engine"],
        f"Раунд {r}: предлагает {ac.slot_desc(SLOT_PROPOSER)}",
        proposer_prompt(r),
    )
    if reply1:
        record_turn(f"Раунд {r} — Предлагает: {ac.slot_desc(SLOT_PROPOSER)}", reply1)

    reply2 = ac.run_agent_turn(
        SLOT_CRITIC, SLOT_CRITIC["engine"],
        f"Раунд {r}: критикует {ac.slot_desc(SLOT_CRITIC)}",
        critic_prompt(r),
    )
    if reply2:
        record_turn(f"Раунд {r} — Критикует: {ac.slot_desc(SLOT_CRITIC)}", reply2)

# --- Подведение итогов + финальный результат в запрошенном виде -------------
result = ac.run_agent_turn(
    SLOT_ARBITER, "arbiter",
    f"Итоги — {ac.slot_desc(SLOT_ARBITER)} готовит финальный результат",
    arbiter_prompt(),
)
if result:
    record_turn(f"Финал — {ac.slot_desc(SLOT_ARBITER)}", result)
    if looks_html(result, DESIRED_OUTPUT):
        result_file = SESSION_DIR / "result.html"
        result_file.write_text(result, encoding="utf-8")
    else:
        result_file = SESSION_DIR / "result.md"
        result_file.write_text(
            f"# Финальный результат — {DATE} {TIME}\n\n"
            f"**Задача:** {TOPIC}\n\n"
            f"**Запрошенный вид:** {DESIRED_OUTPUT or '(не указано)'}\n\n---\n\n{result}\n",
            encoding="utf-8",
        )
    ac.verify_file(result_file)
else:
    ac.log("error", "Подводящий итоги не вернул результат — файл результата не создан.")

ac.status["done"] = True
ac.log("system", f"Готово. Папка сессии: {SESSION_DIR}")

# Статичная копия всего диалога — открыть можно позже, без запущенного сервера.
CONVO_HTML.write_text(ac.build_static_html(ac.snapshot_events(), PAGE_TITLE), encoding="utf-8")
ac.verify_file(CONVO_HTML)

print("Готово. Все файлы — в папке:")
print(f"  {SESSION_DIR}")
print("Страница в браузере останется открытой. Чтобы остановить сервер — нажмите Ctrl+C здесь.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
