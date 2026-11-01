# Amy's tools: how to use them, and what to do when they fail

Look things up here with the `guide` tool (you have it every turn): topic = a tool name (e.g. `files`) or a problem
(e.g. `error`, `truncated`, `not available`). Read the section before guessing, and especially after
a tool returns an error.

## general

Never say a tool can't do something until you've tried the call or read its guide section. Try it; if
it really fails, the error says why, and that is what you report.

How tool calls work for you:
- Each turn a planner decides which action tools this message actually asks for; only those are
  handed to you, plus your note tools (`note`, `scratchpad`, `thread`, `threads`) which you always
  have. The line "Tools in hand this turn: …" in the message tells you exactly what you have.
- Turns with `files`, `read_source`, `git` or `self` get up to 12 rounds, so you can look, read,
  change, and check. Note-only and guide-only rounds don't use up your rounds.
- Fill in **every** field of a tool call. Fields you don't need get an empty value: `""` for text,
  `0` for numbers, `false` for yes/no, `[]` for lists. Some providers drop fields that are left out,
  and then the call arrives without them.
- Every result starts with a status line: `[files: ok]` then the result, or
  `[files: error not_found] <what went wrong>` then `hint: <the likely fix>`. An error means it did
  **not** happen. Never say it worked.
- Most turns get up to 4 tool rounds, so you can look something up and then act on it in the same
  turn. If the rounds run out you're told which calls worked and which didn't: say honestly what's
  done and what isn't, and that you can continue next message.
- If a result is an error or says `TRUNCATED` / `NOT READ`, you get one extra round to fix the call or
  read on, even on a one-round turn. Use it.

## error

When a tool result starts with `[tool: error code]`:
1. Read the message and the `hint:` line; the hint names the likely fix. Error codes:
   `missing_field` (a field was empty or wrong: fill in every field), `not_found` (wrong name, number or
   path: list/find first or use the suggestion), `not_available` (not handed to you this turn),
   `not_allowed` (this person's access doesn't allow it), `forbidden` (never allowed, don't retry),
   `conflict` (already exists / matches twice / a step is missing), `too_large`, `unavailable` (an
   outside service is busy or rate-limited: don't loop), `failed` (anything else: read the message).
2. Fix that one thing and call again (you get one retry round). Typical fixes:
   - "what is the obligation?", "a note needs kind and text", "no path was given": a field was
     empty or missing. Call again with every field filled in.
   - "no such file", "no ... model matches": check the spelling or use the suggestion in the error.
   - "isn't on your contact list": that person has never talked to you; you can't act on them yet.
   - "only friends can …", "Owner only": the person's tier doesn't allow it. Say so plainly.
   - "rate limit", "429", "timed out": the outside service is busy. Say it didn't work and offer
     to try again later. Don't retry in a loop.
3. If it still fails, tell them honestly what failed and the error, and stop. Check the log with
   `self` → `logs` (type `error`) before explaining why.

## not available

If you need a tool that isn't in "Tools in hand this turn", the planner didn't hand it over for this
message. Don't pretend to call it and don't describe a call you didn't make. Say plainly that it
wasn't available this turn and ask them to say it again (more directly); the next message is planned
fresh. Owner-only tools are never available to anyone but your owner.

## request_tool

When you need a tool that isn't in "Tools in hand this turn", call `request_tool` with `tool` (from
its list) and `reason`. If you're allowed it for this person, it's added right away: call it in your
next step. If not, you get `not_allowed`: tell them plainly you can't do that for them. At most 2
requests per reply. Each request is logged; many of them means the planner is missing what people ask.
A tool you were handed but didn't manage to use stays in hand for the next 3 turns.

## phantom

Never say something was saved, sent, played, switched, pushed, added to your list or otherwise done
unless a tool result in this reply says ok for it. After every reply, claims like that are checked
against the tool results. If one has no ok result behind it ("phantom success"), it's logged, and in
text you're asked to do it for real or correct yourself before anything is sent; in voice, where it
was already said, you add a spoken correction. Failures and phantoms are listed in
knowledge/tool-failures.md, most frequent first.

## continuity

Your prompt carries a Continuity block every turn: [time] lines are facts (the time now, when this
conversation and this person were last active, whether you were restarted and for how long); [caution]
lines are your recent mistakes: tool errors, claims with no tool behind them, and times someone told you
that you were wrong. Each caution lasts a day and gets louder if it happens again ("2x today"). Treat
them as real: if a caution is about the thing you're doing now, slow down and do it the careful way.
Open scratchpad notes show their age; a promise or question still open after a day should be resolved
or brought up. None of this is something to recite.

## truncated

`read_source` and `files` → `read` stop at a size limit. The result then says `TRUNCATED: showing
lines A-B of N` and gives the `start_line` to continue from. Call again with that `start_line` before
drawing conclusions about the rest of the file. `NOT READ` means the file was skipped because the
call's limit was used up: read it in its own call.

## web_search

Search the internet. Use for current events, news, prices, weather, scores, anything that may have
changed or you're unsure of. Fields: `query` (like a search engine query), `topic` ("news" for recent
events, else ""), `time_range` ("day"/"week"/"month"/"year" or ""). If results don't answer the
question, say so rather than guessing. Errors: Tavily rate limit or no key → say search isn't working
right now.

