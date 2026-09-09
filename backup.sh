#!/bin/bash
# Консистентный бэкап SQLite (через backup API) + ротация: хранить 14 последних.
set -e
BASE=/opt/beach-volley-coach
DIR=$BASE/data/backups
mkdir -p "$DIR"
TS=$(date +%Y%m%d-%H%M)
OUT="data/backups/coach-$TS.db"
# Консистентная копия через sqlite backup API (переживает WAL); фолбэк — cp.
docker exec beach-volley-coach python -c \
  "import sqlite3; s=sqlite3.connect('data/coach.db'); d=sqlite3.connect('$OUT'); s.backup(d); d.close(); s.close()" \
  || cp "$BASE/data/coach.db" "$DIR/coach-$TS.db"
# Ротация: оставить 14 самых свежих.
ls -1t "$DIR"/coach-*.db 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "backup ok: $TS ($(ls -1 "$DIR"/coach-*.db | wc -l) шт)"
