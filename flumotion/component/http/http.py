# -*- Mode: Python; test-case-name: flumotion.test.test_http -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# flumotion/component/http/http.py: a consumer that streams over HTTP
# Flumotion - a streaming media server
# Copyright (C) 2004 Fluendo (www.fluendo.com)

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# See "LICENSE.GPL" in the source distribution for more information.

# This program is also licensed under the Flumotion license.
# See "LICENSE.Flumotion" in the source distribution for more information.

import os
import random
import time
import thread
import errno

import gobject
import gst

from twisted.protocols import http
from twisted.web import server, resource
from twisted.internet import reactor, defer
from twisted.cred import credentials
import twisted.internet.error

from flumotion.component import feedcomponent
from flumotion.common import auth, bundle, common, interfaces, keycards
from flumotion.utils import gstutils, log
from flumotion.utils.gstutils import gsignal

__all__ = ['HTTPStreamingAdminResource',
           'HTTPStreamingResource', 'MultifdSinkStreamer']

HTTP_NAME = 'FlumotionHTTPServer'
HTTP_VERSION = '0.1.0'

ERROR_TEMPLATE = """<!doctype html public "-//IETF//DTD HTML 2.0//EN">
<html>
<head>
  <title>%(code)d %(error)s</title>
</head>
<body>
<h2>%(code)d %(error)s</h2>
</body>
</html>
"""

STATS_TEMPLATE = """<!doctype html public "-//IETF//DTD HTML 2.0//EN">
<html>
<head>
  <title>Statistics for %(name)s</title>
</head>
<body>
<table>
%(stats)s
</table>
</body>
</html>
"""

HTTP_VERSION = '%s/%s' % (HTTP_NAME, HTTP_VERSION)

# implements a Resource for the HTTP admin interface
class HTTPStreamingAdminResource(resource.Resource):
    def __init__(self, parent):
        'call with a HTTPStreamingResource to admin for'
        self.parent = parent
        self.debug = self.parent.debug
        resource.Resource.__init__(self)

    ### resource.Resource methods

    def getChild(self, path, request):
        return self
   
    def render(self, request):
        self.debug('Request for admin page')
        if not self.isAuthenticated(request):
            self.debug('Unauthorized request for /admin from %s' % request.getClientIP())
            error_code = http.UNAUTHORIZED
            request.setResponseCode(error_code)
            request.setHeader('server', HTTP_VERSION)
            request.setHeader('content-type', 'text/html')
            request.setHeader('WWW-Authenticate', 'Basic realm="Restricted Access"')

            return ERROR_TEMPLATE % {'code': error_code,
                                     'error': http.RESPONSES[error_code]}

        return self._render_stats(request)

    ### our methods

    # FIXME: file has this too - move upperclass ?
    def isAuthenticated(self, request):
        if request.getClientIP() == '127.0.0.1':
            return True
        
        if (request.getUser() == 'admin' and
            request.getPassword() == self.parent.admin_password):
            return True
        return False
     
    def _render_stats(self, request):
        streamer = self.parent.streamer
        s = streamer.getState()
        
        def row(label, value):
            return '<tr><td>%s</td><td>%s</td></tr>' % (label, value)
        block = []

        block.append('<tr><td colspan=2><b>Stream</b></td></tr>')
        block.append('<tr>')
        block.append(row('Mime type',   s['stream-mime']))
        block.append(row('Uptime',      s['stream-uptime']))
        block.append(row('Bit rate',    s['stream-bitrate']))
        block.append(row('Total bytes', s['stream-totalbytes']))
        block.append('</tr>')

        block.append('<tr><td colspan=2><b>Clients</b></td></tr>')
        block.append('<tr>')
        current = s['clients-current']
        max = s['clients-max']
        block.append(row('Current', "%s (of %s) " % (current, max)))
        block.append(row('Average', s['clients-average']))
        peak = s['clients-peak']
        time = s['clients-peak-time']
        block.append(row('Peak',    "%s (at %s) " % (peak, time)))
        block.append('</tr>')

        block.append('<tr><td colspan=2><b>Client consumption</b></td></tr>')
        block.append('<tr>')
        block.append(row('Bit rate',    s['consumption-bitrate']))
        block.append(row('Total bytes', s['consumption-totalbytes']))
        block.append('</tr>')
         
        return STATS_TEMPLATE % {
            'name': streamer.get_name(),
            'stats': "\n".join(block)
        }

