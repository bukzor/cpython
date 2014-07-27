#!/usr/bin/env python3
#
# Argument Clinic
# Copyright 2012-2013 by Larry Hastings.
# Licensed to the PSF under a contributor agreement.
#

import abc
import ast
import atexit
import collections
import contextlib
import copy
import cpp
import functools
import hashlib
import inspect
import io
import itertools
import os
import pprint
import re
import shlex
import string
import sys
import tempfile
import textwrap
import traceback
import uuid

# TODO:
#
# soon:
#
# * allow mixing any two of {positional-only, positional-or-keyword,
#   keyword-only}
#       * dict constructor uses positional-only and keyword-only
#       * max and min use positional only with an optional group
#         and keyword-only
#

version = '1'

_empty = inspect._empty
_void = inspect._void

NoneType = type(None)

class Unspecified:
    def __repr__(self):
        return '<Unspecified>'

unspecified = Unspecified()


class Null:
    def __repr__(self):
        return '<Null>'

NULL = Null()


class Unknown:
    def __repr__(self):
        return '<Unknown>'

unknown = Unknown()


def _text_accumulator():
    text = []
    def output():
        s = ''.join(text)
        text.clear()
        return s
    return text, text.append, output


def text_accumulator():
    """
    Creates a simple text accumulator / joiner.

    Returns a pair of callables:
        append, output
    "append" appends a string to the accumulator.
    "output" returns the contents of the accumulator
       joined together (''.join(accumulator)) and
       empties the accumulator.
    """
    text, append, output = _text_accumulator()
    return append, output


def warn_or_fail(fail=False, *args, filename=None, line_number=None):
    joined = " ".join([str(a) for a in args])
    add, output = text_accumulator()
    if fail:
        add("Error")
    else:
        add("Warning")
    if clinic:
        if filename is None:
            filename = clinic.filename
        if getattr(clinic, 'block_parser', None) and (line_number is None):
            line_number = clinic.block_parser.line_number
    if filename is not None:
        add(' in file "' + filename + '"')
    if line_number is not None:
        add(" on line " + str(line_number))
    add(':\n')
    add(joined)
    print(output())
    if fail:
        sys.exit(-1)


def warn(*args, filename=None, line_number=None):
    return warn_or_fail(False, *args, filename=filename, line_number=line_number)

def fail(*args, filename=None, line_number=None):
    return warn_or_fail(True, *args, filename=filename, line_number=line_number)


def quoted_for_c_string(s):
    for old, new in (
        ('\\', '\\\\'), # must be first!
        ('"', '\\"'),
        ("'", "\\'"),
        ):
        s = s.replace(old, new)
    return s

def c_repr(s):
    return '"' + s + '"'


is_legal_c_identifier = re.compile('^[A-Za-z_][A-Za-z0-9_]*$').match

def is_legal_py_identifier(s):
    return all(is_legal_c_identifier(field) for field in s.split('.'))

# identifiers that are okay in Python but aren't a good idea in C.
# so if they're used Argument Clinic will add "_value" to the end
# of the name in C.
c_keywords = set("""
asm auto break case char const continue default do double
else enum extern float for goto if inline int long
register return short signed sizeof static struct switch
typedef typeof union unsigned void volatile while
""".strip().split())

def ensure_legal_c_identifier(s):
    # for now, just complain if what we're given isn't legal
    if not is_legal_c_identifier(s):
        fail("Illegal C identifier: {}".format(s))
    # but if we picked a C keyword, pick something else
    if s in c_keywords:
        return s + "_value"
    return s

def rstrip_lines(s):
    text, add, output = _text_accumulator()
    for line in s.split('\n'):
        add(line.rstrip())
        add('\n')
    text.pop()
    return output()

def linear_format(s, **kwargs):
    """
    Perform str.format-like substitution, except:
      * The strings substituted must be on lines by
        themselves.  (This line is the "source line".)
      * If the substitution text is empty, the source line
        is removed in the output.
      * If the field is not recognized, the original line
        is passed unmodified through to the output.
      * If the substitution text is not empty:
          * Each line of the substituted text is indented
            by the indent of the source line.
          * A newline will be added to the end.
    """

    add, output = text_accumulator()
    for line in s.split('\n'):
        indent, curly, trailing = line.partition('{')
        if not curly:
            add(line)
            add('\n')
            continue

        name, curl, trailing = trailing.partition('}')
        if not curly or name not in kwargs:
            add(line)
            add('\n')
            continue

        if trailing:
            fail("Text found after {" + name + "} block marker!  It must be on a line by itself.")
        if indent.strip():
            fail("Non-whitespace characters found before {" + name + "} block marker!  It must be on a line by itself.")

        value = kwargs[name]
        if not value:
            continue

        value = textwrap.indent(rstrip_lines(value), indent)
        add(value)
        add('\n')

    return output()[:-1]

def indent_all_lines(s, prefix):
    """
    Returns 's', with 'prefix' prepended to all lines.

    If the last line is empty, prefix is not prepended
    to it.  (If s is blank, returns s unchanged.)

    (textwrap.indent only adds to non-blank lines.)
    """
    split = s.split('\n')
    last = split.pop()
    final = []
    for line in split:
        final.append(prefix)
        final.append(line)
        final.append('\n')
    if last:
        final.append(prefix)
        final.append(last)
    return ''.join(final)

def suffix_all_lines(s, suffix):
    """
    Returns 's', with 'suffix' appended to all lines.

    If the last line is empty, suffix is not appended
    to it.  (If s is blank, returns s unchanged.)
    """
    split = s.split('\n')
    last = split.pop()
    final = []
    for line in split:
        final.append(line)
        final.append(suffix)
        final.append('\n')
    if last:
        final.append(last)
        final.append(suffix)
    return ''.join(final)


def version_splitter(s):
    """Splits a version string into a tuple of integers.

    The following ASCII characters are allowed, and employ
    the following conversions:
        a -> -3
        b -> -2
        c -> -1
    (This permits Python-style version strings such as "1.4b3".)
    """
    version = []
    accumulator = []
    def flush():
        if not accumulator:
            raise ValueError('Unsupported version string: ' + repr(s))
        version.append(int(''.join(accumulator)))
        accumulator.clear()

    for c in s:
        if c.isdigit():
            accumulator.append(c)
        elif c == '.':
            flush()
        elif c in 'abc':
            flush()
            version.append('abc'.index(c) - 3)
        else:
            raise ValueError('Illegal character ' + repr(c) + ' in version string ' + repr(s))
    flush()
    return tuple(version)

def version_comparitor(version1, version2):
    iterator = itertools.zip_longest(version_splitter(version1), version_splitter(version2), fillvalue=0)
    for i, (a, b) in enumerate(iterator):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


class CRenderData:
    def __init__(self):

        # The C statements to declare variables.
        # Should be full lines with \n eol characters.
        self.declarations = []

        # The C statements required to initialize the variables before the parse call.
        # Should be full lines with \n eol characters.
        self.initializers = []

        # The C statements needed to dynamically modify the values
        # parsed by the parse call, before calling the impl.
        self.modifications = []

        # The entries for the "keywords" array for PyArg_ParseTuple.
        # Should be individual strings representing the names.
        self.keywords = []

        # The "format units" for PyArg_ParseTuple.
        # Should be individual strings that will get
        self.format_units = []

        # The varargs arguments for PyArg_ParseTuple.
        self.parse_arguments = []

        # The parameter declarations for the impl function.
        self.impl_parameters = []

        # The arguments to the impl function at the time it's called.
        self.impl_arguments = []

        # For return converters: the name of the variable that
        # should receive the value returned by the impl.
        self.return_value = "return_value"

        # For return converters: the code to convert the return
        # value from the parse function.  This is also where
        # you should check the _return_value for errors, and
        # "goto exit" if there are any.
        self.return_conversion = []

        # The C statements required to clean up after the impl call.
        self.cleanup = []


class FormatCounterFormatter(string.Formatter):
    """
    This counts how many instances of each formatter
    "replacement string" appear in the format string.

    e.g. after evaluating "string {a}, {b}, {c}, {a}"
         the counts dict would now look like
         {'a': 2, 'b': 1, 'c': 1}
    """
    def __init__(self):
        self.counts = collections.Counter()

    def get_value(self, key, args, kwargs):
        self.counts[key] += 1
        return ''

class Language(metaclass=abc.ABCMeta):

    start_line = ""
    body_prefix = ""
    stop_line = ""
    checksum_line = ""

    def __init__(self, filename):
        pass

    @abc.abstractmethod
    def render(self, clinic, signatures):
        pass

    def parse_line(self, line):
        pass

    def validate(self):
        def assert_only_one(attr, *additional_fields):
            """
            Ensures that the string found at getattr(self, attr)
            contains exactly one formatter replacement string for
            each valid field.  The list of valid fields is
            ['dsl_name'] extended by additional_fields.

            e.g.
                self.fmt = "{dsl_name} {a} {b}"

                # this passes
                self.assert_only_one('fmt', 'a', 'b')

                # this fails, the format string has a {b} in it
                self.assert_only_one('fmt', 'a')

                # this fails, the format string doesn't have a {c} in it
                self.assert_only_one('fmt', 'a', 'b', 'c')

                # this fails, the format string has two {a}s in it,
                # it must contain exactly one
                self.fmt2 = '{dsl_name} {a} {a}'
                self.assert_only_one('fmt2', 'a')

            """
            fields = ['dsl_name']
            fields.extend(additional_fields)
            line = getattr(self, attr)
            fcf = FormatCounterFormatter()
            fcf.format(line)
            def local_fail(should_be_there_but_isnt):
                if should_be_there_but_isnt:
                    fail("{} {} must contain {{{}}} exactly once!".format(
                        self.__class__.__name__, attr, name))
                else:
                    fail("{} {} must not contain {{{}}}!".format(
                        self.__class__.__name__, attr, name))

            for name, count in fcf.counts.items():
                if name in fields:
                    if count > 1:
                        local_fail(True)
                else:
                    local_fail(False)
            for name in fields:
                if fcf.counts.get(name) != 1:
                    local_fail(True)

        assert_only_one('start_line')
        assert_only_one('stop_line')

        field = "arguments" if "{arguments}" in self.checksum_line else "checksum"
        assert_only_one('checksum_line', field)



class PythonLanguage(Language):

    language      = 'Python'
    start_line    = "#/*[{dsl_name} input]"
    body_prefix   = "#"
    stop_line     = "#[{dsl_name} start generated code]*/"
    checksum_line = "#/*[{dsl_name} end generated code: {arguments}]*/"


def permute_left_option_groups(l):
    """
    Given [1, 2, 3], should yield:
       ()
       (3,)
       (2, 3)
       (1, 2, 3)
    """
    yield tuple()
    accumulator = []
    for group in reversed(l):
        accumulator = list(group) + accumulator
        yield tuple(accumulator)


def permute_right_option_groups(l):
    """
    Given [1, 2, 3], should yield:
      ()
      (1,)
      (1, 2)
      (1, 2, 3)
    """
    yield tuple()
    accumulator = []
    for group in l:
        accumulator.extend(group)
        yield tuple(accumulator)


def permute_optional_groups(left, required, right):
    """
    Generator function that computes the set of acceptable
    argument lists for the provided iterables of
    argument groups.  (Actually it generates a tuple of tuples.)

    Algorithm: prefer left options over right options.

    If required is empty, left must also be empty.
    """
    required = tuple(required)
    result = []

    if not required:
        assert not left

    accumulator = []
    counts = set()
    for r in permute_right_option_groups(right):
        for l in permute_left_option_groups(left):
            t = l + required + r
            if len(t) in counts:
                continue
            counts.add(len(t))
            accumulator.append(t)

    accumulator.sort(key=len)
    return tuple(accumulator)


def strip_leading_and_trailing_blank_lines(s):
    lines = s.rstrip().split('\n')
    while lines:
        line = lines[0]
        if line.strip():
            break
        del lines[0]
    return '\n'.join(lines)

@functools.lru_cache()
def normalize_snippet(s, *, indent=0):
    """
    Reformats s:
        * removes leading and trailing blank lines
        * ensures that it does not end with a newline
        * dedents so the first nonwhite character on any line is at column "indent"
    """
    s = strip_leading_and_trailing_blank_lines(s)
    s = textwrap.dedent(s)
    if indent:
        s = textwrap.indent(s, ' ' * indent)
    return s


