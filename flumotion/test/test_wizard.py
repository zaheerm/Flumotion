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

from twisted.internet import defer
from twisted.spread import jelly

from flumotion.common import worker
from flumotion.common import testsuite

try:
    from flumotion.ui import fgtk
except RuntimeError:
    import os
    os._exit(0)

from flumotion.common import enum
from flumotion.wizard import enums, wizard, step
from flumotion.admin import admin

class WizardStepTest(testsuite.TestCase):
    def setUpClass(self):
        wiz = wizard.Wizard()
        self.steps = wiz.scenario.steps

    def testLoadSteps(self):
        for s in self.steps:
            self.assert_(isinstance(s, step.WizardStep))
            self.assert_(hasattr(s, 'icon'))
            self.assert_(hasattr(s, 'icon'))
            self.assert_(hasattr(s, 'glade_file'))
            self.assert_(hasattr(s, 'name'))
            if s.get_name() == 'Firewire':
                s._queryCallback(dict(height=576, width=720, par=(59,54)))
            self.assert_(isinstance(s.get_state(), dict))
            self.assertEqual(s.name, s.get_name())

            if s.get_name() != 'Summary':
                get_next_ret = s.get_next()
                self.assert_(not get_next_ret or isinstance(get_next_ret, str))
    testLoadSteps.skip = 'Andy, maybe your generator work broke this ?'

    def testStepWidgets(self):
        widgets = [widget for s in self.steps if s.get_name() != 'Firewire'
                              for widget in s.iterate_widgets()]
        for widget in widgets:
            if isinstance(widget, fgtk.FSpinButton):
                self.assert_(isinstance(widget.get_state(), float))
            elif isinstance(widget, (fgtk.FRadioButton,
                                     fgtk.FCheckButton)):
                self.assert_(isinstance(widget.get_state(), bool))
            elif isinstance(widget, fgtk.FEntry):
                self.assert_(isinstance(widget.get_state(), str))
            elif isinstance(widget, fgtk.FComboBox):
                state = widget.get_state()
                if hasattr(widget, 'enum_class'):
                    self.failUnless(isinstance(state, enum.Enum))
                else:
                    # state can be None in the testsuite as well
                    self.failUnless(not state or isinstance(state, int),
                        "state %r is not an instance of int on widget %r" % (
                            state, widget))

    def testStepComponentProperties(self):
        for s in self.steps:
            if s.get_name() == 'Firewire':
                s._queryCallback(dict(height=576, width=720, par=(59,54)))
            self.assert_(isinstance(s.get_component_properties(), dict))
    testStepComponentProperties.skip = 'Andy, maybe your generator work broke this ?'


class TestAdmin(admin.AdminModel):
    def _makeFactory(self, username, password):
        return admin.AdminClientFactory('medium', 'user', 'pass')

    def workerRun(self, worker, module, function, *args, **kwargs):
        success = {('localhost', 'flumotion.worker.checks.video', 'checkTVCard'):
                   {'height': 576, 'width': 720, 'par': (59,54)}}
        failures = {}

        key = (worker, module, function)
        if key in success:
            return defer.succeed(success[key])
        elif key in failures:
            return defer.fail(failures[key])
        else:
            assert False

class WizardSaveTest(testsuite.TestCase):
    def setUp(self):
        self.wizard = wizard.Wizard()
        self.wizard.admin = TestAdmin('user', 'test')
        s = worker.ManagerWorkerHeavenState()
        s.set('names', ['localhost'])
        self.workerHeavenState = jelly.unjelly(jelly.jelly(s))

    def testFirewireAudioAndVideo(self):
        source = self.wizard['Source']
        source.combobox_video.set_active(enums.VideoDevice.Firewire)
        source.combobox_audio.set_active(enums.AudioDevice.Firewire)

        self.wizard['Firewire'].run_checks()
        self.wizard.run(False, self.workerHeavenState, True)

        config = self.wizard.getConfig()
        self.assert_(config.has_key('video-source'))
        self.assert_(not config.has_key('audio-source'))
        videoSource = config['video-source']
        self.failUnlessEqual(videoSource.type, 'firewire')

        self.failUnlessEqual(config['audio-encoder'].getFeeders(), ['video-source:audio'])
        self.failUnlessEqual(config['video-overlay'].getFeeders(), ['video-source:video'])
    testFirewireAudioAndVideo.skip = 'Andy, maybe your generator work broke this ?'

    def testAudioTestWorkers(self):
        source = self.wizard['Source']
        source.combobox_video.set_active(enums.VideoDevice.Webcam)
        source.combobox_audio.set_active(enums.AudioDevice.Test)

        self.wizard.run(False, ['first', 'second'], True)

        self.wizard['Source'].worker = 'second'
        self.wizard['Webcam'].worker = 'second'
        self.wizard['Overlay'].worker = 'second'
        self.wizard['Encoding'].worker = 'second'
        self.wizard['Theora'].worker = 'second'
        self.wizard['Vorbis'].worker = 'second'
        self.wizard['HTTP Streamer (audio & video)'].worker = 'first'

        config = self.wizard.getConfig()
        for item in config.values():
            print item.name, item.worker
        #print self.wizard.printOut()
    testAudioTestWorkers.skip = 'Andy, maybe your generator work broke this ?'
