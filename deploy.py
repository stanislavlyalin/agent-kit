#!/usr/bin/env python3

from pathlib import Path
from shutil import copyfile
from tempfile import NamedTemporaryFile


def main():
    materials = []
    while True:
        name = input("Название материала (или 'готово' для завершения): ")
        if name == "готово":
            break
        link = input("Ссылка на материал: ")
        materials.append(f"- {name}: `{link}`;")

    agents = Path("AGENTS.md")
    content = agents.read_text(encoding="utf-8")
    content = content.replace("<MATERIALS>", "\n".join(materials))

    with NamedTemporaryFile("w", encoding="utf-8", delete=False) as temporary:
        temporary.write(content)

    destination = input("Путь для копирования AGENTS.md: ")
    copyfile(temporary.name, destination)
    Path(temporary.name).unlink()


if __name__ == "__main__":
    main()
