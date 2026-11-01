"""Examples of how to talk to him, shown when he doesn't understand or someone asks what he can do.

Each hint picks a few from different categories, so people see the range (facts, questions, rules,
corrections, memory, small talk) instead of the same line every time. If what they said looked
like a question, question examples come first.
"""

import random
import re

EXAMPLES: dict[str, tuple[str, ...]] = {
    "fact": (
        "Howard owns a guitar.", "Bananas are fruit.", "Rex is a dog.", "Bob lives in Dayton.",
        "My sister works at a bakery.", "Dogs chase cats.", "The bakery is famous.",
        "Howard drove to Ohio yesterday.", "I play guitar.", "Your favorite color is blue.",
    ),
    "question": (
        "Does Howard own a vehicle?", "What does Bob drive?", "Where does Bob live?", "Who owns a dog?",
        "Is Rex an animal?", "When did Howard go to Ohio?", "Tell me about Howard.", "What are you?",
    ),
    "rule": (
        "If someone owns a dog, they are a pet owner.", "Anyone who drives a bus is a driver.",
        "If something is a bird, it can fly.", "If a person is a vegetarian, they don't eat meat.",
    ),
    "correction": (
        "No, he lives in Columbus.", "Actually, Bob drives a truck.", "That's wrong.",
        "Forget that Howard owns a boat.", "Howard drives a bus, not a truck.",
    ),
    "memory": (
        "What did I just say?", "What have we been talking about?", "Did I tell you that Bob owns a boat?",
        "What did I tell you about Howard?", "Repeat that.",
    ),
    "social": ("How are you?", "What can you do?", "Who made you?", "What's your favorite color?"),
}
QUESTION_START = re.compile(r"^\W*(?:what|who|where|when|why|how|which|is|are|am|do|does|did|can|could|will|"
                            r"would|should|have|has)\b", re.I)


def hint(said: str = "", count: int = 3, rng: random.Random | None = None) -> str:
    """'You could try something like "…", "…", or "…".' with examples from different categories."""
    rng = rng or random
    looks_like_question = said.strip().endswith("?") or bool(QUESTION_START.match(said))
    order = ["question", "fact", "rule", "memory", "correction", "social"] if looks_like_question \
        else ["fact", "question", "rule", "correction", "memory", "social"]
    lead, rest = order[0], order[1:]
    rng.shuffle(rest)
    picks = [rng.choice(EXAMPLES[c]) for c in [lead, *rest][:count]]
    quoted = [f"“{p}”" for p in picks]
    listed = ", ".join(quoted[:-1]) + f", or {quoted[-1]}" if len(quoted) > 1 else quoted[0]
    return f"You could try something like {listed}."


def capabilities(rng: random.Random | None = None) -> str:
    """For 'what can you do?': one example of each kind of thing he understands."""
    rng = rng or random
    labels = [("fact", "teach me facts"), ("question", "ask me questions"), ("rule", "give me rules"),
              ("correction", "correct me"), ("memory", "ask what we said")]
    parts = [f"{label} (“{rng.choice(EXAMPLES[key])}”)" for key, label in labels]
    return "You can " + ", ".join(parts[:-1]) + f", or {parts[-1]}."
