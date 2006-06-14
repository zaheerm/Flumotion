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

"""
Contains the base class for PB client-side mediums.
"""

from twisted.spread import pb
from twisted.internet import defer

from flumotion.twisted.defer import defer_generator_method
from flumotion.common import log, interfaces, bundleclient, errors, common
from flumotion.twisted.compat import implements

class BaseMedium(pb.Referenceable, log.Loggable):
    """
    I am a base interface for PB clients interfacing with PB server-side
    avatars.
    Used by admin/worker/component to talk to manager's vishnu,
    and by job to talk to worker's brain.

    @ivar remoteReference: L{twisted.spread.pb.RemoteReference}
    @ivar bundleLoader: L{flumotion.common.bundleclient.BundleLoader}
    """

    # subclasses will need to set this to the specific medium type
    # tho...
    implements(interfaces.IMedium)
    logCategory = "basemedium"

    remote = None
    bundleLoader = None

    def setRemoteReference(self, remoteReference):
        """
        Set the given remoteReference as the reference to the server-side
        avatar.

        @param remoteReference: L{twisted.spread.pb.RemoteReference}
        """
        self.debug('%r.setRemoteReference: %r' % (self, remoteReference))
        self.remote = remoteReference
        def nullRemote(x):
            self.info('%r: disconnected from %r' % (self, self.remote))
            self.remote = None
        self.remote.notifyOnDisconnect(nullRemote)

        self.bundleLoader = bundleclient.BundleLoader(self.remote)

        # figure out connection addresses if it's an internet address
        tarzan = None
        jane = None
        try:
            transport = remoteReference.broker.transport
            tarzan = transport.getHost()
            jane = transport.getPeer()
        except Exception, e:
            self.debug("could not get connection info, reason %r" % e)
        if tarzan and jane:
            self.debug("connection is from me on %s to manager on %s" % (
                common.addressGetHost(tarzan),
                common.addressGetHost(jane)))

    def hasRemoteReference(self):
        """
        Does the medium have a remote reference to a server-side avatar ?
        """
        return self.remote != None

    def callRemote(self, name, *args, **kwargs):
        """
        Call the given method with the given arguments remotely on the
        server-side avatar.

        Gets serialized to server-side perspective_ methods.
        """
        if not self.remote:
            self.warning('Tried to callRemote(%s), but we are disconnected'
                         % name)
            return defer.fail(errors.NotConnectedError())
        
        def errback(failure):
            # shouldn't be a warning, since this a common occurrence
            # when running worker tests
            self.debug('callRemote(%s) failed: %r' % (name, failure))
            failure.trap(pb.PBConnectionLost)
        d = self.remote.callRemote(name, *args, **kwargs)
        d.addErrback(errback)
        return d

    def runBundledFunction(self, module, function, *args, **kwargs):
        """
        Runs the given function in the given module with the given arguments.
        
        @param module:   module the function lives in
        @type  module:   str
        @param function: function to run
        @type  function: str

        @returns: the return value of the given function in the module.
        """
        self.debug('remote runFunction(%r, %r)' % (module, function))
        d = self.bundleLoader.loadModule(module)
        yield d

        try:
            mod = d.value()
        except errors.NoBundleError:
            msg = 'Failed to find bundle for module %s' % module
            self.warning(msg)
            raise errors.RemoteRunError(msg)
        except Exception, e:
            msg = 'Failed to load bundle for module %s' % module
            self.debug("exception %r" % e)
            self.warning(msg)
            raise errors.RemoteRunError(msg)

        try:
            proc = getattr(mod, function)
        except AttributeError:
            msg = 'No procedure named %s in module %s' % (function, module)
            self.warning(msg)
            raise errors.RemoteRunError(msg)

        try:
            self.debug('calling %s.%s(%r, %r)' % (
                module, function, args, kwargs))
            d = proc(*args, **kwargs)
        except Exception, e:
            # FIXME: make e.g. GStreamerError nicely serializable, without
            # printing ugly tracebacks
            msg = ('calling %s.%s(*args=%r, **kwargs=%r) failed: %s' % (
                module, function, args, kwargs,
                log.getExceptionMessage(e)))
            self.debug(msg)
            raise errors.RemoteRunError(log.getExceptionMessage(e))
 
        yield d

        try:
            # only if d was actually a deferred will we get here
            # this is a bit nasty :/
            result = d.value()
            if not isinstance(result, messages.Result):
                msg = 'function %r returned a non-Result %r' % (
                    proc, result)
                raise errors.RemoteRunError(msg)

            self.debug('yielding result %r with failed %r' % (result,
                result.failed))
            yield result
        except Exception, e:
            # FIXME: make e.g. GStreamerError nicely serializable, without
            # printing ugly tracebacks
            msg = ('%s.%s(*args=%r, **kwargs=%r) failed: %s' % (
                module, function, args, kwargs,
                log.getExceptionMessage(e)))
            self.debug(msg)
            raise e
    runBundledFunction = defer_generator_method(runBundledFunction)