# FIXME: generalize this class and move it out here ?
class Stats:
    def __init__(self, sink):
        self.sink = sink
        
        self.no_clients = 0        
        self.start_time = time.time()
        # keep track of the highest number and the last epoch this was reached
        self.peak_client_number = 0 
        self.peak_epoch = self.start_time

        # keep track of average clients by tracking last average and its time
        self.average_client_number = 0
        self.average_time = self.start_time
        
    def _updateAverage(self):
        # update running average of clients connected
        now = time.time()
        # calculate deltas
        dt1 = self.average_time - self.start_time
        dc1 = self.average_client_number
        dt2 = now - self.average_time
        dc2 = self.no_clients
        self.average_time = now # we can update now that we used self.av
        if dt1 == 0:
            # first measurement
            self.average_client_number = 0
        else:
            self.average_client_number = (dc1 * dt1 / (dt1 + dt2) +
                                          dc2 * dt2 / (dt1 + dt2))

    def clientAdded(self):
        self._updateAverage()

        self.no_clients += 1

        # >= so we get the last epoch this peak was achieved
        if self.no_clients >= self.peak_client_number:
            self.peak_epoch = time.time()
            self.peak_client_number = self.no_clients
    
    def clientRemoved(self):
        self._updateAverage()
        self.no_clients -= 1

    def getBytesSent(self):
        return self.sink.get_property('bytes-served')
    
    def getBytesReceived(self):
        return self.sink.get_property('bytes-to-serve')
    
    def getUptime(self):
        return time.time() - self.start_time
    
    def getClients(self):
        return self.no_clients
    
    def getPeakClients(self):
        return self.peak_client_number

    def getPeakEpoch(self):
        return self.peak_epoch
    
    def getAverageClients(self):
        return self.average_client_number

    def getState(self):
        c = self
        s = {}
 
        bytes_sent      = c.getBytesSent()
        bytes_received  = c.getBytesReceived()
        uptime          = c.getUptime()

        s['stream-mime'] = c.get_mime()
        s['stream-uptime'] = common.formatTime(uptime)
        bitspeed = bytes_received * 8 / uptime
        s['stream-bitrate'] = common.formatStorage(bitspeed) + 'bit/s'
        s['stream-totalbytes'] = common.formatStorage(bytes_received) + 'Byte'

        s['clients-current'] = str(c.getClients())
        s['clients-max'] = str(c.getMaxClients())
        s['clients-peak'] = str(c.getPeakClients())
        s['clients-peak-time'] = time.ctime(c.getPeakEpoch())
        s['clients-average'] = str(int(c.getAverageClients()))

        bitspeed = bytes_sent * 8 / uptime
        s['consumption-bitrate'] = common.formatStorage(bitspeed) + 'bit/s'
        s['consumption-totalbytes'] = common.formatStorage(bytes_sent) + 'Byte'

        return s

