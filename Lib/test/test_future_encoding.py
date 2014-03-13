# Does it crash??
from __future__ import explicit_encoding

import unittest
from . import test_support


class TestExplicitEncoding(unittest.TestCase):

    def test_doesnt_crash(self):
        self.assertTrue(True)

def test_main():
    test_support.run_unittest(TestExplicitEncoding)
