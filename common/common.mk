check-local: check-local-pychecker

test:
	@make check -C flumotion/test

check-docs:
	@make check -C doc/reference

coverage:
	@trial --temp-directory=_trial_coverage --coverage flumotion.test
	make show-coverage

show-coverage:
	@test ! -z "$(COVERAGE_MODULES)" ||				\
	(echo Define COVERAGE_MODULES in your Makefile.am; exit 1)
	@keep="";							\
	for m in $(COVERAGE_MODULES); do				\
		echo adding $$m;					\
		keep="$$keep `ls _trial_coverage/coverage/$$m*`";	\
	done;								\
	$(PYTHON) common/show-coverage.py $$keep

fixme:
	tools/fixme | less -R

# remove any cache written in distcheck	
dist-hook:
	rm -rf cache

release: dist
	make $(PACKAGE)-$(VERSION).tar.bz2.md5

# generate md5 sum files
%.md5: %
	md5sum $< > $@

# generate a sloc count
sloc:
	sloccount flumotion | grep "(SLOC)" | cut -d = -f 2

.PHONY: test


locale-uninstalled:
	if test -d po; then \
	cd po; \
	make datadir=../$(top_builddir) install; \
	fi

locale-uninstalled-clean:
	@-rm -rf _trial_temp
	@-rm -rf $(top_builddir)/locale
