# -*- Mode: Python; test-case-name: flumotion.test.test_wizard -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# flumotion/wizard/wizard.py: the configuration wizard
#
# Flumotion - a streaming media server
# Copyright (C) 2004 Fluendo (www.fluendo.com)

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# See "LICENSE.GPL" in the source distribution for more information.

# This program is also licensed under the Flumotion license.
# See "LICENSE.Flumotion" in the source distribution for more information.

import os

import gobject
import gtk
import gtk.gdk
import gtk.glade

from flumotion.configure import configure
from flumotion.common import log
from flumotion.utils.gstutils import gsignal
from flumotion.wizard import enums, save

def escape(text):
    return text.replace('&', '&amp;')

class Stack(list):
    push = list.append
    def peek(self):
        return self[-1]

class WizardComboBox(gtk.ComboBox):
    COLUMN_NICK = 0
    COLUMN_NAME = 1
    COLUMN_VALUE = 2

    _column_types = str, str, int
    
    def __len__(self):
        model = self.get_model()
        iter = model.get_iter((0,))
        return model.iter_n_children(iter)

    def get_column_content(self, column):
        iter = self.get_active_iter()
        if iter:
            model = self.get_model()
            return model.get(iter, column)[0]
        
    def get_text(self):
        return self.get_column_content(self.COLUMN_NICK)
    
    def get_string(self):
        return self.get_column_content(self.COLUMN_NAME)

    def get_value(self):
        return self.get_column_content(self.COLUMN_VALUE)

    def get_enum(self):
        if hasattr(self, 'enum_class'):
            return self.enum_class.get(self.get_value())
        else:
            return self.get_value()
    
    def set_enum(self, enum_class, value_filter=()):
        model = self.get_model()
        model.clear()
        for enum in enum_class:
            # If values are specified, filter them out
            if value_filter and not enum in value_filter:
                continue
            iter = model.append()
            model.set(iter,
                      self.COLUMN_NAME, enum.name,
                      self.COLUMN_VALUE, enum.value,
                      self.COLUMN_NICK, enum.nick)

        self.set_active(0)
        self.enum_class = enum_class
        
    def set_multi_active(self, *values): 
        if not hasattr(self, 'enum_class'):
            raise TypeError
        
        self.set_enum(self.enum_class, values)
        if len(values) == 1:
            self.set_sensitive(False)
        else:
            self.set_sensitive(True)

    def set_active(self, item):
        """Small wrapper around set_active() to support enums"""
        if isinstance(item, enums.Enum):
            gtk.ComboBox.set_active(self, item.value)
        else:
            gtk.ComboBox.set_active(self, item)
            
    def get_active(self):
        """Small wrapper around get_active() to support enums"""
        value = gtk.ComboBox.get_active(self)
        if hasattr(self, 'enum_class'):
            value = self.enum_class.get(value)
        return value

    def setup(self):
        "This copies the values from an old model to a new"
        old_model = self.get_model()
        
        model = gtk.ListStore(*self._column_types)
        value = 0
        for item in old_model:
            # Get the value from the first column
            name = old_model.get(item.iter, 0)[0]
            iter = model.append()
            model.set(iter,
                      self.COLUMN_NAME, name,
                      self.COLUMN_NICK, name,
                      self.COLUMN_VALUE, value)
            value += 1
        self.set_model(model)
        
    def get_state(self):
        return self.get_enum()
gobject.type_register(WizardComboBox)



class WizardEntry(gtk.Entry):
    def get_state(self):
        return self.get_text()
gobject.type_register(WizardEntry)


    
class WizardCheckButton(gtk.CheckButton):
    def get_state(self):
        return self.get_active()
    
    def __nonzero__(self):
        return self.get_active()
gobject.type_register(WizardCheckButton)



class WizardRadioButton(gtk.RadioButton):
    def get_state(self):
        return self.get_active()

    def __nonzero__(self):
        return self.get_active()
gobject.type_register(WizardRadioButton)



class WizardSpinButton(gtk.SpinButton):
    def get_state(self):
        return self.get_value()
gobject.type_register(WizardSpinButton)



