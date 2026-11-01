import { SlashCommandBuilder, MessageFlags } from 'discord.js';
import { listMemories, forget } from '../memory.js';
import { isOwner } from '../contacts.js';
import * as scratch from '../scratchpad.js';

export const data = new SlashCommandBuilder()
  .setName('memories')
  .setDescription('See or erase what the bot remembers about you')
  .addSubcommand((sub) => sub.setName('list').setDescription('Show what I remember about you'))
  .addSubcommand((sub) =>
    sub
      .setName('forget')
      .setDescription('Forget one memory, or everything')
      .addIntegerOption((opt) => opt.setName('id').setDescription('Memory # from /memories list (leave empty to forget all)')),
  )
  .addSubcommand((sub) =>
    sub
      .setName('notes')
      .setDescription("Owner: see or wipe her working notes (this channel's scratchpad, the voice call's, active threads)")
      .addStringOption((opt) =>
        opt.setName('wipe').setDescription('Wipe instead of showing').addChoices(
          { name: 'this channel and voice scratchpads', value: 'scratchpad' },
          { name: 'active threads', value: 'threads' },
          { name: 'everything', value: 'all' },
        ),
      ),
  );

export async function execute(interaction) {
  const userId = interaction.user.id;

  if (interaction.options.getSubcommand() === 'notes') {
    if (!isOwner(userId)) return interaction.reply({ content: 'Only the owner can look at my working notes.', flags: MessageFlags.Ephemeral });
    const pads = [`text:${interaction.channelId}`, interaction.guildId && `voice:${interaction.guildId}`].filter(Boolean);
    const wipe = interaction.options.getString('wipe');
    if (wipe) {
      const notes = wipe === 'threads' ? 0 : pads.reduce((n, c) => n + scratch.clear(c), 0);
      const threads = wipe === 'scratchpad' ? 0 : scratch.clearThreads();
      return interaction.reply({ content: `Wiped ${notes} note${notes === 1 ? '' : 's'} and ${threads} thread${threads === 1 ? '' : 's'}.`, flags: MessageFlags.Ephemeral });
    }
    let content = '';
    for (const c of pads) {
      const list = scratch.entries(c);
      content += `**Scratchpad (${c.startsWith('voice') ? 'voice call' : 'this channel'}):**\n${list.map((e) => `\`#${e.id}\` [${e.kind}] ${e.text}`).join('\n') || '(empty)'}\n\n`;
    }
    const open = scratch.threads();
    content += `**Active threads (${open.length}/${scratch.MAX_THREADS}):**\n${open.map((t) => `\`#${t.id}\` **${t.label}**: ${t.state}`).join('\n') || '(none)'}`;
    return interaction.reply({ content: content.slice(0, 1990), flags: MessageFlags.Ephemeral });
  }

  if (interaction.options.getSubcommand() === 'forget') {
    const id = interaction.options.getInteger('id');
    const removed = forget(userId, id);
    const content = id == null ? `Forgot ${removed} memor${removed === 1 ? 'y' : 'ies'}.` : removed ? `Forgot #${id}.` : `No memory #${id}.`;
    return interaction.reply({ content, flags: MessageFlags.Ephemeral });
  }

  const rows = listMemories(userId);
  if (!rows.length) {
    return interaction.reply({ content: "I don't remember anything about you yet.", flags: MessageFlags.Ephemeral });
  }
  let content = '**What I remember about you:**\n';
  for (const r of rows) {
    const line = `\`#${r.id}\` ${r.content}\n`;
    if (content.length + line.length > 1950) {
      content += '…and more (see the dashboard)';
      break;
    }
    content += line;
  }
  await interaction.reply({ content, flags: MessageFlags.Ephemeral });
}
