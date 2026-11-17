# Top-8 analogue matching + horizon persistence blend, LOO-calibrated
[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/binasalama12/makefun-gta6-twitch-ccu-forecast-01-w3/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/binasalama12/makefun-gta6-twitch-ccu-forecast-01-w3/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

This entry scores every past launch comparable for similarity to the target —
unlock type (rolling midnight-local vs single global unlock), console
platforms, release-day scale relative to the target's first hour, and the
pre-release viewership ramp — and keeps the eight best matches. Each forecast
hour's quantiles come from the empirical distribution of those analogues'
log viewership ratios to their own first hour, blended in log space with the
house persistence forecast using horizon-dependent weights, with a single
spread multiplier around the blended median. The blend weights and spread
were chosen by a leave-one-out backtest over the 37 launch comparables in
the practice data (grid: weights in {0, 0.15, 0.3} per horizon bucket,
spread in {0.9, 1.0, 1.1, 1.25}) and are hardcoded as constants in main.py.
The program is pure Python standard library, deterministic, and needs no
network.
