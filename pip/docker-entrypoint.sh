#!/bin/sh
# Write parts/ and bin/ from the mounted secret.cfg and local.cfg, then run the command
set -e
make -s config > /dev/null
exec "$@"
