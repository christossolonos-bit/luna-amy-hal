"""Concept dossiers grounded in conversation/reading episodes, not invented experiences."""

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re

from dialogue import realizer
from nlp.wordnet import tokens


@dataclass
class Episode:
    key: str
    kind: str
    speakers: list[str]
    utterances: list[int]
    started: str
    ended: str
    topics: list[str]


@dataclass
class ConceptDossier:
    concept: str
    episode_keys: list[str]
    facts: list[int]
    supported_connections: list[dict]
    questions: list[str]
    hypotheses: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)


class EpisodeIndex:
    def __init__(self,store):
        self.store=store
        store.conn.executescript('''
            CREATE TABLE IF NOT EXISTS thought_episodes(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS concept_dossiers(concept TEXT PRIMARY KEY,payload TEXT NOT NULL);
        ''')
        store.conn.commit()

    def ingest(self,repo,batch=100):
        cursor=self.store.get('episode_cursor',0)
        rows=repo.conn.execute('SELECT id,speaker,text,created_at FROM utterances WHERE id>? ORDER BY id LIMIT ?',
                               (cursor,batch)).fetchall()
        for row in rows:
            source=row['speaker'] or 'unknown'
            kind='reading' if source.startswith('source:') else 'conversation'
            # Only actual logged inputs form episodes. Generated replies are not evidence.
            try:
                when=datetime.fromisoformat(row['created_at'].replace('Z','+00:00'))
                if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
                bucket=int(when.timestamp()//900)
            except ValueError:
                bucket=row['id']//20
            key=f'{kind}:{source if kind=="reading" else "conversation"}:{bucket}'
            old=self.store.conn.execute('SELECT payload FROM thought_episodes WHERE key=?',(key,)).fetchone()
            episode=Episode(**json.loads(old[0])) if old else Episode(key,kind,[],[],row['created_at'],row['created_at'],[])
            if source not in episode.speakers:episode.speakers.append(source)
            episode.utterances.append(row['id'])
            episode.ended=row['created_at']
            episode.topics=sorted(set(episode.topics)|tokens(row['text']))
            with self.store.conn:
                self.store.conn.execute('INSERT INTO thought_episodes VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                                        (key,json.dumps(asdict(episode))))
        if rows:self.store.set('episode_cursor',rows[-1]['id'])
        return len(rows)

    def relevant(self,concept,limit=12):
        wanted=tokens(concept)
        episodes=[]
        # Indexing is local to the independent queue DB; never a query on the bot's fact store.
        for row in self.store.conn.execute('SELECT payload FROM thought_episodes ORDER BY rowid DESC'):
            episode=Episode(**json.loads(row[0]))
            if wanted & set(episode.topics):episodes.append(episode)
            if len(episodes)>=limit:break
        return episodes

    def save(self,dossier):
        with self.store.conn:
            self.store.conn.execute('INSERT INTO concept_dossiers VALUES(?,?) ON CONFLICT(concept) DO UPDATE SET payload=excluded.payload',
                                    (dossier.concept,json.dumps(asdict(dossier))))


class ConceptReflection:
    def __init__(self,repo,store):
        self.repo,self.store=repo,store
        self.episodes=EpisodeIndex(store)

    def choose(self,rows):
        requested=self.store.get('requested_focus')
        if isinstance(requested,str) and requested.strip():return requested.strip()
        concepts=[]
        for row in rows:
            if row['predicate'].startswith('rel:'):concepts.append(row['predicate'][4:])
            if row['obj_type']=='c':
                name=self.repo.concept_lemma(row['obj_id'])
                if name!='_':concepts.append(name)
        if not concepts:return None
        frequency=Counter(concepts)
        last=self.store.get('concept_visits',{})
        concept=min(frequency,key=lambda word:(last.get(word,0),-frequency[word],word))
        last[concept]=last.get(concept,0)+1
        self.store.set('concept_visits',last)
        return concept

    def build(self,concept,chains):
        if not concept:return None
        episodes=self.episodes.relevant(concept)
        ids={uid for episode in episodes for uid in episode.utterances}
        facts=[]
        if ids:
            marks=','.join('?' for _ in ids)
            facts=self.repo.conn.execute(f"SELECT * FROM assertions WHERE status='active' AND source_utterance_id IN ({marks}) "
                                         'ORDER BY id DESC LIMIT 60',tuple(ids)).fetchall()
        wanted=tokens(concept)
        relevant=[]
        for row in facts:
            text=realizer.realize_assertion(self.repo,row)
            if text and (wanted & tokens(text) or row['predicate']==f'rel:{concept}'):
                relevant.append(row)
        fact_ids={r['id'] for r in relevant}
        connections=[asdict(chain) for chain in chains if fact_ids & set(chain.premises)]
        questions=list(dict.fromkeys(chain['question'] for chain in connections))
        if not questions:
            questions=[f'What would help distinguish the different accounts of {concept}?'] if len(episodes)>1 else [f'What does {concept} mean in this situation?']
        # A new connection remains a proposal until its own evidence establishes it.
        hypotheses=list(dict.fromkeys(chain['hypothesis'] for chain in connections if chain.get('hypothesis')))
        dossier=ConceptDossier(concept,[e.key for e in episodes],sorted(fact_ids),connections,questions,hypotheses,
                               ['identify concept','retrieve episodes','collect active evidence','check connections','identify open question'])
        self.episodes.save(dossier)
        return dossier

    def episode_labels(self,keys):
        out=[]
        for key in keys:
            row=self.store.conn.execute('SELECT payload FROM thought_episodes WHERE key=?',(key,)).fetchone()
            if not row:continue
            e=Episode(**json.loads(row[0]))
            if e.kind=='reading':out.append('a reading episode')
            else:out.append('a conversation with '+realizer.join_clauses(e.speakers))
        return out
