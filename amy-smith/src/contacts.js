import { config } from './config.js';
import { contactTier } from './memory.js';

// What each tier of contact is allowed to do. Everyone starts as a stranger; only the owner
// (OWNER_IDS, plus anyone using the dashboard) can move people between tiers, by asking the bot.
export const TIERS = {
  stranger: { remember: false, search: false, files: false, sendElsewhere: false, persona: false },
  guest: { remember: true, search: true, files: true, sendElsewhere: false, persona: false },
  friend: { remember: true, search: true, files: true, sendElsewhere: true, persona: true },
};

export function isOwner(userId) {
  return userId === 'dashboard' || config.ownerIds.includes(userId);
}

// 'owner' | 'friend' | 'guest' | 'stranger'
export function tierOf(userId) {
  return isOwner(userId) ? 'owner' : contactTier(userId);
}

export function permissions(userId) {
  const tier = tierOf(userId);
  if (tier === 'owner') return { ...TIERS.friend, tier, owner: true };
  return { ...(TIERS[tier] ?? TIERS.stranger), tier, owner: false };
}
