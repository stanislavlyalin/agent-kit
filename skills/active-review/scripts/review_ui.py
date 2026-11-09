"""Опрос в отдельной вкладке GNOME Terminal."""

import argparse
from contextlib import redirect_stdout
import io
import curses
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time
import quiz
import review_results
import shutil


def draw(screen, title, options, active):
    screen.clear()
    height, width = screen.getmaxyx()
    rows = textwrap.wrap(title, max(10, width - 4))
    for y, line in enumerate(rows[: height - 4]):
        screen.addnstr(y, 1, line, width - 2)
    top = len(rows) + 1
    visible = max(1, height - top - 3)
    start = max(0, active - visible + 1)
    for y, option in enumerate(options[start : start + visible], top):
        index = start + y - top
        screen.addnstr(
            y, 1, option, width - 2, curses.color_pair(1) if index == active else 0
        )
    screen.addnstr(
        height - 1,
        0,
        "↑↓ выбор • Пробел отметка • Enter подтвердить • Esc сохранить и выйти",
        width - 1,
    )
    screen.refresh()


def choose(screen, title, options, multiple=False):
    active, selected = 0, set()
    while True:
        labels = [
            ("☑ " if i in selected else "☐ ") + v if multiple else v
            for i, v in enumerate(options)
        ]
        draw(screen, title, labels, active)
        key = screen.getch()
        if key == 27:
            raise KeyboardInterrupt
        if key == curses.KEY_UP:
            active = (active - 1) % len(options)
        if key == curses.KEY_DOWN:
            active = (active + 1) % len(options)
        if key == 32 and multiple:
            selected.symmetric_difference_update({active})
        if key in (10, 13, curses.KEY_ENTER):
            return sorted(selected) if multiple and selected else [active]


def entry(screen, title):
    value = ""
    while True:
        draw(screen, title, [value + "▏"], 0)
        key = screen.get_wch()
        if key == "\x1b":
            raise KeyboardInterrupt
        if key in ("\n", "\r") and value.strip():
            return value.strip()
        if key in ("\x7f", "\b", curses.KEY_BACKSPACE):
            value = value[:-1]
        elif isinstance(key, str) and key.isprintable():
            value += key


