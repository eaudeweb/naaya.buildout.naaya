#!/bin/sh
# Run the Naaya tests for an instance: bin/nytest INSTANCE [zope.testrunner options]
case "$1" in
{cases}
    *) echo "Usage: $0 INSTANCE [zope.testrunner options]" >&2; exit 1 ;;
esac
exec "$PY" {root}/bin/nytest.py "$@"
