// tests/test_tz.js -- Sprint 29 TZ-01 unit tests for prime_ui/tz.js
// Run directly:  node tests/test_tz.js
// Also executed under pytest via tests/test_tz_js.py.
//
// Verifies UTC->ET conversion through Intl (America/New_York), covering the
// summer/winter DST cases, the spring-forward boundary, UTC midnight, and the
// naive / fractional-second timestamp formats the backend actually emits.

const assert = require('assert');
const path = require('path');
const { formatET, formatETFull, holdTimeFromEntry, _parseUtc } =
  require(path.join(__dirname, '..', 'prime_ui', 'tz.js'));

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log('  ok - ' + name);
}

// (a) Summer: Phoenix 09:00 MST == 16:00 UTC == 12:00 EDT (UTC-4, +3 from Phoenix)
check('summer date converts UTC->EDT (+3 from Phoenix)', () => {
  assert.strictEqual(formatET('2026-07-01T16:00:00Z'), '12:00 ET');
});

// (b) Winter: Phoenix 09:00 MST == 16:00 UTC == 11:00 EST (UTC-5, +2 from Phoenix)
check('winter date converts UTC->EST (+2 from Phoenix)', () => {
  assert.strictEqual(formatET('2026-01-15T16:00:00Z'), '11:00 ET');
});

// (c) DST boundary (spring forward 2026-03-08): the 02:00 ET hour is skipped.
//     06:30 UTC -> 01:30 EST (before); 07:30 UTC -> 03:30 EDT (after).
check('DST spring-forward boundary has no 02:00 artifact', () => {
  assert.strictEqual(formatET('2026-03-08T06:30:00Z'), '01:30 ET');
  assert.strictEqual(formatET('2026-03-08T07:30:00Z'), '03:30 ET');
});

// (c2) DST boundary (fall back 2026-11-01): 05:30 UTC -> 01:30 EDT; 06:30 UTC -> 01:30 EST.
check('DST fall-back boundary converts correctly', () => {
  assert.strictEqual(formatET('2026-11-01T05:30:00Z'), '01:30 ET');
  assert.strictEqual(formatET('2026-11-01T06:30:00Z'), '01:30 ET');
});

// (d) UTC midnight -> 20:00 ET the previous day (EDT).
check('UTC midnight converts to previous-day 20:00 ET', () => {
  assert.strictEqual(formatET('2026-06-10T00:00:00Z'), '20:00 ET');
});

// Naive DB-format (no tz designator) is interpreted as UTC, not browser-local.
check('naive DB-format timestamp is treated as UTC', () => {
  assert.strictEqual(formatET('2026-07-01 16:00:00'), '12:00 ET');
});

// Fractional-second ISO with no tz (datetime.utcnow().isoformat()) -> UTC.
check('fractional-second isoformat (no Z) is treated as UTC', () => {
  assert.strictEqual(formatET('2026-07-01T16:00:00.123456'), '12:00 ET');
});

// Empty / unparseable input degrades gracefully.
check('empty input returns placeholder', () => {
  assert.strictEqual(formatET(''), '--');
  assert.strictEqual(formatET(null), '--');
  assert.strictEqual(_parseUtc('not-a-date'), null);
});

// formatETFull prefixes a date (or "Today") and keeps the ET suffix.
check('formatETFull includes a date prefix and ET suffix', () => {
  const out = formatETFull('2020-07-01T16:00:00Z');
  assert.ok(/ET$/.test(out), 'ends with ET');
  assert.ok(/Jul 1 12:00 ET/.test(out), 'shows Jul 1 12:00 ET, got: ' + out);
});

// holdTimeFromEntry returns a human duration and handles bad input.
check('holdTimeFromEntry formats durations and handles bad input', () => {
  assert.strictEqual(holdTimeFromEntry(''), '--');
  const past = new Date(Date.now() - 75 * 60000).toISOString();
  assert.strictEqual(holdTimeFromEntry(past), '1h 15m');
});

console.log('\n' + passed + ' tz.js test groups passed.');
