#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

on_error() {
    local status=$?
    echo "Deployment failed. Recent app logs:" >&2
    docker compose logs --tail=100 app >&2 || true
    exit "$status"
}

trap on_error ERR

cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
    echo "Missing .env. Create it from .env.example and set production values." >&2
    exit 1
fi

git pull --ff-only
docker compose build app
docker compose up -d
docker compose ps
