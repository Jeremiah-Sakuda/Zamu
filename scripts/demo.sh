#!/usr/bin/env bash
#
# A narrated walk through the whole loop, in a terminal.
#
# Written for recording: it pauses between beats so a voiceover has room, and every
# command it runs is one you could type yourself. Nothing here is staged — the output
# is whatever Zamu actually does with the seeded roster.
#
#   ./scripts/demo.sh            # pause for a keypress between beats
#   ./scripts/demo.sh --auto     # run straight through, for a screen recording
#
set -euo pipefail

cd "$(dirname "$0")/.."

ZAMU="${ZAMU:-.venv/bin/zamu}"
DB="${ZAMU_DB:-.zamu/demo.sqlite}"
AUTO="${1:-}"

bold=$'\033[1m'; dim=$'\033[2m'; blue=$'\033[34m'; reset=$'\033[0m'

say()  { printf '\n%s%s%s\n\n' "$bold" "$1" "$reset"; }
note() { printf '%s%s%s\n' "$dim" "$1" "$reset"; }
run()  { printf '%s$ %s%s\n\n' "$blue" "$*" "$reset"; "$@"; }

beat() {
  if [ "$AUTO" = "--auto" ]; then sleep 2; else
    printf '\n%s— press enter —%s' "$dim" "$reset"; read -r _; fi
}

if [ ! -x "$ZAMU" ]; then
  echo "Zamu is not installed. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[api,agent,dev]'" >&2
  exit 1
fi

clear
say "Zamu — an agent that keeps a volunteer roster covered"
note "Riverside Community Food Bank runs on six volunteers and one coordinator."
note "Nobody has an account. Nobody installs anything. The roster is a spreadsheet."
beat

say "1 · The roster, this morning"
run "$ZAMU" --db "$DB" demo --reset
run "$ZAMU" --db "$DB" status
note "Priya replied to her reminder three hours ago to say she can't make her shift."
note "Nobody has posted in the group chat. The shift is simply uncovered."
beat

say "2 · Who has actually been carrying this"
run "$ZAMU" --db "$DB" fairness
note "Amara has done ten shifts in six weeks. Marcus has done one."
note "This is the fact a group chat never has, and the reason reliable people quit."
beat

say "3 · Who Zamu would ask, and why"
# Pin the duty so every later beat is about the same shift. Without this the
# counterfactual quietly compares two different gaps, which proves nothing.
GAP="$("$ZAMU" --db "$DB" gaps | awk '/dut_/ {print $1; exit}')"
run "$ZAMU" --db "$DB" rank "$GAP"
note "Deterministic. Same roster, same instant, same order — every time."
note "And it says who it will not ask, which is the first thing anyone asks about a ranking."
beat

say "4 · One person. Not the group."
run "$ZAMU" --db "$DB" fill "$GAP"
note "One message, to one named person, about one shift, with a deadline."
note "Broadcasting is the bug: it spreads responsibility until nobody feels it."
beat

say "5 · What Marcus actually receives"
run "$ZAMU" --db "$DB" outbox
beat

say "6 · Marcus says yes"
TOKEN="$("$ZAMU" --db "$DB" outbox | awk '/zamu accept/ {print $NF; exit}')"
run "$ZAMU" --db "$DB" accept "$TOKEN"
beat

say "7 · The part almost nobody ships"
run "$ZAMU" --db "$DB" receipts --limit 2
note "Zamu did not believe its own write. It re-read the roster and compared what it"
note "found to what it intended. That difference is the whole difference between a"
note "demo and a system you would let near a real organization."
beat

say "8 · Nothing needed the coordinator"
run "$ZAMU" --db "$DB" brief
beat

say "9 · Now the same shift again, with the permission taken away"
note "Rewinding to exactly where we started, so this is the same gap and not a new one."
run "$ZAMU" --db "$DB" demo --reset
run "$ZAMU" --db "$DB" grants
run "$ZAMU" --db "$DB" revoke send_ask
run "$ZAMU" --db "$DB" fill "$GAP"
note "No message left the system. A finished draft is waiting for a human to send."
note "That is not the prompt asking nicely — it is a hook that cancels the tool call"
note "before the tool body runs, checked against grants a person created."
note ""
note "Notice the shortlist also changed. Ben never agreed to hear from Zamu, so Zamu"
note "may not message him — but a coordinator may. Authority does not just change what"
note "Zamu does; it changes who is askable at all."
beat

say "10 · And with nothing granted at all"
note "Rewinding once more, so this is the same gap from the same starting point."
run "$ZAMU" --db "$DB" demo --reset
run "$ZAMU" --db "$DB" revoke send_ask
run "$ZAMU" --db "$DB" revoke draft_ask
run "$ZAMU" --db "$DB" fill "$GAP"
note "It stops, names the rule, and hands over. Silence would have been the bug."
beat

say "Zamu"
note "Coverage is a promise, not a task."
note "github.com/Jeremiah-Sakuda/Zamu"
echo
