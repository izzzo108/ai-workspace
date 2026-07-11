#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# agents_common.py — общий модуль для наших live-скриптов (brainstorm-live.py,
# plan-review-live.py). Здесь живёт всё, что у них одинаково:
#   - выбор движка и модели (Claude / Codex / GLM), где GLM показывается только
#     если установлен opencode;
#   - запуск одного «хода» любого движка с получением чистого текста;
#   - живой веб-просмотр (локальный сервер + HTML) и статичная копия страницы.
#
# ВАЖНО: родительские скрипты запускаются ЧЕРЕЗ Git Bash, например:
#     python scripts/brainstorm-live.py "тема"
#     python scripts/plan-review-live.py "текст задачи"
# Именно в Git Bash в PATH лежат CLI claude / codex / opencode, которыми мы пользуемся.
#
# Модуль импортируется так: `import agents_common as ac` — Python сам добавляет папку
# scripts/ в sys.path, когда запускает скрипт из неё, поэтому импорт работает.

import http.server
import socketserver
import subprocess
import shutil
import sys
import os
import re
import time
import json
import threading
import webbrowser

PER_TURN_TIMEOUT = 600           # секунд на один ход (защита от зависаний)
INLINE_TRANSCRIPT_LIMIT = 24000  # макс. символов контекста, вставляемого в промпт


def enable_utf8_console():
    """UTF-8 в консоли Windows — иначе кириллица от CLI может испортиться в файлах.
    Вызывать из родительского скрипта (он запускается через Git Bash)."""
    if os.name == "nt":
        try:
            subprocess.run("chcp 65001", shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


# ===========================================================================
# Движки и модели
# ===========================================================================

CLAUDE_MODELS = [
    ("sonnet", "sonnet — Claude Sonnet 5 (по умолчанию)"),
    ("opus", "opus — Claude Opus 4.8 (мощнее, медленнее)"),
    ("haiku", "haiku — Claude Haiku 4.5 (быстрее и дешевле)"),
    ("fable", "fable — Claude Fable 5 (самая мощная)"),
]

CODEX_MODELS = [
    ("gpt-5.6-sol", "gpt-5.6-sol — Codex 5.6 (новая, по умолчанию)"),
    ("gpt-5.5", "gpt-5.5 — предыдущая основная"),
    ("gpt-5.4-mini", "gpt-5.4-mini — быстрее и дешевле"),
    ("gpt-5.3-codex", "gpt-5.3-codex — максимальная глубина кода"),
]

# GLM через opencode: значение — полный id провайдер/модель, как ждёт `opencode run -m`.
GLM_MODELS = [
    ("zai-coding-plan/glm-5.2", "glm-5.2 — основная (по умолчанию)"),
    ("zai-coding-plan/glm-4.7", "glm-4.7 — предыдущая"),
    ("zai-coding-plan/glm-5-turbo", "glm-5-turbo — быстрее"),
    ("zai-coding-plan/glm-4.5-air", "glm-4.5-air — легче и дешевле"),
]

ENGINE_TITLE = {"claude": "Claude", "codex": "Codex", "glm": "GLM"}
ENGINE_MODELS = {"claude": CLAUDE_MODELS, "codex": CODEX_MODELS, "glm": GLM_MODELS}


def glm_available():
    """GLM показываем в выборе, только если установлен opencode."""
    return shutil.which("opencode") is not None


def engine_options():
    """Список движков для меню. GLM добавляется, только если доступен."""
    opts = [("claude", "Claude"), ("codex", "Codex")]
    if glm_available():
        opts.append(("glm", "GLM (через opencode)"))
    return opts


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


def ask_line(prompt, required=False, preset=None):
    """Одна строка ввода. Enter = пропустить (если не required)."""
    if preset:
        return preset.strip()
    while True:
        try:
            raw = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nОтменено пользователем.")
            sys.exit(1)
        if raw:
            return raw
        if not required:
            return ""
        print("Это обязательный пункт — введите значение.")


def choose_slot(role_label):
    """Двухшаговый выбор: движок (Claude/Codex/GLM — GLM если доступен), затем модель."""
    print(f"\n=== {role_label} ===")
    engine = choose("Какой движок в этой роли?", engine_options())
    model = choose(f"Выберите модель {ENGINE_TITLE[engine]}:", ENGINE_MODELS[engine])
    slot = {"engine": engine, "model": model, "role": role_label}
    print(f"→ {role_label}: {slot_desc(slot)}")
    return slot


def slot_desc(slot):
    m = slot["model"]
    short = m.split("/")[-1] if slot["engine"] == "glm" else m
    return f"{ENGINE_TITLE[slot['engine']]}/{short}"


def slugify(text):
    """Безопасное имя папки: убираем недопустимые символы, пробелы -> _. Кириллица ок."""
    s = text.strip().lower()
    s = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:40] or "session"


