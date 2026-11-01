import { Client, Events, GatewayIntentBits, MessageFlags, Partials, ChannelType } from 'discord.js';
import { config, updateSettings } from './config.js';
import { loadCommands } from './loadCommands.js';
import { respond, SEARCH_ANNOUNCEMENT } from './companion.js';
import { sessions } from './voice/session.js';
import { requestClip } from './avatarVideo.js';
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

    const speaker = {
      id: message.author.id,
      name: message.member?.displayName ?? message.author.displayName,
      username: message.author.username,
      dm: isDM,
    };
    const files = []; // attachments the bot makes this turn ride along with its reply
    const typing = setInterval(() => message.channel.sendTyping().catch(() => {}), 8000);
    message.channel.sendTyping().catch(() => {});

    try {
      const reply = await respond({
        conversation: `text:${message.channelId}`,
        speaker,
        text,
        onSearching: () =>
          message.reply({ content: `🔎 ${SEARCH_ANNOUNCEMENT}`, allowedMentions: { repliedUser: false } }).catch(() => {}),
        // voice: if she's in a voice channel in this server, typed music requests play there.
        discord: {
          client,
          guild: message.guild,
          sendHere: async (file) => files.push(file),
          voice: message.guild && sessions.get(message.guild.id),
          // Background helpers report back here when they finish, long after this reply.
          notify: (text) => { for (const chunk of splitMessage(text)) message.channel.send({ content: chunk, allowedMentions: { parse: [] } }).catch(() => {}); },
        },
      });
      clearInterval(typing);
      const chunks = splitMessage(reply || '…');
      const last = chunks.length - 1;
      // A clip of her saying it, if the recorder page is open. The text goes out now regardless;
      // the clip is added to the same message when (and if) it arrives.
      const clip = reply ? requestClip(reply) : Promise.resolve(null);
      const first = await message.reply({ content: chunks[0], files: last === 0 ? files : [], allowedMentions: { repliedUser: false, parse: [] } });
      clip.then((video) => video && first.edit({ files: [{ attachment: video, name: 'reply.webm' }] })).catch((err) => activity('error', { where: 'video reply', message: err.message }));
      for (const [i, chunk] of chunks.slice(1).entries()) {
        await message.channel.send({ content: chunk, files: i + 1 === last ? files : [], allowedMentions: { parse: [] } });
      }
    } catch (err) {
      clearInterval(typing);
      activity('error', { where: 'text reply', message: err.message });
      await message.reply('My brain glitched for a sec, try again?').catch(() => {});
    }
  });

  // Leave voice when everyone else has left.
  client.on(Events.VoiceStateUpdate, (oldState, newState) => {
    const session = sessions.get(oldState.guild.id);
    if (!session) return;
    // Someone joined her channel: an intro if they've never met her, plus anything she owes them.
    if (newState.channelId === session.channelId && oldState.channelId !== session.channelId && newState.member) session.arrived([newState.member]);
    if (oldState.channelId !== session.channelId) return;
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
