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
import html
import tempfile
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

# --- Claude: уровни размышления (флаг --effort) ---
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CLAUDE_DEFAULT_EFFORT = "medium"

# --- Codex: реальный список моделей ПОД ПОДПИСКУ читаем из кэша самого CLI ---
CODEX_MODELS_CACHE = os.path.join(os.path.expanduser("~"), ".codex", "models_cache.json")

# Запасной список, если кэш недоступен. Актуально на 2026-07: семейство GPT-5.6
# Sol/Terra/Luna + предыдущие. Кортеж: (slug, подпись, default_effort, [efforts]).
_CODEX_FALLBACK = [
    ("gpt-5.6-sol", "GPT-5.6 Sol — флагман (сложное кодирование и ресёрч)", "medium",
     ["low", "medium", "high", "xhigh", "max", "ultra"]),
    ("gpt-5.6-terra", "GPT-5.6 Terra — баланс для повседневной работы", "medium",
     ["low", "medium", "high", "xhigh", "max", "ultra"]),
    ("gpt-5.6-luna", "GPT-5.6 Luna — быстрая и дешёвая", "medium",
     ["low", "medium", "high", "xhigh", "max"]),
    ("gpt-5.5", "GPT-5.5 — предыдущий флагман", "medium",
     ["low", "medium", "high", "xhigh"]),
    ("gpt-5.4", "GPT-5.4 — крепкая повседневная", "medium",
     ["low", "medium", "high", "xhigh"]),
    ("gpt-5.4-mini", "GPT-5.4 Mini — самая быстрая и дешёвая", "medium",
     ["low", "medium", "high", "xhigh"]),
]


def load_codex_models():
    """Список моделей Codex: надёжный актуальный набор (_CODEX_FALLBACK) + всё новое, что
    появится в кэше codex CLI (~/.codex/models_cache.json). Порядок: сначала известные,
    потом добор из кэша. Кэш codex обновляет на лету, поэтому известные модели (в т.ч.
    семейство GPT-5.6) всегда остаются на месте — динамика их не выкинет, только дополнит.
    Элемент: (slug, подпись, default_effort, [efforts])."""
    result = [t for t in _CODEX_FALLBACK]
    have = {t[0] for t in result}
    try:
        data = json.load(open(CODEX_MODELS_CACHE, encoding="utf-8"))
        for m in data.get("models", []):
            slug = m.get("slug")
            if not slug or slug in have or m.get("visibility") != "list":
                continue
            efforts = [lvl["effort"] for lvl in m.get("supported_reasoning_levels", []) if lvl.get("effort")]
            desc = (m.get("description") or "").rstrip(".")
            label = f"{m.get('display_name') or slug}" + (f" — {desc}" if desc else "")
            result.append((slug, label, m.get("default_reasoning_level") or "medium",
                           efforts or ["low", "medium", "high"]))
            have.add(slug)
    except Exception:
        pass
    return result


# GLM через opencode: значение — полный id провайдер/модель, как ждёт `opencode run -m`.
GLM_MODELS = [
    ("zai-coding-plan/glm-5.2", "glm-5.2 — основная (уровни размышления high/max)"),
    ("zai-coding-plan/glm-5.1", "glm-5.1"),
    ("zai-coding-plan/glm-4.7", "glm-4.7"),
    ("zai-coding-plan/glm-5-turbo", "glm-5-turbo — быстрее"),
    ("zai-coding-plan/glm-4.5-air", "glm-4.5-air — легче и дешевле"),
]

# --- GLM: тип «варианта размышления» модели читаем из каталога opencode ---
OPENCODE_MODELS_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "opencode", "models.json")


def glm_variant_spec(model_id):
    """Тип варианта размышления GLM-модели из каталога opencode (поле reasoning_options):
    {'type':'effort','values':[...]} (напр. glm-5.2 → high/max) или {'type':'toggle'}
    или None. model_id — 'zai-coding-plan/glm-5.2'."""
    try:
        provider, mid = model_id.split("/", 1)
        data = json.load(open(OPENCODE_MODELS_CACHE, encoding="utf-8"))
        prov = data.get(provider) or {}
        models = prov.get("models") if isinstance(prov.get("models"), dict) else prov
        m = models.get(mid) or {}
        opts = m.get("reasoning_options") or m.get("variants")
        if isinstance(opts, list) and opts:
            return opts[0]
    except Exception:
        pass
    return None


