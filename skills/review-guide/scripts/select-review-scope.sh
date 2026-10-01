#!/usr/bin/env bash
set -euo pipefail

error() {
    printf '%s\n' "$1" >&2
    exit 1
}

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    error "Ошибка: скрипт нужно запускать внутри Git-репозитория."
fi

if ! git rev-parse --verify HEAD^{commit} >/dev/null 2>&1; then
    error "Ошибка: в репозитории нет коммитов для ревью."
fi

branch_ref() {
    local name="$1"

    if git show-ref --verify --quiet "refs/heads/$name"; then
        printf '%s\n' "$name"
    elif git show-ref --verify --quiet "refs/remotes/$name"; then
        printf '%s\n' "$name"
    else
        return 1
    fi
}

branch_base() {
    local branch="$1" target upstream candidate merge_base remote

    target=$(git rev-parse --verify "$branch^{commit}")
    upstream=$(git for-each-ref --format='%(upstream:short)' "refs/heads/$branch")
    if [ -n "$upstream" ]; then
        merge_base=$(git merge-base "$target" "$upstream") || return 1
        printf '%s\n' "$merge_base"
        return
    fi

    if [[ "$branch" == */* ]]; then
        remote=${branch%%/*}
        candidate=$(git symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null || true)
        if [ -n "$candidate" ]; then
            merge_base=$(git merge-base "$target" "$candidate") || return 1
            printf '%s\n' "$merge_base"
            return
        fi
    fi

    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        [ "$candidate" = "$branch" ] && continue
        merge_base=$(git merge-base "$target" "$candidate") || continue
        printf '%s\n' "$merge_base"
        return
    done < <(git for-each-ref --format='%(refname:short)' 'refs/remotes/*/HEAD')

    for candidate in main master origin/main origin/master; do
        [ "$candidate" = "$branch" ] && continue
        if git rev-parse --verify --quiet "$candidate^{commit}" >/dev/null; then
            merge_base=$(git merge-base "$target" "$candidate") || continue
            printf '%s\n' "$merge_base"
            return
        fi
    done

    return 1
}

[ "$#" -eq 1 ] || error "Использование: select-review-scope.sh <имя-ветки|число-коммитов|хеш-коммита>."
scope="$1"

if [[ "$scope" =~ ^[1-9][0-9]*$ ]]; then
    total=$(git rev-list --count HEAD)
    (( scope <= total )) || error "Ошибка: в истории только $total коммитов."

    if (( scope == total )); then
        base=$(git hash-object -t tree /dev/null)
    else
        base="HEAD~$scope"
    fi

    printf 'MODE=commits\nCOUNT=%s\nBASE=%s\nHEAD=HEAD\n' "$scope" "$base"
elif [[ "$scope" =~ ^[0-9A-Fa-f]{4,64}$ ]]; then
    target=$(git rev-parse --verify "$scope^{commit}" 2>/dev/null) || error "Ошибка: коммит с таким хешем не найден."

    if base=$(git rev-parse --verify "$target^" 2>/dev/null); then
        :
    else
        base=$(git hash-object -t tree /dev/null)
    fi

    printf 'MODE=commit\nCOMMIT=%s\nBASE=%s\nHEAD=%s\n' "$target" "$base" "$target"
else
    branch=$(branch_ref "$scope") || error "Ошибка: ветка с таким именем не найдена."
    target=$(git rev-parse --verify "$branch^{commit}")
    base=$(branch_base "$branch") || error "Ошибка: не удалось надёжно определить ветку-основу для $branch."
    printf 'MODE=branch\nBRANCH=%s\nBASE=%s\nHEAD=%s\n' "$branch" "$base" "$target"
fi
