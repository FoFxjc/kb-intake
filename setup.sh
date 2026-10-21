#!/usr/bin/env bash
# kb-intake: install everything, then check the installation (docs/DEPLOYMENT.md, step 4). Run it from anywhere;
# it works in the directory it lives in. Safe to run again: what is already in place is kept.
#
#   1. read .env (variables already set in the environment win)
#   2. a Python 3.12 environment: conda (env "kb-intake") when conda is available, else a venv in ./.venv;
#      conda only provides Python, every package comes from pip with constraints.txt
#   3. pip install -c constraints.txt .   (skipped when this source is already installed)
#   4. the models in INTAKE_MODELS_DIR: verified, or put into the expected layout first (kb-intake models import)
#   5. kb-intake doctor --smoke
#
# Exit code 0 when kb-intake is ready, 1 otherwise; every failure says why and what to do.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
fail() {  # fail "what went wrong" "what to do"
  printf '\nFAILED: %s\n' "$1" >&2
  [ -n "${2:-}" ] && printf 'What to do: %s\n' "$2" >&2
  exit 1
}

# ---------- 1. .env ----------
step "1/5 settings (.env)"
[ -f .env ] || fail "there is no .env in $ROOT" "copy .env.example to .env and fill it in (DEPLOYMENT.md step 3)"
while IFS= read -r line || [ -n "$line" ]; do
  line="${line#"${line%%[![:space:]]*}"}"                       # leading blanks
  case "$line" in ''|'#'*) continue ;; esac
  line="${line#export }"
  name="${line%%=*}"; value="${line#*=}"
  [ "$name" = "$line" ] && continue                              # no '='
  name="${name%"${name##*[![:space:]]}"}"
  [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  value="${value#"${value%%[![:space:]]*}"}"
  if [[ "$value" == \"*\"* || "$value" == \'*\'* ]]; then       # quoted: up to the closing quote
    q="${value:0:1}"; value="${value:1}"; value="${value%%"$q"*}"
  else                                                          # unquoted: ' #' starts a comment
    value="${value%% #*}"; value="${value%"${value##*[![:space:]]}"}"
  fi
  [ -n "${!name+x}" ] && continue                                # the environment wins
  export "$name=$value"
done < .env
ok "read $(grep -cE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' .env) setting(s) from .env"
abspath() { case "$1" in /*) printf '%s' "$1" ;; ~*) printf '%s' "${1/#\~/$HOME}" ;; *) printf '%s/%s' "$ROOT" "${1#./}" ;; esac; }
export INTAKE_MODELS_DIR="${INTAKE_MODELS_DIR:-./models}"
for var in INTAKE_MODELS_DIR INTAKE_DATA_DIR INTAKE_CACHE_DIR INTAKE_WORK_DIR INTAKE_DELIVERY_DIR INTAKE_CONFIG \
           SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE PIP_CERT; do   # relative to this directory, as kb-intake reads them
  [ -n "${!var:-}" ] && export "$var=$(abspath "${!var}")"
done
if [ -n "${SSL_CERT_FILE:-}" ] && [ -z "${PIP_CERT:-}" ]; then   # pip does not read SSL_CERT_FILE
  export PIP_CERT="$SSL_CERT_FILE"
fi
[ -n "${PIP_INDEX_URL:-}" ] && ok "pip index: $PIP_INDEX_URL" || ok "pip index: your pip configuration (unchanged)"

# ---------- 2. Python 3.12 environment ----------
step "2/5 Python 3.12 environment"
MODE="${INTAKE_ENV:-auto}"
CONDA="${CONDA_EXE:-$(command -v conda || command -v mamba || true)}"
case "$MODE" in
  auto) [ -n "$CONDA" ] && MODE=conda || MODE=venv ;;
  conda) [ -n "$CONDA" ] || fail "INTAKE_ENV=conda, but conda was not found" "install conda (or miniforge), or set INTAKE_ENV=venv" ;;
  venv) ;;
  *) fail "INTAKE_ENV=$MODE" "use auto, conda or venv" ;;
esac
if [ "$MODE" = conda ]; then
  ENV_NAME="${INTAKE_CONDA_ENV:-kb-intake}"
  PREFIX="$("$CONDA" env list 2>/dev/null | awk -v n="$ENV_NAME" '$1 == n {print $NF}')"
  if [ -z "$PREFIX" ]; then
    echo "   creating conda environment $ENV_NAME (Python 3.12 only; packages come from pip)"
    "$CONDA" create -y -q -n "$ENV_NAME" python=3.12 pip >/dev/null \
      || fail "conda could not create the environment $ENV_NAME" "check your conda channels / mirror (.condarc), or set INTAKE_ENV=venv to use a python3.12 instead"
    PREFIX="$("$CONDA" env list 2>/dev/null | awk -v n="$ENV_NAME" '$1 == n {print $NF}')"
  fi
  PY="$PREFIX/bin/python"
  RUN_HINT="conda activate $ENV_NAME   (then run kb-intake in $ROOT)"
else
  BASE_PY="${INTAKE_PYTHON:-python3.12}"
  command -v "$BASE_PY" >/dev/null || fail "$BASE_PY not found" "install Python 3.12 (python.org, your package manager or conda), or set INTAKE_PYTHON to its path"
  if [ ! -x .venv/bin/python ]; then
    echo "   creating .venv with $BASE_PY"
    "$BASE_PY" -m venv .venv || fail "could not create .venv with $BASE_PY" "on Debian/Ubuntu install python3.12-venv"
  fi
  PY="$ROOT/.venv/bin/python"
  RUN_HINT="source $ROOT/.venv/bin/activate   (then run kb-intake in $ROOT)"
fi
[ -x "$PY" ] || fail "no Python at $PY" "remove the broken environment (conda env remove -n ${INTAKE_CONDA_ENV:-kb-intake}, or rm -rf .venv) and run ./setup.sh again"
"$PY" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' \
  || fail "$PY is Python $("$PY" -c 'import platform; print(platform.python_version())'), not 3.12" "remove that environment (conda env remove -n ${INTAKE_CONDA_ENV:-kb-intake}, or rm -rf .venv) and run ./setup.sh again"
ok "$MODE: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# ---------- 3. packages ----------
step "3/5 packages (pip install -c constraints.txt .)"
STAMP_FILE="$(dirname "$PY")/../.kb-intake-setup"
STAMP="$( (cat constraints.txt pyproject.toml; find src -type f ! -name '*.pyc' | LC_ALL=C sort | xargs cat) | "$PY" -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
if [ -f "$STAMP_FILE" ] && [ "$(cat "$STAMP_FILE")" = "$STAMP" ] && "$PY" -c 'import kb_intake' 2>/dev/null; then
  ok "this source is already installed (skipped)"
else
  echo "   installing (the first time takes a few minutes: torch and Docling are large)"
  LOG="$ROOT/setup-pip.log"
  if ! "$PY" -m pip install --disable-pip-version-check -c constraints.txt . >"$LOG" 2>&1; then
    tail -15 "$LOG" >&2
    fail "pip install failed (full log: $LOG)" "check the package mirror (PIP_INDEX_URL in .env, or your pip.conf), HTTPS_PROXY, and SSL_CERT_FILE / PIP_CERT if the company re-signs TLS. A package missing from the mirror is named in the log"
  fi
  echo "$STAMP" > "$STAMP_FILE"
  ok "installed (log: $LOG)"
fi
KB="$(dirname "$PY")/kb-intake"
[ -x "$KB" ] || fail "kb-intake was not installed into $(dirname "$PY")" "see $ROOT/setup-pip.log"
cat > "$ROOT/kb-intake" <<EOF
#!/usr/bin/env bash
# made by setup.sh: runs kb-intake from its environment ($MODE). Start it in $ROOT so it reads .env.
exec "$KB" "\$@"
EOF
chmod +x "$ROOT/kb-intake"

# ---------- 4. models ----------
step "4/5 models ($INTAKE_MODELS_DIR)"
[ -d "$INTAKE_MODELS_DIR" ] || fail "$INTAKE_MODELS_DIR does not exist" "put the model files there (DEPLOYMENT.md step 2), or set INTAKE_MODELS_DIR in .env"
if "$KB" models verify --dir "$INTAKE_MODELS_DIR" >/dev/null 2>&1; then
  ok "every pinned file present, sha256 match"
else
  echo "   not in the expected layout or incomplete: looking for the pinned files below it (kb-intake models import)"
  if ! out="$("$KB" models import "$INTAKE_MODELS_DIR" --to "$INTAKE_MODELS_DIR" 2>&1)"; then
    printf '%s\n' "$out" | tail -3 >&2
    fail "the model files are incomplete" "add the files named above (each with the page of its pinned commit) to $INTAKE_MODELS_DIR; DEPLOYMENT.md step 2 lists every file with its sha256"
  fi
  ok "put into the expected layout and verified"
fi

# ---------- 5. doctor ----------
step "5/5 kb-intake doctor --smoke"
"$KB" doctor --smoke --report "$ROOT/kb-intake-doctor.json"
rc=$?
if [ $rc -ne 0 ]; then
  fail "kb-intake doctor found a problem (report: $ROOT/kb-intake-doctor.json)" "follow the 'fix:' line under each FAIL above, then run ./setup.sh again"
fi
printf '\nkb-intake is ready.\n  start:  cd %s && ./kb-intake serve      (http://127.0.0.1:8700/)\n  or:     %s\n' "$ROOT" "$RUN_HINT"
