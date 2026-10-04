# BTC5m Grid Gamma Opportunity Study — G0 (2026-10-04)

Status: RESEARCH COMPLETE — AWAITING INDEPENDENT REVIEW; no reviewed checkpoint or live authority. Git base: `m4a-a1-pass` (`97ab0f9`), with pre-existing unrelated dirty working tree preserved.

## Result

**NO MATERIAL GRID EDGE**

## Sample

Exactly 32 execution-replayable BTC5m markets from the saved 2026-10-03 audit; 32/32 local official settlement identities matched the capture manifests. Labels did not enter edge calculations. The 32-market selector establishes execution replay, not full M4A telemetry acceptance; the earlier audit records only 29/42 sessions passing the stricter integrity gate.

## Grid Activity

- 1,921 crossings, 60.03 per market (median 50.5); pooled GridCrossingRate 0.258/valid-book second.
- 740 alternating pairs, 23.12 per market (median 19.0). Counts include same-grid reversals with zero edge and rolling reuse of a completing crossing.
- Markets with ≥1 / ≥3 / ≥5 alternating pairs: 32/32 (100.0%), 31/32 (96.9%), 29/32 (90.6%). Markets with any **positive-edge** pair: 13/32 (40.6%).

## Theoretical Pair Edge

- Mean 0.001824 (0.182¢); median 0.00; p25 / p75 / p90 = 0.00 / 0.00 / 0.00.
- Positive / zero / negative pairs: 27 / 713 / 0; only 3.6% are positive.
- Total theoretical edge per share summed over all rolling pairs: 1.35; mean 0.04219 per market (4.219¢), median 0.00. Pooled TheoreticalGridEdgePerMinute is 0.01088 per share per valid-book minute. These rolling sums are an opportunity index, not additive realizable PnL.

## Timing

| Elapsed | Valid-book seconds | Markets observed | Crossings | Crossings/active min | Pairs initiated | Pairs completed | Completed/active min | Positive-edge completed | Mean edge | Median edge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0-30s | 941.1 | 32 | 222 | 14.15 | 96 | 86 | 5.48 | 3 | 0.0017 | 0.00 |
| 30-60s | 959.7 | 32 | 181 | 11.32 | 71 | 75 | 4.69 | 2 | 0.0013 | 0.00 |
| 60-90s | 960.0 | 32 | 199 | 12.44 | 67 | 66 | 4.13 | 1 | 0.0008 | 0.00 |
| 90-120s | 959.5 | 32 | 169 | 10.57 | 65 | 64 | 4.00 | 0 | 0.0000 | 0.00 |
| 120-150s | 951.0 | 32 | 193 | 12.18 | 58 | 57 | 3.60 | 2 | 0.0018 | 0.00 |
| 150-180s | 920.7 | 31 | 193 | 12.58 | 77 | 82 | 5.34 | 3 | 0.0018 | 0.00 |
| 180-210s | 779.4 | 29 | 244 | 18.78 | 99 | 95 | 7.31 | 4 | 0.0021 | 0.00 |
| 210-240s | 577.9 | 25 | 234 | 24.29 | 89 | 95 | 9.86 | 5 | 0.0026 | 0.00 |
| 240-270s | 327.7 | 20 | 257 | 47.06 | 105 | 106 | 19.41 | 7 | 0.0033 | 0.00 |
| 270-300s | 66.7 | 7 | 29 | 26.10 | 13 | 14 | 12.60 | 0 | 0.0000 | 0.00 |

First 150s versus later observed time: crossings 964 versus 957, and pair completions 348 versus 392. Per valid-book minute, crossing activity is 12.12 versus 21.49; completions are 4.38 versus 8.80. The observed rates do **not** decline, but coverage falls from 32 markets in each early bucket to 7 in the last bucket, so this is not evidence of late opportunity for the full 32-market universe. This metric is Grid Crossing Activity, not mathematical Gamma.

## Completion Time

- Mean 2.280s; median 0.709s; p75 2.761s; p90 5.772s; max 38.532s, measured only for the 740 observed completions. The short duration mainly describes same-grid back-and-forth, not profitable 5¢ excursions.

