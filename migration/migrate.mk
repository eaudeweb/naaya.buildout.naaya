# Python 2 to Python 3 Data.fs migration; include it after setting INSTANCE and DATAFS
MIGRATION := $(dir $(lastword $(MAKEFILE_LIST)))

.PHONY: migrate reindex

migrate:
	@echo "=== Migrating $(DATAFS) with $(INSTANCE) ==="
	@MARKER_DIR=$$(dirname "$(DATAFS)"); \
	if [ ! -f "$$MARKER_DIR/.zodbupdate-done" ]; then \
		echo "[Step 1/3] Convert to Python 3 (utf-8, binary attributes kept as bytes)"; \
		printf FS30 | dd of="$(DATAFS)" bs=4 count=1 conv=notrunc status=none && \
		parts/$(INSTANCE)/bin/interpreter $(MIGRATION)py3migration/analyze_py2.py "$(DATAFS)" "$$MARKER_DIR/py2-analysis.json" && \
		parts/$(INSTANCE)/bin/interpreter $(MIGRATION)py3migration/convert_py3.py "$(DATAFS)" "$$MARKER_DIR/py2-analysis.json" && \
		touch "$$MARKER_DIR/.zodbupdate-done"; \
	else \
		echo "[Step 1/3] Convert to Python 3: SKIPPED (already done)"; \
	fi
	@echo "[Step 2/3] fix_provides"
	parts/$(INSTANCE)/bin/interpreter $(MIGRATION)fix_provides.py "$(DATAFS)"
	@echo "[Step 3/3] Zope-level fixes"
	@# pip installs write zope-migrate.conf: Data.fs opened directly, no ZEO and no temporary database
	@if [ -f parts/$(INSTANCE)/etc/zope-migrate.conf ]; then \
	    parts/$(INSTANCE)/bin/interpreter -m Zope2.utilities.zconsole run \
	        parts/$(INSTANCE)/etc/zope-migrate.conf $(MIGRATION)migrate_zodb.py; \
	    exit $$?; \
	fi; \
	ZEO=$$(grep -q '<zeoclient>' parts/$(INSTANCE)/etc/zope.conf && test -x bin/zeoserver && echo yes); \
	if [ -n "$$ZEO" ]; then bin/zeoserver start; fi; \
	bin/$(INSTANCE) run $(MIGRATION)migrate_zodb.py; STATUS=$$?; \
	if [ -n "$$ZEO" ]; then bin/zeoserver stop; fi; \
	exit $$STATUS
	@echo "=== Migration complete ==="

# Rebuild the catalogs of an already migrated Data.fs (e.g. after indexing changes)
reindex:
	parts/$(INSTANCE)/bin/interpreter -m Zope2.utilities.zconsole run \
	    parts/$(INSTANCE)/etc/zope-migrate.conf $(MIGRATION)migrate_zodb.py --reindex-only
