#!/bin/sh
# Runs before nginx starts: hash the admin password and mint this container's session cookie value.
set -eu
if [ -z "${EDGE_ADMIN_PASSWORD:-}" ]; then
  echo "dpl-edge: EDGE_ADMIN_PASSWORD is not set; refusing to start without admin protection" >&2
  exit 1
fi
user="${EDGE_ADMIN_USER:-admin}"
umask 077
printf '%s:%s\n' "$user" "$(printf '%s' "$EDGE_ADMIN_PASSWORD" | openssl passwd -6 -stdin)" > /etc/nginx/dpl/htpasswd
chown nginx /etc/nginx/dpl/htpasswd
# A new random value per container start: restarting the edge logs everyone out.
token="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
sed -i "s/__EDGE_TOKEN__/$token/g" /etc/nginx/dpl/gate.conf /etc/nginx/dpl/login.conf
