# Paragraph-reading benchmark

69 fixed cases, independently annotated with 116 question/answer checks:
32 Wikipedia summaries, 9 NASA news excerpts, 20 Open English WordNet definitions,
and 8 project-authored fictional conversation paragraphs. The first 61 are sourced
material; the last 8 are synthetic scenarios, not private user transcripts.

`sources.json` retains the original Wikipedia/NASA text, URLs, retrieval times,
revision identifiers where supplied, and attribution. `cases.json` contains the
frozen inputs and gold probes; tests and evaluation never fetch fresh content.
The news is a historical snapshot, not a claim that it is current.

WordNet inputs add the headword and a copula to otherwise subjectless definitions.
The original gloss and sense ID remain in metadata. Definitions may contain dated
claims (notably the traditional nine-planet definition); the benchmark checks what
the supplied source says, not independent scientific correctness. Gloss examples
are never used as personal facts. Wikipedia content is by its contributors under
CC BY-SA; follow the retained page/revision links for attribution and history.
NASA excerpts retain their source URLs; third-party credits can apply.
WordNet is derived from Princeton WordNet, with OEWN contributions under CC BY 4.0:
https://github.com/globalwordnet/english-wordnet/blob/main/LICENSE.md

Run from the project root:

```powershell
python -m tools.convo_eval.paragraphs --report fixtures/paragraphs/report.json
python -m tools.convo_eval.paragraphs --category wikipedia --limit 5
```

Each case uses a fresh temporary database, calls `digest(text, source, confidence)`
once, asks its probes, then closes and removes the temporary database. The runner
never opens the live memory. Existing web/news flags are disabled and restored.
No language model or network access is needed. Exit codes: 0 all pass; 1 measured
failures/errors; 2 integration blocked because `Conversation.digest` is missing.
The full report records stored IDs, skipped clauses/reasons, actual replies and
probe failure reasons. An unknown reply echoing the right noun does not pass;
neither does a prior dictionary answer when digest stored no facts.

To intentionally refresh source excerpts, run `collect_sources.py`; to regenerate
gold case packaging, run `build_cases.py --wordnet-db PATH`. The human annotations
in that script must be reviewed when source texts change. Neither script is run
automatically by tests. Synthetic gold expectations do not come from Adam's parser.