class CLanguage(Language):

    body_prefix   = "#"
    language      = 'C'
    start_line    = "/*[{dsl_name} input]"
    body_prefix   = ""
    stop_line     = "[{dsl_name} start generated code]*/"
    checksum_line = "/*[{dsl_name} end generated code: {arguments}]*/"

    def __init__(self, filename):
        super().__init__(filename)
        self.cpp = cpp.Monitor(filename)
        self.cpp.fail = fail

    def parse_line(self, line):
        self.cpp.writeline(line)

    def render(self, clinic, signatures):
        function = None
        for o in signatures:
            if isinstance(o, Function):
                if function:
                    fail("You may specify at most one function per block.\nFound a block containing at least two:\n\t" + repr(function) + " and " + repr(o))
                function = o
        return self.render_function(clinic, function)

    def docstring_for_c_string(self, f):
        text, add, output = _text_accumulator()
        # turn docstring into a properly quoted C string
        for line in f.docstring.split('\n'):
            add('"')
            add(quoted_for_c_string(line))
            add('\\n"\n')

        text.pop()
        add('"')
        return ''.join(text)

    def output_templates(self, f):
        parameters = list(f.parameters.values())
        assert parameters
        assert isinstance(parameters[0].converter, self_converter)
        del parameters[0]
        converters = [p.converter for p in parameters]

        has_option_groups = parameters and (parameters[0].group or parameters[-1].group)
        default_return_converter = (not f.return_converter or
            f.return_converter.type == 'PyObject *')

        positional = parameters and (parameters[-1].kind == inspect.Parameter.POSITIONAL_ONLY)
        all_boring_objects = False # yes, this will be false if there are 0 parameters, it's fine
        first_optional = len(parameters)
        for i, p in enumerate(parameters):
            c = p.converter
            if type(c) != object_converter:
                break
            if c.format_unit != 'O':
                break
            if p.default is not unspecified:
                first_optional = min(first_optional, i)
        else:
            all_boring_objects = True

        new_or_init = f.kind in (METHOD_NEW, METHOD_INIT)

        meth_o = (len(parameters) == 1 and
              parameters[0].kind == inspect.Parameter.POSITIONAL_ONLY and
              not converters[0].is_optional() and
              isinstance(converters[0], object_converter) and
              converters[0].format_unit == 'O' and
              not new_or_init)

        # we have to set these things before we're done:
        #
        # docstring_prototype
        # docstring_definition
        # impl_prototype
        # methoddef_define
        # parser_prototype
        # parser_definition
        # impl_definition
        # cpp_if
        # cpp_endif
        # methoddef_ifndef

        return_value_declaration = "PyObject *return_value = NULL;"

        methoddef_define = normalize_snippet("""
            #define {methoddef_name}    \\
                {{"{name}", (PyCFunction){c_basename}, {methoddef_flags}, {c_basename}__doc__}},
            """)
        if new_or_init and not f.docstring:
            docstring_prototype = docstring_definition = ''
        else:
            docstring_prototype = normalize_snippet("""
                PyDoc_VAR({c_basename}__doc__);
                """)
            docstring_definition = normalize_snippet("""
                PyDoc_STRVAR({c_basename}__doc__,
                {docstring});
                """)
        impl_definition = normalize_snippet("""
            static {impl_return_type}
            {c_basename}_impl({impl_parameters})
            """)
        impl_prototype = parser_prototype = parser_definition = None

        parser_prototype_keyword = normalize_snippet("""
            static PyObject *
            {c_basename}({self_type}{self_name}, PyObject *args, PyObject *kwargs)
            """)

        parser_prototype_varargs = normalize_snippet("""
            static PyObject *
            {c_basename}({self_type}{self_name}, PyObject *args)
            """)

        # parser_body_fields remembers the fields passed in to the
        # previous call to parser_body. this is used for an awful hack.
        parser_body_fields = ()
        def parser_body(prototype, *fields):
            nonlocal parser_body_fields
            add, output = text_accumulator()
            add(prototype)
            parser_body_fields = fields

            fields = list(fields)
            fields.insert(0, normalize_snippet("""
                {{
                    {return_value_declaration}
                    {declarations}
                    {initializers}
                """) + "\n")
            # just imagine--your code is here in the middle
            fields.append(normalize_snippet("""
                    {modifications}
                    {return_value} = {c_basename}_impl({impl_arguments});
                    {return_conversion}

                {exit_label}
                    {cleanup}
                    return return_value;
                }}
                """))
            for field in fields:
                add('\n')
                add(field)
            return output()

        def insert_keywords(s):
            return linear_format(s, declarations="static char *_keywords[] = {{{keywords}, NULL}};\n{declarations}")

        if not parameters:
            # no parameters, METH_NOARGS

            flags = "METH_NOARGS"

            parser_prototype = normalize_snippet("""
                static PyObject *
                {c_basename}({self_type}{self_name}, PyObject *Py_UNUSED(ignored))
                """)
            parser_definition = parser_prototype

            if default_return_converter:
                parser_definition = parser_prototype + '\n' + normalize_snippet("""
                    {{
                        return {c_basename}_impl({impl_arguments});
                    }}
                    """)
            else:
                parser_definition = parser_body(parser_prototype)

        elif meth_o:
            flags = "METH_O"

            meth_o_prototype = normalize_snippet("""
                static PyObject *
                {c_basename}({impl_parameters})
                """)

            if default_return_converter:
                # maps perfectly to METH_O, doesn't need a return converter.
                # so we skip making a parse function
                # and call directly into the impl function.
                impl_prototype = parser_prototype = parser_definition = ''
                impl_definition = meth_o_prototype
            else:
                # SLIGHT HACK
                # use impl_parameters for the parser here!
                parser_prototype = meth_o_prototype
                parser_definition = parser_body(parser_prototype)

        elif has_option_groups:
            # positional parameters with option groups
            # (we have to generate lots of PyArg_ParseTuple calls
            #  in a big switch statement)

            flags = "METH_VARARGS"
            parser_prototype = parser_prototype_varargs

            parser_definition = parser_body(parser_prototype, '    {option_group_parsing}')

        elif positional and all_boring_objects:
            # positional-only, but no option groups,
            # and nothing but normal objects:
            # PyArg_UnpackTuple!

            flags = "METH_VARARGS"
            parser_prototype = parser_prototype_varargs

            parser_definition = parser_body(parser_prototype, normalize_snippet("""
                if (!PyArg_UnpackTuple(args, "{name}",
                    {unpack_min}, {unpack_max},
                    {parse_arguments}))
                    goto exit;
                """, indent=4))

        elif positional:
            # positional-only, but no option groups
            # we only need one call to PyArg_ParseTuple

            flags = "METH_VARARGS"
            parser_prototype = parser_prototype_varargs

            parser_definition = parser_body(parser_prototype, normalize_snippet("""
                if (!PyArg_ParseTuple(args,
                    "{format_units}:{name}",
                    {parse_arguments}))
                    goto exit;
                """, indent=4))

        else:
            # positional-or-keyword arguments
            flags = "METH_VARARGS|METH_KEYWORDS"

            parser_prototype = parser_prototype_keyword

            body = normalize_snippet("""
                if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                    "{format_units}:{name}", _keywords,
                    {parse_arguments}))
                    goto exit;
            """, indent=4)
            parser_definition = parser_body(parser_prototype, normalize_snippet("""
                if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                    "{format_units}:{name}", _keywords,
                    {parse_arguments}))
                    goto exit;
                """, indent=4))
            parser_definition = insert_keywords(parser_definition)


        if new_or_init:
            methoddef_define = ''

            if f.kind == METHOD_NEW:
                parser_prototype = parser_prototype_keyword
            else:
                return_value_declaration = "int return_value = -1;"
                parser_prototype = normalize_snippet("""
                    static int
                    {c_basename}({self_type}{self_name}, PyObject *args, PyObject *kwargs)
                    """)

            fields = list(parser_body_fields)
            parses_positional = 'METH_NOARGS' not in flags
            parses_keywords = 'METH_KEYWORDS' in flags
            if parses_keywords:
                assert parses_positional

            if not parses_keywords:
                fields.insert(0, normalize_snippet("""
                    if ({self_type_check}!_PyArg_NoKeywords("{name}", kwargs))
                        goto exit;
                    """, indent=4))
                if not parses_positional:
                    fields.insert(0, normalize_snippet("""
                        if ({self_type_check}!_PyArg_NoPositional("{name}", args))
                            goto exit;
                        """, indent=4))

            parser_definition = parser_body(parser_prototype, *fields)
            if parses_keywords:
                parser_definition = insert_keywords(parser_definition)


        if f.methoddef_flags:
            flags += '|' + f.methoddef_flags

        methoddef_define = methoddef_define.replace('{methoddef_flags}', flags)

        methoddef_ifndef = ''
        conditional = self.cpp.condition()
        if not conditional:
            cpp_if = cpp_endif = ''
        else:
            cpp_if = "#if " + conditional
            cpp_endif = "#endif /* " + conditional + " */"

            if methoddef_define:
                methoddef_ifndef = normalize_snippet("""
                    #ifndef {methoddef_name}
                        #define {methoddef_name}
                    #endif /* !defined({methoddef_name}) */
                    """)


        # add ';' to the end of parser_prototype and impl_prototype
        # (they mustn't be None, but they could be an empty string.)
        assert parser_prototype is not None
        if parser_prototype:
            assert not parser_prototype.endswith(';')
            parser_prototype += ';'

        if impl_prototype is None:
            impl_prototype = impl_definition
        if impl_prototype:
            impl_prototype += ";"

        parser_definition = parser_definition.replace("{return_value_declaration}", return_value_declaration)

        d = {
            "docstring_prototype" : docstring_prototype,
            "docstring_definition" : docstring_definition,
            "impl_prototype" : impl_prototype,
            "methoddef_define" : methoddef_define,
            "parser_prototype" : parser_prototype,
            "parser_definition" : parser_definition,
            "impl_definition" : impl_definition,
            "cpp_if" : cpp_if,
            "cpp_endif" : cpp_endif,
            "methoddef_ifndef" : methoddef_ifndef,
        }

        # make sure we didn't forget to assign something,
        # and wrap each non-empty value in \n's
        d2 = {}
        for name, value in d.items():
            assert value is not None, "got a None value for template " + repr(name)
            if value:
                value = '\n' + value + '\n'
            d2[name] = value
        return d2

    @staticmethod
    def group_to_variable_name(group):
        adjective = "left_" if group < 0 else "right_"
        return "group_" + adjective + str(abs(group))

    def render_option_group_parsing(self, f, template_dict):
        # positional only, grouped, optional arguments!
        # can be optional on the left or right.
        # here's an example:
        #
        # [ [ [ A1 A2 ] B1 B2 B3 ] C1 C2 ] D1 D2 D3 [ E1 E2 E3 [ F1 F2 F3 ] ]
        #
        # Here group D are required, and all other groups are optional.
        # (Group D's "group" is actually None.)
        # We can figure out which sets of arguments we have based on
        # how many arguments are in the tuple.
        #
        # Note that you need to count up on both sides.  For example,
        # you could have groups C+D, or C+D+E, or C+D+E+F.
        #
        # What if the number of arguments leads us to an ambiguous result?
        # Clinic prefers groups on the left.  So in the above example,
        # five arguments would map to B+C, not C+D.

        add, output = text_accumulator()
        parameters = list(f.parameters.values())
        if isinstance(parameters[0].converter, self_converter):
            del parameters[0]

        groups = []
        group = None
        left = []
        right = []
        required = []
        last = unspecified

        for p in parameters:
            group_id = p.group
            if group_id != last:
                last = group_id
                group = []
                if group_id < 0:
                    left.append(group)
                elif group_id == 0:
                    group = required
                else:
                    right.append(group)
            group.append(p)

        count_min = sys.maxsize
        count_max = -1

        add("switch (PyTuple_GET_SIZE(args)) {{\n")
        for subset in permute_optional_groups(left, required, right):
            count = len(subset)
            count_min = min(count_min, count)
            count_max = max(count_max, count)

            if count == 0:
                add("""    case 0:
        break;
""")
                continue

            group_ids = {p.group for p in subset}  # eliminate duplicates
            d = {}
            d['count'] = count
            d['name'] = f.name
            d['groups'] = sorted(group_ids)
            d['format_units'] = "".join(p.converter.format_unit for p in subset)

            parse_arguments = []
            for p in subset:
                p.converter.parse_argument(parse_arguments)
            d['parse_arguments'] = ", ".join(parse_arguments)

            group_ids.discard(0)
            lines = [self.group_to_variable_name(g) + " = 1;" for g in group_ids]
            lines = "\n".join(lines)

            s = """
    case {count}:
        if (!PyArg_ParseTuple(args, "{format_units}:{name}", {parse_arguments}))
            goto exit;
        {group_booleans}
        break;
"""[1:]
            s = linear_format(s, group_booleans=lines)
            s = s.format_map(d)
            add(s)

        add("    default:\n")
        s = '        PyErr_SetString(PyExc_TypeError, "{} requires {} to {} arguments");\n'
        add(s.format(f.full_name, count_min, count_max))
        add('        goto exit;\n')
        add("}}")
        template_dict['option_group_parsing'] = output()

    def render_function(self, clinic, f):
        if not f:
            return ""

        add, output = text_accumulator()
        data = CRenderData()

        assert f.parameters, "We should always have a 'self' at this point!"
        parameters = f.render_parameters
        converters = [p.converter for p in parameters]

        templates = self.output_templates(f)

        f_self = parameters[0]
        selfless = parameters[1:]
        assert isinstance(f_self.converter, self_converter), "No self parameter in " + repr(f.full_name) + "!"

        last_group = 0
        first_optional = len(selfless)
        positional = selfless and selfless[-1].kind == inspect.Parameter.POSITIONAL_ONLY
        new_or_init = f.kind in (METHOD_NEW, METHOD_INIT)
        default_return_converter = (not f.return_converter or
            f.return_converter.type == 'PyObject *')
        has_option_groups = False

        # offset i by -1 because first_optional needs to ignore self
        for i, p in enumerate(parameters, -1):
            c = p.converter

            if (i != -1) and (p.default is not unspecified):
                first_optional = min(first_optional, i)

            # insert group variable
            group = p.group
            if last_group != group:
                last_group = group
                if group:
                    group_name = self.group_to_variable_name(group)
                    data.impl_arguments.append(group_name)
                    data.declarations.append("int " + group_name + " = 0;")
                    data.impl_parameters.append("int " + group_name)
                    has_option_groups = True

            c.render(p, data)

        if has_option_groups and (not positional):
            fail("You cannot use optional groups ('[' and ']')\nunless all parameters are positional-only ('/').")

        # HACK
        # when we're METH_O, but have a custom return converter,
        # we use "impl_parameters" for the parsing function
        # because that works better.  but that means we must
        # supress actually declaring the impl's parameters
        # as variables in the parsing function.  but since it's
        # METH_O, we have exactly one anyway, so we know exactly
        # where it is.
        if ("METH_O" in templates['methoddef_define'] and
            not default_return_converter):
            data.declarations.pop(0)

        template_dict = {}

        full_name = f.full_name
        template_dict['full_name'] = full_name

        if new_or_init:
            name = f.cls.name
        else:
            name = f.name

        template_dict['name'] = name

        if f.c_basename:
            c_basename = f.c_basename
        else:
            fields = full_name.split(".")
            if fields[-1] == '__new__':
                fields.pop()
            c_basename = "_".join(fields)

        template_dict['c_basename'] = c_basename

        methoddef_name = "{}_METHODDEF".format(c_basename.upper())
        template_dict['methoddef_name'] = methoddef_name

        template_dict['docstring'] = self.docstring_for_c_string(f)

        template_dict['self_name'] = template_dict['self_type'] = template_dict['self_type_check'] = ''
        f_self.converter.set_template_dict(template_dict)

        f.return_converter.render(f, data)
        template_dict['impl_return_type'] = f.return_converter.type

        template_dict['declarations'] = "\n".join(data.declarations)
        template_dict['initializers'] = "\n\n".join(data.initializers)
        template_dict['modifications'] = '\n\n'.join(data.modifications)
        template_dict['keywords'] = '"' + '", "'.join(data.keywords) + '"'
        template_dict['format_units'] = ''.join(data.format_units)
        template_dict['parse_arguments'] = ', '.join(data.parse_arguments)
        template_dict['impl_parameters'] = ", ".join(data.impl_parameters)
        template_dict['impl_arguments'] = ", ".join(data.impl_arguments)
        template_dict['return_conversion'] = "".join(data.return_conversion).rstrip()
        template_dict['cleanup'] = "".join(data.cleanup)
        template_dict['return_value'] = data.return_value

        # used by unpack tuple code generator
        ignore_self = -1 if isinstance(converters[0], self_converter) else 0
        unpack_min = first_optional
        unpack_max = len(selfless)
        template_dict['unpack_min'] = str(unpack_min)
        template_dict['unpack_max'] = str(unpack_max)

        if has_option_groups:
            self.render_option_group_parsing(f, template_dict)

        for name, destination in clinic.field_destinations.items():
            template = templates[name]
            if has_option_groups:
                template = linear_format(template,
                        option_group_parsing=template_dict['option_group_parsing'])
            template = linear_format(template,
                declarations=template_dict['declarations'],
                return_conversion=template_dict['return_conversion'],
                initializers=template_dict['initializers'],
                modifications=template_dict['modifications'],
                cleanup=template_dict['cleanup'],
                )

            # Only generate the "exit:" label
            # if we have any gotos
            need_exit_label = "goto exit;" in template
            template = linear_format(template,
                exit_label="exit:" if need_exit_label else ''
                )

            s = template.format_map(template_dict)

            if clinic.line_prefix:
                s = indent_all_lines(s, clinic.line_prefix)
            if clinic.line_suffix:
                s = suffix_all_lines(s, clinic.line_suffix)

            destination.append(s)

        return clinic.get_destination('block').dump()




