# -*- Mode: Python; test-case-name: flumotion.test.test_wizard -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.


import os
import sets

import gtk
import gtk.gdk
import gtk.glade
from twisted.internet import defer

from flumotion.configure import configure
from flumotion.common import errors, log, pygobject, messages
from flumotion.common.pygobject import gsignal
from flumotion.common.messages import N_, ngettext
from flumotion.wizard import save, classes
from flumotion.wizard.models import Flow
from flumotion.ui import fgtk
from flumotion.ui.glade import GladeWidget, GladeWindow

T_ = messages.gettexter('flumotion')

# pychecker doesn't like the auto-generated widget attrs
# or the extra args we name in callbacks
__pychecker__ = 'no-classattr no-argsused'

def escape(text):
    return text.replace('&', '&amp;')

class Sections(classes.KeyedList):
    def __init__(self, *args):
        classes.KeyedList.__init__(self, *args)
        self.add_key(str, lambda x: x.section)


class WizardStep(GladeWidget, log.Loggable):
    glade_typedict = fgtk.WidgetMapping()

    # set by subclasses
    name = None
    section = None
    icon = 'placeholder.png'

    # optional
    sidebar_name = None
    has_worker = True

    def __init__(self, wizard):
        """
        @param wizard: the wizard this step is a part of
        @type  wizard: L{Wizard}
        """
        self.visited = False
        self.worker = None
        self.wizard = wizard

        GladeWidget.__init__(self)
        self.set_name(self.name)
        if not self.sidebar_name:
            self.sidebar_name = self.name
        self.setup()

    def __repr__(self):
        return '<WizardStep object %r>' % self.name

    # Public API

    def iterate_widgets(self):
        # depth-first
        def iterator(w):
            if isinstance(w, gtk.Container):
                for c in w.get_children():
                    for cc in iterator(c):
                        yield cc
            yield w
        return iterator(self)

    def run_in_worker(self, module, function, *args, **kwargs):
        return self.wizard.run_in_worker(self.worker, module, function,
                                         *args, **kwargs)

    # Required vmethods

    def get_next(self):
        """Called when the user presses next in the wizard.
        @returns: name of next step or next_step instance
        @rtype:  string or an L{WizardStep} instance
        """
        raise NotImplementedError

    # Optional vmethods
    def setup(self):
        """This is called after the step is constructed, to be able to
        do some initalization time logic in the steps."""

    def activated(self):
        """Called just before the step is shown, so the step can
        do some logic, eg setup the default state"""

    def deactivated(self):
        """Called after the user pressed next"""

    def before_show(self):
        """This is called just before we show the widget, everything
        is created and in place"""

    def worker_changed(self):
        pass

    def get_state(self):
        return self._get_widget_states()

    # Private API

    def _get_widget_states(self):
        # returns a new dict. is this necessary?
        state_dict = {}
        for w in self.iterate_widgets():
            if hasattr(w, 'get_state') and w != self:
                # only fgtk widgets implement get_state
                # every widget that implements get_state automatically becomes
                # a property
                # spinbutton_some_property -> some-property
                name = '-'.join(w.get_name().split('_'))
                key = name.split('-', 1)[1]
                state_dict[key] = w.get_state()

        return state_dict


class WizardSection(WizardStep):
    def __init__(self, *args):
        if not self.name:
            self.name = self.section
        WizardStep.__init__(self, *args)

    def __repr__(self):
        return '<WizardSection object %s>' % self.name


