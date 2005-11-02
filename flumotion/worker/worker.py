# -*- Mode: Python; test-case-name:flumotion.test.test_worker_worker -*-
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
worker-side objects to handle worker clients
"""

import errno
import os
import signal
import sys

import gst
import gst.interfaces

from twisted.cred import portal
from twisted.internet import defer, protocol, reactor
from twisted.spread import pb
import twisted.cred.error
import twisted.internet.error

from flumotion.common import errors, interfaces, log, bundleclient
from flumotion.common import common, medium
from flumotion.twisted import checkers
from flumotion.twisted import pb as fpb
from flumotion.twisted.defer import defer_generator_method
from flumotion.worker import job
from flumotion.configure import configure

factoryClass = fpb.ReconnectingFPBClientFactory
class WorkerClientFactory(factoryClass):
    """
    I am a client factory for the worker to log in to the manager.
    """
    logCategory = 'worker'
    def __init__(self, brain):
        """
        @type brain: L{flumotion.worker.worker.WorkerBrain}
        """
        self.manager_host = brain.manager_host
        self.manager_port = brain.manager_port
        self.medium = brain.medium
        # doing this as a class method triggers a doc error
        factoryClass.__init__(self)
        # maximum 10 second delay for workers to attempt to log in again
        self.maxDelay = 10
        
    def startLogin(self, keycard):
        factoryClass.startLogin(self, keycard, self.medium,
            interfaces.IWorkerMedium)
        
    # vmethod implementation
    def gotDeferredLogin(self, d):
        def remoteDisconnected(remoteReference):
            if reactor.killed:
                self.log('Connection to manager lost due to SIGINT shutdown')
            else:
                self.warning('Lost connection to manager, '
                             'will attempt to reconnect')

        def loginCallback(reference):
            self.info("Logged in to manager")
            self.debug("remote reference %r" % reference)
           
            self.medium.setRemoteReference(reference)
            reference.notifyOnDisconnect(remoteDisconnected)

        def alreadyConnectedErrback(failure):
            failure.trap(errors.AlreadyConnectedError)
            self.error('A worker with the name "%s" is already connected.' %
                failure.value)

        def accessDeniedErrback(failure):
            failure.trap(twisted.cred.error.UnauthorizedLogin)
            self.error('Access denied.')
            
        def connectionRefusedErrback(failure):
            failure.trap(twisted.internet.error.ConnectionRefusedError)
            self.error('Connection to %s:%d refused.' % (self.manager_host,
                                                         self.manager_port))
                                                          
        def loginFailedErrback(failure):
            self.error('Login failed, reason: %s' % str(failure))

        d.addCallback(loginCallback)
        d.addErrback(accessDeniedErrback)
        d.addErrback(connectionRefusedErrback)
        d.addErrback(alreadyConnectedErrback)
        d.addErrback(loginFailedErrback)
            
    # override log.Loggable method so we don't traceback
    def error(self, message):
        self.warning('Shutting down worker because of error:')
        self.warning(message)
        print >> sys.stderr, 'ERROR: %s' % message
        reactor.stop()

class WorkerMedium(medium.BaseMedium):
    """
    I am a medium interfacing with the manager-side WorkerAvatar.
    """
    
    logCategory = 'workermedium'

    __implements__ = interfaces.IWorkerMedium,
    
    def __init__(self, brain):
        self.brain = brain
        
    def cb_processFinished(self, *args):
        self.debug('processFinished %r' % args)

    def cb_processFailed(self, *args):
        self.debug('processFailed %r' % args)

    ### pb.Referenceable method for the manager's WorkerAvatar
    def remote_start(self, avatarId, type, moduleName, methodName, config):
        """
        Start a component of the given type with the given config.

        @param avatarId:   avatar identification string
        @type  avatarId:   string
        @param type:       type of the component to start
        @type  type:       string
        @param moduleName: name of the module to create the component from
        @type  moduleName: string
        @param methodName: the factory method to use to create the component
        @type  methodName: string
        @param config:     a configuration dictionary for the component
        @type  config:     dict

        @returns: a deferred fired when the process has started
        """

        # from flumotion.common import debug
        # def write(indent, str, *args):
        #     print ('[%d]%s%s' % (os.getpid(), indent, str)) % args
        # debug.trace_start(ignore_files_re='twisted/python/rebuild', write=write)

        self.info('Starting component "%s" of type "%s"' % (avatarId, type))
        self.debug('remote_start(): id %s, type %s, config %r' % (
            avatarId, type, config))

        # set up bundles as we need to have a pb connection to download
        # the modules -- can't do that in the kid yet.
        self.debug('setting up bundles for %s' % moduleName)
        d = self.bundleLoader.loadModule(moduleName)
        yield d
        # check errors, will proxy to the manager
        d.value()

        d = self.brain.deferredStartCreate(avatarId)

        # unwind the stack -- write more about me!
        reactor.callLater(0, self.brain.kindergarten.play,
            avatarId, type, moduleName, methodName, config)

        yield d

        try:
            result = d.value()
            self.debug('deferred start for %s succeeded (%r)'
                       % (avatarId, result))
            yield result
        except Exception, e:
            msg = ('Component "%s" has already received a start request'
                   % avatarId)
            raise errors.ComponentStart(msg)
    remote_start = defer_generator_method(remote_start)

    def remote_checkElements(self, elementNames):
        """
        Checks if one or more GStreamer elements are present and can be
        instantiated.

        @param elementNames:   names of the Gstreamer elements
        @type  elementNames:   list of strings

        @rtype:   list of strings
        @returns: a list of instantiatable element names
        """
        self.debug('remote_checkElements: element names to check %r' % (
            elementNames,))

        list = [name for name in elementNames
                         if gst.element_factory_make(name) != None]
        self.debug('remote_checkElements: returning elements names %r' % list)
        return list

    def remote_runProc(self, module, function, *args, **kwargs):
        """
        Runs the given function in the given module with the given arguments.
        
        @param module:   module the function lives in
        @type  module:   string
        @param function: function to run
        @type  function: string

        @returns: the return value of the given function in the module.
        """
        return self.run_bundled_proc(module, function, *args, **kwargs)


class Kid:
    """
    I am an abstraction of a job process started by the worker.
    """
    def __init__(self, pid, avatarId, type, moduleName, methodName, config):
        self.pid = pid 
        self.avatarId = avatarId
        self.type = type
        self.moduleName = moduleName
        self.methodName = methodName
        self.config = config

    # pid = protocol.transport.pid
    def getPid(self):
        return self.pid

class Kindergarten(log.Loggable):
    """
    I spawn job processes.
    I live in the worker brain.
    """

    logCategory = 'workerbrain' # thomas: I don't like Kindergarten

    def __init__(self, options):
        """
        @param options: the optparse option instance of command-line options
        @type  options: dict
        """
        dirname = os.path.split(os.path.abspath(sys.argv[0]))[0]
        self.program = os.path.join(dirname, 'flumotion-worker')
        self.kids = {} # avatarId -> Kid
        self.options = options
        
    def play(self, avatarId, type, moduleName, methodName, config):
        """
        Create a kid and make it "play" by starting a job.
        Starts a component with the given name, of the given type, and
        the given config dictionary.

        Returns the pid of the child process, or None if returning from
        the child process, just like os.fork().

        @param avatarId:   avatarId the component should use to log in
        @type  avatarId:   string
        @param type:       type of component to start
        @type  type:       string
        @param moduleName: name of the module to create the component from
        @type  moduleName: string
        @param methodName: the factory method to use to create the component
        @type  methodName: string
        @param config:     a configuration dictionary for the component
        @type  config:     dict
        """
        # This forks and returns the pid, or None if we're in the kid
        pid = job.run(avatarId, self.options)

        if not pid:
            # this is the kid, just return so we can unwind the stack
            # back to the reactor
            return None

        # we're the parent
        self.kids[avatarId] = \
            Kid(pid, avatarId, type, moduleName, methodName, config)

        return pid

    def getKid(self, avatarId):
        return self.kids[avatarId]
    
    def getKids(self):
        return self.kids.values()

    def removeKidByPid(self, pid):
        """
        Remove the kid from the kindergarten based on the pid.
        Called by the signal handler in the brain.

        @returns: whether or not a kid with that pid was removed
        @rtype: boolean
        """
        for path, kid in self.kids.items():
            if kid.getPid() == pid:
                self.debug('Removing kid with name %s and pid %d' % (
                    path, pid))
                del self.kids[path]
                return True

        self.warning('Asked to remove kid with pid %d but not found' % pid)
        return False

# Similar to Vishnu, but for worker related classes
class WorkerBrain(log.Loggable):
    """
    I manage jobs and everything related.
    I live in the main worker process.
    """

    logCategory = 'workerbrain'

    def __init__(self, options):
        """
        @param options: the optparsed dictionary of command-line options
        @type  options: an object with attributes
        """
        self._port = None
        self._oldSIGCHLDHandler = None # stored by installSIGCHLDHandler
        self._oldSIGTERMHandler = None # stored by installSIGTERMHandler
        self.options = options

        # we used to ignore SIGINT from here on down, but actually
        # the reactor catches these properly in both 1.3 and 2.0,
        # and in 2.0 setting it to ignore first will make the reactor
        # not catch it (because it compares to the default int handler)
        # signal.signal(signal.SIGINT, signal.SIG_IGN)

        self.manager_host = options.host
        self.manager_port = options.port
        self.manager_transport = options.transport

        self.workerName = options.name
        
        self.kindergarten = Kindergarten(options)
        self.job_server_factory, self.job_heaven = self.setup()

        self.medium = WorkerMedium(self)
        self.worker_client_factory = WorkerClientFactory(self)

        self._startDeferreds = {}

    def login(self, keycard):
        self.worker_client_factory.startLogin(keycard)
                             
    def setup(self):
        # called from Init
        root = JobHeaven(self, self.options.feederports)
        dispatcher = JobDispatcher(root)
        # FIXME: we should hand a username and password to log in with to
        # the job process instead of allowing anonymous
        checker = checkers.FlexibleCredentialsChecker()
        checker.allowPasswordless(True)
        p = portal.Portal(dispatcher, [checker])
        job_server_factory = pb.PBServerFactory(p)
        self._port = reactor.listenUNIX(job.getSocketPath(), job_server_factory)

        return job_server_factory, root

    def teardown(self):
        """
        Clean up after setup()

        @Returns: a L{twisted.internet.defer.Deferred} that fires when
                  the teardown is completed
        """
        self.debug("cleaning up port %r" % self._port)
        return self._port.stopListening()

    # override log.Loggable method so we don't traceback
    def error(self, message):
        self.warning('Shutting down worker because of error:')
        self.warning(message)
        print >> sys.stderr, 'ERROR: %s' % message
        reactor.stop()

    def installSIGCHLDHandler(self):
        """
        Install our own signal handler for SIGCHLD.
        This will call the currently installed one first, then reap
        any leftover zombies.
        """
        self.debug("Installing SIGCHLD handler")
        handler = signal.signal(signal.SIGCHLD, self._SIGCHLDHandler)
        if handler not in (signal.SIG_IGN, signal.SIG_DFL, None):
            self._oldSIGCHLDHandler = handler

    def _SIGCHLDHandler(self, signum, frame):
        self.debug("handling SIGCHLD")
        if self._oldSIGCHLDHandler:
            self.debug("calling Twisted handler")
            self._oldSIGCHLDHandler(signum, frame)
            
        # we could have been called for more than one kid, so handle all
        reaped = False

        while True:
            # find a pid that needs reaping
            # only allow ECHILD to pass, which means no children needed reaping
            pid = 0
            try:
                self.debug('calling os.waitpid to reap children')
                pid, status = os.waitpid(-1, os.WNOHANG)
                self.debug('os.waitpid() returned pid %d' % pid)
                reaped = True
            except OSError, e:
                if not e.errno == errno.ECHILD:
                    raise
                
            # check if we reaped a child or not
            # if we reaped none at all in this handling, something shady went
            # on, so we info
            if not pid:
                if not reaped:
                    self.info('No children of mine to wait on')
                else:
                    self.debug('Done reaping children')
                return

            # we reaped, so ...
            # remove from the kindergarten
            self.kindergarten.removeKidByPid(pid)

            # check if it exited nicely; see Stevens
            if os.WIFEXITED(status):
                retval = os.WEXITSTATUS(status)
                self.info("Reaped child job with pid %d, exit value %d" % (
                    pid, retval))
            elif os.WIFSIGNALED(status):
                signum = os.WTERMSIG(status)
                if signum == signal.SIGSEGV:
                    self.warning("Job child with pid %d segfaulted" % pid)
                    if not os.WCOREDUMP(status):
                        self.warning(
                            "No core dump generated.  "\
                            "Were core dumps enabled at the start ?")
                else:
                    self.info(
                        "Reaped job child with pid %d signaled by signal %d" % (
                            pid, signum))
                if os.WCOREDUMP(status):
                    self.info("Core dumped (in %s)" % os.getcwd())
                    
            elif os.WIFSTOPPED(status):
                signum = os.WSTOPSIG(status)
                self.info(
                    "Reaped job child with pid %d stopped by signal %d" % (
                        pid, signum))
            else:
                self.info(
                    "Reaped job child with pid %d and unhandled status %d" % (
                        pid, status))

    def installSIGTERMHandler(self):
        """
        Install our own signal handler for SIGTERM.
        This will call the currently installed one first, then shut down
        jobs.
        """
        self.debug("Installing SIGTERM handler")
        handler = signal.signal(signal.SIGTERM, self._SIGTERMHandler)
        if handler not in (signal.SIG_IGN, signal.SIG_DFL, None):
            self._oldSIGTERMHandler = handler

    def _SIGTERMHandler(self, signum, frame):
        self.info("Worker daemon received TERM signal, shutting down")
        self.debug("handling SIGTERM")
        reactor.killed = True
        self.debug("_SIGTERMHandler: shutting down jobheaven")
        d = self.job_heaven.shutdown()

        if self._oldSIGTERMHandler:
            if d:
                self.debug("chaining Twisted handler")
                d.addCallback(lambda result: self._oldSIGTERMHandler(signum, frame))
            else:
                self.debug("calling Twisted handler")
                self._oldSIGTERMHandler(signum, frame)

        self.debug("_SIGTERMHandler: done")

    def deferredStartCreate(self, avatarId):
        """
        Create and register a deferred for starting up the given component.
        This deferred will be fired when the JobAvatar has instructed the
        job to start the component.
        """
        self.debug('creating start deferred for %s' % avatarId)
        if avatarId in self._startDeferreds.keys():
            self.warning('Already a start deferred registered for %s' %
                avatarId)
            return None

        d = defer.Deferred()
        self._startDeferreds[avatarId] = d
        return d

    def deferredStartTrigger(self, avatarId):
        """
        Trigger a previously registered deferred for starting up the given
        component.
        """
        self.debug('triggering start deferred for %s' % avatarId)
        if not avatarId in self._startDeferreds.keys():
            self.warning('No deferred start registered for %s' % avatarId)
            return

        d = self._startDeferreds[avatarId]
        del self._startDeferreds[avatarId]
        # return the avatarId the component will use to the original caller
        d.callback(avatarId)
 
    def deferredStartFailed(self, avatarId, failure):
        """
        Notify the caller that a start has failed, and remove the start
        from the list of pending starts.
        """
        self.debug('deferred start failed for %s' % avatarId)
        assert avatarId in self._startDeferreds.keys()

        d = self._startDeferreds[avatarId]
        del self._startDeferreds[avatarId]
        d.errback(failure)
 
class JobDispatcher:
    """
    I am a Realm inside the worker for forked jobs to log in to.
    """
    __implements__ = portal.IRealm
    
    def __init__(self, root):
        """
        @type root: L{flumotion.worker.worker.JobHeaven}
        """
        self.root = root
        
    ### portal.IRealm methods
    # flumotion-worker job processes log in to us.
    # The mind is a RemoteReference which allows the brain to call back into
    # the job.
    # the avatar id is of the form /(parent)/(name) 
    def requestAvatar(self, avatarId, mind, *interfaces):
        if pb.IPerspective in interfaces:
            avatar = self.root.createAvatar(avatarId)
            reactor.callLater(0, avatar.attached, mind)
            return pb.IPerspective, avatar, avatar.logout
        else:
            raise NotImplementedError("no interface")

class Port:
    """
    I am an abstraction of a local TCP port which will be used by GStreamer.
    """
    def __init__(self, number):
        self.number = number
        self.used = False

    def free(self):
        self.used = False

    def use(self):
        self.used = True

    def isFree(self):
        return self.used is False

    def getNumber(self):
        return self.number

    def __repr__(self):
        if self.isFree():
            return '<Port %d (unused)>' % self.getNumber()
        else:
            return '<Port %d (used)>' % self.getNumber()

class JobAvatar(pb.Avatar, log.Loggable):
    """
    I am an avatar for the job living in the worker.
    """
    logCategory = 'job-avatar'

    def __init__(self, heaven, avatarId):
        """
        @type  heaven:   L{flumotion.worker.worker.JobHeaven}
        @type  avatarId: string
        """
        
        self.heaven = heaven
        self.avatarId = avatarId
        self.mind = None
        self.debug("created new JobAvatar")
        
        self.feeds = []
            
    def hasRemoteReference(self):
        """
        Check if the avatar has a remote reference to the peer.

        @rtype: boolean
        """
        return self.mind != None

    def attached(self, mind):
        """
        @param mind: reference to the job's JobMedium on which we can call
        @type mind: L{twisted.spread.pb.RemoteReference}
        
        I am scheduled from the dispatcher's requestAvatar method.
        """
        self.mind = mind
        self.log('Client attached mind %s' % mind)
        host = self.heaven.brain.manager_host
        port = self.heaven.brain.manager_port
        transport = self.heaven.brain.manager_transport
        cb = self.mind.callRemote('initial', host, port, transport)
        cb.addCallback(self._cb_afterInitial)

    def _getFreePort(self):
        for port in self.heaven.ports:
            if port.isFree():
                port.use()
                return port

        # XXX: Raise better error message
        raise AssertionError
    
    def _defaultErrback(self, failure):
        self.warning('unhandled remote error: type %s, message %s' % (
            failure.type, failure.getErrorMessage()))
        
    def _startErrback(self, failure, avatarId, type):
        failure.trap(errors.ComponentStart)
        
        self.warning('could not start component %s of type %s: %r' % (
            avatarId, type, failure.getErrorMessage()))
        self.heaven.brain.deferredStartFailed(avatarId, failure)

    def _cb_afterInitial(self, unused):
        kid = self.heaven.brain.kindergarten.getKid(self.avatarId)
        # we got kid.config through WorkerMedium.remote_start from the manager
        feedNames = kid.config.get('feed', [])
        self.log('_cb_afterInitial(): feedNames %r' % feedNames)

        # This is going to be sent to the component
        feedPorts = {} # feedName -> port number
        # This is saved, so we can unmark the ports when shutting down
        self.feeds = []
        for feedName in feedNames:
            port = self._getFreePort()
            feedPorts[feedName] = port.getNumber()
            self.debug('reserving port %r for feed %s' % (port, feedName))
            self.feeds.append((feedName, port))
            
        self.debug('asking job to start with config %r and feedPorts %r' % (
            kid.config, feedPorts))
        d = self.mind.callRemote('start', kid.avatarId, kid.type,
            kid.moduleName, kid.methodName, kid.config, feedPorts)

        # make sure that we trigger the start deferred after this
        d.addCallback(
            lambda result, n: self.heaven.brain.deferredStartTrigger(n),
                kid.avatarId)
        d.addErrback(self._startErrback, kid.avatarId, kid.type)
        d.addErrback(self._defaultErrback)
                                          
    def logout(self):
        self.log('logout called, %s disconnected' % self.avatarId)
        self.mind = None
        for feed, port in self.feeds:
            port.free()
        self.feeds = []
        
    def stop(self):
        """
        returns: a deferred marking completed stop.
        """
        self.debug('stopping %s' % self.avatarId)
        if not self.mind:
            return defer.succeed(None)
        
        return self.mind.callRemote('stop')
        
    def remote_ready(self):
        pass

### this is a different kind of heaven, not IHeaven, for now...
class JobHeaven(pb.Root, log.Loggable):
    """
    I am similar to but not quite the same as a manager-side Heaven.
    I manage avatars inside the worker for job processes forked by the worker.
    """
    logCategory = "job-heaven"
    def __init__(self, brain, feederports):
        self.avatars = {}
        self.brain = brain
        self.feederports = feederports

        # FIXME: use and option from the command line for port range
        # Allocate ports
        self.ports = []
        for port in self.feederports:
            self.ports.append(Port(port))
        
    def createAvatar(self, avatarId):
        avatar = JobAvatar(self, avatarId)
        self.avatars[avatarId] = avatar
        return avatar

    def shutdown(self):
        self.debug('Shutting down JobHeaven')
        self.debug('Stopping all jobs')
        dl = defer.DeferredList([x.stop() for x in self.avatars.values()])
        dl.addCallback(lambda result: self.debug('Stopped all jobs'))
        return dl
