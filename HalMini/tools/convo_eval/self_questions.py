"""500 conversations of people asking how he works and how to talk to him, with checks on the answers."""

import random

PHRASINGS = {
    "overview": ["How do you work?", "How are you built?", "What's under the hood?", "Explain yourself.",
                 "Give me a rundown of how you work.", "Walk me through how you work.", "What can you do?",
                 "What are your functions?", "How does your brain work?", "What are your capabilities?"],
    "teach": ["How do I teach you?", "How can I teach you things?", "How do you learn?", "How do I train you?",
              "How should I give you information?", "How do I add facts to you?", "How does learning work for you?",
              "What's the best way to teach you something?"],
    "talk": ["How do I talk to you?", "How should I speak to you?", "What can I say to you?",
             "How do I ask you things?", "What should I type?", "How do I communicate with you?",
             "Is there a right way to talk to you?", "What's a good way to phrase things for you?"],
    "memory": ["How does your memory work?", "How do you remember things?", "Do you remember what I say? How?",
               "How do you keep track of conversations?", "How do you store things?"],
    "reasoning": ["How do you reason?", "How do you figure things out?", "How do you come up with answers?",
                  "How do you think?", "How do you work things out?"],
    "dictionary": ["How do you know what words mean?", "What is WordNet?", "Where do your definitions come from?",
                   "How does your dictionary work?"],
    "rules": ["How do rules work?", "How do I teach you a rule?", "How do if-then rules work for you?"],
    "corrections": ["How do I correct you?", "What do I do if you get something wrong?", "How do I fix a mistake you made?",
                    "How can I undo something I told you?"],
    "voice": ["How do I get your attention?", "What's your wake word?", "How does voice work for you?",
              "How do I call you?", "How do you know when I'm talking to you?", "How do you hear me?"],
    "state": ["How do your feelings work?", "How does your mood work?", "How does your state work?",
              "How do your emotions work?"],
    "limits": ["What can't you do?", "What are your limitations?", "Why don't you understand me sometimes?",
               "What are you bad at?"],
    "ai": ["Are you an AI?", "Do you use ChatGPT?", "Are you a language model?", "Do you use machine learning?",
           "How were you trained?"],
    "questions": ["Why do you ask questions?", "Why do you ask me so many questions?"],
}
KEYWORDS = {
    "overview": ("grammar", "reason"), "teach": ("sentence", "fact"), "talk": ("sentence", "question"),
    "memory": ("told", "remember"), "reasoning": ("rule", "chain"), "dictionary": ("WordNet",),
    "rules": ("if",), "corrections": ("wrong",), "voice": ("name",), "state": ("mood",),
    "limits": ("can't",), "ai": ("language model",), "questions": ("ask",),
}
LEADS = ["", "", "", "So ", "Okay, ", "Hey Adam, ", "Adam, ", "Um, ", "Quick question: ", "I was wondering, "]
SPEAKERS = ["Travis", "Dana", "Chris", "Priya", "Luis", "Grace"]


def noisy(text: str, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.1:
        return text.lower()
    if roll < 0.15:
        return text.rstrip("?.") + " lol"
    if roll < 0.2:
        return text.rstrip("?.!")
    return text


def generate(count: int, rng: random.Random) -> list:
    conversations = []
    topics = list(PHRASINGS)
    for i in range(count):
        speaker = rng.choice(SPEAKERS)
        turns = []
        for topic in rng.sample(topics, rng.randint(1, 3)):
            question = rng.choice(LEADS) + rng.choice(PHRASINGS[topic])
            question = question[:1].upper() + question[1:] if question[:1].islower() and rng.random() < 0.5 else question
            turns.append((speaker, noisy(question, rng), topic))
            if topic == "teach" and rng.random() < 0.5:  # then follow his own advice and check it works
                turns.append((speaker, "Maria plays the violin.", "_teach"))
                turns.append((speaker, "What does Maria play?", "_check"))
            if topic == "corrections" and rng.random() < 0.5:
                turns.append((speaker, "Maria lives in Dayton.", "_teach"))
                turns.append((speaker, "No, she lives in Denver.", "_teach"))
                turns.append((speaker, "Where does Maria live?", "_check_place"))
        conversations.append((f"self/{i}", turns))
    return conversations


def check(topic: str, reply: str) -> list[str]:
    issues = []
    if topic == "_check":
        return [] if "violin" in reply else ["advice-did-not-work"]
    if topic == "_check_place":
        return [] if "Denver" in reply and "Dayton" not in reply else ["advice-did-not-work"]
    if topic.startswith("_"):
        return []
    if len(reply) < 280:
        issues.append("too-short")
    if not any(k.lower() in reply.lower() for k in KEYWORDS[topic]):
        issues.append("off-topic")
    if any(p in reply for p in ("didn't follow", "not following", "I couldn't", "I'm unsure", "Sorry, I didn't")):
        issues.append("not-understood")
    return issues
