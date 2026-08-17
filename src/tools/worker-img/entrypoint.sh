#!/bin/bash
set -e
if [ -z "${WORKER_PASS:-}" ]; then
  WORKER_PASS=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)
fi
echo "root:${WORKER_PASS}" | chpasswd
mkdir -p /run/sshd
exec /usr/sbin/sshd -D -e
