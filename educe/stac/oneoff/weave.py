# Author: Eric Kow
# License: BSD3

"""
Combining annotations from an augmented 'source' document (with likely extra
text) with those in a 'target' document. This involves copying missing
annotations over and shifting the text spans of any matching documents
"""

from __future__ import print_function

from collections import namedtuple
import sys

from educe.annotation import Span
from educe.stac.annotation import is_structure, DIALOGUE_ACTS, RENAMES
from educe.stac.context import enclosed


class WeaveException(Exception):

    """
    Unexpected alignment issues between the source and target
    document
    """

    def __init__(self, *args, **kw):
        super(WeaveException, self).__init__(*args, **kw)


class Updates(namedtuple('Updates',
                         ['shift_if_ge',
                          'abnormal_src_only',
                          'abnormal_tgt_only',
                          'expected_src_only'])):

    """Expected updates to the target document.

    We expect to see four types of annotation:

    1. target annotations for which there exists a
       source annotation in the equivalent span

    2. target annotations for which there is no equivalent
       source annotation (eg. Resources, Preferences, but also
       annotation moves)

    3. source annotations for which there is at least one target
       annotation at the equivalent span (the mirror to case 1;
       note that these are not represented in this structure
       because we don't need to say much about them)

    4. source annotations for which there is no match in the target side

    5. source annotations that lie in between the matching bits of
       text

    Parameters
    ----------
    shift_if_ge : dict(int, int)
        (case 1 and 2) shift points and offsets for characters
        in the target document (see `shift_spans`)
    abnormal_src_only: [Annotation]
        (case 4) annotations that only occur in the
        source document (weird, found in matches)
    abnormal_tgt_only : [Annotation]
        (case 2) annotations that only occur
        in the target document (weird, found in matches)
    expected_src_only [Annotation]
        (case 5) annotations that only occur in the
        source doc (ok, found in gaps)
    """
    def __new__(cls):
        return super(Updates, cls).__new__(cls, {}, [], [], [])

    def map(self, fun):
        """
        Return an `Updates` in which a function has been applied to
        all annotations in this one (eg. useful for previewing),
        and to all spans
        """
        supercls = super(Updates, Updates)
        return supercls.__new__(Updates,
                                self.shift_if_ge,
                                [fun(x) for x in self.abnormal_src_only],
                                [fun(x) for x in self.abnormal_tgt_only],
                                [fun(x) for x in self.expected_src_only])

# ---------------------------------------------------------------------
#
# ---------------------------------------------------------------------


def src_gaps(matches):
    """
    Given matches between the source and target document, return the spaces
    between these matches as source offset and size (a bit like the matches).
    Note that we assume that the target document text is a subsequence of the
    source document.
    """
    gaps = []
    last_idx = 0
    for src, _, size in matches:
        if src != last_idx:
            gaps.append((last_idx, src - last_idx))
        last_idx = src + size
    return gaps


def tgt_gaps(matches):
    """
    Given matches between the source and target document, return the spaces
    between these matches as target offset and size (a bit like the matches).
    By rights this should be empty, but you never know
    """
    gaps = []
    last_idx = 0
    for _, tgt, size in matches:
        if tgt != last_idx:
            gaps.append((last_idx, tgt - last_idx))
        last_idx = tgt + size
    return gaps


