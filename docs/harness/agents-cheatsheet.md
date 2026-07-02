# Агенты — шпаргалка

Зовёте в чате: `@planner`, `@code-reviewer` и т.д.

---

## Четыре цикла

**Новый проект (простой):**
```
@planner хочу ... → Реализуй по плану → @code-reviewer проверь
```

**Новый проект (сложный — несколько больших частей):**
```
@architect ... → @planner план → Реализуй → @code-reviewer проверь
```

**Старый или чужой проект:**
```
@code-explorer что тут → @planner хочу ... → Реализуй → @code-reviewer проверь
```

**Плохая архитектура — хочу переделать:**
```
@code-explorer объясни что плохо → @architect предложи лучшую архитектуру → @planner план перехода → Реализуй по шагам → @code-reviewer проверь
```

**Desktop GUI на PySide6 (новый или миграция в веб):**
```
Нет пайплайна .ui → @qt-ui-pipeline-bootstrapper настрой пайплайн → дальше обычный цикл фич
Есть Qt/Tkinter GUI, нужна веб-версия → @qt-html-mirror-builder сделай HTML-зеркало
```

Подробно: [новый проект](./agents-new-project.md) · [старый проект](./agents-existing-project.md)

**Сессия прервалась?** Planner сохраняет план в `docs/plans/<дата>-<название>.md` со статусом фаз (⬜/🔄/✅). Новый чат → `Продолжи по docs/plans/<файл>`. См. [docs/plans/README.md](../plans/README.md).

---

## Не знаешь какого агента звать?

```
@advisor [опиши ситуацию или задай вопрос]
```

Примеры: `@advisor хочу переделать архитектуру` · `@advisor что делает refactor-cleaner?` · `@advisor проект не запускается`

---

## Агенты которые нужны чаще всего

| Агент | Когда | Пример |
|-------|-------|--------|
| **advisor** | Не знаешь какого агента вызвать | `@advisor хочу добавить оплату` |
| **planner** | Начало любой задачи | `@planner хочу форму записи` |
| **code-reviewer** | После каждой правки | `@code-reviewer проверь` |
| **code-explorer** | Первый раз в старом коде | `@code-explorer что делает проект` |
| **build-error-resolver** | Не запускается | `@build-error-resolver почини` |
| **security-reviewer** | Пароли, формы, данные | `@security-reviewer проверь логин` |

---

## Остальные агенты (по ситуации)

| Агент | Когда |
|-------|-------|
| architect | Несколько больших частей сразу (магазин + оплата + база) |
| code-architect | Большая фича в уже большом старом коде |
| typescript-reviewer | После правок в `.ts`, `.tsx`, `.js` файлах |
| python-reviewer | После правок в `.py` файлах |
| tdd-guide | Важный баг — нужны тесты |
| code-simplifier | Код раздулся, стал нечитаемым |
| refactor-cleaner | Много мусора, дублирований |
| silent-failure-hunter | Что-то не работает, но ошибок нет |
| docs-writer | Нужен README для людей |
| doc-updater | Обновить карту файлов проекта |
| qt-ui-pipeline-bootstrapper | Настроить PySide6 Qt Designer пайплайн (.ui → .py, темы, иконки) в проекте, где его ещё нет |
| qt-html-mirror-builder | Сделать HTML/CSS-зеркало существующего PySide6/PyQt/Tkinter GUI с сохранением нейминга — шаг к переходу на не-Qt фронтенд |

---

## Skills — что это и зачем

Skills — это дополнительные инструкции для ИИ про конкретные технологии.

**Включаются сами** — вы ничего не нажимаете. Когда работаете с Python-кодом, Claude автоматически читает `python-patterns`. Когда с React — `react-patterns`.

| Skill | Когда включается |
|-------|-----------------|
| python-patterns | Python код |
| python-testing | тесты на pytest |
| tdd-workflow | разработка через тесты |
| security-review | задачи с безопасностью |
| frontend-patterns | UI, вёрстка |
| vite-patterns | проекты на Vite |
| react-patterns | React компоненты |

Хранятся в `.claude/skills/` — включаются автоматически по типу файла.
