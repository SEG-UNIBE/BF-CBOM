#!/bin/sh
set -eu
# Self-signed certificate for "gitserver" (CBOMkit rewrites scan URLs to https://gitserver/...).
# Generated once into the shared /certs volume; worker-cbomkit imports gitserver.crt into its truststore.
if [ ! -s /certs/gitserver.key ] || [ ! -s /certs/gitserver.crt ]; then
  echo "[gitserver] generating self-signed TLS certificate for gitserver"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout /certs/gitserver.key -out /certs/gitserver.crt \
    -subj "/CN=gitserver" -addext "subjectAltName=DNS:gitserver,DNS:localhost" >/dev/null 2>&1
  chmod 0644 /certs/gitserver.crt && chmod 0600 /certs/gitserver.key
fi
# fcgiwrap runs git-http-backend as the unprivileged nginx user (read-only access to /srv/git). Each fcgiwrap
# process serves one request until its response ends, so -c 16 runs 16 of them, more than the workers that clone at
# once: with one (the default) a long CBOMkit full clone made every other worker's clone wait, inside its run time.
spawn-fcgi -s /run/fcgiwrap.sock -M 0666 -u nginx -g nginx -- /usr/bin/fcgiwrap -f -c 16
exec nginx -g 'daemon off;'
