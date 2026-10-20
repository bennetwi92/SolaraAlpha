# 0029: Research protocol for technical signals: one target, a locked holdout, one bar

**Status:** accepted (owner and PM, 2026-10-20, issue #210). Applies to every test of a
technical, levels or relative-strength signal and to any rank model. Changes need the owner.

**Context.** No technical signal in the pipeline has shown edge (ADR 0014 amendment #192,
docs/research/0001). The history is short: after warm-up there are about 17.6 independent
8-week periods in-sample and 6.5 in the last year (#210, Q2). With so few, the real risk is
fooling ourselves, so the target, the held-back data and the pass bar are fixed before any
test is run.

**Decision.**

*Target.* `burst_10w` = 1 if, from **Monday's open** after the signal Friday (buy-stops: the
trigger, or the open when Monday gaps above it), the daily high reaches entry × 1.15 before the
daily low reaches entry − 3 × daily ATR(14), within 50 sessions; else 0. The same at 2 and 4
ATR is reported alongside. Friday's close is never the entry.

*Holdout (locked).* Signal weeks with Fridays from **2025-10-24 to 2026-10-16** (52 weeks). No
one looks at results on these weeks until a candidate has passed in-sample and the owner signs
off. A week is in-sample only if its whole forward window ends on or before **2025-10-17**: the
last in-sample signal Friday is 2025-08-22 for 8-week labels and 2025-08-08 for `burst_10w` (50
sessions). The first replayable card is 2022-12-16 (60-week warm-up). The holdout is already
spent for `trend`, `compression` and `growth`, which were studied on full history (#192,
research 0001, #210 Q9); it stays clean for every new signal. Live scans from 2026-10-20 on are
a second, true out-of-sample set. The holdout gate is a sign check: the same sign as in-sample
and at least half its size (its minimum detectable IC is about 0.08).

*Pass bar* (two tiers, #210 Q4, accepted by the PM):

| tier | slice | requirement |
|---|---|---|
| detection | full cross-section | 8-week rank IC against the target, beating a vol + ADV logistic null; 90% moving-block bootstrap interval (16-week blocks, all stocks in a week resampled together) excluding zero; same sign in at least 5 of the 6 in-sample half-years (⌈5/6 × available⌉ for longer warm-ups); adjusted for the number of variants tried |
| economics | top 3 a week and the 5-slot replay (heat, 5% ADV cap, 7-day validity, one order per stock) | simulated expectancy above +0.2R after 2× costs (½ × max(60-day median, 60-day mean) Corwin-Schultz spread per side), interval shown, survives dropping the best 3 names |

*Pre-registration.* Each test is written down first: signal, target, horizon, variants, the bar
and its minimum detectable effect. "Material" filings are a fixed list of NSM type codes; event
signals use NSM filings only (headlines are not point-in-time). A failed signal gets its note
too.

*Power.* The programme confirms only large effects: about IC 0.05–0.07 or 0.3R (#210 Q3). A fail
means "not shown above the MDE", and every note states its MDE.

*Simple models.* Rank-transformed features, logistic regression, walk-forward. No flexible ML
before several hundred live trades. No per-stock tuning; volatility is never a gate.

**Consequences.** Research notes in `docs/research/` follow this protocol and say so. The rank
weights stay as they are until a model passes (owner, 2026-10-20); the packs say the rank is
unvalidated (`fit.RANK_NOTE`). History stays at 5 years (owner declined a research-only 10-year
fetch; revisit after the survivorship rebuild).