## Late Entry Risk

| First crossing elapsed | Candidates | Completed | Completion rate | Superseded by better same-direction crossing | Censored at invalid book | Open at final event |
|---|---:|---:|---:|---:|---:|---:|
| 0-150s | 903 | 357 | 39.5% | 242 | 304 | 0 |
| 150-180s | 186 | 77 | 41.4% | 51 | 58 | 0 |
| 180-210s | 224 | 99 | 44.2% | 64 | 61 | 0 |
| 210-240s | 224 | 89 | 39.7% | 67 | 68 | 0 |
| 240-270s | 228 | 105 | 46.1% | 69 | 54 | 0 |
| 270-300s | 26 | 13 | 50.0% | 5 | 8 | 0 |

For candidates opened at or after T150, 383/888 (43.1%) visibly completed before market end; 249 were censored by an invalid YES book. The rate is a conservative observed-candidate fraction, not a true settlement completion probability, and includes zero-edge completions.

## Unpaired Risk

- Markets ending with a still-open crossing: 0/32; final direction/grid/time-open are blank in `market_summary.csv` for all 32.
- Across the path, 553 open candidates were instead censored when the YES book became invalid. This is material unobserved residual-exposure risk; zero final open candidates does **not** establish that a hypothetical first leg would be flat at settlement. No position or PnL is modeled.

## Representative Paths

### High grid activity: `btc-updown-5m-1790966700`

141 crossings (UP 67, DOWN 74), 39 alternating completions, 5 positive-edge completions. The sequence below is complete for this market; `mid` is YES mid **after** the crossing event and blank edge means no pair was completed at that crossing.

