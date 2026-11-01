"""Clean ordinary reply text before the trusted voice director adds TTS controls."""

import re


def speakable(text: str) -> str:
    text = re.sub(r"<\|[^>]*\|>", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    # Do not let quoted legacy voice instructions become delivery controls.
    text = re.sub(r"\((?:happy|sad|angry|excited|calm|nervous|laugh|laughing|chuckling|"
                  r"breath|sigh|sighing|break|long-break|whispering|shouting|yawning)\)", "", text, flags=re.I)
    text = re.sub(r"[*_`#>“”\"]", "", text)
    return re.sub(r"\s+", " ", text).strip()
