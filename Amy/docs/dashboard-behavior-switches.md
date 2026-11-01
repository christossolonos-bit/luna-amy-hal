# Greeting and automatic speech switches

Settings now includes visual switches for automatic call speech on Alex, Gramps, Nova, and New Champ. The master disables room greetings, newcomer introductions, arrival follow-ups, and reconnect/restart announcements. Individual switches remain independent; background task progress has its own switch. Room-greeting off no longer falls back to per-person introductions on bot entry. Presence tracking remains active. Pending generation checks the current policy before emitting new speech.

The switches save immediately through the existing settings API and persist in each bot's own settings.json. Their default values preserve existing behavior until changed. No credentials are exposed or changed. Existing live settings were not rewritten during installation.

Amy and Adam have a separate Greetings and automatic speech section with live greeting-reply, farewell-reply, and curiosity switches. Amy also has automatic reflection and investigation-narration switches. These settings persist separately in data/behavior-switches.json, bypass .env, and apply at use time. Turning off a greeting keeps the substantive question/fact after it; disabled greeting-only replies generate no text or audio. These Python bots do not currently implement the Node bots' automatic room-entry greetings, so no nonfunctional controls for those are shown.

The new code must first be loaded by restarting the relevant applications, including the Python dashboard servers. No bot or dashboard was restarted during this work. Once loaded, subsequent switch changes take effect without restarting. Audio already handed to playback may finish.

Verification: 24 Python dashboard/API/greeting checks across Amy and Adam; 16 isolated Node speech-policy checks across the four companion projects; JavaScript/Python syntax checks. No live Discord/TTS calls.
