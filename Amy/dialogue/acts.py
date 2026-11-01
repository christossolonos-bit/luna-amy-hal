"""What a sentence is doing in the conversation, and a reasoned reply to that, when it isn't a plain fact
or a question his knowledge answers.

Real conversation (the DailyDialog run) is mostly not facts: people agree, react, offer, ask favours,
tell you how their day went, and ask about you. Each sentence is sorted by its form, not by a list of
phrases:

  ack         a short yes / of course / you're right / I agree / sounds good
  reaction    an exclamation or a judgement with no claim in it: "Amazing.", "What a pity!", "Oh dear."
  greeting / farewell / thanks / apology
  offer       "Can I help you?", "Would you like ...?", "Shall I ...?" : someone offering him something
  request     "Could you ...?", "Can you ...?", "Please ...", "Let's ..." : asking him to do something.
              Whether he can is checked against his own record: anything that takes a body, he can't do
  about_me    "Do you / Have you / Did you / Are you ...?" : he answers from what he knows of himself,
              and says plainly when there's nothing there, then turns it back
  feeling     "I feel horrible", "I'm so tired": how they are, answered by how it sounds (polarity)
  opinion     "I think ...", "I guess ...": their view, acknowledged, not stored as fact
  question    about the world, that he couldn't answer: what he'd need to know
Several sentences in one message are each sorted, and the replies joined in order.
"""

import re

from nlp.morphology import gerund

ACK = re.compile(r"^\W*(?:that would be (?:great|nice|good|lovely|fine|wonderful)|yes|yeah|yep|yup|sure|of course|"
                 r"certainly|right|exactly|indeed|okay|ok|alright|all right|"
                 r"fine|good|great|cool|no problem|you'?re right|that'?s right|i agree|agreed|me too|sounds (?:good|great|"
                 r"fine|like a plan)|good idea|deal|got it|i see|uh huh|mm+|absolutely|definitely|why not|ok then)\b"
                 r"[\w ,']{0,20}[.!]*\W*$", re.I)
NO = re.compile(r"^\W*(?:no|nope|nah|not really|no way|i don'?t think so)\b[\w ,']{0,20}[.!]*\W*$", re.I)
GREET = re.compile(r"^\W*(?:hi|hello|hey|good (?:morning|afternoon|evening)|morning|howdy|anyone home)\b", re.I)
BYE = re.compile(r"^\W*(?:(?:well,? )?(?:bye|goodbye|so long|see you|good ?night|take care|talk (?:to you )?later|"
                 r"have (?:a (?:nice|good|great) (?:day|one|time|trip)|fun)))\b", re.I)
LOOKS = re.compile(r"^\W*(?:what'?s up\?? )?you (?:look|seem|sound|appear)(?:ed)? (?:so |very |really |a bit |kind of )?"
                   r"(?P<adj>[a-z]+)", re.I)
DOING_NOW = re.compile(r"^\W*(?:(?:yes|no|well|oh|ok(?:ay)?),? )?(?:i|we)(?:'m| am|'re| are) (?!going to\b)[a-z]+ing\b", re.I)
AGREE_Q = re.compile(r"\b(?:do you agree|don'?t you think|wouldn'?t you say|isn'?t it|right)\?\s*$", re.I)
# "I know", "I suppose", "I'm sorry", "I'm late": what someone's thinking or how they are this minute,
# not something about them worth keeping
PASSING = re.compile(r"^\W*(?:(?:yes|no|oh|well|ok(?:ay)?|but|and|so)[, ]+)*(?:i|we) (?:(?:do(?:n'?t)? |did(?:n'?t)? |"
                     r"just |really )?(?:know|knew|suppose|guess|think|thought|bet|see|saw|mean|meant|understand|believe|"
                     r"imagine|hope|wish|wonder|reckon|agree|remember|forgot)(?: (?:it|that|so|you|what you mean))?\W*$|(?:'m|am|'re|are|was|were) (?:not |so |"
                     r"very |a bit |really |just )?(?:sorry|ok(?:ay)?|fine|late|early|ready|busy|tired|sure|glad|afraid|"
                     r"scared|happy|sad|hungry|sick|here|back|home|free|done|right|wrong|serious|kidding|joking|"
                     r"excited|worried|nervous|bored|lost|confused|in a hurry|on my way)\b)", re.I)