ENGINE_TITLE = {"claude": "Claude", "codex": "Codex", "glm": "GLM"}


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


def _choose_effort(prompt, efforts, default):
    """Меню глубины размышления: default идёт первым и помечен."""
    ordered = ([default] if default in efforts else []) + [e for e in efforts if e != default]
    opts = [(e, f"{e}{' — по умолчанию' if e == default else ''}") for e in ordered]
    return choose(prompt, opts)


def _choose_glm_effort(model_id):
    """Глубина для GLM = provider-specific вариант, в том виде как его даёт модель:
    effort-модели (glm-5.2) — high/max; toggle-модели — размышление вкл по умолчанию
    (градаций нет). Возврат '' означает «не передавать --variant»."""
    spec = glm_variant_spec(model_id)
    if spec and spec.get("type") == "effort" and spec.get("values"):
        opts = [("", "обычная глубина (по умолчанию)")] + [(v, v) for v in spec["values"]]
        return choose(f"Глубина размышления GLM ({model_id.split('/')[-1]}):", opts)
    return ""  # toggle или неизвестно — градаций нет


def choose_slot(role_label):
    """Выбор движка → модели → глубины размышления для роли (в том виде, как её
    предоставляет каждый движок: Claude --effort, Codex model_reasoning_effort,
    GLM --variant). GLM показывается, только если доступен opencode."""
    print(f"\n=== {role_label} ===")
    engine = choose("Какой движок в этой роли?", engine_options())
    if engine == "codex":
        models = load_codex_models()
        model = choose("Выберите модель Codex:", [(s, d) for s, d, _, _ in models])
        _, _, default_eff, efforts = next(m for m in models if m[0] == model)
        effort = _choose_effort(f"Глубина размышления Codex ({model}):", efforts, default_eff)
    elif engine == "claude":
        model = choose("Выберите модель Claude:", CLAUDE_MODELS)
        effort = _choose_effort("Глубина размышления Claude:", CLAUDE_EFFORTS, CLAUDE_DEFAULT_EFFORT)
    else:  # glm
        model = choose("Выберите модель GLM:", GLM_MODELS)
        effort = _choose_glm_effort(model)
    slot = {"engine": engine, "model": model, "effort": effort, "role": role_label}
    print(f"→ {role_label}: {slot_desc(slot)}")
    return slot


def slot_desc(slot):
    m = slot["model"]
    short = m.split("/")[-1] if slot["engine"] == "glm" else m
    eff = slot.get("effort")
    return f"{ENGINE_TITLE[slot['engine']]}/{short}" + (f" ({eff})" if eff else "")


def slugify(text):
    """Безопасное имя папки: убираем недопустимые символы, пробелы -> _. Кириллица ок."""
    s = text.strip()
    s = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:60] or "session"


def extract_html(text):
    """Достаёт чистый HTML-документ из ответа модели: отбрасывает возможную преамбулу,
    markdown-ограждение ```html и всё вне <html>…</html>. Если документа нет — None."""
    if not text:
        return None
    t = text.strip()
    low = t.lower()
    start = low.find("<!doctype")
    if start == -1:
        start = low.find("<html")
    end = low.rfind("</html>")
    if start != -1 and end != -1 and end > start:
        return t[start:end + len("</html>")]
    return None