class Scenario:
    # to be provided by subclasses
    sections = None

    def __init__(self, wizard):
        self.wizard = wizard # remove?
        self.sidebar = wizard.sidebar
        assert self.sections
        self.sidebar.set_sections([(x.section, x.name) for x in self.sections])
        self.current_section = 0
        self.steps = list(self.sections)
        self.stack = classes.WalkableStack()
        self.current_step = None
        self.sidebar.connect('step-chosen', self.step_selected)

    def add_step(self, step_class):
        # FIXME: remove ref to wiz
        self.steps.append(step_class(self.wizard))

    def step_selected(self, sidebar, name):
        self.stack.skip_to(lambda x: x.name == name)
        step = self.stack.current()
        self.current_step = step
        self.sidebar.show_step(step.section)
        self.current_section = self.sections.index(self.sections[step.section])
        self.wizard.set_step(step)

    def show_previous(self):
        step = self.stack.back()
        self.current_section = self.sections.index(self.sections[step.section])
        self.wizard.set_step(step)
        self.current_step = step
        #self._set_worker_from_step(prev_step)
        self.wizard.update_buttons(has_next=True)
        self.sidebar.show_step(step.section)
        has_next = not hasattr(step, 'last_step')
        self.wizard.update_buttons(has_next)

    def show_next(self):
        self.wizard._setup_worker(self.current_step,
                                  self.wizard.worker_list.get_worker())
        next = self.current_step.get_next()
        if isinstance(next, basestring):
            try:
                next_step = self.wizard.get_step(next)
            except KeyError:
                raise TypeError("%r: Wizard step %s is missing" % (
                    self, next))
        elif isinstance(next, WizardStep):
            next_step = next
            if not next_step in self.steps:
                self.steps.append(next_step)
        elif next is None:
            if self.current_section + 1 == len(self.sections):
                self.wizard.finish(save=True)
                return
            self.current_section += 1
            next_step = self.sections[self.current_section]
        else:
            raise AssertionError

        while not self.stack.push(next_step):
            s = self.stack.pop()
            s.visited = False
            self.sidebar.pop()

        if not next_step.visited:
            self.sidebar.push(next_step.section, next_step.name,
                              next_step.sidebar_name)
        else:
            self.sidebar.show_step(next_step.section)
        next_step.visited = True
        self.wizard.set_step(next_step)
        self.current_step = next_step

        has_next = not hasattr(next_step, 'last_step')
        self.wizard.update_buttons(has_next)

    def run(self, interactive):
        section = self.sections[self.current_section]
        self.sidebar.push(section.section, None, section.section)
        self.stack.push(section)
        self.wizard.set_step(section)
        self.current_step = section

        if not interactive:
            while self.show_next():
                pass
            return self.wizard.finish(False)

        self.wizard.present_and_grab()

        if not self.wizard._use_main:
            return

        try:
            gtk.main()
        except KeyboardInterrupt:
            pass


class BasicScenario(Scenario):
    def __init__(self, wizard):
        from flumotion.wizard import steps
        self.sections = Sections()
        for klass in (steps.WelcomeStep,
                      steps.ProductionStep,
                      steps.ConversionStep,
                      steps.ConsumptionStep,
                      steps.LicenseStep,
                      steps.SummaryStep):
            self.sections.append(klass(wizard))

        Scenario.__init__(self, wizard)

        for k in dir(steps):
            v = getattr(steps, k)
            try:
                if issubclass(v, WizardSection):
                    pass
                elif issubclass(v, WizardStep) and v.name:
                    self.add_step(v)
            except TypeError:
                pass


