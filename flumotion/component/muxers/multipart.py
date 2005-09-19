# -*- Mode: Python -*-
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
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

import gst

from flumotion.component import feedcomponent

class Multipart(feedcomponent.ParseLaunchComponent):
    def __init__(self, name, sources, pipeline):
        feedcomponent.ParseLaunchComponent.__init__(self, name,
                                                    sources,
                                                    ['default'],
                                                    pipeline)

def createComponent(config):
    pipeline = 'multipartmux name=muxer '
    for eater in config['source']:
        if gst.gst_version < (0, 9):
            pipeline += '{ @ eater:%s @ ! queue } ! muxer. ' % eater
        else:
            pipeline += '@ eater:%s @ ! queue ! muxer. ' % eater
    pipeline += 'muxer.'
    
    component = Multipart(config['name'], config['source'], pipeline)
    
    return component
