#!/usr/bin/env bash
# Run sqitch against the database described by DB_* vars in .env
# (keeps host and secrets out of sqitch.conf).
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
export SQITCH_TARGET="db:pg://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
export SQITCH_PASSWORD="$DB_PASSWORD"
echo "Running sqitch against $SQITCH_TARGET"
exec sqitch "$@"
