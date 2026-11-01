"""Offline wording audit: count is not evidence of conversational diversity.

Audits exact normalized text, repeated clauses, padded containment and token
shingles. Deterministic lexical checks cannot certify semantic equivalence or
naturalness. Findings use 1-based entry indices for direct editorial review.
"""

from collections import Counter, defaultdict
from difflib import SequenceMatcher
import argparse
import json
from pathlib import Path
import re
import unicodedata

CONTRACTIONS = {"i'm": "i am", "i've": "i have", "i'll": "i will", "i'd": "i would",
                "you're": "you are", "you've": "you have", "you'll": "you will",
                "we're": "we are", "we've": "we have", "we'll": "we will",
                "they're": "they are", "it's": "it is", "that's": "that is",
                "there's": "there is", "what's": "what is", "who's": "who is",
                "can't": "cannot", "won't": "will not", "don't": "do not",
                "doesn't": "does not", "didn't": "did not", "haven't": "have not",
                "hasn't": "has not", "isn't": "is not", "aren't": "are not",
                "wasn't": "was not", "weren't": "were not", "couldn't": "could not",
                "wouldn't": "would not", "shouldn't": "should not", "let's": "let us"}
STOP = set("a an the i you we he she it they am is are was were be been do does did have has had "
           "to of in on at for with and or but that this those these my your our their me us "
           "can could will would shall should may might what how which who where when why slot".split())


def normalize(text, names=()):
    text = unicodedata.normalize('NFKC', text).casefold().replace('’', "'").replace('‘', "'")
    # Runtime fields don't count as unique wording; never evaluate expressions.
    text = re.sub(r'\{[^{}]*\}', ' slot ', text)
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(r'(?<!\w)' + re.escape(name.casefold()) + r'(?!\w)', 'slot', text)
    for old, new in CONTRACTIONS.items():
        text = re.sub(r'(?<!\w)' + re.escape(old) + r'(?!\w)', new, text)
    return ' '.join(re.findall(r"[\w]+", text))


def clauses(text, names=(), allow=()):
    """Substantial exact clauses; trivial salutations/function words are not evidence."""
    allowed = {normalize(p, names) for p in allow}
    out = []
    # Preserve whether each clause was a question for a slightly shorter threshold.
    for match in re.finditer(r'([^.!?;]+)([.!?;]|$)', text):
        clause = normalize(match[1], names)
        words = clause.split()
        substantial = len(words) >= (4 if match[2] == '?' else 5)
        if substantial and len(set(words) - STOP) >= 2 and clause not in allowed:
            out.append(clause)
    return tuple(dict.fromkeys(out))


def substantive_clauses(text, names=(), allow=()):
    """Stable normalized clause fingerprints, suitable for a runtime history set."""
    return clauses(text, names, allow)


def shingles(text, size=3):
    words = text.split()
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def _polarity(text):
    n = normalize(text)
    if re.match(r'^(?:yes|certainly|definitely|indeed)\b', n):
        return 'yes'
    if re.match(r'^(?:no|nope)\b', n) and not n.startswith('no problem'):
        return 'no'
    if re.search(r'\b(?:i do not know|i am not sure|i have not been told|i lack evidence)\b', n):
        return 'unknown'
    return None


