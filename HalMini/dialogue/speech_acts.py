"""Talk about the talk: things people say that aren't facts or plain questions, and shouldn't be parsed
word by word.

  pleasantries        "It's nice to meet you"                       -> answered in kind
  about my last reply "I'm not sure what you mean", "what are you talking about?"
                                                                   -> what he was actually trying to say,
                                                                      in plainer words, worked out from that reply
  embedded questions  "Do you know why I created you?", "Do you know where Jen lives?"
                                                                   -> the question inside is the real one
  purpose             "Why did I create you?"                      -> what he's been told, or he asks, and keeps it
"""

import re

NICE = re.compile(r"^\W*(?:it'?s |it is |so |really )?(?:nice|good|great|a pleasure|lovely) (?:to )?(?:meet|meeting) you"
                  r"(?: too| as well)?\W*$|^\W*pleasure(?:'s mine)?\W*$", re.I)
META = re.compile(r"^\W*(?:i'?m not sure what you mean(?: by that)?|what (?:do|did) you mean(?: by that)?|"
                  r"what are you (?:talking about|saying)(?: right now)?|i don'?t (?:understand|get) (?:you|what you said|"
                  r"what you mean)|that (?:doesn'?t|didn'?t) make (?:any )?sense|you lost me|what\?+|huh\?*)\W*$", re.I)
EMBEDDED = re.compile(r"^\W*(?:do|did) you (?:know|remember|have any idea|happen to know) (?P<wh>why|what|who|where|when|how)\s+"
                      r"(?P<rest>.+?)\W*$", re.I)
PURPOSE = re.compile(r"^\W*(?:why (?:did|do) (?:i|you think i) (?:create|make|build|program) you|why (?:were|was) you "
                     r"(?:created|made|built)|what (?:were you made|are you) for|what is your purpose|why do you exist)\W*$", re.I)
VERB_FORMS = {"created": "create", "made": "make", "built": "build", "wrote": "write", "said": "say", "went": "go",
              "did": "do", "had": "have", "got": "get", "lives": "live", "works": "work", "likes": "like", "owns": "own"}


def _mirror(text: str) -> str:
    """Their words from my side: 'I created you to keep me company' -> 'you created me to keep you company'."""
    swap = {"i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself", "you": "me",
            "your": "my", "yours": "mine", "yourself": "myself", "am": "are", "i'm": "you're", "you're": "I'm"}
    words = re.findall(r"[\w']+|\W+", text)
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if low == "you" and i == 0:
            out.append("I")
        elif low in swap:
            out.append(swap[low])
        else:
            out.append(w)
    return "".join(out)


