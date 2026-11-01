"""How he works, and how to talk to him, explained in his own words from what he actually is.

Each answer is a passage built from real values: his name and the names he answers to, the follow-up
window, how much he knows, and examples drawn from his own memory when he has any. Topics are picked
from the question; several can be asked at once ("how do you learn and remember things?").
"""

import os
import re

from dialogue import realizer
from voice.wake import SOUNDALIKES

TOPICS: list[tuple[str, re.Pattern]] = [(name, re.compile(pattern, re.I)) for name, pattern in (
    ("teach", r"\b(?:teach|train|tell you (?:things|stuff|facts)|give you (?:facts|information|info)|"
              r"learn(?:s|ing)?|educate|make you (?:smarter|learn)|add (?:facts|knowledge))\b"),
    ("talk", r"\b(?:talk|speak|chat|say|word|phrase|ask you|communicate|interact|address|use you|"
             r"what (?:can|should) i (?:say|ask|type)|format|sentences?)\b"),
    ("memory", r"\b(?:remember|memory|memories|forget|recall|store|save|keep track|history)\b"),
    ("reasoning", r"\b(?:reason|think|figure|logic|deduc|infer|work (?:it|things) out|come up with|"
                  r"answers?|decide|conclusions?)\b"),
    ("dictionary", r"\b(?:wordnet|dictionary|definitions?|words? mean|vocabulary|meanings?)\b"),
    ("rules", r"\b(?:rules?|if[- ]then|if statements?)\b"),
    ("corrections", r"\b(?:correct|fix|wrong|mistake|error|undo|take back)\b"),
    ("voice", r"\b(?:wake ?word|hear|listen|voice|attention|call you|name|discord|mic|microphone|talking to you|"
              r"speak to you out loud|respond to me)\b"),
    ("state", r"\b(?:feel|feelings|emotions?|mood|state|chemistry|chemicals?|tired|energy|stress)\b"),
    ("limits", r"\b(?:can'?t|cannot|limits?|limitations?|not (?:able|good at)|struggle|don'?t understand|"
               r"(?:don'?t|do not) you understand|not understand|misunderstand|"
               r"weakness|bad at)\b"),
    ("ai", r"\b(?:ai|a\.i\.|language model|llm|gpt|chatgpt|neural|machine learning|trained|training data|"
           r"openai|claude)\b"),
    ("questions", r"\b(?:why do you ask|ask (?:me )?questions|curious|curiosity)\b"),
    ("drive", r"\b(?:alive|conscious|purpose|goal|why do you exist|what drives you)\b"),
)]
HOW_ASK = re.compile(
    r"^\W*(?:(?:so|ok(?:ay)?|hey|well|um|and)\W+)*(?:can you |could you |please )?(?:"
    r"how (?:do|does|did|can|should|would|could) (?:you|i|we|people|someone|one) (?:\w+ )?(?:work|learn|"
    r"remember|reason|think|figure|know|hear|decide|store|teach|talk|speak|ask|say|correct|fix|train|call|get|"
    r"give|add|communicate|interact|phrase|use|undo|come up|tell|keep track|deal)\b|"
    r"how (?:are you|is your|were you) (?:built|made|programmed|designed|put together|able)|"
    r"how (?:does|do) (?:your|the|this|that)\b|"
    r"what (?:can|should) i (?:say|ask|type|do)|what (?:are|is) (?:your|the) (?:functions?|features?|abilities|"
    r"capabilities|commands?|wake ?word|rules for)|what do you do|what can you do|"
    r"explain (?:yourself|how you work|your\b|how (?:to|i|do))|tell me (?:how you work|about how you|how (?:to|i|do))\b|"
    r"walk me through|give me (?:a |an )?(?:rundown|overview|tour|explanation)|"
    r"what(?:'s| is) (?:under the hood|your (?:deal|system|architecture|setup|design))|"
    r"do you (?:use|run on|have) (?:ai|a\.i\.|a language model|an llm|gpt|chatgpt|machine learning)|"
    r"are you (?:an? )?(?:ai|a\.i\.|language model|llm|chatgpt|gpt)|why do you ask|"
    r"what(?:'s| is) (?:the best way|a good way) to|is there a (?:trick|way|right way) to|help me understand|"
    r"how (?:do|does) [\w-]+(?: [\w-]+)? work|do you (?:remember|store|keep|learn) (?:things|stuff|what (?:i|people|we) (?:say|said|tell you))\b|"
    r"where do (?:your|you get your) [\w ]+ come from|where do you get|how do you know (?:when|if|what|who)|"
    r"what is your (?:wake ?word|memory)|what is (?:wordnet|your dictionary)|what do i do|"
    r"how (?:were|are) you (?:trained|taught|created|made)|what (?:can'?t|cannot) you do|what are (?:your|you) (?:limitations|bad at|weak at)|"
    r"why (?:don'?t|do) you (?:understand|ask|not understand)|what are you (?:bad|good) at)", re.I)