def check_matches(tgt_doc, matches, strict=True):
    """
    Check that the target document text is indeed a subsequence of
    the source document text (the source document is expected to be
    "augmented" version of the target with new text interspersed
    throughout)

    Parameters
    ----------
    tgt_doc :
    matches : list of (int, int, int)
        List of triples (i, j, n) representing matching subsequences:
        a[i:i+n] == b[j:j+n].
        See `difflib.SequenceMatcher.get_matching_blocks`.
    strict : boolean
        If True, raise an exception if there are match gaps in the
        target document, otherwise just print the gaps to stderr.
    """
    tgt_text = tgt_doc.text()

    if not tgt_text:
        return
    elif not matches:
        raise WeaveException('no matches in non-empty target doc')
    elif matches[0].b != 0:
        oops = ('matches ({}) do not start at beginning of target '
                'document <{}>').format(matches[0], tgt_doc.origin)
        raise WeaveException(oops)

    gaps = tgt_gaps(matches)
    if gaps:
        # we might want to give some slack because gaps can result from
        # manual rewrites that happened here and there in the soclogs
        # e.g. a pair of logical not (&not;) around _ => ^_^
        # in these cases, just print them on stderr for quick checks
        for gap in gaps:
            gap_txt = tgt_text[gap[0]:gap[0] + gap[1]]
            print(u"Match gap in tgt doc ({})\t{}\t{}".format(
                tgt_doc.origin, gap, gap_txt), file=sys.stderr)
        print(matches)
        tgt_turns = set(x.features['Identifier']
                        for x in tgt_doc.units
                        if x.features.get('Identifier'))
        print(sorted(tgt_turns))
        print('\n'.join(str(x) for x
                        in sorted(tgt_doc.units, key=lambda y: y.span)))
        # end DEBUG
        if strict:
            oops = 'there are match gaps in the target document {}: {}'
            raise WeaveException(oops.format(tgt_doc.origin, gaps))

    _, tgt, size = matches[-1]
    if tgt + size != len(tgt_text):
        raise WeaveException('matches do not cover the full target '
                             'document')


def compute_updates(src_doc, tgt_doc, matches):
    """Return updates that would need to be made on the target
    document.

    Given matches between the source and target document, return span
    updates along with any source annotations that do not have an
    equivalent in the target document (the latter may indicate that
    resegmentation has taken place, or that there is some kind of problem)

    Parameters
    ----------
    src_doc : Document
    tgt_doc : Document
    matches : [Match]

    Returns
    -------
    updates: Updates
    """
    res = Updates()

    # case 2 and 5 (to be pruned below)
    res.expected_src_only.extend(src_doc.units)
    res.abnormal_tgt_only.extend(tgt_doc.units)

    # case 1, 2 and 4
    for src, tgt, size in matches:
        tgt_to_src = src - tgt
        res.shift_if_ge[tgt] = tgt_to_src  # case 1 and 2
        src_annos = enclosed(Span(src, src + size), src_doc.units)
        tgt_annos = enclosed(Span(tgt, tgt + size), tgt_doc.units)
        # NEW compute (shifted) spans once, before looping over annotations
        src_spans = [anno.text_span() for anno in src_annos]
        tgt_spans = [anno.text_span().shift(tgt_to_src)
                     for anno in tgt_annos]
        for src_span, src_anno in zip(src_spans, src_annos):
            res.expected_src_only.remove(src_anno)  # prune from case 5
            tgt_equiv = [tgt_anno for tgt_span, tgt_anno
                         in zip(tgt_spans, tgt_annos)
                         if tgt_span == src_span]
            if not tgt_equiv:  # case 4
                res.abnormal_src_only.append(src_anno)
            for tgt_anno in tgt_equiv:  # prune from case 2
                if tgt_anno in res.abnormal_tgt_only:
                    res.abnormal_tgt_only.remove(tgt_anno)

    return res


