# -*- Mode: Python; fill-column: 80 -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005 Fluendo, S.L. (www.fluendo.com). All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with th
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.


import gtk.glade

from flumotion.configure import configure
from flumotion.admin.gtk import wizard, connections


# A wizard run when the user first starts flumotion.


# personal note: these things duplicate to a large extent the code in
# flumotion.wizard.steps. A bit irritating to find that out after
# hacking on it for a bit.


# Page classes (see wizard.py for details)

class Initial(wizard.WizardStep):
    name = 'initial'
    title = 'Connect to Flumotion manager'
    text = 'Flumotion Admin needs to connect to a Flumotion manager.\nChoose' \
           + ' an option from the list and click "Forward" to begin.'
    connect_to_existing = None
    next_pages = ['load_connection', 'connect_to_existing']

    def on_next(self, state):
        radio_buttons = self.connect_to_existing.get_group()

        for i in range(len(radio_buttons)):
            if radio_buttons[i].get_active():
                return radio_buttons[i].get_name()
        raise AssertionError
    
    def setup(self, state, available_pages):
        for wname in self.next_pages:
            getattr(self,wname).set_sensitive(wname in available_pages)
        getattr(self,available_pages[0]).set_active(True)


class ConnectToExisting(wizard.WizardStep):
    name='connect_to_existing'
    title='Host information'
    text = 'Please enter the address where the manager is running.'
    host_entry = port_entry = ssl_check = None
    next_pages = ['authenticate']

    def setup(self, state, available_pages):
        self.on_entries_changed()
        self.host_entry.grab_focus()

    def on_entries_changed(self, *args):
        if self.host_entry.get_text() and self.port_entry.get_text():
            self.button_next.set_sensitive(True)
        else:
            self.button_next.set_sensitive(False)

    def on_ssl_check_toggled(self, button):
        if button.get_active():
            self.port_entry.set_text('7531')
        else:
            self.port_entry.set_text('8642')

    def on_next(self, state):
        host = self.host_entry.get_text()
        port = self.port_entry.get_text()
        ssl_check = self.ssl_check.get_active()

        # fixme: check these values here
        state['host'] = host
        state['port'] = int(port)
        state['use_insecure'] = not ssl_check

        return 'authenticate'


class Authenticate(wizard.WizardStep):
    name = 'authenticate'
    title = 'Authentication'
    text = 'Please select among the following authentication methods.'
    auth_method_combo = user_entry = passwd_entry = None
    next_pages = []

    def setup(self, state, available_pages):
        if not 'auth_method' in state:
            self.auth_method_combo.set_active(0)
        self.on_entries_changed()
        self.user_entry.grab_focus()
        self.user_entry.connect('activate',
                                lambda *x: self.passwd_entry.grab_focus())

    def on_entries_changed(self, *args):
        if self.user_entry.get_text() and self.passwd_entry.get_text():
            self.button_next.set_sensitive(True)
        else:
            self.button_next.set_sensitive(False)

    def on_next(self, state):
        user = self.user_entry.get_text()
        passwd = self.passwd_entry.get_text()

        # fixme: check these values here
        state['user'] = user
        state['passwd'] = passwd

        return '*finished*'


class LoadConnection(wizard.WizardStep):
    name = 'load_connection'
    title = 'Load connection'
    text = 'Please choose a connection from the box below.'
    connections = None
    next_pages = []

    def __init__(self, *args):
        def cust_handler(xml, proc, name, *args):
            w = eval(proc)
            w.set_name(name)
            w.show()
            return w
        gtk.glade.set_custom_handler(cust_handler)
        wizard.WizardStep.__init__(self, *args)

    def is_available(self):
        return self.connections.get_selected()

    def on_has_selection(self, widget, has_selection):
        self.button_next.set_sensitive(has_selection)

    def on_connection_activated(self, widget, state):
        self.button_next.emit('clicked')

    def on_next(self, state):
        state.update(self.connections.get_selected())
        return '*finished*'

    def setup(self, state, available_pages):
        self.connections.grab_focus()


class Greeter:
    wiz = None
    def __init__(self):
        self.wiz = wizard.Wizard('greeter', 'initial',
                                 Initial, ConnectToExisting, Authenticate,
                                 LoadConnection)
    def run(self):
        self.wiz.show()
        return self.wiz.run()

    def destroy(self):
        return self.wiz.destroy()

    def hide(self):
        return self.wiz.hide()

    def show(self):
        return self.wiz.show()

    def set_sensitive(self, is_sensitive):
        return self.wiz.set_sensitive(is_sensitive)

