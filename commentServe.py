#!/usr/bin/python

"""comment storage for blogs, photo site, etc

see also:
        sioc:Post  sioc:has_reply sioc:Post / types:Comment
          sioc:content
          content:encoded
          dcterms:created        
        
        types:BlogPost
        types:Comment

"""

import web, time, logging
from datetime import datetime
from uuid import uuid4
from html5lib import html5parser, sanitizer
from web.contrib.template import render_genshi
from rdflib import RDF, URIRef, Literal, Namespace

from dateutil.parser import parse
from honeypot import HoneypotChecker
import restkit
from dateutil.tz import tzlocal
import cyclone.web
from twisted.internet import reactor
from db import DbMongo

SIOC = Namespace("http://rdfs.org/sioc/ns#")
CONTENT = Namespace("http://purl.org/rss/1.0/modules/content/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
XS = Namespace("http://www.w3.org/2001/XMLSchema#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
HTTP = Namespace("http://www.w3.org/2006/http#")
OV = Namespace("http://open.vocab.org/terms/")

log = logging.getLogger()
logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s:%(message)s')
log.setLevel(logging.INFO)

render = render_genshi(['.'], auto_reload=False)

def literalFromUnix(t):
    i = datetime.fromtimestamp(int(t)).replace(tzinfo=tzlocal()).isoformat()
    return Literal(i, datatype=XS['dateTime'])


def agoString(literalTime):
    d = parse(str(literalTime))
    # (assuming 'now' is in the same timezone as d)
    return web.utils.datestr(d, datetime.now().replace(tzinfo=tzlocal()))

def newPublicUser(forwardedFor, name, email):
    """
    a non-logged-in user is posting a comment on a resource that's
    open for public comments. We make a new URI for this user (every
    time) and store some extra statements.

    pass your web post params, which might include 'name' and 'email'.
    
    returns user URI and a list of triples to be stored
    """
    stmts = []
    user = URIRef('http://bigasterisk.com/guest/%s' % uuid4())
    header = URIRef(user + "/header1")
    stmts.extend([
        (user, RDF.type, FOAF.Person),
        (user, DCTERMS.created, literalFromUnix(time.time())),
        (user, OV.usedHttpHeader, header),
        (header, HTTP.fieldName, Literal('X-Forwarded-For')),
        (header, HTTP.fieldValue, Literal(forwardedFor)),
        ])
    if name:
        stmts.append((user, FOAF.name, Literal(name)))
    if email:
        stmts.append((user, FOAF.mbox, URIRef("mailto:%s" % email)))
    return user, stmts

def newCommentUri(secs=None):
    """this is essentially a bnode, but a real URI is easier to work with"""
    if secs is None:
        secs = time.time()
    return URIRef("http://bigasterisk.com/comment/%r" % secs)

class AnyCase(sanitizer.HTMLSanitizer):
    def __init__(self, stream, encoding=None, parseMeta=True, useChardet=True,
                 lowercaseElementName=True, lowercaseAttrName=True):
        sanitizer.HTMLSanitizer.__init__(self, stream, encoding, parseMeta,
                                         useChardet,
                                         lowercaseElementName,
                                         lowercaseAttrName)

class AnyCaseNoSrc(AnyCase):
    allowed_attributes = AnyCase.allowed_attributes[:]
    allowed_attributes.remove('src')

def sanitize_html(stream, srcAttr=False):
    ret = ''.join([token.toxml() for token in
                   html5parser.HTMLParser(tokenizer=AnyCase if srcAttr else AnyCaseNoSrc).
                   parseFragment(stream).childNodes])
    return ret

def spamCheck(content):
    if content.lower().count("<a href") > 4:
        log.error("too many links in %r" % content)
        raise ValueError("too many links")
    if '[url=' in content:
        raise ValueError("url markup is too suspicious")

class Comments(cyclone.web.RequestHandler):
    def get(self, public=False):
        """
        post=<uri to post> (or use 'uri' for the arg)
        
        returns html formatted comments (until i get some more content types)
        """
        t1 = time.time()
        post = (self.get_argument("post", default=None) or
                self.get_argument("uri", default=None))
        if not post:
            raise ValueError("need 'uri' param")
        post = URIRef(post)
        
        foafAgent = None
        try:
            foafAgent = URIRef(self.request.headers['X-Foaf-Agent'])
        except KeyError:
            if not public:
                self.write("Must login to see comments")
                return

        queryTime = time.time()
        rows = self.findComments(post)
        queryTime = time.time() - queryTime

        self.set_header("Content-Type", "text/html")
        ret = render.comments(
            includeJs=self.get_argument("js", default="0") != "0",
            public=public,
            parent=post,
            agoString=agoString,
            you=self.settings.db.value(foafAgent, FOAF.name) if foafAgent else None,
            rows=rows,
            )
        self.write(ret + "<!-- %.2f ms (%.2f ms in query) -->" % (
            1000 * (time.time() - t1),
            1000 * queryTime))

    def findComments(self, post):
        rows = []
        for row in self.settings.db.queryd("""
               SELECT DISTINCT ?who ?when ?content WHERE {
                 ?parent sioc:has_reply [
                   sioc:has_creator ?cr;
                   content:encoded ?content;
                   dcterms:created ?when
                   ]
                 OPTIONAL { ?cr foaf:name ?who }
               } ORDER BY ?when""", initBindings={"parent" : post}):
            row['content'] = sanitize_html(row['content'])
            rows.append(row)
        log.debug("found %s rows with parent %r" % (len(rows), post))
        return rows
    
    def post(self, public=False):
        """
        post=<parent post>
        content=<html content>

        we get the user from the x-foaf-agent header
        """
        parent = self.get_argument('post', default=None) or self.get_argument("uri")
        assert parent is not None
        parent = URIRef(parent)

        # this might be failing on ariblog, but that one is already safe
        ip = self.request.headers.get("X-Forwarded-For")
        if ip is not None:
            HoneypotChecker(open("priv-honeypotkey").read().strip()).check(ip)

        contentArg = self.get_argument("content", default="")
        if not contentArg.strip():
            raise ValueError("no text")

        if contentArg.strip() == 'test':
            return "not adding test comment"

        spamCheck(contentArg)
            
        content = Literal(contentArg, datatype=RDF.XMLLiteral)

        stmts = [] # gathered in one list for an atomic add

        foafHeader = self.request.headers.get('X-Foaf-Agent')
        if not public:
            assert foafHeader
            user = URIRef(foafHeader)
            # make bnode-ish users for anonymous ones. need to get that username passed in here
        else:
            if foafHeader:
                user = URIRef(foafHeader)
            else:
                user, moreStmts = newPublicUser(
                    self.request.headers.get("X-Forwarded-For"),
                    self.get_argument("name", ""),
                    self.get_argument("email", ""))
                stmts.extend(moreStmts)
                
        secs = time.time()
        comment = newCommentUri(secs)

        now = literalFromUnix(secs)

        ctx = URIRef(parent + "/comments")

        stmts.extend([(parent, SIOC.has_reply, comment),
                      (comment, DCTERMS.created, now),
                      (comment, SIOC.has_creator, user),
                      ])
        stmts.extend(commentStatements(user, comment, content))

        db.writeFile(stmts, ctx, fileWords=[parent.split('/')[-1], now])

        try:
            self.sendAlerts(parent, user)
        except Exception, e:
            import traceback
            log.error(e)
            traceback.print_exc()
        
        self.write("added")

    def sendAlerts(self, parent, user):
        c3po = restkit.Resource('http://bang:9040/')
        for listener, mode in [
            ('http://bigasterisk.com/foaf.rdf#drewp', 'xmpp'),
            ('http://bigasterisk.com/kelsi/foaf.rdf#kelsi', 'xmpp')]:
            c3po.post(
                path='', payload={
                    'user': listener,
                    'msg': '%s comment from %s' % (parent, user),
                    'mode': mode,
                },
                # shouldn't this be automatic?
                headers={'content-type' : 'application/x-www-form-urlencoded'},
                )
            
class CommentCount(cyclone.web.RequestHandler):
    def get(self, public=False):
        if not public:
            try:
                self.request.headers['X-Foaf-Agent']
            except KeyError:
                self.set_header("Content-Type", "text/plain")
                self.write("Must login to see comments")
                return

        post = URIRef(self.get_argument("post"))

        rows = self.settings.db.queryd("""
               SELECT DISTINCT ?r WHERE {
                 ?parent sioc:has_reply ?r
               }""", initBindings={"parent" : post})
        count = len(list(rows))
        self.set_header("Content-Type", "text/plain")
        self.write("%s comments" % count if count != 1 else "1 comment")
        
def commentStatements(user, commentUri, realComment):
    # here you can put more processing on the comment text
    realComment = realComment.replace("\r", "") # rdflib n3 can't read these back
    return [(commentUri, CONTENT.encoded, realComment)]  
    
class Index(cyclone.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/plain")
        self.write("commentServe")

class Application(cyclone.web.Application):
    def __init__(self, db):
        handlers = [
            (r'/comments', Comments),
            (r'/(public)/comments', Comments),
            (r'/commentCount', CommentCount),
            (r'/(public)/commentCount', CommentCount),
        ]
        cyclone.web.Application.__init__(self, handlers, db=db)

if __name__ == '__main__':
    db = DbMongo()
    from twisted.python.log import startLogging
    import sys
    startLogging(sys.stdout)
    reactor.listenTCP(9031, Application(db))
    reactor.run()