def stretch_match(updates, src_doc, tgt_doc, span_src, span_tgt,
                  annos_src, annos_tgt, verbose=0):
    """Compute stretch matches between `annos_src` and `annos_tgt`.

    Parameters
    ----------
    updates : Update
    src_doc : Document
    tgt_doc : Document
    span_src : Span
    span_tgt : Span
    annos_src : list of educe.annotation
        Unmatched annotations in `span_src`.
    annos_tgt : list of educe.annotation
        Unmatched annotations in `span_tgt`.
    verbose : int
        Verbosity level

    Returns
    -------
    res : Update
        Possibly trimmed version of `updates`.
    """
    # unmatched structs in src
    cands_src = enclosed(Span(span_src[0], span_src[1]),
                         annos_src)
    spans_src = [anno.text_span() for anno in cands_src]
    # unmatched structs in tgt
    cands_tgt = enclosed(Span(span_tgt[0], span_tgt[1]),
                         annos_tgt)
    spans_tgt = [shift_span(anno.text_span(), updates)
                 for anno in cands_tgt]

    # {one,many} to one match between source and target
    for span_tgt, cand_tgt in zip(spans_tgt, cands_tgt):
        # search for an annotation in source on the exact same span
        src_equiv = [cand_src for span_src, cand_src
                     in zip(spans_src, cands_src)
                     if span_src == span_tgt]
        if src_equiv:
            # 1 to 1 match between source and target
            #
            # the target structure has a (stretch) match in the source
            updates.abnormal_tgt_only.remove(cand_tgt)
            if verbose:
                print('Remove {} from abnormal_tgt_only'.format(cand_tgt),
                      file=sys.stderr)  # DEBUG
            for cand_src in src_equiv:
                # these source structures are neither abnormal
                if cand_src in updates.abnormal_src_only:
                    updates.abnormal_src_only.remove(cand_src)
                    if verbose:
                        print('Remove {} from abnormal_src_only'.format(
                            cand_src),
                              file=sys.stderr)  # DEBUG
                # nor expected to appear only in source
                if cand_src in updates.expected_src_only:
                    updates.expected_src_only.remove(cand_src)
                    if verbose:
                        print('Remove {} from expected_src_only'.format(
                            cand_src),
                              file=sys.stderr)  # DEBUG
        else:
            # many to 1 match between source and target
            #
            # search for a sequence of contiguous annotations in source
            # that covers the same span as a single annotation of the
            # same type in target ; this is supposed to capture the
            # result of `stac-edit merge-{dialogue,edu}`
            src_equiv_cands = enclosed(span_tgt, cands_src)
            src_equiv_seq = sorted(src_equiv_cands, key=lambda x: x.span)
            # if the sequence covers the targeted span
            if ((src_equiv_seq and
                 src_equiv_seq[0].span.char_start == span_tgt.char_start and
                 src_equiv_seq[-1].span.char_end == span_tgt.char_end)):
                # and has no gap or just whitespaces
                gap_str = ''.join(
                    src_doc.text(span=Span(elt_cur.span.char_end,
                                           elt_nex.span.char_start))
                    for elt_cur, elt_nex
                    in zip(src_equiv_seq[:-1], src_equiv_seq[1:])
                )
                gap_str = gap_str.strip()
                if not gap_str:
                    # mark the target anno as matched
                    if cand_tgt in updates.abnormal_tgt_only:
                        updates.abnormal_tgt_only.remove(cand_tgt)
                    # and the source annotations likewise
                    for src_equiv_elt in src_equiv_seq:
                        if src_equiv_elt in updates.abnormal_src_only:
                            updates.abnormal_src_only.remove(src_equiv_elt)
                        if src_equiv_elt in updates.expected_src_only:
                            updates.expected_src_only.remove(src_equiv_elt)
                    if verbose:
                        print('Guess: {} results from a merge on {}'.format(
                            str(cand_tgt), [str(x) for x in src_equiv_seq]),
                              file=sys.stderr)

    # one to many match between source and target
    for span_src, cand_src in zip(spans_src, cands_src):
        # search for a sequence of contiguous annotations in target
        # that covers the same span as a single annotation of the
        # same type in source ; this is supposed to capture the
        # result of `stac-edit split-{dialogue,edu}`
        tgt_equiv_cands = [cand_tgt for span_tgt, cand_tgt
                           in zip(spans_tgt, cands_tgt)
                           if span_src.encloses(span_tgt)]

        tgt_equiv_seq = sorted(tgt_equiv_cands, key=lambda x: x.span)
        # if the sequence covers the source span
        if ((tgt_equiv_seq and
             tgt_equiv_seq[0].span.char_start == span_src.char_start and
             tgt_equiv_seq[-1].span.char_end == span_src.char_end)):
            # and has no gap or just whitespaces
            gap_str = ''.join(
                tgt_doc.text(span=Span(elt_cur.span.char_end,
                                       elt_nex.span.char_start))
                for elt_cur, elt_nex
                in zip(tgt_equiv_seq[:-1], tgt_equiv_seq[1:])
            )
            gap_str = gap_str.strip()
            if not gap_str:
                # mark the source anno as matched
                if cand_src in updates.abnormal_src_only:
                    updates.abnormal_src_only.remove(cand_src)
                if cand_src in updates.expected_src_only:
                    updates.expected_src_only.remove(cand_src)
                # and the target annotations likewise
                for tgt_equiv_elt in tgt_equiv_seq:
                    if tgt_equiv_elt in updates.abnormal_tgt_only:
                        updates.abnormal_tgt_only.remove(tgt_equiv_elt)
                if verbose:
                    print('Guess: {} results from a split on {}'.format(
                        [str(x) for x in tgt_equiv_seq], str(cand_src)),
                          file=sys.stderr)

    # TODO? many to many match between source and target
    return updates