# ===========================================================================
# Состояние для браузера + логирование
# ===========================================================================

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
    if path.exists():
        log("system", f"💾 Файл сохранён: {path}")
        return True
    log("error", f"⚠ Файл НЕ найден: {path}")
    return False


def snapshot_events():
    with lock:
        return list(events)


# ===========================================================================
# Запуск хода: единый интерфейс для Claude / Codex / GLM
# ===========================================================================

_cli_cache = {}


def _extract_exe_from_shim(cmd_path):
    """Из npm .cmd-обёртки достаёт путь к настоящему .exe, который она вызывает
    (строка вида "%dp0%\\node_modules\\<pkg>\\bin\\<name>.exe" %*)."""
    try:
        text = open(cmd_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    dp0 = os.path.dirname(cmd_path)
    m = re.search(r'"([^"]*\.exe)"', text)
    if not m:
        return None
    target = os.path.normpath(m.group(1).replace("%dp0%", dp0).replace("%~dp0", dp0))
    return target if os.path.isfile(target) else None


def resolve_cli(name):
    """Путь к запускаемому CLI. На Windows npm ставит CLI как .cmd-обёртку, которую
    subprocess.Popen не может запустить напрямую (WinError 2: не найден файл). Рядом
    лежит настоящий .exe — находим и используем его. Для .exe-инструментов (claude,
    codex) which сразу вернёт .exe. Результат кэшируется."""
    if name in _cli_cache:
        return _cli_cache[name]
    resolved = name
    found = shutil.which(name)
    if found:
        resolved = found
        if os.name == "nt" and found.lower().endswith((".cmd", ".bat")):
            exe = _extract_exe_from_shim(found)
            if exe:
                resolved = exe
    _cli_cache[name] = resolved
    return resolved


def _kill_after(proc, seconds):
    t = threading.Timer(seconds, proc.kill)
    t.daemon = True
    t.start()
    return t


def run_claude_turn(slot, source, prompt, title):
    """claude -p ... stream-json: стримим текст живьём, tool_use/служебное подавляем."""
    cmd = [resolve_cli("claude"), "-p", prompt, "--model", slot["model"],
           "--output-format", "stream-json", "--verbose"]
    collected, result_text = [], None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, shell=False, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"{title}: не удалось запустить claude — {e}")
        return ""
    timer = _kill_after(proc, PER_TURN_TIMEOUT)
    try:
        for raw_line in proc.stdout:
            raw_line = raw_line.rstrip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t == "assistant":
                for block in (obj.get("message", {}).get("content") or []):
                    if block.get("type") == "text":
                        txt = (block.get("text") or "").strip()
                        if txt:
                            log(source, txt)
                            collected.append(txt)
            elif t == "result":
                r = (obj.get("result") or "").strip()
                if r:
                    result_text = r
    finally:
        proc.wait()
        timer.cancel()
    if proc.returncode not in (0, None):
        log("error", f"{title}: claude завершился с кодом {proc.returncode}")
    final = result_text if result_text else "\n".join(collected)
    if not final:
        log("error", f"{title}: модель вернула пустой ответ")
    return final