@contextlib.contextmanager
def OverrideStdioWith(stdout):
    saved_stdout = sys.stdout
    sys.stdout = stdout
    try:
        yield
    finally:
        assert sys.stdout is stdout
        sys.stdout = saved_stdout


def create_regex(before, after, word=True, whole_line=True):
    """Create an re object for matching marker lines."""
    group_re = "\w+" if word else ".+"
    pattern = r'{}({}){}'
    if whole_line:
        pattern = '^' + pattern + '$'
    pattern = pattern.format(re.escape(before), group_re, re.escape(after))
    return re.compile(pattern)


class Block:
    r"""
    Represents a single block of text embedded in
    another file.  If dsl_name is None, the block represents
    verbatim text, raw original text from the file, in
    which case "input" will be the only non-false member.
    If dsl_name is not None, the block represents a Clinic
    block.

    input is always str, with embedded \n characters.
    input represents the original text from the file;
    if it's a Clinic block, it is the original text with
    the body_prefix and redundant leading whitespace removed.

    dsl_name is either str or None.  If str, it's the text
    found on the start line of the block between the square
    brackets.

    signatures is either list or None.  If it's a list,
    it may only contain clinic.Module, clinic.Class, and
    clinic.Function objects.  At the moment it should
    contain at most one of each.

    output is either str or None.  If str, it's the output
    from this block, with embedded '\n' characters.

    indent is either str or None.  It's the leading whitespace
    that was found on every line of input.  (If body_prefix is
    not empty, this is the indent *after* removing the
    body_prefix.)

    preindent is either str or None.  It's the whitespace that
    was found in front of every line of input *before* the
    "body_prefix" (see the Language object).  If body_prefix
    is empty, preindent must always be empty too.

    To illustrate indent and preindent: Assume that '_'
    represents whitespace.  If the block processed was in a
    Python file, and looked like this:
      ____#/*[python]
      ____#__for a in range(20):
      ____#____print(a)
      ____#[python]*/
    "preindent" would be "____" and "indent" would be "__".

    """
    def __init__(self, input, dsl_name=None, signatures=None, output=None, indent='', preindent=''):
        assert isinstance(input, str)
        self.input = input
        self.dsl_name = dsl_name
        self.signatures = signatures or []
        self.output = output
        self.indent = indent
        self.preindent = preindent

    def __repr__(self):
        dsl_name = self.dsl_name or "text"
        def summarize(s):
            s = repr(s)
            if len(s) > 30:
                return s[:26] + "..." + s[0]
            return s
        return "".join((
            "<Block ", dsl_name, " input=", summarize(self.input), " output=", summarize(self.output), ">"))


class BlockParser:
    """
    Block-oriented parser for Argument Clinic.
    Iterator, yields Block objects.
    """

    def __init__(self, input, language, *, verify=True):
        """
        "input" should be a str object
        with embedded \n characters.

        "language" should be a Language object.
        """
        language.validate()

        self.input = collections.deque(reversed(input.splitlines(keepends=True)))
        self.block_start_line_number = self.line_number = 0

        self.language = language
        before, _, after = language.start_line.partition('{dsl_name}')
        assert _ == '{dsl_name}'
        self.find_start_re = create_regex(before, after, whole_line=False)
        self.start_re = create_regex(before, after)
        self.verify = verify
        self.last_checksum_re = None
        self.last_dsl_name = None
        self.dsl_name = None
        self.first_block = True

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            if not self.input:
                raise StopIteration

            if self.dsl_name:
                return_value = self.parse_clinic_block(self.dsl_name)
                self.dsl_name = None
                self.first_block = False
                return return_value
            block = self.parse_verbatim_block()
            if self.first_block and not block.input:
                continue
            self.first_block = False
            return block


    def is_start_line(self, line):
        match = self.start_re.match(line.lstrip())
        return match.group(1) if match else None

    def _line(self, lookahead=False):
        self.line_number += 1
        line = self.input.pop()
        if not lookahead:
            self.language.parse_line(line)
        return line

    def parse_verbatim_block(self):
        add, output = text_accumulator()
        self.block_start_line_number = self.line_number

        while self.input:
            line = self._line()
            dsl_name = self.is_start_line(line)
            if dsl_name:
                self.dsl_name = dsl_name
                break
            add(line)

        return Block(output())

    def parse_clinic_block(self, dsl_name):
        input_add, input_output = text_accumulator()
        self.block_start_line_number = self.line_number + 1
        stop_line = self.language.stop_line.format(dsl_name=dsl_name)
        body_prefix = self.language.body_prefix.format(dsl_name=dsl_name)

        def is_stop_line(line):
            # make sure to recognize stop line even if it
            # doesn't end with EOL (it could be the very end of the file)
            if not line.startswith(stop_line):
                return False
            remainder = line[len(stop_line):]
            return (not remainder) or remainder.isspace()

        # consume body of program
        while self.input:
            line = self._line()
            if is_stop_line(line) or self.is_start_line(line):
                break
            if body_prefix:
                line = line.lstrip()
                assert line.startswith(body_prefix)
                line = line[len(body_prefix):]
            input_add(line)

        # consume output and checksum line, if present.
        if self.last_dsl_name == dsl_name:
            checksum_re = self.last_checksum_re
        else:
            before, _, after = self.language.checksum_line.format(dsl_name=dsl_name, arguments='{arguments}').partition('{arguments}')
            assert _ == '{arguments}'
            checksum_re = create_regex(before, after, word=False)
            self.last_dsl_name = dsl_name
            self.last_checksum_re = checksum_re

        # scan forward for checksum line
        output_add, output_output = text_accumulator()
        arguments = None
        while self.input:
            line = self._line(lookahead=True)
            match = checksum_re.match(line.lstrip())
            arguments = match.group(1) if match else None
            if arguments:
                break
            output_add(line)
            if self.is_start_line(line):
                break

        output = output_output()
        if arguments:
            d = {}
            for field in shlex.split(arguments):
                name, equals, value = field.partition('=')
                if not equals:
                    fail("Mangled Argument Clinic marker line: {!r}".format(line))
                d[name.strip()] = value.strip()

            if self.verify:
                if 'input' in d:
                    checksum = d['output']
                    input_checksum = d['input']
                else:
                    checksum = d['checksum']
                    input_checksum = None

                computed = compute_checksum(output, len(checksum))
                if checksum != computed:
                    fail("Checksum mismatch!\nExpected: {}\nComputed: {}\n"
                         "Suggested fix: remove all generated code including "
                         "the end marker,\n"
                         "or use the '-f' option."
                        .format(checksum, computed))
        else:
            # put back output
            output_lines = output.splitlines(keepends=True)
            self.line_number -= len(output_lines)
            self.input.extend(reversed(output_lines))
            output = None

        return Block(input_output(), dsl_name, output=output)


class BlockPrinter:

    def __init__(self, language, f=None):
        self.language = language
        self.f = f or io.StringIO()

    def print_block(self, block):
        input = block.input
        output = block.output
        dsl_name = block.dsl_name
        write = self.f.write

        assert not ((dsl_name == None) ^ (output == None)), "you must specify dsl_name and output together, dsl_name " + repr(dsl_name)

        if not dsl_name:
            write(input)
            return

        write(self.language.start_line.format(dsl_name=dsl_name))
        write("\n")

        body_prefix = self.language.body_prefix.format(dsl_name=dsl_name)
        if not body_prefix:
            write(input)
        else:
            for line in input.split('\n'):
                write(body_prefix)
                write(line)
                write("\n")

        write(self.language.stop_line.format(dsl_name=dsl_name))
        write("\n")

        input = ''.join(block.input)
        output = ''.join(block.output)
        if output:
            if not output.endswith('\n'):
                output += '\n'
            write(output)

        arguments="output={} input={}".format(compute_checksum(output, 16), compute_checksum(input, 16))
        write(self.language.checksum_line.format(dsl_name=dsl_name, arguments=arguments))
        write("\n")

    def write(self, text):
        self.f.write(text)


class Destination:
    def __init__(self, name, type, clinic, *args):
        self.name = name
        self.type = type
        self.clinic = clinic
        valid_types = ('buffer', 'file', 'suppress', 'two-pass')
        if type not in valid_types:
            fail("Invalid destination type " + repr(type) + " for " + name + " , must be " + ', '.join(valid_types))
        extra_arguments = 1 if type == "file" else 0
        if len(args) < extra_arguments:
            fail("Not enough arguments for destination " + name + " new " + type)
        if len(args) > extra_arguments:
            fail("Too many arguments for destination " + name + " new " + type)
        if type =='file':
            d = {}
            filename = clinic.filename
            d['path'] = filename
            dirname, basename = os.path.split(filename)
            if not dirname:
                dirname = '.'
            d['dirname'] = dirname
            d['basename'] = basename
            d['basename_root'], d['basename_extension'] = os.path.splitext(filename)
            self.filename = args[0].format_map(d)
        if type == 'two-pass':
            self.id = None

        self.text, self.append, self._dump = _text_accumulator()

    def __repr__(self):
        if self.type == 'file':
            file_repr = " " + repr(self.filename)
        else:
            file_repr = ''
        return "".join(("<Destination ", self.name, " ", self.type, file_repr, ">"))

    def clear(self):
        if self.type != 'buffer':
            fail("Can't clear destination" + self.name + " , it's not of type buffer")
        self.text.clear()

    def dump(self):
        if self.type == 'two-pass':
            if self.id is None:
                self.id = str(uuid.uuid4())
                return self.id
            fail("You can only dump a two-pass buffer exactly once!")
        return self._dump()


# maps strings to Language objects.
# "languages" maps the name of the language ("C", "Python").
# "extensions" maps the file extension ("c", "py").
languages = { 'C': CLanguage, 'Python': PythonLanguage }
extensions = { name: CLanguage for name in "c cc cpp cxx h hh hpp hxx".split() }
extensions['py'] = PythonLanguage


# maps strings to callables.
# these callables must be of the form:
#   def foo(name, default, *, ...)
# The callable may have any number of keyword-only parameters.
# The callable must return a CConverter object.
# The callable should not call builtins.print.
converters = {}

# maps strings to callables.
# these callables follow the same rules as those for "converters" above.
# note however that they will never be called with keyword-only parameters.
legacy_converters = {}


# maps strings to callables.
# these callables must be of the form:
#   def foo(*, ...)
# The callable may have any number of keyword-only parameters.
# The callable must return a CConverter object.
# The callable should not call builtins.print.
return_converters = {}

clinic = None
class Clinic:

    presets_text = """
preset block
everything block
docstring_prototype suppress
parser_prototype suppress
cpp_if suppress
cpp_endif suppress
methoddef_ifndef buffer

preset original
everything block
docstring_prototype suppress
parser_prototype suppress
cpp_if suppress
cpp_endif suppress
methoddef_ifndef buffer

preset file
everything file
docstring_prototype suppress
parser_prototype suppress
impl_definition block

preset buffer
everything buffer
docstring_prototype suppress
impl_prototype suppress
parser_prototype suppress
impl_definition block

preset partial-buffer
everything buffer
docstring_prototype block
impl_prototype suppress
methoddef_define block
parser_prototype block
impl_definition block

preset two-pass
everything buffer
docstring_prototype two-pass
impl_prototype suppress
methoddef_define two-pass
parser_prototype two-pass
impl_definition block

"""

    def __init__(self, language, printer=None, *, force=False, verify=True, filename=None):
        # maps strings to Parser objects.
        # (instantiated from the "parsers" global.)
        self.parsers = {}
        self.language = language
        if printer:
            fail("Custom printers are broken right now")
        self.printer = printer or BlockPrinter(language)
        self.verify = verify
        self.force = force
        self.filename = filename
        self.modules = collections.OrderedDict()
        self.classes = collections.OrderedDict()
        self.functions = []

        self.line_prefix = self.line_suffix = ''

        self.destinations = {}
        self.add_destination("block", "buffer")
        self.add_destination("suppress", "suppress")
        self.add_destination("buffer", "buffer")
        self.add_destination("two-pass", "two-pass")
        if filename:
            self.add_destination("file", "file", "{dirname}/clinic/{basename}.h")

        d = self.destinations.get
        self.field_destinations = collections.OrderedDict((
            ('cpp_if', d('suppress')),
            ('docstring_prototype', d('suppress')),
            ('docstring_definition', d('block')),
            ('methoddef_define', d('block')),
            ('impl_prototype', d('block')),
            ('parser_prototype', d('suppress')),
            ('parser_definition', d('block')),
            ('cpp_endif', d('suppress')),
            ('methoddef_ifndef', d('buffer')),
            ('impl_definition', d('block')),
        ))

        self.field_destinations_stack = []

        self.presets = {}
        preset = None
        for line in self.presets_text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            name, value = line.split()
            if name == 'preset':
                self.presets[value] = preset = collections.OrderedDict()
                continue

            destination = self.get_destination(value)

            if name == 'everything':
                for name in self.field_destinations:
                    preset[name] = destination
                continue

            assert name in self.field_destinations
            preset[name] = destination

        global clinic
        clinic = self

    def get_destination(self, name, default=unspecified):
        d = self.destinations.get(name)
        if not d:
            if default is not unspecified:
                return default
            fail("Destination does not exist: " + repr(name))
        return d

    def add_destination(self, name, type, *args):
        if name in self.destinations:
            fail("Destination already exists: " + repr(name))
        self.destinations[name] = Destination(name, type, self, *args)

    def parse(self, input):
        printer = self.printer
        self.block_parser = BlockParser(input, self.language, verify=self.verify)
        for block in self.block_parser:
            dsl_name = block.dsl_name
            if dsl_name:
                if dsl_name not in self.parsers:
                    assert dsl_name in parsers, "No parser to handle {!r} block.".format(dsl_name)
                    self.parsers[dsl_name] = parsers[dsl_name](self)
                parser = self.parsers[dsl_name]
                try:
                    parser.parse(block)
                except Exception:
                    fail('Exception raised during parsing:\n' +
                         traceback.format_exc().rstrip())
            printer.print_block(block)

        second_pass_replacements = {}

        for name, destination in self.destinations.items():
            if destination.type == 'suppress':
                continue
            output = destination._dump()

            if destination.type == 'two-pass':
                if destination.id:
                    second_pass_replacements[destination.id] = output
                elif output:
                    fail("Two-pass buffer " + repr(name) + " not empty at end of file!")
                continue

            if output:

                block = Block("", dsl_name="clinic", output=output)

                if destination.type == 'buffer':
                    block.input = "dump " + name + "\n"
                    warn("Destination buffer " + repr(name) + " not empty at end of file, emptying.")
                    printer.write("\n")
                    printer.print_block(block)
                    continue

                if destination.type == 'file':
                    try:
                        dirname = os.path.dirname(destination.filename)
                        try:
                            os.makedirs(dirname)
                        except FileExistsError:
                            if not os.path.isdir(dirname):
                                fail("Can't write to destination {}, "
                                     "can't make directory {}!".format(
                                        destination.filename, dirname))
                        if self.verify:
                            with open(destination.filename, "rt") as f:
                                parser_2 = BlockParser(f.read(), language=self.language)
                                blocks = list(parser_2)
                                if (len(blocks) != 1) or (blocks[0].input != 'preserve\n'):
                                    fail("Modified destination file " + repr(destination.filename) + ", not overwriting!")
                    except FileNotFoundError:
                        pass

                    block.input = 'preserve\n'
                    printer_2 = BlockPrinter(self.language)
                    printer_2.print_block(block)
                    with open(destination.filename, "wt") as f:
                        f.write(printer_2.f.getvalue())
                    continue
        text = printer.f.getvalue()

        if second_pass_replacements:
            printer_2 = BlockPrinter(self.language)
            parser_2 = BlockParser(text, self.language)
            changed = False
            for block in parser_2:
                if block.dsl_name:
                    for id, replacement in second_pass_replacements.items():
                        if id in block.output:
                            changed = True
                            block.output = block.output.replace(id, replacement)
                printer_2.print_block(block)
            if changed:
                text = printer_2.f.getvalue()

        return text


    def _module_and_class(self, fields):
        """
        fields should be an iterable of field names.
        returns a tuple of (module, class).
        the module object could actually be self (a clinic object).
        this function is only ever used to find the parent of where
        a new class/module should go.
        """
        in_classes = False
        parent = module = self
        cls = None
        so_far = []

        for field in fields:
            so_far.append(field)
            if not in_classes:
                child = parent.modules.get(field)
                if child:
                    parent = module = child
                    continue
                in_classes = True
            if not hasattr(parent, 'classes'):
                return module, cls
            child = parent.classes.get(field)
            if not child:
                fail('Parent class or module ' + '.'.join(so_far) + " does not exist.")
            cls = parent = child

        return module, cls


