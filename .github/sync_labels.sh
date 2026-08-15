#!/bin/sh
# Apply .github/labels.json to the repository.
#
#   .github/sync_labels.sh [--repo owner/name] [--prune]
#
# Without this, labels.json is decoration: the real taxonomy lives in GitHub's
# settings, the file drifts, and the drift is invisible until someone labels an
# issue with something the file has never heard of.
#
# Creates or updates every label in the file. --prune additionally lists the
# labels that exist on the repository but not in the file, and stops there: it
# does not delete them. Removing a label strips it from every issue that carries
# it, with no undo and no record of what was lost, so that stays a decision a
# person makes one label at a time.

set -eu

repo=""
prune=0

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            if [ $# -lt 2 ]; then
                echo "sync_labels.sh: --repo requires owner/name" >&2
                exit 2
            fi
            repo="$2"
            shift 2
            ;;
        --prune) prune=1; shift ;;
        *)
            echo "sync_labels.sh: unknown argument: $1" >&2
            echo "usage: sync_labels.sh [--repo owner/name] [--prune]" >&2
            exit 2
            ;;
    esac
done

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
file="$root/.github/labels.json"

if [ ! -f "$file" ]; then
    echo "sync_labels.sh: $file not found" >&2
    exit 1
fi

if [ -n "$repo" ]; then
    set -- --repo "$repo"
else
    set --
fi

# Tab-separated so a description with spaces survives the read.
python3 -c '
import json, sys
for label in json.load(open(sys.argv[1])):
    print("\t".join((label["name"], label["color"], label.get("description", ""))))
' "$file" | while IFS="	" read -r name color description; do
    # --force updates an existing label instead of failing on it, which is what
    # makes this safe to run repeatedly.
    gh label create "$name" --color "$color" --description "$description" --force "$@"
done

if [ "$prune" -eq 1 ]; then
    echo
    echo "On the repository but not in labels.json:"
    gh label list --limit 200 --json name --jq '.[].name' "$@" |
        python3 -c '
import json, sys
known = {label["name"] for label in json.load(open(sys.argv[1]))}
extra = [line.strip() for line in sys.stdin if line.strip() and line.strip() not in known]
for name in extra:
    print("  " + name)
if extra:
    print("\nDelete one with: gh label delete <name>")
    print("It is removed from every issue that carries it. There is no undo.")
else:
    print("  (none)")
' "$file"
fi