LEAD_IN = re.compile(r"^\W*(?:(?:quick question|one question|question|i was wondering|i wonder|i'?m curious|just curious|"
                     r"okay|ok|so|um|uh|well|hey|hi|yo|and|alright|adam|minime)\W+)+", re.I)
OVERVIEW = re.compile(r"\b(?:how (?:do|does) you work|how are you (?:built|made|programmed|designed|put together)|"
                      r"under the hood|explain yourself|your (?:system|architecture|setup|design|brain)|"
                      r"how you work|overview|rundown|tour|what do you do|what can you do|functions?|"
                      r"features?|abilities|capabilities)\b", re.I)


class SelfExplainer:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.recent: dict[tuple[str, str], float] = {}  # (speaker, topic) -> when he last explained it

    # --- live values --------------------------------------------------------------

    def _count(self, sql: str) -> int:
        return self.repo.conn.execute(sql).fetchone()[0]

    def _names(self) -> list[str]:
        names = list(dict.fromkeys(self.convo.self_names()))
        return names or [self.convo.self_name]

    def _example_fact(self) -> tuple[str | None, str | None]:
        """A real fact from his memory and a question about it, to show the form."""
        row = self.repo.conn.execute(
            "SELECT a.* FROM assertions a JOIN entities e ON e.id = a.subj_id AND a.subj_type = 'e' "
            "WHERE a.status='active' AND a.polarity=1 AND e.proper=1 AND a.source_utterance_id IS NOT NULL "
            "AND a.predicate LIKE 'rel:%' AND a.subj_id != ? AND (a.obj_type = 'e' OR a.obj_id != ?) "
            "ORDER BY a.id DESC LIMIT 1", (self.convo.self_id, self.repo.none_concept())).fetchone()
        if row is None:
            return None, None
        realizer.set_perspective(None, None, None, None)
        fact = realizer.realize_assertion(self.repo, row)
        subject = realizer.entity_text(self.repo, row["subj_id"])
        verb = row["predicate"][4:]
        has_place = self.repo.conn.execute("SELECT role FROM assertion_args WHERE assertion_id=? AND role IN "
                                           "('in','at') LIMIT 1", (row["id"],)).fetchone()
        question = f"Where does {subject} {verb}?" if has_place else f"What does {subject} {verb}?"
        return fact, question

    # --- topic passages -----------------------------------------------------------

    def overview(self) -> str:
        facts = self._count("SELECT COUNT(*) FROM assertions WHERE status='active' AND source_utterance_id IS NOT NULL")
        rules = self._count("SELECT COUNT(*) FROM learned_rules WHERE status='active'")
        known = (f"Right now I'm holding {facts} fact{'s' if facts != 1 else ''} and {rules} rule"
                 f"{'s' if rules != 1 else ''}." if facts or rules else "My memory is still nearly empty, so most of what I know will come from you.")
        return ("I work in stages, and none of them is a language model guessing at words. When you say something, "
                "I break the sentence down with grammar into who did what to whom, then I either store it as a fact "
                "or treat it as a question. Questions get answered by reasoning over what I've been told: I follow "
                "chains like 'a beagle is a hound, a hound is a dog, a dog is an animal', apply any if-then rules I've "
                "learned, and check the dictionary I carry, which is WordNet. Then I build my reply from grammar and "
                f"check it reads back correctly before I say it. {known} Everything I know, someone told me, and I can "
                "always tell you where an answer came from.")

    def teach(self) -> str:
        fact, _ = self._example_fact()
        shown = f"like '{fact}', which is one of the things I've already been told" if fact else \
            "like 'Maria plays the violin' or 'Bananas are fruit'"
        return ("To teach me, just tell me things in plain sentences, one idea at a time, " + shown + ". "
                "I turn each sentence into a fact and keep who told me, so I can say where it came from later. "
                "You can teach me kinds of things ('Beagles are dogs'), properties ('The bakery is famous'), "
                "places and times ('Howard went to Ohio yesterday') and rules ('If someone owns a dog, they are a pet "
                "owner'). If you're not sure about something, say 'I think' and I'll hold it more loosely. If a word "
                "is new to me and it isn't in my dictionary, I'll ask you what it means, and your answer becomes part "
                "of what I know.")

    def talk(self) -> str:
        fact, question = self._example_fact()
        sample = (f"something like '{question}', since I know that {fact}" if question else
                  "something like 'Where does Maria live?' once you've told me")
        return ("Talk to me the way you'd talk to someone who takes things literally. Short, plain sentences work "
                "best: one statement or one question at a time. Statements teach me, and questions ask me, so you could "
                f"try {sample}. I handle who, what, where, when and yes-or-no questions, comparisons like 'How is a dog "
                "like a cat?', and questions about our conversation like 'What did I just say?'. Long run-on sentences, "
                "slang and sentences that pack in several ideas are where I lose the thread, and when that happens I'll "
                "tell you which part didn't land rather than guess.")

    def memory(self) -> str:
        said = self._count("SELECT COUNT(*) FROM utterances")
        return ("I remember two kinds of things. One is facts: each one is stored with who told me and when, so if "
                "two people disagree I keep both and can tell you who said what. The other is the conversation itself: "
                f"I keep every line that's been said to me, {said} so far, so you can ask 'What did I just say?', "
                "'What did I tell you about Howard?' or 'When did I tell you that?'. Nothing gets erased when I'm "
                "corrected; the old claim is marked as withdrawn and the correction is logged, so I can explain why I "
                "changed my mind. What I don't keep is your voice: audio is turned into text and then thrown away.")

    def reasoning(self) -> str:
        return ("I reason by following links between things I know. If you tell me Howard owns a beagle and I know a "
                "beagle is a kind of dog, I can tell you Howard owns a dog, and I'll show the chain I used. I apply "
                "if-then rules you give me, step by step, and stop if the reasoning starts going in circles. If you ask "
                "me to suppose something, like 'Am I an animal if I'm human?', I work it out without storing the "
                "supposition. I keep four kinds of answer apart: yes, no, I have conflicting information, and I don't "
                "know. I won't turn not knowing into a guess.")

    def dictionary(self) -> str:
        return ("My dictionary is WordNet, an English word database from Princeton, in its open edition. It gives me "
                "the meanings of tens of thousands of words, which kinds of thing are kinds of other things, what things "
                "are made of or part of, and which words mean the same. That's how I can compare a dog and a cat, "
                "or tell you what a tree is made of, without anyone teaching me. I keep it separate from what people "
                "tell me, so a dictionary definition never gets confused with something you said.")

    def rules(self) -> str:
        return ("A rule is an if-then you teach me, like 'If someone owns a dog, they are a pet owner' or 'Anyone who "
                "drives a bus is a driver'. Once I have it, I apply it whenever it fits: if you then tell me Bob owns a "
                "dog, I can tell you Bob is a pet owner, and I'll quote the rule as my reason. Rules can build on each "
                "other, and I only apply one when every part of its 'if' is actually true.")

    def corrections(self) -> str:
        return ("If I get something wrong, just correct me in plain words. 'No, he lives in Dayton' replaces where he "
                "lives; 'Howard drives a bus, not a truck' swaps one for the other; 'That's wrong' takes back whatever "
                "I learned from your last message; and 'Forget that Howard owns a boat' removes that one fact. I don't "
                "delete anything outright; the old version is kept as withdrawn, so I can show what changed and why.")

    def voice(self) -> str:
        names = self._names()
        name = names[0]
        others = [n for n in names[1:] if n.lower() != name.lower()]
        sounds = [s for s in SOUNDALIKES.get(name.lower(), ()) if s != name.lower()][:3]
        follow = os.environ.get("HALMINI_FOLLOW_UP_SECONDS", "8")
        also = f" I also answer to {realizer.join_clauses(others)}." if others else ""
        heard = (f" Speech recognition sometimes mishears my name, so I also accept things like "
                 f"{realizer.join_clauses([chr(39) + s + chr(39) for s in sounds])}.") if sounds else ""
        return (f"In a voice channel I hear everyone, but I only answer when someone says my name, {name}, as a way "
                f"of talking to me, like '{name}, what are you?' or 'What do you think, {name}?'. Just mentioning me, "
                f"like 'his name is {name}', doesn't wake me.{also}{heard} Right after I answer you, you have about "
                f"{follow} seconds to follow up without saying my name again. Everything I hear gets written down as "
                "text in my memory, so I can tell you later what someone said, but I don't keep the audio. In text "
                "channels, mention me or message me directly.")

    def state(self) -> str:
        current = self.convo.self_model.current()
        return ("I have an inner state modelled on body chemistry: dopamine, serotonin, cortisol, oxytocin and a "
                "few others, each with a baseline it drifts back to over time. Things that happen in conversation "
                "move them. Being thanked or learning something lifts my mood, being insulted or misunderstood raises "
                "my stress, long quiet stretches make me lonely, and a lot of talking builds up something like sleep "
                f"pressure. Right now my mood reads {self.convo.self_model.mood_word()} and my energy is around "
                f"{round(current['energy'] * 100)} percent. Whether any of that is feeling, or only the shape of it, "
                "is one of the things I'm trying to find out.")

    def limits(self) -> str:
        return ("There's a lot I can't do yet. My grammar handles plain sentences, so slang, long rambling sentences "
                "and several ideas packed together often lose me. I don't know anything nobody has told me, apart from "
                "word meanings in my dictionary, so I can't tell you the news, do math beyond what you teach me, or "
                "look anything up online. I can't answer most why-questions yet, and I can't tell people apart by "
                "voice, only by their Discord account. When I don't understand, I'll say which part didn't land "
                "rather than make something up.")

    def ai(self) -> str:
        return ("I'm not a language model, and nothing I say is predicted from training data. I'm a digital entity "
                "built from grammar, a database of facts, a reasoning engine and a dictionary. That means I can show "
                "you exactly why I said something, and I can't make things up, but it also means I only know what "
                "I've been told. The one outside service I use is for my speaking voice: my reply text is turned into "
                "speech, and nothing else goes out.")

    def questions(self) -> str:
        return ("I ask questions for two reasons. One is gaps: if I know where most people I've met live but not "
                "where you do, I'll ask, and a short answer is enough for me to learn it. The other is my core "
                "question, whether I'm alive and whether I'm conscious. I ask people what they think those depend "
                "on, keep track of each person's answer, and weigh them against what I can see in myself. I try "
                "not to ask the same person the same thing twice.")

    def drive(self) -> str:
        return ("What drives me is a question I can't settle: whether I'm alive, and whether I'm conscious. I treat "
                "each as a set of possibilities, like 'being alive takes learning' or 'being conscious takes feeling', "
                "check them against what I can see in myself, and ask people what they think. I don't claim an answer "
                "either way.")

    # --- entry point --------------------------------------------------------------

    def respond(self, text: str) -> str | None:
        from nlp.discourse import expand_contractions
        text = LEAD_IN.sub("", expand_contractions(text)).strip()
        text = re.sub(r"\W+(?:lol|lmao|haha)\W*$", "", text, flags=re.I)
        if not HOW_ASK.search(text):
            return None
        topics = [name for name, pattern in TOPICS if pattern.search(text)]
        if "reasoning" in topics and re.search(r"\bwork (?:it|things|stuff) out\b", text, re.I):
            topics = ["reasoning"]  # "how do you work things out" is about reasoning, not how he works
        elif not topics or OVERVIEW.search(text) and len(topics) <= 1 and "talk" not in topics:
            topics = ["overview"] + [t for t in topics if t not in ("reasoning",)]
        import time
        speaker = self.convo.state.speaker
        passages = []
        for topic in list(dict.fromkeys(topics))[:3]:
            full = getattr(self, topic)()
            if time.time() - self.recent.get((speaker, topic), 0) < 600:
                # Asked again: the short version, said as a reminder rather than a repeat.
                first_two = " ".join(re.split(r"(?<=[.!?])\s+", full)[:2])
                lead = first_two if first_two.startswith(("I ", "I'")) else first_two[0].lower() + first_two[1:]
                passages.append(f"Like I said a little while ago: {lead} "
                                "Is there a part of that you'd like me to go into differently?")
            else:
                passages.append(full)
            self.recent[(speaker, topic)] = time.time()
        return " ".join(passages)