def parse_file(filename, *, force=False, verify=True, output=None, encoding='utf-8'):
    extension = os.path.splitext(filename)[1][1:]
    if not extension:
        fail("Can't extract file type for file " + repr(filename))

    try:
        language = extensions[extension](filename)
    except KeyError:
        fail("Can't identify file type for file " + repr(filename))

    with open(filename, 'r', encoding=encoding) as f:
        raw = f.read()

    # exit quickly if there are no clinic markers in the file
    find_start_re = BlockParser("", language).find_start_re
    if not find_start_re.search(raw):
        return

    clinic = Clinic(language, force=force, verify=verify, filename=filename)
    cooked = clinic.parse(raw)
    if (cooked == raw) and not force:
        return

    directory = os.path.dirname(filename) or '.'

    with tempfile.TemporaryDirectory(prefix="clinic", dir=directory) as tmpdir:
        bytes = cooked.encode(encoding)
        tmpfilename = os.path.join(tmpdir, os.path.basename(filename))
        with open(tmpfilename, "wb") as f:
            f.write(bytes)
        os.replace(tmpfilename, output or filename)


def compute_checksum(input, length=None):
    input = input or ''
    s = hashlib.sha1(input.encode('utf-8')).hexdigest()
    if length:
        s = s[:length]
    return s




class PythonParser:
    def __init__(self, clinic):
        pass

    def parse(self, block):
        s = io.StringIO()
        with OverrideStdioWith(s):
            exec(block.input)
        block.output = s.getvalue()


class Module:
    def __init__(self, name, module=None):
        self.name = name
        self.module = self.parent = module

        self.modules = collections.OrderedDict()
        self.classes = collections.OrderedDict()
        self.functions = []

    def __repr__(self):
        return "<clinic.Module " + repr(self.name) + " at " + str(id(self)) + ">"

class Class:
    def __init__(self, name, module=None, cls=None, typedef=None, type_object=None):
        self.name = name
        self.module = module
        self.cls = cls
        self.typedef = typedef
        self.type_object = type_object
        self.parent = cls or module

        self.classes = collections.OrderedDict()
        self.functions = []

    def __repr__(self):
        return "<clinic.Class " + repr(self.name) + " at " + str(id(self)) + ">"

unsupported_special_methods = set("""

__abs__
__add__
__and__
__bytes__
__call__
__complex__
__delitem__
__divmod__
__eq__
__float__
__floordiv__
__ge__
__getattr__
__getattribute__
__getitem__
__gt__
__hash__
__iadd__
__iand__
__idivmod__
__ifloordiv__
__ilshift__
__imod__
__imul__
__index__
__int__
__invert__
__ior__
__ipow__
__irshift__
__isub__
__iter__
__itruediv__
__ixor__
__le__
__len__
__lshift__
__lt__
__mod__
__mul__
__neg__
__new__
__next__
__or__
__pos__
__pow__
__radd__
__rand__
__rdivmod__
__repr__
__rfloordiv__
__rlshift__
__rmod__
__rmul__
__ror__
__round__
__rpow__
__rrshift__
__rshift__
__rsub__
__rtruediv__
__rxor__
__setattr__
__setitem__
__str__
__sub__
__truediv__
__xor__

""".strip().split())


INVALID, CALLABLE, STATIC_METHOD, CLASS_METHOD, METHOD_INIT, METHOD_NEW = """
INVALID, CALLABLE, STATIC_METHOD, CLASS_METHOD, METHOD_INIT, METHOD_NEW
""".replace(",", "").strip().split()

class Function:
    """
    Mutable duck type for inspect.Function.

    docstring - a str containing
        * embedded line breaks
        * text outdented to the left margin
        * no trailing whitespace.
        It will always be true that
            (not docstring) or ((not docstring[0].isspace()) and (docstring.rstrip() == docstring))
    """

    def __init__(self, parameters=None, *, name,
                 module, cls=None, c_basename=None,
                 full_name=None,
                 return_converter, return_annotation=_empty,
                 docstring=None, kind=CALLABLE, coexist=False,
                 docstring_only=False):
        self.parameters = parameters or collections.OrderedDict()
        self.return_annotation = return_annotation
        self.name = name
        self.full_name = full_name
        self.module = module
        self.cls = cls
        self.parent = cls or module
        self.c_basename = c_basename
        self.return_converter = return_converter
        self.docstring = docstring or ''
        self.kind = kind
        self.coexist = coexist
        self.self_converter = None
        # docstring_only means "don't generate a machine-readable
        # signature, just a normal docstring".  it's True for
        # functions with optional groups because we can't represent
        # those accurately with inspect.Signature in 3.4.
        self.docstring_only = docstring_only

        self.rendered_parameters = None

    __render_parameters__ = None
    @property
    def render_parameters(self):
        if not self.__render_parameters__:
            self.__render_parameters__ = l = []
            for p in self.parameters.values():
                p = p.copy()
                p.converter.pre_render()
                l.append(p)
        return self.__render_parameters__

    @property
    def methoddef_flags(self):
        if self.kind in (METHOD_INIT, METHOD_NEW):
            return None
        flags = []
        if self.kind == CLASS_METHOD:
            flags.append('METH_CLASS')
        elif self.kind == STATIC_METHOD:
            flags.append('METH_STATIC')
        else:
            assert self.kind == CALLABLE, "unknown kind: " + repr(self.kind)
        if self.coexist:
            flags.append('METH_COEXIST')
        return '|'.join(flags)

    def __repr__(self):
        return '<clinic.Function ' + self.name + '>'

    def copy(self, **overrides):
        kwargs = {
            'name': self.name, 'module': self.module, 'parameters': self.parameters,
            'cls': self.cls, 'c_basename': self.c_basename,
            'full_name': self.full_name,
            'return_converter': self.return_converter, 'return_annotation': self.return_annotation,
            'docstring': self.docstring, 'kind': self.kind, 'coexist': self.coexist,
            'docstring_only': self.docstring_only,
            }
        kwargs.update(overrides)
        f = Function(**kwargs)

        parameters = collections.OrderedDict()
        for name, value in f.parameters.items():
            value = value.copy(function=f)
            parameters[name] = value
        f.parameters = parameters
        return f


class Parameter:
    """
    Mutable duck type of inspect.Parameter.
    """

    def __init__(self, name, kind, *, default=_empty,
                 function, converter, annotation=_empty,
                 docstring=None, group=0):
        self.name = name
        self.kind = kind
        self.default = default
        self.function = function
        self.converter = converter
        self.annotation = annotation
        self.docstring = docstring or ''
        self.group = group

    def __repr__(self):
        return '<clinic.Parameter ' + self.name + '>'

    def is_keyword_only(self):
        return self.kind == inspect.Parameter.KEYWORD_ONLY

    def is_positional_only(self):
        return self.kind == inspect.Parameter.POSITIONAL_ONLY

    def copy(self, **overrides):
        kwargs = {
            'name': self.name, 'kind': self.kind, 'default':self.default,
                 'function': self.function, 'converter': self.converter, 'annotation': self.annotation,
                 'docstring': self.docstring, 'group': self.group,
            }
        kwargs.update(overrides)
        if 'converter' not in overrides:
            converter = copy.copy(self.converter)
            converter.function = kwargs['function']
            kwargs['converter'] = converter
        return Parameter(**kwargs)



class LandMine:
    # try to access any
    def __init__(self, message):
        self.__message__ = message

    def __repr__(self):
        return '<LandMine ' + repr(self.__message__) + ">"

    def __getattribute__(self, name):
        if name in ('__repr__', '__message__'):
            return super().__getattribute__(name)
        # raise RuntimeError(repr(name))
        fail("Stepped on a land mine, trying to access attribute " + repr(name) + ":\n" + self.__message__)


def add_c_converter(f, name=None):
    if not name:
        name = f.__name__
        if not name.endswith('_converter'):
            return f
        name = name[:-len('_converter')]
    converters[name] = f
    return f

def add_default_legacy_c_converter(cls):
    # automatically add converter for default format unit
    # (but without stomping on the existing one if it's already
    # set, in case you subclass)
    if ((cls.format_unit not in ('O&', '')) and
        (cls.format_unit not in legacy_converters)):
        legacy_converters[cls.format_unit] = cls
    return cls

def add_legacy_c_converter(format_unit, **kwargs):
    """
    Adds a legacy converter.
    """
    def closure(f):
        if not kwargs:
            added_f = f
        else:
            added_f = functools.partial(f, **kwargs)
        if format_unit:
            legacy_converters[format_unit] = added_f
        return f
    return closure

class CConverterAutoRegister(type):
    def __init__(cls, name, bases, classdict):
        add_c_converter(cls)
        add_default_legacy_c_converter(cls)

class CConverter(metaclass=CConverterAutoRegister):
    """
    For the init function, self, name, function, and default
    must be keyword-or-positional parameters.  All other
    parameters must be keyword-only.
    """

    # The C name to use for this variable.
    name = None

    # The Python name to use for this variable.
    py_name = None

    # The C type to use for this variable.
    # 'type' should be a Python string specifying the type, e.g. "int".
    # If this is a pointer type, the type string should end with ' *'.
    type = None

    # The Python default value for this parameter, as a Python value.
    # Or the magic value "unspecified" if there is no default.
    # Or the magic value "unknown" if this value is a cannot be evaluated
    # at Argument-Clinic-preprocessing time (but is presumed to be valid
    # at runtime).
    default = unspecified

    # If not None, default must be isinstance() of this type.
    # (You can also specify a tuple of types.)
    default_type = None

    # "default" converted into a C value, as a string.
    # Or None if there is no default.
    c_default = None

    # "default" converted into a Python value, as a string.
    # Or None if there is no default.
    py_default = None

    # The default value used to initialize the C variable when
    # there is no default, but not specifying a default may
    # result in an "uninitialized variable" warning.  This can
    # easily happen when using option groups--although
    # properly-written code won't actually use the variable,
    # the variable does get passed in to the _impl.  (Ah, if
    # only dataflow analysis could inline the static function!)
    #
    # This value is specified as a string.
    # Every non-abstract subclass should supply a valid value.
    c_ignored_default = 'NULL'

    # The C converter *function* to be used, if any.
    # (If this is not None, format_unit must be 'O&'.)
    converter = None

    # Should Argument Clinic add a '&' before the name of
    # the variable when passing it into the _impl function?
    impl_by_reference = False

    # Should Argument Clinic add a '&' before the name of
    # the variable when passing it into PyArg_ParseTuple (AndKeywords)?
    parse_by_reference = True

    #############################################################
    #############################################################
    ## You shouldn't need to read anything below this point to ##
    ## write your own converter functions.                     ##
    #############################################################
    #############################################################

    # The "format unit" to specify for this variable when
    # parsing arguments using PyArg_ParseTuple (AndKeywords).
    # Custom converters should always use the default value of 'O&'.
    format_unit = 'O&'

    # What encoding do we want for this variable?  Only used
    # by format units starting with 'e'.
    encoding = None

    # Should this object be required to be a subclass of a specific type?
    # If not None, should be a string representing a pointer to a
    # PyTypeObject (e.g. "&PyUnicode_Type").
    # Only used by the 'O!' format unit (and the "object" converter).
    subclass_of = None

    # Do we want an adjacent '_length' variable for this variable?
    # Only used by format units ending with '#'.
    length = False

    # Should we show this parameter in the generated
    # __text_signature__? This is *almost* always True.
    # (It's only False for __new__, __init__, and METH_STATIC functions.)
    show_in_signature = True

    # Overrides the name used in a text signature.
    # The name used for a "self" parameter must be one of
    # self, type, or module; however users can set their own.
    # This lets the self_converter overrule the user-settable
    # name, *just* for the text signature.
    # Only set by self_converter.
    signature_name = None

    # keep in sync with self_converter.__init__!
    def __init__(self, name, py_name, function, default=unspecified, *, c_default=None, py_default=None, annotation=unspecified, **kwargs):
        self.name = name
        self.py_name = py_name

        if default is not unspecified:
            if self.default_type and not isinstance(default, (self.default_type, Unknown)):
                if isinstance(self.default_type, type):
                    types_str = self.default_type.__name__
                else:
                    types_str = ', '.join((cls.__name__ for cls in self.default_type))
                fail("{}: default value {!r} for field {} is not of type {}".format(
                    self.__class__.__name__, default, name, types_str))
            self.default = default

        if c_default:
            self.c_default = c_default
        if py_default:
            self.py_default = py_default

        if annotation != unspecified:
            fail("The 'annotation' parameter is not currently permitted.")

        # this is deliberate, to prevent you from caching information
        # about the function in the init.
        # (that breaks if we get cloned.)
        # so after this change we will noisily fail.
        self.function = LandMine("Don't access members of self.function inside converter_init!")
        self.converter_init(**kwargs)
        self.function = function

    def converter_init(self):
        pass

    def is_optional(self):
        return (self.default is not unspecified)

    def _render_self(self, parameter, data):
        self.parameter = parameter
        original_name = self.name
        name = ensure_legal_c_identifier(original_name)

        # impl_arguments
        s = ("&" if self.impl_by_reference else "") + name
        data.impl_arguments.append(s)
        if self.length:
            data.impl_arguments.append(self.length_name())

        # impl_parameters
        data.impl_parameters.append(self.simple_declaration(by_reference=self.impl_by_reference))
        if self.length:
            data.impl_parameters.append("Py_ssize_clean_t " + self.length_name())

    def _render_non_self(self, parameter, data):
        self.parameter = parameter
        original_name = self.name
        name = ensure_legal_c_identifier(original_name)

        # declarations
        d = self.declaration()
        data.declarations.append(d)

        # initializers
        initializers = self.initialize()
        if initializers:
            data.initializers.append('/* initializers for ' + name + ' */\n' + initializers.rstrip())

        # modifications
        modifications = self.modify()
        if modifications:
            data.modifications.append('/* modifications for ' + name + ' */\n' + modifications.rstrip())

        # keywords
        data.keywords.append(parameter.name)

        # format_units
        if self.is_optional() and '|' not in data.format_units:
            data.format_units.append('|')
        if parameter.is_keyword_only() and '$' not in data.format_units:
            data.format_units.append('$')
        data.format_units.append(self.format_unit)

        # parse_arguments
        self.parse_argument(data.parse_arguments)

        # cleanup
        cleanup = self.cleanup()
        if cleanup:
            data.cleanup.append('/* Cleanup for ' + name + ' */\n' + cleanup.rstrip() + "\n")

    def render(self, parameter, data):
        """
        parameter is a clinic.Parameter instance.
        data is a CRenderData instance.
        """
        self._render_self(parameter, data)
        self._render_non_self(parameter, data)

    def length_name(self):
        """Computes the name of the associated "length" variable."""
        if not self.length:
            return None
        return ensure_legal_c_identifier(self.name) + "_length"

    # Why is this one broken out separately?
    # For "positional-only" function parsing,
    # which generates a bunch of PyArg_ParseTuple calls.
    def parse_argument(self, list):
        assert not (self.converter and self.encoding)
        if self.format_unit == 'O&':
            assert self.converter
            list.append(self.converter)

        if self.encoding:
            list.append(c_repr(self.encoding))
        elif self.subclass_of:
            list.append(self.subclass_of)

        legal_name = ensure_legal_c_identifier(self.name)
        s = ("&" if self.parse_by_reference else "") + legal_name
        list.append(s)

        if self.length:
            list.append("&" + self.length_name())

    #
    # All the functions after here are intended as extension points.
    #

    def simple_declaration(self, by_reference=False):
        """
        Computes the basic declaration of the variable.
        Used in computing the prototype declaration and the
        variable declaration.
        """
        prototype = [self.type]
        if by_reference or not self.type.endswith('*'):
            prototype.append(" ")
        if by_reference:
            prototype.append('*')
        prototype.append(ensure_legal_c_identifier(self.name))
        return "".join(prototype)

    def declaration(self):
        """
        The C statement to declare this variable.
        """
        declaration = [self.simple_declaration()]
        default = self.c_default
        if not default and self.parameter.group:
            default = self.c_ignored_default
        if default:
            declaration.append(" = ")
            declaration.append(default)
        declaration.append(";")
        if self.length:
            declaration.append('\nPy_ssize_clean_t ')
            declaration.append(self.length_name())
            declaration.append(';')
        s = "".join(declaration)
        # double up curly-braces, this string will be used
        # as part of a format_map() template later
        s = s.replace("{", "{{")
        s = s.replace("}", "}}")
        return s

    def initialize(self):
        """
        The C statements required to set up this variable before parsing.
        Returns a string containing this code indented at column 0.
        If no initialization is necessary, returns an empty string.
        """
        return ""

    def modify(self):
        """
        The C statements required to modify this variable after parsing.
        Returns a string containing this code indented at column 0.
        If no initialization is necessary, returns an empty string.
        """
        return ""

    def cleanup(self):
        """
        The C statements required to clean up after this variable.
        Returns a string containing this code indented at column 0.
        If no cleanup is necessary, returns an empty string.
        """
        return ""

    def pre_render(self):
        """
        A second initialization function, like converter_init,
        called just before rendering.
        You are permitted to examine self.function here.
        """
        pass


