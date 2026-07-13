#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Запуск ЧЕРЕЗ Git Bash:
#     python scripts/consilium-live.py "имя папки штурма (необязательно)"
#
# КОНСИЛИУМ по итогам мозгового штурма (brainstorm-live.py): ТРИ LLM на выбор
# (Claude / Codex / GLM) выбирают ЛУЧШИЙ вариант из проведённого штурма и
# составляют по нему максимально развёрнутое ТЗ:
#   1. Выбираешь сессию штурма из docs/brainstorm/ (или передаёшь имя папки
#      аргументом). Скрипт читает transcript.md и текст итоговой result.html.
#   2. Необязательные уточнения: критерии выбора, ограничения, акценты для ТЗ.
#   3. Для каждого из трёх слотов выбираешь движок, модель и глубину размышления:
#        - ЗАЩИТНИК      (выписывает шорт-лист вариантов штурма, выбирает лучший
#                         и обосновывает выбор),
#        - ОППОНЕНТ      (проверяет выбор на прочность, продвигает альтернативы),
#        - СОСТАВИТЕЛЬ ТЗ (фиксирует финальное решение и пишет развёрнутое ТЗ).
#      Любой слот — любой из движков. GLM показывается, только если установлен opencode.
#   4. Задаёшь число раундов (1 раунд = «защита + оппонирование»).
#   5. В браузере (http://127.0.0.1:8766) идёт живая читаемая переписка блоками.
#      Порт отличается от brainstorm-live (8765) — консилиум можно запускать,
#      не закрывая страницу штурма.
#
# Что сохраняется — в папку docs/consilium/<ваше-имя>_ДД_ММ_ГГГГ/ :
#   - tz.md              — развёрнутое ТЗ (markdown) — главный артефакт: его можно
#                          отдавать планировщику/агентам как вход для реализации;
#   - tz.html            — то же ТЗ красивой самодостаточной страницей; по завершении
#                          открывается автоматически в браузере;
#   - conversation.html  — статичная копия всего диалога (открыть можно позже);
#   - transcript.md      — материалы + все раунды + финал.
#
# Требует: CLI claude, codex и (для GLM) opencode в PATH — те, что доступны в Git Bash.
# Общая логика движков и веб-UI — в agents_common.py.
#
# Окно терминала не закрывайте, пока идёт работа. Сервер остановится по Ctrl+C.

import sys
import os
import re
import time
import html
import datetime
from pathlib import Path

import agents_common as ac

PORT = 8766
PAGE_TITLE = "Claude &#8646; Codex &#8646; GLM &mdash; консилиум: выбор варианта и ТЗ"

# Лимиты на вставку текста в промпт. ВАЖНО: промпт передаётся CLI одним аргументом
# командной строки, а на Windows её длина ограничена ~32 тыс. символов — поэтому
# материалы штурма и переписку консилиума подрезаем так, чтобы суммарный промпт
# гарантированно проходил.
MATERIAL_LIMIT = 12000      # материалы штурма (transcript + текст result.html)
DEBATE_LIMIT = 10000        # переписка консилиума в ходах защитника/оппонента
DEBATE_LIMIT_FINAL = 12000  # переписка консилиума в финальном ходе составителя ТЗ
BRIEF_LIMIT = 2000          # уточнения заказчика (BRIEF_MD) — тоже входят в каждый промпт
TZ_HTML_LIMIT = 24000       # макс. размер ТЗ для оформления HTML моделью; ТЗ длиннее
                            # рендерим локально (md_to_html), чтобы НИЧЕГО не потерять

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
os.chdir(PROJECT_ROOT)
ac.enable_utf8_console()


# ---------------------------------------------------------------------------
# Материалы штурма
# ---------------------------------------------------------------------------

