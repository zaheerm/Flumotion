# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

# Flumotion - a video streaming server
# Copyright (C) 2004 Fluendo
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

import socket
import sys
    
import pygtk
pygtk.require('2.0')

import gobject
import gst

if __name__ == '__main__':
    import gstreactor
    gstreactor.install()

from twisted.application import service, internet
from twisted.cred import portal, checkers, credentials
from twisted.internet import reactor, error
from twisted.manhole import telnet
from twisted.spread import pb

from flumotion.twisted import pbutil, shell
from flumotion.utils import gstutils, log

def msg(*args):
    log.msg('controller', *args)

class Dispatcher:
    __implements__ = portal.IRealm
    def __init__(self, controller):
        self.controller = controller

    def requestAvatar(self, avatarID, mind, interface):
        assert interface == pb.IPerspective
        
        #msg('requestAvatar (%s, %s, %s)' % (avatarID, mind, interface))

        # This could use some cleaning up
        component_type, avatarID = avatarID.split('_', 1)
        
        if self.controller.hasComponent(avatarID):
            # XXX: Raise exception/deny access
            return
        
        p = self.controller.getPerspective(component_type, avatarID)

        #msg("returning Avatar(%s): %s" % (avatarID, p))
        if not p:
            raise ValueError, "no perspective for '%s'" % avatarID

        reactor.callLater(0, p.attached, mind)
        
        return (pb.IPerspective, p,
                lambda p=p,mind=mind: p.detached(mind))

class Options:
    """dummy class for storing controller side options of a component"""

class ComponentPerspective(pbutil.NewCredPerspective):
    """Perspective all components will have on the controller side"""
    def __init__(self, controller, username):
        self.controller = controller
        self.username = username
        self.state = gst.STATE_NULL
        self.options = Options()
        self.listen_ports = {}
        self.started = False
        self.starting = False
        
    def __repr__(self):
        return '<%s %s in state %s>' % (self.__class__.__name__,
                                        self.getName(),
                                        gst.element_state_get_name(self.state))

    def msg(self, *args):
        args = ('=%s=' % self.getName(),) + args
        msg(*args)
        
    def getTransportPeer(self):
        return self.mind.broker.transport.getPeer()

    def getSources(self):
        return self.options.sources
    
    def getFeeds(self, longname=False):
        if longname:
            return map(lambda feed:
                       self.getName() + ':' + feed, self.options.feeds)
        else:
            return self.options.feeds

    def getRemoteControllerIP(self):
        return self.options.ip

    def getName(self):
        return self.username

    def getListenHost(self):
        return self.getTransportPeer()[1]

    # This method should ask the component if the port is free
    def getListenPort(self, feed):
        if feed.find(':') != -1:
            feed = feed.split(':')[1]

        assert self.listen_ports.has_key(feed), self.listen_ports
        assert self.listen_ports[feed] != -1, self.listen_ports
        return self.listen_ports[feed]

    def after_register_cb(self, options, cb):
        if options == None:
            cb = self.mind.callRemote('register')
            cb.addCallback(self.after_register_cb, cb)
            return

        for key, value in options.items():
            setattr(self.options, key, value)

        self.controller.componentRegistered(self)
            
    def attached(self, mind):
        #msg('%s attached, calling register()' % self.getName())
        self.mind = mind
        
        cb = mind.callRemote('register')
        cb.addCallback(self.after_register_cb, cb)
        
    def detached(self, mind):
        self.msg('detached')
        name = self.getName()
        if self.controller.hasComponent(name):
            self.controller.removeComponent(self)

    def perspective_stateChanged(self, feed, old, state):
        #self.msg('stateChanged :%s %s' % (feed,
        #                                  gst.element_state_get_name(state)))
        
        self.state = state
        if self.state == gst.STATE_PLAYING:
            self.msg('%s is now playing' % feed)
            self.controller.startPendingComponents(self, feed)
            
    def perspective_error(self, element, error):
        self.msg('error element=%s string=%s' % (element, error))
        
        self.controller.removeComponent(self)

class ProducerPerspective(ComponentPerspective):
    """Perspective for producer components"""
    kind = 'producer'
    def after_get_free_ports_cb(self, (feeds, ports)):
        self.listen_ports = ports
        self.msg('Calling remote method listen (%s)' % feeds)
        self.mind.callRemote('listen', feeds)

    def listen(self, feeds):
        """starts the remote methods listen"""

        self.msg('Calling remote method get_free_ports()')
        cb = self.mind.callRemote('get_free_ports', feeds)
        cb.addCallback(self.after_get_free_ports_cb)
            
