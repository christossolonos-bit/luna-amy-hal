import { config, updateSettings } from './config.js';
import { savePersonaVersion, hasPersonaHistory } from './memory.js';
import { activity } from './events.js';
import { permissions } from './contacts.js';

// Record the starting persona so the very first change can be undone.
if (!hasPersonaHistory()) {
  savePersonaVersion({ name: config.botName, persona: config.persona, reason: 'Original persona' });
}

// Applies a new name and/or persona, keeping a version in history.
export function setPersona({ name, persona, reason, changedBy }) {
  const patch = {};
  if (name && name.trim() && name.trim() !== config.botName) patch.botName = name.trim();
  if (persona && persona.trim() && persona.trim() !== config.persona) patch.persona = persona.trim();
  if (!Object.keys(patch).length) return false;

  updateSettings(patch);
  savePersonaVersion({ name: config.botName, persona: config.persona, reason, changedBy });
  activity('persona', { reason, changedBy, name: patch.botName });
  return true;
}

// Friends (and the owner) can reshape the persona just by asking.
export function canEditPersona(userId) {
  return config.personaSelfEdit && permissions(userId).persona;
}
