import { config } from './config.js';
import { checkOllama } from './llm.js';
import { activity } from './events.js';
import { bot } from './bot.js';
import { startDashboard } from './dashboard/server.js';
import { loadWhisper } from './voice/whisper.js';
import { startPressure, setResearcher } from './pressure.js';
import { research } from './journal.js';
import { startContinuity } from './continuity.js';

// Ollama only required if it's the teacher or embedder; default stack is HalMini + NVIDIA.
if (config.brain === 'ollama' || config.embedder === 'ollama') {
  await checkOllama().catch((err) => {
    console.error(`Ollama check failed: ${err.message}`);
    process.exit(1);
  });
} else {
  console.log(`[startup] teacher=${config.brain} embedder=${config.embedder} model=${config.nvidia.embedModel}`);
}

process.on('unhandledRejection', (err) => activity('error', { where: 'unhandled', message: String(err?.message ?? err) }));

// The dashboard always runs; the Discord bot can be switched on and off from it.
startDashboard(bot);

// Her pressure buckets (boredom, curiosity, loneliness...): curiosity and boredom send her off to
// research and write in her journal; the rest speak up in voice calls (see pressure.js).
setResearcher(research);
startPressure();

// Continuity: a heartbeat so downtime is known after a restart, plus her recent mistakes.
startContinuity();

// Load local Whisper in the background: it's the main transcriber, or Groq's instant fallback.
if (config.stt !== 'fish') loadWhisper().catch((err) => activity('error', { where: 'whisper', message: err.message }));

if (!config.discordToken) {
  activity('error', { where: 'startup', message: 'DISCORD_TOKEN is empty in .env, so the bot stays offline' });
} else if (config.botEnabled) {
  bot.start().catch(() => {});
} else {
  activity('power', { message: 'Bot is switched off. Turn it on from the dashboard.' });
}
