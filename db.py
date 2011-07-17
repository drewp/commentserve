import time, glob, os, tempfile, logging
from rdflib.Graph import ConjunctiveGraph
from rdflib import Namespace
from sparqlhttp.dictquery import Graph2

log = logging.getLogger("db")

SIOC = Namespace("http://rdfs.org/sioc/ns#")
CONTENT = Namespace("http://purl.org/rss/1.0/modules/content/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
XS = Namespace("http://www.w3.org/2001/XMLSchema#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")

class Db(object):
    def __init__(self):
        self.lastTimes = []

    def getGraph(self):
        t1 = time.time()

        mtimes = []
        for f in (["/my/proj/openid_proxy/access.n3"] +
                  glob.glob("commentstore/*.nt")):
            mtimes.append(os.path.getmtime(f))

        if mtimes == self.lastTimes:
            return self.currentGraph
        self.lastTimes = mtimes

        tf = tempfile.NamedTemporaryFile()
        os.system("cat /my/proj/openid_proxy/access.n3 commentstore/*.nt > %s" % tf.name)
        g = ConjunctiveGraph()
        g.parse(tf.name, format="n3")

        self.currentGraph = Graph2(g, initNs=dict(sioc=SIOC, content=CONTENT,
                                                  foaf=FOAF, dcterms=DCTERMS,
                                                  xs=XS))

        log.info("reloaded comments from disk in %f sec" % (time.time() - t1))

        return self.currentGraph

    def queryd(self, *args, **kw):
        return self.getGraph().queryd(*args, **kw)
    
    def value(self, *args, **kw):
        return self.getGraph().value(*args, **kw)
        
    def writeFile(self, stmts, ctx, fileWords):
        outfile = "commentstore/post-%s.nt" % ("-".join(fileWords))
        graph = Graph2(ConjunctiveGraph())

        graph.add(*stmts, **{'context' : ctx})
        graph.graph.serialize(outfile, format='n3')
        log.info("wrote new comment to %s", outfile)
        # this could be optimized to add to currentGraph and reload
        # less often, but i'm currently at 600ms for a reload
