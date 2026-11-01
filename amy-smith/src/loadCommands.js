import { readdir } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const commandsDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'commands');

// Every file in src/commands exports { data, execute }.
export async function loadCommands() {
  const commands = new Map();
  for (const file of await readdir(commandsDir)) {
    if (!file.endsWith('.js')) continue;
    const mod = await import(pathToFileURL(path.join(commandsDir, file)).href);
    if (!mod.data || !mod.execute) {
      console.warn(`Skipping ${file}: missing "data" or "execute" export`);
      continue;
    }
    commands.set(mod.data.name, mod);
  }
  return commands;
}
