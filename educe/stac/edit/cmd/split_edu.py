# Author: Eric Kow
# License: CeCILL-B (French BSD3-like)

"""
Split an EDU along given cut points
"""

from __future__ import print_function
from collections import namedtuple
import copy
import sys

import educe.annotation
import educe.stac

from educe.stac.util.annotate import show_diff, annotate_doc
from educe.stac.util.glozz import\
    TimestampCache, set_anno_author, set_anno_date,\
    anno_id_from_tuple
from educe.stac.util.args import\
    add_usual_input_args, add_usual_output_args,\
    add_commit_args,\
    read_corpus_with_unannotated,\
    get_output_dir, announce_output_dir,\
    comma_span
from educe.stac.util.doc import\
    narrow_to_span, enclosing_span, retarget
from educe.stac.util.output import save_document


NAME = 'split-edu'
_AUTHOR = 'stacutil'
# pylint: disable=fixme
_SPLIT_PREFIX = 'FIXME:'
# pylint: enable=fixme


def config_argparser(parser):
    """
    Subcommand flags.

    You should create and pass in the subparser to which the flags
    are to be added.
    """
    add_usual_input_args(parser, doc_subdoc_required=True)
    parser.add_argument('--annotator', metavar='PY_REGEX',
                        required=True,  # should limit annotator
                        help='annotator')
    parser.add_argument('--spans', metavar='SPAN', type=comma_span,
                        required=True,
                        nargs='+',
                        help='Desired output spans (must cover original EDU)')
    add_usual_output_args(parser, default_overwrite=True)
    add_commit_args(parser)
    parser.set_defaults(func=main)


def _tweak_presplit(tcache, doc, spans):
    """
    What to do in case the split was already done manually
    (in the discourse section)
    """
    renames = {}
    for span in sorted(spans):
        matches = [x for x in doc.units
                   if x.text_span() == span and educe.stac.is_edu(x)]
        if not matches:
            raise Exception("No matches found for %s in %s" %
                            (span, doc.origin), file=sys.stderr)
        edu = matches[0]
        old_id = edu.local_id()
        new_id = anno_id_from_tuple((_AUTHOR, tcache.get(span)))
        set_anno_date(edu, tcache.get(span))
        set_anno_author(edu, _AUTHOR)
        renames[old_id] = new_id

    for rel in doc.relations:
        if rel.span.t1 in renames:
            rel.span.t1 = renames[rel.span.t1]
        if rel.span.t2 in renames:
            rel.span.t2 = renames[rel.span.t2]
    for schema in doc.schemas:
        units2 = set(schema.units)
        for unit in schema.units:
            if unit in renames:
                units2.remove(unit)
                units2.add(renames[unit])
        schema.units = units2


def _actually_split(tcache, doc, spans, edu):
    """
    Split the EDU, trying to generate the same new ID for the
    same new EDU across all sections

    Discourse stage: If the EDU is in any relations or CDUs,
    replace any references to it with a new CDU encompassing
    the newly created EDUs
    """

    new_edus = {}
    for span in sorted(spans):
        stamp = tcache.get(span)
        edu2 = copy.deepcopy(edu)
        new_id = anno_id_from_tuple((_AUTHOR, stamp))
        set_anno_date(edu2, stamp)
        set_anno_author(edu2, _AUTHOR)
        if doc.origin.stage == 'units':
            edu2.type = _SPLIT_PREFIX + edu2.type
            for key in edu2.features:
                edu2.features[key] = _SPLIT_PREFIX + edu2.features[key]
        new_edus[new_id] = edu2
        edu2.span = span
        doc.units.append(edu2)

    cdu_stamp = tcache.get(enclosing_span(spans))
    cdu = educe.annotation.Schema(anno_id_from_tuple((_AUTHOR, cdu_stamp)),
                                  frozenset(new_edus),
                                  frozenset(),
                                  frozenset(),
                                  'Complex_discourse_unit',
                                  {},
                                  metadata={'author': _AUTHOR,
                                            'creation-date': str(cdu_stamp)})
    cdu.fleshout(new_edus)

    want_cdu = retarget(doc, edu.local_id(), cdu)
    doc.units.remove(edu)
    if want_cdu:
        doc.schemas.append(cdu)


def _split_edu(tcache, k, doc, spans):
    """
    Find the edu covered by these spans and do the split
    """
    # seek edu
    big_span = enclosing_span(spans)
    matches = [x for x in doc.units
               if x.text_span() == big_span and educe.stac.is_edu(x)]
    if not matches and k.stage != 'discourse':
        print("No matches found in %s" % k, file=sys.stderr)
    elif not matches:
        _tweak_presplit(tcache, doc, spans)
    else:
        _actually_split(tcache, doc, spans, matches[0])


def _mini_diff(k, old_doc, new_doc, span):
    """
    Return lines of text to be printed out, showing how the EDU
    split affected the text
    """
    mini_old_doc = narrow_to_span(old_doc, span)
    mini_new_doc = narrow_to_span(new_doc, span)
    return ["======= SPLIT EDU %s ========" % (k),
            "...",
            show_diff(mini_old_doc, mini_new_doc),
            "...",
            ""]


CommitInfo = namedtuple("CommitTuple", "key annotator before after span")


def commit_msg(info):
    """
    Generate a commit message describing the operation
    we just did
    """
    k = info.key
    turns = [x for x in info.before.units if educe.stac.is_turn(x) and
             x.text_span().encloses(info.span)]
    if turns:
        turn = turns[0]
        tspan = turn.text_span()
        ttext = info.before.text(tspan)
        prefix_b = educe.stac.split_turn_text(ttext)[0]
    else:
        tspan = info.span
        prefix_b = "    "
    prefix_a = "==> ".rjust(len(prefix_b))

    def anno(doc, prefix, tspan):
        "pad text segment as needed"

        prefix_t = "..."\
            if tspan.char_start + len(prefix) < info.span.char_start\
            else ""
        suffix_t = "..."\
            if tspan.char_end > info.span.char_end + 1\
            else ""
        return "".join([prefix,
                        prefix_t,
                        annotate_doc(doc, span=info.span),
                        suffix_t])

    lines = ["%s_%s: scary edit (split EDUs)" % (k.doc, k.subdoc),
             "",
             anno(info.before, prefix_b, tspan),
             anno(info.after, prefix_a, tspan),
             "",
             "NB: only unannotated and %s are modified" % info.annotator]
    return "\n".join(lines)


def main(args):
    """
    Subcommand main.

    You shouldn't need to call this yourself if you're using
    `config_argparser`
    """
    corpus = read_corpus_with_unannotated(args)
    tcache = TimestampCache()
    output_dir = get_output_dir(args, default_overwrite=True)
    commit_info = None
    for k in corpus:
        old_doc = corpus[k]
        new_doc = copy.deepcopy(old_doc)
        span = enclosing_span(args.spans)
        _split_edu(tcache, k, new_doc, args.spans)
        diffs = _mini_diff(k, old_doc, new_doc, span)
        print("\n".join(diffs).encode('utf-8'), file=sys.stderr)
        save_document(output_dir, k, new_doc)
        # for commit message generation
        commit_info = CommitInfo(key=k,
                                 annotator=args.annotator,
                                 before=old_doc,
                                 after=new_doc,
                                 span=span)
    if commit_info and not args.no_commit_msg:
        print("-----8<------")
        print(commit_msg(commit_info))
    announce_output_dir(output_dir)
