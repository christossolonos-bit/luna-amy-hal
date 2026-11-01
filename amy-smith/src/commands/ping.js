import { SlashCommandBuilder } from 'discord.js';

export const data = new SlashCommandBuilder()
  .setName('ping')
  .setDescription('Check if the bot is alive and see its latency');

export async function execute(interaction) {
  const { resource } = await interaction.reply({ content: 'Pinging...', withResponse: true });
  const roundTrip = resource.message.createdTimestamp - interaction.createdTimestamp;
  await interaction.editReply(`Pong! Round trip: ${roundTrip}ms · WebSocket: ${interaction.client.ws.ping}ms`);
}
