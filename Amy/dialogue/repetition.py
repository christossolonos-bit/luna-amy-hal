"""Persistent wording history shared by phrase selection and delivered replies."""
import hashlib
import re


def normalized(text):
    text=text.casefold().replace('’',"'")
    for short,long in (("i'm","i am"),("i've","i have"),("i'll","i will"),("don't","do not"),
                       ("didn't","did not"),("can't","cannot"),("couldn't","could not"),
                       ("haven't","have not"),("that's","that is"),("you're","you are")):
        text=text.replace(short,long)
    return ' '.join(re.findall(r'\w+',text))


def cores(text):
    # Introductory padding must not disguise the same underlying sentence.
    text=re.sub(r"^(?:let me (?:put it this way|explain|be direct|say it a little differently)|to put it plainly|here's (?:what i mean|my answer|how i'd explain that|the short version)|in other words|put another way|to be clear|what i can say is this|the way i'd put it is)\s*:\s*",'',text,flags=re.I)
    return {normalized(part) for part in re.split(r'[.!?;]+',text)
            if len(normalized(part).split())>=5}


class WordingHistory:
    def __init__(self,repo):
        self.repo=repo
        repo.conn.executescript('''
            CREATE TABLE IF NOT EXISTS wording_history(
                scope TEXT NOT NULL, fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
                PRIMARY KEY(scope,fingerprint,kind));
        ''')
        repo.conn.commit()
        # Existing replies count too, including replies spoken before this feature.
        if not self.has('*','seeded','meta'):
            for row in repo.conn.execute("SELECT text FROM dialogue_turns WHERE role='engine'").fetchall():
                self.remember('*',row['text'],'reply')
                for clause in cores(row['text']):self.remember('*',clause,'clause')
                for clause in cores(row['text']):self.remember('*',clause,'delivered-core')
            self.remember('*','seeded','meta')

    def has(self,scope,text,kind='reply'):
        key=hashlib.sha256(normalized(text).encode()).hexdigest()
        return self.repo.conn.execute('SELECT 1 FROM wording_history WHERE scope IN (?,?) AND fingerprint=? AND kind=?',
                                       (scope,'*',key,kind)).fetchone() is not None

    def remember(self,scope,text,kind='reply'):
        key=hashlib.sha256(normalized(text).encode()).hexdigest()
        with self.repo.conn:
            self.repo.conn.execute('INSERT OR IGNORE INTO wording_history VALUES(?,?,?)',(scope,key,kind))

    def available(self,scope,template,rendered):
        return not self.has(scope,template,'template') and not self.has(scope,rendered) and not self.has(scope,rendered,'selected') and not any(
            self.has(scope,clause,'clause') for clause in cores(template))

    def selected(self,scope,template,rendered):
        self.remember(scope,template,'template')
        self.remember(scope,rendered,'selected')
        for clause in cores(template):self.remember(scope,clause,'clause')

    def fresh(self,text,alternatives=()):
        """A finite set never wraps around. Exhaustion returns no spoken duplicate."""
        for candidate in (text,*alternatives):
            if candidate and not self.has('*',candidate,'delivered') and not self.has('*',candidate,'reply'):
                return candidate
        return None

    def delivered(self,text):
        self.remember('*',text,'delivered')
        for clause in cores(text):self.remember('*',clause,'delivered-core')
