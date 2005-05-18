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

"""
Feed components, participating in the stream
"""

import gst
import gst.interfaces
import gobject

from twisted.internet import reactor
from twisted.spread import pb

from flumotion.configure import configure
from flumotion.component import component as basecomponent
from flumotion.common import common, interfaces, errors, log
from flumotion.common import gstreamer, pygobject

from flumotion.common.planet import moods
from flumotion.common.pygobject import gsignal

class FeedComponentMedium(basecomponent.BaseComponentMedium):
    """
    I am a component-side medium for a FeedComponent to interface with
    the manager-side ComponentAvatar.
    """
    __implements__ = interfaces.IComponentMedium,
    logCategory = 'basecomponentmedium'

    def __init__(self, component):
        """
        @param component: L{flumotion.component.feedcomponent.FeedComponent}
        """
        basecomponent.BaseComponentMedium.__init__(self, component)

        self.comp.connect('feed-state-changed',
            self._component_feed_state_changed_cb)
        self.comp.connect('error', self._component_error_cb)
        self.comp.connect('notify-feed-ports',
            self._component_notify_feed_ports_cb)
        
        # override base Errback for callRemote to stop the pipeline
        #def callRemoteErrback(reason):
        #    self.warning('stopping pipeline because of %s' % reason)
        #    self.comp.pipeline_stop()

    def _component_error_cb(self, component, element_path, message):
        self.comp.setMood(moods.sad)
        self.comp.state.set('message',
            "GStreamer error in component %s (%s)" % (self.comp.name,
            message))
        self.callRemote('error', element_path, message)
        
    def _component_feed_state_changed_cb(self, component, feed_name, old, state):
        self.callRemote('feedStateChanged', feed_name, old, state)
        # FIXME: everything heeds to be playing, not just one !
        if state == gst.STATE_PLAYING:
            self.info('component is HAPPY')
            self.comp.setMood(moods.happy)

    def _component_notify_feed_ports_cb(self, component):
        self.callRemote('notifyFeedPorts', component.feed_ports)

    ### Referenceable remote methods which can be called from manager
    def remote_play(self):
        self.debug('manager asked to play')
        self.comp.play()
        
    def remote_pause(self):
        self.debug('manager asked to pause')
        self.comp.pause()

    def remote_getElementProperty(self, elementName, property):
        return self.comp.get_element_property(elementName, property)
        
    def remote_setElementProperty(self, elementName, property, value):
        self.comp.set_element_property(elementName, property, value)

    def remote_getState(self):
        """
        @rtype: L{flumotion.common.planet.WorkerJobState}
        """
        state = basecomponent.BaseComponentMedium.remote_getState(self)
        if not state:
            return state

        # FIXME: rename functions to Twisted style
        state.set('eaterNames', self.comp.get_eater_names())
        state.set('feederNames', self.comp.get_feeder_names())
        state.set('elementNames', self.comp.get_element_names())
        self.debug('remote_getState of fc: returning state %r' % state)

        return state
    
    def remote_getFreePorts(self, feeders):
        retval = []
        ports = {}
        startPort = configure.defaultGstPortRange[0]
        free_port = common.getFirstFreePort(startPort)
        for name, host, port in feeders:
            if port == None:
                port = free_port
                free_port += 1
            ports[name] = port
            retval.append((name, host, port))
            
        return retval, ports

    def remote_effect(self, effectName, methodName, *args, **kwargs):
        self.debug("calling %s on effect %s" % (methodName, effectName))
        if not effectName in self.comp.effects:
            raise errors.UnknownEffectError(effectName)
        effect = self.comp.effects[effectName]
        if not hasattr(effect, "effect_%s" % methodName):
            raise errors.NoMethodError("%s on effect %s" % (methodName,
                effectName))
        method = getattr(effect, "effect_%s" % methodName)
        try:
            result = method(*args, **kwargs)
        except TypeError:
            msg = "effect method %s did not accept %s and %s" % (
                methodName, args, kwargs)
            self.debug(msg)
            raise errors.RemoteRunError(msg)
        self.debug("effect: result: %r" % result)
        return result