class ConverterPerspective(ComponentPerspective):
    """Perspective for converter components"""
    kind = 'converter'

    def start(self, sources, feeds):
        def after_get_free_ports_cb((feeds, ports)):
            self.listen_ports = ports
            self.msg('Calling remote method start (%s %s)' % (sources, feeds))
            self.mind.callRemote('start', sources, feeds)
            
        """starts the remote methods start"""
        self.msg('Calling remote method get_free_port()')
        cb = self.mind.callRemote('get_free_ports', feeds)
        cb.addCallback(after_get_free_ports_cb)
            
class StreamerPerspective(ComponentPerspective):
    """Perspective for streamer components"""
    kind = 'streamer'
            
    def getListenHost(self):
        "Should never be called, a Streamer does not accept incoming components"
        raise AssertionError
    
    def getListenPort(self):
        "Should never be called, a Streamer does not accept incoming components"
        raise AssertionError

    def connect(self, sources):
        """starts the remote methods connect"""
        self.msg('Calling remote method connect(%s)' % sources)
        self.mind.callRemote('connect', sources)

STATE_NULL     = 0
STATE_STARTING = 1
STATE_READY    = 2

class Feed:
    def __init__(self, name):
        self.name = name
        self.dependencies = []
        self.state = STATE_NULL
        self.component = None

    def setComponent(self, component):
        self.component = component
        
    def addDependency(self, func, *args):
        self.dependencies.append((func, args))

    def setReady(self):
        self.state = STATE_READY
        for func, args in self.dependencies:
            func(*args)
        self.dependencies = []

    def getName(self):
        return self.name

    def getListenHost(self):
        return self.component.getListenHost()

    def getListenPort(self):
        return self.component.getListenPort(self.name)
    
    def __repr__(self):
        return '<Feed %s state=%d>' % (self.name, self.state)
    
class FeedManager:
    def __init__(self):
        self.feeds = {}

    def hasFeed(self, feedname):
        if feedname.find(':') == -1:
            feedname += ':default'

        return self.feeds.has_key(feedname)
    
    def __getitem__(self, key):
        if key.find(':') == -1:
            key += ':default'

        return self.feeds[key]
        
    def getFeed(self, feedname):
        return self[feedname]
    
    def addFeeds(self, component):
        name = component.getName()
        feeds = component.getFeeds(True)
        for feedname in feeds:
            longname = name + ':' + feedname
            if not self.feeds.has_key(feedname):
                self.feeds[feedname] = Feed(feedname)
            self.feeds[feedname].setComponent(component)
            
    def isFeedReady(self, feedname):
        if not self.hasFeed(feedname):
            return False

        feed = self[feedname]

        return feed.state == STATE_READY
    
    def feedReady(self, feedname): 
        # If we don't specify the feed
        log.msg('controller', '=%s= ready' % (feedname))

        feed = self.feeds[feedname]
        feed.setReady()
            
    def dependOnFeed(self, feedname, func, *args):
        # If we don't specify the feed
        if feedname.find(':') == -1:
            feedname += ':default'

        if not self.feeds.has_key(feedname):
            self.feeds[feedname] = Feed(feedname)
            
        feed = self.feeds[feedname]
        if feed.state != STATE_READY:
            feed.addDependency(func, *args)
        else:
            func(*args)
    
