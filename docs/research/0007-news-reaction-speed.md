# Research 0007: How fast do AIM prices absorb news? (against the FTSE 100)

*Exploratory, 2026-10-27. **Not a pre-registered signal test** (ADR 0029): it describes how
prices react, to check a claim made in a chat. Any trading rule it suggests has to be
pre-registered and tested on its own. Script: `scripts/research/r7_reaction_speed.py`.*

## Question

The claim from an earlier chat was that large US stocks absorb news in minutes, while London AIM
stocks take days. Is that true for our universe? It matters for the strategy (ADR 0022: catalyst-led,
weekly). If AIM takes days, a Monday entry after a news week still catches part of the move. If
it takes minutes, the reaction is gone before the weekly pipeline sees it, and only a slower
drift (if one exists) is left to trade.

## What the literature says

- **US large caps react within seconds to minutes.** The classic result is from Patell & Wolfson
  (1984): most of the reaction to earnings and dividend news is in the first trades, and simple
  trading rules stop earning within 5 to 10 minutes. Recent work finds the largest stocks
  reprice within milliseconds ("Warp speed price moves", *JFE* 2025).
- **Small caps are slower, but by minutes, not days.** NYSE estimates are about 5 to 15 minutes
  for large stocks and about 20 minutes longer for small ones. Grégoire & Martineau (*JAR* 2022)
  find that trade prices of S&P 600 small caps lag their own quotes after earnings, while S&P 500
  prices do not. For thinly traded names, the trade price understates how fast the quote moves.
- **What lasts for days is drift, not the reaction itself.** Post-earnings-announcement drift
  (prices keep moving in the direction of the surprise for weeks) is concentrated in small,
  thinly covered stocks. In the UK, Hew, Skerratt, Strong & Walker (1996) find drift for small
  firms but not large ones, and Liu, Strong & Xu (2003) confirm UK drift after controlling for
  risk. Transaction costs take much of the profit in small caps (Ng, Rusticus & Verdi 2008).
- **No study measures AIM specifically** at intraday frequency (searched 2026-10-27).

## Data and method

- **Universe.** The 104 watchlist stocks (AIM; a survivorship-biased snapshot, see below) and 28
  FTSE 100 stocks as a same-exchange control: same 07:00 RNS timing and the same 07:50-08:00
  opening auction. Barclays is excluded because its filing count exceeds the NSM's result cap.
- **News.** NSM filings of business-news types (`NEWS` in the script: results, trading
  statements, updates, contracts, M&A, equity issues, and others). Holdings, director dealings,
  buybacks and notices are excluded. For each stock, the earliest such filing per session is
  placed in its reaction session (`events.reaction_session`). A **big** news day has a
  market-adjusted day-0 move with |z| ≥ 2 against the stock's 60-day volatility.
- **Intraday.** Yahoo 5-minute bars, which cover only the last ~60 days (4 Aug to 27 Oct 2026).
  The sample is the pre-market filings (07:00-07:59). For each, the script measures how much of
  the day's close-to-close move the last trade had reached at each time.
  This sample overlaps the ADR 0029 holdout weeks. It reads only within-day price paths, none of
  the holdout's `burst_10w` outcomes.
- **Daily drift.** Market-adjusted (`^FTAI` / `^FTSE`) returns 5, 10 and 20 sessions after day
  0, signed by day 0's direction. The sample is in-sample only: reaction sessions up to
  2025-09-18, so every window ends by 2025-10-17 (ADR 0029).
  **Survivorship correction:** the watchlist is today's index members, which went up. An average
  day for these stocks is followed by +1.6% over 20 sessions on AIM (+0.35% on the FTSE 100).
  That baseline is subtracted. t-statistics are clustered by date.

## Results

### 1. AIM reacts in minutes, not days

The table shows the share of the day's move made by each time, for big pre-market news days.

| | n | median day move | at the open | 08:15 | 08:30 | 09:00 | 10:00 | 12:00 | 15:00 |
|---|---|---|---|---|---|---|---|---|---|
| AIM | 57 | 7.8% | 55% | **78%** | 80% | 81% | 86% | 93% | 98% |
| FTSE 100 | 9 | 5.2% | 43% | 62% | 73% | 72% | 71% | 81% | 95% |

On AIM, about four-fifths of the day's move is in the price within the first 15 minutes of
trading. The FTSE 100 sample is too small to rank the two markets
(n = 9). The useful result is that AIM is not slow on the day.

The 08:05 figure (the first 5-minute bar) sits below the official open. This is a quirk of
Yahoo's first bar after the auction, and the bar is not used for anything else.

### 2. The "days" part: some drift after good news, in the least liquid AIM names

Market-adjusted move after big news days, minus the survivorship baseline, signed by day-0
direction:

| | n | +5d | +10d | +20d |
|---|---|---|---|---|
| AIM, good news | 570 | +0.9% (t 1.9) | +1.0% (t 1.8) | +1.5% (t 2.0) |
| AIM, bad news | 317 | −0.2% (t −0.8) | −0.3% (t −0.3) | +0.2% (t 0.4) |
| FTSE 100, good news | 124 | +0.3% (t 0.6) | +0.2% (t −0.1) | +0.2% (t 0.2) |
| FTSE 100, bad news | 100 | −0.2% (t −1.1) | −0.2% (t −0.8) | +0.2% (t 0.3) |
| AIM good news, low liquidity (£32k ADV) | 218 | +1.0% (t 1.1) | +2.1% (t 2.2) | +2.7% (t 2.1) |
| AIM good news, mid liquidity (£152k) | 187 | +1.2% (t 1.9) | +1.6% (t 1.8) | +2.0% (t 1.5) |
| AIM good news, high liquidity (£884k) | 165 | +0.5% (t 1.1) | −1.0% (t −1.1) | −0.6% (t −0.2) |
| AIM good final results (`FR`) | 77 | **+3.0% (t 3.7)** | +2.3% (t 2.5) | +1.4% (t 1.3) |
| AIM good half-year results (`IR`) | 64 | **+2.4% (t 3.0)** | +2.1% (t 2.1) | +2.4% (t 1.5) |
| AIM good trading statements (`TST`) | 119 | +0.1% | −0.2% | +0.2% |

- **The FTSE 100 shows no drift** in either direction.
- **Bad news on AIM is priced on the day.** There is no further drift down.
- **Good news on AIM drifts a little further**, by about 1 to 3% over 1 to 4 weeks against a
  day-0 move of about 8%. The drift sits in the low- and mid-liquidity thirds, and the most
  liquid third shows none.
- **The strongest cell is good results.** After good finals or interims the stock moves another
  2 to 3% in the first week, with t about 3 to 3.7. Good trading statements show nothing.

## Caveats

- **Multiple comparisons.** Thirteen cells are reported. Only the results cells (`FR`, `IR` at
  +5d) are clearly beyond t = 2. The rest are suggestive.
- **Costs.** In the low-liquidity third, the spread (`liquidity.py`'s Corwin-Schultz proxy) may
  take most of a 2 to 3% drift.
- **Thin intraday sample.** The intraday table covers 57 AIM events (and 9 FTSE events) over
  60 days of a single market regime, because Yahoo keeps only 60 days of 5-minute bars.
- **Survivorship.** The baseline correction is crude. The bias inflates good-news drift most in
  the stocks that later grew into the index, which are the illiquid ones. This could explain
  part of the low-liquidity result.
- **Direction is taken from the price.** "Good news" here means a big up day, not a reading of
  the filing. Research 0004 found the expectations wording too imprecise to use for direction.

## What it means for the desk

1. **The claim is half right.** It is wrong about the reaction: AIM prices most news within
   minutes of the 08:00 open, much like any market. It is partly right about drift: on good
   news, and mainly in the less liquid AIM names, part of the move arrives over the following
   1 to 4 weeks. On the FTSE 100 no such drift appears.
2. **The weekly pipeline cannot catch the reaction.** By the Friday close, a news move is already
   in the price. What is left is the drift. Its size, about 1 to 3%, is small next to the
   +15-30% bursts the strategy looks for. At best it is a tilt in the rank or the catalyst tier,
   not a strategy of its own.
3. **Candidate for a pre-registered test.** A rule such as "good results day (|z| ≥ 2 up on
   `FR` / `IR`) → +1 catalyst tier for the next weekly scan", tested on `burst_10w` per ADR 0029.
   That decision is for the fundamental strategist (catalysts, #214) and the senior quant (#210).
   This note proposes it and does not build it.

## Sources

- Patell & Wolfson (1984), "The intraday speed of adjustment of stock prices to earnings and
  dividend announcements", *JFE* 13(2):
  <https://www.sciencedirect.com/science/article/abs/pii/0304405X84900242>
- "Warp speed price moves: Jumps after earnings announcements", *JFE* (2025):
  <https://www.sciencedirect.com/science/article/abs/pii/S0304405X25000182>
- Grégoire & Martineau (2022), "How is earnings news transmitted to stock prices?", *JAR*:
  <https://onlinelibrary.wiley.com/doi/10.1111/1475-679X.12394>
- Liu, Strong & Xu (2003), "Post-earnings-announcement drift in the UK", *European Financial
  Management* 9(1): <https://research.manchester.ac.uk/en/publications/post-earnings-announcement-drift-in-the-uk/>
- Hew, Skerratt, Strong & Walker (1996), "Post-earnings-announcement drift: some preliminary
  evidence for the UK": <https://research.manchester.ac.uk/en/publications/post-earnings-announcement-drift-some-preliminary-evidence-for-th/>
- Fink (2020), "A review of the post-earnings-announcement drift":
  <https://static.uni-graz.at/fileadmin/sowi/Working_Paper/2020-04_Fink.pdf>
- "How rapidly do stock prices adjust to firm-specific information" (NYSE large vs small):
  <https://zenodo.org/records/21129325>
- Intraday dividend-change adjustment (negative news up to ~75 minutes):
  <https://www.sciencedirect.com/science/article/abs/pii/0378426694001065>
