from collections import Mapping

class frozendict(Mapping):
    def __init__(self, *args, **kwargs):
        self._d = dict(*args, **kwargs)
        self._hash = None

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __getitem__(self, key):
        return self._d[key]

    def __hash__(self):
        # It would have been simpler and maybe more obvious to 
        # use hash(tuple(sorted(self._d.iteritems()))) from this discussion
        # so far, but this solution is O(n). I don't know what kind of 
        # n we are going to run into, but sometimes it's hard to resist the 
        # urge to optimize when it will gain improved algorithmic performance.
        if self._hash is None:
            self._hash = 0
            for pair in self.iteritems():
                self._hash ^= hash(pair)
        return self._hash


f = frozendict(a=1)
print(f['a'])




class immutable(object):
    def __new__(cls, **attrs):
        print('BEFORE.')
        self = super(immutable, cls).__new__(cls)
        self.__dict__ = frozendict(attrs)
        print('AFTER.')
        return self


i = immutable(a=1)
print(i)


print(i.__dict__)
print(i.__dict__['a'])
print(i.a)

# demo immutable assignment:
from traceback import print_exc
try:
    i.a = 2
except:
    print_exc()


# demo immutable deletion
try:
    del i.a
except:
    print_exc()


DEMO = '''
$ ./python immutable.py                                                                                           ⏎ ⬆ ✭ ✱ ◼
1
BEFORE.
AFTER.
<__main__.immutable object at 0x7f8d49580a90>
<__main__.frozendict object at 0x7f8d49580ac8>
1
1
Traceback (most recent call last):
  File "immutable.py", line 56, in <module>
    i.a = 2
TypeError: 'frozendict' object does not support item assignment
Traceback (most recent call last):
  File "immutable.py", line 63, in <module>
    del i.a
TypeError: 'frozendict' object does not support item deletion
'''
