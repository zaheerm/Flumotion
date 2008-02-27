# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2008 Fluendo, S.L. (www.fluendo.com).
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

import gettext
import os
import math

from flumotion.common import errors, messages
from flumotion.common.messages import N_, gettexter
from flumotion.wizard.basesteps import AudioSourceStep, VideoSourceStep
from flumotion.wizard.models import AudioProducer, VideoProducer

__pychecker__ = 'no-returnvalues'
__version__ = "$Rev$"
_ = gettext.gettext
T_ = gettexter('flumotion')


class FireWireAudioProducer(AudioProducer):
    component_type = 'firewire-producer'
    def __init__(self):
        super(FireWireAudioProducer, self).__init__()

        self.properties.is_square = False

    def getFeeders(self):
        for feeder in super(FireWireAudioProducer, self).getFeeders():
            yield feeder + ':audio'


class FireWireVideoProducer(VideoProducer):
    component_type = 'firewire-producer'
    def __init__(self):
        super(FireWireVideoProducer, self).__init__()

        self.properties.is_square = False

    def getFeeders(self):
        for feeder in super(FireWireAudioProducer, self).getFeeders():
            yield feeder + ':video'


class _FireWireCommon:
    glade_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'firewire-wizard.glade')
    component_type = 'firewire'
    icon = 'firewire.png'
    width_corrections = ['none', 'pad', 'stretch']

    def __init__(self):
        # options detected from the device:
        self._dims = None
        self._factors = [1, 2, 3, 4, 6, 8]
        self._input_heights = None
        self._input_widths = None
        self._par = None

        # these are instance state variables:
        self._factor_i = None             # index into self.factors
        self._width_correction = None     # currently chosen item from
                                          # width_corrections

    # WizardStep

    def worker_changed(self, worker):
        self.model.worker = worker
        self._run_checks()

    # Private

    def _set_sensitive(self, is_sensitive):
        self.vbox_controls.set_sensitive(is_sensitive)
        self.wizard.block_next(not is_sensitive)

    def _update_output_format(self):
        d = self._get_width_height()
        self.model.properties.is_square = (
            self.checkbutton_square_pixels.get_active())
        self.model.properties.width = d['ow']
        self.model.properties.height = d['oh']
        self.model.properties.scaled_width = d['sw']
        self.model.properties.framerate = self.spinbutton_framerate.get_value()
        num, den = 1, 1
        if not self.model.properties.is_square:
            num, den = self._par[0], self._par[1]

        msg = _('%dx%d, %d/%d pixel aspect ratio') % (
                   d['ow'], d['oh'], num, den)
        self.label_output_format.set_markup(msg)

    def _get_width_height(self):
        # returns dict with sw, sh, ow, oh
        # which are scaled width and height, and output width and height
        sh = self._input_heights[self._factor_i]
        sw = self._input_widths[self._factor_i]
        par = 1. * self._par[0] / self._par[1]

        if self.model.properties.is_square:
            sw = int(math.ceil(sw * par))
            # for GStreamer element sanity, make sw an even number
            # FIXME: check if this can now be removed
            # sw = sw + (2 - (sw % 2)) % 2

        # if scaled width (after squaring) is not multiple of 8, present
        # width correction
        self.frame_width_correction.set_sensitive(sw % 8 != 0)

        # actual output
        ow = sw
        oh = sh
        if self._width_correction == 'pad':
            ow = sw + (8 - (sw % 8)) % 8
        elif self._width_correction == 'stretch':
            ow = sw + (8 - (sw % 8)) % 8
            sw = ow

        return dict(sw=sw,sh=sh,ow=ow,oh=oh)

    def _run_checks(self):
        self._set_sensitive(False)
        msg = messages.Info(T_(N_('Checking for Firewire device...')),
            id='firewire-check')
        self.wizard.add_msg(msg)
        d = self.run_in_worker('flumotion.worker.checks.video', 'check1394',
            id='firewire-check')

        def firewireCheckDone(options):
            self.wizard.clear_msg('firewire-check')
            self._dims = (options['width'], options['height'])
            self._par = options['par']
            self._input_heights = [self._dims[1]/i for i in self._factors]
            self._input_widths = [self._dims[0]/i for i in self._factors]
            values = []
            for i, height in enumerate(self._input_heights):
                values.append(('%d pixels' % height, i))
            self.combobox_scaled_height.prefill(values)
            self._set_sensitive(True)
            self.on_update_output_format()

        def trapRemote(failure):
            failure.trap(errors.RemoteRunError)
        d.addCallback(firewireCheckDone)
        d.addErrback(trapRemote)
        return d

    # Callbacks

    def on_update_output_format(self, *args):
        # update label_camera_settings
        standard = 'Unknown'
        aspect = 'Unknown'
        h = self._dims[1]
        if h == 576:
            standard = 'PAL'
        elif h == 480:
            standard = 'NTSC'
        else:
            self.warning('Unknown capture standard for height %d' % h)

        nom = self._par[0]
        den = self._par[1]
        if nom == 59 or nom == 10:
            aspect = '4:3'
        elif nom == 118 or nom == 40:
            aspect = '16:9'
        else:
            self.warning('Unknown pixel aspect ratio %d/%d' % (nom, den))

        text = _('%s, %s (%d/%d pixel aspect ratio)') % (standard, aspect,
            nom, den)
        self.label_camera_settings.set_text(text)

        # factor is a double
        self._factor_i = self.combobox_scaled_height.get_selected()

        self._width_correction = None
        for i in type(self).width_corrections:
            if getattr(self,'radiobutton_width_'+i).get_active():
                self._width_correction = i
                break
        assert self._width_correction

        self._update_output_format()


class FireWireVideoStep(_FireWireCommon, VideoSourceStep):
    name = _('Firewire')
    def __init__(self, wizard, model):
        VideoSourceStep.__init__(self, wizard, model)
        _FireWireCommon.__init__(self)


class FireWireAudioStep(_FireWireCommon, AudioSourceStep):
    name = _('Firewire audio')

    def __init__(self, wizard, model):
        AudioSourceStep.__init__(self, wizard, model)
        _FireWireCommon.__init__(self)

    # WizardStep

    def setup(self):
        self.frame_scaling.hide()
        self.frame_width_correction.hide()
        self.frame_capture.hide()
        self.frame_output_format.hide()

    def get_next(self):
        return None


class FireWireWizardPlugin(object):
    def __init__(self, wizard):
        self.wizard = wizard
        self.audio_model = FireWireAudioProducer()
        self.video_model = FireWireVideoProducer()

    def getProductionStep(self, type):
        if type == 'audio':
            # Only show firewire audio if we're using firewire video
            source_step = self.wizard.get_step('Source')
            if source_step.video.get_active() == 'firewire-producer':
                return
            return FireWireAudioStep(self.wizard, self.audio_model)
        elif type == 'video':
            return FireWireVideoStep(self.wizard, self.video_model)