class bool_converter(CConverter):
    type = 'int'
    default_type = bool
    format_unit = 'p'
    c_ignored_default = '0'

    def converter_init(self):
        if self.default is not unspecified:
            self.default = bool(self.default)
            self.c_default = str(int(self.default))

class char_converter(CConverter):
    type = 'char'
    default_type = str
    format_unit = 'c'
    c_ignored_default = "'\0'"

    def converter_init(self):
        if isinstance(self.default, str) and (len(self.default) != 1):
            fail("char_converter: illegal default value " + repr(self.default))


@add_legacy_c_converter('B', bitwise=True)
class unsigned_char_converter(CConverter):
    type = 'unsigned char'
    default_type = int
    format_unit = 'b'
    c_ignored_default = "'\0'"

    def converter_init(self, *, bitwise=False):
        if bitwise:
            self.format_unit = 'B'

class byte_converter(unsigned_char_converter): pass

class short_converter(CConverter):
    type = 'short'
    default_type = int
    format_unit = 'h'
    c_ignored_default = "0"

class unsigned_short_converter(CConverter):
    type = 'unsigned short'
    default_type = int
    format_unit = 'H'
    c_ignored_default = "0"

    def converter_init(self, *, bitwise=False):
        if not bitwise:
            fail("Unsigned shorts must be bitwise (for now).")

@add_legacy_c_converter('C', types='str')
class int_converter(CConverter):
    type = 'int'
    default_type = int
    format_unit = 'i'
    c_ignored_default = "0"

    def converter_init(self, *, types='int'):
        if types == 'str':
            self.format_unit = 'C'
        elif types != 'int':
            fail("int_converter: illegal 'types' argument")

class unsigned_int_converter(CConverter):
    type = 'unsigned int'
    default_type = int
    format_unit = 'I'
    c_ignored_default = "0"

    def converter_init(self, *, bitwise=False):
        if not bitwise:
            fail("Unsigned ints must be bitwise (for now).")

class long_converter(CConverter):
    type = 'long'
    default_type = int
    format_unit = 'l'
    c_ignored_default = "0"

class unsigned_long_converter(CConverter):
    type = 'unsigned long'
    default_type = int
    format_unit = 'k'
    c_ignored_default = "0"

    def converter_init(self, *, bitwise=False):
        if not bitwise:
            fail("Unsigned longs must be bitwise (for now).")

class PY_LONG_LONG_converter(CConverter):
    type = 'PY_LONG_LONG'
    default_type = int
    format_unit = 'L'
    c_ignored_default = "0"

class unsigned_PY_LONG_LONG_converter(CConverter):
    type = 'unsigned PY_LONG_LONG'
    default_type = int
    format_unit = 'K'
    c_ignored_default = "0"

    def converter_init(self, *, bitwise=False):
        if not bitwise:
            fail("Unsigned PY_LONG_LONGs must be bitwise (for now).")

class Py_ssize_t_converter(CConverter):
    type = 'Py_ssize_t'
    default_type = int
    format_unit = 'n'
    c_ignored_default = "0"


class float_converter(CConverter):
    type = 'float'
    default_type = float
    format_unit = 'f'
    c_ignored_default = "0.0"

class double_converter(CConverter):
    type = 'double'
    default_type = float
    format_unit = 'd'
    c_ignored_default = "0.0"


class Py_complex_converter(CConverter):
    type = 'Py_complex'
    default_type = complex
    format_unit = 'D'
    c_ignored_default = "{0.0, 0.0}"


class object_converter(CConverter):
    type = 'PyObject *'
    format_unit = 'O'

    def converter_init(self, *, converter=None, type=None, subclass_of=None):
        if converter:
            if subclass_of:
                fail("object: Cannot pass in both 'converter' and 'subclass_of'")
            self.format_unit = 'O&'
            self.converter = converter
        elif subclass_of:
            self.format_unit = 'O!'
            self.subclass_of = subclass_of

        if type is not None:
            self.type = type


@add_legacy_c_converter('s#', length=True)
@add_legacy_c_converter('y', types="bytes")
@add_legacy_c_converter('y#', types="bytes", length=True)
@add_legacy_c_converter('z', nullable=True)
@add_legacy_c_converter('z#', nullable=True, length=True)
class str_converter(CConverter):
    type = 'const char *'
    default_type = (str, Null, NoneType)
    format_unit = 's'

    def converter_init(self, *, encoding=None, types="str",
        length=False, nullable=False, zeroes=False):

        types = set(types.strip().split())
        bytes_type = set(("bytes",))
        str_type = set(("str",))
        all_3_type = set(("bytearray",)) | bytes_type | str_type
        is_bytes = types == bytes_type
        is_str = types == str_type
        is_all_3 = types == all_3_type

        self.length = bool(length)
        format_unit = None

        if encoding:
            self.encoding = encoding

            if is_str and not (length or zeroes or nullable):
                format_unit = 'es'
            elif is_all_3 and not (length or zeroes or nullable):
                format_unit = 'et'
            elif is_str and length and zeroes and not nullable:
                format_unit = 'es#'
            elif is_all_3 and length and not (nullable or zeroes):
                format_unit = 'et#'

            if format_unit.endswith('#'):
                fail("Sorry: code using format unit ", repr(format_unit), "probably doesn't work properly yet.\nGive Larry your test case and he'll it.")
                # TODO set pointer to NULL
                # TODO add cleanup for buffer
                pass

        else:
            if zeroes:
                fail("str_converter: illegal combination of arguments (zeroes is only legal with an encoding)")

            if is_bytes and not (nullable or length):
                format_unit = 'y'
            elif is_bytes and length and not nullable:
                format_unit = 'y#'
            elif is_str and not (nullable or length):
                format_unit = 's'
            elif is_str and length and not nullable:
                format_unit = 's#'
            elif is_str and nullable  and not length:
                format_unit = 'z'
            elif is_str and nullable and length:
                format_unit = 'z#'

        if not format_unit:
            fail("str_converter: illegal combination of arguments")
        self.format_unit = format_unit


class PyBytesObject_converter(CConverter):
    type = 'PyBytesObject *'
    format_unit = 'S'

class PyByteArrayObject_converter(CConverter):
    type = 'PyByteArrayObject *'
    format_unit = 'Y'

class unicode_converter(CConverter):
    type = 'PyObject *'
    default_type = (str, Null, NoneType)
    format_unit = 'U'

@add_legacy_c_converter('u#', length=True)
@add_legacy_c_converter('Z', nullable=True)
@add_legacy_c_converter('Z#', nullable=True, length=True)
class Py_UNICODE_converter(CConverter):
    type = 'Py_UNICODE *'
    default_type = (str, Null, NoneType)
    format_unit = 'u'

    def converter_init(self, *, nullable=False, length=False):
        format_unit = 'Z' if nullable else 'u'
        if length:
            format_unit += '#'
            self.length = True
        self.format_unit = format_unit

#
# We define three string conventions for buffer types in the 'types' argument:
#  'buffer' : any object supporting the buffer interface
#  'rwbuffer': any object supporting the buffer interface, but must be writeable
#  'robuffer': any object supporting the buffer interface, but must not be writeable
#
@add_legacy_c_converter('s*', types='str bytes bytearray buffer')
@add_legacy_c_converter('z*', types='str bytes bytearray buffer', nullable=True)
@add_legacy_c_converter('w*', types='bytearray rwbuffer')
class Py_buffer_converter(CConverter):
    type = 'Py_buffer'
    format_unit = 'y*'
    impl_by_reference = True
    c_ignored_default = "{NULL, NULL}"

    def converter_init(self, *, types='bytes bytearray buffer', nullable=False):
        if self.default not in (unspecified, None):
            fail("The only legal default value for Py_buffer is None.")
        self.c_default = self.c_ignored_default
        types = set(types.strip().split())
        bytes_type = set(('bytes',))
        bytearray_type = set(('bytearray',))
        buffer_type = set(('buffer',))
        rwbuffer_type = set(('rwbuffer',))
        robuffer_type = set(('robuffer',))
        str_type = set(('str',))
        bytes_bytearray_buffer_type = bytes_type | bytearray_type | buffer_type

        format_unit = None
        if types == (str_type | bytes_bytearray_buffer_type):
            format_unit = 's*' if not nullable else 'z*'
        else:
            if nullable:
                fail('Py_buffer_converter: illegal combination of arguments (nullable=True)')
            elif types == (bytes_bytearray_buffer_type):
                format_unit = 'y*'
            elif types == (bytearray_type | rwbuffer_type):
                format_unit = 'w*'
        if not format_unit:
            fail("Py_buffer_converter: illegal combination of arguments")

        self.format_unit = format_unit

    def cleanup(self):
        name = ensure_legal_c_identifier(self.name)
        return "".join(["if (", name, ".obj)\n   PyBuffer_Release(&", name, ");\n"])


def correct_name_for_self(f):
    if f.kind in (CALLABLE, METHOD_INIT):
        if f.cls:
            return "PyObject *", "self"
        return "PyModuleDef *", "module"
    if f.kind == STATIC_METHOD:
        return "void *", "null"
    if f.kind in (CLASS_METHOD, METHOD_NEW):
        return "PyTypeObject *", "type"
    raise RuntimeError("Unhandled type of function f: " + repr(f.kind))

def required_type_for_self_for_parser(f):
    type, _ = correct_name_for_self(f)
    if f.kind in (METHOD_INIT, METHOD_NEW, STATIC_METHOD, CLASS_METHOD):
        return type
    return None


