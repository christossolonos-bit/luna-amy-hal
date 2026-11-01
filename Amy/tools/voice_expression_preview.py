"""Offline delivery harness; no bot launch, no API calls, no live memory access.

python -m tools.voice_expression_preview --catalog
python -m tools.voice_expression_preview --state excited --effect chuckling
"""

import argparse
import json
import random

from voice.expression import CATALOG, SOUNDS, VoiceExpressionEngine

STATES = {
    "neutral": {"energy": 0.6, "valence": 0, "curiosity": 0.3},
    "excited": {"energy": 0.95, "valence": 0.8, "emotions": {"excitement": 0.95}, "chemicals": {"dopamine": 22}},
    "curious": {"energy": 0.7, "curiosity": 0.95},
    "sad": {"energy": 0.4, "valence": -0.7, "emotions": {"sadness": 0.8}},
    "stressed": {"energy": 0.7, "stress": 0.9, "emotions": {"anxiety": 0.8}, "chemicals": {"cortisol": 25}},
    "tired": {"energy": 0.12, "valence": 0},
    "warm": {"energy": 0.7, "valence": 0.5, "emotions": {"affection": 0.9}, "chemicals": {"oxytocin": 12}},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("--state", choices=STATES, default="neutral")
    parser.add_argument("--effect", choices=SOUNDS)
    parser.add_argument("--model", default="s2.1-pro-free")
    parser.add_argument("--context", default="")
    parser.add_argument("--text", default="That's an interesting connection. What would you like to explore next?")
    args = parser.parse_args()
    if args.catalog:
        print(json.dumps(CATALOG, indent=2))
        return
    engine = VoiceExpressionEngine(rng=random.Random(0))
    plan = engine.plan(args.text, STATES[args.state], args.context, preview_effect=args.effect)
    print(json.dumps({**plan.summary(), "tts_text": plan.render(args.model)}, indent=2))


if __name__ == "__main__":
    main()