### the Twisted resource that handles the base URL
class HTTPStreamingResource(resource.Resource, log.Loggable):
    __reserve_fds__ = 50 # number of fd's to reserve for non-streaming

    logCategory = 'httpstreamer'
    
    def __init__(self, streamer):
        """
        @param streamer: L{MultifdSinkStreamer}
        """
        self.logfile = None
        self.admin_password = None
            
        streamer.connect('client-removed', self._streamer_client_removed_cb)
        self.streamer = streamer
        self.admin = HTTPStreamingAdminResource(self)
        
        self._requests = {}         # request fd -> Request
        self._fdToKeycard = {}      # request fd -> Keycard
        self._idToKeycard = {}      # keycard id -> Keycard
        self._fdToDurationCall = {} # request fd -> IDelayedCall for duration
        self.bouncerName = None
        self.auth = None
        
        self.maxclients = -1
        
        resource.Resource.__init__(self)

    def _streamer_client_removed_cb(self, streamer, sink, fd, reason, stats):
        try:
            request = self._requests[fd]
            self._removeClient(request, fd, stats)
        except KeyError:
            self.warning('[fd %5d] not found in _requests' % fd)

        
    def setLogfile(self, logfile):
        self.logfile = open(logfile, 'a')
        
    def logWrite(self, fd, ip, request, stats):
        if not self.logfile:
            return

        headers = request.getAllHeaders()

        if stats:
            bytes_sent = stats[0]
            time_connected = int(stats[3] / gst.SECOND)
        else:
            bytes_sent = -1
            time_connected = -1

        # ip address
        # ident
        # authenticated name (from http header)
        # date
        # request
        # request response
        # bytes sent
        # referer
        # user agent
        # time connected
        ident = '-'
        username = '-'
        date = time.strftime('%d/%b/%Y:%H:%M:%S %z', time.localtime())
        request_str = '%s %s %s' % (request.method,
                                    request.uri,
                                    request.clientproto)
        response = request.code
        referer = headers.get('referer', '-')
        user_agent = headers.get('user-agent', '-')
        format = "%s %s %s [%s] \"%s\" %d %d %s \"%s\" %d\n"
        msg = format % (ip, ident, username, date, request_str,
                        response, bytes_sent, referer, user_agent,
                        time_connected)
        self.logfile.write(msg)
        self.logfile.flush()

    def setAuth(self, auth):
        self.auth = auth

    def setMaxClients(self, maxclients):
        self.info('setting maxclients to %d' % maxclients)
        self.maxclients = maxclients

    def setAdminPassword(self, password):
        self.admin_password = password

    def setBouncerName(self, bouncerName):
        self.bouncerName = bouncerName

    # FIXME: rename to writeHeaders
    """
    @rtype: boolean
    @returns: whether or not the file descriptor can be used further.
    """
    def _writeHeaders(self, request):
        fd = request.transport.fileno()
        headers = []
        def setHeader(field, name):
            headers.append('%s: %s\r\n' % (field, name))

        # Mimic Twisted as close as possible
        setHeader('Server', HTTP_VERSION)
        setHeader('Date', http.datetimeToString())
        setHeader('Cache-Control', 'no-cache')
        setHeader('Cache-Control', 'private')
        setHeader('Content-type', self.streamer.get_content_type())
            
        #self.debug('setting Content-type to %s' % mime)
        ### FIXME: there's a window where Twisted could have removed the
        # fd because the client disconnected.  Catch EBADF correctly here.
        try:
            os.write(fd, 'HTTP/1.0 200 OK\r\n%s\r\n' % ''.join(headers))
            return True
        except OSError, (no, s):
            if no == errno.EBADF:
                self.warning('[fd %5d] client gone before writing header' % fd)
            else:
                self.warning('[fd %5d] unhandled write error: %s' % (fd, s))
            return False

    def isReady(self):
        if self.streamer.caps is None:
            self.debug('We have no caps yet')
            return False
        
        return True

    def maxAllowedClients(self):
        """
        maximum number of allowed clients based on soft limit for number of
        open file descriptors and fd reservation
        """
        if self.maxclients != -1:
            return self.maxclients
        else:
            from resource import getrlimit, RLIMIT_NOFILE
            limit = getrlimit(RLIMIT_NOFILE)
            return limit[0] - self.__reserve_fds__

    def reachedMaxClients(self):
        return len(self._requests) >= self.maxAllowedClients()
    
    def authenticate(self, request):
        """
        Returns: a deferred returning a keycard or None
        """
        keycard = keycards.HTTPClientKeycard(
            self.streamer.get_name(), request.getUser(),
            request.getPassword(), request.getClientIP())
        keycard._fd = request.transport.fileno()
        
        if self.bouncerName is None:
            return defer.succeed(keycard)

        return self.streamer.medium.authenticate(self.bouncerName, keycard)

    def _addClient(self, request):
        """
        Add a request, so it can be used for statistics.

        @param request: the request
        @type request: twisted.protocol.http.Request
        """

        fd = request.transport.fileno()
        self._requests[fd] = request

    def _removeClient(self, request, fd, stats):
        """
        Removes a request and add logging.
        Note that it does not disconnect the client; it is called in reaction
        to a client disconnecting.
        
        @param request: the request
        @type request: twisted.protocol.http.Request
        @param fd: the file descriptor for the client being removed
        @type fd: L{int}
        @param stats: the statistics for the removed client
        @type stats: GValueArray
        """

        ip = request.getClientIP()
        self.logWrite(fd, ip, request, stats)
        self.info('[fd %5d] client from %s disconnected' % (fd, ip))
        request.finish()
        del self._requests[fd]
        if self.bouncerName and self._fdToKeycard.has_key(fd):
            id = self._fdToKeycard[fd].id
            del self._fdToKeycard[fd]
            del self._idToKeycard[id]
            self.streamer.medium.removeKeycard(self.bouncerName, id)
        if self._fdToDurationCall.has_key(fd):
            self.debug("canceling later expiration on fd %d" % fd)
            self._fdToDurationCall[fd].cancel()
            del self._fdToDurationCall[fd]

    def _durationCallLater(self, fd):
        """
        Expire a client due to a duration expiration.
        """
        self.debug("duration exceeded, expiring client on fd %d" % fd)

        # we're called from a callLater, so we've already run; just delete
        if self._fdToDurationCall.has_key(fd):
            del self._fdToDurationCall[fd]
            
        self.streamer.remove_client(fd)

    def expireKeycard(self, keycardId):
        """
        Expire a client's connection associated with the keycard Id.
        """
        self.debug("expiring client with keycard Id" % keycardId)

        keycard = self._idToKeycard[keycardId]
        fd = keycard._fd

        if self._fdToDurationCall.has_key(fd):
            self.debug("canceling later expiration on fd %d" % fd)
            self._fdToDurationCall[fd].cancel()
            del self._fdToDurationCall[fd]

        self.streamer.remove_client(fd)

    def _handleNotReady(self, request):
        self.debug('Not sending data, it\'s not ready')
        return server.NOT_DONE_YET
        
    def _handleMaxClients(self, request):
        self.debug('Refusing clients, client limit %d reached' % self.maxAllowedClients())

        request.setHeader('content-type', 'text/html')
        request.setHeader('server', HTTP_VERSION)
        
        error_code = http.SERVICE_UNAVAILABLE
        request.setResponseCode(error_code)
        
        return ERROR_TEMPLATE % {'code': error_code,
                                 'error': http.RESPONSES[error_code]}
        
    def _handleUnauthorized(self, request):
        self.debug('client from %s is unauthorized' % (request.getClientIP()))
        request.setHeader('content-type', 'text/html')
        request.setHeader('server', HTTP_VERSION)
        if self.auth:
            request.setHeader('WWW-Authenticate',
                              'Basic realm="%s"' % self.auth.getDomain())
            
        error_code = http.UNAUTHORIZED
        request.setResponseCode(error_code)
        
        # we have to write data ourselves, since we already returned NOT_DONE_YET
        html = ERROR_TEMPLATE % {'code': error_code,
                                 'error': http.RESPONSES[error_code]}
        request.write(html)
        request.finish()

    def _handleNewClient(self, request):
        # everything fulfilled, serve to client
        self._writeHeaders(request)
        self._addClient(request)
        fd = request.transport.fileno()
        
        # take over the file descriptor from Twisted by removing them from
        # the reactor
        # spiv told us to remove* on request.transport, and that works
        reactor.removeReader(request.transport)
        reactor.removeWriter(request.transport)
    
        # hand it to multifdsink
        self.streamer.add_client(fd)
        ip = request.getClientIP()
        self.info('[fd %5d] start streaming to %s' % (fd, ip))

    ### resource.Resource methods

    def _render(self, request):
        fd = request.transport.fileno()
        self.debug('[fd %5d] _render(): client from %s connected, request %s' %
            (fd, request.getClientIP(), request))

        if not self.isReady():
            return self._handleNotReady(request)
        elif self.reachedMaxClients():
            return self._handleMaxClients(request)

        d = self.authenticate(request)
        d.addCallback(self._authenticatedCallback, request)
        self.debug('_render(): asked for authentication')
        # FIXME
        #d.addErrback()

        # we MUST return this from our _render.
        # FIXME: check if this is true
        # FIXME: check how we later handle not authorized
        return server.NOT_DONE_YET

    def _authenticatedCallback(self, keycard, request):
        self.debug('_authenticatedCallback: keycard %r' % keycard)
        if not keycard:
            self._handleUnauthorized(request)
            return

        # properly authenticated
        if request.method == 'GET':
            fd = request.transport.fileno()

            if self.bouncerName:
                self._fdToKeycard[fd] = keycard
                self._idToKeycard[keycard.id] = keycard

            if keycard.duration:
                self.debug('new connection on %d will be expired in %f seconds' % (fd, keycard.duration))
                self._fdToDurationCall[fd] = reactor.callLater(keycard.duration, self._durationCallLater, fd)

            self._handleNewClient(request)

        elif request.method == 'HEAD':
            self.debug('handling HEAD request')
            self._writeHeaders(request)
            # tell Twisted we already wrote headers ourselves
            request.startedWriting = True
            request.finish()

        else:
            raise AssertionError

    render_GET = _render
    render_HEAD = _render
    
    def render_PROPFIND(self, request):
        return http.NOT_ALLOWED
    
    def getChild(self, path, request):
        if path == 'stats':
            return self.admin
        return self