| Elapsed s | YES mid | Direction | Grid | Open crossing after event | Pair completed? | Pair edge |
|---:|---:|---|---:|---|---|---:|
| 18.640 | 0.5650 | DOWN | 0.65 | DOWN 0.65 | no | — |
| 18.640 | 0.5650 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 21.138 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 30.467 | 0.7000 | UP | 0.70 | UP 0.70 | no | — |
| 35.897 | 0.7000 | DOWN | 0.70 | DOWN 0.70 | no | — |
| 61.378 | 0.6500 | DOWN | 0.65 | DOWN 0.65 | no | — |
| 63.444 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 66.727 | 0.6550 | UP | 0.65 | UP 0.65 | no | — |
| 70.639 | 0.6500 | DOWN | 0.65 | DOWN 0.65 | no | — |
| 74.726 | 0.6000 | UP | 0.60 | UP 0.60 | no | — |
| 76.076 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | yes | 0.00 |
| 76.264 | 0.5500 | DOWN | 0.55 | DOWN 0.55 | no | — |
| 76.964 | 0.4950 | DOWN | 0.50 | DOWN 0.50 | no | — |
| 80.574 | 0.5500 | UP | 0.55 | UP 0.55 | no | — |
| 83.097 | 0.5500 | UP | 0.55 | UP 0.55 | no | — |
| 83.127 | 0.5450 | DOWN | 0.55 | DOWN 0.55 | yes | 0.00 |
| 83.169 | 0.5600 | UP | 0.55 | UP 0.55 | yes | 0.00 |
| 86.505 | 0.6000 | UP | 0.60 | UP 0.60 | no | — |
| 87.474 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 88.209 | 0.6500 | UP | 0.65 | UP 0.65 | no | — |
| 90.579 | 0.6450 | DOWN | 0.65 | DOWN 0.65 | yes | 0.00 |
| 92.481 | 0.6500 | UP | 0.65 | UP 0.65 | no | — |
| 95.424 | 0.7000 | UP | 0.70 | UP 0.70 | no | — |
| 96.562 | 0.7500 | UP | 0.75 | UP 0.75 | no | — |
| 102.463 | 0.7500 | DOWN | 0.75 | DOWN 0.75 | no | — |
| 107.149 | 0.7500 | UP | 0.75 | UP 0.75 | yes | 0.00 |
| 113.865 | 0.8000 | DOWN | 0.80 | DOWN 0.80 | no | — |
| 118.318 | 0.8000 | UP | 0.80 | UP 0.80 | yes | 0.00 |
| 121.426 | 0.7500 | DOWN | 0.75 | DOWN 0.75 | no | — |
| 124.386 | 0.7000 | UP | 0.70 | UP 0.70 | no | — |
| 125.257 | 0.7500 | DOWN | 0.75 | DOWN 0.75 | no | — |
| 128.672 | 0.8050 | UP | 0.80 | UP 0.80 | no | — |
| 138.892 | 0.8000 | DOWN | 0.80 | DOWN 0.80 | no | — |
| 139.307 | 0.7500 | DOWN | 0.75 | DOWN 0.75 | no | — |
| 139.675 | 0.7500 | DOWN | 0.75 | DOWN 0.75 | no | — |
| 140.079 | 0.6950 | DOWN | 0.70 | DOWN 0.70 | no | — |
| 140.385 | 0.6500 | DOWN | 0.65 | DOWN 0.65 | no | — |
| 140.834 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 140.980 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 142.075 | 0.5450 | DOWN | 0.55 | DOWN 0.55 | no | — |
| 142.795 | 0.4500 | UP | 0.45 | UP 0.45 | no | — |
| 144.001 | 0.4500 | UP | 0.45 | UP 0.45 | no | — |
| 145.310 | 0.4500 | DOWN | 0.45 | DOWN 0.45 | no | — |
| 145.363 | 0.4600 | UP | 0.45 | UP 0.45 | yes | 0.00 |
| 145.398 | 0.4400 | DOWN | 0.45 | DOWN 0.45 | yes | 0.00 |
| 145.849 | 0.3950 | DOWN | 0.40 | DOWN 0.40 | no | — |
| 145.857 | 0.4000 | UP | 0.40 | UP 0.40 | yes | 0.00 |
| 148.578 | 0.3450 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 148.609 | 0.3650 | UP | 0.35 | UP 0.35 | yes | 0.00 |
| 148.633 | 0.3450 | DOWN | 0.35 | DOWN 0.35 | yes | 0.00 |
| 148.706 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 149.529 | 0.3450 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 151.022 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 153.332 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | no | — |
| 154.332 | 0.2500 | UP | 0.25 | UP 0.25 | no | — |
| 154.575 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | yes | 0.00 |
| 154.590 | 0.2500 | UP | 0.25 | UP 0.25 | yes | 0.00 |
| 174.217 | 0.2500 | UP | 0.25 | UP 0.25 | no | — |
| 174.779 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | yes | 0.00 |
| 181.299 | 0.2600 | UP | 0.25 | UP 0.25 | no | — |
| 181.308 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | yes | 0.00 |
| 181.318 | 0.3050 | UP | 0.30 | UP 0.30 | yes | 0.05 |
| 183.450 | 0.3050 | UP | 0.30 | UP 0.30 | no | — |
| 183.502 | 0.2950 | DOWN | 0.30 | DOWN 0.30 | yes | 0.00 |
| 184.279 | 0.3500 | UP | 0.35 | UP 0.35 | no | — |
| 185.475 | 0.3500 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 188.656 | 0.4000 | UP | 0.40 | UP 0.40 | no | — |
| 190.178 | 0.4000 | DOWN | 0.40 | DOWN 0.40 | no | — |
| 192.782 | 0.3500 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 193.585 | 0.3550 | UP | 0.35 | UP 0.35 | no | — |
| 193.594 | 0.3500 | DOWN | 0.35 | DOWN 0.35 | yes | 0.00 |
| 194.207 | 0.4000 | UP | 0.40 | UP 0.40 | yes | 0.05 |
| 194.539 | 0.4500 | UP | 0.45 | UP 0.45 | no | — |
| 194.909 | 0.4000 | DOWN | 0.40 | DOWN 0.40 | yes | 0.05 |
| 195.616 | 0.4500 | UP | 0.45 | UP 0.45 | no | — |
| 195.932 | 0.5000 | DOWN | 0.50 | DOWN 0.50 | no | — |
| 196.165 | 0.5000 | DOWN | 0.50 | DOWN 0.50 | no | — |
| 196.496 | 0.4500 | DOWN | 0.45 | DOWN 0.45 | no | — |
| 196.602 | 0.4500 | DOWN | 0.45 | DOWN 0.45 | no | — |
| 199.422 | 0.4500 | UP | 0.45 | UP 0.45 | no | — |
| 201.207 | 0.4350 | DOWN | 0.45 | DOWN 0.45 | yes | 0.00 |
| 201.354 | 0.4000 | DOWN | 0.40 | DOWN 0.40 | no | — |
| 202.162 | 0.3500 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 204.878 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 204.971 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 205.590 | 0.3000 | UP | 0.30 | UP 0.30 | yes | 0.00 |
| 205.900 | 0.3000 | UP | 0.30 | UP 0.30 | no | — |
| 206.378 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | yes | 0.00 |
| 207.294 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | no | — |
| 209.307 | 0.2000 | DOWN | 0.20 | DOWN 0.20 | no | — |
| 210.091 | 0.2000 | UP | 0.20 | UP 0.20 | yes | 0.00 |
| 215.710 | 0.2000 | DOWN | 0.20 | DOWN 0.20 | no | — |
| 215.823 | 0.2000 | UP | 0.20 | UP 0.20 | yes | 0.00 |
| 216.417 | 0.2050 | UP | 0.20 | UP 0.20 | no | — |
| 217.020 | 0.2500 | UP | 0.25 | UP 0.25 | no | — |
| 217.039 | 0.3050 | UP | 0.30 | UP 0.30 | no | — |
| 217.171 | 0.3500 | UP | 0.35 | UP 0.35 | no | — |
| 218.071 | 0.4050 | UP | 0.40 | UP 0.40 | no | — |
| 218.482 | 0.3950 | DOWN | 0.40 | DOWN 0.40 | yes | 0.00 |
| 218.798 | 0.4200 | UP | 0.40 | UP 0.40 | yes | 0.00 |
| 218.873 | 0.4500 | UP | 0.45 | UP 0.45 | no | — |
| 220.064 | 0.5500 | UP | 0.55 | UP 0.55 | no | — |
| 220.180 | 0.6000 | UP | 0.60 | UP 0.60 | no | — |
| 221.439 | 0.5950 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 221.439 | 0.6000 | UP | 0.60 | UP 0.60 | yes | 0.00 |
| 221.775 | 0.5350 | DOWN | 0.55 | DOWN 0.55 | yes | 0.05 |
| 223.110 | 0.5000 | DOWN | 0.50 | DOWN 0.50 | no | — |
| 224.692 | 0.4500 | DOWN | 0.45 | DOWN 0.45 | no | — |
| 226.857 | 0.4500 | DOWN | 0.45 | DOWN 0.45 | no | — |
| 227.043 | 0.5650 | UP | 0.55 | UP 0.55 | no | — |
| 227.166 | 0.5450 | DOWN | 0.55 | DOWN 0.55 | yes | 0.00 |
| 227.167 | 0.5850 | UP | 0.55 | UP 0.55 | yes | 0.00 |
| 227.174 | 0.6000 | UP | 0.60 | UP 0.60 | no | — |
| 227.959 | 0.6000 | UP | 0.60 | UP 0.60 | no | — |
| 228.616 | 0.6550 | UP | 0.65 | UP 0.65 | no | — |
| 230.814 | 0.6500 | DOWN | 0.65 | DOWN 0.65 | no | — |
| 231.316 | 0.5500 | DOWN | 0.55 | DOWN 0.55 | no | — |
| 231.470 | 0.5000 | DOWN | 0.50 | DOWN 0.50 | no | — |
| 231.526 | 0.4950 | DOWN | 0.50 | DOWN 0.50 | no | — |
| 231.833 | 0.5000 | UP | 0.50 | UP 0.50 | yes | 0.00 |
| 232.470 | 0.5450 | DOWN | 0.55 | DOWN 0.55 | no | — |
| 232.519 | 0.5500 | UP | 0.55 | UP 0.55 | yes | 0.00 |
| 232.790 | 0.4800 | DOWN | 0.50 | DOWN 0.50 | yes | 0.05 |
| 233.100 | 0.5150 | UP | 0.50 | UP 0.50 | yes | 0.00 |
| 233.572 | 0.5350 | DOWN | 0.55 | DOWN 0.55 | no | — |
| 233.573 | 0.5600 | UP | 0.55 | UP 0.55 | yes | 0.00 |
| 234.772 | 0.5500 | UP | 0.55 | UP 0.55 | no | — |
| 240.518 | 0.5550 | UP | 0.55 | UP 0.55 | no | — |
| 240.541 | 0.6300 | UP | 0.60 | UP 0.60 | no | — |
| 240.556 | 0.6650 | UP | 0.65 | UP 0.65 | no | — |
| 240.560 | 0.7000 | UP | 0.70 | UP 0.70 | no | — |
| 240.584 | 0.7550 | UP | 0.75 | UP 0.75 | no | — |
| 240.613 | 0.8050 | UP | 0.80 | UP 0.80 | no | — |
| 240.903 | 0.7450 | DOWN | 0.75 | DOWN 0.75 | no | — |
| 241.340 | 0.6950 | DOWN | 0.70 | DOWN 0.70 | no | — |
| 242.590 | 0.7500 | UP | 0.75 | UP 0.75 | no | — |
| 242.598 | 0.8000 | UP | 0.80 | UP 0.80 | no | — |
| 243.497 | 0.9000 | UP | 0.90 | UP 0.90 | no | — |
| 243.696 | 0.8900 | DOWN | 0.90 | DOWN 0.90 | yes | 0.00 |
| 244.309 | 0.9000 | UP | 0.90 | UP 0.90 | yes | 0.00 |
| 244.320 | 0.9000 | DOWN | 0.90 | DOWN 0.90 | yes | 0.00 |

