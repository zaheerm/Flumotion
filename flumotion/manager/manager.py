# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

# Flumotion - a video streaming server
# Copyright (C) 2004 Fluendo
#
# manager/manager.py: manager functionality
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
# Foundation, Inc., 59 Temple Street #330, Boston, MA 02111-1307, USA.

"""Manager implementation and related classes

API Stability: semi-stable

Maintainer: U{Johan Dahlin <johan@fluendo.com>}
"""

__all__ = ['ManagerServerFactory', 'Vishnu']

from twisted.internet import reactor
from twisted.python import components
from twisted.spread import pb

from flumotion.manager import admin, component, worker
from flumotion.common import errors, interfaces
from flumotion.twisted import pbutil, portal
from flumotion.utils import log

# an internal class
class Dispatcher(log.Loggable):
    """
    I implement L{portal.IRealm}.
    I make sure that when a L{pb.Avatar} is requested through me, the
    Avatar being returned knows about the mind (client) requesting
    the Avatar.
    """
    
    __implements__ = portal.IRealm

    logCategory = 'dispatcher'

    def __init__(self):
        self.heavens = {}
        self.avatars = {}
        
    # requestAvatar gets called through ClientFactory.login()
    # An optional second argument can be passed to login, which should be
    # a L{twisted.spread.flavours.Referenceable}
    # A L{twisted.spread.pb.RemoteReference} to it is passed to
    # requestAvatar as mind.
    # So in short, the mind is a reference to the client passed in login()
    # on the peer, allowing any object that has the mind to call back
    # to the piece that called login(),
    # which in our case is a component or an admin client.
    def requestAvatar(self, avatarID, mind, *ifaces):
        avatar = self.getAvatarFor(avatarID, ifaces)

        self.debug("returning Avatar: id %s, avatar %s" % (avatarID, avatar))

        # schedule a perspective attached for after this function
        reactor.callLater(0, avatar.attached, mind)

        # return a tuple of interface, aspect, and logout function 
        return (pb.IPerspective, avatar,
                lambda a=avatar, m=mind, i=avatarID: self.removeAvatar(i, a, m))

    def removeAvatar(self, avatarID, avatar, mind):
        heaven = self.avatars[avatarID]
        del self.avatars[avatarID]
        
        avatar.detached(mind)
        heaven.removeAvatar(avatarID)

    def getAvatarFor(self, avatarID, ifaces):
        if not pb.IPerspective in ifaces:
            raise errors.NoPerspectiveError(avatarID)

        for iface in ifaces:
            heaven = self.heavens.get(iface, None)
            if heaven:
                avatar = heaven.getAvatar(avatarID)
                self.avatars[avatarID] = heaven
                return avatar

        raise errors.NoPerspectiveError(avatarID)
        
    def registerHeaven(self, interface, heaven):
        assert components.implements(heaven, interfaces.IHeaven)
        
        self.heavens[interface] = heaven

class Vishnu:
    def __init__(self):
        # create a Dispatcher which will hand out avatars to clients
        # connecting to me
        self.dispatcher = Dispatcher()

        self.workerheaven = self.createHeaven(interfaces.IWorkerComponent,
                                              worker.WorkerHeaven)
        self.componentheaven = self.createHeaven(interfaces.IBaseComponent,
                                                 component.ComponentHeaven)
        self.adminheaven = self.createHeaven(interfaces.IAdminComponent,
                                             admin.AdminHeaven)

        # create a portal so that I can be connected to, through our dispatcher
        # implementing the IRealm and a checker that allows anonymous access
        checker = pbutil.ReallyAllowAnonymousAccess()
        p = portal.FlumotionPortal(self.dispatcher, [checker])
        #unsafeTracebacks = 1 # for debugging tracebacks to clients
        self.factory = pb.PBServerFactory(p)

    def createHeaven(self, interface, klass):
        heaven = klass(self)
        self.dispatcher.registerHeaven(interface, heaven)
        return heaven
    
    def getFactory(self):
        return self.factory
