# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# flumotion/component/producers/bttv/bttv.py: BTTV producer
#
# Flumotion - a streaming media server
# Copyright (C) 2004 Fluendo, S.L. (www.fluendo.com). All rights reserved.

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
import gst.interfaces

from flumotion.component.base import producer

def state_changed_cb(element, old, new, channel):
    if old == gst.STATE_NULL and new == gst.STATE_READY:
        c = element.find_channel_by_name(channel)
        if c:
            element.set_channel(c)
    
def createComponent(config):
    device = config['device']
    width = config.get('width', 320)
    height = config.get('height', 240)
    channel = config['channel']

    # This needs to be done properly
    device_width = width
    device_height = height
    #device_width = config['device-width']
    #device_height = config['device-height']

    framerate = config.get('framerate', 25.0)
    
    pipeline = ('v4lsrc name=src device=%s copy-mode=true ! '
                'video/x-raw-yuv,width=%d,height=%d ! videoscale ! '
                'video/x-raw-yuv,width=%d,height=%d ! videorate ! '
                'video/x-raw-yuv,framerate=%f') % (device,
                                                   device_width,
                                                   device_height,
                                                   width, height,
                                                   framerate)
    config['pipeline'] = pipeline

    component = producer.createComponent(config)
    pipeline = component.get_pipeline() 
    element = pipeline.get_by_name('src')
    element.connect('state-change', state_changed_cb, channel)
    
    return component