def md_to_html(md, title="Результат"):
    """Простой рендер markdown в самостоятельную читаемую HTML-страницу (заголовки,
    списки, жирный, абзацы) — на случай, когда просили HTML, а модель вернула markdown.
    Так не-программист всё равно получает нормальную страницу, а не текстовый файл."""
    def inline(t):
        t = html.escape(t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        return t

    body = []
    in_ul = [False]

    def close_ul():
        if in_ul[0]:
            body.append("</ul>")
            in_ul[0] = False

    for raw in (md or "").strip().split("\n"):
        line = raw.strip()
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            close_ul()
            lvl = min(len(hm.group(1)), 4)
            body.append(f"<h{lvl}>{inline(hm.group(2))}</h{lvl}>")
        elif re.match(r"^[-*]\s+", line):
            if not in_ul[0]:
                body.append("<ul>")
                in_ul[0] = True
            body.append(f"<li>{inline(line[2:].strip())}</li>")
        elif not line:
            close_ul()
        else:
            close_ul()
            body.append(f"<p>{inline(line)}</p>")
    close_ul()
    return (
        '<!DOCTYPE html>\n<html lang="ru"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        "max-width:820px;margin:40px auto;padding:0 20px;line-height:1.6;color:#1b1b1b}"
        "h1,h2,h3,h4{line-height:1.25}code{background:#f0f0f0;padding:1px 5px;border-radius:4px}"
        "li{margin:4px 0}</style></head><body>\n" + "\n".join(body) + "\n</body></html>"
    )


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


def run_claude_turn(slot, source, prompt, title, quiet=False):
    """claude -p ... stream-json: стримим текст живьём, tool_use/служебное подавляем."""
    cmd = [resolve_cli("claude"), "-p", prompt, "--model", slot["model"],
           "--output-format", "stream-json", "--verbose"]
    if slot.get("effort"):
        cmd += ["--effort", slot["effort"]]
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
                            if not quiet:
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


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def run_codex_turn(slot, source, prompt, title, quiet=False):
    """codex exec. Флаг --dangerously-bypass-approvals-and-sandbox обязателен: на Windows
    без него codex падает на старте. Чистый финальный ответ берём через -o <файл>
    (codex пишет туда ТОЛЬКО последнее сообщение, без эха промпта в stdout)."""
    fd, tmp = tempfile.mkstemp(prefix="codex_last_", suffix=".txt")
    os.close(fd)
    cmd = [resolve_cli("codex"), "exec", prompt, "--model", slot["model"],
           "--dangerously-bypass-approvals-and-sandbox", "-o", tmp]
    if slot.get("effort"):
        cmd += ["-c", f"model_reasoning_effort={slot['effort']}"]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        log("error", f"{title}: не удалось запустить codex — {e}")
        _safe_remove(tmp)
        return ""
    try:
        out, _ = proc.communicate(timeout=PER_TURN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        log("error", f"{title}: codex превысил таймаут {PER_TURN_TIMEOUT} сек")
    if proc.returncode not in (0, None):
        log("error", f"{title}: codex завершился с кодом {proc.returncode}")
    # чистый финальный ответ — из файла -o; запасной вариант — очищенный stdout
    text = ""
    try:
        if os.path.isfile(tmp):
            text = open(tmp, encoding="utf-8", errors="replace").read().strip()
    except Exception:
        pass
    _safe_remove(tmp)
    if not text:
        text = clean_codex_output(out)
    if not text:
        log("error", f"{title}: codex не вернул текст")
        return ""
    if not quiet:
        log(source, text)
    return text


def run_glm_turn(slot, source, prompt, title, quiet=False):
    """GLM через `opencode run -m provider/model --format json`. Ответ модели лежит
    в событиях type:"text" -> part.text. Копим и показываем одним чистым блоком."""
    cmd = [resolve_cli("opencode"), "run", prompt, "-m", slot["model"], "--format", "json"]
    if slot.get("effort"):
        cmd += ["--variant", slot["effort"]]
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
    if not quiet:
        log(source, text)
    return text


ENGINE_RUNNERS = {"claude": run_claude_turn, "codex": run_codex_turn, "glm": run_glm_turn}


def run_agent_turn(slot, source, title, prompt, quiet=False):
    """source — CSS-класс окраски блока: 'claude' / 'codex' / 'glm' / 'arbiter'.
    Открывает новый сворачиваемый блок (== title ==) и возвращает чистый текст ответа.
    quiet=True — не выводить сам ответ в живой поток (напр. когда это сырой HTML-код,
    который не нужен человеку; вместо него показывают дружелюбную заметку)."""
    status["current"] = title
    status["started_at"] = time.time()
    log("system", f"== {title} ==")
    return ENGINE_RUNNERS[slot["engine"]](slot, source, prompt, title, quiet)


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
