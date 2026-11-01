# Reply variation and the repeated fallback

The recent conversation log showed three questions receiving exactly:
"I didn't catch what that was about. Say it a different way, maybe shorter,
and I'll have another go." That literal return in `Understanding.make_sense`
bypassed Amy's large misunderstanding banks.

That path now uses the conversation-owned shuffled phrase deck. Each general
misunderstanding and targeted clarification bank has 4,608 templates. Every Amy
social bank and auxiliary phrase bank has at least 15 distinct templates. Short
inline banks are expanded too. No name or fact substitutions count as variants.

`dialogue/scripted_replies.json` inventories literal spoken return values from
the conversation and dialogue modules. Each has at least 15 forms, using
contractions, alternative phrasing and explanatory constructions. Common short
replies such as "Okay", "No problem" and "Take your time" have direct alternative
wordings. Some long informational scripts retain their content with different
introductions rather than paraphrasing the facts. Dynamic grammatical answers
are preserved; this catalog is not a claim that arbitrary generated facts have
15 different paraphrases.

The final response step applies the catalog only to exact canned-response
matches for Amy. It does not replace words inside facts, quoted memories, or
user text. The chosen wording is what is saved to history and sent to TTS.
Per-family shuffled decks avoid repeating a template until the family is
exhausted, and avoid an immediate repeat at the next cycle. Decks reset on restart.

Unknown personal origins get an explanation of the missing fact, rather than
being treated as an unintelligible sentence. Clarification now checks memory
independently of the wording of the fallback: changing a phrase cannot disable
the memory-first recovery path.

Maintain the literal-response catalog after adding scripts:

```powershell
python -m tools.build_scripted_replies
python -m pytest -q tests/test_reply_variation.py
```

The regression test checks that every inventoried literal return is covered.
This work does not expand the English grammar or repair unrelated clause-parser
issues. It stops the variation bypass and distinguishes missing knowledge from
failure to understand the question. No live memory or `.env` edits are required.
Restart Amy manually to load the new code; no bot was restarted during this work.


Persistent wording correction: Amy's authored social and fallback banks now participate in a persistent global wording history in her own database. Phrase selection does not refill an exhausted deck, and repeated substantial core sentences do not become fresh variants through introductory padding. Exact delivered replies, wake-only speech, and investigation narration are also checked before audio playback. Existing logged engine replies seed the history on the first startup using this code. Investigation requests, dictionary explanations and state reports now have independent sentence structures. The runtime selects the Amy persona explicitly, so a display-name change such as Bella no longer disables her variation system.

Finite banks can exhaust. Exhaustion is recorded on the phrase deck; an already-delivered full reply is held out of voice playback while its substantive text remains available in Discord. This does not create unlimited new paraphrases, complete the outstanding collection-wide rewrite, or certify every authored entry's semantics. No live fact database or configuration was edited, and no bot was restarted during installation. Verification includes restart persistence, cross-bank core reuse, padded repeats, renamed-bot voice routing, and the final audio guard.
