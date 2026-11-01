"""Short progress reports drawn exclusively from committed investigation events."""

import json
import hashlib
from reasoning.investigation_results import findings


def blip(state, event, pick=None):
    topic=state['objective'].rstrip('?.')[:100]
    detail=event['detail']
    action=event['action']
    if action=='opened':
        return f"I'm starting with {topic}. First I need to separate the question into pieces I can check."
    if action=='decompose':
        return f"For {topic}, I've identified {len(detail['terms'])} terms to examine. Next I'm checking their meanings."
    if action=='compare-definitions':
        if detail['ambiguities']:
            return f"There are competing meanings in {topic}. I'm keeping those interpretations separate while I compare the evidence."
        if detail['senses']:
            return f"I've found relevant dictionary meanings for {topic}. Now I'm checking what my recorded evidence actually establishes."
        return f"I don't have a local dictionary definition for {topic}. I'm checking the recorded facts before deciding what to ask next."
    if action=='retrieve-evidence':
        return f"I've collected {detail['collected']} relevant recorded claims for {topic}. Next I'll test what follows from them." if event.get('phase')=='derive' else f"I'm still examining the recorded evidence for {topic}; {detail['collected']} relevant claims so far."
    if action=='test-established-rules':
        if detail['gaps']:
            return 'I found a specific gap: '+detail['gaps'][0]['question']
        if detail['conclusions']:
            status=detail['conclusions'][0]['status']
            if status in ('yes','no'):
                conclusion=detail['conclusions'][0].get('text',f"{'yes' if status=='yes' else 'no'} to {topic}")
                return f"The evidence supports this: {conclusion}. I'm checking possible generalizations before I summarize."
        if detail.get('connections'):
            return "I've established a connection: "+detail['connections'][0]['text']+". I'm checking the remaining implications."
        return f"My established rules don't settle {topic}. I'm looking for patterns that I can test as proposals."
    if action=='propose-quarantined-rules':
        count=detail['candidates']
        return f"I've formed {count} provisional rule proposals for {topic}. I'm checking other cases for support and counterexamples." if count else f"I haven't found a defensible new rule for {topic}. I'll summarize the evidence and the remaining questions."
    if action=='counterexample-tests':
        rejected=sum(h['status']=='rejected' for h in detail['hypotheses'])
        retained=sum(h['retained'] for h in detail['hypotheses'])
        return f"The checks rejected {rejected} proposals and retained {retained} as provisional for {topic}. I'm putting the supported findings together."
    if action=='synthesize':
        return f'Here is what I found about {topic}.\n\n'+findings(state,pick=pick)
    if action=='budget-limit':
        return f'Here is what I established so far about {topic}.\n\n'+findings(state,pick=pick)
    return None


def pending(store, owner, now, cooldown=25, pick=None):
    if store.get('narration:muted:'+owner,False):return None
    worker=store.get('worker')
    if worker and (worker.get('error') or now-worker.get('last_cycle',0)>30
                   or store.get('worker_stopped',0)>=worker.get('last_cycle',0)):
        return None
    if now-store.get('narration:last:'+owner,0)<cooldown:
        return None
    rows=store.conn.execute('SELECT id,payload FROM investigations WHERE owner=? '
                            'AND json_extract(payload,"$.parent") IS NULL ORDER BY updated DESC', (owner,)).fetchall()
    for row in rows:
        state=json.loads(row['payload'])
        after=store.get('narration:cursor:'+str(row['id']),0)
        spoken=set(store.get('narration:spoken:'+str(row['id']),[]))
        # Coalesce progress when the worker advances faster than spoken updates.
        events=store.conn.execute('SELECT id,payload FROM investigation_events WHERE investigation_id=? AND id>? '
                                  'ORDER BY id DESC LIMIT 20', (row['id'],after)).fetchall()
        for event_row in events:
            text=blip(state,json.loads(event_row['payload']),pick=pick)
            if text and hashlib.sha256(text.encode()).hexdigest() not in spoken:
                return {'text':text,'event':event_row['id'],'investigation':row['id'],'owner':owner,
                        'revision':state['revision']}
    return None


def delivered(store, notice, now):
    store.set('narration:cursor:'+str(notice['investigation']),notice['event'])
    store.set('narration:last:'+notice['owner'],now)
    key='narration:spoken:'+str(notice['investigation'])
    spoken=store.get(key,[])
    spoken.append(hashlib.sha256(notice['text'].encode()).hexdigest())
    store.set(key,spoken)