class WidgetMapping:
    # In PyGTK 2.4.0 gtk.glade.XML type_dict parameter is buggy
    # If it can't find the name it raises a silent KeyError which
    # will be raised at random point later (as soon some code call
    # PyErr_Occurred()), to avoid this, we reimplement the function
    # as it is internally, eg failback to the real GType, by doing
    # this PyMapping_GetItemString will never set the error.
    
    types = dict(GtkCheckButton=WizardCheckButton,
                 GtkComboBox=WizardComboBox,
                 GtkEntry=WizardEntry,
                 GtkRadioButton=WizardRadioButton,
                 GtkSpinButton=WizardSpinButton)
    
    def __getitem__(self, name):
        if self.types.has_key(name):
            return self.types[name]
        else:
            return gobject.type_from_name(name)
        

class WizardStep(object, log.Loggable):
    step_name = None # Subclass sets this
    glade_file = None # Subclass sets this
    icon = 'placeholder.png'
    has_worker = True
    widget_prefixes = { WizardComboBox    : 'combobox',
                        WizardCheckButton : 'checkbutton',
                        WizardEntry       : 'entry',
                        WizardSpinButton  : 'spinbutton',
                        WizardRadioButton : 'radiobutton' }

    def __init__(self, wizard):
        self.wizard = wizard
        self.widget = None
        self.visited = False
        
        self.load_glade()

    def __repr__(self):
        return '<WizardStep object %s>' % self.step_name
    
    def load_glade(self):
        glade_filename = os.path.join(configure.gladedir, self.glade_file)
        
        self.wtree = gtk.glade.XML(glade_filename,
                                   typedict=WidgetMapping())
        
        windows = []
        self.widgets = self.wtree.get_widget_prefix('')
        for widget in self.widgets:
            name = widget.get_name()
            if isinstance(widget, gtk.Window):
                widget.hide()
                windows.append(widget)
                continue
            
            if isinstance(widget, WizardComboBox):
                widget.setup()
                widget.set_active(0)
                    
            if hasattr(self, name):
                raise TypeError
            setattr(self, name, widget)

        if len(windows) != 1:
            raise AssertionError("only one window per glade file allowed")

        self.window = windows[0]
        child = self.window.get_children()[0]
        self.window.remove(child)
        self.widget = child

        # And at last, connect signals.
        self.wtree.signal_autoconnect(self)
        
    def get_component_properties(self):
        return self.wizard.get_step_state(self)
    
    def get_main_widget(self):
        return self.widget

    def get_state(self):
        state_dict = {}
        for widget in self.widgets:
            name = widget.get_name()
            prefix = self.widget_prefixes.get(widget.__class__, None)
            if not prefix:
                continue
            key = name.split('_', 1)[1]
            state_dict[key] = widget

        return state_dict

    def get_name(self):
        return self.step_name
    
    def get_sidebar_name(self):
        return getattr(self, 'sidebar_name', self.step_name)
        
    def get_next(self):
        """
        @returns name of next step
        @rtype   string

        This is called when the user presses next in the wizard,
        
        A subclass must implement this"""
        
        raise NotImplementedError

    def activated(self):
        """Called just before the step is shown, so the step can
        do some logic, eg setup the default state

        This can be implemented in a subclass"""
        
    def deactivated(self):
        """Called after the user pressed next

        This can be implemented in a subclass"""

    def setup(self):
        """This is called after the step is constructed, to be able to
        do some initalization time logic in the steps.

        This can be implemented in a subclass."""

        

