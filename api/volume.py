"""
PARSING "how many demands a month?" INTO A NUMBER.

She is a receptionist giving an estimate, not filling in a form. The answers
are "about 20", "maybe 5 or 6", "a hundred", "no idea", "depends".

TWO RULES:

  1. THE VERBATIM IS ALWAYS KEPT. The number is a convenience for sorting and
     forecasting; her words are the record.

  2. NULL MEANS SHE DID NOT ANSWER, and null is the honest answer far more
     often than a guess is. Parsing "5 or 6" as 5 is a judgement; parsing
     "depends on the month" as anything is an invention. When in doubt, null -
     a missing number is visibly missing, whereas a wrong one silently skews
     every forecast built on it.
"""

import re

WORDS = {
    'zero': 0, 'none': 0, 'no': 0,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
    'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
    'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70,
    'eighty': 80, 'ninety': 90,
}

# "no idea", "not sure", "depends" - an answer that is not a number. These are
# NOT failures to parse; they are her declining, and the difference matters
# when you are deciding whether to ask again.
NON_ANSWERS = ('no idea', 'not sure', 'dunno', "don't know", 'do not know',
               'depends', 'varies', 'hard to say', 'couldn', 'no clue')

MAX = 10000


def parse(raw):
    """
    (number|None, note). The note says WHY there is no number, so a screen can
    tell "she declined" from "we could not read it".
    """
    text = (raw or '').strip().lower()
    if not text:
        return None, 'not asked or not answered'

    for phrase in NON_ANSWERS:
        if phrase in text:
            return None, 'she did not know'

    # A range - "5 or 6", "10-15", "between 20 and 30". Take the LOW end:
    # a forecast that rounds every estimate up is a forecast that flatters
    # itself, and she is guessing in the first place.
    rng = re.search(r'(\d+)\s*(?:-|to|or|and)\s*(\d+)', text)
    if rng:
        lo, hi = int(rng.group(1)), int(rng.group(2))
        n = min(lo, hi)
        return (n if 0 <= n <= MAX else None), f'range {lo}-{hi}, took the low end'

    digits = re.search(r'\b(\d{1,5})\b', text)
    if digits:
        n = int(digits.group(1))
        return (n if 0 <= n <= MAX else None), ('' if 0 <= n <= MAX
                                                else 'out of plausible range')

    # Words. "a hundred", "one hundred", "a couple" - only the unambiguous ones.
    if re.search(r'\bhundred\b', text):
        m = re.search(r'\b(\w+)\s+hundred\b', text)
        mult = WORDS.get(m.group(1)) if m else None
        if m and m.group(1) in ('a', 'one'):
            mult = 1
        return ((mult or 1) * 100), 'from words'
    words = re.findall(r'\b(' + '|'.join(WORDS) + r')\b', text)
    if words:
        # "twenty five" -> 25; a single word -> itself.
        if len(words) == 2 and WORDS[words[0]] >= 20 and WORDS[words[1]] < 10:
            return WORDS[words[0]] + WORDS[words[1]], 'from words'
        return WORDS[words[0]], 'from words'

    if 'couple' in text:
        return 2, 'from words'
    if 'few' in text:
        return None, 'too vague to number'

    return None, 'could not read a number'
