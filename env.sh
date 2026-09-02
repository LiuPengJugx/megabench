#!/usr/bin/env bash

_megabench_is_sourced() {
  [[ "${BASH_SOURCE[0]}" != "$0" ]]
}

_megabench_fail() {
  echo "env.sh: $*" >&2
  if _megabench_is_sourced; then
    return 1
  fi
  exit 1
}

if ! command -v uv >/dev/null 2>&1; then
  _megabench_fail "uv is not installed. Install it from https://docs.astral.sh/uv/getting-started/installation/"
fi

MEGABENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || _megabench_fail "cannot resolve project root"
export MEGABENCH_ROOT
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$MEGABENCH_ROOT/.venv}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

cd "$MEGABENCH_ROOT" || _megabench_fail "cannot enter project root"

sync_args=()
if [[ "${MEGABENCH_NO_DEV:-0}" == "1" ]]; then
  sync_args+=(--no-dev)
fi

uv sync "${sync_args[@]}" || _megabench_fail "uv sync failed"

if ! _megabench_is_sourced; then
  _megabench_fail "run this script with: source ./env.sh"
fi

# shellcheck disable=SC1091
source "$UV_PROJECT_ENVIRONMENT/bin/activate" || _megabench_fail "cannot activate venv"
case ":${PYTHONPATH:-}:" in
  *":$MEGABENCH_ROOT/src:"*) ;;
  *) export PYTHONPATH="$MEGABENCH_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" ;;
esac

echo "MegaBench environment ready: $UV_PROJECT_ENVIRONMENT"
return 0
