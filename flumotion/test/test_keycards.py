# -*- Mode: Python; test-case-name: flumotion.test.test_keycards -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# flumotion/test/test_keycards.py:
# regression test for flumotion.common.keycards
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

import common
from twisted.trial import unittest

from flumotion.common import keycards

class TestKeycardUACPC(unittest.TestCase):
    def testInit(self):
        keycard = keycards.KeycardUACPC('user', '127.0.0.1')
        self.assertEquals(keycard.state, keycards.REQUESTING)
        
if __name__ == '__main__':
     unittest.main()
