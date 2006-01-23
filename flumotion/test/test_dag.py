# -*- Mode: Python; test-case-name: flumotion.test.test_dag -*-
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

import common

from twisted.trial import unittest

from flumotion.common import dag

class TestDAG(unittest.TestCase):
    def testBible(self):
        graph = dag.DAG()
        
        # first line
        graph.addNode('adam')
        graph.addNode('eve')

        self.assertRaises(KeyError, graph.addNode, 'adam')
        self.assertRaises(KeyError, graph.removeNode, 'abraham')

        self.failUnless(graph.isFloating('adam'))

        # second line
        graph.addNode('cain')
        graph.addNode('abel')
        graph.addNode('seth')

        graph.addEdge('adam', 'cain')
        graph.addEdge('adam', 'abel')
        graph.addEdge('adam', 'seth')
        graph.addEdge('eve', 'cain')
        graph.addEdge('eve', 'abel')
        graph.addEdge('eve', 'seth')

        self.assertRaises(KeyError, graph.addEdge, 'adam', 'cain')
        self.assertRaises(KeyError, graph.addEdge, 'abraham', 'cain')

        self.failIf(graph.isFloating('adam'))
        self.failIf(graph.isFloating('abel'))

        c = graph.getChildren('adam')
        self.failUnless('cain' in c)
        self.failUnless('abel' in c)
        self.failUnless('seth' in c)

        c = graph.getChildren('abel')
        self.failIf(c)

        p = graph.getParents('cain')
        self.failUnless('adam' in p)
        self.failUnless('eve' in p)

        p = graph.getParents('adam')
        self.failIf(p)

        # create a cycle
        graph.addEdge('cain', 'adam')
        self.assertRaises(dag.CycleError, graph.sort)

        # remove cycle
        graph.removeEdge('cain', 'adam')

        # test offspring
        offspring = graph.getOffspring('adam')
        self.assertEquals(len(offspring), 3)
        self.failUnless('cain' in offspring)
        self.failUnless('abel' in offspring)
        self.failUnless('seth' in offspring)
        
        offspring = graph.getOffspring('cain')
        self.assertEquals(len(offspring), 0)

        # add third line
        graph.addNode('enoch')
        graph.addEdge('cain', 'enoch')

        graph.addNode('irad')
        graph.addEdge('enoch', 'irad')

        graph.addNode('enosh')
        graph.addEdge('seth', 'enosh')

        graph.addNode('kenan')
        graph.addEdge('enosh', 'kenan')

        offspring = graph.getOffspring('adam')
        self.assertEquals(len(offspring), 7)
        for n in ['abel', 'cain', 'enoch', 'irad', 'seth', 'enosh', 'kenan']:
            self.failUnless(n in offspring)
  
        offspring = graph.getOffspring('cain')
        self.assertEquals(len(offspring), 2)
        for n in ['enoch', 'irad']:
            self.failUnless(n in offspring)

# example as shown in
# http://www.cs.cornell.edu/courses/cs312/2004fa/lectures/lecture15.htm
    def testExample(self):
        graph = dag.DAG()
        
        for i in range(1, 10):
            graph.addNode(i)

        graph.addEdge(1, 2)
        graph.addEdge(1, 4)
        graph.addEdge(2, 3)
        graph.addEdge(4, 3)
        graph.addEdge(4, 6)
        graph.addEdge(5, 8)
        graph.addEdge(6, 5)
        graph.addEdge(6, 8)
        graph.addEdge(9, 8)

        # check result of sort, using a preferred order chosen to match
        # the example
        # even though multiple answers are possible, the preferred order
        # makes sure we get the one result we want
        nodes = graph._sortPreferred([1, 2, 3, 4, 5, 6, 9, 8, 7])
        sorted = [node.object for node in nodes]
        self.assertEquals(sorted, [7, 9, 1, 4, 6, 5, 8, 2, 3])

        # poke at internal counts to see if the algorithm was done right
        # reference begin and end value for each item - see example
        counts = [(1, 14), (2, 5), (3, 4), (6, 13), (8, 11), (7, 12),
                  (17, 18), (9, 10), (15, 16)]
        for i in range(1,10):
            n = graph.nodes[i]
            begin, end = counts[i - 1]
            self.assertEquals(graph._begin[n], begin)
            self.assertEquals(graph._end[n], end)

        # add an edge that introduces a cycle
        graph.addEdge(5, 4)
        self.assertRaises(dag.CycleError, graph.sort)

class FakeDep:
    def __init__(self, name):
        self.name = name

class FakeFeeder(FakeDep): pass
class FakeEater(FakeDep): pass
class FakeWorker(FakeDep): pass
class FakeKid(FakeDep): pass
class FakeComponent(FakeDep): pass

(feeder, eater, worker, kid, component) = range(0, 5)

class TestPlanet(unittest.TestCase):
    def testPlanet(self):
        graph = dag.DAG()
        
        weu = FakeWorker('europe')
        wus = FakeWorker('america')
        
        graph.addNode(weu, worker)
        graph.addNode(wus, worker)
        
        # producer
        kpr = FakeKid('producer')
        cpr = FakeComponent('producer')
        fau = FakeFeeder('audio')
        fvi = FakeFeeder('video')

        graph.addNode(kpr, kid)
        graph.addNode(cpr, component)
        graph.addNode(fau, feeder)
        graph.addNode(fvi, feeder)

        graph.addEdge(weu, kpr)
        graph.addEdge(kpr, fau)
        graph.addEdge(kpr, fvi)
        graph.addEdge(fau, cpr)
        graph.addEdge(fvi, cpr)

        kcv = FakeKid('converter')
        ccv = FakeComponent('converter')
        fen = FakeFeeder('encoded')
        evi = FakeEater('video')

        graph.addNode(kcv, kid)
        graph.addNode(ccv, component)
        graph.addNode(evi, eater)
        graph.addNode(fen, feeder)

        graph.addEdge(weu, kcv)
        graph.addEdge(kcv, fen)
        graph.addEdge(kcv, evi)
        graph.addEdge(fen, ccv)
        graph.addEdge(evi, ccv)

        # link from producer to converter
        graph.addEdge(fvi, evi)

        # consumer
        kcs = FakeKid('consumer')
        ccs = FakeComponent('consumer')
        een = FakeEater('encoded')

        graph.addNode(kcs, kid)
        graph.addNode(ccs, component)
        graph.addNode(een, feeder)

        graph.addEdge(wus, kcs)
        graph.addEdge(kcs, een)
        graph.addEdge(een, ccs)

        # link from converter to consumer
        graph.addEdge(fen, een)

        # tester
        kte = FakeKid('tester')
        cte = FakeComponent('tester')

        graph.addNode(kte, kid)
        graph.addNode(cte, component)

        graph.addEdge(ccs, cte)

        # test offspring filtered
        
        # all components depend on the european worker
        list = graph.getOffspring(weu, component)
        self.assertEquals(len(list), 4)
        for c in [cpr, ccv, ccs, cte]:
            self.failUnless(c in list)

        # only streamer and tester depend on the us worker
        list = graph.getOffspring(wus, component)
        self.assertEquals(len(list), 2)
        for c in [ccs, cte]:
            self.failUnless(c in list)

