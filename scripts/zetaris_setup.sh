#!/usr/bin/env bash
# One-command local Zetaris for Because.
#
#   ./scripts/zetaris_setup.sh ~/Downloads/zetaris-platform.zip     # first time
#   ./scripts/zetaris_setup.sh                                      # re-run: start + register again
#   ./scripts/zetaris_setup.sh --down                               # stop containers (keeps data)
#
# What it does (following zetaris.github.io/getting-started "Install locally"):
#   1. checks Docker + Compose, a JDK 11+ and Python 3.10+
#   2. unzips the zetaris-platform bundle (from cloud.enterprise.zetaris.com/dashboard/download)
#   3. docker login zetarishackathon.azurecr.io (asks for the registry credentials Zetaris issued, once)
#   4. sets the admin email/password in the bundle's .env if empty, then pull + up + verify
#   5. finds the bundled JDBC driver JAR, writes .env.local for Because (gitignored)
#   6. installs the JDBC extra (jaydebeapi + JPype) into .venv
#   7. `because zetaris ping` (SELECT 1 + SHOW LIGHTNING DATABASES) and `because zetaris register`
#      (CREATE LIGHTNING REST TABLE + SCHEMASTORE VIEW for every source the demo questions use)
# Nothing here prints a password. Requires ~8 GB RAM for Docker and ~15 GB disk.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM_DIR="${ZETARIS_PLATFORM_DIR:-$ROOT/zetaris-platform}"
REGISTRY="zetarishackathon.azurecr.io"
say() { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
die() { printf "\033[1;31mERROR:\033[0m %s\n" "$*" >&2; exit 1; }

if [ "${1:-}" = "--down" ]; then
  (cd "$PLATFORM_DIR" && docker compose stop); exit 0
fi

# 1. prerequisites ---------------------------------------------------------------
command -v docker >/dev/null || die "Docker not found. Install Docker Desktop (macOS/Windows) or Docker Engine + Compose (Linux)."
docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker Desktop and re-run."
docker compose version >/dev/null 2>&1 || die "The 'docker compose' plugin is missing."
if [ -z "${JAVA_HOME:-}" ] && [ -x /usr/libexec/java_home ]; then JAVA_HOME="$(/usr/libexec/java_home 2>/dev/null || true)"; fi
for brew_jdk in /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home; do
  [ -z "${JAVA_HOME:-}" ] && [ -x "$brew_jdk/bin/java" ] && JAVA_HOME="$brew_jdk"
done
JAVA_BIN="${JAVA_HOME:+$JAVA_HOME/bin/}java"
"$JAVA_BIN" -version >/dev/null 2>&1 || die "No JDK found (needed for the JDBC driver). macOS: brew install openjdk@17 ; Ubuntu: sudo apt install openjdk-17-jdk"
export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")}"
PY="$(command -v python3 || true)"; [ -n "$PY" ] || die "python3 not found."

# 2. bundle ----------------------------------------------------------------------
if [ ! -f "$PLATFORM_DIR/docker-compose.yml" ]; then
  ZIP="${1:-$HOME/Downloads/zetaris-platform.zip}"
  [ -f "$ZIP" ] || die "Bundle not found. Download zetaris-platform.zip from https://cloud.enterprise.zetaris.com/dashboard/download and pass its path."
  say "Unzipping $ZIP"
  TMP="$(mktemp -d)"; unzip -q "$ZIP" -d "$TMP"
  SRC="$(dirname "$(find "$TMP" -maxdepth 3 -name docker-compose.yml | head -1)")"
  [ -n "$SRC" ] || die "docker-compose.yml not found inside the zip."
  mv "$SRC" "$PLATFORM_DIR"
fi
cd "$PLATFORM_DIR"

# 3. registry login ----------------------------------------------------------------
if ! grep -q "$REGISTRY" "$HOME/.docker/config.json" 2>/dev/null; then
  say "Log in to $REGISTRY with the registry credentials Zetaris issued (stored in your vault)."
  docker login "$REGISTRY"
fi

# 4. configure + start --------------------------------------------------------------
[ -f .env ] || die "The bundle has no .env file; check the download."
getenv() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
setenv() { if grep -qE "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
if [ -z "$(getenv ZETARIS_ADMIN_EMAIL)" ]; then read -r -p "Admin email for your local Zetaris: " v; setenv ZETARIS_ADMIN_EMAIL "$v"; fi
if [ -z "$(getenv ZETARIS_ADMIN_PASSWORD)" ]; then
  echo "Admin password: 8+ chars with lower, upper, digit and one of \$ @ ! % * ? & (no other punctuation)."
  read -r -s -p "Admin password: " v; echo; setenv ZETARIS_ADMIN_PASSWORD "$v"
fi
[ -z "$(getenv ZETARIS_ADMIN_ORG)" ] && setenv ZETARIS_ADMIN_ORG because
rm -f .env.bak; chmod 600 .env
[ -x ./preflight.sh ] && { ./preflight.sh || say "preflight reported warnings (continuing; docker compose pull is the definitive check)"; }
docker compose config --quiet
say "Pulling images (~4 GB the first time) and starting Zetaris"
docker compose pull
docker compose up -d
say "Waiting for Zetaris to be ready (first start can take 5-15 minutes)"
for i in $(seq 1 60); do
  if [ -x ./verify.sh ] && ./verify.sh >/tmp/zetaris-verify.log 2>&1; then say "verify.sh passed"; break; fi
  sleep 20
  [ "$i" = 60 ] && { tail -20 /tmp/zetaris-verify.log; die "Zetaris did not become ready. Check: docker compose ps --all; docker compose logs --tail=80 driver api bootstrap"; }
done

# 5. JDBC driver + Because env --------------------------------------------------------
JAR="$(find "$PLATFORM_DIR" -name '*.jar' -path '*jdbc*' | grep -i driver | head -1)"
[ -n "$JAR" ] || die "Could not find the Zetaris JDBC driver JAR under $PLATFORM_DIR (expected jdbc/ndp-jdbc-driver-*-driver.jar)."
cd "$ROOT"
touch .env.local; chmod 600 .env.local
upsert() { if grep -qE "^$1=" .env.local; then
    "$PY" - "$1" "$2" <<'PY'
import sys, re, pathlib
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env.local"); s = p.read_text()
p.write_text(re.sub(rf"(?m)^{re.escape(k)}=.*$", f'{k}="{v}"', s))
PY
  else printf '%s="%s"\n' "$1" "$2" >> .env.local; fi; }
upsert BECAUSE_BACKEND zetaris
upsert ZETARIS_CHANNEL jdbc
upsert ZETARIS_JDBC_URL "${ZETARIS_JDBC_URL:-jdbc:zetaris:lightning@localhost:10000}"
upsert ZETARIS_JDBC_JAR "$JAR"
upsert ZETARIS_USERNAME "$(cd "$PLATFORM_DIR" && getenv ZETARIS_ADMIN_EMAIL)"
upsert ZETARIS_PASSWORD "$(cd "$PLATFORM_DIR" && getenv ZETARIS_ADMIN_PASSWORD)"
upsert JAVA_HOME "$JAVA_HOME"
grep -q '^BECAUSE_USER_AGENT=' .env.local || upsert BECAUSE_USER_AGENT "because-agent $(cd "$PLATFORM_DIR" && getenv ZETARIS_ADMIN_EMAIL)"
say "Wrote .env.local (gitignored) with the JDBC settings"

# 6. python deps ------------------------------------------------------------------------
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -e ".[zetaris,dev]"

# 7. connect + register sources -------------------------------------------------------------
say "Checking the JDBC connection (SELECT 1, SHOW LIGHTNING DATABASES)"
.venv/bin/because zetaris ping
say "Registering the demo sources as Lightning REST tables + SchemaStore views"
.venv/bin/because zetaris register
say "Done. Start the UI with:  .venv/bin/because serve   (backend: Zetaris)"
