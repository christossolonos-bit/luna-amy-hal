"""User-facing findings, including useful partial results at every stopping condition."""


def findings(state,pick=None):
    """Render committed evidence, never turn lexical candidates into proven facts."""
    parts=[]
    for conclusion in state.get('conclusions',[]):
        if conclusion.get('text') and conclusion.get('status') in ('yes','no'):
            parts.append('The evidence supports '+conclusion['text'].rstrip('.')+'.')
            if conclusion.get('supporting_facts'):
                parts.append('That follows from '+ '; '.join(conclusion['supporting_facts'])+'.')
        elif conclusion.get('status')=='conflicting':
            parts.append('The recorded evidence supports opposing answers, so I cannot settle the question yet.')
    definitions=state.get('definitions',[])
    phrase=[d for d in definitions if d.get('scope')=='phrase']
    choices=phrase or definitions
    seen=set()
    for definition in choices:
        text=definition['definition'].rstrip('.')
        if text in seen:continue
        seen.add(text)
        if pick:
            from dialogue.investigation_wording import say
            line=say(pick,'meaning',term=definition['term'],definition=text)
            parts.append(line or '"'+definition['term']+'": '+text+'.')
        else:
            parts.append('One dictionary meaning of "'+definition['term']+'" is '+text+'.')
    if choices:
        parts.append('That describes a possible meaning; it does not by itself establish who satisfies it.')
    evidence=state.get('evidence',[])
    if evidence:
        parts.append('The relevant recorded claims include '+ '; '.join(
            e['text'].rstrip('.')+' (from '+str(e.get('source') or 'an unspecified source')+')' for e in evidence[:3])+'.')
    for connection in state.get('connections',[])[:3]:
        parts.append('An established rule connects these facts: '+connection['text'].rstrip('.')+'.')
    for hypothesis in state.get('hypotheses',[])[:3]:
        status=hypothesis.get('status')
        if status=='rejected':
            counter=hypothesis.get('counterexamples',[])
            why=counter[0].get('explanation') if counter else None
            parts.append('I rejected the proposal "'+hypothesis['label']+'"'+(' because '+why.rstrip('.') if why else ' because its evidence checks did not hold')+'.')
        elif status=='provisional-supported':
            parts.append('The pattern "'+hypothesis['label']+'" survived the checked cases, but remains provisional rather than a general fact.')
        elif status=='insufficient-evidence':
            parts.append('The proposal "'+hypothesis['label']+'" remains unconfirmed; the checked evidence is insufficient.')
    mechanism=state.get('mechanisms')
    if mechanism:
        values=', '.join(name+'='+str(round(value,3)) for name,value in mechanism.get('state_variables',{}).items()
                        if isinstance(value,(int,float)))
        if pick:
            from dialogue.investigation_wording import say
            line=say(pick,'state',values=values)
            parts.append(line or values+'.')
        else:parts.append('My current state readings are '+values+'.')
    if not parts:
        parts.append('I have not established a supported finding for this question yet.')
    gaps=state.get('gaps',[])
    if gaps:
        parts.append('What remains unresolved: '+gaps[0]['question'])
    elif not state.get('conclusions') and state.get('phase') in ('waiting','limited'):
        parts.append('I cannot yet establish an answer to the original question from these findings.')
    if state.get('phase')=='limited':
        parts.append('I stopped at the investigation work limit. These are partial findings, not a completed answer.')
    elif state.get('phase')=='waiting':
        parts.append('I have reached the available evidence limit, so I need the missing evidence or criteria before drawing a stronger conclusion.')
    return '\n\n'.join(parts)