class self_converter(CConverter):
    """
    A special-case converter:
    this is the default converter used for "self".
    """
    type = None
    format_unit = ''

    def converter_init(self, *, type=None):
        self.specified_type = type

    def pre_render(self):
        f = self.function
        default_type, default_name = correct_name_for_self(f)
        self.signature_name = default_name
        self.type = self.specified_type or self.type or default_type

        kind = self.function.kind
        new_or_init = kind in (METHOD_NEW, METHOD_INIT)

        if (kind == STATIC_METHOD) or new_or_init:
            self.show_in_signature = False

    # tp_new (METHOD_NEW) functions are of type newfunc:
    #     typedef PyObject *(*newfunc)(struct _typeobject *, PyObject *, PyObject *);
    # PyTypeObject is a typedef for struct _typeobject.
    #
    # tp_init (METHOD_INIT) functions are of type initproc:
    #     typedef int (*initproc)(PyObject *, PyObject *, PyObject *);
    #
    # All other functions generated by Argument Clinic are stored in
    # PyMethodDef structures, in the ml_meth slot, which is of type PyCFunction:
    #     typedef PyObject *(*PyCFunction)(PyObject *, PyObject *);
    # However!  We habitually cast these functions to PyCFunction,
    # since functions that accept keyword arguments don't fit this signature
    # but are stored there anyway.  So strict type equality isn't important
    # for these functions.
    #
    # So:
    #
    # * The name of the first parameter to the impl and the parsing function will always
    #   be self.name.
    #
    # * The type of the first parameter to the impl will always be of self.type.
    #
    # * If the function is neither tp_new (METHOD_NEW) nor tp_init (METHOD_INIT):
    #   * The type of the first parameter to the parsing function is also self.type.
    #     This means that if you step into the parsing function, your "self" parameter
    #     is of the correct type, which may make debugging more pleasant.
    #
    # * Else if the function is tp_new (METHOD_NEW):
    #   * The type of the first parameter to the parsing function is "PyTypeObject *",
    #     so the type signature of the function call is an exact match.
    #   * If self.type != "PyTypeObject *", we cast the first parameter to self.type
    #     in the impl call.
    #
    # * Else if the function is tp_init (METHOD_INIT):
    #   * The type of the first parameter to the parsing function is "PyObject *",
    #     so the type signature of the function call is an exact match.
    #   * If self.type != "PyObject *", we cast the first parameter to self.type
    #     in the impl call.

    @property
    def parser_type(self):
        return required_type_for_self_for_parser(self.function) or self.type

    def render(self, parameter, data):
        """
        parameter is a clinic.Parameter instance.
        data is a CRenderData instance.
        """
        if self.function.kind == STATIC_METHOD:
            return

        self._render_self(parameter, data)

        if self.type != self.parser_type:
            # insert cast to impl_argument[0], aka self.
            # we know we're in the first slot in all the CRenderData lists,
            # because we render parameters in order, and self is always first.
            assert len(data.impl_arguments) == 1
            assert data.impl_arguments[0] == self.name
            data.impl_arguments[0] = '(' + self.type + ")" + data.impl_arguments[0]

    def set_template_dict(self, template_dict):
        template_dict['self_name'] = self.name
        template_dict['self_type'] = self.parser_type
        kind = self.function.kind
        cls = self.function.cls

        if ((kind in (METHOD_NEW, METHOD_INIT)) and cls and cls.typedef):
            if kind == METHOD_NEW:
                passed_in_type = self.name
            else:
                passed_in_type = 'Py_TYPE({})'.format(self.name)

            line = '({passed_in_type} == {type_object}) &&\n        '
            d = {
                'type_object': self.function.cls.type_object,
                'passed_in_type': passed_in_type
                }
            template_dict['self_type_check'] = line.format_map(d)



def add_c_return_converter(f, name=None):
    if not name:
        name = f.__name__
        if not name.endswith('_return_converter'):
            return f
        name = name[:-len('_return_converter')]
    return_converters[name] = f
    return f


class CReturnConverterAutoRegister(type):
    def __init__(cls, name, bases, classdict):
        add_c_return_converter(cls)

class CReturnConverter(metaclass=CReturnConverterAutoRegister):

    # The C type to use for this variable.
    # 'type' should be a Python string specifying the type, e.g. "int".
    # If this is a pointer type, the type string should end with ' *'.
    type = 'PyObject *'

    # The Python default value for this parameter, as a Python value.
    # Or the magic value "unspecified" if there is no default.
    default = None

    def __init__(self, *, py_default=None, **kwargs):
        self.py_default = py_default
        try:
            self.return_converter_init(**kwargs)
        except TypeError as e:
            s = ', '.join(name + '=' + repr(value) for name, value in kwargs.items())
            sys.exit(self.__class__.__name__ + '(' + s + ')\n' + str(e))

    def return_converter_init(self):
        pass

    def declare(self, data, name="_return_value"):
        line = []
        add = line.append
        add(self.type)
        if not self.type.endswith('*'):
            add(' ')
        add(name + ';')
        data.declarations.append(''.join(line))
        data.return_value = name

    def err_occurred_if(self, expr, data):
        data.return_conversion.append('if (({}) && PyErr_Occurred())\n    goto exit;\n'.format(expr))

    def err_occurred_if_null_pointer(self, variable, data):
        data.return_conversion.append('if ({} == NULL)\n    goto exit;\n'.format(variable))

    def render(self, function, data):
        """
        function is a clinic.Function instance.
        data is a CRenderData instance.
        """
        pass

add_c_return_converter(CReturnConverter, 'object')

class NoneType_return_converter(CReturnConverter):
    def render(self, function, data):
        self.declare(data)
        data.return_conversion.append('''
if (_return_value != Py_None)
    goto exit;
return_value = Py_None;
Py_INCREF(Py_None);
'''.strip())

class bool_return_converter(CReturnConverter):
    type = 'int'

    def render(self, function, data):
        self.declare(data)
        self.err_occurred_if("_return_value == -1", data)
        data.return_conversion.append('return_value = PyBool_FromLong((long)_return_value);\n')

class long_return_converter(CReturnConverter):
    type = 'long'
    conversion_fn = 'PyLong_FromLong'
    cast = ''

    def render(self, function, data):
        self.declare(data)
        self.err_occurred_if("_return_value == -1", data)
        data.return_conversion.append(
            ''.join(('return_value = ', self.conversion_fn, '(', self.cast, '_return_value);\n')))

class int_return_converter(long_return_converter):
    type = 'int'
    cast = '(long)'

class init_return_converter(long_return_converter):
    """
    Special return converter for __init__ functions.
    """
    type = 'int'
    cast = '(long)'

    def render(self, function, data):
        pass

class unsigned_long_return_converter(long_return_converter):
    type = 'unsigned long'
    conversion_fn = 'PyLong_FromUnsignedLong'

class unsigned_int_return_converter(unsigned_long_return_converter):
    type = 'unsigned int'
    cast = '(unsigned long)'

class Py_ssize_t_return_converter(long_return_converter):
    type = 'Py_ssize_t'
    conversion_fn = 'PyLong_FromSsize_t'

class size_t_return_converter(long_return_converter):
    type = 'size_t'
    conversion_fn = 'PyLong_FromSize_t'


class double_return_converter(CReturnConverter):
    type = 'double'
    cast = ''

    def render(self, function, data):
        self.declare(data)
        self.err_occurred_if("_return_value == -1.0", data)
        data.return_conversion.append(
            'return_value = PyFloat_FromDouble(' + self.cast + '_return_value);\n')

class float_return_converter(double_return_converter):
    type = 'float'
    cast = '(double)'


class DecodeFSDefault_return_converter(CReturnConverter):
    type = 'char *'

    def render(self, function, data):
        self.declare(data)
        self.err_occurred_if_null_pointer("_return_value", data)
        data.return_conversion.append(
            'return_value = PyUnicode_DecodeFSDefault(_return_value);\n')


class IndentStack:
    def __init__(self):
        self.indents = []
        self.margin = None

    def _ensure(self):
        if not self.indents:
            fail('IndentStack expected indents, but none are defined.')

    def measure(self, line):
        """
        Returns the length of the line's margin.
        """
        if '\t' in line:
            fail('Tab characters are illegal in the Argument Clinic DSL.')
        stripped = line.lstrip()
        if not len(stripped):
            # we can't tell anything from an empty line
            # so just pretend it's indented like our current indent
            self._ensure()
            return self.indents[-1]
        return len(line) - len(stripped)

    def infer(self, line):
        """
        Infer what is now the current margin based on this line.
        Returns:
            1 if we have indented (or this is the first margin)
            0 if the margin has not changed
           -N if we have dedented N times
        """
        indent = self.measure(line)
        margin = ' ' * indent
        if not self.indents:
            self.indents.append(indent)
            self.margin = margin
            return 1
        current = self.indents[-1]
        if indent == current:
            return 0
        if indent > current:
            self.indents.append(indent)
            self.margin = margin
            return 1
        # indent < current
        if indent not in self.indents:
            fail("Illegal outdent.")
        outdent_count = 0
        while indent != current:
            self.indents.pop()
            current = self.indents[-1]
            outdent_count -= 1
        self.margin = margin
        return outdent_count

    @property
    def depth(self):
        """
        Returns how many margins are currently defined.
        """
        return len(self.indents)

    def indent(self, line):
        """
        Indents a line by the currently defined margin.
        """
        return self.margin + line

    def dedent(self, line):
        """
        Dedents a line by the currently defined margin.
        (The inverse of 'indent'.)
        """
        margin = self.margin
        indent = self.indents[-1]
        if not line.startswith(margin):
            fail('Cannot dedent, line does not start with the previous margin:')
        return line[indent:]


