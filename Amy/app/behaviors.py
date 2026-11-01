"""Non-secret dashboard switches, read at use time without restarting the bot."""
import json
import os
from pathlib import Path
import threading

ROOT=Path(__file__).resolve().parents[1]
LABELS={
    'greetings':'Reply to greetings',
    'farewells':'Reply to goodbyes',
    'curiosity':'Ask conversational follow-up questions',
    'thought_posts':'Post automatic background reflections',
    'progress_updates':'Narrate investigation progress',
}
LOCK=threading.Lock()


class BehaviorSwitches:
    def __init__(self,path=None):
        self.path=Path(path) if path else ROOT/'data'/'behavior-switches.json'

    def values(self):
        defaults={key:True for key in LABELS}
        try:
            saved=json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(saved,dict):
                defaults.update({k:v for k,v in saved.items() if k in defaults and type(v) is bool})
        except (OSError,ValueError):pass
        return defaults

    def enabled(self,key):
        return self.values().get(key,True)

    def update(self,patch):
        if not isinstance(patch,dict) or any(k not in LABELS or type(v) is not bool for k,v in patch.items()):
            raise ValueError('Behavior switches must be known boolean values')
        with LOCK:
            values=self.values()
            values.update(patch)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(values,indent=2)+'\n',encoding='utf-8')
            os.replace(temp,self.path)
        return self.settings()

    def settings(self):
        return [{'key':k,'label':LABELS[k],'enabled':v} for k,v in self.values().items()]


def enabled(key):
    return BehaviorSwitches().enabled(key)
