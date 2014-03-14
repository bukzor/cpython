# coding:UTF-8
# Does it crash??
from __future__ import explicit_encoding

import unittest
from . import test_support

# A US-ASCII string.
ASCII = b'Detroit'
# A nice non-latin1 string.
UNICODE = u'Łódź'

class TestExplicitEncoding(unittest.TestCase):

    def test_doesnt_crash(self):
        self.assertTrue(True)

    def test_concat(self):

        self.assertRaisesRegexp(
            TypeError,
            "^Can't convert 'bytes' object to str implicitly$",
            ASCII.__add__,
            UNICODE,
        )
        self.assertRaisesRegexp(
            TypeError,
            "^Can't convert 'bytes' object to str implicitly$",
            UNICODE.__add__,
            ASCII,
        )

    def test_decoding(self):
        self.assertRaisesRegexp(
            TypeError,
            "^decoding Unicode is not supported",
            ASCII.decode,
            'US-ASCII',
        )




def test_main():
    test_support.run_unittest(TestExplicitEncoding)
