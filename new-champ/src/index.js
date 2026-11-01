import { config } from './config.js';
import { checkLlm } from './llm.js';
import { activity } from './events.js';
import { bot } from './bot.js';
import { startDashboard } from './dashboard/server.js';
import { loadWhisper } from './voice/whisper.js';

await checkLlm().catch((err) => {
  console.error(`LLM check failed: ${err.message}`);
  process.exit(1);
});
console.log(
  config.llmBackend === 'nvidia'
    ? `Brain: NVIDIA ${config.nvidia.model}`
    : `Brain: Ollama ${config.ollama.model}`,
);

process.on('unhandledRejection', (err) => activity('error', { where: 'unhandled', message: String(err?.message ?? err) }));

// The dashboard always runs; the Discord bot can be switched on and off from it.
startDashboard(bot);

// Load local Whisper in the background: it's the main transcriber, or Groq's instant fallback.
if (config.stt !== 'fish') loadWhisper().catch((err) => activity('error', { where: 'whisper', message: err.message }));

if (!config.discordToken) {
  activity('error', { where: 'startup', message: 'DISCORD_TOKEN is empty in .env, so the bot stays offline' });
} else if (config.botEnabled) {
  bot.start().catch(() => {});
} else {
  activity('power', { message: 'Bot is switched off. Turn it on from the dashboard.' });
}
