# coding:UTF-8
# Does it crash??
from __future__ import explicit_encoding



import unittest
from . import test_support

# A US-ASCII string.
ASCII = b'Detroit'
# A nice non-latin1 string.
UNICODE = u'Łódź'


def concat_explicit(a, b):
    # We need to do the operation in *this* module to test the module-scoped semantics.
    return a + b


def decode_explicit(u, encoding):
    # decodes a unicode string
    return u.decode(encoding)


class TestExplicitEncoding(unittest.TestCase):
    # TODO tests:
    #  * getattr using unicode attr name
    #  * .encode using unicode codec name

    def test_doesnt_crash(self):
        self.assertTrue(True)

    def test_concat_explicit(self):
        self.assertRaisesRegexp(
            TypeError,
            "^Can't convert 'bytes' object to str implicitly$",
            concat_explicit,
            ASCII,
            UNICODE,
        )
        self.assertRaisesRegexp(
            TypeError,
            "^Can't convert 'bytes' object to str implicitly$",
            concat_explicit,
            UNICODE,
            ASCII,
        )

    def test_concat_implicit(self):
        from .future_encoding_tests import concat_implicit

        self.assertEqual( 
            ASCII.decode('US-ASCII') + UNICODE,
            concat_implicit(ASCII, UNICODE), 
        )

        self.assertEqual( 
            UNICODE + ASCII.decode('US-ASCII'),
            concat_implicit(UNICODE, ASCII), 
        )

    def test_decoding(self):
        self.assertRaisesRegexp(
            TypeError,
            "^decoding Unicode is not supported",
            decode_explicit,
            UNICODE,
            'US-ASCII',
        )


def test_main():
    test_support.run_unittest(TestExplicitEncoding)
