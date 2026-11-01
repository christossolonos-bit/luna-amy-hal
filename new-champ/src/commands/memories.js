import { SlashCommandBuilder, MessageFlags } from 'discord.js';
import { listMemories, forget } from '../memory.js';

export const data = new SlashCommandBuilder()
  .setName('memories')
  .setDescription('See or erase what the bot remembers about you')
  .addSubcommand((sub) => sub.setName('list').setDescription('Show what I remember about you'))
  .addSubcommand((sub) =>
    sub
      .setName('forget')
      .setDescription('Forget one memory, or everything')
      .addIntegerOption((opt) => opt.setName('id').setDescription('Memory # from /memories list (leave empty to forget all)')),
  );

export async function execute(interaction) {
  const userId = interaction.user.id;

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
