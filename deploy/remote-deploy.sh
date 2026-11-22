#!/usr/bin/env bash
# Eseguito sulla VM dal workflow "Deploy backend": prepara la VM (se serve),
# ricostruisce e avvia i container dell'app e pubblica il suo indirizzo HTTPS su Caddy.
# Uso: bash deploy/remote-deploy.sh <nome-app>
set -euo pipefail
APP="$1"
DIR="/opt/apps/$APP"

bash "$DIR/deploy/bootstrap.sh"
cd "$DIR"
sudo docker compose -p "$APP" -f deploy/compose.yml --env-file deploy/.env up -d --build --remove-orphans
sudo install -m 644 deploy/site.caddy "/opt/caddy/sites/$APP.caddy"
sudo docker exec caddy caddy reload --config /etc/caddy/Caddyfile
sudo docker image prune -f >/dev/null
sudo docker compose -p "$APP" -f deploy/compose.yml ps
