#!/bin/sh
# Zope instance {name}: start|stop|restart|status|fg|debug|run SCRIPT [ARGS]
VENV={venv}/bin
ETC={etc}
SOCKET={root}/var/{name}.zdsock
case "$1" in
    fg)
        exec "$VENV/runwsgi" "$ETC/wsgi.ini" ;;
    debug)
        exec "$VENV/zconsole" debug "$ETC/zope.conf" ;;
    run)
        shift
        exec "$VENV/zconsole" run "$ETC/zope.conf" "$@" ;;
    start|stop|restart|status|kill)
        exec "$VENV/zdaemon" -d -s "$SOCKET" -p "$VENV/runwsgi $ETC/wsgi.ini" "$@" ;;
    *)
        echo "Usage: $0 start|stop|restart|status|fg|debug|run SCRIPT [ARGS]" >&2
        exit 1 ;;
esac
