#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Запуск: python scripts/plan-review-live.py "текст задачи"
# Требует: claude и codex CLI в PATH (те же, что вы уже используете в терминале),
#          python (в Git Bash с активным venv — команда "python").
#
# Что делает:
#   1. Поднимает локальный сервер на http://127.0.0.1:8765 и сразу открывает его в браузере.
#   2. Claude сам сохраняет план/финал через свой инструмент записи файлов (он это умеет).
#   3. Codex НЕ просим сохранять файл самостоятельно — он часто работает в "песочнице"
#      без прав на запись, и файл может тихо не появиться. Вместо этого мы забираем
#      его ответ текстом и сохраняем critique.md сами, скриптом — так файл гарантированно
#      появится.
#   4. После каждого шага скрипт печатает "💾 Файл сохранён: путь" или "⚠ Файл не найден" —
#      видно по факту, а не предположительно.
#   5. Claude — оранжевый, Codex — синий, система — серый, ошибки — красные,
#      короткие тезисные сводки от каждой модели — фиолетовые [ИТОГ ...].
#   6. Сохраняет план -> критика -> финальный план в docs/Plans/.
#
# Окно терминала не закрывайте, пока идёт работа. Сервер остановится по Ctrl+C.

import http.server
import socketserver
import subprocess
import sys
import os
import time
import json
import threading
import datetime
import webbrowser
from pathlib import Path

PORT = 8765

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
os.chdir(PROJECT_ROOT)

