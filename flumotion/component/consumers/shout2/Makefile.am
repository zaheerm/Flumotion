include $(top_srcdir)/common/python.mk

component_PYTHON = \
	__init__.py \
	shout2.py

componentdir = $(libdir)/flumotion/python/flumotion/component/consumers/shout2
component_DATA = shout2.xml

clean-local:
	rm -rf *.pyc *.pyo

# PYCHECKER_FILES = $(component_PYTHON)

EXTRA_DIST = $(component_PYTHON) $(component_DATA)
