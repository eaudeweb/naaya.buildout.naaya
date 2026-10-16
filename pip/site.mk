# Shared pip targets for a Naaya site; include it from the site Makefile next to site.cfg
NAAYA_PIP := $(dir $(lastword $(MAKEFILE_LIST)))
PYTHON ?= python3.12
FIND_LINKS ?= https://eggshop.eaudeweb.ro
NAAYA_PIP_CMD = $(PYTHON) $(NAAYA_PIP)naaya_pip.py site.cfg

.PHONY: install requirements packages config haproxy

install: requirements packages config

# One venv per distinct "venv" option of the instances, .venv by default
requirements:
	$(NAAYA_PIP_CMD) requirements

# Old setup.py files need setuptools 81 (pkg_resources), so no build isolation
packages:
	$(NAAYA_PIP_CMD) venvs | while read venv requirements constraints; do \
	    test -x $$venv/bin/python || $(PYTHON) -m venv $$venv || exit 1; \
	    $$venv/bin/pip install -q --upgrade pip setuptools==81.0.0 wheel || exit 1; \
	    $$venv/bin/pip install --no-build-isolation -f $(FIND_LINKS) \
	        -c $$constraints -r $$requirements || exit 1; \
	done

config:
	$(NAAYA_PIP_CMD) config

# haproxy built from source into bin/haproxy; skipped when that version is already there
haproxy:
	set -e; set -- $$($(NAAYA_PIP_CMD) haproxy-version); version=$$1; sha256=$$2; \
	if bin/haproxy -v 2>/dev/null | grep -q "version $$version[- ]"; then exit 0; fi; \
	build=parts/haproxy-build; rm -rf $$build && mkdir -p $$build bin; \
	curl -fsSL -o $$build/haproxy.tar.gz \
	    https://www.haproxy.org/download/$${version%.*}/src/haproxy-$$version.tar.gz; \
	echo "$$sha256  $$build/haproxy.tar.gz" | sha256sum -c -; \
	tar xzf $$build/haproxy.tar.gz -C $$build; \
	make -C $$build/haproxy-$$version -j$$(nproc) TARGET=linux-glibc; \
	install -m 755 $$build/haproxy-$$version/haproxy bin/haproxy
