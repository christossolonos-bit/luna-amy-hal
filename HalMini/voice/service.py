"""Glue between the Discord sidecar and the engine.

utterance (PCM) -> transcribe -> wake-word gate -> Conversation.handle -> post transcript + reply
-> synthesize -> play. Speech not addressed to the bot is remembered (searchable history) but
not answered, and by default not learned as fact.
"""

import base64
import time
from collections.abc import Callable

from app.conversation import Conversation
from voice.wake import WakeResult, WakeWord

STALE_SECONDS = 30
PEER_MAX_EXCHANGES = int(__import__("os").environ.get("HALMINI_PEER_MAX_EXCHANGES", "8"))  # drop speech we couldn't get to in time rather than answer it late


class VoiceService:
    def __init__(self, conversation: Conversation, send: Callable[[dict], None], transcriber,
                 speaker=None, wake: WakeWord | None = None, learn_overheard: bool = False,
                 log: Callable[[str], None] = print) -> None:
        self.convo = conversation
        self.send = send
        self.transcriber = transcriber
        self.speaker = speaker
        self.wake = wake
        self.wake_enabled = wake is not None
        self.learn_overheard = learn_overheard
        self.log = log
        self.last_speaker: dict[str, str] = {}
        self.peer_runs: dict[str, int] = {}  # channel -> bot turns in a row since a person spoke  # guild -> who we last answered by voice

    def vocabulary(self) -> list[str]:
        names = [r[0] for r in self.convo.repo.conn.execute(
            "SELECT name FROM entities WHERE proper = 1 ORDER BY id DESC LIMIT 50")]
        return [self.convo.self_name, *names]

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "utterance":
            self.on_utterance(msg)
        elif kind == "text":
            self.on_text(msg)
        elif kind == "command":
            self.on_command(msg)
        elif kind == "playback":
            self.log(f"sidecar: {msg}")
            if msg.get("state") == "finished" and self.wake:
                who = self.last_speaker.get(msg["guild"])
                if who:
                    self.wake.answered(who)  # the follow-up window starts when I stop talking
        elif kind == "ready":
            # Answer to the Discord account's name too ("MiniMe"), by text and by voice.
            name = msg["user"]["name"]
            self.convo.add_self_alias(name)
            if self.wake:
                self.wake.add_alias(name)
            self.log(f"sidecar: {msg}")
        elif kind in ("joined", "left", "error"):
            self.log(f"sidecar: {msg}")

    def on_utterance(self, msg: dict) -> None:
        if time.time() * 1000 - msg.get("startedAt", time.time() * 1000) > STALE_SECONDS * 1000:
            self.log("dropping stale utterance")
            return
        name = msg["user"]["name"]
        heard = self.transcriber.transcribe(base64.b64decode(msg["pcm"]), self.vocabulary()).text.strip()
        if not heard:
            return
        gate = self.wake.check(heard, name) if self.wake_enabled else WakeResult(True, heard)
        reason = ("follow-up" if gate.by_follow_up else "wake word") if gate.addressed else "not addressed"
        self.log(f"heard {name}: {heard!r} -> {reason}")
        if not gate.addressed:
            self.convo.overhear(heard, name, learn=self.learn_overheard, speaker_id=msg["user"].get("id"),
                                is_bot=bool(msg["user"].get("bot")))
            return
        reply = "Yes?" if gate.wake_only else self.convo.handle(
            gate.text, speaker=name, speaker_id=msg["user"].get("id"), is_bot=bool(msg["user"].get("bot"))).text
        if self.wake:
            self.wake.answered(name)
        self.last_speaker[msg["guild"]] = name
        if msg.get("textChannel"):
            self.send({"type": "say", "channel": msg["textChannel"],
                       "content": f"\U0001f399️ **{name}:** {gate.text or heard}\n{reply}"})
        if self.speaker is not None:
            self.send({"type": "play", "guild": msg["guild"],
                       "pcm": base64.b64encode(self.speaker.discord_pcm(reply)).decode("ascii")})

    def _peer_turn_allowed(self, msg: dict, is_bot: bool) -> bool:
        """Bots may talk with him, but a run of bot-to-bot turns with no person speaking is capped, so two
        companions can't talk each other into an endless loop. Any person speaking resets it."""
        channel = msg.get("channel")
        if not is_bot:
            self.peer_runs[channel] = 0
            return True
        self.peer_runs[channel] = self.peer_runs.get(channel, 0) + 1
        return self.peer_runs[channel] <= PEER_MAX_EXCHANGES

    def on_text(self, msg: dict) -> None:
        author = msg["author"]
        is_bot = bool(author.get("bot"))
        if not self._peer_turn_allowed(msg, is_bot):
            self.convo.overhear(msg["content"], author["name"], speaker_id=author.get("id"), is_bot=True)
            return
        reply = self.convo.handle(msg["content"], speaker=author["name"], speaker_id=author.get("id"),
                                  is_bot=is_bot)
        out = {"type": "say", "channel": msg["channel"], "content": reply.text}
        if is_bot and msg.get("messageId"):
            out["replyTo"] = msg["messageId"]
        self.send(out)

    def on_command(self, msg: dict) -> None:
        if msg["command"] == "state":
            snap = self.convo.self_model.snapshot()
            lines = [f"{k}: {v}" for k, v in snap.items() if k not in ("chemicals", "counts")]
            lines += [f"  {k}: {v}" for k, v in snap["chemicals"].items()]
            self.send({"type": "say", "channel": msg["channel"], "content": "```\n" + "\n".join(lines) + "\n```"})
            return
        if msg["command"] == "mode":
            if msg["arg"] in ("always", "wake"):
                self.wake_enabled = msg["arg"] == "wake" and self.wake is not None
            state = "only when someone says my name" if self.wake_enabled else "to everything I hear"
            self.send({"type": "say", "channel": msg["channel"], "content": f"I'm responding {state}."})
        elif msg["command"] == "learn":
            self.learn_overheard = msg["arg"] == "on"
            self.send({"type": "say", "channel": msg["channel"],
                       "content": "I'll learn facts from conversation I overhear." if self.learn_overheard
                       else "I'll only learn from things said to me."})
        else:
            self.send({"type": "say", "channel": msg["channel"],
                       "content": "Commands: !join, !leave, !listen on|off, !stop, !mode wake|always, !learn on|off, !state"})
