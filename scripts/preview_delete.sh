#!/usr/bin/env bash
# Deletes every Cloudflare Pages deployment of one preview branch (pr-<N>).
#
#   CF_TOKEN=… ACCOUNT=… PROJECT=rustgis-plugins-preview BRANCH=pr-7 scripts/preview_delete.sh
#
# Run by plugin-preview.yml when a pull request closes, and by its publish job when the pull
# request closed while it was publishing. Failures are warnings: the rest are still deleted.
set -euo pipefail
: "${CF_TOKEN:?}" "${ACCOUNT:?}" "${PROJECT:?}" "${BRANCH:?}"
api="https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/pages/projects/$PROJECT/deployments"
ids=""
page=1
# Every page is listed before anything is deleted: deleting while paging would shift the pages.
while :; do
  if ! got=$(curl -sf --retry 3 -H "Authorization: Bearer $CF_TOKEN" "$api?per_page=25&page=$page&env=preview"); then
    echo "::warning::Could not list $BRANCH's deployments (page $page); some may remain."
    break
  fi
  [ "$(jq '.result | length' <<< "$got")" -gt 0 ] || break
  ids+=" $(jq -r --arg b "$BRANCH" '.result[] | select(.deployment_trigger.metadata.branch == $b) | .id' <<< "$got")"
  page=$((page + 1))
  if [ "$page" -gt 400 ]; then
    echo "::warning::Stopped after 10,000 deployments; some of $BRANCH's may remain."
    break
  fi
done
for id in $ids; do
  if curl -sf --retry 3 -X DELETE -H "Authorization: Bearer $CF_TOKEN" "$api/$id?force=true" > /dev/null; then
    echo "deleted $id"
  else
    echo "::warning::Could not delete deployment $id of $BRANCH."
  fi
done
