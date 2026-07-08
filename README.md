# ai-workspace

Болванка для работы с ИИ в Claude Code. Одна папка = агенты + ваш проект.

> Первый раз видите этот проект? Начните с **[user_readme.md](./user_readme.md)**.

> **О происхождении:** агенты в `.claude/agents/` взяты для личного пользования и немного переделаны на основе [affaan-m/ecc](https://github.com/affaan-m/ecc).

## Быстрый старт

```
1. @planner хочу ...
2. Обсудите план — или: «Реализуй по плану»
3. @code-reviewer проверь
```

Сложный проект: `@architect` → `@planner` → … См. [agents-new-project.md](./docs/harness/agents-new-project.md).

## Документация

| Ситуация | Файл |
|----------|------|
| С чего начать | [getting-started.md](./docs/harness/getting-started.md) |
| Новый проект | [agents-new-project.md](./docs/harness/agents-new-project.md) |
| Старый проект | [agents-existing-project.md](./docs/harness/agents-existing-project.md) |
| Все агенты | [agents-cheatsheet.md](./docs/harness/agents-cheatsheet.md) |

## Структура (одна папка на проект)

```
ваш-проект/
├── CLAUDE.md         — единый источник правил (читает Claude Code)
├── .claude/          — agents + skills + языковые rules + slash-команды
├── docs/harness/     — гайды по работе с ИИ
├── docs/plans/       — планы фич от planner, со статусом фаз
├── src/              — ваш код (появится при работе)
└── README.md         — как запустить программу (позже, через @docs-writer)
```

Скопируйте болванку целиком — в новый проект или в старый.

## Установка через терминал

Находясь **в своём проекте** (в VS Code или любом терминале), выполните одну команду —
она скачает инструменты из GitHub и разложит их в текущую папку.

**macOS / Linux / Git-Bash:**
```bash
curl -fsSL https://raw.githubusercontent.com/izzzo108/ai-workspace/main/install.sh | bash
```

**Windows PowerShell:**
```powershell
irm https://raw.githubusercontent.com/izzzo108/ai-workspace/main/install.ps1 | iex
```

Скрипт ставит `.claude/` (agents, skills, rules, commands), `docs/`, `setup.bat`,
`user_readme.md`, а этот гайд кладёт рядом как `ai-workspace-README.md` (ваш `README.md`
не трогается). Если у вас **уже есть** `.claude/settings.json`, `CLAUDE.md`,
`.gitignore` и т.п. — установщик спросит, что делать:

| Режим | Что делает |
|-------|-----------|
| **m** — merge *(по умолчанию)* | добавляет наши ключи в `settings.json` (объединяет `permissions`/`hooks`/`env`), не трогая ваши значения; в `.gitignore`/`requirements.txt` дописывает недостающие строки; ваш `CLAUDE.md` не трогает, а наш кладёт рядом как `CLAUDE.aiworkspace.md` для ручного сравнения |
| **s** — skip | оставляет ваши файлы как есть, ставит только отсутствующее |
| **o** — overwrite | перезаписывает (с бэкапом `*.bak`) |

Режим можно задать сразу, без вопросов:
```bash
curl -fsSL .../install.sh | bash -s -- --skip        # или --merge / --overwrite
```
```powershell
$env:AIWS_MODE='skip'; irm .../install.ps1 | iex     # или 'merge' / 'overwrite'
```

В конце установщик предложит **инициализировать git** в проекте (`git init`) — если вы согласитесь и Git установлен, создастся папка `.git/`. Если Git не найден, скрипт подскажет установить его: [git-scm.com/install/windows](https://git-scm.com/install/windows) (версия 2.54 и выше). Пропустить/форсировать: флаг `--git`/`--no-git` (bash) или `$env:AIWS_GIT='yes'|'no'` (PowerShell).

Повторный запуск = **обновление** инструментов до свежей версии из репозитория.

## Правила — одно место

Все правила лежат в **[CLAUDE.md](./CLAUDE.md)** (корень) — Claude Code читает его автоматически.
Правьте правила **только там**. Языковые (TypeScript/Python и т.д.) остаются раздельными:
`.claude/rules/<lang>-*.md` и `.claude/skills/` — они подключаются по типу файла.

## Python-окружение

`setup.bat` создаёт `.venv` и ставит зависимости из `requirements.txt`. Точка входа (`main.py` или другая) пока не определена — когда появится, запускайте её через `.venv\Scripts\python.exe`.

`requirements.txt` **намеренно пуст** — болванка не привязана к конкретному GUI/backend-стеку (PySide6, pywebview, Flask, что угодно). Зависимости появляются по мере работы: например, `@qt-ui-pipeline-bootstrapper` сам дописывает `PySide6`, когда настраивает Qt-пайплайн. После того как в `requirements.txt` что-то добавилось — перезапустите `setup.bat`.