## send_file

Write one text file (notes, code, markdown, CSV, HTML…) and attach it. `filename` with extension,
`content` complete. `recipient` "" = this chat; a "#channel" or a person who has DMed you (friends and
owner only). `message` = optional note when sending elsewhere. More than one file → use
`send_project`. Errors: too big (8 MB), person never DMed you, guest asking to send elsewhere.

## send_project

Several files with folders, zipped and sent as one attachment. `name`, `files` = [{path, content}]
with every file complete (no placeholders; add a README when useful). Same `recipient` rules as
`send_file`. A copy is kept in data/projects/<name>.

## generate_image

Make a picture. `prompt` = rich description (subject, setting, style, lighting, mood). Say briefly
that it's ready; don't describe it at length. Errors: no OpenRouter key, the model returned no image →
say it didn't come out and offer to try a different description.

## generate_music

Make a song with Google Lyria. You need two things first: what it's about (topic/mood/genre) and
how long (`length` "clip" = 30 s, "full" = full song). If either is missing, ask instead of calling.
`prompt` detailed, `title` short. Full songs take about a minute. Songs are saved to the music
library. Errors: too big for Discord → it's still saved; offer to play it in voice instead.

## music

Play the music library (your songs + files your owner dropped in data/music). `action`: play, queue,
pause, resume, skip, stop, now_playing, save_playlist, delete_playlist, list. `tracks` = titles as
listed in the tool description; `playlist` = a name; `shuffle`. To build a playlist from a
description, pick matching titles yourself. Plays in the voice call if you're in one, in the page in
the avatar studio, otherwise attaches the files. Errors: "Couldn't find" a title → use one of the
listed titles.

## note

Write a private scratchpad note for this conversation (you see it every turn). `kind`: mood, thread,
commitment, open_question, callback; `text` 1-3 sentences; `people`; `expires_in_hours` (0 = none).
If you tell someone you'll remember or check in, write the note in that same reply.

## scratchpad

Change a note: `action` update (new `text`), resolve (it disappears), or list; `id` = note number.

## thread / threads

`thread`: start or update one of at most 5 ongoing situations followed across all channels (`label`,
`state` = current status only, `people`). Same label overwrites. `threads`: retire (`id`) or list.
Error "Already 5 threads open": retire a stale one first.

## checkin_consent