UNITS = DIALOGUE_ACTS + RENAMES.keys()


def compute_structural_updates(src_doc, tgt_doc, matches, updates, verbose=0):
    """Transfer structural annotations from `tgt_doc` to `src_doc`.

    This is the transposition of `compute_updates` to structural
    units (dialogues only, for the moment).
    """
    # match structural units: transpose to the augmented `src_doc`
    # the structures defined on (a subspan of) a sequence of contiguous
    # match spans in b
    stretch_map = {}
    offset_src = matches[0].a
    offset_tgt = matches[0].b
    for m, m1 in zip(matches[:-1], matches[1:]):
        # the next match is not contiguous on tgt
        if m.b + m.size != m1.b:
            merged_span_src = (offset_src, m.a + m.size)
            merged_span_tgt = (offset_tgt, m.b + m.size)
            stretch_map[merged_span_tgt] = merged_span_src
            # update offsets
            offset_src = m.a
            offset_tgt = m.b
    # add final stretch map
    merged_span_src = (offset_src, m.a + m.size)
    merged_span_tgt = (offset_tgt, m.b + m.size)
    stretch_map[merged_span_src] = merged_span_tgt

    # gather all unmatched units from tgt and tgt that can be stretched:
    # dialogues and segments (we'll see if they can be treated the same)
    unmatched_src_annos = set(updates.abnormal_src_only +
                              updates.expected_src_only)
    unmatched_src_dlgs = [x for x in unmatched_src_annos
                          if x.type.lower() == 'dialogue']
    unmatched_src_segs = [x for x in unmatched_src_annos
                          if x.type.lower() == 'segment']
    # target: same categories + units
    unmatched_tgt_annos = set(updates.abnormal_tgt_only)
    unmatched_tgt_dlgs = [x for x in unmatched_tgt_annos
                          if x.type.lower() == 'dialogue']
    unmatched_tgt_segs = [x for x in unmatched_tgt_annos
                          if x.type.lower() == 'segment']
    unmatched_tgt_units = [x for x in unmatched_tgt_annos
                           if x.type.lower() in set(y.lower() for y in UNITS)]
    # try to match them using the stretched maps
    for span_src, span_tgt in stretch_map.items():
        # dialogues
        updates = stretch_match(updates, src_doc, tgt_doc,
                                span_src, span_tgt,
                                unmatched_src_dlgs, unmatched_tgt_dlgs,
                                verbose=verbose)
        # EDUs (segments)
        updates = stretch_match(updates, src_doc, tgt_doc,
                                span_src, span_tgt,
                                unmatched_src_segs, unmatched_tgt_segs,
                                verbose=verbose)
        # units / discourse acts
        updates = stretch_match(updates, src_doc, tgt_doc,
                                span_src, span_tgt,
                                unmatched_src_segs, unmatched_tgt_units,
                                verbose=verbose)
    return updates


def shift_char(position, updates):
    """
    Given a character position an updates tuple, return a shifted over
    position which reflects the update.


    The basic idea that we have a set of "shift points" and their
    corresponding offsets. If a character position 'c' occurs after
    one of the points, we take the offset of the largest such point
    and add it to the character.

    Our assumption here is that the update always consists in adding more
    text so offsets are always positive.

    Parameters
    ----------
    position: int
        initial position
    updates: Updates

    Returns
    -------
    int
        shifted position
    """
    points = [x for x in updates.shift_if_ge if position >= x]
    offset = updates.shift_if_ge[max(points)] if points else 0
    assert offset >= 0
    return position + offset


def shift_span(span, updates):
    """
    Given a span and an updates tuple, return a Span
    that is shifted over to reflect the updates

    Parameters
    ----------
    span: Span
    updates: Updates

    Returns
    -------
    span: Span

    See also
    --------
    shift_char: for details on how this works
    """
    start = shift_char(span.char_start, updates)
    # this is to avoid spurious overstretching of the right
    # boundary of an annotation that buts up against the
    # left of a new annotation
    end = 1 + shift_char(span.char_end - 1, updates)
    return Span(start, end)
