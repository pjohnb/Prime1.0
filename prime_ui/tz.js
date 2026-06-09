// tz.js -- PRIME shared timezone utility (Sprint 28 Item 7)
// All timestamps stored in DB as UTC; display always in ET with " ET" suffix.

const _TZ_ET = 'America/New_York';

/**
 * Format a UTC timestamp string as Eastern Time with " ET" suffix.
 * Accepts ISO-8601 strings with or without timezone designator,
 * as well as "YYYY-MM-DD HH:MM" DB format.
 *
 * Examples:
 *   formatET("2026-06-09T13:30:00Z")   -> "09:30 ET"
 *   formatET("2026-06-09 13:30:00")    -> "09:30 ET"
 *   formatET("2026-06-09T13:30:00Z", true) -> "Jun 9 09:30 ET"
 *
 * @param {string} utcStr  - UTC timestamp string
 * @param {boolean} [includeDate=false] - prefix date when not today
 * @returns {string} formatted time with " ET" suffix
 */
function formatET(utcStr, includeDate) {
  if (!utcStr) return '--';
  // Normalise "YYYY-MM-DD HH:MM:SS" (no Z) so Date() treats it as UTC
  const normalised = utcStr.replace(' ', 'T').replace(/(\d{2}:\d{2}(:\d{2})?)$/, '$1Z')
    .replace(/ZZ$/, 'Z');  // guard double-Z on already-correct strings
  const d = new Date(normalised);
  if (isNaN(d.getTime())) return utcStr;  // unparseable -- return raw

  const opts = { timeZone: _TZ_ET, hour: '2-digit', minute: '2-digit', hour12: false };
  const timePart = d.toLocaleTimeString('en-US', opts) + ' ET';

  if (!includeDate) return timePart;

  const todayET = new Date().toLocaleDateString('en-US', { timeZone: _TZ_ET });
  const signalET = d.toLocaleDateString('en-US', { timeZone: _TZ_ET });
  if (signalET === todayET) return 'Today ' + timePart;

  const mon = d.toLocaleString('en-US', { timeZone: _TZ_ET, month: 'short' });
  const day = d.toLocaleString('en-US', { timeZone: _TZ_ET, day: 'numeric' });
  return `${mon} ${day} ${timePart}`;
}

/**
 * Format a UTC timestamp as a full date+time ET string.
 * e.g. "Jun 9 09:30 ET"
 */
function formatETFull(utcStr) {
  return formatET(utcStr, true);
}

/**
 * Calculate hold time from an entry UTC string to now, in minutes.
 * Returns a human-readable string like "2h 15m" or "47m".
 */
function holdTimeFromEntry(entryUtcStr) {
  if (!entryUtcStr) return '--';
  const normalised = entryUtcStr.replace(' ', 'T').replace(/(\d{2}:\d{2}(:\d{2})?)$/, '$1Z')
    .replace(/ZZ$/, 'Z');
  const entry = new Date(normalised);
  if (isNaN(entry.getTime())) return '--';
  const mins = Math.round((Date.now() - entry.getTime()) / 60000);
  if (mins < 0) return '0m';
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}
