# coding:UTF-8
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
        """Does it crash??"""
        self.assertTrue(True)

    def test_concat_explicit(self):
        """str and unicode can only be combined with explicit encoding."""
        self.assertRaisesRegexp(
            TypeError,
            "^Can't convert 'str' object to unicode implicitly$",
            concat_explicit,
            ASCII,
            UNICODE,
        )
        self.assertRaisesRegexp(
            TypeError,
            "^Can't convert 'str' object to unicode implicitly$",
            concat_explicit,
            UNICODE,
            ASCII,
        )

    def test_concat_implicit(self):
        """implicit encoding is still active for frames lower in the stack."""
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
        """unicode.decode is nonsensical, nonexistant in python3"""
        self.assertRaisesRegexp(
            TypeError,
            "^decoding Unicode is not supported",
            decode_explicit,
            UNICODE,
            'US-ASCII',
        )


    def test_peephole(self):
        """During peephole optimization, there is no frame."""
        # TODO make sure it fails when segfault-check is done wrong.
        import __future__
        compile(
                '''b"a" + u"b"''',
                '<string>',
                'single',
                flags=__future__.CO_FUTURE_EXPLICIT_ENCODING,
                dont_inherit=True,
        )


def test_main():
    test_support.run_unittest(TestExplicitEncoding)
