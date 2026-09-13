# Broadcast evidence model

SLP separates three questions that must never be collapsed into one boolean:

1. **ON_AIR** — this programme is the channel's current slot.
2. **DIRECT claim** — a broadcaster/guide explicitly marks the programme LIVE/direct.
3. **Real-event confirmation** — the sports event actually occurs at the claimed date/time.

## Trust rules

- Qazsport row-level `LIVE` badge inside the concrete `program-item`: official, high-confidence DIRECT claim. It may confirm a sports broadcast even when Championat does not list the event. A non-sport row is never promoted.
- Sport+ `div.blink a[title=LIVE]`: official ON_AIR marker only. The anthem proves why this must not be treated as DIRECT. Sport+ direct sports still require the programme text `Прямая трансляция` / equivalent.
- VseTV `img src="pic/ico_live.gif"`: third-party DIRECT claim. It is useful evidence but cannot confirm a broadcast by itself. It must be paired with Championat or an official event/source match before publication.
- TV+/Mobikino ordinary EPG: schedule evidence only; never DIRECT evidence.
- Q Arena, Q Football and Q League are a stricter TV+ exception: their EPG
  slots become publishable only after Championat confirms the same event on the
  same local date and minute. The generic official-source fallback is disabled
  for these channels, so an unmatched or time-shifted Q slot stays hidden.

The normalized event stores additive `broadcast_evidence` records so provenance is preserved instead of overwritten by one `is_live` flag.