def run(screen, path):
    screen.keypad(True)
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    state = json.loads(path.read_text())
    if state["scope"] is None:
        choices = state["candidates"]
        proposed = choices[0]["refs"][0] if len(choices) == 1 else ""
        mode = choose(
            screen,
            "Что именно ревьювим?",
            [
                f"Ветку {state['branch']} относительно {proposed or 'выбранной основы'} (предположение)",
                "Последние N коммитов",
                "Один коммит: указать хеш",
                "Диапазон A..B, включая оба",
            ],
        )[0]
        value = entry(
            screen,
            [
                "Ветка-основа (можно изменить предположение)",
                "Введите N",
                "Введите хеш коммита",
                "Введите A..B",
            ][mode],
        )
        quiz.scope(state, ["branch", "last", "commit", "range"][mode], value)
        quiz.save(path, state)
    if not state["questions"] and "question_limit" not in state:
        mode = choose(
            screen,
            "Сколько вопросов подготовить?",
            [
                "Агент решит сам (5–15 вопросов)",
                "Указать число-лимит",
            ],
        )[0]
        limit = None
        if mode == 1:
            prompt = "Введите максимальное число вопросов (положительное целое)"
            while True:
                raw = entry(screen, prompt)
                if raw.isascii() and raw.isdigit() and int(raw) > 0:
                    limit = int(raw)
                    break
                prompt = "Нужно положительное целое число. Введите лимит"
        state["question_limit"] = limit
        quiz.save(path, state)
    package = path.parent / "questions.json"
    started = time.monotonic()
    error = None
    while not state["questions"]:
        elapsed = int(time.monotonic() - started)
        status_file = path.parent / "preparation.json"
        try:
            status = json.loads(status_file.read_text()) if status_file.exists() else {}
        except (ValueError, OSError):
            status = {}
        phase = status.get("status", "waiting")
        title = {
            "waiting": "Ожидание агента",
            "preparing": "Агент готовит вопросы",
            "ready": "Вопросы готовы",
            "error": "Ошибка подготовки",
        }.get(phase, "Ожидание агента")
        message = error or status.get("message", "")
        if phase == "waiting" and elapsed >= 60 and not message:
            message = 'Агент ещё не начал подготовку. Если Codex завершил ответ, напишите ему "продолжить".'
        draw(
            screen,
            f"{title}. Ожидание: {elapsed} с. {message}",
            ["Esc - сохранить и выйти"],
            0,
        )
        screen.timeout(500)
        if screen.getch() == 27:
            raise KeyboardInterrupt
        if package.exists() and phase != "error":
            try:
                quiz.load_questions(state, json.loads(package.read_text()))
            except (ValueError, KeyError, TypeError, OSError) as failure:
                error = f"Ошибка загрузки вопросов: {failure}"
            else:
                quiz.save(path, state)
    screen.timeout(-1)
    while len(state["answers"]) < len(state["questions"]):
        i = len(state["answers"])
        q = state["questions"][i]
        title = f"{i + 1}/{len(state['questions'])}. {q['prompt']}"
        free = False
        if q["type"] == "text":
            raw = entry(screen, title)
        else:
            options = q["options"] + ["Свой ответ", "Не знаю"]
            picked = choose(screen, title, options, q["type"] == "multiple")
            if len(options) - 1 in picked:
                raw = "не знаю"
            elif len(options) - 2 in picked:
                raw = entry(screen, title)
                free = True
            else:
                raw = ",".join(str(n + 1) for n in picked)
        if free:
            state["answers"].append(
                {"id": q["id"], "answer": raw, "selected": None, "free": True}
            )
        else:
            quiz.answer(state, {"id": q["id"], "answer": raw})
        quiz.save(path, state)
    (path.parent / "completed").touch()
    while True:
        try:
            result = review_results.evaluate(
                path,
                state,
                lambda seconds: draw(
                    screen, f"Анализ ответов: {seconds} с", ["Ожидайте результата"], 0
                ),
            )
            screen.timeout(-1)
            return review_results.display(screen, result)
        except (ValueError, KeyError, TypeError, OSError) as error:
            action = choose(
                screen,
                f"Ошибка оценки: {error}",
                ["Повторить оценку", "Выйти с сохранением"],
            )[0]
            if action == 1:
                return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--environment", type=Path)
    args = parser.parse_args()
    if args.launch:
        envfile = args.session.parent / "proxy.json"
        keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY"]
        keys += [k.lower() for k in keys]
        with open(envfile, "w", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump({k: os.environ[k] for k in keys if k in os.environ}, stream)
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--session",
            str(args.session),
            "--environment",
            str(envfile),
        ]
        subprocess.run(
            [
                "gnome-terminal",
                "--zoom=1.5",
                "--tab",
                "--title=Активное ревью",
                "--",
                *cmd,
            ],
            check=True,
        )
        return
    if args.environment:
        values = json.loads(args.environment.read_text())
        for key in list(os.environ):
            if key.lower().endswith("_proxy"):
                os.environ.pop(key)
        os.environ.update(values)
        args.environment.unlink()
    try:
        # Сообщения quiz не должны нарушать экран, которым управляет curses.
        with redirect_stdout(io.StringIO()):
            cleanup = curses.wrapper(run, args.session)
        if cleanup:
            directory = args.session.resolve().parent
            if directory.parent == Path("/tmp") and directory.name.startswith(
                "active-review-"
            ):
                shutil.rmtree(directory)
        else:
            print(f"Сессия сохранена: {args.session}")
    except KeyboardInterrupt:
        print("Прогресс сохранён.")


if __name__ == "__main__":
    main()