def html_to_text(raw):
    """Достаёт читаемый текст из result.html штурма (без script/style и тегов)."""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>|</tr>|</div>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def clip_middle(text, limit):
    """Подрезает длинный текст, сохраняя начало (бриф) и конец (финал) — середина
    обсуждения наименее ценна для выбора варианта."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return (text[:head] + "\n\n…(середина материалов опущена для краткости)…\n\n"
            + text[-tail:])


def pick_brainstorm_session(preset=None):
    """Выбор сессии штурма: docs/brainstorm/, свежие первыми. preset — имя папки
    (или её часть) из аргумента запуска; путь к папке тоже принимается."""
    root = PROJECT_ROOT / "docs" / "brainstorm"
    sessions = []
    if root.is_dir():
        sessions = sorted(
            (d for d in root.iterdir() if d.is_dir() and (d / "transcript.md").is_file()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
    if preset:
        p = Path(preset)
        if p.is_dir() and (p / "transcript.md").is_file():
            return p
        matches = [d for d in sessions if preset.lower() in d.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            print(f"«{preset}» подходит к нескольким папкам штурма — выберите из списка.")
        else:
            print(f"Папка штурма «{preset}» не найдена — выберите из списка.")
    if not sessions:
        print("В docs/brainstorm/ нет ни одной сессии с transcript.md.")
        print("Сначала проведите штурм: python scripts/brainstorm-live.py")
        sys.exit(1)

    def label(d):
        ts = datetime.datetime.fromtimestamp(d.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        return f"{d.name}  ({ts})"

    # Первый вопрос — имя папки штурма: принимаем номер из списка ЛИБО имя/часть имени
    # (без кавычек), чтобы не заставлять передавать его аргументом командной строки.
    print("Какой мозговой штурм разбираем? Сессии (свежие первыми):")
    for i, d in enumerate(sessions, 1):
        print(f"  {i}. {label(d)}")
    while True:
        raw = ac.ask_line("Введите номер или имя папки (можно часть имени): ", required=True)
        if raw.isdigit() and 1 <= int(raw) <= len(sessions):
            return sessions[int(raw) - 1]
        matches = [d for d in sessions if raw.lower() in d.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            print("«{0}» подходит к нескольким: {1} — уточните.".format(
                raw, ", ".join(d.name for d in matches)))
        else:
            print(f"«{raw}» не найдено — введите номер из списка или часть имени.")


def load_material(session_dir):
    """Материалы штурма для промптов: transcript.md + текст итоговой страницы."""
    parts = [(session_dir / "transcript.md").read_text(encoding="utf-8", errors="replace")]
    result_html = session_dir / "result.html"
    if result_html.is_file():
        text = html_to_text(result_html.read_text(encoding="utf-8", errors="replace"))
        if text:
            parts.append("## Итоговая страница штурма (текст)\n\n" + text)
    return clip_middle("\n\n".join(parts), MATERIAL_LIMIT)


# ---------------------------------------------------------------------------
# 1. Бриф
# ---------------------------------------------------------------------------

SESSION_PRESET = " ".join(sys.argv[1:]).strip() or None

print("=" * 66)
print("КОНСИЛИУМ ПО ИТОГАМ ШТУРМА (Claude / Codex / GLM)")
print("=" * 66)

BRAINSTORM_DIR = pick_brainstorm_session(SESSION_PRESET)
print(f"Разбираем штурм: {BRAINSTORM_DIR.name}")
MATERIAL_MD = load_material(BRAINSTORM_DIR)

# Необязательные уточнения — задают рамки выбора и акценты для ТЗ. Enter = пропустить.
print("\nНеобязательные уточнения (Enter — пропустить):")
OPTIONAL_POINTS = [
    "Критерии выбора (что важнее: скорость, стоимость, простота, надёжность…)",
    "Ограничения (стек, платформа, бюджет, сроки)",
    "Пожелания к ТЗ (на чём сделать акцент)",
]
extras = []
for label in OPTIONAL_POINTS:
    v = ac.ask_line(f"  • {label}:\n  > ", required=False)
    if v:
        extras.append((label, v))

# Как назвать папку с результатами (обязательно).
FOLDER_NAME = ac.ask_line("\nКак назвать папку с результатами? ", required=True)

BRIEF_MD = f"## Источник\nМозговой штурм: {BRAINSTORM_DIR.name}"
if extras:
    BRIEF_MD += "\n\n## Уточнения заказчика\n" + "\n".join(f"- **{l}:** {v}" for l, v in extras)
# Бриф тоже входит в каждый промпт — подрезаем, чтобы вместе с материалами и перепиской
# гарантированно уложиться в лимит длины командной строки Windows (~32 тыс. символов).
BRIEF_MD = clip_middle(BRIEF_MD, BRIEF_LIMIT)

# ---------------------------------------------------------------------------
# 2. Три слота
# ---------------------------------------------------------------------------

SLOT_ADVOCATE = ac.choose_slot("Слот 1 — ЗАЩИТНИК (выбирает лучший вариант и обосновывает)")
SLOT_OPPONENT = ac.choose_slot("Слот 2 — ОППОНЕНТ (проверяет выбор на прочность)")
SLOT_SPECWRITER = ac.choose_slot("Слот 3 — СОСТАВИТЕЛЬ ТЗ (финальное решение + развёрнутое ТЗ)")

# ---------------------------------------------------------------------------
# 3. Раунды
# ---------------------------------------------------------------------------


def ask_rounds():
    while True:
        raw = ac.ask_line("\nСколько раундов обсуждения? (1 раунд = защита + оппонирование): ", required=True)
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
# Папка-сессия: docs/consilium/<ваше-имя>_ДД_ММ_ГГГГ/
# ---------------------------------------------------------------------------

now = datetime.datetime.now()
DATE = now.strftime("%d_%m_%Y")        # ДД_ММ_ГГГГ — для имени папки
DATE_HUMAN = now.strftime("%d.%m.%Y")
TIME = now.strftime("%H.%M")

_base = f"{ac.slugify(FOLDER_NAME)}_{DATE}"
CONSILIUM_ROOT = PROJECT_ROOT / "docs" / "consilium"
SESSION_DIR = CONSILIUM_ROOT / _base
_n = 2
while SESSION_DIR.exists():             # не перезаписываем прошлый прогон с тем же именем
    SESSION_DIR = CONSILIUM_ROOT / f"{_base}_{_n}"
    _n += 1
SESSION_DIR.mkdir(parents=True, exist_ok=True)

TRANSCRIPT_FILE = SESSION_DIR / "transcript.md"
CONVO_HTML = SESSION_DIR / "conversation.html"
TZ_MD = SESSION_DIR / "tz.md"
TZ_HTML = SESSION_DIR / "tz.html"


# ---------------------------------------------------------------------------
# Транскрипт (владелец записи — только этот скрипт)
# ---------------------------------------------------------------------------

turns = []  # список (заголовок, текст) — источник для inline-контекста и для файла


def write_transcript_header():
    participants = (
        "**Участники:**\n"
        f"- Защитник: {ac.slot_desc(SLOT_ADVOCATE)}\n"
        f"- Оппонент: {ac.slot_desc(SLOT_OPPONENT)}\n"
        f"- Составитель ТЗ: {ac.slot_desc(SLOT_SPECWRITER)}\n"
        f"\nРаундов: {ROUNDS}"
    )
    header = f"# Консилиум — {DATE_HUMAN} {TIME}\n\n{BRIEF_MD}\n\n{participants}\n"
    TRANSCRIPT_FILE.write_text(header, encoding="utf-8")
    ac.verify_file(TRANSCRIPT_FILE)


def record_turn(heading, text):
    turns.append((heading, text))
    with open(TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n\n## {heading}\n\n{text}\n")


def debate_inline(max_chars=DEBATE_LIMIT):
    """Собирает переписку консилиума для вставки в промпт. Держит самые свежие ходы
    в пределах лимита; более ранние подрезает (с пометкой)."""
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


def advocate_prompt(r):
    if r == 1:
        task = (
            "1) Выпиши коротким нумерованным списком все различимые варианты/подходы, "
            "прозвучавшие в штурме (шорт-лист; дальше все ссылаются на эти номера). "
            "2) Выбери ОДИН лучший вариант (обоснованный гибрид двух — можно, но назови его явно) "
            "и обоснуй выбор по критериям заказчика. "
            "3) Честно скажи, чем жертвуем, отклоняя остальные варианты."
        )
        context = ""
    else:
        task = (
            "Учти последнюю критику оппонента: защити свой выбор по пунктам или, если аргументы "
            "убедительны, честно смени вариант (объясни почему). Углубляй конкретику выбранного "
            "решения — к финалу оно должно быть однозначным."
        )
        context = f"Переписка консилиума на данный момент:\n\n{debate_inline()}\n\n"
    return (
        f"Идёт консилиум по итогам мозгового штурма: нужно выбрать лучший вариант.\n\n"
        f"{BRIEF_MD}\n\n"
        f"Материалы штурма:\n\n{MATERIAL_MD}\n\n"
        f"{context}"
        f"Твоя роль — защитник лучшего варианта. Раунд {r} из {ROUNDS}. {task} "
        + DISCUSS_SUFFIX
    )


def opponent_prompt(r):
    return (
        f"Идёт консилиум по итогам мозгового штурма: нужно выбрать лучший вариант.\n\n"
        f"{BRIEF_MD}\n\n"
        f"Материалы штурма:\n\n{MATERIAL_MD}\n\n"
        f"Переписка консилиума на данный момент:\n\n{debate_inline()}\n\n"
        f"Твоя роль — оппонент. Раунд {r} из {ROUNDS}. Проверь выбор защитника на прочность: "
        f"слабые места и риски выбранного варианта, скрытые допущения, во что он обойдётся. "
        f"Сравни с сильнейшей альтернативой из шорт-листа; если считаешь другой вариант "
        f"сильнее — аргументируй за него по тем же критериям. Будь конкретным и конструктивным. "
        + DISCUSS_SUFFIX
    )


def spec_prompt():
    return (
        f"Заверши консилиум: зафиксируй финальное решение и составь развёрнутое ТЗ.\n\n"
        f"{BRIEF_MD}\n\n"
        f"Материалы штурма:\n\n{MATERIAL_MD}\n\n"
        f"Вся переписка консилиума:\n\n{debate_inline(max_chars=DEBATE_LIMIT_FINAL)}\n\n"
        f"Твоя роль — составитель ТЗ. Сам реши с учётом всей дискуссии, какой вариант победил "
        f"(обоснованный гибрид допустим), и напиши по нему МАКСИМАЛЬНО развёрнутое ТЗ — полный "
        f"вход для реализации задачи. Структура (markdown, заголовки ##, нумерация сохраняется):\n"
        f"# ТЗ: <короткое название решения>\n"
        f"## 1. Выбранное решение и обоснование (+ какие альтернативы отклонены и почему)\n"
        f"## 2. Цели и критерии успеха\n"
        f"## 3. Границы работ (что входит / что НЕ входит)\n"
        f"## 4. Функциональные требования (пронумерованные: ФТ-1, ФТ-2, …)\n"
        f"## 5. Нефункциональные требования (производительность, безопасность, удобство…)\n"
        f"## 6. Ограничения и допущения\n"
        f"## 7. Что нужно для старта (входные данные, доступы, ресурсы, решения заказчика)\n"
        f"## 8. Этапы работ и порядок\n"
        f"## 9. Критерии приёмки (проверяемые)\n"
        f"## 10. Риски и меры\n"
        f"## 11. Открытые вопросы\n"
        f"Требования формулируй проверяемо и однозначно. Не выдумывай факты, которых нет в "
        f"материалах: всё недостающее выноси в разделы 7 и 11. "
        f"Верни ТОЛЬКО markdown-документ ТЗ, без ограждений ``` вокруг него. "
        f"Отвечай на русском. Не используй инструменты (чтение/запись файлов, запуск команд). "
        f"Не выводи служебный текст — только сам документ."
    )


def tz_html_prompt(tz_md):
    # Сюда попадает только ТЗ короче TZ_HTML_LIMIT (проверка перед вызовом),
    # поэтому подрезать нечего и обещание «не сокращать» честное.
    return (
        f"Оформи готовое ТЗ красивой, удобной для чтения HTML-страницей.\n\n"
        f"Текст ТЗ (markdown):\n\n{tz_md}\n\n"
        f"Требования к странице:\n"
        f"- Содержание НЕ менять и не сокращать — только оформить (можно добавить оглавление).\n"
        f"- Верни ТОЛЬКО валидный самостоятельный HTML: начни с <!DOCTYPE html>, заверши "
        f"</html>. Никакого текста и markdown-ограждений ``` до или после.\n"
        f"- Всё встраивай прямо в файл (CSS в <style>), БЕЗ внешних зависимостей и CDN — "
        f"страница должна открываться двойным кликом и работать офлайн.\n"
        f"- Современное аккуратное оформление: читаемые шрифты, отступы, заголовки, "
        f"карточки/секции, таблицы для требований, умеренный цвет, тёмный текст на светлом фоне.\n"
        f"- Где это УМЕСТНО (этапы, приоритеты, риски) — простые визуализации ВСТРОЕННЫМ SVG. "
        f"Если подходящих данных нет — не выдумывай их.\n"
        f"Отвечай на русском. Не рассказывай о процессе. Не используй инструменты."
    )


# ===========================================================================
# Основной поток
# ===========================================================================

ac.serve(PORT, PAGE_TITLE)

ac.log("system", f"Источник: мозговой штурм «{BRAINSTORM_DIR.name}»")
ac.log("system", f"Папка результатов: {SESSION_DIR.name}")
ac.log("system", f"Защитник: {ac.slot_desc(SLOT_ADVOCATE)} | "
                 f"Оппонент: {ac.slot_desc(SLOT_OPPONENT)} | "
                 f"ТЗ: {ac.slot_desc(SLOT_SPECWRITER)}")
ac.log("system", f"Раундов: {ROUNDS}")

write_transcript_header()

# --- Раунды: защитник -> оппонент -------------------------------------------
for r in range(1, ROUNDS + 1):
    reply1 = ac.run_agent_turn(
        SLOT_ADVOCATE, SLOT_ADVOCATE["engine"],
        f"Раунд {r}: защищает {ac.slot_desc(SLOT_ADVOCATE)}",
        advocate_prompt(r),
    )
    if reply1:
        record_turn(f"Раунд {r} — Защищает: {ac.slot_desc(SLOT_ADVOCATE)}", reply1)

    reply2 = ac.run_agent_turn(
        SLOT_OPPONENT, SLOT_OPPONENT["engine"],
        f"Раунд {r}: оппонирует {ac.slot_desc(SLOT_OPPONENT)}",
        opponent_prompt(r),
    )
    if reply2:
        record_turn(f"Раунд {r} — Оппонирует: {ac.slot_desc(SLOT_OPPONENT)}", reply2)

# --- Финал: решение + развёрнутое ТЗ (markdown — главный артефакт) -----------
tz_markdown = ac.run_agent_turn(
    SLOT_SPECWRITER, "arbiter",
    f"Финал — {ac.slot_desc(SLOT_SPECWRITER)} фиксирует решение и пишет ТЗ",
    spec_prompt(),
)
if tz_markdown:
    # снимаем возможное ограждение ```…``` вокруг всего документа
    m = re.match(r"^\s*```[a-zA-Z]*\s*\n(.*)\n```\s*$", tz_markdown, re.DOTALL)
    if m and m.group(1).strip():
        tz_markdown = m.group(1)
    TZ_MD.write_text(tz_markdown, encoding="utf-8")
    record_turn(f"Финал — {ac.slot_desc(SLOT_SPECWRITER)}",
                "Развёрнутое ТЗ сохранено в `tz.md`.")
    ac.verify_file(TZ_MD)

    # --- Оформление ТЗ красивой HTML-страницей (сырой HTML в поток не выводим) ---
    # tz.html обязан содержать ТО ЖЕ ТЗ целиком. Слишком длинное ТЗ не влезет в промпт
    # оформителя (лимит командной строки) — тогда рендерим страницу локально, без модели.
    if len(tz_markdown) > TZ_HTML_LIMIT:
        ac.log("system", "ТЗ получилось большим — оформляю страницу локально, "
                         "без сокращений содержимого.")
        page = ac.md_to_html(tz_markdown, f"ТЗ — {FOLDER_NAME}")
    else:
        styled = ac.run_agent_turn(
            SLOT_SPECWRITER, "arbiter",
            f"Оформление — {ac.slot_desc(SLOT_SPECWRITER)} готовит HTML-страницу ТЗ",
            tz_html_prompt(tz_markdown), quiet=True,
        )
        page = ac.extract_html(styled) or ac.md_to_html(tz_markdown, f"ТЗ — {FOLDER_NAME}")
    TZ_HTML.write_text(page, encoding="utf-8")
    ac.log("arbiter", "✅ Готова HTML-страница с ТЗ — открываю tz.html "
                      "(markdown-версия лежит рядом: tz.md).")
    ac.verify_file(TZ_HTML)
    ac.open_in_browser(TZ_HTML)   # автоматически открываем готовую страницу
else:
    ac.log("error", "Составитель ТЗ не вернул результат — файлы ТЗ не созданы.")

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
