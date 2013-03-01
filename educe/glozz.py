# Author: Eric Kow
# License: BSD3

"""
The Glozz_ file format in `educe.annotation` form

You're likely most interested in
`slurp_corpus` and `read_annotation_file`

.. _Glozz: http://www.glozz.org/
"""

import xml.etree.ElementTree as ET
import sys

from educe.annotation import *

# ---------------------------------------------------------------------
# xml processing
# -----------------------------------------------------------

# TODO: learn how exceptions work in Python; can I embed
# arbitrary strings in them?
#
# TODO: probably replace starting/ending integers with
# exception of some sort
class GlozzException(Exception):
    def __init__(self, *args, **kw):
        Exception.__init__(self, *args, **kw)

def on_single_element(root, default, f, name):
    """Return
       * the default if no elements
       * f(the node) if one element
       * an exception if more than one
    """
    nodes=root.findall(name)
    if len(nodes) == 0:
        return default
    elif len(nodes) > 1:
        raise GlozzException()
    else:
        return f(nodes[0])

# ---------------------------------------------------------------------
# glozz files
# ---------------------------------------------------------------------

def read_node(node, context=None):
    def get_one(name, default, ctx=None):
        f = lambda n : read_node(n, ctx)
        return on_single_element(node, default, f, name)

    def get_all(name):
        return map(read_node, node.findall(name))

    if node.tag == 'annotations':
        units = get_all('unit')
        rels  = get_all('relation')
        return (units, rels)

    elif node.tag == 'characterisation':
        fs        = get_one('featureSet', [])
        unit_type = get_one('type'      , GlozzException)
        return (unit_type, fs)

    elif node.tag == 'feature':
        attr=node.attrib['name']
        val =node.text
        return (attr, val)

    ## TODO throw exception if we see more than one instance of a key
    elif node.tag == 'featureSet':
        return dict(get_all('feature'))

    elif node.tag == 'positioning' and context == 'unit':
        start = get_one('start', -2)
        end   = get_one('end',   -2)
        return Span(start,end)

    elif node.tag == 'positioning' and context == 'relation':
        terms = get_all('term')
        if len(terms) != 2:
            raise GlozzException()
        else:
            return RelSpan(terms[0], terms[1])

    elif node.tag == 'relation':
        rel_id          = node.attrib['id']
        (unit_type, fs) = get_one('characterisation', GlozzException)
        span            = get_one('positioning',      RelSpan(-1,-1), 'relation')
        return Relation(rel_id, span, unit_type, fs)

    elif node.tag == 'singlePosition':
        return int(node.attrib['index'])

    elif node.tag == 'start' or node.tag == 'end':
        return get_one('singlePosition', -3)

    elif node.tag == 'term':
        return node.attrib['id']

    elif node.tag == 'type':
        return node.text.strip()

    elif node.tag == 'unit':
        unit_id         = node.attrib['id']
        (unit_type, fs) = get_one('characterisation', GlozzException)
        span            = get_one('positioning',      Span(-1,-1), 'unit')
        return Unit(unit_id, span, unit_type, fs)

def read_annotation_file(anno_filename, text_filename=None):
    """
    Read a single glozz annotation file and its corresponding text
    (if any).
    """
    tree = ET.parse(anno_filename)
    res  = read_node(tree.getroot())
    text = None
    if text_filename is not None:
        with open(text_filename) as tf:
            text = tf.read()
    return Document(res[0],res[1],text)
