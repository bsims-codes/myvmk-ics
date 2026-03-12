#!/usr/bin/env python3
r"""
myvmk-cal.py
Fetch MyVMK events from the API and convert to an .ics feed
you can import or subscribe to in Google Calendar.

This script preserves historical events from previous runs by reading
the existing ICS file and merging with newly fetched events.

Examples:
  python myvmk-cal.py --out myvmk.ics --tz America/New_York
  python myvmk-cal.py --out myvmk.ics --verbose

Requires:
  pip install requests
"""
import argparse
import datetime as dt
import hashlib
import os
import re
import sys
from typing import Optional, List, Dict
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    print("This script requires 'requests'. Install with:")
    print("  pip install requests")
    raise

# API endpoint for MyVMK events
EVENTS_API_URL = "https://www.myvmk.com/api/getevents"


def parse_ics_datetime(dtstr: str, tzid: Optional[str] = None) -> Optional[dt.datetime]:
    """Parse an ICS datetime string like 20260301T210000 into a datetime object."""
    try:
        # Remove any TZID prefix if present in the string
        if ':' in dtstr:
            dtstr = dtstr.split(':')[-1]
        return dt.datetime.strptime(dtstr, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def parse_existing_ics(filepath: str, verbose: bool = False) -> List[Dict]:
    """Parse an existing ICS file and extract events."""
    events = []

    if not os.path.exists(filepath):
        if verbose:
            print(f"[debug] No existing ICS file at {filepath}")
        return events

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        if verbose:
            print(f"[warn] Could not read existing ICS file: {e}")
        return events

    # Split into VEVENT blocks
    vevent_pattern = re.compile(r'BEGIN:VEVENT\r?\n(.*?)END:VEVENT', re.DOTALL)
    for match in vevent_pattern.finditer(content):
        event_block = match.group(1)

        event = {}

        # Extract UID
        uid_match = re.search(r'^UID:(.+)$', event_block, re.MULTILINE)
        if uid_match:
            event['uid'] = uid_match.group(1).strip()

        # Extract SUMMARY
        summary_match = re.search(r'^SUMMARY:(.+)$', event_block, re.MULTILINE)
        if summary_match:
            event['title'] = summary_match.group(1).strip().replace('\\,', ',').replace('\\;', ';')

        # Extract DTSTART
        dtstart_match = re.search(r'^DTSTART[^:]*:(.+)$', event_block, re.MULTILINE)
        if dtstart_match:
            event['start'] = parse_ics_datetime(dtstart_match.group(1).strip())

        # Extract DTEND
        dtend_match = re.search(r'^DTEND[^:]*:(.+)$', event_block, re.MULTILINE)
        if dtend_match:
            event['end'] = parse_ics_datetime(dtend_match.group(1).strip())

        # Extract DESCRIPTION
        desc_match = re.search(r'^DESCRIPTION:(.+?)(?=^[A-Z]|\Z)', event_block, re.MULTILINE | re.DOTALL)
        if desc_match:
            event['description'] = desc_match.group(1).strip().replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';')

        # Only add if we have the essential fields
        if event.get('title') and event.get('start') and event.get('end'):
            event['id'] = 0  # Historical events don't have API IDs
            events.append(event)

    if verbose:
        print(f"[debug] Parsed {len(events)} events from existing ICS file")

    return events


def ics_escape(s: str) -> str:
    """Escape special characters for ICS format."""
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_truncate_description(desc: str, max_len: int = 60) -> str:
    """Truncate description to avoid line folding issues with Google Calendar."""
    available = max_len
    if len(desc) <= available:
        return desc
    return desc[:available-3] + "..."


def ics_dt(dt_obj: dt.datetime, tzid: Optional[str]) -> str:
    """Format datetime for ICS."""
    stamp = dt_obj.strftime("%Y%m%dT%H%M%S")
    return (f";TZID={tzid}:{stamp}" if tzid else f":{stamp}")


def make_uid(title: str, start: dt.datetime, end: dt.datetime, event_id: int) -> str:
    """Generate a unique ID for the event."""
    data = f"{event_id}|{title}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
    return hashlib.sha1(data).hexdigest() + "@myvmk"


