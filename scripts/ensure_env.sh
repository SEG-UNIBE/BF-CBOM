#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
    script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    repo_root=$(cd "${script_dir}/.." && pwd)
    env_dir="${repo_root}/docker/env"
    shopt -s nullglob
    templates=("${env_dir}"/*.env.template)
    if [[ ${#templates[@]} -eq 0 ]]; then
        echo "No environment templates found in ${env_dir}"
        exit 0
    fi
    for template_path in "${templates[@]}"; do
        target_path="${template_path%.template}"
        "${BASH_SOURCE[0]}" "$template_path" "$target_path"
    done
    exit 0
fi

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <template-file> <target-file>" >&2
    exit 1
fi

template_path=$1
target_path=$2

if [[ ! -f "$template_path" ]]; then
    echo "Template file '$template_path' not found" >&2
    exit 1
fi

mkdir -p "$(dirname "$target_path")"

if [[ -f "$target_path" ]]; then
    echo "$target_path already exists; leaving unchanged"
    exit 0
fi

echo "Creating $target_path from template"
cp "$template_path" "$target_path"
