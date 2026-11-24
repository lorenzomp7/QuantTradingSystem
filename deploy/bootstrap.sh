#!/usr/bin/env bash
# Prepara la VM Oracle Cloud (Always Free) a ospitare i backend: Docker, porte 80/443
# aperte nel firewall della VM e un Caddy condiviso che fa da HTTPS per tutte le app.
# È idempotente: il workflow di deploy lo esegue a ogni rilascio, la seconda volta non cambia nulla.
# Testato su Ubuntu 22.04/24.04 (immagini Oracle, utente "ubuntu").
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Installo Docker…"
  curl -fsSL https://get.docker.com | sudo sh
fi

# Le immagini Ubuntu di Oracle bloccano tutto tranne SSH con iptables:
# oltre alla Security List della VCN servono queste regole sulla VM.
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
  fi
done
if ! sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 1 -p udp --dport 443 -j ACCEPT
fi
if command -v netfilter-persistent >/dev/null 2>&1; then
  sudo netfilter-persistent save >/dev/null
fi

# Rete Docker condivisa: Caddy raggiunge ogni app per nome del container.
sudo docker network inspect web >/dev/null 2>&1 || sudo docker network create web >/dev/null

# Caddy: ogni app aggiunge il suo file in /opt/caddy/sites/<app>.caddy
sudo mkdir -p /opt/caddy/sites
if [ ! -f /opt/caddy/Caddyfile ]; then
  echo 'import /etc/caddy/sites/*.caddy' | sudo tee /opt/caddy/Caddyfile >/dev/null
fi
if [ ! -f /opt/caddy/sites/_vuoto.caddy ]; then
  echo '# segnaposto: i siti veri sono negli altri file di questa cartella' | sudo tee /opt/caddy/sites/_vuoto.caddy >/dev/null
fi
if [ -z "$(sudo docker ps -q --filter name=^caddy$)" ]; then
  sudo docker rm -f caddy >/dev/null 2>&1 || true
  sudo docker run -d --name caddy --restart unless-stopped --network web \
    -p 80:80 -p 443:443 -p 443:443/udp \
    -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
    -v /opt/caddy/sites:/etc/caddy/sites:ro \
    -v caddy_data:/data -v caddy_config:/config \
    caddy:2 >/dev/null
fi
echo "VM pronta."
