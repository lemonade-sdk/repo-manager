"""Reading a sentence for what it actually claims.

The validators guard against one failure above all: an artifact whose list says "nothing to
do" while its prose says a human still has to look at something. Matching a word is not
enough to catch that — "nothing blocks the release" is the sentence a clean review writes,
and a guard that fires on it would force a retry loop over a correct answer.

So each match is read in its own context: a claim that carries a negation just before it, in
the same clause, is not a claim. That keeps both halves of the sentence honest in "Nothing
else blocks the release, though a maintainer should confirm the Fedora package before
shipping" — the first clause is dismissed, the second is caught.
"""

import re


CLAUSE_END = re.compile(r"[.;!?]")


def asserts(text, pattern, negation, window=50):
    """Does any match of `pattern` stand without a negation just before it?"""
    text = str(text or "")
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - window) : match.start()]
        clause = CLAUSE_END.split(before)[-1]
        if not negation.search(clause):
            return True
    return False
