"""Persistent, bounded investigations and quarantined empirical rule proposals.

Every pass advances a recorded state. Unknowns remain unknown; proposed rules
never enter Repository.learned_rules or participate in the ordinary reasoner.
"""

from dataclasses import asdict
import hashlib
import json
import re
import time
from nlp.morphology import analyses

from dialogue import realizer
from nlp.wordnet import WordNet, tokens
from reasoning.engine import Engine, query_from_row
from reasoning.proofs import Query, YES, NO, UNKNOWN, CONFLICTING

RESULTS_VERSION=3


def lexical_findings(wordnet, objective, terms):
    """Prefer dictionary multiword expressions before splitting their component words."""
    words=re.findall(r"[a-z]+",objective.lower())[:24]
    candidates=[]
    for width in range(min(5,len(words)),1,-1):
        for start in range(len(words)-width+1):
            phrase=words[start:start+width]
            candidates.append(' '.join(phrase))
            for index,word in enumerate(phrase):
                for lemma,_ in analyses(word)[:2]:
                    candidates.append(' '.join(phrase[:index]+[lemma]+phrase[index+1:]))
    checked=set()
    for phrase in candidates:
        if phrase in checked:continue
        checked.add(phrase)
        if len(checked)>60:break
        senses=wordnet.lookup(phrase,None)
        if senses:
            return [dict(term=phrase,sense=s.id,definition=s.definition,source='local WordNet',
                         status='lexical-interpretation',scope='phrase') for s in senses[:8]]
    return [dict(term=term,sense=s.id,definition=s.definition,source='local WordNet',
                 status='lexical-interpretation',scope='word')
            for term in terms for s in wordnet.lookup(term)[:8]]


def fingerprint(repo):
    """Include edits/retractions/confidence changes, not merely the newest ID."""
    digest = hashlib.sha256()
    for table in ('assertions', 'assertion_args', 'arguments', 'learned_rules', 'concepts', 'entities', 'aliases'):
        # Older repositories may use a different arguments table name.
        exists = repo.conn.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone()
        if exists:
            for row in repo.conn.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                digest.update(json.dumps(tuple(row), default=str).encode())
    return digest.hexdigest()


