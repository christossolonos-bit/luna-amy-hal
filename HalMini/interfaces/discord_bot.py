"""Run HalMini on Discord: text chat plus voice (via the Node sidecar).

    python -m interfaces.discord_bot

In Discord: join a voice channel and type !join. Talk normally; say "Adam, ..." to address it.
"""

import os
import sys

from app.config import load_config, load_dotenv
from app.conversation import Conversation
from db.repository import Repository
from voice.bridge import SidecarBridge
from voice.service import VoiceService
from voice.wake import WakeWord


def load_speaker():
    engine = os.environ.get("TTS_ENGINE", "fish").lower()
    if engine == "fish":
        if not os.environ.get("FISH_API_KEY"):
            print("FISH_API_KEY is not set.", file=sys.stderr)
            return None
        from voice.tts import FishSpeaker
        return FishSpeaker(os.environ["FISH_API_KEY"], voice_id=os.environ.get("FISH_VOICE_ID") or None,
                           model=os.environ.get("FISH_MODEL", "s2.1-pro-free"),
                           latency=os.environ.get("FISH_LATENCY", "normal"))
    if engine == "piper" and os.environ.get("PIPER_VOICE"):
        from voice.tts import PiperSpeaker
        return PiperSpeaker(os.environ["PIPER_VOICE"])
    return None


def main() -> None:
    load_dotenv()
    config = load_config()
    if not os.environ.get("DISCORD_TOKEN"):
        sys.exit("Set DISCORD_TOKEN (see .env.example).")

    name = os.environ.get("HALMINI_NAME", "Adam")
    repo = Repository(config.db_path)
    convo = Conversation(repo, self_name=name, max_depth=config.max_depth, budget=config.inference_budget,
                         curious=True)

    from voice.stt import Transcriber
    print("loading speech recognition model...", file=sys.stderr)
    transcriber = Transcriber(os.environ.get("WHISPER_MODEL", "small.en"),
                              device=os.environ.get("WHISPER_DEVICE", "auto"),
                              compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "default"))
    speaker = load_speaker()
    if speaker is None:
        print("No text-to-speech configured: replies will be text only.", file=sys.stderr)

    aliases = tuple(a.strip().lower() for a in os.environ.get("HALMINI_WAKE_WORDS", "").split(",") if a.strip())
    wake = WakeWord(name, aliases or None,
                    follow_up_seconds=float(os.environ.get("HALMINI_FOLLOW_UP_SECONDS", "0")))

    bridge = SidecarBridge(os.environ.get("NODE", "node"))
    service = VoiceService(convo, bridge.send, transcriber, speaker, wake,
                           learn_overheard=os.environ.get("HALMINI_LEARN_OVERHEARD", "false") == "true",
                           log=lambda m: print(m, file=sys.stderr))
    service.wake_enabled = os.environ.get("HALMINI_LISTEN_MODE", "wake") == "wake"
    bridge.start()
    try:
        for message in bridge.messages():
            try:
                service.handle(message)
            except Exception as exc:  # one bad utterance must not take the bot down
                print(f"error handling {message.get('type')}: {exc!r}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        repo.close()


if __name__ == "__main__":
    main()