def build_ics(events: List[dict], tzid: Optional[str], cal_name: str = "MyVMK Events") -> str:
    """Build ICS file content from events list."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MyVMK Scraper//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
    ]

    # Add VTIMEZONE component for America/New_York if using that timezone
    if tzid == "America/New_York":
        lines += [
            "BEGIN:VTIMEZONE",
            "TZID:America/New_York",
            "X-LIC-LOCATION:America/New_York",
            "BEGIN:DAYLIGHT",
            "TZOFFSETFROM:-0500",
            "TZOFFSETTO:-0400",
            "TZNAME:EDT",
            "DTSTART:19700308T020000",
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
            "END:DAYLIGHT",
            "BEGIN:STANDARD",
            "TZOFFSETFROM:-0400",
            "TZOFFSETTO:-0500",
            "TZNAME:EST",
            "DTSTART:19701101T020000",
            "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
    for ev in events:
        title = ev["title"] or "MyVMK Event"
        start = ev["start"]
        end = ev["end"]
        uid = make_uid(title, start, end, ev.get("id", 0))
        lines += [
            "BEGIN:VEVENT",
            f"DTSTAMP:{now}",
            f"UID:{uid}",
            f"SUMMARY:{ics_escape(title)}",
            f"DTSTART{ics_dt(start, tzid)}",
            f"DTEND{ics_dt(end, tzid)}",
        ]
        if ev.get("description"):
            truncated_desc = ics_truncate_description(ics_escape(ev['description']))
            lines.append(f"DESCRIPTION:{truncated_desc}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"


def make_event_key(event: Dict) -> str:
    """Create a unique key for an event based on title, start, and end times."""
    title = event.get('title', '').strip().lower()
    start = event.get('start')
    end = event.get('end')

    # Normalize the key - use date and hour to allow for minor time differences
    start_key = start.strftime("%Y%m%d%H%M") if start else ""
    end_key = end.strftime("%Y%m%d%H%M") if end else ""

    return f"{title}|{start_key}|{end_key}"


def merge_events(existing: List[Dict], new_events: List[Dict], verbose: bool = False) -> List[Dict]:
    """Merge existing events with new events, avoiding duplicates.

    New events take precedence over existing ones (they may have updated descriptions).
    """
    # Build a dict of new events by key
    new_by_key = {}
    for ev in new_events:
        key = make_event_key(ev)
        new_by_key[key] = ev

    # Start with new events
    merged = list(new_events)
    kept_historical = 0

    # Add historical events that aren't in the new set
    for ev in existing:
        key = make_event_key(ev)
        if key not in new_by_key:
            merged.append(ev)
            kept_historical += 1

    # Sort by start time
    merged.sort(key=lambda e: e.get('start') or dt.datetime.min)

    if verbose:
        print(f"[debug] Merged: {len(new_events)} new + {kept_historical} historical = {len(merged)} total events")

    return merged


def fetch_events(verbose: bool = False) -> List[Dict]:
    """Fetch events from the MyVMK API."""
    if verbose:
        print(f"[debug] Fetching events from {EVENTS_API_URL}")

    try:
        response = requests.get(EVENTS_API_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"[error] Failed to fetch events: {e}")
        return []
    except ValueError as e:
        print(f"[error] Failed to parse JSON response: {e}")
        return []

    if verbose:
        print(f"[debug] API response received, parsing events...")

    events: List[dict] = []

    # Parse current month events
    current = data.get("current", {})
    year = current.get("year", dt.date.today().year)
    raw_events = current.get("events", [])

    if verbose:
        print(f"[debug] Found {len(raw_events)} events for year {year}")

    for ev in raw_events:
        try:
            name = ev.get("name", "MyVMK Event").strip()
            start_ts = ev.get("startTime")
            end_ts = ev.get("endTime")
            event_id = ev.get("id", 0)
            host = ev.get("host", "")
            description = ev.get("description", "")

            if not start_ts or not end_ts:
                if verbose:
                    print(f"[warn] Skipping event '{name}' - missing timestamps")
                continue

            # Convert Unix timestamps to datetime in Eastern time
            # NOTE: The MyVMK API has a bug where timestamps are stored with the
            # UTC offset ADDED instead of subtracted (i.e., Eastern times stored as UTC values).
            # The timestamps are consistently 5 hours (EST offset) ahead of correct values.
            # Fix: subtract 5 hours (18000 seconds) and interpret as local time.

            # Correct the API timestamps by subtracting 5 hours (the EST offset that was incorrectly added)
            corrected_start_ts = start_ts - 18000  # 5 hours in seconds
            corrected_end_ts = end_ts - 18000

            # Convert to naive datetime - fromtimestamp without tz uses local time
            # which gives us the correct Eastern time
            start_dt = dt.datetime.fromtimestamp(corrected_start_ts)
            end_dt = dt.datetime.fromtimestamp(corrected_end_ts)

            # Validate: end time must be after start time
            # If end is before start, assume 1-hour duration (API data error)
            if end_dt <= start_dt:
                if verbose:
                    print(f"[warn] Fixing event '{name}' - invalid end time, assuming 1 hour duration")
                end_dt = start_dt + dt.timedelta(hours=1)

            # Build description with host info
            full_desc = description
            if host:
                full_desc = f"Host: {host}\n{description}" if description else f"Host: {host}"

            events.append({
                "title": name,
                "start": start_dt,
                "end": end_dt,
                "id": event_id,
                "description": full_desc,
            })

            if verbose:
                print(f"[debug] Parsed: {name} @ {start_dt.strftime('%Y-%m-%d %H:%M')}")

        except Exception as e:
            if verbose:
                print(f"[warn] Failed to parse event: {e}")
            continue

    if verbose:
        print(f"[info] Parsed {len(events)} events total")

    return events


def main():
    ap = argparse.ArgumentParser(description="Convert MyVMK events to .ics using the API")
    ap.add_argument("--out", default="myvmk.ics", help="Output .ics filename")
    ap.add_argument("--tz", default="America/New_York",
                    help="TZID for DTSTART/DTEND (VTIMEZONE emitted for America/New_York)")
    ap.add_argument("--no-merge", action="store_true",
                    help="Don't merge with existing ICS file (fresh start)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        # Fetch new events from API
        new_events = fetch_events(args.verbose)

        if args.no_merge:
            events = new_events
            if args.verbose:
                print(f"[debug] Skipping merge, using only new events")
        else:
            # Parse existing ICS file to preserve historical events
            existing_events = parse_existing_ics(args.out, args.verbose)

            # Merge existing and new events
            events = merge_events(existing_events, new_events, args.verbose)

        # Build and write ICS
        ics_text = build_ics(events, args.tz)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(ics_text)
        print(f"Wrote {len(events)} events to {args.out}")
    except Exception as e:
        print("Error:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