### Low grid activity: `btc-updown-5m-1791016500`

10 crossings (UP 8, DOWN 2), 2 alternating completions, 0 positive-edge completions. The sequence below is complete for this market; `mid` is YES mid **after** the crossing event and blank edge means no pair was completed at that crossing.

| Elapsed s | YES mid | Direction | Grid | Open crossing after event | Pair completed? | Pair edge |
|---:|---:|---|---:|---|---|---:|
| 0.419 | 0.6000 | DOWN | 0.60 | DOWN 0.60 | no | — |
| 0.523 | 0.6000 | UP | 0.60 | UP 0.60 | yes | 0.00 |
| 2.386 | 0.6500 | UP | 0.65 | UP 0.65 | no | — |
| 15.235 | 0.7500 | UP | 0.75 | UP 0.75 | no | — |
| 32.909 | 0.8000 | UP | 0.80 | UP 0.80 | no | — |
| 32.910 | 0.8000 | UP | 0.80 | UP 0.80 | no | — |
| 34.766 | 0.8000 | DOWN | 0.80 | DOWN 0.80 | yes | 0.00 |
| 94.890 | 0.8500 | UP | 0.85 | UP 0.85 | no | — |
| 95.726 | 0.9050 | UP | 0.90 | UP 0.90 | no | — |
| 118.115 | 0.9500 | UP | 0.95 | UP 0.95 | no | — |

