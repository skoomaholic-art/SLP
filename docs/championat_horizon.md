# Championat future horizon

SLP treats TV+/Mobikino EPG as a channel schedule, not as proof of a live sports broadcast.

For current events, the cached Championat match center is the primary reconciliation dataset. For the next seven days, SLP derives unique sport/tournament hints from the EPG, discovers matching Championat tournament pages in batched sport sections, fetches only the selected tournament calendar pages, and caches them. Matching against EPG is then local.

Publication states:

- `confirmed_direct`: real event and TV slot match within the allowed pre-show/late-join window; eligible for the bot.
- `mismatch`: the real event exists at a different time/date; treated as replay/non-live.
- `unknown`: Championat does not confirm the row; near-term events may use the strict official-organizer fallback, otherwise they remain unpublished.

This prevents a large EPG from being mistaken for a large LIVE schedule while retaining future schedule coverage without issuing one web request per EPG row.