def clean_codex_output(raw):
    """Убирает служебный шум codex exec (баннер, метаданные сессии), сохраняя ответ."""
    if not raw:
        return ""
    cleaned = []
    for line in raw.splitlines():
        s = line.rstrip()
        low = s.strip().lower()
        if re.match(r"^-{3,}$", low):
            continue
        if re.match(r"^(workdir|model|provider|approval|sandbox|reasoning effort|"
                    r"reasoning summaries|version|session id)\s*:", low):
            continue
        if re.match(r"^\[\d.*\]\s*(openai codex|thinking|codex|tokens used|exec|user instructions)", low):
            continue
        if low.startswith("openai codex"):
            continue
        cleaned.append(s)
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned).strip())
    return result if result else raw.strip()


def run_codex_turn(slot, source, prompt, title):
    """codex exec отвечает текстом. Флаг --dangerously-bypass-approvals-and-sandbox
    обязателен: на Windows без него codex падает на старте. Здесь безопасно — только чтение."""
    cmd = [resolve_cli("codex"), "exec", prompt, "--model", slot["model"],
           "--dangerously-bypass-approvals-and-sandbox"]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"{title}: не удалось запустить codex — {e}")
        return ""
    try:
        out, _ = proc.communicate(timeout=PER_TURN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        log("error", f"{title}: codex превысил таймаут {PER_TURN_TIMEOUT} сек")
    if proc.returncode not in (0, None):
        log("error", f"{title}: codex завершился с кодом {proc.returncode}")
    text = clean_codex_output(out)
    if not text:
        log("error", f"{title}: codex не вернул текст")
        return ""
    log(source, text)
    return text


def run_glm_turn(slot, source, prompt, title):
    """GLM через `opencode run -m provider/model --format json`. Ответ модели лежит
    в событиях type:"text" -> part.text. Копим и показываем одним чистым блоком."""
    cmd = [resolve_cli("opencode"), "run", prompt, "-m", slot["model"], "--format", "json"]
    parts = []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, shell=False, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"{title}: не удалось запустить opencode — {e}")
        return ""
    timer = _kill_after(proc, PER_TURN_TIMEOUT)
    try:
        for raw_line in proc.stdout:
            raw_line = raw_line.rstrip("\n")
            if not raw_line.strip():
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "text":
                txt = (obj.get("part") or {}).get("text") or ""
                if txt:
                    parts.append(txt)
    finally:
        proc.wait()
        timer.cancel()
    if proc.returncode not in (0, None):
        log("error", f"{title}: opencode завершился с кодом {proc.returncode}")
    text = "".join(parts).strip()
    if not text:
        log("error", f"{title}: GLM (opencode) не вернул текст")
        return ""
    log(source, text)
    return text


ENGINE_RUNNERS = {"claude": run_claude_turn, "codex": run_codex_turn, "glm": run_glm_turn}


def run_agent_turn(slot, source, title, prompt):
    """source — CSS-класс окраски блока: 'claude' / 'codex' / 'glm' / 'arbiter'.
    Открывает новый сворачиваемый блок (== title ==) и возвращает чистый текст ответа."""
    status["current"] = title
    status["started_at"] = time.time()
    log("system", f"== {title} ==")
    return ENGINE_RUNNERS[slot["engine"]](slot, source, prompt, title)


# ===========================================================================
# Веб-страница (живой просмотр) + статичная копия
# ===========================================================================