class HTTPMedium(feedcomponent.FeedComponentMedium):
    def __init__(self, comp):
        """
        @type comp: L{Stats}
        """
        feedcomponent.FeedComponentMedium.__init__(self, comp)

        self.comp.connect('ui-state-changed', self._comp_ui_state_changed_cb)

    def getState(self):
        return self.comp.getState()

    def _comp_ui_state_changed_cb(self, comp):
        self.callRemote('uiStateChanged', self.comp.get_name(), self.getState())

    def authenticate(self, bouncerName, keycard):
        """
        @rtype: L{twisted.internet.defer.Deferred}
        """
        return self.callRemote('authenticate', bouncerName, keycard)

    def removeKeycard(self, bouncerName, keycardId):
        """
        @rtype: L{twisted.internet.defer.Deferred}
        """
        return self.callRemote('removeKeycard', bouncerName, keycardId)

    def remote_expireKeycard(self, keycardId):
        self.comp.expireKeycard(keycardId)

### the actual component is a streamer using multifdsink
class MultifdSinkStreamer(feedcomponent.ParseLaunchComponent, Stats):
    # this object is given to the HTTPMedium as comp
    logCategory = 'cons-http'
    # use select for test
    pipe_template = 'multifdsink name=sink ' + \
                                'buffers-max=500 ' + \
                                'buffers-soft-max=250 ' + \
                                'sync-clients=TRUE ' + \
                                'recover-policy=3'

    gsignal('client-removed', object, int, int, object)
    gsignal('ui-state-changed')
    
    component_medium_class = HTTPMedium

    def __init__(self, name, source, port):
        self.port = port
        self.gst_properties = []
        feedcomponent.ParseLaunchComponent.__init__(self, name, [source], [],
                                                self.pipe_template)
        Stats.__init__(self, sink=self.get_sink())
        self.caps = None
        self.resource = None

        # handled regular updating
        self.needsUpdate = False
        # FIXME: call self._callLaterId.cancel() somewhere on shutdown
        self._callLaterId = reactor.callLater(1, self._checkUpdate)

        # handle added and removed queue
        self._added_lock = thread.allocate_lock()
        self._added_queue = []
        self._removed_lock = thread.allocate_lock()
        self._removed_queue = []
        # FIXME: do a .cancel on this Id somewhere
        self._queueCallLaterId = reactor.callLater(0.1, self._handleQueue)
        
    def __repr__(self):
        return '<MultifdSinkStreamer (%s)>' % self.component_name

    # UI code
    def _checkUpdate(self):
        if self.needsUpdate == True:
            self.needsUpdate = False
            self.update_ui_state()
        self._callLaterId = reactor.callLater(1, self._checkUpdate)

    def getMaxClients(self):
        return self.resource.maxAllowedClients()

    def remote_notifyState(self):
        self.update_ui_state()

    def _notify_caps_cb(self, element, pad, param):
        caps = pad.get_negotiated_caps()
        if caps is None:
            return
        
        caps_str = gstutils.caps_repr(caps)
        self.debug('Got caps: %s' % caps_str)
        
        if not self.caps is None:
            self.warn('Already had caps: %s, replacing' % caps_str)

        self.debug('Storing caps: %s' % caps_str)
        self.caps = caps
        
        self.update_ui_state()
        
    def get_mime(self):
        if self.caps:
            return self.caps.get_structure(0).get_name()

    def get_content_type(self):
        mime = self.get_mime()
        if mime == 'multipart/x-mixed-replace':
            mime += ";boundary=ThisRandomString"
        return mime
    
    def add_client(self, fd):
        sink = self.get_sink()
        stats = sink.emit('add', fd)

    def remove_client(self, fd):
        sink = self.get_sink()
        stats = sink.emit('remove', fd)

    def get_sink(self):
        assert self.pipeline, 'Pipeline not created'
        sink = self.pipeline.get_by_name('sink')
        assert sink, 'No sink element in pipeline'
        assert isinstance(sink, gst.Element)
        return sink

    def update_ui_state(self):
        self.emit('ui-state-changed')

    # handle the thread deserializing queues
    def _handleQueue(self):

        # handle added clients
        self._added_lock.acquire()

        while self._added_queue:
            (sink, fd) = self._added_queue.pop()
            self._added_lock.release()
            self._client_added_handler(sink, fd)
            self._added_lock.acquire()

        self._added_lock.release()

        # handle removed clients
        self._removed_lock.acquire()

        while self._removed_queue:
            (sink, fd, reason, stats) = self._removed_queue.pop()
            self._removed_lock.release()
            self._client_removed_handler(sink, fd, reason, stats)
            self._removed_lock.acquire()

        self._removed_lock.release()
         
        self._queueCallLaterId = reactor.callLater(0.1, self._handleQueue)

    def _client_added_handler(self, sink, fd):
        self.log('[%5d] client_added_handler from thread %d' % 
            (fd, thread.get_ident())) 
        Stats.clientAdded(self)
        # FIXME: GIL problem, don't update UI for now
        self.needsUpdate = True
        #self.update_ui_state()
        
    def _client_removed_handler(self, sink, fd, reason, stats):
        self.log('[fd %5d] client_removed_handler from thread %d, reason %s' %
            (fd, thread.get_ident(), reason)) 
        # Johan will trap GST_CLIENT_STATUS_ERROR here someday
        # because STATUS_ERROR seems to have already closed the fd somewhere
        self.emit('client-removed', sink, fd, reason, stats)
        Stats.clientRemoved(self)
        # FIXME: GIL problem, don't update UI for now
        self.needsUpdate = True
        #self.update_ui_state()

    ### START OF THREAD-AWARE CODE

    # this can be called from both application and streaming thread !
    def _client_added_cb(self, sink, fd):
        self._added_lock.acquire()
        self._added_queue.append((sink, fd))
        self._added_lock.release()

    # this can be called from both application and streaming thread !
    def _client_removed_cb(self, sink, fd, reason):
        self._removed_lock.acquire()
        # commented out to see if it solves GIL problems
        #stats = sink.emit('get-stats', fd)
        stats = None
        self._removed_queue.append((sink, fd, reason, stats))
        self._removed_lock.release()

    ### END OF THREAD-AWARE CODE

    # FIXME: a streamer doesn't have feeders, so shouldn't call the base
    # method; right now this is done so the manager knows it started.
    # fix this by implementing concept of "moods" for components
    def _sink_state_change_cb(self, element, old, state):
        feedcomponent.FeedComponent.feeder_state_change_cb(self, element,
                                                     old, state, '')
        if state == gst.STATE_PLAYING:
            self.debug('Ready to serve clients on %d' % self.port)

    def link_setup(self, eaters, feeders):
        sink = self.get_sink()
        # FIXME: these should be made threadsafe if we use GstThreads
        sink.connect('deep-notify::caps', self._notify_caps_cb)
        sink.connect('state-change', self._sink_state_change_cb)
        # these are made threadsafe using idle_add in the handler
        sink.connect('client-removed', self._client_removed_cb)
        sink.connect('client-added', self._client_added_cb)

        self.setGstProperties()

    def setGstProperties(self):
        for prop in self.gst_properties:
            type = prop.type
            if type == 'int':
                value = int(prop.data)
            elif type == 'str':
                value = str(prop.data)
            else:
                value = prop.data

            element = self.pipeline.get_by_name(prop.element)
            element.set_property(prop.name, value)

    def setProperties(self, properties):
        self.gst_properties = properties
        
