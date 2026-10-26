#!/usr/bin/env bash
# Fetch the BOE build stack — shallow clones of the donor repositories.
#
# The stack is third-party code: it is git-ignored and never committed.
# Clones are shallow (--depth 1). Where a pinned commit from
# BOE-BUILD-MANIFEST.md is still reachable it is checked out; otherwise the
# default branch head is used and the deviation is reported.
#
# Usage:
#   bash scripts/fetch_stack.sh                 # fetch all
#   bash scripts/fetch_stack.sh dsrf v4-core    # fetch named repos only

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK="${STACK_DIR:-$ROOT/boe-stack}"
mkdir -p "$STACK"

# name|upstream slug|pinned commit|branch hint
MANIFEST=(
  "stardust-distro|Bianca7492/Distro|-|release"
  "stardust-dsp|daddykev/stardust-dsp|7bee6bc|-"
  "dsrf|ddexnet/dsrf|1dc231e|-"
  "ddex-suite|daddykev/ddex-suite|3529ae5|-"
  "ddex-workbench|daddykev/ddex-workbench|2788924|-"
  "ddex-processor|AudiusProject/ddex-processor|5292f91|-"
  "rd-portal-backend|adytonentertainment/rd-portal-backend|eee2eb1|-"
  "dedex|miqwit/dedex|-|-"
  "ERC-3643|ERC-3643/ERC-3643|2f0704d|-"
  "zora-protocol|ourzora/zora-protocol|32c2931|-"
  "splits-contracts|0xSplits/splits-contracts|6f474b8|-"
  "openzeppelin-contracts|OpenZeppelin/openzeppelin-contracts|11565e5|-"
  "v4-core|Uniswap/v4-core|46c6834|-"
  "bnbchain-mcp|bnb-chain/bnbchain-mcp|c4557e2|-"
  "eunomia|eunomia-finance/eunomia|8fb280d|-"
  "nirium|nirium-protocol/nirium|443d52f|-"
  "labelgrid-mcp|labelgrid/labelgrid-mcp|42e6428|-"
  "x402|coinbase/x402|0e6e8c5|-"
  "btcpayserver|btcpayserver/btcpayserver|a298387|-"
)

want=("$@")
selected() {
  [ ${#want[@]} -eq 0 ] && return 0
  for w in "${want[@]}"; do [ "$w" = "$1" ] && return 0; done
  return 1
}

ok=0; failed=0; pinned_ok=0; pinned_miss=0

for entry in "${MANIFEST[@]}"; do
  IFS='|' read -r name slug commit branch <<<"$entry"
  selected "$name" || continue

  dest="$STACK/$name"
  if [ -d "$dest/.git" ]; then
    echo "== $name: already present, skipping"
    ok=$((ok+1)); continue
  fi

  echo "== $name  <-  $slug"
  clone_args=(--depth 1 --quiet)
  [ "$branch" != "-" ] && clone_args+=(--branch "$branch")

  if ! git clone "${clone_args[@]}" "https://github.com/$slug.git" "$dest" 2>/dev/null; then
    echo "   !! clone failed: $slug"
    failed=$((failed+1)); continue
  fi

  if [ "$commit" != "-" ]; then
    if git -C "$dest" cat-file -e "$commit^{commit}" 2>/dev/null; then
      git -C "$dest" checkout --quiet "$commit"
      echo "   pinned at $commit"
      pinned_ok=$((pinned_ok+1))
    else
      head="$(git -C "$dest" rev-parse --short HEAD)"
      echo "   !! pinned $commit not in shallow clone; using HEAD $head"
      pinned_miss=$((pinned_miss+1))
    fi
  fi
  ok=$((ok+1))
done

echo
echo "fetched: $ok  failed: $failed  pinned-ok: $pinned_ok  pinned-missed: $pinned_miss"
echo "stack at: $STACK"
du -sh "$STACK" 2>/dev/null | awk '{print "size: "$1}'