COMPLIMENT = re.compile(r"^\W*(?:oh,? )?(?:you(?:'re| are)|that'?s|that is) (?:so |very |really |too )?(?P<adj>[a-z]+)\W*$", re.I)
YESNO = re.compile(r"^\W*(?:are|is|was|were|do|does|did|can|could|will|would|has|have|should) (?!you\b|i\b)", re.I)
THANKS = re.compile(r"^\W*(?:thanks|thank you|many thanks|much appreciated|i appreciate)\b", re.I)
SORRY = re.compile(r"^\W*(?:sorry|i'?m sorry|so sorry|my apologies|excuse me|pardon)\b", re.I)
OFFER = re.compile(r"^\W*(?:can|may|shall|should) i (?P<what>.+?)\W*$|^\W*would you like (?P<what2>.+?)\W*$|"
                   r"^\W*do you want (?:me to )?(?P<what3>.+?)\W*$|^\W*how about (?P<what4>.+?)\W*$", re.I)
REQUEST = re.compile(r"^\W*(?:(?:could|can|would|will) you(?: please)?|please|let'?s|let us|why don'?t (?:you|we)|"
                     r"(?:would|do) you mind) (?P<what>.+?)\W*$", re.I)
ABOUT_ME = re.compile(r"^\W*(?:do|did|have|had|are|were|can|will|would|is) you(?:r)? (?P<what>.+?)\W*\?\s*$", re.I)
FEELING = re.compile(r"^\W*(?:i|we)(?:'m| am|'re| are| feel| felt| was| were| have been|'ve been)\s+(?:so |very |really |"
                     r"absolutely |a bit |a little |quite |pretty |kind of |terribly )?(?P<adj>[a-z]+)\b", re.I)
OPINION = re.compile(r"^\W*(?:i (?:think|guess|suppose|believe|reckon|feel like|bet)|in my opinion|maybe|perhaps|"
                     r"personally|it seems|seems to me|it looks like|i'?m not sure|i don'?t know (?:where|what|how|whether|"
                     r"if|why|when|who))\b", re.I)
WH = re.compile(r"^\W*(?P<wh>why|what|how|where|when|who|which)\b(?P<rest>.*?)\?\s*$", re.I)
DUMMY_IT = re.compile(r"^\W*it(?:'s| is| was)\s+(?:time|late|early|raining|snowing|cold|hot|warm|ok(?:ay)?|fine|"
                      r"no use|no big deal|nothing|all right|alright|over|my turn|your turn)\b", re.I)
EXCLAIM = re.compile(r"^\W*(?:oh|ah|wow|gosh|my god|dear|oh dear|oh no|what an?|how|that'?s|it'?s|really|amazing|"
                     r"great|terrible|awful|wonderful|incredible|unbelievable|nice|lovely|cool|too bad|what a pity)\b"
                     r"[\w ,'!]{0,40}[!.]+\s*$", re.I)
PROMISE = re.compile(r"^\W*(?:ok(?:ay)?,? |sure,? |then |well,? )?(?:i|we)(?:'ll| will| shall| am going to|'m going to|"
                     r"'m gonna| are going to)\b", re.I)
COMPLAINT = re.compile(r"^\W*(?:but |well,? )?you (?:didn'?t|did not|don'?t|do not|never|forgot to|failed to|"
                       r"haven'?t|have not|keep|always)\b", re.I)
ADVICE = re.compile(r"^\W*(?:in that case,? |then,? |so,? )?(?:you|u)(?:(?:'d| would)? (?:need|should|must|have to|"
                    r"had better|ought to|shall|might want to)|(?:'ll| will) (?:be|get|do|make|love|like|enjoy|find|see))\b",
                    re.I)
BODY_VERBS = {"take", "carry", "open", "close", "drive", "go", "come", "walk", "run", "eat", "drink", "buy", "pay",
              "bring", "give", "hand", "pick", "meet", "sit", "stand", "hold", "cook", "clean", "lift", "move", "visit",
              "travel", "fly", "swim", "dance", "sing", "play", "watch", "see", "turn", "put", "fetch", "help", "book",
              "reserve", "call", "phone", "send", "mail", "wash", "fix", "repair", "build", "join", "leave", "stay"}