gobject.type_register(MultifdSinkStreamer)

### create the component based on the config file
def createComponent(config):
    reactor.debug = True

    name = config['name']
    port = int(config['port'])
    source = config['source']

    component = MultifdSinkStreamer(name, source, port)
    resource = HTTPStreamingResource(component)
    
    # FIXME: tie these together more nicely
    component.resource = resource
    
    factory = server.Site(resource=resource)
    
    if config.has_key('gst-property'):
        component.setProperties(config['gst-property'])

    if config.has_key('logfile'):
        component.debug('Logging to %s' % config['logfile'])
        resource.setLogfile(config['logfile'])

    if config.has_key('auth'):
        auth_component = auth.getAuth(config['config'],
                                      config['auth'])
        resource.setAuth(auth_component)

    if config.has_key('maxclients'):
        resource.setMaxClients(int(config['maxclients']))
        
    if config.has_key('admin-password'):
        resource.setAdminPassword(config['admin-password'])

    if config.has_key('bouncer'):
        resource.setBouncerName(config['bouncer'])
        
    # create bundlers for UI
    # FIXME: make it so the bundles extract in the full path
    # for later when we transmit everything they depend on
    bundler = bundle.Bundler()
    # where do we live ?
    dir = os.path.split(__file__)[0]
    bundler.add(os.path.join(dir, 'gtk.py'))
    bundler.add(os.path.join(dir, 'http.glade'))
    component.addUIBundler(bundler, "admin", "gtk")
    
    component.debug('Listening on %d' % port)
    try:
        reactor.listenTCP(port, factory)
    except twisted.internet.error.CannotListenError:
        component.error('Port %d is not available.' % port)

    return component
