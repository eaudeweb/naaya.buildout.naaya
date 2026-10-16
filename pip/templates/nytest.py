#!{root}/.venv/bin/python
# Run the Naaya tests for an instance: bin/nytest INSTANCE [zope.testrunner options]
import sys

import naaya_nose

if __name__ == '__main__':
    sys.exit(naaya_nose.main())