class DSLParser:
    def __init__(self, clinic):
        self.clinic = clinic

        self.directives = {}
        for name in dir(self):
            # functions that start with directive_ are added to directives
            _, s, key = name.partition("directive_")
            if s:
                self.directives[key] = getattr(self, name)

            # functions that start with at_ are too, with an @ in front
            _, s, key = name.partition("at_")
            if s:
                self.directives['@' + key] = getattr(self, name)

        self.reset()

    def reset(self):
        self.function = None
        self.state = self.state_dsl_start
        self.parameter_indent = None
        self.keyword_only = False
        self.group = 0
        self.parameter_state = self.ps_start
        self.seen_positional_with_default = False
        self.indent = IndentStack()
        self.kind = CALLABLE
        self.coexist = False
        self.parameter_continuation = ''
        self.preserve_output = False

    def directive_version(self, required):
        global version
        if version_comparitor(version, required) < 0:
            fail("Insufficient Clinic version!\n  Version: " + version + "\n  Required: " + required)

    def directive_module(self, name):
        fields = name.split('.')
        new = fields.pop()
        module, cls = self.clinic._module_and_class(fields)
        if cls:
            fail("Can't nest a module inside a class!")

        if name in module.classes:
            fail("Already defined module " + repr(name) + "!")

        m = Module(name, module)
        module.modules[name] = m
        self.block.signatures.append(m)

    def directive_class(self, name, typedef, type_object):
        fields = name.split('.')
        in_classes = False
        parent = self
        name = fields.pop()
        so_far = []
        module, cls = self.clinic._module_and_class(fields)

        parent = cls or module
        if name in parent.classes:
            fail("Already defined class " + repr(name) + "!")

        c = Class(name, module, cls, typedef, type_object)
        parent.classes[name] = c
        self.block.signatures.append(c)

    def directive_set(self, name, value):
        if name not in ("line_prefix", "line_suffix"):
            fail("unknown variable", repr(name))

        value = value.format_map({
            'block comment start': '/*',
            'block comment end': '*/',
            })

        self.clinic.__dict__[name] = value

    def directive_destination(self, name, command, *args):
        if command == 'new':
            self.clinic.add_destination(name, *args)
            return

        if command == 'clear':
            self.clinic.get_destination(name).clear()
        fail("unknown destination command", repr(command))


    def directive_output(self, field, destination=''):
        fd = self.clinic.field_destinations

        if field == "preset":
            preset = self.clinic.presets.get(destination)
            if not preset:
                fail("Unknown preset " + repr(destination) + "!")
            fd.update(preset)
            return

        if field == "push":
            self.clinic.field_destinations_stack.append(fd.copy())
            return

        if field == "pop":
            if not self.clinic.field_destinations_stack:
                fail("Can't 'output pop', stack is empty!")
            previous_fd = self.clinic.field_destinations_stack.pop()
            fd.update(previous_fd)
            return

        # secret command for debugging!
        if field == "print":
            self.block.output.append(pprint.pformat(fd))
            self.block.output.append('\n')
            return

        d = self.clinic.get_destination(destination)

        if field == "everything":
            for name in list(fd):
                fd[name] = d
            return

        if field not in fd:
            fail("Invalid field " + repr(field) + ", must be one of:\n  preset push pop print everything " + " ".join(fd))
        fd[field] = d

    def directive_dump(self, name):
        self.block.output.append(self.clinic.get_destination(name).dump())

    def directive_print(self, *args):
        self.block.output.append(' '.join(args))
        self.block.output.append('\n')

    def directive_preserve(self):
        if self.preserve_output:
            fail("Can't have preserve twice in one block!")
        self.preserve_output = True

    def at_classmethod(self):
        if self.kind is not CALLABLE:
            fail("Can't set @classmethod, function is not a normal callable")
        self.kind = CLASS_METHOD

    def at_staticmethod(self):
        if self.kind is not CALLABLE:
            fail("Can't set @staticmethod, function is not a normal callable")
        self.kind = STATIC_METHOD

    def at_coexist(self):
        if self.coexist:
            fail("Called @coexist twice!")
        self.coexist = True

    def parse(self, block):
        self.reset()
        self.block = block
        self.saved_output = self.block.output
        block.output = []
        block_start = self.clinic.block_parser.line_number
        lines = block.input.split('\n')
        for line_number, line in enumerate(lines, self.clinic.block_parser.block_start_line_number):
            if '\t' in line:
                fail('Tab characters are illegal in the Clinic DSL.\n\t' + repr(line), line_number=block_start)
            self.state(line)

        self.next(self.state_terminal)
        self.state(None)

        block.output.extend(self.clinic.language.render(clinic, block.signatures))

        if self.preserve_output:
            if block.output:
                fail("'preserve' only works for blocks that don't produce any output!")
            block.output = self.saved_output

    @staticmethod
    def ignore_line(line):
        # ignore comment-only lines
        if line.lstrip().startswith('#'):
            return True

        # Ignore empty lines too
        # (but not in docstring sections!)
        if not line.strip():
            return True

        return False

    @staticmethod
    def calculate_indent(line):
        return len(line) - len(line.strip())

    def next(self, state, line=None):
        # real_print(self.state.__name__, "->", state.__name__, ", line=", line)
        self.state = state
        if line is not None:
            self.state(line)

    def state_dsl_start(self, line):
        # self.block = self.ClinicOutputBlock(self)
        if self.ignore_line(line):
            return

        # is it a directive?
        fields = shlex.split(line)
        directive_name = fields[0]
        directive = self.directives.get(directive_name, None)
        if directive:
            try:
                directive(*fields[1:])
            except TypeError as e:
                fail(str(e))
            return

        self.next(self.state_modulename_name, line)

    def state_modulename_name(self, line):
        # looking for declaration, which establishes the leftmost column
        # line should be
        #     modulename.fnname [as c_basename] [-> return annotation]
        # square brackets denote optional syntax.
        #
        # alternatively:
        #     modulename.fnname [as c_basename] = modulename.existing_fn_name
        # clones the parameters and return converter from that
        # function.  you can't modify them.  you must enter a
        # new docstring.
        #
        # (but we might find a directive first!)
        #
        # this line is permitted to start with whitespace.
        # we'll call this number of spaces F (for "function").

        if not line.strip():
            return

        self.indent.infer(line)

        # are we cloning?
        before, equals, existing = line.rpartition('=')
        if equals:
            full_name, _, c_basename = before.partition(' as ')
            full_name = full_name.strip()
            c_basename = c_basename.strip()
            existing = existing.strip()
            if (is_legal_py_identifier(full_name) and
                (not c_basename or is_legal_c_identifier(c_basename)) and
                is_legal_py_identifier(existing)):
                # we're cloning!
                fields = [x.strip() for x in existing.split('.')]
                function_name = fields.pop()
                module, cls = self.clinic._module_and_class(fields)

                for existing_function in (cls or module).functions:
                    if existing_function.name == function_name:
                        break
                else:
                    existing_function = None
                if not existing_function:
                    print("class", cls, "module", module, "existing", existing)
                    print("cls. functions", cls.functions)
                    fail("Couldn't find existing function " + repr(existing) + "!")

                fields = [x.strip() for x in full_name.split('.')]
                function_name = fields.pop()
                module, cls = self.clinic._module_and_class(fields)

                if not (existing_function.kind == self.kind and existing_function.coexist == self.coexist):
                    fail("'kind' of function and cloned function don't match!  (@classmethod/@staticmethod/@coexist)")
                self.function = existing_function.copy(name=function_name, full_name=full_name, module=module, cls=cls, c_basename=c_basename, docstring='')

                self.block.signatures.append(self.function)
                (cls or module).functions.append(self.function)
                self.next(self.state_function_docstring)
                return

        line, _, returns = line.partition('->')

        full_name, _, c_basename = line.partition(' as ')
        full_name = full_name.strip()
        c_basename = c_basename.strip() or None

        if not is_legal_py_identifier(full_name):
            fail("Illegal function name: {}".format(full_name))
        if c_basename and not is_legal_c_identifier(c_basename):
            fail("Illegal C basename: {}".format(c_basename))

        return_converter = None
        if returns:
            ast_input = "def x() -> {}: pass".format(returns)
            module = None
            try:
                module = ast.parse(ast_input)
            except SyntaxError:
                pass
            if not module:
                fail("Badly-formed annotation for " + full_name + ": " + returns)
            try:
                name, legacy, kwargs = self.parse_converter(module.body[0].returns)
                if legacy:
                    fail("Legacy converter {!r} not allowed as a return converter"
                         .format(name))
                if name not in return_converters:
                    fail("No available return converter called " + repr(name))
                return_converter = return_converters[name](**kwargs)
            except ValueError:
                fail("Badly-formed annotation for " + full_name + ": " + returns)

        fields = [x.strip() for x in full_name.split('.')]
        function_name = fields.pop()
        module, cls = self.clinic._module_and_class(fields)

        fields = full_name.split('.')
        if fields[-1] == '__new__':
            if (self.kind != CLASS_METHOD) or (not cls):
                fail("__new__ must be a class method!")
            self.kind = METHOD_NEW
        elif fields[-1] == '__init__':
            if (self.kind != CALLABLE) or (not cls):
                fail("__init__ must be a normal method, not a class or static method!")
            self.kind = METHOD_INIT
            if not return_converter:
                return_converter = init_return_converter()
        elif fields[-1] in unsupported_special_methods:
            fail(fields[-1] + " is a special method and cannot be converted to Argument Clinic!  (Yet.)")

        if not return_converter:
            return_converter = CReturnConverter()

        if not module:
            fail("Undefined module used in declaration of " + repr(full_name.strip()) + ".")
        self.function = Function(name=function_name, full_name=full_name, module=module, cls=cls, c_basename=c_basename,
                                 return_converter=return_converter, kind=self.kind, coexist=self.coexist)
        self.block.signatures.append(self.function)

        # insert a self converter automatically
        type, name = correct_name_for_self(self.function)
        kwargs = {}
        if cls and type == "PyObject *":
            kwargs['type'] = cls.typedef
        sc = self.function.self_converter = self_converter(name, name, self.function, **kwargs)
        p_self = Parameter(sc.name, inspect.Parameter.POSITIONAL_ONLY, function=self.function, converter=sc)
        self.function.parameters[sc.name] = p_self

        (cls or module).functions.append(self.function)
        self.next(self.state_parameters_start)

    # Now entering the parameters section.  The rules, formally stated:
    #
    #   * All lines must be indented with spaces only.
    #   * The first line must be a parameter declaration.
    #   * The first line must be indented.
    #       * This first line establishes the indent for parameters.
    #       * We'll call this number of spaces P (for "parameter").
    #   * Thenceforth:
    #       * Lines indented with P spaces specify a parameter.
    #       * Lines indented with > P spaces are docstrings for the previous
    #         parameter.
    #           * We'll call this number of spaces D (for "docstring").
    #           * All subsequent lines indented with >= D spaces are stored as
    #             part of the per-parameter docstring.
    #           * All lines will have the first D spaces of the indent stripped
    #             before they are stored.
    #           * It's illegal to have a line starting with a number of spaces X
    #             such that P < X < D.
    #       * A line with < P spaces is the first line of the function
    #         docstring, which ends processing for parameters and per-parameter
    #         docstrings.
    #           * The first line of the function docstring must be at the same
    #             indent as the function declaration.
    #       * It's illegal to have any line in the parameters section starting
    #         with X spaces such that F < X < P.  (As before, F is the indent
    #         of the function declaration.)
    #
    # Also, currently Argument Clinic places the following restrictions on groups:
    #   * Each group must contain at least one parameter.
    #   * Each group may contain at most one group, which must be the furthest
    #     thing in the group from the required parameters.  (The nested group
    #     must be the first in the group when it's before the required
    #     parameters, and the last thing in the group when after the required
    #     parameters.)
    #   * There may be at most one (top-level) group to the left or right of
    #     the required parameters.
    #   * You must specify a slash, and it must be after all parameters.
    #     (In other words: either all parameters are positional-only,
    #      or none are.)
    #
    #  Said another way:
    #   * Each group must contain at least one parameter.
    #   * All left square brackets before the required parameters must be
    #     consecutive.  (You can't have a left square bracket followed
    #     by a parameter, then another left square bracket.  You can't
    #     have a left square bracket, a parameter, a right square bracket,
    #     and then a left square bracket.)
    #   * All right square brackets after the required parameters must be
    #     consecutive.
    #
    # These rules are enforced with a single state variable:
    # "parameter_state".  (Previously the code was a miasma of ifs and
    # separate boolean state variables.)  The states are:
    #
    #  [ [ a, b, ] c, ] d, e, f=3, [ g, h, [ i ] ] /   <- line
    # 01   2          3       4    5           6   7   <- state transitions
    #
    # 0: ps_start.  before we've seen anything.  legal transitions are to 1 or 3.
    # 1: ps_left_square_before.  left square brackets before required parameters.
    # 2: ps_group_before.  in a group, before required parameters.
    # 3: ps_required.  required parameters, positional-or-keyword or positional-only
    #     (we don't know yet).  (renumber left groups!)
    # 4: ps_optional.  positional-or-keyword or positional-only parameters that
    #    now must have default values.
    # 5: ps_group_after.  in a group, after required parameters.
    # 6: ps_right_square_after.  right square brackets after required parameters.
    # 7: ps_seen_slash.  seen slash.
    ps_start, ps_left_square_before, ps_group_before, ps_required, \
    ps_optional, ps_group_after, ps_right_square_after, ps_seen_slash = range(8)

    def state_parameters_start(self, line):
        if self.ignore_line(line):
            return

        # if this line is not indented, we have no parameters
        if not self.indent.infer(line):
            return self.next(self.state_function_docstring, line)

        self.parameter_continuation = ''
        return self.next(self.state_parameter, line)


    def to_required(self):
        """
        Transition to the "required" parameter state.
        """
        if self.parameter_state != self.ps_required:
            self.parameter_state = self.ps_required
            for p in self.function.parameters.values():
                p.group = -p.group

    def state_parameter(self, line):
        if self.parameter_continuation:
            line = self.parameter_continuation + ' ' + line.lstrip()
            self.parameter_continuation = ''

        if self.ignore_line(line):
            return

        assert self.indent.depth == 2
        indent = self.indent.infer(line)
        if indent == -1:
            # we outdented, must be to definition column
            return self.next(self.state_function_docstring, line)

        if indent == 1:
            # we indented, must be to new parameter docstring column
            return self.next(self.state_parameter_docstring_start, line)

        line = line.rstrip()
        if line.endswith('\\'):
            self.parameter_continuation = line[:-1]
            return

        line = line.lstrip()

        if line in ('*', '/', '[', ']'):
            self.parse_special_symbol(line)
            return

        if self.parameter_state in (self.ps_start, self.ps_required):
            self.to_required()
        elif self.parameter_state == self.ps_left_square_before:
            self.parameter_state = self.ps_group_before
        elif self.parameter_state == self.ps_group_before:
            if not self.group:
                self.to_required()
        elif self.parameter_state in (self.ps_group_after, self.ps_optional):
            pass
        else:
            fail("Function " + self.function.name + " has an unsupported group configuration. (Unexpected state " + str(self.parameter_state) + ".a)")

        # handle "as" for  parameters too
        c_name = None
        name, have_as_token, trailing = line.partition(' as ')
        if have_as_token:
            name = name.strip()
            if ' ' not in name:
                fields = trailing.strip().split(' ')
                if not fields:
                    fail("Invalid 'as' clause!")
                c_name = fields[0]
                if c_name.endswith(':'):
                    name += ':'
                    c_name = c_name[:-1]
                fields[0] = name
                line = ' '.join(fields)

        base, equals, default = line.rpartition('=')
        if not equals:
            base = default
            default = None

        module = None
        try:
            ast_input = "def x({}): pass".format(base)
            module = ast.parse(ast_input)
        except SyntaxError:
            try:
                # the last = was probably inside a function call, like
                #   i: int(nullable=True)
                # so assume there was no actual default value.
                default = None
                ast_input = "def x({}): pass".format(line)
                module = ast.parse(ast_input)
            except SyntaxError:
                pass
        if not module:
            fail("Function " + self.function.name + " has an invalid parameter declaration:\n\t" + line)

        function_args = module.body[0].args
        parameter = function_args.args[0]

        parameter_name = parameter.arg
        name, legacy, kwargs = self.parse_converter(parameter.annotation)

        if not default:
            if self.parameter_state == self.ps_optional:
                fail("Can't have a parameter without a default (" + repr(parameter_name) + ")\nafter a parameter with a default!")
            value = unspecified
            if 'py_default' in kwargs:
                fail("You can't specify py_default without specifying a default value!")
        else:
            if self.parameter_state == self.ps_required:
                self.parameter_state = self.ps_optional
            default = default.strip()
            bad = False
            ast_input = "x = {}".format(default)
            bad = False
            try:
                module = ast.parse(ast_input)

                if 'c_default' not in kwargs:
                    # we can only represent very simple data values in C.
                    # detect whether default is okay, via a blacklist
                    # of disallowed ast nodes.
                    class DetectBadNodes(ast.NodeVisitor):
                        bad = False
                        def bad_node(self, node):
                            self.bad = True

                        # inline function call
                        visit_Call = bad_node
                        # inline if statement ("x = 3 if y else z")
                        visit_IfExp = bad_node

                        # comprehensions and generator expressions
                        visit_ListComp = visit_SetComp = bad_node
                        visit_DictComp = visit_GeneratorExp = bad_node

                        # literals for advanced types
                        visit_Dict = visit_Set = bad_node
                        visit_List = visit_Tuple = bad_node

                        # "starred": "a = [1, 2, 3]; *a"
                        visit_Starred = bad_node

                        # allow ellipsis, for now
                        # visit_Ellipsis = bad_node

                    blacklist = DetectBadNodes()
                    blacklist.visit(module)
                    bad = blacklist.bad
                else:
                    # if they specify a c_default, we can be more lenient about the default value.
                    # but at least make an attempt at ensuring it's a valid expression.
                    try:
                        value = eval(default)
                        if value == unspecified:
                            fail("'unspecified' is not a legal default value!")
                    except NameError:
                        pass # probably a named constant
                    except Exception as e:
                        fail("Malformed expression given as default value\n"
                             "{!r} caused {!r}".format(default, e))
                if bad:
                    fail("Unsupported expression as default value: " + repr(default))

                expr = module.body[0].value
                # mild hack: explicitly support NULL as a default value
                if isinstance(expr, ast.Name) and expr.id == 'NULL':
                    value = NULL
                    py_default = 'None'
                    c_default = "NULL"
                elif (isinstance(expr, ast.BinOp) or
                    (isinstance(expr, ast.UnaryOp) and not isinstance(expr.operand, ast.Num))):
                    c_default = kwargs.get("c_default")
                    if not (isinstance(c_default, str) and c_default):
                        fail("When you specify an expression (" + repr(default) + ") as your default value,\nyou MUST specify a valid c_default.")
                    py_default = default
                    value = unknown
                elif isinstance(expr, ast.Attribute):
                    a = []
                    n = expr
                    while isinstance(n, ast.Attribute):
                        a.append(n.attr)
                        n = n.value
                    if not isinstance(n, ast.Name):
                        fail("Unsupported default value " + repr(default) + " (looked like a Python constant)")
                    a.append(n.id)
                    py_default = ".".join(reversed(a))

                    c_default = kwargs.get("c_default")
                    if not (isinstance(c_default, str) and c_default):
                        fail("When you specify a named constant (" + repr(py_default) + ") as your default value,\nyou MUST specify a valid c_default.")

                    try:
                        value = eval(py_default)
                    except NameError:
                        value = unknown
                else:
                    value = ast.literal_eval(expr)
                    py_default = repr(value)
                    if isinstance(value, (bool, None.__class__)):
                        c_default = "Py_" + py_default
                    elif isinstance(value, str):
                        c_default = c_repr(value)
                    else:
                        c_default = py_default

            except SyntaxError as e:
                fail("Syntax error: " + repr(e.text))
            except (ValueError, AttributeError):
                value = unknown
                c_default = kwargs.get("c_default")
                py_default = default
                if not (isinstance(c_default, str) and c_default):
                    fail("When you specify a named constant (" + repr(py_default) + ") as your default value,\nyou MUST specify a valid c_default.")

            kwargs.setdefault('c_default', c_default)
            kwargs.setdefault('py_default', py_default)

        dict = legacy_converters if legacy else converters
        legacy_str = "legacy " if legacy else ""
        if name not in dict:
            fail('{} is not a valid {}converter'.format(name, legacy_str))
        # if you use a c_name for the parameter, we just give that name to the converter
        # but the parameter object gets the python name
        converter = dict[name](c_name or parameter_name, parameter_name, self.function, value, **kwargs)

        kind = inspect.Parameter.KEYWORD_ONLY if self.keyword_only else inspect.Parameter.POSITIONAL_OR_KEYWORD

        if isinstance(converter, self_converter):
            if len(self.function.parameters) == 1:
                if (self.parameter_state != self.ps_required):
                    fail("A 'self' parameter cannot be marked optional.")
                if value is not unspecified:
                    fail("A 'self' parameter cannot have a default value.")
                if self.group:
                    fail("A 'self' parameter cannot be in an optional group.")
                kind = inspect.Parameter.POSITIONAL_ONLY
                self.parameter_state = self.ps_start
                self.function.parameters.clear()
            else:
                fail("A 'self' parameter, if specified, must be the very first thing in the parameter block.")

        p = Parameter(parameter_name, kind, function=self.function, converter=converter, default=value, group=self.group)

        if parameter_name in self.function.parameters:
            fail("You can't have two parameters named " + repr(parameter_name) + "!")
        self.function.parameters[parameter_name] = p

    def parse_converter(self, annotation):
        if isinstance(annotation, ast.Str):
            return annotation.s, True, {}

        if isinstance(annotation, ast.Name):
            return annotation.id, False, {}

        if not isinstance(annotation, ast.Call):
            fail("Annotations must be either a name, a function call, or a string.")

        name = annotation.func.id
        kwargs = {node.arg: ast.literal_eval(node.value) for node in annotation.keywords}
        return name, False, kwargs

    def parse_special_symbol(self, symbol):
        if self.parameter_state == self.ps_seen_slash:
            fail("Function " + self.function.name + " specifies " + symbol + " after /, which is unsupported.")

        if symbol == '*':
            if self.keyword_only:
                fail("Function " + self.function.name + " uses '*' more than once.")
            self.keyword_only = True
        elif symbol == '[':
            if self.parameter_state in (self.ps_start, self.ps_left_square_before):
                self.parameter_state = self.ps_left_square_before
            elif self.parameter_state in (self.ps_required, self.ps_group_after):
                self.parameter_state = self.ps_group_after
            else:
                fail("Function " + self.function.name + " has an unsupported group configuration. (Unexpected state " + str(self.parameter_state) + ".b)")
            self.group += 1
            self.function.docstring_only = True
        elif symbol == ']':
            if not self.group:
                fail("Function " + self.function.name + " has a ] without a matching [.")
            if not any(p.group == self.group for p in self.function.parameters.values()):
                fail("Function " + self.function.name + " has an empty group.\nAll groups must contain at least one parameter.")
            self.group -= 1
            if self.parameter_state in (self.ps_left_square_before, self.ps_group_before):
                self.parameter_state = self.ps_group_before
            elif self.parameter_state in (self.ps_group_after, self.ps_right_square_after):
                self.parameter_state = self.ps_right_square_after
            else:
                fail("Function " + self.function.name + " has an unsupported group configuration. (Unexpected state " + str(self.parameter_state) + ".c)")
        elif symbol == '/':
            # ps_required and ps_optional are allowed here, that allows positional-only without option groups
            # to work (and have default values!)
            if (self.parameter_state not in (self.ps_required, self.ps_optional, self.ps_right_square_after, self.ps_group_before)) or self.group:
                fail("Function " + self.function.name + " has an unsupported group configuration. (Unexpected state " + str(self.parameter_state) + ".d)")
            if self.keyword_only:
                fail("Function " + self.function.name + " mixes keyword-only and positional-only parameters, which is unsupported.")
            self.parameter_state = self.ps_seen_slash
            # fixup preceeding parameters
            for p in self.function.parameters.values():
                if (p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD and not isinstance(p.converter, self_converter)):
                    fail("Function " + self.function.name + " mixes keyword-only and positional-only parameters, which is unsupported.")
                p.kind = inspect.Parameter.POSITIONAL_ONLY

    def state_parameter_docstring_start(self, line):
        self.parameter_docstring_indent = len(self.indent.margin)
        assert self.indent.depth == 3
        return self.next(self.state_parameter_docstring, line)

    # every line of the docstring must start with at least F spaces,
    # where F > P.
    # these F spaces will be stripped.
    def state_parameter_docstring(self, line):
        stripped = line.strip()
        if stripped.startswith('#'):
            return

        indent = self.indent.measure(line)
        if indent < self.parameter_docstring_indent:
            self.indent.infer(line)
            assert self.indent.depth < 3
            if self.indent.depth == 2:
                # back to a parameter
                return self.next(self.state_parameter, line)
            assert self.indent.depth == 1
            return self.next(self.state_function_docstring, line)

        assert self.function.parameters
        last_parameter = next(reversed(list(self.function.parameters.values())))

        new_docstring = last_parameter.docstring

        if new_docstring:
            new_docstring += '\n'
        if stripped:
            new_docstring += self.indent.dedent(line)

        last_parameter.docstring = new_docstring

    # the final stanza of the DSL is the docstring.
    def state_function_docstring(self, line):
        if self.group:
            fail("Function " + self.function.name + " has a ] without a matching [.")

        stripped = line.strip()
        if stripped.startswith('#'):
            return

        new_docstring = self.function.docstring
        if new_docstring:
            new_docstring += "\n"
        if stripped:
            line = self.indent.dedent(line).rstrip()
        else:
            line = ''
        new_docstring += line
        self.function.docstring = new_docstring

    def format_docstring(self):
        f = self.function

        new_or_init = f.kind in (METHOD_NEW, METHOD_INIT)
        if new_or_init and not f.docstring:
            # don't render a docstring at all, no signature, nothing.
            return f.docstring

        text, add, output = _text_accumulator()
        parameters = f.render_parameters

        ##
        ## docstring first line
        ##

        if new_or_init:
            # classes get *just* the name of the class
            # not __new__, not __init__, and not module.classname
            assert f.cls
            add(f.cls.name)
        else:
            add(f.name)
        add('(')

        # populate "right_bracket_count" field for every parameter
        assert parameters, "We should always have a self parameter. " + repr(f)
        assert isinstance(parameters[0].converter, self_converter)
        parameters[0].right_bracket_count = 0
        parameters_after_self = parameters[1:]
        if parameters_after_self:
            # for now, the only way Clinic supports positional-only parameters
            # is if all of them are positional-only...
            #
            # ... except for self!  self is always positional-only.

            positional_only_parameters = [p.kind == inspect.Parameter.POSITIONAL_ONLY for p in parameters_after_self]
            if parameters_after_self[0].kind == inspect.Parameter.POSITIONAL_ONLY:
                assert all(positional_only_parameters)
                for p in parameters:
                    p.right_bracket_count = abs(p.group)
            else:
                # don't put any right brackets around non-positional-only parameters, ever.
                for p in parameters_after_self:
                    p.right_bracket_count = 0

        right_bracket_count = 0

        def fix_right_bracket_count(desired):
            nonlocal right_bracket_count
            s = ''
            while right_bracket_count < desired:
                s += '['
                right_bracket_count += 1
            while right_bracket_count > desired:
                s += ']'
                right_bracket_count -= 1
            return s

        need_slash = False
        added_slash = False
        need_a_trailing_slash = False

        # we only need a trailing slash:
        #   * if this is not a "docstring_only" signature
        #   * and if the last *shown* parameter is
        #     positional only
        if not f.docstring_only:
            for p in reversed(parameters):
                if not p.converter.show_in_signature:
                    continue
                if p.is_positional_only():
                    need_a_trailing_slash = True
                break


        added_star = False

        first_parameter = True
        last_p = parameters[-1]
        line_length = len(''.join(text))
        indent = " " * line_length
        def add_parameter(text):
            nonlocal line_length
            nonlocal first_parameter
            if first_parameter:
                s = text
                first_parameter = False
            else:
                s = ' ' + text
                if line_length + len(s) >= 72:
                    add('\n')
                    add(indent)
                    line_length = len(indent)
                    s = text
            line_length += len(s)
            add(s)

        for p in parameters:
            if not p.converter.show_in_signature:
                continue
            assert p.name

            is_self = isinstance(p.converter, self_converter)
            if is_self and f.docstring_only:
                # this isn't a real machine-parsable signature,
                # so let's not print the "self" parameter
                continue

            if p.is_positional_only():
                need_slash = not f.docstring_only
            elif need_slash and not (added_slash or p.is_positional_only()):
                added_slash = True
                add_parameter('/,')

            if p.is_keyword_only() and not added_star:
                added_star = True
                add_parameter('*,')

            p_add, p_output = text_accumulator()
            p_add(fix_right_bracket_count(p.right_bracket_count))

            if isinstance(p.converter, self_converter):
                # annotate first parameter as being a "self".
                #
                # if inspect.Signature gets this function,
                # and it's already bound, the self parameter
                # will be stripped off.
                #
                # if it's not bound, it should be marked
                # as positional-only.
                #
                # note: we don't print "self" for __init__,
                # because this isn't actually the signature
                # for __init__.  (it can't be, __init__ doesn't
                # have a docstring.)  if this is an __init__
                # (or __new__), then this signature is for
                # calling the class to contruct a new instance.
                p_add('$')

            name = p.converter.signature_name or p.name
            p_add(name)

            if p.converter.is_optional():
                p_add('=')
                value = p.converter.py_default
                if not value:
                    value = repr(p.converter.default)
                p_add(value)

            if (p != last_p) or need_a_trailing_slash:
                p_add(',')

            add_parameter(p_output())

        add(fix_right_bracket_count(0))
        if need_a_trailing_slash:
            add_parameter('/')
        add(')')

        # PEP 8 says:
        #
        #     The Python standard library will not use function annotations
        #     as that would result in a premature commitment to a particular
        #     annotation style. Instead, the annotations are left for users
        #     to discover and experiment with useful annotation styles.
        #
        # therefore this is commented out:
        #
        # if f.return_converter.py_default:
        #     add(' -> ')
        #     add(f.return_converter.py_default)

        if not f.docstring_only:
            add("\n--\n")

        docstring_first_line = output()

        # now fix up the places where the brackets look wrong
        docstring_first_line = docstring_first_line.replace(', ]', ',] ')

        # okay.  now we're officially building the "parameters" section.
        # create substitution text for {parameters}
        spacer_line = False
        for p in parameters:
            if not p.docstring.strip():
                continue
            if spacer_line:
                add('\n')
            else:
                spacer_line = True
            add("  ")
            add(p.name)
            add('\n')
            add(textwrap.indent(rstrip_lines(p.docstring.rstrip()), "    "))
        parameters = output()
        if parameters:
            parameters += '\n'

        ##
        ## docstring body
        ##

        docstring = f.docstring.rstrip()
        lines = [line.rstrip() for line in docstring.split('\n')]

        # Enforce the summary line!
        # The first line of a docstring should be a summary of the function.
        # It should fit on one line (80 columns? 79 maybe?) and be a paragraph
        # by itself.
        #
        # Argument Clinic enforces the following rule:
        #  * either the docstring is empty,
        #  * or it must have a summary line.
        #
        # Guido said Clinic should enforce this:
        # http://mail.python.org/pipermail/python-dev/2013-June/127110.html

        if len(lines) >= 2:
            if lines[1]:
                fail("Docstring for " + f.full_name + " does not have a summary line!\n" +
                    "Every non-blank function docstring must start with\n" +
                    "a single line summary followed by an empty line.")
        elif len(lines) == 1:
            # the docstring is only one line right now--the summary line.
            # add an empty line after the summary line so we have space
            # between it and the {parameters} we're about to add.
            lines.append('')

        parameters_marker_count = len(docstring.split('{parameters}')) - 1
        if parameters_marker_count > 1:
            fail('You may not specify {parameters} more than once in a docstring!')

        if not parameters_marker_count:
            # insert after summary line
            lines.insert(2, '{parameters}')

        # insert at front of docstring
        lines.insert(0, docstring_first_line)

        docstring = "\n".join(lines)

        add(docstring)
        docstring = output()

        docstring = linear_format(docstring, parameters=parameters)
        docstring = docstring.rstrip()

        return docstring

    def state_terminal(self, line):
        """
        Called when processing the block is done.
        """
        assert not line

        if not self.function:
            return

        if self.keyword_only:
            values = self.function.parameters.values()
            if not values:
                no_parameter_after_star = True
            else:
                last_parameter = next(reversed(list(values)))
                no_parameter_after_star = last_parameter.kind != inspect.Parameter.KEYWORD_ONLY
            if no_parameter_after_star:
                fail("Function " + self.function.name + " specifies '*' without any parameters afterwards.")

        # remove trailing whitespace from all parameter docstrings
        for name, value in self.function.parameters.items():
            if not value:
                continue
            value.docstring = value.docstring.rstrip()

        self.function.docstring = self.format_docstring()




