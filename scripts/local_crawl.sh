#!/bin/zsh
# Daily crawl, from a machine in Taiwan, of the companies marked `local_only` in companies.yaml
# (Workday tenants, UMC, Far EasTone): their sites refuse or time out from GitHub's cloud runners.
# Same polite, self-identifying crawler as the cloud run; it just connects from a Taiwan network.
#
# Runs inside a dedicated clone (see install_local_schedule.sh) so it never touches your working copy.
# Optional LLM stage: store your key once with
#   security add-generic-password -a "$USER" -s ai-career-anthropic -w
set -uo pipefail
REPO="${0:A:h:h}"
cd "$REPO" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
echo "=== $(date '+%F %T') local crawl in $REPO"

if key=$(security find-generic-password -a "$USER" -s ai-career-anthropic -w 2>/dev/null); then
  export ANTHROPIC_API_KEY="$key"
else
  echo "no ai-career-anthropic key in Keychain: running without the LLM stage"
fi

crawl_and_push() {
  git fetch -q origin main && git reset -q --hard origin/main || return 1
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/pip -q install -r requirements.txt || return 1
  caffeinate -i .venv/bin/python -u -m scraper.run --mode local || return 1
  git add data/
  if git diff --cached --quiet; then echo "no data changes"; return 0; fi
  git commit -q -m "data: local crawl $(date +%F) (Workday, UMC, FET)"
  git pull -q --rebase origin main && git push -q origin main
}

# A cloud run may push data while we crawl; on a conflict, start over from the new tip (the detail
# cache in .cache/ makes the second pass much faster).
for attempt in 1 2; do
  if crawl_and_push; then echo "=== done"; exit 0; fi
  git rebase --abort 2>/dev/null
  echo "attempt $attempt failed, retrying from origin/main"
done
echo "=== failed"
exit 1
