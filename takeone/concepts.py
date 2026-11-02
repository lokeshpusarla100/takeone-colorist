"""SAM concept phrases: the planner's phrase, its own simpler fallbacks, then a deterministic one.

SAM 3 concept prompting matches short noun phrases ("rock", "sky", "yellow boat"). A planner
sentence such as "dark coastal rocks forming natural arch" can match nothing even when the
object fills the frame. When a phrase finds no mask in any required frame, the service tries
the next phrase here, bounded to MAX_ATTEMPTS billed SAM calls per concept. Nothing about a
particular video is encoded: the fallback is plain English noun-phrase structure.
"""
import re

MAX_ATTEMPTS = 3
# Words that start a clause, relation, position or lighting qualifier after the noun phrase.
_BREAKS = re.compile(r'\b(with|forming|formed|above|below|under|over|behind|beside|near|next|on|in|at|of|from|'
                     r'along|across|around|between|against|that|which|where|while|during|lit|sunlit|covered|'
                     r'containing|showing|including|and|or)\b', re.I)


def head_phrase(concept):
    """Noun phrase before any clause/relation word, and its last word (the head noun in English)."""
    text = ' '.join(concept.replace('_', ' ').split())
    left = _BREAKS.split(text, maxsplit=1)[0].strip(' -,.') or text
    words = left.split()
    return left, (words[-1] if words else left)


def candidates(concept, alternatives=(), limit=MAX_ATTEMPTS):
    """Ordered, de-duplicated phrases to try: as planned, the planner's fallbacks, then the head phrase/noun."""
    left, head = head_phrase(concept)
    result = []
    for phrase in [concept, *alternatives, left, head]:
        phrase = ' '.join(str(phrase).split())
        if phrase and phrase.casefold() not in {p.casefold() for p in result}:
            result.append(phrase)
    return result[:limit]


# Mass nouns name a material or area rather than one countable thing; plural heads name a class.
# Either way "the X" covers every match, so single_instance would demand a choice the phrase never made.
MASS_NOUNS = {'water', 'sea', 'ocean', 'sky', 'sand', 'grass', 'foliage', 'vegetation', 'greenery', 'snow', 'ice', 'fog',
              'mist', 'haze', 'smoke', 'hair', 'skin', 'ground', 'terrain', 'land', 'road', 'mud', 'dirt', 'soil', 'lava',
              'fire', 'sunlight', 'light', 'background', 'scenery', 'surf', 'foam', 'shoreline', 'coastline', 'rock',
              'stone', 'gravel', 'concrete', 'asphalt', 'pavement', 'architecture', 'masonry', 'brickwork', 'woodland',
              'forest', 'jungle', 'desert', 'cloud cover', 'undergrowth', 'moss', 'sea foam', 'water surface'}
_IRREGULAR_PLURALS = {'people', 'men', 'women', 'children', 'feet', 'teeth', 'mice', 'geese', 'cattle', 'sheep', 'deer', 'fish'}
_NOT_PLURAL = ('ss', 'us', 'is', 'ous', 'sis')


def names_a_class(concept):
    """True for a plural ('rocks', 'boats') or material ('water', 'sky') phrase: it denotes every match.

    'rock' and 'stone' are listed as materials because a rocky area is usually several SAM objects; a
    countable singular subject ('boat', 'person', 'moving yellow boat') stays single_instance.
    """
    left, head = head_phrase(concept)
    head = head.casefold().strip('.,')
    if left.casefold() in MASS_NOUNS or head in MASS_NOUNS or head in _IRREGULAR_PLURALS:
        return True
    return len(head) > 3 and head.endswith('s') and not head.endswith(_NOT_PLURAL)
