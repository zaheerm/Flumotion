# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2007 Fluendo, S.L. (www.fluendo.com).
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

import common

from twisted.internet import defer

from flumotion.component.bouncers import plug

import bouncertest

class TrivialBouncerTest(bouncertest.TrivialBouncerTest):
    def setUp(self):
        args = {'socket': 'flumotion.component.bouncers.plug.BouncerPlug',
                'type': 'trivial-bouncer-plug',
                'properties': {}}
        self.obj = plug.TrivialBouncerPlug(args)
        self.medium = bouncertest.FakeMedium()
        self.obj.setMedium(self.medium)
        d = defer.maybeDeferred(self.obj.start, None)
        d.addCallback(lambda _: bouncertest.TrivialBouncerTest.setUp(self))
        return d

    def tearDown(self):
        d = defer.maybeDeferred(self.obj.stop, None)
        d.addCallback(lambda _: bouncertest.TrivialBouncerTest.tearDown(self))
        return d
