# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# flumotion/service/main.py: Service main function
#
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
import sys
import optparse

from flumotion.configure import configure
from flumotion.service import service

def main(args):
    parser = optparse.OptionParser()
    parser.add_option('-c', '--configdir',
                      action="store", dest="configdir",
                      help="flumotion configuration directory")
    parser.add_option('-l', '--logfile',
                      action="store", dest="logfile",
                      help="flumotion service log file")

    options, args = parser.parse_args(args)

    if not options.configdir:
        options.configdir = configure.configdir

    # if log file is specified, redirect stdout and stderr
    if options.logfile:
        out = open(options.logfile, 'a+')
        err = open(options.logfile, 'a+', 0)
        os.dup2(out.fileno(), sys.stdout.fileno())
        os.dup2(err.fileno(), sys.stderr.fileno())

    servicer = service.Servicer(options.configdir)
    try:
        command = args[1]
    except IndexError:
        print "Usage: flumotion {list|start|stop} [options]"
        sys.exit(0)
    
    if command == "list":
        return servicer.list()
    elif command == "start":
        return servicer.start(args[2:])
    elif command == "stop":
        return servicer.stop(args[2:])
    elif command == "restart":
        return servicer.stop(args[2:]) + servicer.start(args[2:])
    elif command == "status":
        return servicer.status(args[2:])

    sys.stderr.write("No such command '%s'\n" % command)
    return 1