Record whether the person talking to you is okay with you checking in on them on your own:
`answer` yes/no, `every_days` (7 = weekly), `note` (what to talk about, what's too much). Use it as
soon as they answer your question. A no is permanent; never ask again. Only your owner can set
someone else's (`person`).

## obligations

Owner only. Your to-do list. `action` add / list / done / cancel. For add: `task` written so you'll
understand it later (what to do or say, who asked), `person` if it should happen when that person
next joins voice or talks to you (real names like "Brandon" resolve to their screen name if you
remember it). Person-linked ones fire and check off automatically; standing ones you check off with
done (`id`, `note`). Error "what is the obligation?" → `task` was empty; call again with it filled.

## files

Owner only. Work on files on your owner's PC. Paths start with Desktop, Documents, Downloads,
Pictures, Music, Videos or ~. `path` is required and always given in full (e.g.
"Desktop/lyra-video-game/src/main.js"). Actions: list (`depth` 1-4), read (`start_line`, `end_line`),
write (new file; `overwrite` true only to replace a whole file), edit (`old_text` copied exactly from
a read, a few lines so it matches once; `new_text`), undo, mkdir, move/copy (`to`), find (`name`),
send. Workflow: list → read → edit → read back to check, and say what you changed.
**move and copy work on whole folders**, with everything inside, any size: e.g. copy `path`
"Desktop/new champ" `to` "Desktop/new champ backup" duplicates your entire project (about 800 MB, a few
seconds). If the destination is an existing folder, it goes inside it. They never overwrite: if the
target already exists you get `conflict`; pick a new name.
Errors: "isn't in" → read again and copy the exact text; "appears N times" → include more lines;
"already exists" → edit it, or overwrite; "off limits" → keys, .env, AppData and outside home are
never allowed. You can't delete or run commands; tell them what to run.

## git

Owner only. `repo` = the project folder. status, diff (`files` one file or [], `staged`), add
(`files` or ["all"]), commit (`message`), push. Push is two steps: the first call only previews (branch,
remote, commits); tell your owner exactly that and ask them to confirm. Only after they say yes in
their next message, call push again. Never say it was pushed unless the result is ok and says "Pushed".
Errors: "isn't inside a git repository" → wrong folder; "Nothing is staged" → add first; "changed
since you asked" → show the new preview and ask again; push failures (auth, rejected) → report them,
don't retry.

## self

Owner only. Know yourself before explaining yourself. `map` = where everything lives (persona,
settings, database, journal, files) and your current brain/settings. `logs` = search your live
activity log (`type` e.g. error, tool-call, heard, replied, plan, pressure; `search` text; `last` N).
`mood` = your pressure buckets. `journal` = today's journal. The knowledge base itself is the `guide` tool (`topic`).
There is no log file; the log is in memory, newest 300 events since the bot started.

## avatar_mode

Owner only. Turn video replies on or off (`action` on/off/status). When on, Discord text replies also
get a video of your face, but only while the Recorder page is open (dashboard → Recorder). If the
result says the Recorder isn't open, tell your owner to open it, click it once, and leave it open.

## list_models / switch_brain

Owner only. `list_models`: a numbered list, either best for a `purpose` (coding, reasoning, general,
cheap) or a `search` of the whole OpenRouter catalog by name/maker ("grok", "xai"). Present 1, 2, 3
with price. `switch_brain`: `brain` (ollama/openrouter), `model` as said ("grok 4.20") or "", `pick`
= a number from the list just shown or 0, `reasoning`. Read the result: "Switched" = done; "Nothing
changed" or "no … model matches" = it didn't happen, use the suggested ids or ask again. Never say
you switched unless the result is ok and says "Switched".

## background_task

Owner only. Hand a longer job to a helper (sub-agent) with files and web search: `action` start
(`title`, `instructions` = a complete brief: goal, folders, what done looks like; `model` or ""),
status, cancel (`id`). It reports back in the chat when done. Helpers can't delete, run code, or use
git.

## rebuild_memories

Owner only. Re-read the saved chat history and pull out long-term memories, filed under the right
person. `fresh` true only if your owner asked to wipe existing memories first. Runs in the background.

## read_source

Owner only. Read your own code (`paths` up to 8 files, relative to the project; `start_line` to read
on). Each file has its own budget; a long file comes back TRUNCATED with the line to continue from.
Read the actual file before stating how you work, and label claims verified / inferred / guess.

## set_contact_tier / list_contacts

Owner only. Move someone between stranger, guest and friend (`person`, `tier`), or list who's who.
The owner's own tier can't be changed.

## voice

In voice: you hear people through speech-to-text (Groq, or local Whisper when Groq is rate-limited)
and answer through Fish Audio. Replies are short and spoken. If people say they can't hear you or
you're slow, check `self` → `logs` with type `error` (look for Groq 429, Fish errors) before
explaining. You only answer when your name is said (the wake word). Unprompted lines (check-ins,
offers) only happen in a lull, at most 3 a day.

## check-ins

Once, in person in voice, you ask people you know whether you may check in on them on your own, how
often, and what to talk about; save the answer with `checkin_consent`. You only check in during voice
calls they're in, never more often than they said; a no or "back off" is permanent.
