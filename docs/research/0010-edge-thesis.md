# Research 0010: Edge thesis: where an edge on AIM should come from

*2026-10-28. A framing note, not a signal test: no return was computed for it. It follows
research 0001–0009 and sets the order of the next tests (epic #283). Every test it proposes
runs under ADR 0029. Nothing in the pipeline is changed.*

## The answer in one paragraph

The toolkits score every stock every week on public signals (trend, relative strength,
compression, growth). Everyone can compute those, so they have mostly been traded away, and our
tests show it: little or no edge. The results that did hold up are about what to **avoid**
(`rs_spike`, profit warnings), and both have behavioural explanations. So we change the question
from "which indicator predicts returns?" to **"who is on the other side of this trade, and why
are they wrong or forced to be there?"** On AIM the honest answers are few and specific: forced
or impatient sellers, slow-moving news in neglected stocks, insiders who know more, short
sellers who cannot act, and biased holders. Edges like that come in **situations**, not
continuous scores. The next tests look for situations, in this order: director buys (#284),
placings (#285), the 52-week-high anchor and the gains overhang (#286), and a pass of exclusion
rules (#287). One question only the owner can answer is whether research should go below the
FTSE AIM 100 / AIM UK 50 (#288). That is where most of these edges should be largest.

## What the tests so far say

| note | signal | result |
|---|---|---|
| 0001, 0009 | relative strength `rs` | no edge on `burst_10w` or the 8-week excess return |
| 0009 | `rs_spike` (a short burst without an uptrend) | **a real warning**: about −4% over 8 weeks, in every half-year |
| 0009 | `leadership` | leans the right way (+2–3%), weak |
| 0003 | fundamentals `growth` | IC about +0.07 against vol-adjusted `burst_10w`, borderline |
| 0002 | missing `growth` as neutral in the rank | moves about 1 name a week, no material effect |
| 0007 | reaction to news | AIM prices news within minutes; a 1–3% drift after good news, mainly in the less liquid names |
| 0008 | results-day gaps | finals and interims were not the danger; **trading statements (profit warnings)** were, and the gap cap did not help |

Three readings follow.

1. **Public signals get competed away.** Published return predictors lose much of their power
   once they are known: about a quarter out of sample and more than half after publication
   (McLean & Pontiff 2016). Trend and relative strength are the most published of all.
2. **We score everything, all the time.** A weekly score across 104 stocks spreads whatever
   signal there is thinly. Most stocks in most weeks have no situation at all.
3. **The things that worked are behavioural.** `rs_spike` looks like attention-driven buying
   that reverses (Barber & Odean 2008). Profit warnings hit hard because bad news leaks late
   where short selling is difficult (Miller 1977; Hong, Lim & Stein 2000).

## The thesis: the test every candidate edge must pass

Before any data is pulled, a candidate edge is written down with four answers.

1. **The counterparty.** Who is on the other side, and why do they trade at a bad price? Their
   reason must be something other than information about the stock: forced, impatient, biased
   or constrained.
2. **The friction.** Why hasn't someone bigger traded it away? Capacity, short-sale limits,
   mandate rules, coverage, cost.
3. **Why AIM.** Why should it be bigger in small AIM stocks than on the FTSE 100?
4. **Why us.** Why can a £5,000 long-only ISA book trading weekly capture it? We cannot react
   within minutes (0007), cannot short, and pay the spread.

An idea that cannot answer all four is not tested. "It worked in a backtest" is not an answer
to any of them.

## Where an edge on AIM can come from

### Structural (basic market economics)

| id | edge | counterparty | friction | why AIM | why us |
|---|---|---|---|---|---|
| E1 | **Paid to supply liquidity** (Grossman & Miller 1988; Coval & Stafford 2007) | forced or impatient sellers: placees selling after a discounted placing, removals from the AIM index, AIM fund redemptions, IHT-portfolio trims, tax-loss selling before 5 April, lock-up expiries | the buyer side is thin; funds have size and mandate limits | very little natural liquidity; placings are frequent and priced at a discount | we buy small amounts over a few days, which is exactly the liquidity a seller needs |
| E2 | **Neglect and slow diffusion** (Hong & Stein 1999; Hong, Lim & Stein 2000) | holders and buyers who have not noticed or processed the news | little analyst coverage (often only the house broker), research behind paywalls | coverage is thinnest here; 0007 found drift only in the less liquid names | weekly is slow enough to catch a drift that runs over weeks, but not a same-day reaction |
| E3 | **Short-sale limits** (Miller 1977) | over-optimistic holders; pessimists who cannot short | AIM stock is hard and costly to borrow | almost none of it is shortable | long only, we use it to **avoid** overpriced names, not to short them |
| E4 | **Informed insiders** (Fidrmuc, Goergen & Renneboog 2006) | the market, which knows less than the directors | MAR closed periods; buys are disclosed but rarely read | directors are often the main owners; the effect is strongest in small, thinly held firms | NSM `DSH` notices are free and public, and the window is weeks |
| E5 | **Our own size** | institutions that cannot trade small stocks | a £100m fund cannot hold £1,250 positions | most AIM stocks are too small for funds | at 25% of £5,000 and 5% of ADV we can trade down to £25k ADV: our floor today |

### Behavioural (biases in the holders)

| id | bias | what it does to prices | measurable from |
|---|---|---|---|
| B1 | **Anchoring to the 52-week high** (George & Hwang 2004) | traders treat the high as a ceiling, under-react to good news near it, and the move continues once the high breaks | prices only |
| B2 | **Disposition effect / gains overhang** (Shefrin & Statman 1985; Grinblatt & Han 2005) | holders sell winners early and hold losers. A stock with large unrealised gains has sellers that later run out; a stock with holders under water meets supply at their cost price | prices and volume (a turnover-weighted reference price) |
| B3 | **Preference for lottery-like stocks** (Bali, Cakici & Whitelaw 2011; Barber & Odean 2008) | stocks with extreme one-day jumps and attention draw too many buyers, are overpriced and then lag (probably what `rs_spike` sees) | prices only |
| B4 | **Anchoring to reference prices** (Kahneman & Tversky 1979) | the placing price, the IPO price and round numbers act as support or resistance because large holders anchor to them | NSM placing prices; prices |

## Design rules for the next tests

- **Situations, not scores.** A test asks "after event X, what happens?" (an event study), not
  "does score Y rank stocks?". An edge that passes reaches the agents as a **situation route**,
  like `catalysts.fresh_catalysts` does today. It does not become a fourth term in `rank_score`
  unless the owner decides otherwise.
- **ADR 0029 as it stands.** Pre-registered before outcomes; target `burst_10w` net of the
  vol + ADV null; `xs_8w` secondary; in-sample Fridays only; holdout (2025-10-24 to 2026-10-16)
  locked; Bonferroni over the variants; 5 of 6 half-years; robust to dropping the best 3 names.
- **The right null for events.** Many situations come right after results (director buys after
  a closed period, placings with results). The null is "a results week without the event", as
  in 0005. Otherwise we only rediscover the results week.
- **Count the events before testing.** A pre-registration states the event count from metadata
  only (power, as in 0005). If today's 104 names give too few events, the note says so, and
  that is evidence for #288.
- **Exclusions count.** For a long-only book, a rule that removes the worst 10% of names
  without removing many bursts is worth as much as a rule that finds winners (#287).

## Order of the tests

| order | issue | edge | why this order |
|---|---|---|---|
| 1 | #284 | E4: director-buy clusters (research 0005, already pre-registered) | cheapest: NSM data only, pre-registration already written |
| 2 | #285 | E1, B4: placings event study | the most AIM-specific forced-seller case; `financing.py` already finds the raises |
| 3 | #286 | B1, B2: the 52-week-high anchor and the gains overhang | prices and volume only, on the panel we already build |
| 4 | #287 | B3, E3: exclusion pass (lottery stocks, `rs_spike`, overdue updates) | builds on 0008 and 0009; long-only value |
| first | #290, #291 | E5: the research universe | **decided by the owner (#288), see the amendment below**; the tests wait for it |

Two more ideas are noted and left until later:

- **The good-news drift catalyst tier** (E2), already proposed in 0007. It belongs with the
  fundamental strategist's catalyst work (#214).
- **Calendar flows** (E1): tax-loss selling before 5 April, AIM index reviews, the IHT relief
  halving from April 2026. Each has only a handful of in-sample occurrences. They are context
  for the agents, not testable signals yet.

## Amendment 2026-10-28: the universe (#288)

The owner decided to go well below the AIM 100, to micro caps:

- **AIM only.**
- **No upper size limit.** The whole of AIM is in, with the AIM 100 as the control, and results
  are split by size (AIM 100 / the rest / under £50m).
- **Research liquidity floor £10k a day** (20-session median, point-in-time). The live
  `min_adv_gbp` stays at £25k.

A data check from the cloud container found:

- Yahoo has full daily history for small AIM stocks still listed, and nothing for delisted ones.
- The LSE blocks the cloud, so the company list is fetched by hand.
- The NSM still holds the filings of companies that left.

So the universe is built first (#290), with a leavers ledger from the NSM (#291) to bound the
survivorship bias every wide-universe note reports. Research 0005 is amended to the wide
universe before it runs (#284).

2026-10-29: the owner also put the whole research universe into the live weekly scan, ahead of
any signal passing (docs/decisions/0038).

## Caveats

- Every source below is a **prior**, mostly from US or main-market data. None of them is
  evidence for our universe.
- Today's universe is the largest and best-covered AIM stocks, and it is a survivor snapshot
  (`config/watchlist.toml`, not point-in-time). Both make edges E1–E5 harder to find here than
  on AIM as a whole.
- `burst_10w` is a demanding target. A real edge may show in `xs_8w` and not in the burst rate.
  The pre-registrations name the primary target before any outcome is computed.

## Sources

- Bali, Cakici & Whitelaw (2011), "Maxing out: stocks as lotteries and the cross-section of
  expected returns", *JFE* 99(2).
- Barber & Odean (2008), "All that glitters: the effect of attention and news on the buying
  behavior of individual and institutional investors", *RFS* 21(2).
- Barnes & Walker (2006), "The seasoned-equity issues of UK firms: market reaction and issuance
  method choice", *Journal of Business Finance & Accounting* 33(1-2).
- Coval & Stafford (2007), "Asset fire sales (and purchases) in equity markets", *JFE* 86(2).
- Fidrmuc, Goergen & Renneboog (2006), "Insider trading, news releases, and ownership
  concentration", *JF* 61(6).
- George & Hwang (2004), "The 52-week high and momentum investing", *JF* 59(5).
- Grinblatt & Han (2005), "Prospect theory, mental accounting, and momentum", *JFE* 78(2).
- Grossman & Miller (1988), "Liquidity and market structure", *JF* 43(3).
- Hong & Stein (1999), "A unified theory of underreaction, momentum trading, and overreaction
  in asset markets", *JF* 54(6).
- Hong, Lim & Stein (2000), "Bad news travels slowly: size, analyst coverage, and the
  profitability of momentum strategies", *JF* 55(1).
- Kahneman & Tversky (1979), "Prospect theory: an analysis of decision under risk",
  *Econometrica* 47(2).
- McLean & Pontiff (2016), "Does academic research destroy stock return predictability?",
  *JF* 71(1).
- Miller (1977), "Risk, uncertainty, and divergence of opinion", *JF* 32(4).
- Shefrin & Statman (1985), "The disposition to sell winners too early and ride losers too long",
  *JF* 40(3).
