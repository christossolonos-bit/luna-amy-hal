"""Bounded reflection over a private knowledge snapshot; results are evidence, not new facts."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import sqlite3
import time

from dialogue import realizer
from nlp.lexicon import article
from nlp.wordnet import WordNet
from reasoning.engine import Engine, query_from_row
from reasoning.proofs import CONFLICTING, NO, UNKNOWN, YES, Query
from reasoning import thought_phrasing


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def revision(repo):
    c = repo.conn
    return [c.execute('SELECT COALESCE(MAX(id),0) FROM assertions').fetchone()[0],
            c.execute('SELECT COALESCE(MAX(id),0) FROM corrections').fetchone()[0],
            c.execute('SELECT COALESCE(MAX(id),0) FROM learned_rules').fetchone()[0]]


@dataclass
class ThoughtChain:
    key: str
    kind: str
    topic: str
    premises: list[int]
    conclusion: str
    question: str
    confidence: float
    rules: list[int] = field(default_factory=list)
    lexical_path: list[str] = field(default_factory=list)
    hypothesis: str | None = None
    knowledge_revision: list[int] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    concept: str | None = None
    episodes: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    # Fields for spoken wording; `question` stays canonical for delivery bookkeeping.
    ask: dict = field(default_factory=dict)


class ThoughtStore:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), timeout=0.15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS thought_chains(
                key TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS thought_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS thought_delivery(
                speaker TEXT NOT NULL,question TEXT NOT NULL,delivered REAL NOT NULL,
                PRIMARY KEY(speaker,question));
        ''')
        self.conn.commit()

    def close(self):
        self.conn.close()

    def get(self, key, default=None):
        row = self.conn.execute('SELECT value FROM thought_meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.conn:
            self.conn.execute('INSERT INTO thought_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (key, json.dumps(value)))

    def publish(self, chain):
        with self.conn:
            self.conn.execute('INSERT INTO thought_chains VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET '
                              'payload=excluded.payload,created=excluded.created,status=excluded.status',
                              (chain.key, json.dumps(asdict(chain)), chain.created,
                               'ready' if chain.kind=='concept-reflection' else 'internal'))

    def ready(self, limit=30):
        rows = self.conn.execute("SELECT payload FROM thought_chains WHERE status='ready' ORDER BY created DESC LIMIT ?", (limit,))
        return [ThoughtChain(**json.loads(r[0])) for r in rows]

    def recent(self, limit=30):
        rows = self.conn.execute('SELECT payload FROM thought_chains ORDER BY created DESC LIMIT ?', (limit,))
        return [ThoughtChain(**json.loads(r[0])) for r in rows]

    def delivered(self, speaker, question):
        return self.conn.execute('SELECT 1 FROM thought_delivery WHERE speaker=? AND question=?',
                                 (speaker, signature(question.casefold()))).fetchone() is not None

    def mark_delivered(self, speaker, chain, now=None):
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO thought_delivery VALUES(?,?,?)',
                              (speaker, signature(chain.question.casefold()), now or time.time()))

    def last_delivery(self, speaker):
        return self.conn.execute('SELECT COALESCE(MAX(delivered),0) FROM thought_delivery WHERE speaker=?', (speaker,)).fetchone()[0]


class ThinkingEngine:
    def __init__(self, repo, store, wordnet=None, max_steps=1500, max_depth=8):
        self.repo, self.store = repo, store
        self.wordnet = wordnet or WordNet()
        self.engine = Engine(repo, max_depth=max_depth, budget=max_steps)

    def _safe(self, row):
        return row['status'] == 'active' and row['confidence'] >= .75 and \
               self.engine.ask(query_from_row(self.repo, row)).status != CONFLICTING

    def _name(self, eid):
        row = self.repo.entity_row(eid)
        return row['name'] if row else ''

    def _pattern_query(self, pattern, eid):
        # The existing reasoner provides the exact rule-variable substitution.
        return self.engine._instantiate(pattern, ('e', eid))

    def _publish(self, kind, topic, premises, conclusion, question, confidence, rules=(), path=(), hypothesis=None, ask=None):
        chain = ThoughtChain(signature([kind, topic, conclusion, question, sorted(premises), sorted(rules)]),
                             kind, topic, list(dict.fromkeys(premises)), conclusion, question, confidence,
                             list(rules), list(path), hypothesis, revision(self.repo), ask=ask or {})
        if chain.premises and chain.question and chain.conclusion:
            self.store.publish(chain)
            return chain
        return None

    def _rule_connections(self, entities, query_budget):
        rules = self.repo.active_rules()
        patterns=[(rule,pattern) for rule in rules for pattern in rule['conclusions']]
        if not patterns or not entities:
            return []
        cursors=self.store.get('rule_cursors',{})
        selected=[]
        for _ in range(query_budget):
            eid=min(entities,key=lambda ident:(cursors.get(str(ident),0),ident))
            offset=cursors.get(str(eid),0)
            rule,pattern=patterns[offset%len(patterns)]
            selected.append((eid,rule,pattern))
            cursors[str(eid)]=offset+1
        self.store.set('rule_cursors',cursors)
        out = []
        for eid, rule, pattern in selected:
            query = self._pattern_query(pattern, eid)
            answer = self.engine.ask(query)
            if answer.status not in (YES, NO) or answer.budget_exhausted:
                continue
            proofs = answer.support if pattern.get('polarity', True) else answer.against
            for proof in proofs[:1]:
                if not proof.learned_rules or not proof.steps:
                    continue
                conclusion = realizer.derived_text(self.repo, proof)
                if not conclusion:
                    continue
                topic = self._name(eid)
                # The rule licenses an implication, not a physical causal claim.
                text = realizer.rule_text(self.repo, rule)
                question = f"Does the rule '{text}' have any exceptions?"
                chain = self._publish('rule-chain', topic, proof.steps, conclusion, question,
                                      proof.confidence, proof.learned_rules, ask={'kind': 'rule', 'rule': text})
                if chain:
                    out.append(chain)
        return out

    def _lexical_connections(self, rows, budget):
        out = []
        for row in rows:
            if len(out) >= budget:
                break
            if row['predicate'] != 'instance_of' or not row['polarity'] or row['subj_type'] != 'e' \
                    or not self.repo.entity_row(row['subj_id'])['proper'] or not self._safe(row):
                continue
            kind = self.repo.concept_lemma(row['obj_id'])
            context = ' '.join(t for r in self.repo.assertions('rel:%', subj=('e',row['subj_id']))
                               if (t := realizer.realize_assertion(self.repo,r)))
            selection = self.wordnet.select(kind, context)
            if selection.sense is None:
                continue
            paths = self.wordnet.ancestors(selection.sense.id, depth=8, budget=90)
            for ident, path in paths.items():
                if len(path) < 3:
                    continue
                target = self.wordnet.sense(ident)
                if target is None or target.lemma in ('entity','physical entity','object','whole','unit','being','organism','abstraction'):
                    continue
                cid = self.repo.find_concept(target.lemma)
                if cid:
                    known = self.engine.ask(Query('is_a',('e',row['subj_id']),('c',cid)))
                    if known.status in (NO,CONFLICTING):
                        continue
                if not self.wordnet.verify_path(path):
                    continue
                name = self._name(row['subj_id'])
                conclusion = f"{name} is {article(target.lemma)} {target.lemma}"
                question = f"What else have you noticed about {name} as {article(kind)} {kind}?"
                chain=self._publish('category-chain',name,[row['id']],conclusion,question,
                                    min(row['confidence'],selection.confidence),path=path,
                                    ask={'kind':'category','name':name,'a_kind':f"{article(kind)} {kind}"})
                if chain:out.append(chain)
                break
        return out

    def _shared_connections(self, rows, budget):
        """Shared properties are observations, not evidence of shared preferences."""
        out=[]
        for row in rows:
            if len(out)>=budget:break
            if row['subj_type']!='e' or row['predicate'] not in ('has_property','instance_of') or not row['polarity'] \
                    or not self.repo.entity_row(row['subj_id'])['proper'] or not self._safe(row):
                continue
            peers=self.repo.assertions(row['predicate'],obj=(row['obj_type'],row['obj_id']),polarity=True)
            peer=next((r for r in peers if r['subj_type']=='e' and r['subj_id']!=row['subj_id'] \
                       and self.repo.entity_row(r['subj_id'])['proper'] and self._safe(r)),None)
            if peer is None:continue
            left,right=sorted((row['subj_id'],peer['subj_id']))
            a,b=self._name(left),self._name(right)
            if not a or not b:continue
            value=self.repo.concept_lemma(row['obj_id'])
            predicate=f"are both {value}" if row['predicate']=='has_property' else f"both belong to the category {value}"
            hypothesis=f"Sharing the category {value} may suggest something worth comparing; it does not establish that they share other traits."
            question=f"What differences have you noticed between {a} and {b}?"
            chain=self._publish('shared-feature',a,[row['id'],peer['id']],f"{a} and {b} {predicate}",
                                question,min(row['confidence'],peer['confidence']),hypothesis=hypothesis,
                                ask={'kind':'differences','a':a,'b':b,'value':value})
            if chain:out.append(chain)
        return out

    def _conflicts(self, rows, budget):
        out=[]
        for row in rows:
            if len(out)>=budget:break
            answer=self.engine.ask(query_from_row(self.repo,row))
            if answer.status!=CONFLICTING:continue
            ids=list(dict.fromkeys(a for p in answer.support[:1]+answer.against[:1] for a in p.steps))
            name=self._name(row['subj_id']) if row['subj_type']=='e' else self.repo.concept_lemma(row['subj_id'])
            claim=realizer.realize_assertion(self.repo,row)
            if not claim:continue
            chain=self._publish('conflict',name,ids,f"I have both supporting and opposing claims about whether {claim}",
                                f"Which information about whether {claim} should be treated as current?",1.0,
                                ask={'kind':'current','claim':claim})
            if chain:out.append(chain)
        return out

    def cycle(self, batch=24, query_budget=8):
        """Advance an agenda through all active facts over successive bounded cycles."""
        cursor=self.store.get('fact_cursor',0)
        rows=self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND source_utterance_id IS NOT NULL "
                                    "AND id>? ORDER BY id LIMIT ?",(cursor,batch)).fetchall()
        if not rows:
            rows=self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND source_utterance_id IS NOT NULL "
                                        "ORDER BY id LIMIT ?",(batch,)).fetchall()
        if rows:self.store.set('fact_cursor',rows[-1]['id'])
        from reasoning.concepts import ConceptReflection
        reflection=ConceptReflection(self.repo,self.store)
        reflection.episodes.ingest(self.repo)
        concept=reflection.choose(rows)
        entities=sorted({r['subj_id'] for r in rows if r['subj_type']=='e' and self.repo.entity_row(r['subj_id'])['proper']})
        chains=self._rule_connections(entities,query_budget)
        chains+=self._lexical_connections(rows,3)
        chains+=self._shared_connections(rows,3)
        chains+=self._conflicts(rows,2)
        dossier=reflection.build(concept,chains)
        if dossier and len(dossier.facts)>=2 and (dossier.supported_connections or len(dossier.episode_keys)>=2):
            question=dossier.questions[0]
            ask=next((dict(c.get('ask') or {}) for c in dossier.supported_connections if c['question']==question),None)                 or {'kind':'distinguish' if len(dossier.episode_keys)>1 else 'meaning','concept':concept}
            hypothesis=dossier.hypotheses[0] if dossier.hypotheses else None
            value=next((c['ask'].get('value') for c in dossier.supported_connections
                        if c.get('hypothesis')==hypothesis and c.get('ask')),None)
            ask.pop('value',None)
            if value:ask['value']=value
            if dossier.supported_connections:
                strongest=max(dossier.supported_connections,key=lambda c:(len(c['premises'])+len(c['rules']),c['confidence']))
                premises=list(dict.fromkeys([*strongest['premises'],*dossier.facts[:6]]))
                conclusion=strongest['conclusion']
                confidence=strongest['confidence']
                rules=strongest['rules']
                path=strongest['lexical_path']
            else:
                premises=dossier.facts[:6]
                conclusion=f'these separate accounts all concern {concept}, without establishing a general rule'
                confidence=min(self.repo.assertion(aid)['confidence'] for aid in premises)
                rules,path=[],[]
            chain=ThoughtChain(signature(['concept',concept,premises,conclusion,question]),'concept-reflection',concept,
                               premises,conclusion,question,confidence,rules,path,
                               hypothesis,revision(self.repo),
                               concept=concept,episodes=dossier.episode_keys,steps=dossier.supported_connections,ask=ask)
            self.store.publish(chain)
            chains.append(chain)
        self.store.set('status',{'last_cycle':time.time(),'scanned':len(rows),'cursor':rows[-1]['id'] if rows else 0,
                                 'connections':len(chains),'knowledge_revision':revision(self.repo)})
        return chains


def valid_chain(chain, repo):
    """Main process performs only bounded SQL validation, never heavy reasoning."""
    if chain.knowledge_revision!=revision(repo):
        return False
    for aid in chain.premises:
        row=repo.assertion(aid)
        if row is None or row['status']!='active':return False
    for rid in chain.rules:
        rule=repo.rule(rid)
        if rule is None or rule['status']!='active':return False
    return bool(chain.premises)


def explain_chain(chain, repo, pick=thought_phrasing.default_pick):
    say=lambda key,**fields:thought_phrasing.say(pick,key,**fields)
    ask=chain.ask or {}
    premises=list(dict.fromkeys(t for aid in chain.premises if (t:=realizer.realize_assertion(repo,repo.assertion(aid)))))
    if chain.kind=='conflict':
        conclusion=say('conflict',claim=ask['claim']) if 'claim' in ask else chain.conclusion
        body=realizer.sentence(realizer.join_clauses(premises))+" "+realizer.sentence(conclusion)
    else:
        evidence=realizer.join_clauses(premises)
        if chain.rules:
            rules=realizer.join_clauses([realizer.rule_text(repo,repo.rule(rid)) for rid in chain.rules])
            evidence=realizer.join_clauses([evidence,rules])
        body=realizer.sentence(say('inference',e=evidence,c=chain.conclusion))
        if chain.confidence<.75:body=say('tentative')+" "+body
    if chain.kind=='concept-reflection' and chain.episodes:
        body=f"I'm examining {chain.concept or chain.topic} across {len(chain.episodes)} recorded conversation or reading episodes. "+body
        additional=list(dict.fromkeys(step['conclusion'] for step in chain.steps
                                      if step.get('conclusion') and step['conclusion']!=chain.conclusion))[:2]
        if additional:
            body+=' '+realizer.sentence('Another supported connection is '+realizer.join_clauses(additional))
    if chain.hypothesis:
        body+=" "+(say('shared_caution',value=ask['value']) if ask.get('value') else chain.hypothesis)
    return body+" "+thought_phrasing.question(pick,chain)
