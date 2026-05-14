# CLAUDE.md

Guidance for Claude (or any contributor) working in this repo. Describes how the
project works, its history, and the non-obvious pitfalls that keep biting.

## What this is

A daily-running scraper that fetches MyVMK in-game event data, converts it to a
standard `.ics` calendar feed, and publishes the file via GitHub Pages so users
can subscribe to it in Google Calendar, Apple Calendar, or Outlook.

The canonical subscribe URL is:

    https://bsims-codes.github.io/myvmk-ics/myvmk.ics

## Pipeline

1. `.github/workflows/build-ics.yml` runs on `cron: "0 8 * * *"` (daily, 08:00 UTC).
2. The workflow runs `python myvmk-cal.py --out docs/myvmk.ics --tz America/New_York`.
3. The script fetches events from the MyVMK API, merges with the previous
   `docs/myvmk.ics` to preserve historical events, and writes the new file.
4. The workflow commits `docs/` and pushes back to `main`. GitHub Pages serves
   the `docs/` folder, so the new file is live within ~1 minute of the push.
5. The workflow uses `[skip ci]` in the commit message so the push doesn't
   retrigger the workflow.

Subscribed calendar clients refresh on their own schedules (Google a few times
per day, Apple often slower, especially on iOS).

## Data source: history of the scraper -> API switch

The project originally scraped `https://www.myvmk.com/events` with Playwright +
BeautifulSoup (see commit `9d408c9` "working python script"). It rendered the
page in a headless Chromium, waited for the calendar to populate the DOM, then
extracted events from HTML.

In March 2026 MyVMK migrated their site to a JavaScript framework that no
longer populated the DOM with event content the way the scraper expected, so
the scraper began returning 0 events. Commit `e6ed474` ("Fix calendar scraper:
switch to API and restore historical events", 2026-03-10) rewrote the script
to call the MyVMK JSON API directly and added historical-event merging.

In May 2026 the public `/events` page on the website was further changed: the
events are now rendered by a separate JS widget hosted at
`https://download.myvmk.com/calendar.html`, which itself loads
`Calendar.js` from `myvmk-public.s3.us-west-2.amazonaws.com`. That widget
*still* calls the same `getevents` API our script uses; only the rendering
layer changed. So despite appearances, the underlying data source has been
stable since the March switch.

### The current API

    GET https://www.myvmk.com/api/getevents

Response shape:

    {
      "previous": { "year": 2026, "events": [...] },
      "current":  { "year": 2026, "events": [...] },
      "next":     { "year": 2026, "events": [...] }
    }

Each event has: `name`, `host`, `description`, `color`, `id`, `startTime`,
`endTime`. Times are Unix seconds.

Pitfalls:

- **Timestamp bug** in the API: `startTime` and `endTime` are stored 5 hours
  ahead of the correct Eastern-time value (offset added instead of subtracted).
  `fetch_events()` corrects this by subtracting 18000 seconds and interpreting
  the result as local time. The GitHub Actions runner sets `TZ=America/New_York`
  to make this work. If you run the script on a machine in a different
  timezone, set `TZ=America/New_York` first or times will be wrong.
- **Buckets matter.** The script must read all three buckets (`previous`,
  `current`, `next`), not just `current`. Events that age out of `current`
  re-appear in `previous` for one month before falling off the API entirely.
  Reading only `current` causes UID instability (see "Subscriber UID stability"
  below).
- Some events have `endTime <= startTime` (API data error). The script
  defaults those to a 1-hour duration.

## The ICS file

Written by `build_ics()`. Key correctness requirements that have all been
violated and fixed at various points:

1. **CRLF line endings.** RFC 5545 requires CRLF. Google Calendar's manual
   import path rejects LF-only files with "Imported 0 of 0 events. Unable to
   process your iCal/CSV file." `build_ics()` joins with `\r\n` and the writer
   uses `newline=""` to keep Python from translating line endings.
2. **`.gitattributes` must not flatten CRLF.** Earlier the file had
   `*.ics text eol=lf`, which made git rewrite CRLF to LF on every commit,
   nullifying any in-script CRLF work. It is now `*.ics text eol=crlf`. **Do
   not change this back to `eol=lf`.** The misleading commit `f6c253e` once
   reverted to LF claiming "CRLF was causing issues with git line ending
   conversion" - the real cause was a missing/wrong `.gitattributes`, not an
   incompatibility.
3. **`METHOD:PUBLISH`** is needed for Google's manual-import path.
   Subscription via URL is more lenient, but adding `METHOD:PUBLISH` doesn't
   hurt either path.
4. **`VTIMEZONE` block.** Emitted when `--tz America/New_York` (the only TZ
   the script knows how to emit). Without it, GCal historically duplicated
   events. See commit `60c7118`.
5. **Description truncation.** Long descriptions used to cause iCal line-
   folding issues that broke various parsers. `ics_truncate_description()`
   chops descriptions to 60 characters and appends "...". If you ever change
   this length, verify that truncation never lands in the middle of an iCal
   escape sequence (`\,`, `\n`, `\;`, `\\`) - a half-escape will break
   parsers.

## Subscriber UID stability

Subscriber calendar clients identify events by `UID`. If the UID for the same
event changes between runs, the client sees a delete + recreate, which on
slow-refreshing clients (notably Apple Calendar on iOS) looks like the event
"disappeared".

There are two mechanisms that keep UIDs stable across runs:

1. `fetch_events()` reads all three buckets and dedupes by API `id`, so events
   keep the same `make_uid(title, start, end, id)` as long as they remain in
   the API at all (up to one month after they end).
2. `parse_existing_ics()` extracts the existing `UID:` value from each
   `VEVENT`, and `build_ics()` reuses that UID when present instead of
   recomputing. `merge_events()` also carries the existing UID forward to a
   matching new event - so on the first run after a UID-generation fix, events
   that were previously written with the buggy `id=0` UID retain that UID
   rather than abruptly changing.

If you ever change `make_uid()`'s hash inputs, you will invalidate every
subscriber's existing UIDs. Don't do this casually; if you must, plan to
absorb a one-time delete+recreate event for every subscriber.

## Files

- `myvmk-cal.py` - the script. Has `fetch_events`, `parse_existing_ics`,
  `merge_events`, `build_ics`, and CLI entry point.
- `.github/workflows/build-ics.yml` - the daily cron + commit workflow.
- `.gitattributes` - forces CRLF for `*.ics`. Keep this.
- `docs/myvmk.ics` - the generated calendar. Committed; subscribers fetch it
  from GitHub Pages.
- `docs/index.html` - tiny landing page (created by the workflow) so the
  Pages site has something at `/`.
- `docs/.nojekyll` - prevents Jekyll processing on Pages.

## Running locally

    python -m pip install requests
    python myvmk-cal.py --out docs/myvmk.ics --tz America/New_York --verbose

If you only want fresh API data without merging in the local file's
historical events, pass `--no-merge`.

For strict validation:

    python -m pip install icalendar
    python -c "from icalendar import Calendar; Calendar.from_ical(open('docs/myvmk.ics','rb').read())"

## Things to avoid

- Don't switch `.gitattributes` back to `eol=lf` for `*.ics`. It will silently
  break Google Calendar import again.
- Don't drop `METHOD:PUBLISH` from the calendar header.
- Don't change `make_uid()`'s inputs without a migration plan.
- Don't add a `Playwright` dependency back. The API works; the scraper is
  dead and shouldn't be revived unless the API itself goes away.
- The MyVMK API's "5 hours ahead" timestamp bug is in their data, not ours.
  Don't try to fix it on their end.
