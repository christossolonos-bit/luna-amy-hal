import { SlashCommandBuilder, MessageFlags } from 'discord.js';
import { leaveChannel } from '../voice/session.js';

export const data = new SlashCommandBuilder()
  .setName('leave')
  .setDescription('Leave the voice channel')
  .setDMPermission(false);

export async function execute(interaction) {
  const left = leaveChannel(interaction.guildId);
  await interaction.reply(
    left ? 'Later! 👋' : { content: "I'm not in a voice channel.", flags: MessageFlags.Ephemeral },
  );
}
