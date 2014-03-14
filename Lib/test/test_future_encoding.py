# coding:UTF-8
# Does it crash??
from __future__ import explicit_encoding

import unittest
from . import test_support

# A US-ASCII string.
ASCII = b'Detroit'
# A nice non-latin1 string.
UNICODE = u'Łódź'


"""The *_explicit functions defined below have the semantics of
__future__.explicit_encoding because they're defined in *this* module.
We have to do this to test the module-scoped semantics.
"""
def concat_explicit(a, b):
    return a + b

def decode_explicit(u, encoding):
    return u.decode(encoding)

def equals_explicit(a, b):
    return a == b

def get_explicit(d, v):
    return d.get(v)


class TestExplicitEncoding(unittest.TestCase):
    # TODO tests:
    #  * getattr using unicode attr name
    #  * .encode using unicode codec name

    def test_doesnt_crash(self):
        self.assertTrue(True)

    def test_concat_explicit(self):
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

    def test_unicode_codec_names(self):
        self.assertEqual(
            UNICODE.encode(u'UTF-8'),
            UNICODE.encode(b'UTF-8'),
        )

        self.assertEqual(
            ASCII.decode(u'US-ASCII'),
            ASCII.decode(b'US-ASCII'),
        )


    def test_dict_get(self):
        d = {b'foo': 'bar', u'baz': 'quux'}

        self.assertEqual('bar', get_explicit(d, b'foo'))
        self.assertEqual(None, get_explicit(d, u'foo'))

        self.assertEqual('quux', get_explicit(d, u'baz'))
        self.assertEqual(None, get_explicit(d, b'baz'))


    def test_unicode_attr_names(self):

        class Foo(object):
            bar = 'baz'

        self.assertEqual('baz', getattr(Foo(), u'bar'))
        self.assertEqual('baz', getattr(Foo(), b'bar'))


    def test_comparison(self):
        """In Python 3 str and byte comparison always evaluates to false."""
        self.assertIs(equals_explicit(b'foo', b'foo'), True)
        self.assertIs(equals_explicit(u'bar', u'bar'), True)
        self.assertIs(equals_explicit(u'bar', b'bar'), False)


def test_main():
    test_support.run_unittest(TestExplicitEncoding)