### One-direction example: `btc-updown-5m-1790962500`

23 crossings (UP 7, DOWN 16), 3 alternating completions, 0 positive-edge completions. The sequence below is complete for this market; `mid` is YES mid **after** the crossing event and blank edge means no pair was completed at that crossing.

| Elapsed s | YES mid | Direction | Grid | Open crossing after event | Pair completed? | Pair edge |
|---:|---:|---|---:|---|---|---:|
| 1.591 | 0.4200 | DOWN | 0.45 | DOWN 0.45 | no | — |
| 1.594 | 0.4000 | DOWN | 0.40 | DOWN 0.40 | no | — |
| 2.084 | 0.3500 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 2.152 | 0.2900 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 4.198 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | no | — |
| 8.056 | 0.2550 | UP | 0.25 | UP 0.25 | yes | 0.00 |
| 10.860 | 0.3250 | UP | 0.30 | UP 0.30 | no | — |
| 26.710 | 0.3450 | DOWN | 0.35 | DOWN 0.35 | no | — |
| 37.006 | 0.3500 | UP | 0.35 | UP 0.35 | no | — |
| 62.884 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 67.814 | 0.3000 | UP | 0.30 | UP 0.30 | no | — |
| 67.818 | 0.3000 | UP | 0.30 | UP 0.30 | no | — |
| 68.325 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | yes | 0.00 |
| 72.691 | 0.3000 | UP | 0.30 | UP 0.30 | yes | 0.00 |
| 75.360 | 0.3000 | DOWN | 0.30 | DOWN 0.30 | no | — |
| 78.125 | 0.2500 | DOWN | 0.25 | DOWN 0.25 | no | — |
| 122.830 | 0.1500 | DOWN | 0.15 | DOWN 0.15 | no | — |
| 140.892 | 0.1500 | DOWN | 0.15 | DOWN 0.15 | no | — |
| 153.299 | 0.1500 | DOWN | 0.15 | DOWN 0.15 | no | — |
| 166.257 | 0.1500 | UP | 0.15 | UP 0.15 | no | — |
| 171.727 | 0.1000 | DOWN | 0.10 | DOWN 0.10 | no | — |
| 172.250 | 0.1000 | DOWN | 0.10 | DOWN 0.10 | no | — |
| 184.733 | 0.0500 | DOWN | 0.05 | DOWN 0.05 | no | — |