# Переключаем кодировку консоли Windows на UTF-8. Без этого кириллица от
# claude/codex может испортиться (кракозябры) при сохранении в файлы.
if os.name == "nt":
    try:
        subprocess.run("chcp 65001", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

TASK = " ".join(sys.argv[1:]).strip()
if not TASK:
    print('Использование: python scripts/plan-review-live.py "текст задачи"')
    sys.exit(1)


def choose(prompt, options):
    """options: список (значение, описание). Возвращает выбранное значение."""
    print(prompt)
    for i, (value, desc) in enumerate(options, 1):
        print(f"  {i}. {desc}")
    while True:
        try:
            raw = input(f"Введите число (1-{len(options)}): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nОтменено пользователем.")
            sys.exit(1)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("Некорректный ввод, попробуйте ещё раз.")


CLAUDE_MODEL = choose(
    "Выберите модель Claude:",
    [
        ("sonnet", "sonnet — Claude Sonnet 5 (по умолчанию)"),
        ("opus", "opus — Claude Opus 4.8 (мощнее, медленнее)"),
        ("haiku", "haiku — Claude Haiku 4.5 (быстрее и дешевле)"),
        ("fable", "fable — Claude Fable 5 (самая мощная)"),
    ],
)
print(f"Выбрана модель Claude: {CLAUDE_MODEL}")

CODEX_MODEL = choose(
    "Выберите модель Codex:",
    [
        ("gpt-5.5", "gpt-5.5 — основная (по умолчанию)"),
        ("gpt-5.4-mini", "gpt-5.4-mini — быстрее и дешевле"),
        ("gpt-5.3-codex", "gpt-5.3-codex — максимальная глубина кода"),
    ],
)
print(f"Выбрана модель Codex: {CODEX_MODEL}")

now = datetime.datetime.now()
DATE = now.strftime("%d.%m.%Y")
TIME = now.strftime("%H.%M")

PLAN_DIR = PROJECT_ROOT / "docs" / "Plans"
PLAN_DIR.mkdir(parents=True, exist_ok=True)

PLAN_FILE = PLAN_DIR / f"{DATE}_{TIME}_plan.md"
CRITIQUE_FILE = PLAN_DIR / f"{DATE}_{TIME}_critique.md"
FINAL_FILE = PLAN_DIR / f"{DATE}_{TIME}_plan-final.md"

# --- общее состояние, которое читает браузер ---
events = []
status = {"current": "запуск...", "done": False, "started_at": None}
lock = threading.Lock()
_next_id = 0


def log(source, text):
    global _next_id
    if not text:
        return
    with lock:
        _next_id += 1
        events.append({"id": _next_id, "source": source, "text": text})


def verify_file(path):
    """Проверяет, реально ли появился файл, и честно об этом сообщает."""
    if path.exists():
        log("system", f"💾 Файл сохранён: {path}")
        return True
    else:
        log("error", f"⚠ Файл НЕ найден: {path}")
        return False


def parse_claude_line(line):
    """Разбирает построчный JSON от claude -p --output-format stream-json.
    Возвращает читаемый текст события или None, если событие не интересно."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return line.strip() or None

    t = obj.get("type")
    if t == "assistant":
        out = []
        for block in (obj.get("message", {}).get("content") or []):
            bt = block.get("type")
            if bt == "text":
                txt = (block.get("text") or "").strip()
                if txt:
                    out.append(txt)
            elif bt == "tool_use":
                name = block.get("name", "?")
                inp = json.dumps(block.get("input", {}), ensure_ascii=False)
                if len(inp) > 150:
                    inp = inp[:150] + "…"
                out.append(f"🔧 использует инструмент: {name} {inp}")
        return "\n".join(out) if out else None
    if t == "result":
        res = (obj.get("result") or "").strip()
        return f"[результат] {res[:300]}" if res else "[шаг завершён]"
    return None


def run_step(title, source, cmd, parser):
    status["current"] = title
    status["started_at"] = time.time()
    log("system", f"== {title} ==")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, shell=False,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"{title}: не удалось запустить процесс — {e}")
        return
    for raw_line in proc.stdout:
        raw_line = raw_line.rstrip()
        if not raw_line:
            continue
        text = parser(raw_line)
        if text:
            log(source, text)
    proc.wait()
    if proc.returncode != 0:
        log("error", f"{title}: завершилось с ошибкой (код {proc.returncode})")


def run_codex_critique(title, prompt, model, target_file):
    """Codex просто отвечает текстом критики (без --json, без просьбы что-то
    сохранять) — а сохраняет файл сам скрипт. Так критика гарантированно
    попадает на диск, даже если у Codex нет прав на запись в проект."""
    status["current"] = title
    status["started_at"] = time.time()
    log("system", f"== {title} ==")
    # --dangerously-bypass-approvals-and-sandbox: обходит песочницу Codex.
    # На Windows у Codex CLI известный баг — его собственный "хелпер" песочницы
    # не запускается (orchestrator_helper_launch_failed), и без этого флага
    # codex exec падает на старте, ничего не читая. Здесь это безопасно —
    # Codex только читает файл и отвечает текстом, ничего не выполняет.
    cmd = ["codex", "exec", prompt, "--model", model, "--dangerously-bypass-approvals-and-sandbox"]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"{title}: не удалось запустить процесс — {e}")
        return False
    out, _ = proc.communicate()
    text = out.strip()
    if proc.returncode != 0:
        log("error", f"{title}: завершилось с ошибкой (код {proc.returncode})")
    if not text:
        log("error", "Codex не вернул текст критики — файл не будет создан.")
        return False
    log("codex", text)
    try:
        target_file.write_text(text, encoding="utf-8")
    except Exception as e:
        log("error", f"Не удалось сохранить критику в файл: {e}")
        return False
    return verify_file(target_file)


def run_summary(source, cmd):
    """Отдельный короткий вызов: просим модель тезисно (3-5 пунктов) рассказать,
    что она сделала. Тратит немного лишних токенов, зато видно суть работы.
    Ошибки теперь тоже показываются, а не пропадают молча."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, shell=False,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"Сводка ({source}): не удалось запустить — {e}")
        return
    out, _ = proc.communicate()
    text = out.strip()
    if proc.returncode != 0:
        log("error", f"Сводка ({source}): код возврата {proc.returncode}")
    if text:
        log(source, text)
    else:
        log("error", f"Сводка ({source}): модель вернула пустой ответ")


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Claude + Codex — работа над планом</title>
<style>
  body { background:#0d1117; color:#c9d1d9; font-family: Consolas, 'Courier New', monospace; margin:0; }
  header { padding: 12px 20px; background:#161b22; border-bottom:1px solid #30363d;
           display:flex; align-items:center; gap:14px; position:sticky; top:0; }
  header h1 { font-size:14px; margin:0; color:#8b949e; font-weight:normal; }
  #status { font-size:13px; padding:4px 10px; border-radius:4px; background:#21262d; }
  #status.working { color:#f0b400; }
  #status.done { color:#3fb950; }
  #term { padding: 16px 20px; white-space: pre-wrap; word-break: break-word;
          height: calc(100vh - 60px); overflow-y:auto; font-size:13px; line-height:1.5; }
  .line { margin:0; }
  .system { color:#8b949e; }
  .claude { color:#ff9d5c; }
  .codex { color:#58a6ff; }
  .error { color:#f85149; }
  .claude-summary, .codex-summary { color:#d2a8ff; font-weight:600; }
  .file-saved { color:#3fb950; font-weight:600; }
  .tag { opacity:0.7; margin-right:6px; }
  details.section { margin: 10px 0; border:1px solid #30363d; border-radius:6px; overflow:hidden; }
  details.section > summary { cursor:pointer; padding:8px 12px; background:#161b22;
    color:#c9d1d9; font-weight:600; list-style:none; user-select:none; }
  details.section > summary::-webkit-details-marker { display:none; }
  details.section > summary:before { content:'▾ '; opacity:0.6; }
  details.section:not([open]) > summary:before { content:'▸ '; opacity:0.6; }
  .section-body { padding:8px 12px; }
</style>
</head>
<body>
<header>
  <h1>Claude &#8646; Codex &mdash; работа над планом</h1>
  <span id="status">запуск&hellip;</span>
</header>
<div id="term"></div>
<script>
let lastId = 0;
const term = document.getElementById('term');
const statusEl = document.getElementById('status');
const tagText = {system:'[СИСТЕМА]', claude:'[CLAUDE]', codex:'[CODEX]', error:'[ОШИБКА]',
  'claude-summary':'[ИТОГ CLAUDE]', 'codex-summary':'[ИТОГ CODEX]'};

let currentBody = null;

function startSection(title) {
  const details = document.createElement('details');
  details.open = true;
  details.className = 'section';
  const summary = document.createElement('summary');
  summary.textContent = title;
  details.appendChild(summary);
  const body = document.createElement('div');
  body.className = 'section-body';
  details.appendChild(body);
  term.appendChild(details);
  currentBody = body;
}

function addLine(ev) {
  // Строка "== Шаг N: ... ==" открывает новый сворачиваемый блок,
  // все следующие строки идут внутрь него, пока не появится следующий шаг.
  if (ev.source === 'system' && /^==.*==$/.test(ev.text)) {
    const title = ev.text.replace(/^==\s*/, '').replace(/\s*==$/, '');
    startSection(title);
    term.scrollTop = term.scrollHeight;
    return;
  }

  const div = document.createElement('div');
  const isFileSaved = ev.source === 'system' && ev.text.indexOf('💾') === 0;
  div.className = 'line ' + (isFileSaved ? 'file-saved' : ev.source);
  const t = tagText[ev.source] || '';
  const safe = ev.text.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  div.innerHTML = '<span class="tag">' + t + '</span>' + safe;
  (currentBody || term).appendChild(div);
  term.scrollTop = term.scrollHeight;
}

async function poll() {
  try {
    const r = await fetch('/events');
    const data = await r.json();
    for (const ev of data.events) {
      if (ev.id > lastId) { addLine(ev); lastId = ev.id; }
    }
    if (data.status.done) {
      statusEl.textContent = 'Готово ✓';
      statusEl.className = 'done';
    } else {
      const elapsed = data.status.started_at
        ? Math.max(0, Math.round(Date.now() / 1000 - data.status.started_at))
        : 0;
      statusEl.textContent = 'Работает: ' + data.status.current + ' — ' + elapsed + ' сек';
      statusEl.className = 'working';
    }
  } catch (e) {}
  setTimeout(poll, 1000);
}
poll();
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/events"):
            with lock:
                data = json.dumps({"events": events, "status": status}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # не засорять терминал служебными логами сервера


def start_server():
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        httpd.serve_forever()


threading.Thread(target=start_server, daemon=True).start()
webbrowser.open(f"http://127.0.0.1:{PORT}")
time.sleep(1)

# --- Шаг 1: Claude составляет план ---
run_step(
    f"Шаг 1: Claude ({CLAUDE_MODEL}) составляет план", "claude",
    ["claude", "-p",
     f"Используй planner.md как инструкцию. Составь план по задаче: {TASK}. "
     f"Сохрани план в файл {PLAN_FILE}.",
     "--model", CLAUDE_MODEL, "--output-format", "stream-json", "--verbose"],
    parse_claude_line,
)
plan_ok = verify_file(PLAN_FILE)
if plan_ok:
    run_summary("claude-summary", [
        "claude", "-p",
        f"Прочитай {PLAN_FILE}. Одним списком из 3-5 пунктов коротко перечисли, "
        f"что ты запланировал (главные фазы и решения). Только список, без вступления и заключения.",
        "--model", CLAUDE_MODEL,
    ])

# --- Шаг 2: Codex критикует план (файл сохраняет сам скрипт, не Codex) ---
critique_ok = False
if plan_ok:
    critique_ok = run_codex_critique(
        f"Шаг 2: Codex ({CODEX_MODEL}) критикует план",
        f"Прочитай файл {PLAN_FILE}. Дай критику: найди слабые места, риски, недостающие шаги, "
        f"нестыковки. Выведи только список замечаний текстом прямо в ответе — сохранять "
        f"файлы не нужно, об этом позаботится скрипт.",
        CODEX_MODEL,
        CRITIQUE_FILE,
    )
    if critique_ok:
        run_summary("codex-summary", [
            "codex", "exec",
            f"Вот твоя критика плана:\n\n{CRITIQUE_FILE.read_text(encoding='utf-8')}\n\n"
            f"Одним списком из 3-5 пунктов коротко перечисли главные замечания. "
            f"Только список, без вступления.",
            "--model", CODEX_MODEL, "--dangerously-bypass-approvals-and-sandbox",
        ])
else:
    log("error", "Шаг 2 пропущен: нет плана для критики.")

# --- Шаг 3: Claude учитывает критику ---
if plan_ok:
    if critique_ok:
        final_prompt = (
            f"Прочитай {PLAN_FILE} (твой план) и {CRITIQUE_FILE} (критика от другого ИИ, Codex). "
            f"Реши, какие замечания принять, а какие отклонить — и почему. "
            f"Дополни и допиши план с учётом принятых замечаний. Сохрани финальную версию в {FINAL_FILE}."
        )
    else:
        final_prompt = (
            f"Критика от Codex недоступна (шаг критики не удался). Прочитай {PLAN_FILE} "
            f"и просто сохрани его копию как финальную версию в {FINAL_FILE}, без изменений."
        )
    run_step(
        f"Шаг 3: Claude ({CLAUDE_MODEL}) учитывает критику и дорабатывает план", "claude",
        ["claude", "-p", final_prompt,
         "--model", CLAUDE_MODEL, "--output-format", "stream-json", "--verbose"],
        parse_claude_line,
    )
    final_ok = verify_file(FINAL_FILE)
    if final_ok and critique_ok:
        run_summary("claude-summary", [
            "claude", "-p",
            f"Прочитай {FINAL_FILE} и {CRITIQUE_FILE}. Одним списком из 3-5 пунктов коротко перечисли: "
            f"что из критики Codex ты принял, а что отклонил и почему. Только список, без вступления.",
            "--model", CLAUDE_MODEL,
        ])

status["done"] = True
log("system", f"Готово. Итог: {FINAL_FILE if plan_ok else '(план не был создан)'}")
print("Готово.")
print("Страница в браузере останется открытой. Чтобы остановить сервер — нажмите Ctrl+C здесь.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
