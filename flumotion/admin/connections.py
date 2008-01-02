# -*- Mode: Python; fill-column: 80 -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with th
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

__version__ = "$Rev$"


import datetime
import os
from xml.dom import minidom, Node

from flumotion.configure import configure
from flumotion.common import connection, errors, log
from flumotion.twisted import pb as fpb


class RecentConnection(object):
    def __init__(self, host, filename, info):
        self.name = str(info)
        self.host = host
        self.filename = filename
        self.info = info
        self.timestamp = datetime.datetime.fromtimestamp(
            os.stat(filename).st_ctime)

    def update_timestamp(self):
        os.utime(self.filename, None)


def get_recent_connections():
    def parse_connection(f):
        tree = minidom.parse(f)
        state = {}
        for n in [x for x in tree.documentElement.childNodes
                    if x.nodeType != Node.TEXT_NODE
                       and x.nodeType != Node.COMMENT_NODE]:
            state[n.nodeName] = n.childNodes[0].wholeText
        state['port'] = int(state['port'])
        state['use_insecure'] = (state['use_insecure'] != '0')
        authenticator = fpb.Authenticator(username=state['user'],
                                          password=state['passwd'])
        return connection.PBConnectionInfo(state['host'], state['port'],
                                           not state['use_insecure'],
                                           authenticator)

    try:
        # DSU, or as perl folks call it, a Schwartz Transform
        files = os.listdir(configure.registrydir)
        files = [os.path.join(configure.registrydir, f) for f in files]
        files = [(os.stat(f).st_mtime, f) for f in files
                                          if f.endswith('.connection')]
    except OSError, e:
        log.warning('connections', 'Error: %s: %s', e.strerror, e.filename)
        return []

    files.sort()
    files.reverse()

    ret = []
    for f in [x[1] for x in files]:
        try:
            state = parse_connection(f)
            ret.append(RecentConnection(str(state),
                                        filename=f,
                                        info=state))
        except Exception, e:
            log.warning('connections', 'Error parsing %s: %r', f, e)
    return ret

def parsePBConnectionInfo(managerString, use_ssl=True,
                          defaultPort=configure.defaultSSLManagerPort):
    """The same as L{flumotion.common.connection.parsePBConnectionInfo},
    but fills in missing information from the recent connections cache
    if possible.
    """
    recent = get_recent_connections()
    if not managerString:
        if recent:
            return recent[0].info
        else:
            raise errors.OptionError('No string given and no recent '
                                     'connections to use')

    info = connection.parsePBConnectionInfo(managerString, username=None,
                                            password=None,
                                            port=defaultPort,
                                            use_ssl=use_ssl)

    def compatible(i1, i2):
        if i1.port and i1.port != i2.port:
            return False
        if i1.use_ssl != i2.use_ssl:
            return False
        a1, a2 = i1.authenticator, i2.authenticator
        if a1.username and a1.username != a2.username:
            return False
        if a1.password and a1.password != a2.password:
            return False
        return True

    if not info.authenticator.username:
        for c in recent:
            recent = c.info
            if compatible(info, recent):
                info.authenticator.username = recent.authenticator.username
                info.authenticator.password = recent.authenticator.password
                break
    elif not info.authenticator.password:
        for c in recent:
            recent = c.info
            if compatible(info, recent):
                info.authenticator.password = recent.authenticator.password
                break
    if not (info.authenticator.username and info.authenticator.password):
        raise errors.OptionError('You are connecting to %s for the '
                                 'first time; please specify a user and '
                                 'password (e.g. user:test@%s).'
                                 % (managerString, managerString))
    else:
        return info