class Investigations:
    def __init__(self, store):
        self.store = store
        store.conn.executescript('''
            CREATE TABLE IF NOT EXISTS investigations(
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL, objective TEXT NOT NULL,
                payload TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS investigation_events(
                id INTEGER PRIMARY KEY, investigation_id INTEGER NOT NULL,
                created REAL NOT NULL, payload TEXT NOT NULL);
        ''')
        store.conn.commit()

    def open(self, objective, owner, parent=None, query=None, depth=0):
        objective = re.sub(r'\s+', ' ', objective).strip()[:500]
        if not objective:
            raise ValueError('An investigation needs an objective')
        for row in self.store.conn.execute('SELECT payload FROM investigations WHERE owner=? AND objective=?', (owner, objective)):
            prior = json.loads(row[0])
            if prior['parent'] == parent:
                return prior
        state = dict(objective=objective, owner=owner, parent=parent, depth=depth,
                     phase='analyze', passes=0, query=query, evidence=[], definitions=[],
                     interpretations=[], gaps=[], children=[], hypotheses=[], concepts=[], conclusions=[], alternatives=[],
                     connections=[], goal_cursor=0,
                     trace=[], revision=None, cursor=0, started=time.time(), spent=0.,
                     limit_seconds=300., limit_passes=80, reason=None)
        with self.store.conn:
            cursor = self.store.conn.execute('INSERT INTO investigations(owner,objective,payload,updated) VALUES(?,?,?,?)',
                                             (owner, objective, '{}', time.time()))
            state['id'] = cursor.lastrowid
            self._save(state, 'opened', {'objective': objective})
        return state

    def _save(self, state, action, detail):
        event = dict(action=action, detail=detail, pass_number=state['passes'], phase=state['phase'])
        state['trace'] = (state['trace'] + [event])[-40:]
        with self.store.conn:
            self.store.conn.execute('UPDATE investigations SET payload=?,updated=? WHERE id=?',
                                    (json.dumps(state), time.time(), state['id']))
            self.store.conn.execute('INSERT INTO investigation_events(investigation_id,created,payload) VALUES(?,?,?)',
                                    (state['id'], time.time(), json.dumps(event)))

    def get(self, ident):
        row = self.store.conn.execute('SELECT payload FROM investigations WHERE id=?', (ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self, owner):
        row = self.store.conn.execute('SELECT payload FROM investigations WHERE owner=? AND json_extract(payload,"$.parent") IS NULL '
                                      'ORDER BY updated DESC LIMIT 1', (owner,)).fetchone()
        return json.loads(row[0]) if row else None


def _query(data):
    if not data:
        return None
    def tup(value):
        return tuple(tup(v) for v in value) if isinstance(value, list) else value
    return Query(**{key: tup(value) for key, value in data.items()})


def question_query(repo, objective):
    """Resolve only explicit known terms. Never invent a subject or a definition."""
    match = re.fullmatch(r'\W*(?:is|was) (.+?) (?:a |an )?([\w -]+?)\W*', objective, re.I)
    if not match:
        return None
    name, value = match.groups()
    eid = repo.find_entity(name)
    cid = repo.find_concept(value.lower())
    if eid is None or cid is None:
        return None
    relation = 'is_a' if re.search(r'\b(?:a|an)\b', objective, re.I) else 'property'
    query = Query('is_a', ('e', eid), ('c', cid)) if relation == 'is_a' else Query('property', ('e', eid), properties=(cid,))
    return asdict(query)


class InvestigationController:
    def __init__(self, repo, store, wordnet=None, max_queries=12):
        self.repo, self.store = repo, store
        self.sessions = Investigations(store)
        self.wordnet = wordnet or WordNet()
        self.engine = Engine(repo, max_depth=8, budget=1500)
        self.max_queries = max_queries

    def tick(self, limit=1):
        revision = fingerprint(self.repo)
        rows = self.store.conn.execute('SELECT payload FROM investigations ORDER BY updated,id').fetchall()
        advanced = []
        for row in rows:
            state = json.loads(row[0])
            if state['revision'] is not None and state['revision'] != revision and state['phase'] not in ('waiting','complete','limited'):
                state.update(phase='analyze', evidence=[], definitions=[], interpretations=[], gaps=[],
                             children=[], hypotheses=[], concepts=[], conclusions=[], alternatives=[], connections=[], goal_cursor=0, cursor=0)
            if state['phase'] in ('waiting', 'complete', 'limited'):
                if (state['revision'] == revision or state['phase'] == 'limited') and state.get('results_version',0)>=RESULTS_VERSION:
                    continue
                state.update(phase='analyze', evidence=[], definitions=[], interpretations=[], gaps=[],
                             children=[], hypotheses=[], concepts=[], conclusions=[], alternatives=[], connections=[], goal_cursor=0, cursor=0, spent=0., passes=0)
            start = time.monotonic()
            if state['passes'] >= state['limit_passes'] or state['spent'] >= state['limit_seconds']:
                state.update(phase='limited', reason='Investigation budget reached; unresolved questions retained.')
                self.sessions._save(state, 'budget-limit', {})
            else:
                self.advance(state, revision)
                state['spent'] += time.monotonic() - start
                # Persist accumulated work time along with the event created by advance.
                with self.store.conn:
                    self.store.conn.execute('UPDATE investigations SET payload=? WHERE id=?', (json.dumps(state), state['id']))
            advanced.append(state['id'])
            if len(advanced) >= limit:
                break
        return advanced

    def advance(self, state, revision):
        state['passes'] += 1
        phase = state['phase']
        state['revision'] = revision
        if phase == 'analyze':
            state['results_version']=RESULTS_VERSION
            if state.get('self_inspection'):
                from dialogue.self_model import SelfModel
                state['mechanisms']={'source':'dialogue.self_model.SelfModel.current',
                                     'state_variables':SelfModel(self.repo).current()}
            state['terms'] = sorted(tokens(state['objective']))[:12]
            state['query'] = state['query'] or question_query(self.repo, state['objective'])
            state['phase'] = 'definitions'
            self.sessions._save(state, 'decompose', {'terms': state['terms'], 'query': state['query']})
        elif phase == 'definitions':
            state['definitions']=lexical_findings(self.wordnet,state['objective'],state['terms'])
            for term in dict.fromkeys(d['term'] for d in state['definitions']):
                senses=[d['sense'] for d in state['definitions'] if d['term']==term]
                if len(senses)>1:
                    state['interpretations'].append(dict(term=term,alternatives=senses))
            state['phase'] = 'retrieve'
            self.sessions._save(state, 'compare-definitions', {'senses': len(state['definitions']),
                                                              'ambiguities': state['interpretations']})
        elif phase == 'retrieve':
            rows = self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND id>? ORDER BY id LIMIT 64",
                                          (state['cursor'],)).fetchall()
            wanted = set(state['terms'])
            query = _query(state['query'])
            for row in rows:
                text = realizer.realize_assertion(self.repo, row)
                subject_hit = query and query.subject == (row['subj_type'], row['subj_id'])
                if text and row['source_utterance_id'] and (wanted & tokens(text) or subject_hit):
                    state['evidence'].append(dict(id=row['id'], text=text, source=self.repo.source_speaker(row['id']),
                                                  utterance=row['source_utterance_id'], confidence=row['confidence'],
                                                  polarity=bool(row['polarity'])))
                state['cursor'] = row['id']
            if len(rows) < 64:
                state['phase'] = 'derive'
            self.sessions._save(state, 'retrieve-evidence', {'scanned': len(rows), 'collected': len(state['evidence'])})
        elif phase == 'derive':
            finished=self._derive(state)
            if finished:state['phase'] = 'propose'
            self.sessions._save(state, 'test-established-rules', {'conclusions': state['conclusions'], 'gaps': state['gaps'],
                                                               'connections':state.get('connections',[]), 'checked_goals':state.get('goal_cursor',0)})
        elif phase == 'propose':
            self._propose(state)
            state['phase'] = 'test'
            self.sessions._save(state, 'propose-quarantined-rules', {'candidates': len(state['hypotheses'])})
        elif phase == 'test':
            self._test(state)
            state['phase'] = 'synthesize'
            self.sessions._save(state, 'counterexample-tests', {'hypotheses': state['hypotheses']})
        elif phase == 'synthesize':
            query = _query(state['query'])
            resolved = bool(query and any(c['status'] in (YES, NO) for c in state['conclusions']))
            state['phase'] = 'complete' if resolved and not state['gaps'] else 'waiting'
            if not query:
                state['gaps'].append({'kind': 'criteria', 'question': 'Which interpretation or observable criteria would settle '+state['objective'].rstrip('?.')+'?'})
            state['reason'] = 'Supported answer established.' if state['phase'] == 'complete' else 'Waiting for specific missing evidence or criteria; repeating lookups would add nothing.'
            self.sessions._save(state, 'synthesize', {'status': state['phase'], 'reason': state['reason']})

    def _derive(self, state):
        query = _query(state['query'])
        if not query:
            # For concept questions, make an evidence-driven agenda of possible
            # rule conclusions rather than repeatedly asking the same lookup.
            entities=sorted({self.repo.assertion(item['id'])['subj_id'] for item in state['evidence']
                             if self.repo.assertion(item['id'])['subj_type']=='e'})[:16]
            goals=[(eid,rule,pattern) for eid in entities for rule in self.repo.active_rules()
                   for pattern in rule['conclusions']]
            offset=state.get('goal_cursor',0)
            for eid,rule,pattern in goals[offset:offset+self.max_queries]:
                goal=self.engine._instantiate(pattern,('e',eid))
                answer=self.engine.ask(goal)
                if answer.budget_exhausted:
                    state['gaps'].append({'kind':'budget','question':'A candidate inference exceeded its proof budget.'})
                    continue
                if answer.status in (YES,NO):
                    proofs=answer.support if pattern.get('polarity',True) else answer.against
                    for proof in proofs[:1]:
                        text=realizer.derived_text(self.repo,proof)
                        if text and proof.learned_rules and proof.steps:
                            connection=dict(text=text,premises=proof.steps,rules=proof.learned_rules,confidence=proof.confidence)
                            if connection not in state['connections']:state['connections'].append(connection)
                elif answer.status==CONFLICTING:
                    name=realizer.term_text(self.repo,'e',eid,definite=True)
                    state['gaps'].append(dict(kind='conflict',question='Which source is current for '+realizer.pattern_text(self.repo,pattern,name)+'?'))
            state['goal_cursor']=offset+self.max_queries
            if self.max_queries==0 and goals:
                state['gaps'].append({'kind':'budget','question':'No proof queries were available in this work budget.'})
                return True
            return state['goal_cursor']>=len(goals)
        answer = self.engine.ask(query)
        state['conclusions'] = [dict(status=answer.status, proofs=[asdict(p) for p in answer.support],
                                     against=[asdict(p) for p in answer.against], budget_exhausted=answer.budget_exhausted)]
        if answer.status in (YES,NO) and query.subject:
            pattern=dict(subject=list(query.subject),relation=query.relation,obj=query.obj,props=query.properties,
                         verb=query.verb,args=query.args,polarity=answer.status==YES)
            state['conclusions'][0]['text']=realizer.pattern_text(self.repo,pattern,None)
            proofs=answer.support if answer.status==YES else answer.against
            if proofs:
                state['conclusions'][0]['supporting_facts']=[text.rstrip('.') for aid in proofs[0].steps
                    if (text:=realizer.realize_assertion(self.repo,self.repo.assertion(aid)))]
        if answer.status in (YES, NO) and not answer.budget_exhausted:
            return True
        if answer.status == CONFLICTING:
            state['gaps'].append(dict(kind='conflict', question='Which conflicting source is current for '+state['objective']+'?'))
            return True
        checks = 0
        for rule in self.repo.active_rules():
            for pattern in rule['conclusions']:
                try:
                    bound = self.engine._unify(pattern, query)
                except (TypeError, KeyError):
                    continue
                if bound is None or bound[0] is None:
                    continue
                pending=[]
                ruled_out=False
                for condition in rule['conditions']:
                    if checks >= self.max_queries:
                        state['gaps'].append(dict(kind='budget', question='More candidate rule conditions remain to be checked.'))
                        return True
                    checks += 1
                    subquery = self.engine._instantiate(condition, bound[0])
                    result = self.engine.ask(subquery)
                    expected=YES if condition.get('polarity',True) else NO
                    if result.status in (YES,NO,CONFLICTING) and result.status!=expected:
                        ruled_out=True
                    elif result.status==UNKNOWN:
                        pending.append((condition,subquery))
                state.setdefault('alternatives',[]).append({'rule':rule['id'],'status':'ruled-out' if ruled_out else 'missing-premises'})
                if ruled_out:continue
                for condition,subquery in pending:
                    name = realizer.term_text(self.repo, bound[0][0], bound[0][1], definite=True)
                    text = realizer.pattern_text(self.repo, condition, name)
                    state['gaps'].append(dict(kind='premise', rule=rule['id'], question='Is it established that '+text+'?', query=asdict(subquery)))
                    if state['depth'] < 2 and len(state['children']) < 4 and asdict(subquery) != state['query']:
                        child = self.sessions.open('Establish whether '+text, state['owner'], state['id'], asdict(subquery), state['depth']+1)
                        if child['id'] not in state['children']:
                            state['children'].append(child['id'])
        if not state['gaps']:
            state['gaps'].append(dict(kind='missing-rule', question='What evidence or established rule connects the known facts to '+state['objective'].rstrip('?.')+'?'))
        return True

    def _propose(self, state):
        relevant = {item['id'] for item in state['evidence'] if item['confidence'] >= .75}
        # One example proposes an implication; different entities test it. Restrict
        # this first generator to explicit class -> property patterns.
        rows = self.repo.conn.execute("SELECT a.id AS class_id,a.obj_id AS kind,b.id AS prop_id,b.obj_id AS property,"
                                      "a.subj_id AS entity FROM assertions a JOIN assertions b ON a.subj_id=b.subj_id "
                                      "AND a.subj_type='e' AND b.subj_type='e' WHERE a.predicate='instance_of' "
                                      "AND b.predicate='has_property' AND a.polarity=1 AND b.polarity=1 "
                                      "AND a.status='active' AND b.status='active' AND a.confidence>=.75 AND b.confidence>=.75 "
                                      "AND a.source_utterance_id IS NOT NULL AND b.source_utterance_id IS NOT NULL ORDER BY a.id,b.id").fetchall()
        seen = set()
        for row in rows:
            pair = row['kind'], row['property']
            if pair in seen or not ({row['class_id'], row['prop_id']} & relevant):
                continue
            seen.add(pair)
            state['hypotheses'].append(dict(kind=row['kind'], property=row['property'],
                label='Members of '+self.repo.concept_lemma(row['kind'])+' might be '+self.repo.concept_lemma(row['property']),
                training_entity=row['entity'], training=[row['class_id'], row['prop_id']],
                status='proposed', tests=[], counterexamples=[], retained=False))
            state.setdefault('concepts', []).append(dict(
                label=self.repo.concept_lemma(row['kind'])+' with property '+self.repo.concept_lemma(row['property']),
                criteria=[{'predicate':'instance_of','concept':row['kind']},
                          {'predicate':'has_property','concept':row['property']}],
                status='proposed-composite', members=[], retained=False,
                caution='A working intersection of established concepts; not a newly discovered natural category.'))
            if len(seen) >= 8:
                break

    def _test(self, state):
        remaining = self.max_queries
        for hypothesis in state['hypotheses']:
            training_valid=None
            if remaining>=2:
                remaining-=2
                training_answers=[self.engine.ask(query_from_row(self.repo,self.repo.assertion(aid)))
                                  for aid in hypothesis['training']]
                training_valid=all(a.status==YES and not a.budget_exhausted for a in training_answers)
            rows = self.repo.assertions('instance_of', obj=('c', hypothesis['kind']), polarity=True)
            members = sorted({r['subj_id'] for r in rows if r['subj_type']=='e'})
            tests = []
            for eid in members:
                if eid == hypothesis['training_entity']:
                    continue
                if remaining < 2:
                    break
                remaining -= 2
                membership=self.engine.ask(Query('is_a',('e',eid),('c',hypothesis['kind'])))
                answer = self.engine.ask(Query('property', ('e', eid), properties=(hypothesis['property'],)))
                status=answer.status if membership.status==YES else CONFLICTING if membership.status==CONFLICTING else UNKNOWN
                tests.append(dict(entity=eid, status=status, proofs=[asdict(p) for p in answer.support],
                                  membership_proofs=[asdict(p) for p in membership.support],
                                  against=[asdict(p) for p in answer.against], budget_exhausted=answer.budget_exhausted or membership.budget_exhausted))
                name=realizer.term_text(self.repo,'e',eid,definite=True)
                if status==NO:
                    tests[-1]['explanation']=name+' is recorded as not '+self.repo.concept_lemma(hypothesis['property'])
                elif status==CONFLICTING:
                    tests[-1]['explanation']='the recorded category or property claims about '+name+' conflict'
            hypothesis['tests'] = tests
            hypothesis['counterexamples'] = [t for t in tests if t['status'] in (NO, CONFLICTING)]
            good = [t for t in tests if t['status']==YES and not t['budget_exhausted'] and
                    any(p['confidence']>=.75 for p in t['membership_proofs']) and
                    any(p['steps'] and not set(p['steps']) & set(hypothesis['training']) for p in t['proofs'])]
            covered = len(tests) == max(0, len(members)-1)
            if hypothesis['counterexamples'] or training_valid is False:
                hypothesis['status'] = 'rejected'
            elif good and covered and training_valid is True:
                hypothesis['status'] = 'provisional-supported'
                hypothesis['retained'] = True
            else:
                hypothesis['status'] = 'insufficient-evidence'
            hypothesis['coverage'] = dict(tested=len(tests), available=max(0, len(members)-1), complete=covered)
            hypothesis['caution'] = 'An empirical proposal, not a universal fact or a causal explanation.'
            # A falsified universal implication need not invalidate a useful
            # intersection: playful dogs can exist even if not every dog plays.
            concept=next(c for c in state['concepts'] if c['criteria'][0]['concept']==hypothesis['kind']
                         and c['criteria'][1]['concept']==hypothesis['property'])
            concept['members']=([hypothesis['training_entity']] if training_valid else [])+[t['entity'] for t in good]
            concept['status']='grounded-working-concept' if len(concept['members'])>=2 else 'insufficient-evidence'
            concept['retained']=len(concept['members'])>=2


def explain(state, repo, pick=None):
    """Use the same substantive findings for explicit inspection and automatic delivery."""
    from reasoning.investigation_results import findings
    parts=[f"Investigation #{state['id']}: {state['objective']} ({state['phase']}, {state['passes']} passes).",
           findings(state,pick=pick)]
    ids=[str(item['id']) for item in state.get('evidence',[]) if repo.assertion(item['id']) is not None]
    if ids:parts.append('Evidence assertion IDs: '+', '.join(ids)+'.')
    if state.get('children'):
        parts.append('Linked premise investigations: '+', '.join('#'+str(i) for i in state['children'])+'.')
    return '\n\n'.join(parts)
