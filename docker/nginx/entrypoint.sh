#!/bin/bash

mkdir -p /etc/nginx/ssl

if [ ! -e "/etc/nginx/ssl/cert.pem" ] || [ ! -e "/etc/nginx/ssl/key.pem" ]
then
  echo ">> generating self signed cert"
  openssl req -x509 -newkey rsa:2048 \
  -subj "/C=XX/ST=XXXX/L=XXXX/O=XXXX/CN=api-local.changemyworldnow.com" \
  -keyout "/etc/nginx/ssl/key.pem" \
  -out "/etc/nginx/ssl/cert.crt" \
  -days 3650 -nodes -sha256
fi

# exec CMD
echo ">> exec docker CMD"
echo "$@"
exec "$@"