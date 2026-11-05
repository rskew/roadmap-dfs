#!/bin/sh
# Bring up shop-app's e2e environment inside a replay box (W130.39), from the box's own nix store.
#   sh docs/dfs-shop-app-e2e-up.sh DEST APP [SPEC]
# DEST is the replay clone (the sandbox's CWD), APP the store path of the flake's `.#shop-app` as built on the
# host (`nix build --print-out-paths .#shop-app`; the box store holds it when prepared with `--also shop-app`).
# Starts Postgres on 5442 (devShells.db), builds the schema, starts the backend on 3006, and with SPEC runs it.
# Why the schema steps are not just base_schema + migrate.sh: the baseline file has drifted from the migrations
# (order_item_note and sale_item_note lack `context`, which migrations 0004 and 0020 read), and the views
# and the migrations need each other (0041 makes cash_count_slot but needs the change-log state that
# db-create-views makes; the views' triggers need cash_count_slot). A fresh database is therefore built by:
# baseline, a shim for the two tables, the migrations that can run, the views applied leniently, the migrations
# again, the views strictly.
set -u
D=$1; APP=$2; SPEC=${3:-}
cd "$D" || exit 1
# The sandbox's PATH holds the agent's tools only: take coreutils, psql and the rest from the db shell.
[ -z "${E2E_UP_INSIDE:-}" ] && exec nix develop .#db -c env E2E_UP_INSIDE=1 sh "$(cd "$(dirname "$0")" && pwd)/$(basename "$0")" "$@"
cd "$D" && mkdir -p .run   # the db shell's hook cd'd into db/
unset PGOPTIONS            # the shell's register-db option is the server's, not a client's
export PGHOST=localhost PGPORT=5442 PGDATABASE=postgres PGUSER=postgres PGPASSWORD=asdf
quiet() { grep -v '^error (ignored)\|^ *Reason: \|^ *Output paths\|^ */nix/store\|^warning: unable\|^these .* derivations\|^building '; }
db() { "$@" 2>&1 | quiet; }
up() { bash -c "exec 3<>/dev/tcp/localhost/$1" 2>/dev/null; }

if ! up 5442; then
  mkdir -p db/.db-data
  if [ ! -f db/.db-data/PG_VERSION ]; then
    pw=$(mktemp); printf "%s\n" "$PGPASSWORD" > "$pw"
    (cd db && initdb --locale=C.UTF-8 --user "$PGUSER" --pwfile="$pw") >/dev/null 2>&1
  fi
  (cd db && setsid nohup postgres-wrapper > "$D/.run/postgres.log" 2>&1 &)
  for i in $(seq 1 60); do up 5442 && break; sleep 1; done
fi
up 5442 || { echo "postgres did not start"; exit 1; }

if [ -z "$(db psql -tAc "select to_regclass('public.buying_view')")" ]; then
  db psql -q -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;' >/dev/null
  db psql -v ON_ERROR_STOP=1 -q -f "$D/db/base_schema.sql"
  db psql -q -c "ALTER TABLE order_item_note ADD COLUMN IF NOT EXISTS context TEXT NOT NULL DEFAULT 'ordering'; ALTER TABLE sale_item_note ADD COLUMN IF NOT EXISTS context TEXT NOT NULL DEFAULT 'pricing'; CREATE TABLE IF NOT EXISTS registers_cache (plu integer PRIMARY KEY, name text, category text, unit text, price_cents integer)"
  mig() { for f in db/migrations/*.sql; do db psql --single-transaction -v ON_ERROR_STOP=1 -qX -f "$D/$f" >/dev/null; done; }
  mig
  nix run .#db-create-views -- --emit .run/views.sql 2>&1 | quiet | tail -1
  psql -qX -f .run/views.sql >.run/views.log 2>&1
  mig
  nix run .#db-create-views 2>&1 | grep -v NOTICE | quiet | tail -2
  mig
fi

if ! up 3006; then
  . scripts/dev-env.sh
  cd shop-app && (setsid nohup "$APP/bin/shop-app" > "$D/.run/shop-app.log" 2>&1 &)
  cd "$D"
  for i in $(seq 1 60); do up 3006 && break; sleep 1; done
fi
up 3006 || { echo "backend did not start"; tail -5 .run/shop-app.log; exit 1; }
echo "postgres on 5442 and backend on 3006 are up"
[ -z "$SPEC" ] || (cd shop-app/e2e && nix develop "$D#shop-app-e2e" -c playwright test "$SPEC" --retries=0 --reporter=line 2>&1 | quiet | tail -30)
