"""Closed-class words, a seed verb list, and a Lexicon that also knows verbs learned from conversation."""

from collections.abc import Iterable

from nlp import morphology
from nlp.morphology import lemmatize_noun  # re-exported for callers

__all__ = ["Lexicon", "lemmatize_noun", "article"]

DETERMINERS = {"a", "an", "the", "some", "any", "every", "all", "each", "this", "that",
               "these", "those", "another"}
GENERIC_DETS = {"every", "all", "each"}
DEFINITE_DETS = {"the", "this", "that", "these", "those"}
NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "several", "many", "few"}
POSSESSIVE_DETS = {"my": "i", "his": "he", "her": "she", "its": "it", "their": "they",
                   "our": "we", "your": "you"}
POSSESSIVE_OF = {v: k for k, v in POSSESSIVE_DETS.items()}
SUBJECT_PRONOUNS = {"i", "you", "he", "she", "it", "they", "we"}
OBJECT_PRONOUNS = {"me": "i", "you": "you", "him": "he", "her": "she", "it": "it",
                   "them": "they", "us": "we"}

BE = {"is": ("present", "sg"), "are": ("present", "pl"), "am": ("present", "sg"),
      "was": ("past", "sg"), "were": ("past", "pl")}
NEG_BE = {"isn't": ("present", "sg"), "aren't": ("present", "pl"),
          "wasn't": ("past", "sg"), "weren't": ("past", "pl")}
DO = {"does": ("present", "sg"), "do": ("present", "pl"), "did": ("past", None)}
NEG_DO = {"doesn't": ("present", "sg"), "don't": ("present", "pl"), "didn't": ("past", None)}
MODALS = {"can", "could", "will", "would", "should", "might", "must"}
NEG_MODALS = {"can't": "can", "cannot": "can", "won't": "will", "couldn't": "could",
              "shouldn't": "should", "wouldn't": "would"}
HAVE_FORMS = {"has": "present", "have": "present", "had": "past"}
AUX_WORDS = set(BE) | set(NEG_BE) | set(DO) | set(NEG_DO) | MODALS | set(NEG_MODALS) | set(HAVE_FORMS)

NEGATORS = {"not", "never"}
PREPOSITIONS = {"to", "in", "at", "on", "from", "with", "for", "near", "into", "by", "under",
                "over", "about", "behind", "inside", "outside", "across", "through", "after",
                "before", "during", "without", "around", "beside", "between", "onto", "of",
                "like", "against", "toward", "towards"}
CONJUNCTIONS = {"and", "but"}
CLAUSE_LINKS = {"because", "so", "then", "while", "although", "though"}
RELATIVES = {"who", "which", "that"}
WH = {"what", "who", "where", "when"}
QUANTIFIERS = {"someone", "somebody", "something", "anyone", "anybody", "anything",
               "everyone", "everybody", "everything"}
PERSON_QUANTIFIERS = {"someone", "somebody", "anyone", "anybody", "everyone", "everybody"}
RULE_STARTERS = {"if", "whenever", "when"}

ADVERBS = {"really", "also", "too", "just", "still", "only", "even", "usually", "always",
           "often", "sometimes", "definitely", "certainly", "actually", "basically", "totally",
           "very", "quite", "almost", "already", "again", "together", "here", "there"}
LY_NOT_ADVERBS = {"family", "fly", "ally", "belly", "jelly", "rally", "reply", "apply", "supply",
                  "italy", "july", "holy", "ugly", "silly", "friendly", "lovely", "lonely",
                  "early", "daily", "curly", "chilly", "lily", "bully"}

TIME_WORDS = {"yesterday", "today", "tomorrow", "tonight", "now", "recently", "lately",
              "earlier", "later", "currently"}
TIME_LEAD = {"last", "next", "this", "every"}
DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
MONTHS = {"january", "february", "march", "april", "june", "july", "august", "september",
          "october", "november", "december"}
TIME_UNITS = {"week", "month", "year", "night", "morning", "evening", "afternoon", "weekend",
              "day", "summer", "winter", "spring", "autumn", "decade", "century"} | DAYS | MONTHS

BOUNDARY = AUX_WORDS | PREPOSITIONS | CONJUNCTIONS | CLAUSE_LINKS | RELATIVES | NEGATORS | {",", "if"}

SEED_VERBS = set("""
own like love hate want need eat drink drive ride live work play read write watch see know think
believe make build buy sell give take bring carry use visit meet help teach learn study speak talk
say tell ask chase catch hunt fly swim run walk climb jump sleep wake cook bake clean wash fix
repair paint sing dance laugh cry listen hear feel hold open close start stop begin finish win
lose find keep leave enter join follow lead move travel go come arrive return stay wait sit stand
grow plant feed pet adopt rescue protect attack bite kill save pay spend earn borrow lend send
receive collect create invent discover explore enjoy prefer remember forget understand trust
respect marry include contain produce chew breathe smell weigh hide steal shoot throw kick hit
push pull drop lift wear serve manage operate control install sail fear guard support fight
call answer bark meow fetch purr hug kiss miss date hire fire employ lay hatch migrate
""".split())


def article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


class Lexicon:
    def __init__(self, learned_verbs: Iterable[str] = (), proper_form=None) -> None:
        self.verbs = SEED_VERBS | set(morphology.IRREGULAR_VERBS) | set(learned_verbs)
        self.proper_form = proper_form or (lambda word: None)  # 'texas' -> 'Texas' (from WordNet)

    def verb(self, form: str, allow_unknown: bool = False) -> tuple[str, str] | None:
        """(lemma, tense) if `form` is a verb this lexicon knows, else a guess or None."""
        if not form or not form.replace("-", "").isalpha():
            return None
        for lemma, tense in morphology.analyses(form):
            if lemma in self.verbs:
                return lemma, tense
        return morphology.guess(form) if allow_unknown else None

    def inflected_verb(self, form: str) -> bool:
        """True for forms that can only be verbs here: 'chases', 'drove' (not bare 'watch')."""
        found = self.verb(form)
        return found is not None and found[0] != form
