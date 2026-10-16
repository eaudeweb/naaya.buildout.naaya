#!/bin/sh
# ZEO server: start|stop|restart|status|fg
VENV={venv}/bin
CONF={etc}/zeo.conf
SOCKET={root}/var/zeoserver.zdsock
case "$1" in
    fg)
        exec "$VENV/runzeo" -C "$CONF" ;;
    start|stop|restart|status|kill)
        exec "$VENV/zdaemon" -d -s "$SOCKET" -p "$VENV/runzeo -C $CONF" "$@" ;;
    *)
        echo "Usage: $0 start|stop|restart|status|fg" >&2
        exit 1 ;;
esac