class FeedComponent(basecomponent.BaseComponent):
    """
    I am a base class for all Flumotion feed components.
    """

    logCategory = 'feedcomponent'

    gsignal('feed-state-changed', str, object, object)
    gsignal('error', str, str)
    gsignal('notify-feed-ports')

    component_medium_class = FeedComponentMedium
    _reconnectInterval = 3
    
    def __init__(self, name, eater_config, feeder_config):
        """
        @param name: name of the component
        @type  name: string
        @param eater_config: entries between <source>...</source> from config
        @param feeder_config: entries between <feed>...</feed> from config
        """
        basecomponent.BaseComponent.__init__(self, name)

        self.debug("feedcomponent.__init__: eater_config %r" % eater_config)
        self.debug("feedcomponent.__init__: feeder_config %r" % feeder_config)
        
        self.feed_ports = {} # feed_name -> port mapping
        self.pipeline = None
        self.pipeline_signals = []
        self.files = []
        self.effects = {}
        self._iterate_idle_id = None

        # add extra keys to state
        self.state.addKey('eaterNames')
        self.state.addKey('feederNames')
        self.state.addKey('elementNames')

        self.feed_names = None # done by self.parse*
        self.feeder_names = None

        self.eater_names = [] # componentName:feedName list
        self.parseEaterConfig(eater_config)
        self.eatersWaiting = len(self.eater_names)
        self._eaterReconnectDC = {} 
        for name in self.eater_names:
            self._eaterReconnectDC['eater:' + name] = None

        self.feedersFeeding = 0
        self.feed_names = []
        self.feeder_names = []
        self.parseFeederConfig(feeder_config)
        self.feedersWaiting = len(self.feeder_names)
        self.debug('__init__ with %d eaters and %d feeders waiting' % (
            self.eatersWaiting, self.feedersWaiting))

        # FIXME: maybe this should move to a callLater ?
        self.setup_pipeline()
        self.debug('__init__ finished')

    ### base class overrides
    def updateMood(self):
        """
        Update the mood because a mood condition has changed.
        Will not change the mood if it's sad - sad needs to be explicitly
        fixed.

        See the mood transition diagram.
        """
        mood = self.state.get('mood')
        if mood == moods.sad:
            return

        if self.eatersWaiting == 0 and self.feedersWaiting == 0:
            self.setMood(moods.happy)
            return

        if self.eatersWaiting == 0:
            self.setMood(moods.waking)
        else:
            self.setMood(moods.hungry)
        return

    def addEffect(self, effect):
        self.effects[effect.name] = effect
        effect.setComponent(self)

    def effectPropertyChanged(self, effectName, propertyName, value):
        """
        Notify the manager that an effect property has changed to a new value.
        
        Admin clients will receive it as a propertyChanged message for
        effectName:propertyName.
        """
        self.medium.callRemote("propertyChanged", self.name,
            "%s:%s" % (effectName, propertyName), value)

    def parseEaterConfig(self, eater_config):
        # the source feeder names come from the config
        # they are specified under <component> as <source> elements in XML
        # so if they don't specify a feed name, use "default" as the feed name
        eater_names = []
        for block in eater_config:
            eater_name = block
            if block.find(':') == -1:
                eater_name = block + ':default'
            eater_names.append(eater_name)
        self.eater_names = eater_names
            
    def parseFeederConfig(self, feeder_config):
        # for pipeline components, in the case there is only one
        # feeder, <feed></feed> still needs to be listed explicitly

        # the feed names come from the config
        # they are specified under <component> as <feed> elements in XML
        self.feed_names = feeder_config
        #self.debug("parseFeederConfig: feed_names: %r" % self.feed_names)

        # we create feeder names this component contains based on feed names
        self.feeder_names = map(lambda n: self.name + ':' + n, self.feed_names)

    def get_eater_names(self):
        """
        Return the list of feeder names this component eats from.
        """
        return self.eater_names
    
    def get_feeder_names(self):
        """
        Return the list of feeder names this component has.
        """
        return self.feeder_names

    def get_feed_names(self):
        """
        Return the list of feeder names this component has.
        """
        return self.feed_names

    def restart(self):
        self.debug('restarting')
        self.cleanup()
        self.setup_pipeline()
       
    def set_state_and_iterate(self, state):
        """
        Set the given gst state and start iterating the pipeline if not done
        yet.
        """
        retval = self.pipeline.set_state(state)
        if not retval:
            self.warning('Changing state to %s failed' %
                    gst.element_state_get_name(state))
        if not self._iterate_idle_id:
            self._iterate_idle_id = gobject.idle_add(self.pipeline.iterate)

        return retval

    def get_pipeline(self):
        return self.pipeline

    def create_pipeline(self):
        raise NotImplementedError, "subclass must implement create_pipeline"
        
    def _pipeline_error_cb(self, object, element, error, arg):
        self.debug('element %s error %s %s' % (element.get_path_string(), str(error), repr(arg)))
        self.emit('error', element.get_path_string(), error.message)
        #self.restart()
     
    def setup_pipeline(self):
        self.pipeline.set_name('pipeline-' + self.getName())
        sig_id = self.pipeline.connect('error', self._pipeline_error_cb)
        self.pipeline_signals.append(sig_id)
        
        sig_id = self.pipeline.connect('deep-notify',
                                       gstreamer.verbose_deep_notify_cb, self)
        self.pipeline_signals.append(sig_id)

    def pipeline_pause(self):
        self.set_state_and_iterate(gst.STATE_PAUSED)
        
    def pipeline_play(self):
        """
        Start playing the pipeline.

        @returns: whether or not the pipeline was started successfully.
        """
        retval = self.set_state_and_iterate(gst.STATE_PLAYING)
        if not retval:
            self.setMood(moods.sad)
            self.state.set('message',
                "Component %s could not start" % self.name)
            return False

        return True

    def pipeline_stop(self):
        if not self.pipeline:
            return
        
        retval = self.set_state_and_iterate(gst.STATE_NULL)
        if not retval:
            self.warning('Setting pipeline to NULL failed')

    def set_feed_ports(self, feed_ports):
        """
        @param feed_ports: feed_name -> port
        @type feed_ports: dict
        """
        assert isinstance(feed_ports, dict)
        self.feed_ports = feed_ports
        
    def _setup_eaters(self, eatersData):
        """
        Set up the feeded GStreamer elements in our pipeline based on
        information in the tuple.  For each feeded element in the tuple,
        it sets the host and port of the feeder on the feeded element.

        @type eatersData: list
        @param eatersData: list of (feederName, host, port) tuples
        """
        if not self.pipeline:
            raise errors.NotReadyError('No pipeline')
        
        # Setup all eaters
        for feederName, host, port in eatersData:
            self.debug('Going to connect to feeder %s (%s:%d)' % (feederName, host, port))
            name = 'eater:' + feederName
            eater = self.get_element(name)
            assert eater, 'No eater element named %s in pipeline' % name
            assert isinstance(eater, gst.Element)
            
            eater.set_property('host', host)
            eater.set_property('port', port)
            eater.set_property('protocol', 'gdp')
            eater.connect('state-change', self.eater_state_change_cb)

    def eater_state_change_cb(self, element, old, state):
        """
        Called when the eater element changes state.
        """
        # also called by subclasses
        name = element.get_name()
        self.debug('eater-state-changed: eater %s, element %s, state %s' % (
            name,
            element.get_path_string(),
            gst.element_state_get_name(state)))

        # update eatersWaiting count
        if old == gst.STATE_PAUSED and state == gst.STATE_PLAYING:
            self.debug('eater %s is now eating' % name)
            self.eatersWaiting -= 1
            self.updateMood()
            if self._eaterReconnectDC[name]:
                self._eaterReconnectDC[name].cancel()
                self._eaterReconnectDC[name] = None
                
        if old == gst.STATE_PLAYING and state == gst.STATE_PAUSED:
            self.debug('eater %s is now hungry' % name)
            self.eatersWaiting += 1
            self.state.set('message',
                "Component %s is now hungry, starting reconnect" % self.name)
            self.updateMood()
            self._eaterReconnectDC[name] = reactor.callLater(
                self._reconnectInterval, self._eaterReconnect, element)
            
        self.debug('%d eaters waiting' % self.eatersWaiting)

    def _eaterReconnect(self, element):
        name = element.get_name()
        self.debug('Trying to reconnect eater %s' % name)
        host = element.get_property('host')
        port = element.get_property('port')
        if common.checkRemotePort(host, port):
            self.debug('%s:%d accepting connections, setting to PLAYING' % (
                host, port))
            self._eaterReconnectDC[name] = None
            # currently, we need to make sure all other elements go to PLAYING
            # as well, so we PAUSE then PLAY the complete pipeline
            #element.set_state(gst.STATE_PLAYING)
            self.debug('pausing and iterating')
            self.pipeline_pause()
            self.debug('playing and iterating')
            self.pipeline_play()
            self.debug('reconnected')
        else:
            self.debug('%s:%d not accepting connections, trying later' % (
                host, port))
            self._eaterReconnectDC[name] = reactor.callLater(
                self._reconnectInterval, self._eaterReconnect, element)
            
    def _setup_feeders(self, feedersData):
        """
        Set up the feeding GStreamer elements in our pipeline based on
        information in the tuple.  For each feeding element in the tuple,
        it sets the host it will listen as.

        @type  feedersData: tuple
        @param feedersData: a list of (feederName, host) tuples.

        @returns: a list of (feedName, host, port) tuples for our feeders.
        """
 
        if not self.pipeline:
            raise errors.NotReadyError('No pipeline')

        self.debug("_setup_feeders: feedersData %r" % feedersData)

        retval = []
        # Setup all feeders
        for feeder_name, host in feedersData:
            feed_name = feeder_name.split(':')[1]
            self.debug("_setup_feeders: self.feed_ports: %r" % self.feed_ports)
            assert self.feed_ports.has_key(feed_name), feed_name
            port = self.feed_ports[feed_name]
            self.debug('Going to listen on feeder %s (%s:%d)' % (
                feeder_name, host, port))
            name = 'feeder:' + feeder_name
            feeder = self.get_element(name)
            assert feeder, 'No feeder element named %s in pipeline' % feed_name
            assert isinstance(feeder, gst.Element)

            feeder.connect('state-change', self.feeder_state_change_cb, feed_name)
            feeder.set_property('host', host)
            feeder.set_property('port', port)
            feeder.set_property('protocol', 'gdp')

            retval.append((feed_name, host, port))

        return retval

    # FIXME: need to make a separate callback to implement "mood" of component
    #        This is used by file/file.py, so make sure to syncronize them
    def feeder_state_change_cb(self, element, old, state, feed_name):
        # also called by subclasses
        self.debug('feed-state-changed on feed %s: element %s, state %s' % (
            feed_name, element.get_path_string(),
            gst.element_state_get_name(state)))

        # update feedersWaiting count
        if old == gst.STATE_PAUSED and state == gst.STATE_PLAYING:
            self.debug('feeder %s is now feeding' % element.get_name())
            self.feedersWaiting -= 1
            self.updateMood()
        if old == gst.STATE_PLAYING and state == gst.STATE_PAUSED:
            self.debug('feeder %s is now waiting' % element.get_name())
            self.feedersWaiting += 1
            self.updateMood()

        self.debug('%d feeders waiting' % self.feedersWaiting)
        self.emit('feed-state-changed', feed_name, old, state)

    
    def cleanup(self):
        self.debug("cleaning up")
        
        assert self.pipeline != None

        if self.pipeline.get_state() != gst.STATE_NULL:
            self.debug('Pipeline was in state %s, changing to NULL' %
                     gst.element_state_get_name(self.pipeline.get_state()))
            self.pipeline.set_state(gst.STATE_NULL)
                
        # Disconnect signals and id's
        map(self.pipeline.disconnect, self.pipeline_signals)
        self.pipeline = None
        self.pipeline_signals = []
        if self._iterate_idle_id:
            gobject.source_remove(self._iterate_idle_id)

    def play(self):
        self.debug('Playing')
        self.pipeline_play()

    def stop(self):
        self.debug('Stopping')
        self.pipeline_stop()
        self.debug('Stopped')
        basecomponent.BaseComponent.stop(self)

    def pause(self):
        self.debug('Pausing')
        self.pipeline_pause()
                
    # FIXME: rename, unambiguate and comment
    def link(self, eatersData, feedersData):
        """
        Make the component eat from the feeds it depends on and start
        producing feeds itself.

        @param eatersData: list of (feederName, host, port) tuples to eat from
        @param feedersData: list of (feederName, host) tuples to use as feeders

        @returns: a list of (feedName, host, port) tuples for our feeders
        """
        # if we have eaters waiting, we start out hungry, else waking
        if self.eatersWaiting:
            self.setMood(moods.hungry)
        else:
            self.setMood(moods.waking)

        self.debug('manager asks us to link')
        self.debug('setting up eaters')
        self._setup_eaters(eatersData)

        self.debug('setting up feeders')
        retval = self._setup_feeders(feedersData)
        
        # call a child's link_setup() method if it has it
        func = getattr(self, 'link_setup', None)
        if func:
            self.debug('calling function %r' % func)
            func(eatersData, feedersData)
            
        self.debug('setting pipeline to play')
        self.pipeline_play()
        # FIXME: fill feedPorts
        self.debug('emitting feed port notify')
        self.emit('notify-feed-ports')
        self.debug('.link() returning %s' % retval)

        return retval

    def get_element(self, element_name):
        assert self.pipeline
        element = self.pipeline.get_by_name(element_name)
        return element
    
    def get_element_names(self):
        'Return the names of all elements in the GStreamer pipeline.'
        pipeline = self.get_pipeline()
        return [element.get_name() for element in pipeline.get_list()]
        
    def get_element_property(self, element_name, property):
        'Gets a property of an element in the GStreamer pipeline.'
        self.debug("%s: getting property %s of element %s" % (self.getName(), property, element_name))
        element = self.get_element(element_name)
        if not element:
            msg = "Element '%s' does not exist" % element_name
            self.warning(msg)
            raise errors.PropertyError(msg)
        
        self.debug('getting property %s on element %s' % (property, element_name))
        try:
            value = element.get_property(property)
        except (ValueError, TypeError):
            msg = "Property '%s' on element '%s' does not exist" % (property, element_name)
            self.warning(msg)
            raise errors.PropertyError(msg)

        # param enums and enums need to be returned by integer value
        if isinstance(value, gobject.GEnum):
            value = int(value)

        return value

    def set_element_property(self, element_name, property, value):
        'Sets a property on an element in the GStreamer pipeline.'
        self.debug("%s: setting property %s of element %s to %s" % (
            self.getName(), property, element_name, value))
        element = self.get_element(element_name)
        if not element:
            msg = "Element '%s' does not exist" % element_name
            self.warning(msg)
            raise errors.PropertyError(msg)

        self.debug('setting property %s on element %r to %s' %
                   (property, element_name, value))
        pygobject.gobject_set_property(element, property, value)
    
