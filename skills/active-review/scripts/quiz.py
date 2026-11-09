"""Детерминированное состояние активного ревью; ввод передаёт агент Codex."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


MIN_QUESTIONS = 5
MAX_QUESTIONS = 15
VERDICTS = ("верно", "частично верно", "неверно")


def git(repo, *args, input_text=None):
    result = subprocess.run(
        ["git", "-C", repo, *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Команда Git завершилась ошибкой")
    return result.stdout.rstrip("\n")


def commit(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")


def parent(repo, sha):
    parents = git(repo, "rev-list", "--parents", "-n", "1", sha).split()
    return parents[1] if len(parents) > 1 else None


def candidates(repo, head, branch):
    history = git(repo, "rev-list", "--first-parent", head).splitlines()
    distances = {sha: number for number, sha in enumerate(history)}
    refs = git(
        repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"
    )
    groups = {}
    for ref in refs.splitlines():
        if ref == f"refs/heads/{branch}" or ref.endswith("/HEAD"):
            continue
        if ref.startswith("refs/remotes/") and ref.split("/", 3)[-1] == branch:
            continue
        sha = commit(repo, ref)
        # Ветки, содержащие текущую вершину, не определяют её точку ответвления.
        try:
            base = git(repo, "merge-base", head, sha)
        except ValueError:
            continue
        if base == head or base not in distances:
            continue
        group = groups.setdefault(
            sha, {"refs": [], "base": base, "distance": distances[base]}
        )
        group["refs"].append(ref)
    ordered = sorted(groups.values(), key=lambda row: (row["distance"], row["refs"][0]))
    return (
        [row for row in ordered if row["distance"] == ordered[0]["distance"]]
        if ordered
        else []
    )


def save(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def start(repo):
    repo = git(str(Path(repo).resolve()), "rev-parse", "--show-toplevel")
    head = commit(repo, "HEAD")
    branch = git(repo, "branch", "--show-current")
    choices = candidates(repo, head, branch)
    path = Path(tempfile.mkdtemp(prefix="active-review-")) / "state.json"
    state = {
        "repo": repo,
        "head": head,
        "branch": branch,
        "candidates": choices,
        "scope": None,
        "questions": [],
        "answers": [],
        "grades": None,
    }
    save(path, state)
    proposed = choices[0]["refs"][0] if len(choices) == 1 else "указать основу"
    print(json.dumps({"session": str(path), "candidates": choices}, ensure_ascii=False))
    print("Что именно ревьювим?")
    print(
        f"1. Ветку {branch or '(detached HEAD)'} относительно {proposed} (предположение)."
    )
    print("2. Последние N коммитов - введите N.")
    print("3. Конкретный коммит - введите хеш.")
    print("4. Диапазон A..B, включая оба коммита - введите хеши.")
    print("Ответ: номер и параметр; для 1 можно подтвердить основу или указать другую.")


def scope(state, mode, value):
    if state["scope"] is not None:
        raise ValueError(
            "Диапазон уже зафиксирован; для другого диапазона начните новый тест"
        )
    repo, head = state["repo"], state["head"]
    if mode == "branch":
        if not state["branch"]:
            raise ValueError("Detached HEAD: выберите коммит или диапазон")
        if not value:
            if len(state["candidates"]) != 1:
                raise ValueError("Укажите ветку-основу: однозначного предположения нет")
            value = state["candidates"][0]["refs"][0]
        base = git(repo, "merge-base", head, commit(repo, value))
    elif mode == "last":
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("N должно быть положительным целым числом")
        history = git(repo, "rev-list", "--first-parent", head).splitlines()
        count = int(value)
        if count > len(history):
            raise ValueError(
                f"Доступно только {len(history)} коммитов по первым родителям"
            )
        base = parent(repo, history[count - 1])
    elif mode == "commit":
        head = hash_commit(repo, value)
        base = parent(repo, head)
    else:
        parts = value.split("..")
        if len(parts) != 2:
            raise ValueError("Ожидается включительный диапазон A..B")
        first, head = [hash_commit(repo, part) for part in parts]
        git(repo, "merge-base", "--is-ancestor", first, head)
        base = parent(repo, first)
    empty = git(repo, "hash-object", "-t", "tree", "--stdin", input_text="")
    tree_base = base or empty
    if not git(repo, "diff", "--name-only", tree_base, head, "--"):
        raise ValueError("В выбранном диапазоне нет итоговых изменений")
    state["scope"] = {
        "mode": mode,
        "value": value,
        "base": base,
        "tree_base": tree_base,
        "head": head,
        "history": f"{base}..{head}" if base else head,
    }
    print(json.dumps(state["scope"], ensure_ascii=False))


def hash_commit(repo, value):
    if not re.fullmatch(r"[0-9a-fA-F]{4,64}", value):
        raise ValueError("Введите хеш коммита длиной не менее четырёх символов")
    return commit(repo, value)


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Ожидается непустой текст")
    return value


def load_questions(state, questions):
    if state["scope"] is None or state["questions"]:
        raise ValueError("Нужен выбранный диапазон без ранее загруженных вопросов")
    limit = state.get("question_limit")
    minimum, maximum = (MIN_QUESTIONS, MAX_QUESTIONS) if limit is None else (1, limit)
    if type(maximum) is not int or maximum < 1:
        raise ValueError("Лимит должен быть положительным целым числом")
    if not isinstance(questions, list) or not minimum <= len(questions) <= maximum:
        raise ValueError(f"Нужно от {minimum} до {maximum} вопросов")
    ids = set()
    for question in questions:
        identifier = text(question["id"])
        if identifier in ids:
            raise ValueError("Идентификаторы вопросов должны быть уникальны")
        ids.add(identifier)
        text(question["prompt"])
        text(question["explanation"])
        kind = question["type"]
        if kind not in ("single", "multiple", "text"):
            raise ValueError("Неизвестный тип вопроса")
        if kind == "text":
            text(question["rubric"])
        else:
            options, correct = question["options"], question["correct"]
            if not isinstance(options, list) or len(options) < 2:
                raise ValueError("Нужно минимум два варианта")
            for option in options:
                text(option)
            if (
                not isinstance(correct, list)
                or not correct
                or any(
                    type(n) is not int or not 1 <= n <= len(options) for n in correct
                )
                or len(set(correct)) != len(correct)
                or (kind == "single" and len(correct) != 1)
            ):
                raise ValueError("Некорректный ключ ответов")
        if not isinstance(question["refs"], list) or not question["refs"]:
            raise ValueError("Нужна ссылка на код выбранной ревизии")
        for ref in question["refs"]:
            path = text(ref["path"])
            text(ref["symbol"])
            revision = ref.get("revision", state["scope"]["head"])
            if (
                revision not in (state["scope"]["base"], state["scope"]["head"])
                or revision is None
            ):
                raise ValueError(
                    "Ссылка должна относиться к начальной или конечной ревизии"
                )
            source = git(state["repo"], "show", f"{revision}:{path}")
            if type(ref["line"]) is not int or not 1 <= ref["line"] <= len(
                source.splitlines()
            ):
                raise ValueError("Строка ссылки вне файла")
    state["questions"] = questions
    print(f"Загружено вопросов: {len(questions)}")


def next_question(state):
    if not state["questions"]:
        raise ValueError("Сначала загрузите вопросы")
    index = len(state["answers"])
    if index == len(state["questions"]):
        print("Все ответы получены. Можно сформировать вердикт.")
        return
    question = state["questions"][index]
    print(f"{index + 1}/{len(state['questions'])}. {question['prompt']}")
    labels = {
        "single": "Выберите один вариант.",
        "multiple": "Выберите все верные варианты.",
        "text": "Введите ответ текстом.",
    }
    print(labels[question["type"]])
    for number, option in enumerate(question.get("options", []), 1):
        print(f"{number}. {option}")
    print("Допустим ответ «не знаю».")


def answer(state, payload):
    index = len(state["answers"])
    if index >= len(state["questions"]):
        raise ValueError("Нет вопроса, ожидающего ответа")
    question = state["questions"][index]
    if payload["id"] != question["id"]:
        raise ValueError("Ответ относится к другому вопросу")
    raw = text(payload["answer"]).strip()
    selected = None
    if question["type"] != "text" and raw.casefold() != "не знаю":
        if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", raw):
            raise ValueError("Введите номер или номера через запятую")
        selected = [int(part) for part in raw.split(",")]
        if (
            len(set(selected)) != len(selected)
            or any(not 1 <= n <= len(question["options"]) for n in selected)
            or (question["type"] == "single" and len(selected) != 1)
        ):
            raise ValueError("Недопустимый набор вариантов")
    state["answers"].append({"id": question["id"], "answer": raw, "selected": selected})
    print(f"Сохранён ответ {index + 1}/{len(state['questions'])}")


def results(state, grades):
    if not state["questions"] or len(state["answers"]) != len(state["questions"]):
        raise ValueError("Сначала ответьте на все вопросы")
    if state["grades"] is not None:
        grades = state["grades"]
    expected = {q["id"] for q in state["questions"] if q["type"] == "text"}
    if not isinstance(grades, dict) or set(grades) != expected:
        raise ValueError("Нужны оценки всех и только текстовых вопросов")
    rows = []
    for question, response in zip(state["questions"], state["answers"]):
        if question["type"] == "text":
            grade = grades[question["id"]]
            verdict, explanation = grade["verdict"], text(grade["explanation"])
            if verdict not in VERDICTS:
                raise ValueError("Неизвестная оценка")
        else:
            correct = set(response["selected"] or []) == set(question["correct"])
            verdict = "верно" if correct else "неверно"
            explanation = question["explanation"]
        rows.append(
            {
                "id": question["id"],
                "verdict": verdict,
                "answer": response["answer"],
                "explanation": explanation,
                "refs": question["refs"],
            }
        )
    state["grades"] = grades
    print(
        json.dumps(
            {
                "counts": {v: sum(r["verdict"] == v for r in rows) for v in VERDICTS},
                "results": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("start", "scope", "load", "next", "answer", "results")
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session", type=Path)
    parser.add_argument("--mode", choices=("branch", "last", "commit", "range"))
    parser.add_argument("--value", default="")
    parser.add_argument("--file", type=Path, help="JSON вопросов, ответа или оценок")
    args = parser.parse_args()
    try:
        if args.command == "start":
            start(args.repo)
            return
        if args.session is None:
            raise ValueError("Нужен --session из команды start")
        state = json.loads(args.session.read_text(encoding="utf-8"))
        payload = (
            json.loads(args.file.read_text(encoding="utf-8")) if args.file else None
        )
        if args.command == "scope":
            if args.mode is None:
                raise ValueError("Нужен --mode")
            scope(state, args.mode, args.value)
        elif args.command == "load":
            load_questions(state, payload)
        elif args.command == "next":
            next_question(state)
        elif args.command == "answer":
            answer(state, payload)
        else:
            results(state, payload or {})
        if args.command != "next":
            save(args.session, state)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Ошибка: {error}\n")


if __name__ == "__main__":
    main()
