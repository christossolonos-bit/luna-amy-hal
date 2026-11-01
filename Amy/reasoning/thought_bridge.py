"""Main-process adapter: manage a child worker and offer its independently checked chains."""

import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from reasoning import thought_phrasing
from reasoning.thoughts import ThoughtStore, explain_chain, valid_chain

ROOT=Path(__file__).resolve().parents[1]
ASK=re.compile(r"^\W*(?:what (?:have you been|are you) thinking(?: about)?|"
               r"what connections have you made|show me your (?:thoughts|reasoning)|do you have (?:any )?thoughts|"
               r"tell me what you(?:'ve| have) worked out)\W*$",re.I)
STOP=re.compile(r"\b(?:be quiet|stop talking|stop asking|don't ask|do not ask|goodbye|bye)\b",re.I)
SENSITIVE=re.compile(r"\b(?:grief|died|death|funeral|bereaved|suicide|heartbroken|depressed)\b",re.I)


class ThoughtBridge:
    def __init__(self,memory_path,queue_path=None,clock=time.time):
        self.memory_path=Path(memory_path).resolve()
        self.queue_path=Path(queue_path or self.memory_path.with_suffix('.thoughts.db')).resolve()
        if self.queue_path==self.memory_path:
            raise ValueError('Thought queue must not be live memory')
        self.proc=None
        self.clock=clock
        self.turns={}

    def start(self):
        if self.proc and self.proc.poll() is None:return
        command=[sys.executable,'-m','reasoning.thought_worker','--memory',str(self.memory_path),'--queue',str(self.queue_path)]
        self.proc=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                                   stderr=None,close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:self.proc.kill()

    def _take(self,convo,heard,explicit=False):
        if not self.queue_path.is_file():return None
        speaker=convo.state.speaker
        now=self.clock()
        state=convo.self_model.current()
        if not explicit and (state['energy']<.35 or state['stress']>.7 or state['curiosity']<.45):return None
        words=set(re.findall(r"\w+",heard.casefold()))
        try:
            store=ThoughtStore(self.queue_path)
            try:
                if not explicit and now-store.last_delivery(speaker)<max(40,150-100*state['curiosity']):return None
                for chain in store.ready():
                    if not explicit and not words & set(re.findall(r"\w+",chain.topic.casefold())):continue
                    if store.delivered(speaker,chain.question) or not valid_chain(chain,convo.repo):continue
                    text=explain_chain(chain,convo.repo,convo.social.phrases.pick)
                    store.mark_delivered(speaker,chain,now)
                    return text,{'key':chain.key,'kind':chain.kind,'topic':chain.topic,'premises':chain.premises,
                                 'rules':chain.rules,'lexical_path':chain.lexical_path,'confidence':chain.confidence,
                                 'hypothesis':chain.hypothesis,'question':chain.question}
            finally:store.close()
        except sqlite3.Error:
            return None
        return None

    def announce(self,convo):
        from app.behaviors import enabled
        if not enabled("thought_posts"):return []
        """Each conclusion the worker reaches, explained once, for posting to a text channel."""
        if not self.queue_path.is_file():return []
        out=[]
        try:
            store=ThoughtStore(self.queue_path)
            try:
                if self.clock()-store.get('last_announcement',0)<120:return []
                posted=set(store.get('announced',[]))
                for chain in store.ready():
                    if chain.key in posted or not valid_chain(chain,convo.repo):continue
                    out.append(explain_chain(chain,convo.repo,convo.social.phrases.pick))
                    posted.add(chain.key)
                    store.set("last_announcement",self.clock())
                    break
                if out:store.set('announced',sorted(posted))
            finally:store.close()
        except sqlite3.Error:
            return []
        return out

    def respond(self,convo,heard):
        from reasoning.investigations import Investigations, explain, fingerprint
        from reasoning.investigation_results import findings
        from dialogue.investigation_wording import say
        wording=lambda key,**fields:say(convo.social.phrases.pick,key,**fields)
        heard=re.sub(r"^\W*(?:(?:I (?:want|need|would like) you to)|please)\s+",'',heard,flags=re.I)
        investigate=re.fullmatch(r"\W*(?:investigate|work through|reason through|research)\s+(.{1,500})",heard,re.I)
        inspect=re.fullmatch(r"\W*(?:(?:show|explain)(?: me)? (?:your |the )?(?:investigation|investigations|progress)|!investigations)(?:\s+#?(\d+))?\W*",heard,re.I)
        updates=re.fullmatch(r"\W*(stop|pause|resume|start) (?:the |your )?(?:progress|thinking|investigation) updates\W*",heard,re.I)
        results=re.fullmatch(r"\W*(?:what (?:did|have) you (?:(?:find|found)(?: out)?|learn(?:ed|t)?|discover(?:ed)?|work(?:ed)? out)|what were your (?:findings|results|conclusions)|(?:tell|show) me (?:what you found|your findings|the results))\W*",heard,re.I)
        capability=re.fullmatch(r"\W*(?:(?:can|could) you|are you (?:able to|capable of))\s+(.{1,150}?)\W*",heard,re.I)
        # Operational commands continue to their existing handlers.
        if capability and re.match(r"(?:hear|see|read|remember|learn|speak|say|stop|join|leave|help|look|search|tell|explain|show|think|understand)\b",capability.group(1),re.I):
            capability=None
        focus=re.fullmatch(r"\W*(?:think|reason|reflect) (?:about|on) (.{1,100}?)\W*",heard,re.I)
        status=heard.strip().lower().rstrip('?.!') in ('!thoughts','thinking status','show thinking status')
        if not (focus or status or ASK.match(heard) or investigate or inspect or capability or updates or results):return None
        try:
            store=ThoughtStore(self.queue_path)
            try:
                sessions=Investigations(store)
                if updates:
                    muted=updates.group(1).lower() in ('stop','pause')
                    store.set('narration:muted:'+convo.state.speaker,muted)
                    return ('Progress updates paused. The investigation can continue.' if muted else 'Progress updates resumed.',
                            {'status':'updates-paused' if muted else 'updates-resumed'})
                if inspect or results:
                    state=sessions.get(int(inspect.group(1))) if inspect and inspect.group(1) else sessions.latest(convo.state.speaker)
                    if not state or state['owner']!=convo.state.speaker:
                        if results:return None
                        return ('I have no investigation for you to show yet. Ask me to investigate a question.',{'status':'no-investigation'})
                    if state['revision'] and state['revision']!=fingerprint(convo.repo):
                        return ('My evidence has changed since that investigation pass. The worker needs to recheck the conclusions and provisional rules.',
                                {'status':'investigation-stale','investigation_id':state['id']})
                    return (findings(state,pick=convo.social.phrases.pick) if results else explain(state,convo.repo,pick=convo.social.phrases.pick)),{'status':'investigation','investigation':state}
                if investigate or capability:
                    objective=investigate.group(1).strip() if investigate else re.sub(r'\byou\b',convo.self_name,heard,flags=re.I)
                    state=sessions.open(objective,convo.state.speaker)
                    if capability:
                        state['self_inspection']=True
                        sessions._save(state,'request-self-inspection',{})
                    return (wording('queued',id=state['id'],topic=objective),
                            {'status':'investigation-queued','investigation_id':state['id']})
                if focus:
                    topic=focus.group(1).strip()
                    store.set('requested_focus',topic)
                    state=sessions.open(topic,convo.state.speaker)
                    return (wording('focus',topic=topic),
                            {'status':'focus-requested','topic':topic,'investigation_id':state['id']})
                worker=store.get('worker',{})
                running=bool(worker.get('alive') and not worker.get('error') and self.clock()-worker.get('last_cycle',0)<30
                             and store.get('worker_stopped',0)<worker.get('last_cycle',0))
                prefix='The thinking worker is active.' if running else 'The thinking worker is not currently reporting healthy activity.'
                if worker.get('error'):prefix+=' Its last error was '+worker['error']+'.'
                chains=[c for c in store.ready() if valid_chain(c,convo.repo)]
                if not chains:
                    return (prefix+' I do not have a supported concept reflection ready to show yet.',{'status':'waiting','worker':worker})
                chain=chains[0]
                text=explain_chain(chain,convo.repo,convo.social.phrases.pick)
                if status:
                    text=prefix+'\n\n'+text+'\n\nEvidence: assertion IDs '+', '.join(map(str,chain.premises))+'.'
                return text,{'status':'ready','key':chain.key,'premises':chain.premises,'rules':chain.rules,
                             'episodes':chain.episodes,'steps':chain.steps,'worker':worker}
            finally:store.close()
        except sqlite3.Error:
            return ('My thinking queue is unavailable right now.',{'status':'unavailable'})

    def offer(self,convo,heard,reply,is_bot=False):
        speaker=convo.state.speaker
        self.turns[speaker]=self.turns.get(speaker,0)+1
        if is_bot or STOP.search(heard) or SENSITIVE.search(heard+' '+reply) or '?' in reply or self.turns[speaker]<6:
            return None
        result=self._take(convo,heard)
        if result:self.turns[speaker]=0
        return result

    def narration(self,convo,owner,deliver):
        from app.behaviors import enabled
        if not enabled("progress_updates"):return False
        """Advance the delivery cursor only after the service accepted the blip."""
        if not self.queue_path.is_file():return False
        from reasoning.investigations import Investigations, fingerprint
        from reasoning.narration import pending, delivered
        store=ThoughtStore(self.queue_path)
        try:
            Investigations(store)
            notice=pending(store,owner,self.clock(),pick=convo.social.phrases.pick)
            if not notice:return False
            if notice['revision'] and notice['revision']!=fingerprint(convo.repo):return False
            if not deliver(notice['text']):return False
            delivered(store,notice,self.clock())
            return True
        finally:store.close()

    def investigating(self,owner):
        if not self.queue_path.is_file():return False
        from reasoning.investigations import Investigations
        store=ThoughtStore(self.queue_path)
        try:
            state=Investigations(store).latest(owner)
            return bool(state and state['phase'] not in ('waiting','complete','limited'))
        finally:store.close()
