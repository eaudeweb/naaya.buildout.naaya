# Shared pip targets for a Naaya site; include it from the site Makefile next to site.cfg
NAAYA_PIP := $(dir $(lastword $(MAKEFILE_LIST)))
PYTHON ?= python3.12
VERSIONS ?= $(NAAYA_PIP)../zope6-versions.cfg
FIND_LINKS ?= https://eggshop.eaudeweb.ro
PIP := .venv/bin/pip

.PHONY: install venv requirements packages config

install: venv requirements packages config

venv:
	test -x .venv/bin/python || $(PYTHON) -m venv .venv
	$(PIP) install -q --upgrade pip setuptools==81.0.0 wheel

# Packages installed with -e are left out of constraints.txt
requirements:
	SKIP=$$($(PYTHON) $(NAAYA_PIP)naaya_pip.py site.cfg requirements) && \
	$(PYTHON) $(NAAYA_PIP)make_constraints.py $(VERSIONS) $$SKIP > constraints.txt

# Old setup.py files need setuptools 81 (pkg_resources), so no build isolation
packages:
	$(PIP) install --no-build-isolation \
	    -f $(FIND_LINKS) -c constraints.txt -r requirements.txt

config:
	$(PYTHON) $(NAAYA_PIP)naaya_pip.py site.cfg config