gobject.type_register(FeedComponent)

class ParseLaunchComponent(FeedComponent):
    'A component using gst-launch syntax'

    # keep these as class variables for the tests
    EATER_TMPL = 'tcpclientsrc'
    FEEDER_TMPL = 'tcpserversink buffers-max=500 buffers-soft-max=450 recover-policy=1'
    DELIMETER = '@'

    def __init__(self, name, eaters, feeders, pipeline_string=''):
        self.pipeline_string = pipeline_string
        FeedComponent.__init__(self, name, eaters, feeders)

    ### FeedComponent methods
    def setup_pipeline(self):
        pipeline = self.parse_pipeline(self.pipeline_string)
        self.pipeline_string = pipeline
        try:
            self.pipeline = gst.parse_launch(pipeline)
        except gobject.GError, e:
            raise errors.PipelineParseError(e.message)
        FeedComponent.setup_pipeline(self)

    ### ParseLaunchComponent methods
    def _expandElementName(self, block):
        """
        Expand the given string to a full element name for an eater or feeder.
        The full name is of the form eater:(sourceComponentName):(feedName)
        or feeder:(componentName):feedName
        """
        if ' ' in block:
            raise TypeError, "spaces not allowed in '%s'" % block
        if not ':' in block:
            raise TypeError, "no colons in'%s'" % block
        if block.count(':') > 2:
            raise TypeError, "too many colons in '%s'" % block
            
        parts = block.split(':')

        if parts[0] != 'eater' and parts[0] != 'feeder':
            raise TypeError, "'%s' does not start with eater or feeder" % block
            
        # we can only fill in component names for feeders
        if not parts[1]:
            if parts[0] == 'eater':
                raise TypeError, "'%s' should specify feeder component" % block
            parts[1] = self.name
        if len(parts) == 2:
            parts.append('')
        if  not parts[2]:
            parts[2] = 'default'

        return ":".join(parts)
        
    def _expandElementNames(self, block):
        """
        Expand each @..@ block to use the full element name for eater or feeder.
        The full name is of the form eater:(sourceComponentName):(feedName)
        or feeder:(componentName):feedName
        This also does some basic checking of the block.
        """
        assert block != ''

        # verify the template has an even number of delimiters
        if block.count(self.DELIMETER) % 2 != 0:
            raise TypeError, "'%s' contains an odd number of '%s'" % (block, self.DELIMETER)
        
        # when splitting, the even-indexed members will remain,
        # and the odd-indexed members are the blocks to be substituted
        blocks = block.split(self.DELIMETER)

        for i in range(1, len(blocks) - 1, 2):
            blocks[i] = self._expandElementName(blocks[i].strip())
        return "@".join(blocks)
  
    def parse_tmpl(self, pipeline, names, template, format):
        """
        Expand the given pipeline string representation by substituting
        blocks between '@' with a filled-in template.

        @param pipeline: a pipeline string representation with variables
        @param names: the element names to substitute for @...@ segments
        @param template: the template to use for element factory info
        @param format: the format to use when substituting

        Returns: a new pipeline string representation.
        """
        assert pipeline != ''

        deli = self.DELIMETER

        if len(names) == 1:
            part_name = names[0]
            if pipeline.find(part_name) != -1:
                pipeline = pipeline.replace(deli + part_name + deli, '%s name=%s' % (template, part_name))
            else:
                pipeline = format % {'tmpl': template, 'name': part_name, 'pipeline': pipeline}
        else:
            for part in names:
                part_name = deli + part + deli
                if pipeline.find(part_name) == -1:
                    raise TypeError, "%s needs to be specified in the pipeline '%s'" % (part_name, pipeline)
            
                pipeline = pipeline.replace(part_name, '%s name=%s' % (template, part))
        return pipeline
        
    def parse_pipeline(self, pipeline):
        pipeline = " ".join(pipeline.split())
        self.debug('Creating pipeline, template is %s' % pipeline)
        
        eater_names = self.get_eater_names()
        if pipeline == '' and not eater_names:
            raise TypeError, "Need a pipeline or a eater"

        if pipeline == '':
            assert eater_names
            pipeline = 'fakesink signal-handoffs=1 silent=1 name=sink'
            
        # we expand the pipeline based on the templates and eater/feeder names
        # elements are named eater:(source_component_name):(feed_name)
        # or feeder:(component_name):(feed_name)
        eater_element_names = map(lambda n: "eater:" + n, eater_names)
        feeder_element_names = map(lambda n: "feeder:" + n, self.feeder_names)
        self.debug('we eat with eater elements %s' % eater_element_names)
        self.debug('we feed with feeder elements %s' % feeder_element_names)
        pipeline = self._expandElementNames(pipeline)
        
        pipeline = self.parse_tmpl(pipeline, eater_element_names,
                                   self.EATER_TMPL,
                                   '%(tmpl)s name=%(name)s ! %(pipeline)s') 
        pipeline = self.parse_tmpl(pipeline, feeder_element_names,
                                   self.FEEDER_TMPL,
                                   '%(pipeline)s ! %(tmpl)s name=%(name)s') 
        pipeline = " ".join(pipeline.split())
        
        self.debug('pipeline for %s is %s' % (self.getName(), pipeline))
        assert self.DELIMETER not in pipeline
        
        return pipeline

    # mood change/state functions
    def start(self, eatersData, feedersData):
        """
        Tell the component to start, linking itself to other components.

        @type eatersData:  list of (feedername, host, port) tuples of elements
                           feeding our eaters.
        @type feedersData: list of (name, host) tuples of our feeding elements

        @returns: list of (feedName, host, port)-tuples of feeds the component
                  produces.
        """
        self.debug('start with eaters data %s and feeders data %s' % (
            eatersData, feedersData))
        self.setMood(moods.waking)

        ret = self.link(eatersData, feedersData)

        self.debug('start: returning value %s' % ret)

        # chain to parent 
        basecomponent.BaseComponent.start(self)
        
        return ret


class Effect(log.Loggable):
    """
    I am a part of a feed component for a specific group
    of functionality.

    @ivar name:      name of the effect
    @type name:      string
    @ivar component: component owning the effect
    @type component: L{FeedComponent}
    """
    logCategory = "effect"

    def __init__(self, name):
        """
        @param name: the name of the effect
        """
        self.name = name
        self.component = None # component owning this effect

    def setComponent(self, component):
        """
        Set the given component as the effect's owner.
        
        @param component: the component to set as an owner of this effect
        @type  component: L{FeedComponent}
        """                               
        self.component = component

    def getComponent(self):
        """
        Get the component owning this effect.
        
        @rtype:  L{FeedComponent}
        """                               
        return self.component

