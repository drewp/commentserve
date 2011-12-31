# one time import from files to mongo
import os
from db import DbMongo
from rdflib.graph import ConjunctiveGraph
db = DbMongo()

for name in os.listdir("commentstore"):
    filename = os.path.join("commentstore", name)
    if os.path.isdir(filename):
        continue
    print filename
    g = ConjunctiveGraph()
    g.parse(filename, format="n3")
    
    db.writeFile(list(g), None, name[len('post-'):].split('-'))