class Wizard(GladeWindow, log.Loggable):
    gsignal('finished', str)
    gsignal('destroy')

    logCategory = 'wizard'

    flowName = 'default'

    glade_file = 'wizard.glade'

    def __init__(self, parent_window=None, admin=None):
        GladeWindow.__init__(self, parent_window)
        for k, v in self.widgets.items():
            setattr(self, k, v)

        self.flow = Flow("default")
        self.scenario = BasicScenario(self)
        self.window.set_icon_from_file(os.path.join(configure.imagedir,
                                                    'fluendo.png'))
        self.current_step = None
        self._admin = admin
        self._save = save.WizardSaver(self)
        self._use_main = True
        self._workerHeavenState = None
        self._last_worker = 0 # combo id last worker from step to step
        self.worker_list.connect('worker-selected',
                                 self.on_combobox_worker_changed)

        self.window.connect_after('realize', self.on_window_realize)
        self.window.connect('destroy', self.on_window_destroy)

    def __len__(self):
        return len(self.scenario.steps)

    # Public API

    def get_step(self, stepname):
        """Fetches a step. KeyError is raised when the step is not found.
        @param stepname: name of the step to fetch
        @type stepname: str
        @returns: a L{WizardStep} instance or raises KeyError
        """
        for item in self.scenario.steps:
            if item.get_name() == stepname:
                return item
        else:
            raise KeyError(stepname)

    def get_step_option(self, stepname, option):
        state = self.get_step_options(stepname)
        return state[option]

    def get_step_options(self, stepname):
        step = self.get_step(stepname)
        return step.get_state()

    def present_and_grab(self):
        self.window.present()
        self.window.grab_focus()

    def destroy(self):
        GladeWindow.destroy(self)
        del self._admin
        del self._save

    def hide(self):
        self.window.hide()

    def finish(self, main=True, save=True):
        if save:
            configuration = self._save.getXML()
            self.emit('finished', configuration)

        if self._use_main:
            try:
                gtk.main_quit()
            except RuntimeError:
                pass

    def run(self, interactive, workerHeavenState, main=True):
        self._workerHeavenState = workerHeavenState
        self.worker_list.set_worker_heaven_state(self._workerHeavenState)
        self._use_main = main
        self.scenario.run(interactive)

    def clear_msg(self, id):
        self.message_area.clear_message(id)

    def add_msg(self, msg):
        self.message_area.add_message(msg)

    def block_next(self, block):
        self.button_next.set_sensitive(not block)
        # work around a gtk+ bug #56070
        if not block:
            self.button_next.hide()
            self.button_next.show()

    def set_step(self, step):
        # Remove previous step
        map(self.content_area.remove, self.content_area.get_children())
        self.message_area.clear()

        # Add current
        self.content_area.pack_start(step, True, True, 0)

        icon_filename = os.path.join(configure.imagedir, 'wizard', step.icon)
        self.image_icon.set_from_file(icon_filename)

        m = '<span size="x-large">%s</span>' % escape(step.name)
        self.label_title.set_markup(m)

        if self.current_step:
            self.current_step.deactivated()

        self.current_step = step

        self.update_buttons(has_next=True)
        self.block_next(False)

        if step.has_worker:
            self.worker_list.show()
            self.worker_list.notify_selected()
        else:
            self.worker_list.hide()

        self._setup_worker(step, self.worker_list.get_worker())
        step.before_show()

        self.debug('showing step %r' % step)
        step.show()
        step.activated()

    def check_elements(self, workerName, *elementNames):
        """
        Check if the given list of GStreamer elements exist on the given worker.

        @param workerName: name of the worker to check on
        @param elementNames: names of the elements to check

        @returns: a deferred returning a tuple of the missing elements
        """
        if not self._admin:
            self.debug('No admin connected, not checking presence of elements')
            return

        asked = sets.Set(elementNames)
        def _checkElementsCallback(existing, workerName):
            existing = sets.Set(existing)
            self.block_next(False)
            return tuple(asked.difference(existing))

        self.block_next(True)
        d = self._admin.checkElements(workerName, elementNames)
        d.addCallback(_checkElementsCallback, workerName)
        return d

    def require_elements(self, workerName, *elementNames):
        """
        Require that the given list of GStreamer elements exists on the
        given worker. If the elements do not exist, an error message is
        posted and the next button remains blocked.

        @param workerName: name of the worker to check on
        @param elementNames: names of the elements to check
        """
        if not self._admin:
            self.debug('No admin connected, not checking presence of elements')
            return

        self.debug('requiring elements %r' % (elementNames,))
        def got_missing_elements(elements, workerName):
            if elements:
                self.warning('elements %r do not exist' % (elements,))
                f = ngettext("Worker '%s' is missing GStreamer element '%s'.",
                    "Worker '%s' is missing GStreamer elements '%s'.",
                    len(elements))
                message = messages.Error(T_(f, workerName,
                    "', '".join(elements)))
                message.add(T_(N_("\n"
                    "Please install the necessary GStreamer plug-ins that "
                    "provide these elements and restart the worker.")))
                message.add(T_(N_("\n\n"
                    "You will not be able to go forward using this worker.")))
                self.block_next(True)
                message.id = 'element' + '-'.join(elementNames)
                self.add_msg(message)
            return elements

        d = self.check_elements(workerName, *elementNames)
        d.addCallback(got_missing_elements, workerName)

        return d

    def check_import(self, workerName, moduleName):
        """
        Check if the given module can be imported.

        @param workerName:  name of the worker to check on
        @param moduleName:  name of the module to import

        @returns: a deferred returning None or Failure.
        """
        if not self._admin:
            self.debug('No admin connected, not checking presence of elements')
            return

        d = self._admin.checkImport(workerName, moduleName)
        return d


    def require_import(self, workerName, moduleName, projectName=None,
                       projectURL=None):
        """
        Require that the given module can be imported on the given worker.
        If the module cannot be imported, an error message is
        posted and the next button remains blocked.

        @param workerName:  name of the worker to check on
        @param moduleName:  name of the module to import
        @param projectName: name of the module to import
        @param projectURL:  URL of the project
        """
        if not self._admin:
            self.debug('No admin connected, not checking presence of elements')
            return

        self.debug('requiring module %s' % moduleName)
        def _checkImportErrback(failure):
            self.warning('could not import %s', moduleName)
            message = messages.Error(T_(N_(
                "Worker '%s' cannot import module '%s'."),
                workerName, moduleName))
            if projectName:
                message.add(T_(N_("\n"
                    "This module is part of '%s'."), projectName))
            if projectURL:
                message.add(T_(N_("\n"
                    "The project's homepage is %s"), projectURL))
            message.add(T_(N_("\n\n"
                "You will not be able to go forward using this worker.")))
            self.block_next(True)
            message.id = 'module-%s' % moduleName
            self.add_msg(message)

        d = self.check_import(workerName, moduleName)
        d.addErrback(_checkImportErrback)
        return d

    # FIXME: maybe add id here for return messages ?
    def run_in_worker(self, worker, module, function, *args, **kwargs):
        """
        Run the given function and arguments on the selected worker.

        @param worker:
        @param module:
        @param function:
        @returns: L{twisted.internet.defer.Deferred}
        """
        self.debug('run_in_worker(module=%r, function=%r)' % (module, function))
        admin = self._admin
        if not admin:
            self.warning('skipping run_in_worker, no admin')
            return defer.fail(errors.FlumotionError('no admin'))

        if not worker:
            self.warning('skipping run_in_worker, no worker')
            return defer.fail(errors.FlumotionError('no worker'))

        d = admin.workerRun(worker, module, function, *args, **kwargs)

        def callback(result):
            self.debug('run_in_worker callbacked a result')
            self.clear_msg(function)

            if not isinstance(result, messages.Result):
                msg = messages.Error(T_(
                    N_("Internal error: could not run check code on worker.")),
                    debug=('function %r returned a non-Result %r'
                           % (function, result)))
                self.add_msg(msg)
                raise errors.RemoteRunError(function, 'Internal error.')

            for m in result.messages:
                self.debug('showing msg %r' % m)
                self.add_msg(m)

            if result.failed:
                self.debug('... that failed')
                raise errors.RemoteRunFailure(function, 'Result failed')
            self.debug('... that succeeded')
            return result.value

        def errback(failure):
            self.debug('run_in_worker errbacked, showing error msg')
            if failure.check(errors.RemoteRunError):
                debug = failure.value
            else:
                debug = "Failure while running %s.%s:\n%s" % (
                    module, function, failure.getTraceback())

            msg = messages.Error(T_(
                N_("Internal error: could not run check code on worker.")),
                debug=debug)
            self.add_msg(msg)
            raise errors.RemoteRunError(function, 'Internal error.')

        d.addErrback(errback)
        d.addCallback(callback)
        return d

    def update_buttons(self, has_next):
        # update the forward and next buttons
        # has_next: whether or not there is a next step
        if self.scenario.stack.pos == 0:
            self.button_prev.set_sensitive(False)
        else:
            self.button_prev.set_sensitive(True)

        # XXX: Use the current step, not the one on the top of the stack
        if has_next:
            self.button_next.set_label(gtk.STOCK_GO_FORWARD)
        else:
            # use APPLY, just like in gnomemeeting
            self.button_next.set_label(gtk.STOCK_APPLY)

    # Private

    def _setup_worker(self, step, worker):
        # get name of active worker
        self.debug('%r setting worker to %s' % (step, worker))
        step.worker = worker

    def _set_worker_from_step(self, step):
        if not hasattr(step, 'worker'):
            return

        model = self.combobox_worker.get_model()
        current_text = step.worker
        for row in model:
            text = model.get(row.iter, 0)[0]
            if current_text == text:
                self.combobox_worker.set_active_iter(row.iter)
                break

    # Callbacks

    def on_window_realize(self, window):
        # have to get the style from the theme, but it's not really
        # there until it's attached
        style = self.eventbox_top.get_style()
        bg = style.bg[gtk.STATE_SELECTED]
        fg = style.fg[gtk.STATE_SELECTED]
        self.eventbox_top.modify_bg(gtk.STATE_NORMAL, bg)
        self.hbuttonbox2.modify_bg(gtk.STATE_NORMAL, bg)
        self.label_title.modify_fg(gtk.STATE_NORMAL, fg)

    def on_window_destroy(self, window):
        self.emit('destroy')

    def on_window_delete_event(self, wizard, event):
        self.finish(self._use_main, save=False)

    def on_button_prev_clicked(self, button):
        self.scenario.show_previous()

    def on_button_next_clicked(self, button):
        self.scenario.show_next()

    def on_combobox_worker_changed(self, combobox, worker):
        self.debug('combobox_worker_changed, worker %r' % worker)
        if worker:
            self.clear_msg('worker-error')
            self._last_worker = worker
            if self.current_step:
                self._setup_worker(self.current_step, worker)
                self.debug('calling %r.worker_changed' % self.current_step)
                self.current_step.worker_changed()
        else:
            msg = messages.Error(T_(
                    N_('All workers have logged out.\n'
                    'Make sure your Flumotion network is running '
                    'properly and try again.')),
                id='worker-error')
            self.add_msg(msg)


pygobject.type_register(Wizard)
