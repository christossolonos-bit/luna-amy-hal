# Inspecting Amy's concept reflections

The Discord startup attaches a ThoughtBridge to Conversation and starts a separate local worker. The worker reads a read-only snapshot of memory, indexes conversation/reading episodes, and writes evidence-linked concept reflections to a separate `.thoughts.db`. It does not promote proposals to learned facts. This is bounded symbolic reasoning over supported facts and learned rules, not an unrestricted language-model reasoning loop.

Say these normally after the wake name, or send them as text:

- `What are you thinking about?` / `Show me your thoughts.`: inspect the latest valid concept reflection. Repeated inspection remains possible.
- `Think about friendship.`: focus the background agenda on that concept.
- `!thoughts` / `Show thinking status`: inspect worker health, reflection and supporting assertion IDs. Provenance, episode keys and connection steps are also retained in the reply trace.

Completed concept reflections post to the most recently used Discord text channel, at most one every 120 seconds. Voice messages record their associated text channel too. Internal one-off deductions are not individually broadcast. Invalidated evidence is not shown; the worker must recompute against current memory. If there is no supported reflection, the response says so instead of inventing one. A worker heartbeat older than 30 seconds is reported as unhealthy.

The code takes effect when the bot is next started or restarted by its operator. Editing these files does not update an already-running Python process. Do not launch the bot as a test.

Verification: `python -m pytest tests/test_thinking_integration.py tests/test_voice.py tests/test_voice_greetings.py -q`. The worker integration test launches only a temporary offline `--once` worker and verifies the temporary source database is byte-for-byte unchanged.