## Answers to G0 questions

Q1: 23.12 alternating pairs per market, including zero edge. Q2: mean edge 0.001824, median 0. Q3: positive opportunities are not confined to the first half (8 positive completions before T150, 19 after), but the late sample is selective. Q4: no observed active-time decline can be established; coverage collapses late. Q5: median opposite crossing time 0.709s, p90 5.772s among observed completions. Q6: 383/888 (43.1%) T150+ candidates visibly complete; many are censored and most completed pairs have zero edge. Q7: no stable material 5¢ grid edge is demonstrated by this path sample.

## Main Finding

The fixed 5¢ grid generates frequent bidirectional line crossings, but 96.4% of alternating pairs return at the same level and have zero complete-set edge. Only 27 pairs across 13 markets have positive grid edge, while the median market has zero total theoretical edge. The observed activity persists in later valid-book windows, yet late coverage is too selective to generalize. The path evidence therefore does not support moving this specific grid into maker execution testing as a demonstrated Gamma-harvesting edge.

## Main Limitation

This is an as-known mid-price geometry test. Crossed grid levels are theoretical prices, not executable YES or NO quotes; no spread, depth, latency, queue, fills, capital, inventory or settlement PnL is represented. Invalid YES-book intervals censor open candidates and sharply reduce late-window coverage, so the market-end residual and late-completion estimates are lower-bound observations rather than full-path probabilities. The execution-replayable selector is broader than strict M4A integrity acceptance. Because each completed crossing starts the next candidate, pair counts and summed edge are rolling path statistics rather than independent achievable transactions.

## Validation and provenance

`python3 research_tools/grid_gamma_opportunity_g0.py` completed for 32 unique markets with matching local settlement identities. Synthetic assertions verified single- and multi-level crossing direction/order, including exact-line bounds. Independent CSV checks matched crossing, pair and market totals to `summary.json`; every pair cost plus edge equals 1, every pair has opposite directions and nonnegative holding time, all events are within 0–300s, and the entry cohorts reconcile completions/supersessions/censoring/open states. `python3 -m py_compile` and `git diff --check` passed. No independent Reviewer PASS or Git checkpoint exists for G0.

## Next Action

Independently review G0 crossing, invalid-book censoring, and rolling-pair accounting against the 32 raw capture paths.