# maps strings to callables.
# the callable should return an object
# that implements the clinic parser
# interface (__init__ and parse).
#
# example parsers:
#   "clinic", handles the Clinic DSL
#   "python", handles running Python code
#
parsers = {'clinic' : DSLParser, 'python': PythonParser}


clinic = None


def main(argv):
    import sys

    if sys.version_info.major < 3 or sys.version_info.minor < 3:
        sys.exit("Error: clinic.py requires Python 3.3 or greater.")

    import argparse
    cmdline = argparse.ArgumentParser()
    cmdline.add_argument("-f", "--force", action='store_true')
    cmdline.add_argument("-o", "--output", type=str)
    cmdline.add_argument("-v", "--verbose", action='store_true')
    cmdline.add_argument("--converters", action='store_true')
    cmdline.add_argument("--make", action='store_true')
    cmdline.add_argument("filename", type=str, nargs="*")
    ns = cmdline.parse_args(argv)

    if ns.converters:
        if ns.filename:
            print("Usage error: can't specify --converters and a filename at the same time.")
            print()
            cmdline.print_usage()
            sys.exit(-1)
        converters = []
        return_converters = []
        ignored = set("""
            add_c_converter
            add_c_return_converter
            add_default_legacy_c_converter
            add_legacy_c_converter
            """.strip().split())
        module = globals()
        for name in module:
            for suffix, ids in (
                ("_return_converter", return_converters),
                ("_converter", converters),
            ):
                if name in ignored:
                    continue
                if name.endswith(suffix):
                    ids.append((name, name[:-len(suffix)]))
                    break
        print()

        print("Legacy converters:")
        legacy = sorted(legacy_converters)
        print('    ' + ' '.join(c for c in legacy if c[0].isupper()))
        print('    ' + ' '.join(c for c in legacy if c[0].islower()))
        print()

        for title, attribute, ids in (
            ("Converters", 'converter_init', converters),
            ("Return converters", 'return_converter_init', return_converters),
        ):
            print(title + ":")
            longest = -1
            for name, short_name in ids:
                longest = max(longest, len(short_name))
            for name, short_name in sorted(ids, key=lambda x: x[1].lower()):
                cls = module[name]
                callable = getattr(cls, attribute, None)
                if not callable:
                    continue
                signature = inspect.signature(callable)
                parameters = []
                for parameter_name, parameter in signature.parameters.items():
                    if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                        if parameter.default != inspect.Parameter.empty:
                            s = '{}={!r}'.format(parameter_name, parameter.default)
                        else:
                            s = parameter_name
                        parameters.append(s)
                print('    {}({})'.format(short_name, ', '.join(parameters)))
            print()
        print("All converters also accept (c_default=None, py_default=None, annotation=None).")
        print("All return converters also accept (py_default=None).")
        sys.exit(0)

    if ns.make:
        if ns.output or ns.filename:
            print("Usage error: can't use -o or filenames with --make.")
            print()
            cmdline.print_usage()
            sys.exit(-1)
        for root, dirs, files in os.walk('.'):
            for rcs_dir in ('.svn', '.git', '.hg', 'build'):
                if rcs_dir in dirs:
                    dirs.remove(rcs_dir)
            for filename in files:
                if not (filename.endswith('.c') or filename.endswith('.h')):
                    continue
                path = os.path.join(root, filename)
                if ns.verbose:
                    print(path)
                parse_file(path, force=ns.force, verify=not ns.force)
        return

    if not ns.filename:
        cmdline.print_usage()
        sys.exit(-1)

    if ns.output and len(ns.filename) > 1:
        print("Usage error: can't use -o with multiple filenames.")
        print()
        cmdline.print_usage()
        sys.exit(-1)

    for filename in ns.filename:
        if ns.verbose:
            print(filename)
        parse_file(filename, output=ns.output, force=ns.force, verify=not ns.force)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
