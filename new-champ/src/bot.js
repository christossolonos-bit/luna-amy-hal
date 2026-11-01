import { Client, Events, GatewayIntentBits, MessageFlags, Partials, ChannelType } from 'discord.js';
import { config, updateSettings } from './config.js';
import { loadCommands } from './loadCommands.js';
import { respond, SEARCH_ANNOUNCEMENT } from './companion.js';
import { sessions } from './voice/session.js';
import { activity } from './events.js';

const commands = await loadCommands();

// Owns the Discord connection so the dashboard can turn the bot on and off without restarting the app.
export const bot = {
  client: null,
  status: 'offline', // offline | starting | online | stopping
  since: Date.now(),
  error: null,

  async start() {
    if (this.client) return;
    this.setStatus('starting');
    const client = createClient();
    this.client = client;
    try {
      await client.login(config.discordToken);
      updateSettings({ botEnabled: true });
    } catch (err) {
      this.client = null;
      client.destroy().catch(() => {});
      this.error = err.message;
      this.setStatus('offline');
      activity('error', { where: 'discord login', message: err.message });
      throw err;
    }
  },

  async stop() {
    if (!this.client) return;
    this.setStatus('stopping');
    for (const session of [...sessions.values()]) session.destroy();
    const client = this.client;
    this.client = null;
    await client.destroy();
    updateSettings({ botEnabled: false });
    this.setStatus('offline');
    activity('power', { message: 'Bot turned off' });
  },

  setStatus(status) {
    this.status = status;
    this.since = Date.now();
    if (status !== 'offline') this.error = null;
  },
};

function createClient() {
  const client = new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMessages,
      GatewayIntentBits.MessageContent, // privileged: enable in the Developer Portal
      GatewayIntentBits.DirectMessages,
      GatewayIntentBits.GuildVoiceStates,
      GatewayIntentBits.GuildMembers, // privileged: needed for speaker names in voice
    ],
    partials: [Partials.Channel], // required to receive DMs
  });

  client.once(Events.ClientReady, (c) => {
    bot.setStatus('online');
    activity('power', { message: `Online as ${c.user.tag} in ${c.guilds.cache.size} server(s)` });
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isChatInputCommand()) return;

    const command = commands.get(interaction.commandName);
    if (!command) return;

    try {
      await command.execute(interaction);
    } catch (err) {
      activity('error', { where: `/${interaction.commandName}`, message: err.message });
      const reply = { content: 'Something went wrong running that command.', flags: MessageFlags.Ephemeral };
      if (interaction.replied || interaction.deferred) await interaction.followUp(reply).catch(() => {});
      else await interaction.reply(reply).catch(() => {});
    }
  });

  client.on(Events.MessageCreate, async (message) => {
    if (message.author.bot || !message.content) return;

    const isDM = message.channel.type === ChannelType.DM;
    const mentioned = message.mentions.has(client.user, { ignoreEveryone: true, ignoreRoles: true });
    const openChannel = config.chatChannelIds.includes(message.channelId);
    if (!isDM && !mentioned && !openChannel) return;

    const text = message.content.replace(new RegExp(`<@!?${client.user.id}>`, 'g'), '').trim();
    if (!text) return;

    const speaker = { id: message.author.id, name: message.member?.displayName ?? message.author.displayName };
    const typing = setInterval(() => message.channel.sendTyping().catch(() => {}), 8000);
    message.channel.sendTyping().catch(() => {});

    try {
      const reply = await respond({
        conversation: `text:${message.channelId}`,
        speaker,
        text,
        onSearching: () =>
          message.reply({ content: `🔎 ${SEARCH_ANNOUNCEMENT}`, allowedMentions: { repliedUser: false } }).catch(() => {}),
      });
      clearInterval(typing);
      const chunks = splitMessage(reply || '…');
      await message.reply({ content: chunks[0], allowedMentions: { repliedUser: false, parse: [] } });
      for (const chunk of chunks.slice(1)) await message.channel.send({ content: chunk, allowedMentions: { parse: [] } });
    } catch (err) {
      clearInterval(typing);
      activity('error', { where: 'text reply', message: err.message });
      await message.reply('My brain glitched for a sec, try again?').catch(() => {});
    }
  });

  // Leave voice when everyone else has left.
  client.on(Events.VoiceStateUpdate, (oldState) => {
    const session = sessions.get(oldState.guild.id);
    if (!session || oldState.channelId !== session.channelId) return;
    const channel = oldState.guild.channels.cache.get(session.channelId);
    if (channel && !channel.members.some((m) => !m.user.bot)) session.destroy();
  });

  return client;
}

function splitMessage(text, max = 2000) {
  const chunks = [];
  while (text.length > max) {
    let cut = text.lastIndexOf('\n', max);
    if (cut < max / 2) cut = text.lastIndexOf(' ', max);
    if (cut < max / 2) cut = max;
    chunks.push(text.slice(0, cut));
    text = text.slice(cut).trimStart();
  }
  chunks.push(text);
  return chunks;
}
