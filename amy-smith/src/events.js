import { EventEmitter } from 'node:events';

// App-wide activity feed. The dashboard streams these live; the last few hundred are kept for page loads.
//   bus.emit('activity', { type, ...details })
export const bus = new EventEmitter();
bus.setMaxListeners(100);

const MAX = 300;
export const recentActivity = [];

export function activity(type, details = {}) {
  const event = { type, time: Date.now(), ...details };
  recentActivity.push(event);
  if (recentActivity.length > MAX) recentActivity.shift();
  bus.emit('activity', event);
  return event;
}