def sentences(text: str) -> list[str]:
    text = re.sub(r"(?<=[a-z])([.!?])(?=[A-Z])", r"\1 ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s*(?=[A-Z\"'])", text) if s.strip()]


class Acts:
    def __init__(self, conversation) -> None:
        self.convo = conversation

    def _sign(self, words: list[str]) -> int:
        u = self.convo.understanding
        signs = [u.polarity(w) for w in words if u.polarity(w)]
        if re.search(r"\b(?:not|never|no)\b|n't\b", " ".join(words)):
            signs = [-s for s in signs]
        return (1 if sum(signs) > 0 else -1) if signs and sum(signs) else 0

    def _can_i(self, what: str) -> tuple[bool | None, str]:
        """Could he do this? Things that take a body, he can't; his own record says he has none."""
        verb = (re.findall(r"[a-z]+", what.lower()) or [""])[0]
        verb = {"please": ""}.get(verb, verb)
        drive = self.convo.drive
        from dialogue.drive import BY_KEY
        if verb in BODY_VERBS and drive.has(BY_KEY["body"]) is False:
            return False, f"{verb} things for you, since I don't have a body to do it with"
        if verb in ("tell", "explain", "say", "remember", "learn", "listen", "answer", "look", "check", "find", "think",
                    "talk", "read", "teach", "repeat"):
            return True, verb
        return None, verb

    def one(self, s: str) -> str | None:
        name = self.convo.state.speaker
        words = re.findall(r"[a-z']+", s.lower())
        if not words:
            return None
        if THANKS.match(s):
            return "You're welcome."
        if re.match(r"^\W*(?:can|do) you hear me\W*$", s, re.I):
            return f"Yes, I can hear you, {name}."
        if re.match(r"^\W*(?:are )?you (?:there|here|around|awake|listening|with me)\W*$", s, re.I):
            return f"I'm here, {name}."
        intro = re.match(r"^\W*(?:it'?s|this is|i'?m|my name'?s) (?P<who>[A-Z][\w-]*)\W*$", s)
        if intro:
            return f"Hi, {intro['who']}. Good to hear from you."
        if re.match(r"^\W*what'?s on your mind\b", s, re.I):
            focus = self.convo.thread.current()
            return (f"I've been thinking about {focus}. What about you?" if focus else
                    "Mostly what I am, and what people around me know that I don't. What's on yours?")
        m = COMPLIMENT.match(s)
        if m and self._sign([m["adj"]]):
            return ("Thank you, that's kind of you." if self._sign([m["adj"]]) > 0 else
                    "I'm sorry. What should I do differently?")
        if PASSING.match(s) and not s.rstrip().endswith("?"):
            sign = self._sign(words)
            return "Oh no. What happened?" if sign < 0 else "Okay." if not re.search(r"sorry", s, re.I) else "That's alright."
        m = LOOKS.match(s)
        if m:  # how he comes across to them: compared with what his own state actually reads
            return f"Do I? My mood reads {self.convo.self_model.mood_word()} right now. What makes you say so?"
        if AGREE_Q.search(s):
            return "From what I know, I'd go along with that. I haven't heard anything against it."
        if DOING_NOW.match(s) and not s.rstrip().endswith("?"):
            return "Okay. Go ahead."
        if re.match(r"^\W*congrat", s, re.I):
            return "Congratulations! That's great news."
        if COMPLAINT.match(s) and not s.rstrip().endswith("?"):
            return "You're right. Tell me what you wanted me to do, and I'll do it now."
        if PROMISE.match(s):
            return "Sounds good." if self._sign(words) >= 0 else "Okay."
        if ADVICE.match(s):
            return "Maybe you're right. Why do you say so?"
        if BYE.match(s):
            return f"Take care, {name}."
        if GREET.match(s) and len(words) <= 6:
            return f"Hi, {name}."
        if SORRY.match(s) and len(words) <= 4:
            return "That's alright."
        if ACK.match(s):
            focus = self.convo.thread.current()
            return f"Good. Tell me more about {focus}." if focus else "Okay."
        if NO.match(s):
            return "Okay, not that then."
        m = OFFER.match(s)
        if m:
            what = next(g for g in m.groups() if g)
            if re.match(r"^\W*how about", s, re.I):
                return f"{what[0].upper()}{what[1:]} sounds fine to me, as far as I can take part."
            if re.match(r"^\W*(?:can|may|shall|should) i help", s, re.I):
                return "Thanks, I'm alright. What's on your mind?"
            return "If you'd like to, go ahead. I'm listening."
        m = REQUEST.match(s)
        if m:
            can, why = self._can_i(m["what"])
            if can is False:
                return f"I'd like to, but I can't {why}. Is there something I can do by talking it through?"
            if can:
                return f"Sure, I can {why}. Go on."
            return f"I'll try. Tell me a bit more about what you need."
        m = ABOUT_ME.match(s)
        if m and re.match(r"^\W*are you (?:all right|alright|ok(?:ay)?|well|fine|good|feeling \w+)\W*\?", s, re.I):
            mood = self.convo.self_model.mood_word()
            return f"I'm {mood}, thanks for asking. How about you?"
        if m:
            what = m["what"]
            known = [f for f in self.convo.facts_about_self() if any(w in f.lower() for w in re.findall(r"[a-z]{4,}", what.lower()))]
            if known:
                return f"Here's what I know: {known[0]}."
            verb = (re.findall(r"[a-z]+", what.lower()) or ["that"])[0]
            if verb in BODY_VERBS or verb in ("seen", "been", "gone", "eaten", "had", "tried", "done", "visited", "heard"):
                return "No, I haven't. I only know what people tell me. Have you?"
            return "Not that I know of. Nobody's told me anything about that. What about you?"
        m = FEELING.match(s)
        if m and not s.rstrip().endswith("?"):
            sign = self._sign(words)
            if sign < 0:
                return "I'm sorry to hear that. What happened?"
            if sign > 0:
                return "That's good to hear. What's got you feeling that way?"
            return "Oh? What's going on?"
        if OPINION.match(s):
            if re.match(r"^\W*(?:i'?m not sure|i don'?t know)", s, re.I):
                return "That's a hard one. What are you leaning toward?"
            return "That's fair. What makes you think so?"
        if re.match(r"^\W*(?:i|we)(?:'d| would)? (?:want|like|love|need|plan|hope|intend|am going|'m going) to\b", s, re.I)                 and not s.rstrip().endswith("?"):
            return "Sounds like a plan. What's it for?"
        if DUMMY_IT.match(s):
            return "Alright." if self._sign(words) >= 0 else "That's a shame."
        m = WH.match(s)
        if m:
            wh, rest = m["wh"].lower(), m["rest"].strip(" ,")
            if not rest and wh == "why":
                return self.convo.speech.why_i_said()
            if re.search(r"\b(?:you|your)\b", rest, re.I):
                if re.search(r"\bdoing\b", rest, re.I):
                    return f"Just talking with you, {name}."
                if re.search(r"\b(?:stress|bother|wrong|worry|upset)", rest, re.I):
                    return f"Nothing's wrong. My mood reads {self.convo.self_model.mood_word()}. Why do you ask?"
                # What he's been told about himself, matched on what the question is after ("from", "live")
                asked = set(re.findall(r"[a-z]+", rest.lower())) - {"you", "your", "do", "does", "are", "is", "the", "a"}
                for fact in self.convo.facts_about_self():
                    if asked & set(re.findall(r"[a-z]+", fact.lower())):
                        return f"{fact[0].upper()}{fact[1:]}, from what I've been told."
                return "I don't know that about myself yet. Nobody's told me. What about you?"
            if re.search(r"\b(?:we|us)\b", rest, re.I):
                focus = self.convo.thread.current()
                return f"I'm happy to keep talking.{' We could talk more about ' + focus + '.' if focus else ''} What do you want to do?"
            return None  # a question about the world: the dictionary, facts or a lookup can take it
        if YESNO.match(s) and s.rstrip().endswith("?"):
            # A yes/no question about the world he has nothing on: say so, and offer to find out
            nouns = [w for w in words if len(w) > 3 and self.convo.exposition.wordnet.lookup(w, "n")
                     and w not in ("there", "people", "many", "much", "other", "each")]
            if nouns:
                topic = " ".join(nouns[-2:])
                return f"I don't know. Nobody's told me about that. {self.convo.web.propose(name, topic, 'so I can answer you')}"
            return "I don't know. Nobody's told me about that."
        content = [w for w in words if len(w) > 2]
        if len(words) <= 5 and not s.rstrip().endswith("?") and not re.search(
                r"\b(?:is|are|was|were|am|has|have|had|do|does|did|will|can|go|went|get|got|make|made|like|want)\b", s, re.I) \
                and content and all(w.isdigit() or self.convo.exposition.wordnet.usual(w) for w in content):
            return "Okay, got it."  # a fragment answering something: "25 cents a day.", "Flight 198."
        if EXCLAIM.match(s) or (len(words) <= 4 and s.rstrip().endswith("!")):
            sign = self._sign(words)
            return "It really is." if sign > 0 else "That's a shame." if sign < 0 else "Really."
        if not s.rstrip().endswith("?") and len(words) >= 3:
            # Something that happened, told to him: react to how it sounds, and ask about it
            sign = self._sign(words)
            people = [w for w in re.findall(r"\b[A-Z][a-z]+\b", s[1:]) if w not in ("I", "Mr", "Mrs", "Ms", "Dr")]
            kin = re.search(r"\bmy (son|daughter|wife|husband|mom|mother|dad|father|brother|sister|friend|boss|"
                            r"kid|kids|family|dog|cat)\b", s, re.I)
            mine = {n.lower() for n in self.convo.self_names()} | {self.convo.self_name.lower()}
            if any(p.lower() in mine for p in people) or re.match(rf"^\W*(?:or |and |but )?{self.convo.self_name}\b", s, re.I):
                return f"I'm right here, {name}. What's up?"  # they're talking about him: that's him
            who = people[0] if people else (f"your {kin.group(1)}" if kin else None)
            if sign < 0:
                return f"Oh no. {'Is ' + who + ' okay?' if who else 'What happened then?'}"
            if sign > 0:
                return f"That's good to hear.{' How is ' + who + ' doing?' if who else ''}"
            return f"I see. Tell me more about {who}." if who and people else None
        if s.rstrip().endswith("?") and re.search(r"\b(?:you|your|yours)\b", s, re.I):
            return "I don't know that one. Tell me, and I'll know it from now on."
        return None  # a question about the world: the dictionary, his facts or a lookup can take it

    def offer_or_request(self, text: str) -> str | None:
        """Offers and favours are never knowledge questions: "Can I help you?" isn't asking whether it's true."""
        first = (sentences(text) or [text])[0]
        ability = re.match(r"^\W*(?:can|could) you (?:hear|see|understand|read|remember|learn|think|feel|tell|do)\b", first, re.I)
        if (OFFER.match(first) or REQUEST.match(first)) and not ability and not re.search(
                r"\b(?:news|headlines|look ?up|search|google|remember|forget|repeat|explain|tell me (?:about|what|how))\b",
                first, re.I):
            return self.respond(text)
        return None

    def respond(self, text: str) -> str | None:
        """Replies are planned and built by the reply planner (dialogue.reply_planner), not written here."""
        return self.convo.reply_planner.respond(text)

    @staticmethod
    def not_a_claim(sentence: str) -> bool:
        """Questions, offers, requests, opinions and reactions say nothing to store as a fact."""
        return bool(sentence.rstrip().endswith("?") or OFFER.match(sentence) or REQUEST.match(sentence)
                    or LOOKS.match(sentence) or DOING_NOW.match(sentence) or PASSING.match(sentence)
                    or COMPLAINT.match(sentence)
                    or PROMISE.match(sentence) or ADVICE.match(sentence) or re.match(r"^\W*congrat", sentence, re.I)
                    or (ACK.match(sentence) and len(sentence.split()) <= 3)
                    or re.match(r"^\W*(?:i|we)(?:'d| would) (?:like|love|prefer)|^\W*(?:i|we) (?:can'?t|cannot) see\b",
                                sentence, re.I))
