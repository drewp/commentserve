"""
based on
http://trac.edgewall.org/browser/plugins/0.12/spam-filter-captcha/tracspamfilter/filters/httpbl.py
BSD license: http://trac.edgewall.org/wiki/TracLicense

"""
import logging
from dns.resolver import query, Timeout, NXDOMAIN, NoAnswer, NoNameservers
log = logging.getLogger()

class HoneypotChecker(object):
    def __init__(self, key):
        """
        key is a string you get from registering with honeypot
        """
        self.key = key

    def check(self, ip):
        """
        raises if this ip fails the httpbl check
        """
        
        reverse_octal = '.'.join(reversed(ip.split('.')))
        addr = '%s.%s.dnsbl.httpbl.org' % (self.key, reverse_octal)
        log.debug('Querying Http:BL: %s' % addr)
        try:
            dns_answer = query(addr)
            answer = [int(i) for i in str(dns_answer[0]).split('.')]
            if answer[0] != 127:
                log.warn('Invalid Http:BL reply for IP "%s": %s' %
                                 (ip, dns_answer))
                return

            # TODO: answer[1] represents number of days since last activity
            #       and answer[2] is treat score assigned by Project Honey
            #       Pot. We could use both to adjust karma.

            is_suspicious = answer[3] & 1
            is_spammer =    answer[3] & 4

            if is_spammer:
                raise ValueError("IP %s rejected" % ip)


        except NXDOMAIN:
            # not blacklisted on this server
            return

        except (Timeout, NoAnswer, NoNameservers), e:
            log.warn('Error checking Http:BL for IP "%s": %s' %
                     (ip, e))

