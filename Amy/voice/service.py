"""Glue between the Discord sidecar and the engine.

utterance (PCM) -> transcribe -> wake-word gate -> Conversation.handle -> post transcript + reply
-> synthesize -> play. Speech not addressed to the bot is remembered (searchable history) but
not answered, and by default not learned as fact.
"""

import base64
import json
import re
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
        self._transport = send
        self.send = self._send
        self.transcriber = transcriber
        self.speaker = speaker
        self.wake = wake
        self.wake_enabled = wake is not None
        self.learn_overheard = learn_overheard
        self.log = log
        self.last_speaker: dict[str, str] = {}
        self.peer_runs: dict[str, int] = {}  # channel -> bot turns in a row since a person spoke  # guild -> who we last answered by voice
        self.last_voice_plan = None
        self.progress_targets = {}  # identified speaker -> their requesting Discord channel/guild
        self.playing = set()
        self.last_activity = time.time()
        self.narrated_recent = []
        self.wording=getattr(getattr(getattr(conversation,'social',None),'phrases',None),'history',None)

    def _fresh_spoken(self,text):
        if self.wording is None:return text
        from dialogue.repetition import cores
        catalog=getattr(getattr(self.convo,'scripted_replies',None),'banks',{})
        if text.strip() in catalog and any(self.wording.has('*',clause,'delivered-core') for clause in cores(text)):
            self.log('speech held: the canned sentence was already spoken inside another reply')
            return None
        candidate=self.wording.fresh(text)
        if candidate is None:
            self.log('speech held: this wording has already been delivered; text remains available')
        return candidate

    def _send(self, message):
        """Keep full findings when Discord's per-message text limit is exceeded."""
        content=message.get('content','')
        if message.get('type')!='say' or len(content)<=2000:
            return self._transport(message)
        while content:
            end=2000
            if len(content)>end:
                boundary=max(content.rfind('\n',0,end),content.rfind(' ',0,end))
                if boundary>1000:end=boundary+1
            self._transport({**message,'content':content[:end]})
            content=content[end:]

    def _voice_pcm(self, reply, heard, intent=None):
        directed = getattr(self.speaker, "discord_pcm_with_state", None)
        if not callable(directed):
            return self.speaker.discord_pcm(reply)
        trace = self.convo.state.last_trace or {}
        pcm = directed(reply, self.convo.self_model.snapshot(), context=heard, intent=intent or trace.get("intent"))
        plan = getattr(self.speaker, "last_plan", None)
        if plan is not None:
            self.last_voice_plan = plan.summary()
            self.log("voice expression: " + json.dumps(self.last_voice_plan))
        return pcm

    def vocabulary(self) -> list[str]:
        names = [r[0] for r in self.convo.repo.conn.execute(
            "SELECT name FROM entities WHERE proper = 1 ORDER BY id DESC LIMIT 50")]
        return [self.convo.self_name, *names]

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind in ('utterance','text','command'):
            self.last_activity=time.time()
        if kind == "utterance":
            self.on_utterance(msg)
        elif kind == "text":
            self.on_text(msg)
        elif kind == "command":
            self.on_command(msg)
        elif kind == "playback":
            if msg.get('state')=='started':self.playing.add(msg['guild'])
            elif msg.get('state') in ('finished','interrupted'):self.playing.discard(msg['guild'])
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
            if kind=='left':
                guild=msg.get('guild')
                self.playing.discard(guild)
                self.progress_targets={k:v for k,v in self.progress_targets.items() if v.get('guild')!=guild}
            self.log(f"sidecar: {msg}")

    def progress(self, text, target):
        """Narrate only in a conversational pause, never over active bot playback."""
        guild=target.get('guild')
        channel=target.get('channel')
        if not channel or time.time()-self.last_activity<8 or guild in self.playing:
            return False
        fresh=self._fresh_spoken(text)
        if fresh is None:
            self.send({'type':'say','channel':channel,'content':text})
            return True
        text=fresh
        if guild and self.speaker is not None:
            pcm=self._voice_pcm(text,'investigation progress',intent='thinking-progress')
            self.send({'type':'play','guild':guild,'pcm':base64.b64encode(pcm).decode('ascii')})
            self.playing.add(guild)
        self.send({'type':'say','channel':channel,'content':text})
        if self.wording:self.wording.delivered(text)
        self.narrated_recent=[(when,said) for when,said in self.narrated_recent if time.time()-when<180]
        self.narrated_recent.append((time.time(),text))
        return True

    def _my_own_echo(self, heard: str) -> bool:
        words = set(re.findall(r"[a-z']{3,}", heard.lower()))
        if len(words) < 4:
            return False
        for when,said in self.narrated_recent:
            mine=set(re.findall(r"[a-z']{3,}",said.lower()))
            if time.time()-when<180 and mine and len(words & mine)/len(words)>=.6:
                return True
        rows = self.convo.repo.conn.execute(
            "SELECT text FROM dialogue_turns WHERE role='engine' AND created_at >= datetime('now', '-3 minutes') "
            "ORDER BY id DESC LIMIT 5").fetchall()
        for row in rows:
            mine = set(re.findall(r"[a-z']{3,}", row["text"].lower()))
            if mine and len(words & mine) / len(words) >= 0.6:
                return True
        return False

    def on_utterance(self, msg: dict) -> None:
        if msg.get("textChannel"):
            self.last_text_channel = msg["textChannel"]
        if time.time() * 1000 - msg.get("startedAt", time.time() * 1000) > STALE_SECONDS * 1000:
            self.log("dropping stale utterance")
            return
        name = msg["user"]["name"]
        heard = self.transcriber.transcribe(base64.b64decode(msg["pcm"]), self.vocabulary()).text.strip()
        if not heard:
            return
        if self._my_own_echo(heard):
            self.log(f"ignoring {name}: that's my own reply coming back through their mic")
            return
        gate = self.wake.check(heard, name) if self.wake_enabled else WakeResult(True, heard)
        reason = ("follow-up" if gate.by_follow_up else "wake word") if gate.addressed else "not addressed"
        self.log(f"heard {name}: {heard!r} -> {reason}")
        if not gate.addressed:
            self.convo.overhear(heard, name, learn=self.learn_overheard, speaker_id=msg["user"].get("id"),
                                is_bot=bool(msg["user"].get("bot")))
            return
        if gate.wake_only:
            reply = self.convo.social.phrases.pick("wake-only", (
                "Yes?", "I'm listening.", "You have my attention.", "What would you like to talk about?",
                "Hello there. What's on your mind?", "Go ahead.", "I'm here. What is it?",
                "What can I help you with?", "Tell me.", "What are you curious about?",
                "Yes, I'm here.", "What's the story?", "What would you like to ask?",
                "I'm all ears.", "What shall we explore?")) if self.convo.persona == "amy" else "Yes?"
        else:
            reply = self.convo.handle(gate.text, speaker=name, speaker_id=msg["user"].get("id"),
                                      is_bot=bool(msg["user"].get("bot"))).text
            self.progress_targets[self.convo.state.speaker]={'guild':msg['guild'],'channel':msg.get('textChannel')}
        if not reply:
            return
        if self.wake:
            self.wake.answered(name)
        self.last_speaker[msg["guild"]] = name
        if msg.get("textChannel"):
            self.send({"type": "say", "channel": msg["textChannel"],
                       "content": f"\U0001f399️ **{name}:** {gate.text or heard}\n{reply}"})
        if self.speaker is not None:
            spoken=self._fresh_spoken(reply)
            if spoken:
                self.send({"type": "play", "guild": msg["guild"],
                           "pcm": base64.b64encode(self._voice_pcm(spoken, gate.text or heard)).decode("ascii")})
                if self.wording:self.wording.delivered(spoken)
                self.playing.add(msg['guild'])

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
        content = msg["content"]
        if not msg.get("direct", True) and self.wake is not None:
            gate = self.wake.check(content, author["name"])
            if not gate.addressed:  # his name came up, but nobody was talking to him
                self.convo.overhear(content, author["name"], speaker_id=author.get("id"), is_bot=is_bot)
                return
            content = gate.text or content
        if not self._peer_turn_allowed(msg, is_bot):
            self.convo.overhear(content, author["name"], speaker_id=author.get("id"), is_bot=True)
            return
        reply = self.convo.handle(content, speaker=author["name"], speaker_id=author.get("id"),
                                  is_bot=is_bot)
        if not reply.text:
            return
        out = {"type": "say", "channel": msg["channel"], "content": reply.text}
        self.last_text_channel = msg["channel"]  # where finished thoughts get posted
        self.progress_targets[self.convo.state.speaker]={'guild':None,'channel':msg['channel']}
        if is_bot and msg.get("messageId"):
            out["replyTo"] = msg["messageId"]
        self.send(out)

    def on_command(self, msg: dict) -> None:
        if msg['command']=='stop':
            self.playing.discard(msg.get('guild'))
            self.progress_targets={k:v for k,v in self.progress_targets.items() if v.get('guild')!=msg.get('guild')}
            return
        if msg['command'] in ('thoughts','investigations','investigate'):
            content= ('Investigate '+msg.get('arg','')) if msg['command']=='investigate' else '!'+msg['command']+(' '+msg['arg'] if msg.get('arg') else '')
            self.on_text({'author':msg['author'],'channel':msg['channel'],'content':content})
            return
        if msg["command"] == "voice":
            self.send({"type": "say", "channel": msg["channel"], "content":
                       "No voice delivery plan yet." if self.last_voice_plan is None else
                       "```json\n" + json.dumps(self.last_voice_plan, indent=2) + "\n```"})
            return
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