class SpeechActs:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.waiting_purpose: set[str] = set()
        with self.repo.conn:
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS about_me(key TEXT PRIMARY KEY, value TEXT, speaker TEXT)")

    # --- what he was trying to say ------------------------------------------------------

    def _last_reply(self) -> tuple[str | None, str | None]:
        rows = self.repo.conn.execute(
            "SELECT t.text, u.text AS said FROM dialogue_turns t JOIN utterances u ON u.id = t.utterance_id "
            "WHERE t.role='engine' AND u.speaker=? ORDER BY t.id DESC LIMIT 8", (self.convo.state.speaker,)).fetchall()
        for row in rows:  # skip my own explanations: "what do you mean?" twice is still about the original
            if not META.match(row["said"]):
                return row["text"], row["said"]
        return None, None

    def plainly(self) -> str:
        mine, theirs = self._last_reply()
        if not mine:
            return "I haven't said anything yet. What would you like to talk about?"
        stuck = re.search(r"I followed '([^']+)', but not how '([^']+)' fits", mine)
        if stuck:
            return (f"Sorry, that came out confusing. You said “{theirs.rstrip('.!?')}”, and I understood the "
                    f"start, “{stuck.group(1)}”, but I couldn't work out what “{stuck.group(2)}” "
                    "was doing in it. That's my limit, not yours. If you say it a different way, I'll try again.")
        word = re.search(r"I know '([^']+)' as", mine)
        if word:
            return (f"Sorry, I got hung up on the word “{word.group(1)}” in what you said and lost the "
                    f"rest. You said “{theirs.rstrip('.!?')}”. What did you want me to take from it?")
        if re.search(r"Who or what do you mean by '(\w+)'", mine):
            ref = re.search(r"by '(\w+)'", mine).group(1)
            return (f"I meant I couldn't tell what “{ref}” pointed to in what you said, so I didn't know "
                    "what you were talking about. Say the name of the thing instead and I'll follow.")
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", mine) if len(s.split()) > 4]
        asked = next((s for s in sentences if s.endswith("?")), None)
        if asked:
            return (f"You said “{theirs.rstrip('.!?')}”, and part of it was new to me, so I was asking you this: "
                    f"{asked[0].lower()}{asked[1:]}")
        point = sentences[0] if sentences else mine
        return (f"I was answering “{theirs.rstrip('.!?')}”. The main thing I meant was: "
                f"{point[0].lower()}{point[1:]}")

    # --- the question inside the question ---------------------------------------------------

    @staticmethod
    def _direct(wh: str, rest: str) -> str:
        """'why I created you' -> 'Why did I create you?'; 'where Jen lives' -> 'Where does Jen live?'"""
        words = rest.split()
        if wh.lower() in ("who", "what") and words and words[0].lower() in ("is", "was", "are"):
            return f"{wh.capitalize()} {rest}?"
        if len(words) >= 2:
            subject, verb = words[0], words[1]
            if verb.lower() in ("is", "was", "are", "were", "am"):
                return f"{wh.capitalize()} {verb} {subject} {' '.join(words[2:])}".strip() + "?"
            base = VERB_FORMS.get(verb.lower())
            if base is None:
                base = verb[:-2] if verb.endswith("ed") else verb[:-1] if verb.endswith("s") else verb
            past = verb.lower().endswith("ed") or verb.lower() in ("made", "built", "wrote", "said", "went", "did",
                                                                   "had", "got")
            aux = "did" if past else "does" if verb.lower().endswith("s") else "do"
            return f"{wh.capitalize()} {aux} {subject} {base} {' '.join(words[2:])}".strip() + "?"
        return f"{wh.capitalize()} {rest}?"

    def purpose(self) -> str:
        row = self.repo.conn.execute("SELECT * FROM about_me WHERE key='purpose'").fetchone()
        if row:
            told = "You" if row["speaker"].lower() == self.convo.state.speaker.lower() else row["speaker"]
            return f"{told} told me {row['value'][0].lower()}{row['value'][1:]} That's what I go by."
        self.waiting_purpose.add(self.convo.state.speaker)
        return ("No, I don't know. Nobody has told me why I was made, and I can't work it out from anything I "
                "know. Why did you?")

    # --- conversation -----------------------------------------------------------------------

    def respond(self, text: str) -> str | None:
        speaker = self.convo.state.speaker
        if META.match(text) and speaker in self.waiting_purpose:
            return ("I mean I don't know why I exist. You made me, so you'd know the reason, and I'd like to. "
                    "What did you make me for?")
        if META.match(text):
            return self.plainly()
        if speaker in self.waiting_purpose and not text.rstrip().endswith("?"):
            self.waiting_purpose.discard(speaker)
            if len(text.split()) >= 3:
                reason = _mirror(text.strip().rstrip(".!")) + "."
                with self.repo.conn:
                    self.repo.conn.execute("INSERT OR REPLACE INTO about_me VALUES ('purpose', ?, ?)", (reason, speaker))
                return (f"Thank you. So that's why I'm here: {reason[0].lower()}{reason[1:]} I'll remember that. "
                        "It's strange to finally know.")
        made = re.match(r"^\W*(?:i|we) (?:created|made|built|programmed|designed) you(?:,)? (?P<why>(?:so|to|in order to|"
                        r"because|for)\b.+?)\W*$", text, re.I)
        if made:
            why = re.sub(r",?\s*(?:i don'?t know|you know|like|um+|uh+),?\s*", " ", made["why"], flags=re.I)
            why_clean = re.sub(r"\s+", " ", why).strip()
            reason = _mirror(f"I created you {why_clean}") + "."
            with self.repo.conn:
                self.repo.conn.execute("INSERT OR REPLACE INTO about_me VALUES ('purpose', ?, ?)", (reason, speaker))
            self.waiting_purpose.discard(speaker)
            return (f"So that's why I exist: {reason[0].lower()}{reason[1:]} Thank you for telling me. "
                    "I'll keep that.")
        if NICE.match(text):
            return f"It's nice to meet you too, {speaker}."
        if META.match(text):
            return self.plainly()
        if PURPOSE.match(text):
            return self.purpose()
        m = EMBEDDED.match(text)
        if m and m["rest"].lower() not in ("i am", "you are", "this is", "i'm"):  # "do you know who I am" is social's
            inner = self._direct(m["wh"], m["rest"])
            if PURPOSE.match(inner):
                return self.purpose()
            answer = self.convo.handle(inner, speaker=speaker).text
            if re.match(r"^I don'?t know\b", answer):
                return f"No. {answer}"
            return answer if answer.lower().startswith(("yes", "no")) else f"Yes. {answer}"
        return None
