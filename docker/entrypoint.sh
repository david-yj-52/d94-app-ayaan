#!/bin/sh
# Runs as root only to fix bind-mount ownership, then drops to ayaan (uid 10001) for good.
set -eu
mkdir -p /home/ayaan /data /workspace
chown -R 10001:10001 /home/ayaan /data
chown 10001:10001 /workspace  # top level only: never recurse into the host's repos
# repos under /workspace may be owned by the host user ("dubious ownership"); replace-all: no dupes on restart
git config --system --replace-all safe.directory '*'
exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