CSS = r"""<style>
  body { background:#0d1117; color:#c9d1d9; font-family: Consolas, 'Courier New', monospace; margin:0; }
  header { padding: 12px 20px; background:#161b22; border-bottom:1px solid #30363d;
           display:flex; align-items:center; gap:14px; position:sticky; top:0; }
  header h1 { font-size:14px; margin:0; color:#8b949e; font-weight:normal; }
  #status { font-size:13px; padding:4px 10px; border-radius:4px; background:#21262d; }
  #status.working { color:#f0b400; }
  #status.done { color:#3fb950; }
  #term { padding: 16px 20px; height: calc(100vh - 60px); overflow-y:auto; font-size:13px; line-height:1.55; }
  .line { margin:0 0 8px 0; white-space: pre-wrap; word-break: break-word; }
  .system { color:#8b949e; }
  .claude { color:#ff9d5c; }
  .codex { color:#58a6ff; }
  .glm { color:#7ee787; }
  .arbiter { color:#e3b341; }
  .error { color:#f85149; }
  .file-saved { color:#3fb950; font-weight:600; }
  .tag { opacity:0.7; margin-right:6px; }
  .md-h { font-weight:700; color:#e6edf3; }
  code { background:#161b22; padding:0 4px; border-radius:3px; }
  details.section { margin: 10px 0; border:1px solid #30363d; border-radius:6px; overflow:hidden; }
  details.section > summary { cursor:pointer; padding:8px 12px; background:#161b22;
    color:#c9d1d9; font-weight:600; list-style:none; user-select:none; }
  details.section > summary::-webkit-details-marker { display:none; }
  details.section > summary:before { content:'▾ '; opacity:0.6; }
  details.section:not([open]) > summary:before { content:'▸ '; opacity:0.6; }
  .section-body { padding:8px 12px; }
</style>"""

RENDER_JS = r"""
const term = document.getElementById('term');
const statusEl = document.getElementById('status');
const tagText = {system:'[СИСТЕМА]', claude:'[CLAUDE]', codex:'[CODEX]', glm:'[GLM]',
  arbiter:'[ИТОГ]', error:'[ОШИБКА]'};
let currentBody = null;

function renderMd(raw) {
  let s = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const lines = s.split('\n').map(function (line) {
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { return '<span class="md-h">' + h[2] + '</span>'; }
    return line.replace(/^(\s*)[-*]\s+/, '$1• ');
  });
  return lines.join('\n')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

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
  div.innerHTML = '<span class="tag">' + t + '</span>' + renderMd(ev.text);
  (currentBody || term).appendChild(div);
  term.scrollTop = term.scrollHeight;
}
"""

POLL_JS = r"""
let lastId = 0;
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
"""


def _document(trailing_script, title):
    return (
        '<!DOCTYPE html>\n<html lang="ru"><head><meta charset="utf-8">'
        f'<title>{title}</title>' + CSS +
        '</head><body>'
        f'<header><h1>{title}</h1><span id="status">запуск&hellip;</span></header>'
        '<div id="term"></div><script>' + RENDER_JS + trailing_script +
        '</script></body></html>'
    )


def build_live_html(title):
    return _document(POLL_JS, title)


def build_static_html(evs, title):
    # Экранируем "</" -> "<\/", иначе литерал "</script>" в тексте реплики закрыл бы
    # тег <script> раньше времени и сломал бы страницу. В JS-строке это эквивалентно.
    embedded = json.dumps(evs, ensure_ascii=False).replace("</", "<\\/")
    tail = (
        "const EVENTS = " + embedded + ";"
        "for (const ev of EVENTS) { addLine(ev); }"
        "statusEl.textContent = 'Готово ✓ (сохранённая копия)';"
        "statusEl.className = 'done';"
    )
    return _document(tail, title)


_LIVE_HTML = "<!DOCTYPE html><html><body>загрузка…</body></html>"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _LIVE_HTML.encode("utf-8")
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


def serve(port, title):
    """Строит живую страницу, поднимает локальный сервер и открывает браузер."""
    global _LIVE_HTML
    _LIVE_HTML = build_live_html(title)

    def _run():
        with socketserver.ThreadingTCPServer(("127.0.0.1", port), _Handler) as httpd:
            httpd.serve_forever()

    threading.Thread(target=_run, daemon=True).start()
    webbrowser.open(f"http://127.0.0.1:{port}")
    time.sleep(1)