class Wizard(gobject.GObject):
    sidebar_color = gtk.gdk.color_parse('#9bc6ff')
    main_color = gtk.gdk.color_parse('white')
    sidebar_active_color = gtk.gdk.color_parse('#79abed')
    gsignal('finished', str)
    
    def __init__(self):
        self.__gobject_init__()
        self.wtree = gtk.glade.XML(os.path.join(configure.gladedir, 'wizard.glade'))
        for widget in self.wtree.get_widget_prefix(''):
            setattr(self, widget.get_name(), widget)
        self.wtree.signal_autoconnect(self)
        self.eventbox_top.modify_bg(gtk.STATE_NORMAL, self.sidebar_color)
        self.eventbox_main.modify_bg(gtk.STATE_NORMAL, self.main_color)
        self.window.set_icon_from_file(os.path.join(configure.imagedir,
                                                    'fluendo.png'))

        self._save = save.WizardSaver(self)
        self._use_main = True
        self._workers = []
        self.steps = []
        self.stack = Stack()
        self.current_step = None

    def __getitem__(self, stepname):
        for item in self.steps:
            if item.get_name() == stepname:
                return item
        else:
            raise KeyError

    def __len__(self):
        return len(self.steps)
            
    def get_step_option(self, stepname, option):
        state = self.get_step_options(stepname)
        return state[option]

    def get_step_options(self, stepname):
        step = self[stepname]
        return self.get_step_state(step)
    
    def get_step_state(self, step):
        state = step.get_state()
        dict = {}
        for key, widget in state.items():
            dict[key] = widget.get_state()
        return dict
    
    def block_next(self, block):
        self.button_next.set_sensitive(not block)

    def block_prev(self, block):
        self.button_prev.set_sensitive(not block)

    def add_step(self, step_class, initial=False):
        # If we don't have step_name set, count it as a base class
        name = step_class.step_name
        if name == None:
            return
        
        #if self.steps.has_key(name):
        #    raise TypeError("%s added twice" % name)

        step = step_class(self)
        self.steps.append(step)

        if step.__dict__.has_key('get_state'):
            state = self.get_step_state(step)
            assert type(state) == dict
            assert state, state
        
        step.setup()

        if initial:
            self.stack.push(step)

    def set_step(self, step):
        # Remove previous step
        map(self.content_area.remove, self.content_area.get_children())

        # Add current
        widget = step.get_main_widget()
        self.content_area.add(widget)
        self.content_area.set_child_packing(widget, False, False, 0, 0)

        self._append_workers(step)
        icon_filename = os.path.join(configure.imagedir, 'wizard', step.icon)
        self.image_icon.set_from_file(icon_filename)
            
        self.label_title.set_markup('<span size="x-large">' + escape(step.get_name()) + '</span>')

        if self.current_step:
            self.current_step.deactivated()

        self.current_step = step
        
        self.update_sidebar(step)
        self.update_buttons(has_next=True)
        
        # Finally show
        widget.show()
        step.activated()

    def _append_workers(self, step):
        if not step.has_worker:
            self.combobox_worker = None
            return
        
        # Horizontal, under step
        hbox = gtk.HBox()
        self.content_area.pack_end(hbox, False, False)
        hbox.set_border_width(6)
        hbox.show()
        
        frame = gtk.Frame('Worker')
        hbox.pack_end(frame, False, False, 0)
        frame.show()

        # Internal, so we can add border width
        box = gtk.HBox()
        frame.add(box)
        box.set_border_width(6)
        box.show()

        self.combobox_worker = gtk.combo_box_new_text()
        box.pack_start(self.combobox_worker, False, False, 6)
        map(self.combobox_worker.append_text, self._workers)
            
        self.combobox_worker.set_active(0)
        self.combobox_worker.show()
        
    def show_previous(self):
        self.stack.pop()
        prev_step = self.stack.peek()
        self.set_step(prev_step)

        self.update_buttons(has_next=True)

    def show_next(self):
        next = self.current_step.get_next()
        if not next:
            self.finish()
            return

        try:
            next_step = self[next]
        except KeyError:
            raise TypeError("Wizard step %s is missing" % `next`)

        # get name of active worker
        if self.combobox_worker:
            iter = self.combobox_worker.get_active_iter()
            model = self.combobox_worker.get_model()
            text = model.get(iter, 0)[0]
            self.current_step.worker = text
        
        next_step.visited = True

        self.stack.push(next_step)
        self.set_step(next_step)

        self.update_buttons(next)

    def update_buttons(self, has_next):
        if len(self.stack) == 1:
            self.button_prev.set_sensitive(False)
        else:
            self.button_prev.set_sensitive(True)

        # XXX: Use the current step, not the one on the top of the stack
        if has_next:
            self.button_next.set_label(gtk.STOCK_GO_FORWARD)
        else:
            self.button_next.set_label(gtk.STOCK_QUIT)

    def _sidebar_clean(self):
        # First remove the old the VBox if we can find one
        parent = self.vbox_sidebar.get_parent()
        if parent:
            parent.remove(self.vbox_sidebar)
        else:
            parent = self.eventbox_sidebar

        parent.modify_bg(gtk.STATE_NORMAL, self.sidebar_color)
        self.vbox_sidebar = gtk.VBox()
        self.vbox_sidebar.set_border_width(5)
        self.vbox_sidebar.set_size_request(180, -1)
        parent.add(self.vbox_sidebar)

    def _sidebar_add_placeholder(self):
        # Placeholder label, which expands vertically
        ph = gtk.Label()
        ph.show()
        self.vbox_sidebar.pack_start(ph)
        
    def _sidebar_add_step(self, step, name, active, padding):
        hbox = gtk.HBox(0, False)
        hbox.show()

        text = escape(name)
        button = gtk.Button('')
        button.modify_bg(gtk.STATE_PRELIGHT, self.sidebar_active_color)
        button.modify_bg(gtk.STATE_ACTIVE, self.sidebar_active_color)

        label = button.get_children()[0]
        label.set_padding(padding, 0)
        label.set_alignment(0, 0.5)
        button.set_relief(gtk.RELIEF_NONE)
        hbox.pack_start(button, True, True)
        self.vbox_sidebar.pack_start(hbox, False, False)

        if not step:
            steps = [step for step in self.steps
                              if getattr(step, 'section_name', '') == name]
            assert len(steps) == 1
            step = steps[0]

        def button_clicked_cb(button, step):
            self.set_step(step)
            
        if step:
            button.connect('clicked', button_clicked_cb, step)
        else:
            button.connect('clicked', button_clicked_cb, steps[0])
            
        current = self.current_step
        if current == step:
            button.set_sensitive(False)
            
        if not active and not step.visited:
            markup = '<span color="#7a7a7a">%s</span>' % name
            button.set_sensitive(False)
        else:
            markup = '<span color="black">%s</span>' % (name)
            button.set_property('can_focus', False)
            
        label.set_markup(markup)

        button.show()
        return button
    
    def _sidebar_add_substeps(self, section):
        filtered_steps = [step for step in self.steps
                                   if (step.section == section and
                                       step.visited == True and
                                       not hasattr(step, 'section_name'))]
        for step in filtered_steps:
            label = step.get_sidebar_name()
            self._sidebar_add_step(step, label, True, 20)

    def update_sidebar(self, step):
        current = step.section

        self._sidebar_clean()
        
        sidebar_steps = ('Welcome',
                         'Production', 'Conversion',
                         'Consumption', 'License')
        active = True
        for stepname in sidebar_steps:
            self._sidebar_add_step(None, stepname, active, 10)

            if current == stepname:
                self._sidebar_add_substeps(stepname)
                self._sidebar_add_placeholder()
                active = False
            else:
                continue
            
        self.vbox_sidebar.show()
        
    def on_wizard_delete_event(self, wizard, event):
        self.finish(self._use_main)

    def on_button_prev_clicked(self, button):
        self.show_previous()

    def on_button_next_clicked(self, button):
        self.show_next()

    def finish(self, main=True):
        configuration = self._save.getXML()
        self.emit('finished', configuration)
        
        if self._use_main:
            try:
                gtk.main_quit()
            except RuntimeError:
                pass

    def hide(self):
        self.window.hide()

    def run(self, interactive, workers=[], main=True):
        self._workers = workers
        self._use_main = main
        
        if not self.stack:
            raise TypeError("need an initial step")

        self.set_step(self.stack.peek())
        
        if not interactive:
            while self.current_step.get_next():
                self.show_next()
                
            return self.finish(False)

        self.window.show()
        if not self._use_main:
            return
        
        try:
            gtk.main()
        except KeyboardInterrupt:
            pass

    def load_steps(self):
        # XXX: This is really ugly
        globals()['wiz'] = self
        import flumotion.wizard.steps

    def printOut(self):
        print self._save.getXML()[:-1]
        
gobject.type_register(Wizard)

        
wiz = None
def register_step(klass):
    global wiz

    if not len(wiz):
        wiz.add_step(klass, initial=True)
    else:
        wiz.add_step(klass)
