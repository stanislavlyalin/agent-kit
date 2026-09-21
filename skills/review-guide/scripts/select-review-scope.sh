#!/usr/bin/env bash
set -euo pipefail

error() {
    printf '%s\n' "$1" >&2
    exit 1
}

prompt() {
    printf '%s' "$1" >&2
}

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    error "Ошибка: скрипт нужно запускать внутри Git-репозитория."
fi

if ! git rev-parse --verify HEAD^{commit} >/dev/null 2>&1; then
    error "Ошибка: в репозитории нет коммитов для ревью."
fi

branch_base() {
    local current_branch upstream candidate merge_base

    current_branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)

    if upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null); then
        merge_base=$(git merge-base HEAD "$upstream") || return 1
        printf '%s\n' "$merge_base"
        return
    fi

    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        merge_base=$(git merge-base HEAD "$candidate") || continue
        printf '%s\n' "$merge_base"
        return
    done < <(git for-each-ref --format='%(refname:short)' 'refs/remotes/*/HEAD')

    for candidate in main master origin/main origin/master; do
        [ "$candidate" = "$current_branch" ] && continue
        if git rev-parse --verify --quiet "$candidate^{commit}" >/dev/null; then
            merge_base=$(git merge-base HEAD "$candidate") || continue
            printf '%s\n' "$merge_base"
            return
        fi
    done

    return 1
}

prompt $'Что вы хотите посмотреть?\n\n1) Все изменения текущей ветки\n2) Последний коммит\n3) Последние N коммитов\n\nВыбор: '
read -r choice || error "Ошибка: не удалось прочитать выбор."

case "$choice" in
    1)
        base=$(branch_base) || error "Ошибка: не удалось надёжно определить ветку-основу. Укажите base branch для ревью."
        printf 'MODE=branch\nBASE=%s\nHEAD=HEAD\n' "$base"
        ;;
    2)
        git rev-parse --verify HEAD^ >/dev/null 2>&1 || error "Ошибка: у последнего коммита нет родителя; выберите все доступные коммиты."
        printf 'MODE=last\nBASE=HEAD^\nHEAD=HEAD\n'
        ;;
    3)
        prompt 'Сколько последних коммитов? '
        read -r count || error "Ошибка: не удалось прочитать количество коммитов."
        [[ "$count" =~ ^[1-9][0-9]*$ ]] || error "Ошибка: введите положительное целое число."

        total=$(git rev-list --count HEAD)
        (( count <= total )) || error "Ошибка: в истории только $total коммитов."

        if (( count == total )); then
            base=$(git hash-object -t tree /dev/null)
        else
            base="HEAD~$count"
        fi

        printf 'MODE=commits\nCOUNT=%s\nBASE=%s\nHEAD=HEAD\n' "$count" "$base"
        ;;
    *)
        error "Ошибка: выберите 1, 2 или 3."
        ;;
esac
