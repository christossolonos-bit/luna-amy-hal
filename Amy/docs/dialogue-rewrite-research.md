# Dialogue diversity: research and acceptance checks

The user's complaint is repeated substantive wording, not a prohibition on the
ordinary words needed to speak English. A greeting plus a repeated question plus
an additional tail is one reused core, not a genuinely new response.

## Primary references

- Manning, Raghavan and Schütze, *Introduction to Information Retrieval*, section
  [Near-duplicates and shingling](https://nlp.stanford.edu/IR-book/html/htmledition/near-duplicates-and-shingling-1.html):
  consecutive token shingles and their Jaccard overlap provide a direct lexical
  near-duplicate measure. MinHash approximates that overlap for scale; it is not
  semantic validation. This project uses exact token sets rather than probabilistic
  hashing for the candidate pairs it examines.
- [Measuring and Improving Semantic Diversity of Dialogue Generation](https://aclanthology.org/2022.findings-emnlp.66/):
  distinguishes the semantic aspects of dialogue diversity from merely varied
  vocabulary. This audit therefore does not describe an n-gram pass as a proof
  of distinct meaning or conversation quality.
- [Trading Off Diversity and Quality in Natural Language Generation](https://aclanthology.org/2021.humeval-1.3/):
  studies quality/diversity tradeoffs. The implication for this task is editorial:
  raising a count by increasing awkward or irrelevant outputs is not success.
- [Standardizing the Measurement of Text Diversity: A Tool and Comparative Analysis](https://aclanthology.org/2025.ijcnlp-demo.5/):
  compares diversity measures and recommends complementary measures rather than
  a single score. Our layered checks similarly separate exact reuse, clause reuse,
  padded containment, and lexical similarity.

## Implemented audit

`tools/dialogue_quality.py` provides `normalize`, `substantive_clauses`,
`audit_bank`, and `audit_banks`. It runs offline with the standard library.

1. Normalize Unicode, punctuation, case, curly apostrophes and common contractions.
2. Neutralize runtime placeholders; optionally neutralize supplied actual names.
3. Reject duplicate normalized full responses. Names or contraction changes do
   not count as new variants.
4. Index substantial standalone clauses and questions. A question of at least
   four tokens with at least two content tokens counts; ordinary greetings such
   as `Hello{name}` do not. Ordinary statements need at least five tokens.
5. Reject full-core token containment for cores of at least five tokens, catching
   different introductions or appended tails.
6. Flag high 3-shingle Jaccard overlap (default 0.72), or very high token-sequence
   similarity with shared shingles. These are conservative project heuristics,
   not thresholds promised by the research as universally valid.
7. Optionally check explicit yes/no/unknown intent labels against simple surface
   polarity cues. This catches blatant reversals, not general entailment errors.
8. Report repeated substantive clauses across families separately, so moving a
   stock question between greeting and thanks does not hide the reuse.

Findings retain 1-based pair indices and the repeated clause or similarity score.
An explicit allowlist can exempt a required factual clause; there is no broad
default exception that silently excuses canned questions. Exact and clause
indexes are exhaustive. Near-pair candidate and posting caps are disclosed in
the report, so a large run is not falsely described as exhaustive pairwise review.

## Runtime recommendations

- Keep a per-speaker history of normalized *rendered* replies and substantive
  clause fingerprints across families, not just a separate deck for each key.
- Persist that history in a separate state store only if the user authorizes the
  additional persistence. Loading a new process must not casually reset a promise
  of long-term nonrepetition.
- Reject candidates sharing a recently spoken substantial clause before ranking
  by relevance, mood and novelty. If no suitable candidate remains, report
  exhaustion to diagnostics instead of secretly recycling a padded variant.
- Keep factual confidence separate from conversational tone. Never replace an
  unknown answer with an affirmative one to obtain different wording.
- Use transcript-to-reply conversation checks. Correct recognized words should
  survive wake handling and produce a response appropriate to the intended act.

## The tripling target

The old 12,840-entry inventory is not 12,840 independently written ideas: it
includes thousands of prefix/core/tail products, raw templates, and source-group
overlaps. Retaining that padded baseline as a strict count target while demanding
zero recognizable core reuse creates a real quality/count conflict.

A single-sentence compositional grammar can improve the earlier disconnected
sentence product. For example, valid verb/object choices and alternate argument
structures can express a clarification request without a fixed apology sentence.
But a product that changes only an adjective, a polite auxiliary, or an optional
adjunct will still produce near duplicates under the stated acceptance rule.
There is no research-backed guarantee that 13,824 naturally distinct generic
clarification utterances can be obtained by a small synonym grid. The threshold
must not be weakened solely to make that number pass. Report the generated count,
accepted count, quality findings, and any shortfall separately. Human review is
still needed for naturalness, semantic preservation and conversational fit.

## Known routes outside the main catalogs

- `Understanding.make_sense` has inline question/statement gist banks and
  missing-origin replies.
- `Conversation._compose`, partial-remainder handling and repeated-question
  handling contain inline banks or parameterized sentence constructions.
- `VoiceService.on_utterance` has a wake-only attention bank, separate from
  greetings; confusing these was the observed greeting bug.
- `Exposition.INTRO` holds paragraph introductions.
- `Planner.build` constructs clauses and learned phrasings rather than taking
  every output through a phrase-bank key.
- `SpeechActs`, `Acts`, self explanations, recall, guide, web and news handlers
  contain literals and parameterized returns. Exact-literal catalog matching
  cannot cover all composed outputs from these paths.

These routes need either the same editorial data/quality gates or a runtime
rendered-clause reuse check. Increasing only `AMY_SOCIAL` does not cover them.
