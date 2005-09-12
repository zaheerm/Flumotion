# -*- Mode: Python; test-case-name: flumotion.test.test_component -*-
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
worker-side objects for components
"""

import os
import sys
import time
import socket

import gobject

from twisted.internet import reactor, error, defer
from twisted.cred import error as crederror
from twisted.spread import pb

from flumotion.common import interfaces, errors, log, planet, medium, compat
from flumotion.common import componentui
from flumotion.common.planet import moods
from flumotion.configure import configure
from flumotion.twisted import credentials
from flumotion.twisted import pb as fpb
from flumotion.common.pygobject import gsignal


class ComponentClientFactory(fpb.ReconnectingFPBClientFactory):
    """
    I am a client factory for a component logging in to the manager.
    """
    logCategory = 'component'
    def __init__(self, component):
        """
        @param component: L{flumotion.component.component.BaseComponent}
        """
        # doing this as a class method triggers a doc error
        fpb.ReconnectingFPBClientFactory.__init__(self)
        
        self.component = component
        # get the component's medium class, defaulting to the base one
        klass = getattr(component, 'component_medium_class', BaseComponentMedium)
        # instantiate the medium, giving it the component it's a medium for
        self.medium = klass(component)
        component.setMedium(self.medium)

        self.maxDelay = 10
        # get the interfaces implemented by the component medium class
        self.interfaces = getattr(klass, '__implements__', ())

        self.logName = component.name
        
    # override log.Loggable method so we don't traceback
    def error(self, message):
        self.warning('Shutting down because of %s' % message)
        print >> sys.stderr, 'ERROR: %s' % message
        # FIXME: do we need to make sure that this cannot shut down the
        # manager if it's the manager's bouncer ?
        reactor.stop()
        self.component.setMood(moods.sad)

    # vmethod implementation
    def gotDeferredLogin(self, d):
        def remoteDisconnected(remoteReference):
            self.warning('Lost connection to manager, will attempt to reconnect')

        def loginCallback(reference):
            self.info("Logged in to manager")
            self.debug("remote reference %r" % reference)
            self.medium.setRemoteReference(reference)
            reference.notifyOnDisconnect(remoteDisconnected)

        def accessDeniedErrback(failure):
            failure.trap(crederror.UnauthorizedLogin)
            self.error('Access denied.')
            
        def connectionRefusedErrback(failure):
            failure.trap(error.ConnectionRefusedError)
            self.error('Connection to manager refused.')
                                                          
        def loginFailedErrback(failure):
            self.error('Login failed, reason: %s' % failure)

        d.addCallback(loginCallback)
        d.addErrback(accessDeniedErrback)
        d.addErrback(connectionRefusedErrback)
        d.addErrback(loginFailedErrback)

    def startLogin(self, keycard):
        fpb.ReconnectingFPBClientFactory.startLogin(self, keycard, self.medium,
                                                    interfaces.IComponentMedium)
        
# needs to be before BaseComponent because BaseComponent references it
class BaseComponentMedium(medium.BaseMedium):
    """
    I am a medium interfacing with a manager-side avatar.
    I implement a Referenceable for the manager's avatar to call on me.
    I have a remote reference to the manager's avatar to call upon.
    """

    __implements__ = interfaces.IComponentMedium,
    logCategory = 'basecompmedium'

    def __init__(self, component):
        """
        @param component: L{flumotion.component.component.BaseComponent}
        """
        self.comp = component
        self.comp.connect('log', self._component_log_cb)
        
        self.logName = component.name
        
    ### our methods
    def getIP(self):
        """
        Return our own IP as seen from the manager.
        """
        assert self.remote
        peer = self.remote.broker.transport.getPeer()
        try:
            host = peer.host
        except AttributeError:
            host = peer[1]

        return socket.gethostbyname(host)

    def _component_log_cb(self, component, args):
        self.callRemote('log', *args)
        
    ### pb.Referenceable remote methods
    ### called from manager by our avatar
    def remote_getState(self):
        """
        Return the state of the component, which will be serialized to a
        L{flumotion.common.planet.ManagerJobState} object.

        @rtype:   L{flumotion.common.planet.WorkerJobState}
        @returns: state of component
        """
        # we can only get the IP after we have a remote reference, so add it
        # here
        self.comp.state.set('ip', self.getIP())
        self.debug('remote_getState of f: returning %r' % self.comp.state)

        return self.comp.state
        
    def remote_getConfig(self):
        """
        Return the configuration of the component.

        @rtype:   dict
        @returns: component's current configuration
        """
        self.debug('remote_getConfig of f: returning %r' % self.comp.config)
        return self.comp.config
        
    def remote_start(self, *args, **kwargs):
        return self.comp.start(*args, **kwargs)
       
    def remote_stop(self):
        self.info('Stopping job')
        d = defer.maybeDeferred(self.comp.stop)
        d.addCallback(self._destroyCallback)

        return d

    def _destroyCallback(self, result):
        self.debug('_destroyCallback: losing connection and stopping reactor')
        reactor.callLater(0, self.remote.broker.transport.loseConnection)
        reactor.callLater(0, reactor.stop)

    def remote_reloadComponent(self):
        """Reload modules in the component."""
        import sys
        from twisted.python.rebuild import rebuild
        from twisted.python.reflect import filenameToModuleName
        name = filenameToModuleName(__file__)

        # reload ourselves first
        rebuild(sys.modules[name])

        # now rebuild relevant modules
        import flumotion.common.reload
        rebuild(sys.modules['flumotion.common'])
        try:
            flumotion.common.reload.reload()
        except SyntaxError, msg:
            raise errors.ReloadSyntaxError(msg)
        self._reloaded()

    def remote_getUIState(self):
        """Get a WorkerComponentUIState containing details needed to
        present an admin-side UI state
        """
        return self.comp.uiState

    # separate method so it runs the newly reloaded one :)
    def _reloaded(self):
        self.info('reloaded module code for %s' % __name__)

    def remote_callMethod(self, methodName, *args, **kwargs):
        method = getattr(self.comp, 'remote_' + methodName, None)
        if method:
            return method(*args, **kwargs)
        msg = "%r doesn't have method remote_%s" % (self.comp, methodName)
        self.warning(msg)
        raise errors.MoMethodError(msg)

class BaseComponent(log.Loggable, gobject.GObject):
    """
    I am the base class for all Flumotion components.

    @ivar name: the name of the component
    @type name: string

    @cvar component_medium_class: the medium class to use for this component
    @type component_medium_class: child class of L{BaseComponentMedium}
    """

    logCategory = 'basecomp'

    gsignal('log', object)

    component_medium_class = BaseComponentMedium
    _heartbeatInterval = configure.heartbeatInterval
    
    def __init__(self, name):
        # FIXME: name is unique where ? only in flow, so not in worker
        # need to use full path maybe ?
        """
        @param name: unique name of the component
        @type name: string
        """
        gobject.GObject.__init__(self)

        self.state = planet.WorkerJobState()
        self.config = None # a dict
        
        #self.state.set('name', name)
        self.state.set('mood', moods.sleeping.value)
        self.state.set('pid', os.getpid())

        # FIXME: remove stuff in state
        self.name = name

        self._HeartbeatDC = None
        self.medium = None # the medium connecting us to the manager's avatar
 
        self.uiState = componentui.WorkerComponentUIState()

        # FIXME: when we need this somewhere else, put this in a class and
        # use it that way
        self.baseTime = time.time()
        self.lastTime = time.time()
        self.lastClock = time.clock()

    def updateMood(self):
        """
        Update the mood because a mood condition has changed.
        Will not change the mood if it's sad - sad needs to be explicitly
        fixed.

        See the mood transition diagram.
        """
        mood = self.state.get('mood')
        if mood == moods.sad.value:
            return

        # FIXME: probably could use a state where it's still starting ?
        self.setMood(moods.happy)
    
    def startHeartbeat(self):
        """
        Start sending heartbeats.
        """
        self.debug('start sending heartbeats')
        self._heartbeat()

    def stopHeartbeat(self):
        """
        Stop sending heartbeats.
        """
        self.debug('stop sending heartbeats')
        if self._HeartbeatDC:
            self.debug('canceling pending heartbeat')
            self._HeartbeatDC.cancel()
        self._HeartbeatDC = None
         
    def _heartbeat(self):
        """
        Send heartbeat to manager and reschedule.
        """
        #self.log('Sending heartbeat')
        if self.medium:
            self.medium.callRemote('heartbeat', self.state.get('mood'))
        self._HeartbeatDC = reactor.callLater(self._heartbeatInterval,
            self._heartbeat)

        # update CPU time stats
        nowTime = time.time()
        nowClock = time.clock()
        deltaTime = nowTime - self.lastTime
        deltaClock = nowClock - self.lastClock
        CPU = deltaClock/deltaTime
        self.state.set('cpu', CPU)
        deltaTime = nowTime - self.baseTime
        deltaClock = nowClock
        CPU = deltaClock/deltaTime
        self.lastTime = nowTime
        self.lastClock = nowClock

    ### GObject methods
    def emit(self, name, *args):
        if 'uninitialized' in str(self):
            self.warning('Uninitialized object!')
            #self.__gobject_init__()
        else:
            gobject.GObject.emit(self, name, *args)
        
    ### BaseComponent methods
    def getName(self):
        return self.name

    def setWorkerName(self, workerName):
        self.state.set('workerName', workerName)

    def setConfig(self, config):
        self.config = config

    def getWorkerName(self):
        return self.state.get('workerName')

    def setMedium(self, medium):
        assert isinstance(medium, BaseComponentMedium)
        self.medium = medium
        # send a heartbeat right now
        if self._HeartbeatDC:
            self._HeartbeatDC.reset(0)

    def setMood(self, mood):
        """
        Set the given mood on the component if it's different from the current
        one.
        """
        current = self.state.get('mood')

        if current == mood.value:
            self.log('already in mood %r' % mood)
            return
        elif current == moods.sad.value:
            self.info('tried to set mood to %r, but already sad :-(' % mood)
            return

        self.debug('MOOD changed to %r' % mood)
        self.state.set('mood', mood.value)
        # send a heartbeat right now
        if self._HeartbeatDC:
            self._HeartbeatDC.reset(0)
        
    def adminCallRemote(self, methodName, *args, **kwargs):
        """
        Call a remote method on all admin client views on this component.

        This gets serialized through the manager and multiplexed to all
        admin clients, and from there on to all views connected to each
        admin client model.
        """
        self.medium.callRemote("adminCallRemote", methodName, *args, **kwargs)

    # mood change functions
    def do_start(self, *args, **kwargs):
        """
        BaseComponent vmethod for starting up. If you override this
        method, you are responsible for arranging that the component
        becomes happy.
        """
        # default behavior
        self.setMood(moods.happy)
        
    def start(self, *args, **kwargs):
        """
        Tell the component to start.  This is called when all its dependencies
        are already started.

        To hook onto this method, implement your own do_start method.
        See BaseComponent.do_start() for what your do_start method is
        responsible for doing.

        Again, don't override this method. Thanks.
        """
        self.debug('BaseComponent.start')
        self.setMood(moods.waking)

        ret = self.do_start(*args, **kwargs)

        self.debug('start: returning value %s' % ret)

        self.startHeartbeat()

        return ret
        
    def stop(self):
        """
        Tell the component to stop.
        The connection to the manager will be closed.
        The job process will also finish.
        """
        self.debug('BaseComponent.stop')
        self.setMood(moods.sleeping)
        self.stopHeartbeat()

compat.type_register(BaseComponent)
