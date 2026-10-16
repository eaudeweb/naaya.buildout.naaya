#!/bin/sh
# Pack the ZEO storage, keeping the last {pack_days} days
exec {venv}/bin/zeopack -d {pack_days} {server} "$@"
