import time, glob, os, tempfile, logging, datetime
from dateutil.parser import parse
from dateutil.tz import tzlocal
import rdflib
rdflib.plugin.register('sparql', rdflib.query.Processor,
                       'rdfextras.sparql.processor', 'Processor')
rdflib.plugin.register('sparql', rdflib.query.Result,
                       'rdfextras.sparql.query', 'SPARQLQueryResult')

from rdflib.graph import ConjunctiveGraph
from rdflib import Namespace
from rdflib.parser import StringInputSource
import sys
sys.path.append("/my/proj/sparqlhttp")
import rdflib
if rdflib.__version__ == '3.2.0-dev':
    rdflib.__version__ = '3.2.0' # workaround for sparqlhttp weakness
from sparqlhttp.dictquery import Graph2

log = logging.getLogger("db")

SIOC = Namespace("http://rdfs.org/sioc/ns#")
CONTENT = Namespace("http://purl.org/rss/1.0/modules/content/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
XS = Namespace("http://www.w3.org/2001/XMLSchema#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")

class _shared(object):
    def queryd(self, *args, **kw):
        return self.getGraph().queryd(*args, **kw)
    
    def value(self, *args, **kw):
        return self.getGraph().value(*args, **kw)

class Db(_shared):
    def __init__(self):
        self.lastTimes = []

    def getGraph(self):
        t1 = time.time()

        mtimes = []
        for f in (["/my/proj/openid_proxy/access.n3"] +
                  glob.glob("commentstore/*.nt")):
            mtimes.append(os.path.getmtime(f))

        if mtimes == self.lastTimes and hasattr(self, 'currentGraph'):
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
        
    def writeFile(self, stmts, ctx, fileWords):
        outfile = "commentstore/post-%s.nt" % ("-".join(fileWords))
        graph = Graph2(ConjunctiveGraph())

        graph.add(*stmts, **{'context' : ctx})
        graph.graph.serialize(outfile, format='n3')
        log.info("wrote new comment to %s", outfile)
        # this could be optimized to add to currentGraph and reload
        # less often, but i'm currently at 600ms for a reload

class DbMongo(_shared):
    def __init__(self):
        from pymongo import Connection
        self.mongo = Connection('bang', 27017, tz_aware=True)['comment']

        self.lastTime = 0
        self.notSpam = {"type":{"$ne":"spam"}}

    def getGraph(self):

        newDoc = self.mongo['comment'].find_one(self.notSpam,
                                                sort=[('created', -1)])
        newDocTime = time.mktime(newDoc['created'].astimezone(tzlocal()).timetuple()) if newDoc is not None else 0

        mtime = os.path.getmtime("/my/proj/openid_proxy/access.n3")

        if newDocTime > self.lastTime or mtime > self.lastTime:
            g = ConjunctiveGraph()
            g.parse("/my/proj/openid_proxy/access.n3", format="n3")
            for doc in self.mongo['comment'].find(self.notSpam):
                g.parse(StringInputSource(doc['n3'].encode('utf8')),
                        format="n3")

            self.currentGraph = Graph2(g, initNs=dict(
                sioc=SIOC, content=CONTENT, foaf=FOAF, dcterms=DCTERMS, xs=XS))
            self.lastTime = max(newDocTime, mtime)
        return self.currentGraph
        
    def writeFile(self, stmts, ctx, fileWords):
        g = ConjunctiveGraph()
        doc = {'ctx' : ctx}

        for s in stmts:
            g.add(s)
            if s[1] == SIOC.has_reply:
                doc['topic'] = s[0]
            if s[1] == DCTERMS.created: # expecting 2 of these, but same value
                doc['created'] = parse(s[2])

        doc['n3'] = g.serialize(format="n3")
        self.mongo['comment'].insert(doc)

    def getRecentComments(self, n=10, notOlderThan=None, withSpam=False):
        self.mongo['comment'].ensure_index('created')
        spec = {}
        if not withSpam:
            spec = self.notSpam
        if notOlderThan is not None:
            now = datetime.datetime.now(tzlocal())
            spec['created'] = {
                '$gt' : now - datetime.timedelta(days=notOlderThan)}
        for doc in self.mongo['comment'].find(spec, limit=n,
                                              sort=[('created', -1)]):
            g = ConjunctiveGraph()
            g.parse(StringInputSource(doc['n3'].encode('utf8')), format='n3')
            parent, _, uri = g.triples((None, SIOC.has_reply, None)).next()
            created = g.value(uri, DCTERMS.created)
            content = g.value(uri, CONTENT.encoded)
            creator = g.value(uri, SIOC.has_creator)
            docId = str(doc['_id'])
            isSpam = doc.get('type', '')
            yield vars()

    def setType(self, docId, type):
        from bson import ObjectId
        self.mongo['comment'].update({'_id' : ObjectId(docId)},
                                     {"$set" : {"type" : type}},
                                     multi=False, safe=True)
        self.lastTime = 0
            
    """
    what this api should be:

    saveComment(parent, content, time, user=foaf_or_public, publicName, publicEmail, moreHeaders)
    getCommentsForParent(parent, withSpam=False) -> [(uri, t, user, userFoafName, content, isSpam), ...]
    getCommentCount(parent) -> n
    setSpam(uri, isSpam)
    getRecentComments(n) -> [(uri, t, user, content, isSpam)]
    getCommentsByUser(user, paging)
    """
    
