// tz.js -- PRIME shared timezone utility
// Sprint 28 Item 7: introduced. Sprint 29 TZ-01: hardened parsing + node export.
// AUDIT-016: scanners/DB write machine-local ET (datetime.now()), not UTC --
// naive strings are now parsed as ET, not UTC (see _parseUtc below).
//
// Contract: naive (timezone-less) timestamps are STORED in the database as
// machine-local ET. Timestamps that carry an explicit 'Z' or ±hh:mm offset
// (legacy rows written with datetime.utcnow()) are honored as-is. Either way,
// display uses Intl.DateTimeFormat with timeZone 'America/New_York', which
// handles Daylight Saving transitions automatically (EDT = UTC-4 in summer,
// EST = UTC-5 in winter).

const _TZ_ET = 'America/New_York';

/**
 * Look up the America/New_York UTC offset (e.g. "-04:00" or "-05:00") in
 * effect on the given calendar date, using Intl so DST transitions are
 * resolved correctly without hardcoding the US DST rules.
 */
function _etOffsetForDate(year, month, day) {
  const ref = new Date(Date.UTC(year, month - 1, day, 12));
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: _TZ_ET, timeZoneName: 'shortOffset',
  }).formatToParts(ref);
  const tzPart = parts.find((p) => p.type === 'timeZoneName');
  const m = tzPart && /GMT([+-]\d+)/.exec(tzPart.value);
  const hours = m ? parseInt(m[1], 10) : -5;
  const sign = hours < 0 ? '-' : '+';
  return `${sign}${String(Math.abs(hours)).padStart(2, '0')}:00`;
}

/**
 * Parse a timestamp string into a Date, interpreting naive (timezone-less)
 * strings as machine-local ET (what scanners/DB actually write). Strings that
 * already carry a 'Z' or an explicit ±hh:mm offset are respected as-is.
 *
 * Accepts:
 *   "2026-06-09T13:30:00Z"          (ISO-8601 with Z, legacy UTC rows)
 *   "2026-06-09T13:30:00.123456"    (ISO-8601 with fractional secs, no tz -> ET)
 *   "2026-06-09 13:30:00"           (DB format, no tz -> ET)
 *   "2026-06-09T13:30:00+00:00"     (explicit offset)
 *
 * @param {string} value
 * @returns {Date|null} parsed Date, or null when empty/unparseable.
 */
function _parseUtc(value) {
  if (!value) return null;
  let s = String(value).trim().replace(' ', 'T');
  // No explicit timezone designator -> the value is machine-local ET, not UTC.
  if (!/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(s)) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    s += m ? _etOffsetForDate(+m[1], +m[2], +m[3]) : '-05:00';
  }
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * Format a UTC timestamp string as Eastern Time with a " ET" suffix.
 *
 * Examples (summer / EDT = UTC-4):
 *   formatET("2026-06-09T13:30:00Z")        -> "09:30 ET"
 *   formatET("2026-06-09 13:30:00")         -> "09:30 ET"
 *   formatET("2026-06-09T13:30:00Z", true)  -> "Today 09:30 ET" | "Jun 9 09:30 ET"
 *
 * @param {string} utcStr  - UTC timestamp string
 * @param {boolean} [includeDate=false] - prefix "Today" / "Mon D" when given
 * @returns {string} formatted time with " ET" suffix, or "--" when empty
 */
function formatET(utcStr, includeDate) {
  const d = _parseUtc(utcStr);
  if (!d) return utcStr ? String(utcStr) : '--';

  const opts = { timeZone: _TZ_ET, hour: '2-digit', minute: '2-digit', hour12: false };
  const timePart = d.toLocaleTimeString('en-US', opts) + ' ET';
  if (!includeDate) return timePart;

  const todayET = new Date().toLocaleDateString('en-US', { timeZone: _TZ_ET });
  const thatET = d.toLocaleDateString('en-US', { timeZone: _TZ_ET });
  if (thatET === todayET) return 'Today ' + timePart;

  const mon = d.toLocaleString('en-US', { timeZone: _TZ_ET, month: 'short' });
  const day = d.toLocaleString('en-US', { timeZone: _TZ_ET, day: 'numeric' });
  return `${mon} ${day} ${timePart}`;
}

/**
 * Format a UTC timestamp as a full date+time ET string, e.g. "Jun 9 09:30 ET".
 */
function formatETFull(utcStr) {
  return formatET(utcStr, true);
}

/**
 * Hold time from an entry UTC timestamp to now, as "2h 15m" / "47m".
 */
function holdTimeFromEntry(entryUtcStr) {
  const entry = _parseUtc(entryUtcStr);
  if (!entry) return '--';
  const mins = Math.round((Date.now() - entry.getTime()) / 60000);
  if (mins < 0) return '0m';
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

// Node export shim (no-op in the browser, where `module` is undefined) so the
// utility can be unit-tested under node without a bundler.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { formatET, formatETFull, holdTimeFromEntry, _parseUtc, _TZ_ET };
}
