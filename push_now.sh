#!/bin/zsh
# Manual run: same as a scheduled GitHub Actions run, but from this machine. Use it when
# GitHub skips/delays a cron run and an early game is about to start.
#
# Pulls origin/main first (the bot pushes team_ratings.db on its own, and a stale local DB
# would fork the binary file), then runs daily_run.py, which regenerates docs/index.html,
# records pre-game picks, and commits + pushes to main itself.
set -e
cd "${0:A:h}"

if ! git diff --quiet -- docs/index.html team_ratings.db; then
  echo "docs/index.html or team_ratings.db has uncommitted local changes -- commit or discard them first." >&2
  exit 1
fi

stashed=0
if [[ -n "$(git status --porcelain)" ]]; then
  git stash push -u -q -m "push_now.sh autostash" && stashed=1
fi
git pull -q --ff-only
(( stashed )) && git stash pop -q

venv/bin/python daily_run.py
