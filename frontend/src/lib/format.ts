export const FLIGHT_COLORS = ['#38e1d0', '#ffb547', '#a78bfa', '#5aa9ff', '#ff6b8b', '#9be15d', '#f472b6', '#facc15'];
export const ROLE_COLORS: Record<string, string> = {atc: '#5aa9ff', pilot: '#38e1d0', ground: '#ffb547'};
export const ROLE_LABEL: Record<string, string> = {atc: 'Air traffic control', pilot: 'Flight captain', ground: 'Ground operations'};
export const TYPE_COLORS: Record<string, string> = {PROPOSE: '#5aa9ff', ACCEPT: '#4ade80', UNABLE: '#ffb547', PREFER: '#a78bfa', COUNTER: '#a78bfa', REJECT: '#ff6b8b'};
export const STATUS: Record<string, string> = {negotiating: 'Coordinating', awaiting_approval: 'Your decision needed', approved: 'Plan approved', rejected: 'Plan rejected', fallback: 'Fallback available', unresolved: 'Human review needed', interrupted: 'Coordination interrupted'};

export function clock(start: string, minutes: number) {
  const [h, m] = start.split(':').map(Number);
  const value = Math.round(h * 60 + m + minutes);
  const day = Math.floor(value / 1440);
  const wrapped = ((value % 1440) + 1440) % 1440;
  return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}${day > 0 ? ' +1d' : day < 0 ? ' −1d' : ''}`;
}

export function histTime(value: unknown) {
  if (value === undefined || value === null || value === '') return '—';
  const s = String(value).padStart(4, '0');
  return s.length === 4 ? `${s.slice(0, 2)}:${s.slice(2)}` : String(value);
}

export function json(value: unknown) { return JSON.stringify(value, null, 2); }

export function compact(n: number) { return n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : n.toLocaleString(); }

export function airlineName(airline: string | {code: string; name: string}) { return typeof airline === 'string' ? airline : airline.name; }

/** Initial compass bearing (degrees, clockwise from north) from point A to point B. */
export function bearing(lat1: number, lon1: number, lat2: number, lon2: number) {
  const r = Math.PI / 180;
  const y = Math.sin((lon2 - lon1) * r) * Math.cos(lat2 * r);
  const x = Math.cos(lat1 * r) * Math.sin(lat2 * r) - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos((lon2 - lon1) * r);
  return (Math.atan2(y, x) / r + 360) % 360;
}