class Controller(pb.Root):
    def __init__(self):
        self.components = {}
        self.feed_manager = FeedManager()
        
        self.last_free_port = 5500
        
    def getPerspective(self, component_type, username):
        if component_type == 'producer':
            klass = ProducerPerspective
        elif component_type == 'converter':
            klass = ConverterPerspective
        elif component_type == 'streamer':
            klass = StreamerPerspective
        else:
            raise AssertionError

        component = klass(self, username)
        self.addComponent(component)
        return component

    def isLocalComponent(self, component):
        # TODO: This could be a lot smarter
        if component.getListenHost() == '127.0.0.1':
            return True
        else:
            return False

    def isComponentStarted(self, component_name):
        if not self.hasComponent(component_name):
            return False

        component = self.components[component_name]

        return component.started == True
    
    def getComponent(self, name):
        """retrieves a new component
        @type name:  string
        @param name: name of the component
        @rtype:      component
        @returns:    the component"""

        if not self.hasComponent(name):
            raise KeyError, name
        
        return self.components[name]
    
    def hasComponent(self, name):
        """adds a new component
        @type name:  string
        @param name: name of the component
        @rtype:      boolean
        @returns:    True if a component with that name is registered, otherwise False"""
        
        return self.components.has_key(name)
    
    def addComponent(self, component):
        """adds a component
        @type component: component
        @param component: the component"""

        component_name = component.getName()
        if self.hasComponent(component_name):
            raise KeyError, component_name
            
        self.components[component_name] = component

    def removeComponent(self, component):
        """removes a component
        @type component: component
        @param component: the component"""

        component_name = component.getName()
        if not self.hasComponent(component_name):
            raise KeyError, component_name

        del self.components[component_name]

    def getSourceComponents(self, component):
        """Retrives the source components for component

        @type component:  component
        @param component: the component
        @rtype:           tuple of with 3 items
        @returns:         name, hostname and port"""

        assert not isinstance(component, ProducerPerspective)

        peernames = component.getSources()
        retval = []
        for peername in peernames:
            feed = self.feed_manager.getFeed(peername)
            feedname = feed.getName()
            if feedname.endswith(':default'):
                feedname = feedname[:-8]
                
            retval.append((feedname,
                           feed.getListenHost(),
                           feed.getListenPort()))
        return retval

    def getFeedsForComponent(self, component):
        """Retrives the source components for component

        @type component:  component
        @param component: the component
        @rtype:           tuple of with 3 items
        @returns:         name, hostname and port"""

        assert isinstance(component, ComponentPerspective), component

        host = component.getListenHost()
        feednames = component.getFeeds()
        retval = []
        for feedname in feednames:
            if self.isLocalComponent(component):
                port = gstutils.get_free_port(self.last_free_port)
                self.last_free_port = port + 1
            else:
                port = None

            retval.append((feedname, host, port))
        return retval

    def producerStart(self, producer):
        assert isinstance(producer, ProducerPerspective)

        feeds = self.getFeedsForComponent(producer)
        producer.listen(feeds)

    def converterStart(self, converter):
        assert isinstance(converter, ConverterPerspective)
        
        sources = self.getSourceComponents(converter)
        feeds = self.getFeedsForComponent(converter)
        converter.start(sources, feeds)
            
    def streamerStart(self, streamer):
        assert isinstance(streamer, StreamerPerspective)
        
        sources = self.getSourceComponents(streamer)
        streamer.connect(sources)
        
    def componentStart(self, component):
        component.msg('Starting')
        assert isinstance(component, ComponentPerspective)
        assert component != ComponentPerspective

        if isinstance(component, ProducerPerspective):
            self.producerStart(component)
        elif isinstance(component, ConverterPerspective):
            self.converterStart(component)
        elif isinstance(component, StreamerPerspective):
            self.streamerStart(component)

    def maybeComponentStart(self, component):
        component.msg('maybeComponentStart')
        
        for source in component.getSources():
            if not self.feed_manager.isFeedReady(source):
                component.msg('source %s is not ready' % (source))
                return

        if component.starting:
            return
        
        component.starting = True
        self.componentStart(component)
        
    def componentRegistered(self, component):
        component.msg('in componentRegistered')
    
        self.feed_manager.addFeeds(component)

        sources = component.getSources()
        if not sources:
            component.msg('no sources, starting immediatelly')
            self.componentStart(component)
            return
        else:
            for source in sources:
                self.feed_manager.dependOnFeed(source,
                                               self.maybeComponentStart,
                                               component)
                
    def startPendingComponents(self, component, feed):
        feedname = component.getName() + ':' + feed
        self.feed_manager.feedReady(feedname)

class ControllerServerFactory(pb.PBServerFactory):
    """A Server Factory with a Dispatcher and a Portal"""
    def __init__(self):
        self.controller = Controller()
        self.dispatcher = Dispatcher(self.controller)
        checker = pbutil.ReallyAllowAnonymousAccess()
        
        self.portal = portal.Portal(self.dispatcher, [checker])
        pb.PBServerFactory.__init__(self, self.portal)

    def __repr__(self):
        return '<ControllerServerFactory>'

if __name__ == '__main__':
    controller = ControllerServerFactory()

    ts = telnet.ShellFactory()
    ts.namespace.update(controller.controller.__dict__)
    ts.namespace['dispatcher'] = controller.dispatcher
    ts.namespace['portal'] = controller.portal

    ts.protocol = twistedutils.Shell
    try:
        reactor.listenTCP(8890, controller)
        reactor.listenTCP(4040, ts)
    except error.CannotListenError, e:
        print 'ERROR:', e
        raise SystemExit

    
    reactor.run(False)
