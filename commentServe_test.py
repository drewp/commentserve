import unittest
from commentServe import spamCheck
class TestSpamCheck(unittest.TestCase):
    def testOkMessage(self):
        self.assertEqual(spamCheck(None, "this message is fine"), None)
    def testNormalLink(self):
        self.assertEqual(spamCheck(None, "you can say http://example.com/ a link if you need to"), None)
    def testTooManyLinks(self):
        self.assertRaises(ValueError, spamCheck, None, '<a href="not even">a single html</a> link is currently allowed')
    def testNoLinkAtTheEnd(self):
        self.assertRaises(ValueError, spamCheck, None, 'too many spammers were doing this http://dumbsite.com')
        
