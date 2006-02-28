# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006 Fluendo, S.L. (www.fluendo.com).
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

from math import frexp
from flumotion.component import feedcomponent

import gst

class Volume(feedcomponent.Effect):
    logCategory = "volume"

    def __init__(self, name, element, pipeline=None):
        """
        @param element: the level element
        @param pipeline: the pipeline (only need in gst > 0.9)
        """
        feedcomponent.Effect.__init__(self, name)
        self._element = element
        # FIXME: set notification to every 1/5th sec, but maybe make
        # configurable ?
        if gst.gst_version < (0,9):
            element.set_property('interval', 0.2)
            element.connect('level', self._level_changed_cb)
        else:
            element.set_property('interval', 200000000)
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect('message::element', self._bus_message_received_cb)

    # Level changed for 0.9
    def _bus_message_received_cb(self, bus, message):
        """
        @param bus: the message bus sending the message
        @param message: the message received
        """
        if message.structure.get_name() == 'level':
            s = message.structure
            # FIXME: have to fix the way we do channels' level display
            # here and in the below 0.8 function
            for i in range(0, len(s['peak'])):
                peak = s['peak'][i]
                decay = s['decay'][i]
                rms = s['rms'][i]
                try:
                    frexp(rms)
                    frexp(peak)
                    frexp(decay)
                except (SystemError, OverflowError, ValueError):
                    # something confused log10() on the C side, punt
                    rms = peak = decay = -100.0
                self.component.adminCallRemote("volumeChanged",
                    i, peak, rms, decay)

    # Level changed only for 0.8
    def _level_changed_cb(self, element, time, channel,
                          rms, peak, decay):
        # notify ui of level change
        try:
            frexp(rms)
            frexp(peak)
            frexp(decay)
        except (SystemError, OverflowError, ValueError):
            # something confused log10() on the C side, punt
            rms = peak = decay = -100.0
        self.component.adminCallRemote("volumeChanged",
            channel, peak, rms, decay)

    def effect_setVolume(self, value):
        """
        Sets volume

        @param value: what value to set volume to (float between 0.0 and 4.0)

        Returns: the actual value it was set to
        """
        self.component.setVolume(value)
        # notify admin clients
        self.component.adminCallRemote("effectVolumeSet", self.name, value)
                        

        return value

