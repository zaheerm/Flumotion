# -*- Mode: Python; test-case-name: flumotion.test.test_manager_common -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# flumotion/manager/common.py: common classes for manager-side objects
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

"""
Common classes and code to support manager-side objects.
"""

from twisted.internet import reactor
from twisted.spread import pb

from flumotion.common import errors, interfaces, log

class ManagerAvatar(pb.Avatar, log.Loggable):
    """
    I am a base class for manager-side avatars to subclass from.
    """
    def __init__(self, heaven, avatarId):
        """
        @type heaven: L{flumotion.manager.common.ManagerHeaven}
        """
        self.heaven = heaven
        self.avatarId = avatarId
        self.mind = None
        self.vishnu = heaven.vishnu

        self.debug("created new Avatar with id %s" % avatarId)
        
    def hasRemoteReference(self):
        """
        Check if the avatar has a remote reference to the peer.

        @rtype: boolean
        """
        return self.mind != None
    
    def mindCallRemote(self, name, *args, **kwargs):
        """
        Call the given remote method.
        """
        if not self.hasRemoteReference():
            self.warning("Can't call remote method %s, no mind" % name)
            return
        
        # we can't do a .debug here, since it will trigger a resend of the
        # debug message as well, causing infinite recursion !
        # self.debug('Calling remote method %s%r' % (name, args))
        try:
            return self.mind.callRemote(name, *args, **kwargs)
        except pb.DeadReferenceError:
            self.warning("mind %s is a dead reference, removing" % self.mind)
            self.mind = None
            return

    def attached(self, mind):
        """
        Tell the avatar that the given mind has been attached.
        This gives the avatar a way to call remotely to the client that
        requested this avatar.
        This is scheduled by the portal after the client has logged in.

        @type mind: L{twisted.spread.pb.RemoteReference}
        """
        self.mind = mind
        ip = self.mind.broker.transport.getPeer().host
        self.debug('PB Client from %s attached' % ip)
        self.log('Client attached is mind %s' % mind)

    def detached(self, mind):
        """
        Tell the avatar that the peer's client referenced by the mind
        has detached.
        """
        assert(self.mind == mind)
        self.debug('Client from %s detached' % self.getClientAddress())
        self.mind = None
        self.log('Client detached is mind %s' % mind)

    def getClientAddress(self):
        """
        Get the IPv4 address of the machine the client is connecting from.
        """
        if self.mind:
            peer = self.mind.broker.transport.getPeer()
            # pre-Twisted 1.3.0 compatibility
            try:
                return peer.host
            except AttributeError:
                return peer[1]
                
        return None

### FIXME: rework bundler requester stuff
    def perspective_getBundleSumsByFile(self, filename):
        """
        Get the name of the bundle, and a dictionary of md5sums of all
        dependency bundles, including this bundle.

        @type  filename: string
        @param filename: the name of the file in a bundle
        """
        pass

class ManagerHeaven(pb.Root, log.Loggable):
    """
    I am a base class for heavens in the manager.
    """

    avatarClass = None

    def __init__(self, vishnu):
        """
        @type vishnu: L{flumotion.manager.manager.Vishnu}
        @param vishnu: the Vishnu in control of all the heavens
        """
        self.vishnu = vishnu
        self.avatars = {} # name -> avatar
       
    ### ManagerHeaven methods
    def createAvatar(self, avatarId):
        """
        Create a new administration avatar and manage it.

        @rtype:   L{flumotion.manager.admin.AdminAvatar}
        @returns: a new avatar for the admin client.
        """
        self.debug('creating new Avatar with name %s' % avatarId)
        if self.avatars.has_key(avatarId):
            raise errors.AlreadyConnectedError(avatarId)

        avatar = self.avatarClass(self, avatarId)
        
        self.avatars[avatarId] = avatar
        return avatar

    def removeAvatar(self, avatarId):
        """
        Stop managing the given avatar.

        @type avatarId:  string
        @param avatarId: id of the avatar to remove
        """
        self.debug('removing Avatar with id %s' % avatarId)
        del self.avatars[avatarId]
        
    def getAvatar(self, avatarId):
        return self.avatars[avatarId]

    def getAvatars(self):
        return self.avatars.values()