def audit_bank(lines, *, name='', names=(), allow_clauses=(), intent=None,
               minimum=None, max_findings=100, near_threshold=0.72, candidate_limit=256):
    """Report lexical defects with bounded near-duplicate candidate generation.

    Exact duplicate and repeated-clause indexes cover all entries. Up to 256
    shingle-neighbor candidates per entry receive pairwise near checks; reports
    disclose pruning. This is not a semantic correctness or grammar checker.
    """
    lines = [str(s) for s in lines]
    norms = [normalize(s, names) for s in lines]
    counts, findings = Counter(), []
    def finding(kind, i, j=None, **extra):
        counts[kind] += 1
        if len(findings) < max_findings:
            item = {'kind': kind, 'first': i + 1, **extra}
            if j is not None:
                item['second'] = j + 1
            findings.append(item)
    seen, clause_seen, index = {}, {}, defaultdict(list)
    grams = [shingles(n) for n in norms]
    candidate_pruned = 0
    postings_pruned = 0
    checked = 0
    for i, (line, n) in enumerate(zip(lines, norms)):
        if not n:
            finding('empty', i)
        if n in seen:
            finding('normalized-duplicate', seen[n], i, evidence=n)
        else:
            seen[n] = i
        for clause in clauses(line, names, allow_clauses):
            if clause in clause_seen:
                finding('repeated-clause', clause_seen[clause], i, evidence=clause)
            else:
                clause_seen[clause] = i
        observed = _polarity(line)
        if intent in ('yes', 'no', 'unknown') and observed and observed != intent:
            finding('intent-polarity-mismatch', i, expected=intent, observed=observed)
        candidates = Counter()
        for gram in grams[i]:
            for j in index[gram]:
                candidates[j] += 1
        ranked = sorted(candidates, key=lambda j: (-candidates[j], j))
        candidate_pruned += max(0, len(ranked) - candidate_limit)
        for j in ranked[:candidate_limit]:
            if n == norms[j]:
                continue
            checked += 1
            shorter, longer = sorted((n, norms[j]), key=len)
            if len(shorter.split()) >= 5 and (' ' + shorter + ' ') in (' ' + longer + ' '):
                finding('padded-containment', j, i, evidence=shorter)
                continue
            common = grams[i] & grams[j]
            union = grams[i] | grams[j]
            score = len(common) / len(union) if union else 0
            ratio = SequenceMatcher(None, n.split(), norms[j].split(), autojunk=False).ratio()
            if len(n.split()) >= 6 and len(norms[j].split()) >= 6 and (
                    score >= near_threshold or (ratio >= .88 and len(common) >= 2)):
                finding('near-duplicate', j, i, score=round(score, 3), token_ratio=round(ratio, 3))
        for gram in grams[i]:
            if len(index[gram]) < candidate_limit:
                index[gram].append(i)
            else:
                postings_pruned += 1
    if minimum is not None and len(seen) < minimum:
        counts['below-minimum'] += 1
        findings.append({'kind':'below-minimum', 'required':minimum, 'actual':len(seen)})
    return {'name':name, 'entries':len(lines), 'distinct_normalized':len(seen),
            'passed':not counts, 'counts':dict(counts), 'findings':findings,
            'findings_truncated':sum(counts.values()) > len(findings),
            'near_pairs_checked':checked, 'near_candidates_pruned':candidate_pruned,
            'near_postings_pruned':postings_pruned,
            'limitations':['Lexical checks do not prove meaning preservation, factual correctness, or naturalness.',
                           'Exact duplicate and clause checks are exhaustive; near-pair pruning is disclosed.']}


def audit_collection(banks, **kwargs):
    results = [audit_bank(lines, name=key, **kwargs) for key, lines in banks.items()]
    # Cross-family reuse is separately visible, not hidden by intent boundaries.
    owners, reused, full_owners, full_reused = {}, [], {}, []
    repeated_clauses, repeated_responses = 0, 0
    for key, lines in banks.items():
        for i, line in enumerate(lines):
            normalized = normalize(line, kwargs.get('names', ()))
            if normalized in full_owners and full_owners[normalized][0] != key:
                repeated_responses += 1
                if len(full_reused) < 100:
                    full_reused.append({'first_family':full_owners[normalized][0],
                                        'first_entry':full_owners[normalized][1],
                                        'second_family':key,'second_entry':i+1})
            else:
                full_owners[normalized]=(key,i+1)
            for clause in clauses(line, kwargs.get('names', ()), kwargs.get('allow_clauses', ())):
                if clause in owners and owners[clause][0] != key:
                    repeated_clauses += 1
                    if len(reused) < 100:
                        reused.append({'clause':clause, 'first_family':owners[clause][0],
                                       'first_entry':owners[clause][1], 'second_family':key,'second_entry':i+1})
                else:
                    owners[clause] = (key,i+1)
    return {'passed':all(r['passed'] for r in results) and not repeated_clauses and not repeated_responses,
            'families':results, 'cross_family_reuse':reused,
            'cross_family_duplicate_responses':full_reused,
            'cross_family_clause_reuse_count':repeated_clauses,
            'cross_family_duplicate_response_count':repeated_responses,
            'total_entries':sum(r['entries'] for r in results)}


def audit_banks(banks, **kwargs):
    """Public alias used by the runtime rebuild/collection acceptance harness."""
    return audit_collection(banks, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--minimum', type=int)
    args=parser.parse_args(argv)
    data=json.loads(args.catalog.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or any(not isinstance(v,list) for v in data.values()):
        parser.error('Catalog must map family names to arrays of reply strings')
    report=audit_collection(data,minimum=args.minimum)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'families':len(report['families']),
                      'entries':report['total_entries'],
                      'failing_families':sum(not r['passed'] for r in report['families'])},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
