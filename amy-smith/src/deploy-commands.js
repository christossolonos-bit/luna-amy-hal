import 'dotenv/config';
import { REST, Routes } from 'discord.js';
import { loadCommands } from './loadCommands.js';

const { DISCORD_TOKEN, CLIENT_ID, GUILD_ID } = process.env;
if (!DISCORD_TOKEN || !CLIENT_ID) {
  console.error('Missing DISCORD_TOKEN or CLIENT_ID in .env');
  process.exit(1);
}

const commands = await loadCommands();
const body = [...commands.values()].map((c) => c.data.toJSON());
const rest = new REST().setToken(DISCORD_TOKEN);

// Guild commands update instantly; global ones can take up to an hour.
const route = GUILD_ID
  ? Routes.applicationGuildCommands(CLIENT_ID, GUILD_ID)
  : Routes.applicationCommands(CLIENT_ID);

const data = await rest.put(route, { body });
console.log(`Registered ${data.length} command(s) ${GUILD_ID ? `to guild ${GUILD_ID}` : 'globally'}`);
