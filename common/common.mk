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


locale-uninstalled-1:
	if test -d po; then \
	cd po; \
	make datadir=../$(top_builddir) itlocaledir=../$(top_builddir)/locale install; \
	fi

# the locale-uninstalled rule can be replaced with the following lines, 
# once we can depend on a newer intltool than 0.34.2
# 	if test -d po; then \
# 	cd po; \
# 	make datadir=../$(top_builddir) itlocaledir=../$(top_builddir)/locale install; \
# 	fi

locale-uninstalled:
	if test -d po; then \
	mkdir -p $(top_builddir)/locale; \
	cd po; \
	make; \
	for file in $$(ls $(srcdir)/po/*.gmo); do \
	  lang=`echo $$file|cut -d/ -f3|cut -d. -f1`; \
	  dir=../$(top_builddir)/locale/$$lang/LC_MESSAGES; \
	  mkdir -p $$dir; \
	  if test -r $$lang.gmo; then \
	    install $$lang.gmo $$dir/$(GETTEXT_PACKAGE).mo; \
	    echo "installing $$lang.gmo as $$dir/$(GETTEXT_PACKAGE).mo"; \
	  else \
	    install $(srcdir)/$$lang.gmo $$dir/$(GETTEXT_PACKAGE).mo; \
	    echo "installing $(srcdir)/$$lang.gmo as" \
		 "$$dir/$(GETTEXT_PACKAGE).mo"; \
	  fi; \
	done; \
	fi

locale-uninstalled-clean:
	@-rm -rf _trial_temp
	@-rm -rf $(top_builddir)/locale
