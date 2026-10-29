# Research 0011: The AIM research universe: coverage, noise and size groups

*2026-10-29. A data note, not a signal test: no forward return was computed for it. Issue #290,
epic #283, the universe the owner chose on #288 (research 0010 amendment). Nothing in the live
pipeline or watchlist changes.*

## The answer in one paragraph

The research universe is **556 AIM companies that Trading212 offers**
(`config/research_universe.toml`, snapshot 2026-10-28). 545 have Yahoo prices in the in-sample
window and **about 290–320 pass the £10k research floor on a typical in-sample Friday**, three
times today's 104-stock watchlist. Half of those are micro caps (under £50m). A first build
leaked **20 non-AIM lines** (3.5%: Main Market shares suspended for a takeover, Specialist Fund
Segment vehicles, standard-list shells), which are now excluded. The NSM holds filings for 541
of the 556 (97%); the other 15 cannot enter a filing-based test.

## Coverage per in-sample year

In-sample signal Fridays (ADR 0029, `burst_10w`): 2022-12-16 to 2025-08-08. "Past £10k" uses the
20-session median traded value as of each Friday (`analysis.universe.weekly_adv`), never today's.
Size groups per Friday: `aim100` = today's watchlist (FTSE AIM 100 + AIM UK 50, the owner's
control), else `micro` under £50m market cap, else `rest`.

| year | in-sample Fridays | with Yahoo history | past £10k on any Friday | past £10k per Friday (median) | of which aim100 | rest | micro |
|---|---|---|---|---|---|---|---|
| 2022 | 3 | 518 | 302 | 281 | 89 | 87 | 105 |
| 2023 | 52 | 526 | 424 | 292 | 90 | 90 | 114 |
| 2024 | 52 | 536 | 430 | 304 | 95 | 84 | 127 |
| 2025 | 32 | 545 | 429 | 323 | 98 | 78 | 144 |

- 518 companies have prices from the first in-sample Friday; 27 start later (listed, or first
  traded, during the window); 11 have no bars in the window at all (listed after it).
- Over the window, 483 companies pass the floor on at least one Friday: by their usual size group,
  274 micro, 107 rest, 101 aim100 (1 unknown).
- The count past the floor rises through the window. Part of that is survivorship: a company
  that left AIM before 2026-10-28 is not in the list at all (#291 bounds it).
- Market cap = close × the ordinary share count of the latest annual statement public by that
  Friday (`fundamentals.known`). For 17% of stock-weeks, mostly in 2023, no statement in Yahoo's
  four years was public yet, so the earliest one is used (`size_pit` False). This moves only
  the size group a week is reported in, never a signal.

Reproduce: `uv run python scripts/research/coverage.py RAW_DIR` (fetches into `RAW_DIR`, keep it
out of `data/`).

## Is it all AIM? The noise check

The universe is Trading212's London GBX share lines minus every ISIN on the FCA Official List
(AIM shares are not listed securities). The check was done twice.

1. **The planned random sample** (40 names, seed 290): all 40 are AIM companies. Each one's
   latest filings name AIM or a nominated adviser. The one hint of another market (EDX Medical)
   was a director buying on the Aquis venue.
2. **Every line**, because 40 names cannot see a 3% leak: each company's latest non-dealing
   filings were searched for "AIM", "nominated adviser", "Main Market", "Official List" and
   "Specialist Fund Segment". 100 names had no "AIM" in their latest two; their latest ten were
   read for the same words, and the unclear ones by hand.

That found **20 lines that are not AIM**:

| kind | lines |
|---|---|
| Main Market shares suspended for a takeover or cancellation (they drop off the *live* Official List) | Senior, Pinewood Technologies, Headlam |
| Specialist Fund Segment or Main Market investment companies (never on the Official List as listed shares) | Tetragon, JZ Capital Partners, EJF Investments, Castelnau, Sure Ventures, UIL |
| Standard-list shells and small Main Market companies, mostly suspended | BAY Capital, Golden Rock Global, Hellenic Dynamics, Kanabo, New Frontier Minerals (Castillo Copper), Pennpetro Energy, Pristine Capital, PYX Resources, Supply@Me Capital, Apertura Energy (Red Capital), Adalan Ventures |

3.5% of the list, and concentrated in exactly the names (shells, suspended stocks) that would
distort an event study, so it is material. They are excluded by ISIN in
`official_list.NOT_AIM` (applied by `aim_lines`, so a refresh keeps them out) and removed from
the snapshot. A future refresh can let in a new suspended Main Market name; the check above is
the one to repeat after each refresh.

## LEIs: can the NSM see each company?

Event tests read the NSM by LEI. Every LEI was checked for filings since 2022.

- **7 LEIs were wrong** (GLEIF links the ISIN to a different entity than the one that files, or
  none): hVIVO, Invinity Energy Systems, Cornish Metals, Strip Tinning, Digitalbox, Engage XR,
  HUTCHMED. Fixed by hand to the NSM filer's LEI; `scripts/research_universe.py` now keeps
  GLEIF's LEI only when the NSM has filings under it, else takes the NSM filer of the same name.
- **15 companies have no NSM filings to read**: 10 whose LEI has none since 2022 (Arbuthnot
  Banking, Andrews Sykes, Conygar, Celebrus, Chesterfield Special Cylinders, Ingenta, KCR
  Residential REIT, Light Science Technologies, Symphony Environmental, Mission Group) and 5
  with no LEI found (Eco Atlantic, Harvest Minerals, Alef Bet, Secure Property Development,
  Talisman Metals). Their announcements go out through a service the NSM search does not index
  under that LEI. They stay in the universe for price-only tests and are reported as missing in
  filing-based ones.

## The panels on this universe

`scripts/research/build_panel.py OUT_DIR research` (research 0002 / 0003 panel) and
`scripts/research/r9_relative_strength.py build PANEL_DIR research` (0009) build the same
per-Friday rows for the research universe, with `floor`, `mcap`, `size_pit` and `size` added
(`analysis.universe.with_universe`). The reports take the research floor: `r5_rank_growth.py
PANEL --floor 10000`, `r9_relative_strength.py report PANEL research`. The NSM reads go through
`http.patient` (retry with waits), because the NSM answers 403 to bursts of parallel reads.

## Caveats

- **Survivors only.** Every company is one still trading on 2026-10-28; leavers have no free
  price history. The leavers ledger (#291) gives the bound each wide-universe note reports.
- **Trading212 only.** AIM companies Trading212 does not offer are out (owner's scope: what we
  could trade).
- **The control is today's membership**, not point-in-time index membership. It is the list the
  live pipeline runs on, which is the comparison the owner asked for.
- Yahoo's share counts are annual only; a placing between statements leaves the count, and so
  the market cap, low until the next statement.
