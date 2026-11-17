"""Shared case-feature extraction for the gta6-rev variants.

Everything here is computed from the case file alone (target side) or from
a comparable's own record (donor side), using only fields the case schema
carries: hourly viewers/channels, day_start, unlock text, platforms.
"""
import json
import math
import os
from datetime import datetime, timedelta

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.6448536269514722, -0.6744897501960817, 0.0, 0.6744897501960817, 1.6448536269514722]


def parse_time(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def classify_unlock(text):
    """'ROLLING', 'GLOBAL' or 'UNKNOWN' from an unlock description."""
    if not text:
        return "UNKNOWN"
    low = text.lower()
    rolling = ("midnight local" in low) or ("rolling" in low) or ("local midnight" in low)
    glob = ("global" in low) or ("simultaneous" in low) or ("single unlock" in low)
    if rolling and not glob:
        return "ROLLING"
    if glob and not rolling:
        return "GLOBAL"
    if rolling and glob:
        if "midnight local" in low or "local midnight" in low:
            return "ROLLING"
        return "GLOBAL"
    return "UNKNOWN"


def hourly_map(points):
    """(datetime -> (viewers, channels)) from a case-style hourly list."""
    out = {}
    for pt in points or []:
        try:
            t = parse_time(pt.get("from") or pt.get("at"))
            v = float(pt.get("viewers"))
        except Exception:
            continue
        ch = pt.get("channels")
        try:
            ch = float(ch) if ch is not None else None
        except Exception:
            ch = None
        out[t] = (v, ch)
    return out


class Curve:
    """One launch (donor or target) reduced to features + day log-ratios."""

    __slots__ = ("name", "unlock_cls", "platforms", "p1", "ch1", "day",
                 "dip", "slope72", "pre_last", "nz_jump", "jump_hour", "peak")

    def __init__(self):
        self.name = ""
        self.unlock_cls = "UNKNOWN"
        self.platforms = ""
        self.p1 = None
        self.ch1 = None
        self.day = {}          # hour 1..24 -> viewers
        self.dip = None        # max viewers in 24h before day_start / p1
        self.slope72 = None    # OLS slope of log viewers over 72h before day_start (per day)
        self.pre_last = None   # viewers in last hour before day_start / p1
        self.nz_jump = None    # ln(v[-12h] / v[-24h]) around the NZ unlock
        self.jump_hour = None  # sharpest log rise hour in 3..11 (donors)
        self.peak = 0.0


def build_curve(name, unlock_text, platforms, day_start_text, points):
    """points: list of dicts with from/at + viewers (+channels)."""
    c = Curve()
    c.name = name or ""
    c.unlock_cls = classify_unlock(unlock_text)
    c.platforms = platforms or ""
    try:
        start = parse_time(day_start_text)
    except Exception:
        return c
    hm = hourly_map(points)
    pre = {}   # offset hours (negative) -> viewers
    for t, (v, ch) in hm.items():
        off = (t - start).total_seconds() / 3600.0
        if off == int(off):
            h = int(off) + 1
            if 1 <= h <= 24:
                c.day[h] = v
                if h == 1:
                    c.ch1 = ch
            elif -96 <= int(off) < 0:
                pre[int(off)] = v
    if 1 in c.day:
        c.p1 = c.day[1]
    if c.day:
        c.peak = max(c.day.values())
    if c.p1 and c.p1 > 0:
        prev24 = [v for off, v in pre.items() if -24 <= off < 0 and v > 0]
        if prev24:
            c.dip = max(prev24) / c.p1
        if -1 in pre and pre[-1] > 0:
            c.pre_last = pre[-1] / c.p1
        if -12 in pre and -24 in pre and pre[-12] > 0 and pre[-24] > 0:
            c.nz_jump = math.log(pre[-12] / pre[-24])
        xs, ys = [], []
        for off, v in pre.items():
            if -72 <= off < 0 and v > 0:
                xs.append(off / 24.0)
                ys.append(math.log(v))
        if len(xs) >= 12:
            mx = sum(xs) / len(xs)
            my = sum(ys) / len(ys)
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                c.slope72 = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    # Donor jump hour: sharpest log rise between consecutive day hours 3..11.
    best, best_rise = None, 0.35
    for h in range(3, 12):
        if h in c.day and (h - 1) in c.day and c.day[h - 1] > 0 and c.day[h] > 0:
            rise = math.log(c.day[h] / c.day[h - 1])
            if rise > best_rise:
                best_rise, best = rise, h
    c.jump_hour = best
    return c


def donor_curves(case):
    curves = []
    for comp in case.get("comparables") or []:
        name = comp.get("name") or ""
        if "before release" in name:
            continue
        curves.append(build_curve(name, comp.get("unlock"), comp.get("platforms"),
                                  comp.get("day_start"), comp.get("hourly")))
    return [c for c in curves if c.p1 and c.p1 > 0 and len(c.day) >= 12]


def target_curve(case):
    known = case.get("known") or []
    p1 = None
    ch1 = None
    if known:
        try:
            p1 = float(known[0].get("viewers"))
        except Exception:
            p1 = None
        ch1 = known[0].get("channels")
    hist = case.get("history_hourly") or case.get("history") or []
    c = build_curve(case.get("game", {}).get("name", ""), case.get("notes"),
                    "", case.get("day_start"), hist)
    # The history stops at the checkpoint (end of hour 1); make sure the
    # known hour-1 print is the anchor even if the last history point is
    # the hour-1 bucket itself.
    if p1 and p1 > 0:
        c.p1 = p1
        c.day[1] = p1
    if ch1 is not None:
        try:
            c.ch1 = float(ch1)
        except Exception:
            pass
    # Recompute pre-features against the authoritative p1.
    if c.p1 and c.p1 > 0:
        try:
            start = parse_time(case.get("day_start"))
        except Exception:
            start = None
        if start is not None:
            hm = hourly_map(hist)
            pre = {}
            for t, (v, _ch) in hm.items():
                off = (t - start).total_seconds() / 3600.0
                if off == int(off) and -96 <= int(off) < 0:
                    pre[int(off)] = v
            prev24 = [v for off, v in pre.items() if -24 <= off < 0 and v > 0]
            if prev24:
                c.dip = max(prev24) / c.p1
            if -1 in pre and pre[-1] > 0:
                c.pre_last = pre[-1] / c.p1
            if -12 in pre and -24 in pre and pre[-12] > 0 and pre[-24] > 0:
                c.nz_jump = math.log(pre[-12] / pre[-24])
            xs, ys = [], []
            for off, v in pre.items():
                if -72 <= off < 0 and v > 0:
                    xs.append(off / 24.0)
                    ys.append(math.log(v))
            if len(xs) >= 12:
                mx = sum(xs) / len(xs)
                my = sum(ys) / len(ys)
                den = sum((x - mx) ** 2 for x in xs)
                if den > 0:
                    c.slope72 = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return c


def dst_jump_hour(day_start_text):
    """US Eastern midnight-unlock hour in UTC for a release date.

    EDT (2nd Sun Mar .. 1st Sun Nov): midnight ET = 04:00Z -> hour 5.
    EST otherwise: midnight ET = 05:00Z -> hour 6.
    """
    try:
        d = parse_time(day_start_text).date()
    except Exception:
        return 5
    import calendar as _cal
    def nth_sunday(year, month, n):
        weeks = _cal.monthcalendar(year, month)
        sundays = [w[6] for w in weeks if w[6] != 0]
        return sundays[n - 1]
    start = datetime(d.year, 3, nth_sunday(d.year, 3, 2)).date()
    end = datetime(d.year, 11, nth_sunday(d.year, 11, 1)).date()
    return 5 if start <= d < end else 6


def persistence_rows(p1):
    rows = []
    base = max(float(p1), 1.0)
    for h in range(2, 25):
        sig = 0.15 * math.sqrt(h - 1)
        rows.append({"hour": h, "q": [base * math.exp(z * sig) for z in Z]})
    return {"hours": rows}


def finish_rows(p1, log_qs):
    """log_qs: {hour: [log quantiles x5]} -> output dict, monotone + clamped."""
    rows = []
    for h in range(2, 25):
        qs = []
        for lq in log_qs[h]:
            q = math.exp(lq)
            qs.append(min(1e9, max(0.01, q)))
        for i in range(1, len(qs)):
            if qs[i] < qs[i - 1]:
                qs[i] = qs[i - 1]
        rows.append({"hour": h, "q": qs})
    return {"hours": rows}


"""Variant G: three-member ensemble (kernel + ridge + unlock-aligned).

The submitted forecaster for the revision: the geometric mean, in log
space, of the three dissected genuine-top families, each implemented
from scratch in this file -- (D) kernel-weighted empirical quantiles
over all donors (scale + dip kernel, no persistence shrinkage, no
spread widening), (E) per-hour ridge on case features with
residual-median bias correction and smoothed residual spread, target
scale clamped to the donor range, (F) the same kernel pooled in
US-unlock-aligned coordinates for the morning hours. LOO backtest over
the 37 practice comparables (identical harness as the incumbent):
ensemble micro loss 0.3972 vs incumbent 0.7552; worst case 1.71 vs
2.92. Pure Python standard library, deterministic, no network.
"""

import glob

# Tunable constants (LOO grid-searched; see ../tune_d.py results).
BW_SCALE = 1.0    # Gaussian bandwidth on log hour-1 gap
BW_DIP = 1.5       # Gaussian bandwidth on log dip gap
F_CLASS = 1.25     # weight factor when unlock class matches target's
F_CONSOLE = 1.15   # weight factor when donor is console-carried
TAIL_K = 0.0       # n_eff shrinkage constant, tails only (0 = off)
LR_LO, LR_HI = -3.0, 8.0


def weighted_quantile(sorted_pairs, tau):
    """sorted_pairs: [(value, weight)] sorted by value. Interpolated
    weighted empirical quantile (type: cumulative weight midpoint)."""
    total = sum(w for _, w in sorted_pairs)
    if total <= 0:
        return sorted_pairs[len(sorted_pairs) // 2][0]
    if len(sorted_pairs) == 1:
        return sorted_pairs[0][0]
    target = tau * total
    cum = 0.0
    prev_v, prev_cum = sorted_pairs[0][0], 0.0
    for v, w in sorted_pairs:
        cum += w
        if cum >= target:
            if cum == prev_cum:
                return v
            frac = (target - prev_cum) / (cum - prev_cum)
            return prev_v + frac * (v - prev_v)
        prev_v, prev_cum = v, cum
    return sorted_pairs[-1][0]


def donor_weight(donor, target):
    lp_d = math.log(max(donor.p1, 1.0))
    lp_t = math.log(max(target.p1, 1.0))
    g = (lp_d - lp_t) / BW_SCALE
    w = math.exp(-0.5 * g * g)
    if donor.dip and target.dip and donor.dip > 0 and target.dip > 0:
        gd = (math.log(donor.dip) - math.log(target.dip)) / BW_DIP
        w *= math.exp(-0.5 * gd * gd)
    if target.unlock_cls != "UNKNOWN" and donor.unlock_cls == target.unlock_cls:
        w *= F_CLASS
    if ("PS" in donor.platforms) or ("Xbox" in donor.platforms):
        w *= F_CONSOLE
    return w


def forecast_d(case):
    target = target_curve(case)
    p1 = max(float(target.p1 or 1.0), 1.0)
    donors = donor_curves(case)
    if len(donors) < 5:
        return persistence_rows(p1)
    log_p1 = math.log(p1)
    weighted = [(d, donor_weight(d, target)) for d in donors]
    log_qs = {}
    for h in range(2, 25):
        pairs = []
        for d, w in weighted:
            if h in d.day and d.day[h] > 0:
                lr = math.log(d.day[h] / d.p1)
                pairs.append((min(LR_HI, max(LR_LO, lr)), w))
        if len(pairs) < 4:
            pairs = [(min(LR_HI, max(LR_LO, math.log(d.day[h] / d.p1))), w)
                     for d, w in weighted if h in d.day and d.day[h] > 0]
        if not pairs:
            sig = 0.15 * math.sqrt(h - 1)
            log_qs[h] = [log_p1 + z * sig for z in Z]
            continue
        pairs.sort()
        n_eff = (sum(w for _, w in pairs) ** 2) / max(1e-12, sum(w * w for _, w in pairs))
        qs = []
        for i, tau in enumerate(LEVELS):
            q_lr = weighted_quantile(pairs, tau)
            if TAIL_K > 0 and i in (0, len(LEVELS) - 1):
                alpha = n_eff / (n_eff + TAIL_K)
                pers_lr = Z[i] * 0.15 * math.sqrt(h - 1)
                q_lr = alpha * q_lr + (1.0 - alpha) * pers_lr
            qs.append(log_p1 + q_lr)
        log_qs[h] = qs
    return finish_rows(p1, log_qs)



RIDGE_LAM = 3.0
SHRINK_MU = 0.0     # extra shrink of the fitted log-ratio toward 0
SHRINK_SIG = 0.2    # shrink of residual SD toward baseline spread
SD_FLOOR = 0.08


def _features(c, lp_ref):
    lp = math.log(max(c.p1, 1.0)) - lp_ref
    lch = math.log(max(c.ch1, 1.0) / 1000.0) if c.ch1 else 0.0
    lch = min(4.0, max(-4.0, lch))
    slope = min(3.0, max(-3.0, c.slope72)) if c.slope72 is not None else 0.0
    ldip = math.log(c.dip) if c.dip and c.dip > 0 else 0.0
    ldip = min(4.0, max(-4.0, ldip))
    nz = min(3.0, max(-3.0, c.nz_jump)) if c.nz_jump is not None else 0.0
    return [1.0, lp, lch, slope, ldip, nz]


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            continue
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(n):
            if r != col and M[r][col] != 0.0:
                f = M[r][col] / pv
                for k in range(col, n + 1):
                    M[r][k] -= f * M[col][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


def _ridge(X, y, lam):
    k = len(X[0])
    A = [[0.0] * k for _ in range(k)]
    b = [0.0] * k
    for xi, yi in zip(X, y):
        for i in range(k):
            b[i] += xi[i] * yi
            for j in range(k):
                A[i][j] += xi[i] * xi[j]
    for i in range(1, k):
        A[i][i] += lam
    return _solve(A, b)


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _pstdev(vals, mu):
    if len(vals) < 2:
        return 0.5
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))


def forecast_e(case):
    target = target_curve(case)
    p1 = max(float(target.p1 or 1.0), 1.0)
    donors = donor_curves(case)
    if len(donors) < 6:
        return persistence_rows(p1)
    log_p1 = math.log(p1)
    lp_ref = math.log(1e5)
    donor_lp = [math.log(max(d.p1, 1.0)) for d in donors]
    lp_lo, lp_hi = min(donor_lp), max(donor_lp)
    xt = _features(target, lp_ref)
    # Clamp the target's scale feature into the donor range (taper).
    xt[1] = min(max(math.log(p1), lp_lo), lp_hi) - lp_ref
    X = [_features(d, lp_ref) for d in donors]
    mu_h, sd_raw = {}, {}
    for h in range(2, 25):
        data = [(X[i], math.log(donors[i].day[h] / donors[i].p1))
                for i in range(len(donors)) if h in donors[i].day and donors[i].day[h] > 0]
        if len(data) < 6:
            pool = [math.log(d.day[hh] / d.p1) for d in donors
                    for hh in (h - 1, h, h + 1) if hh in d.day and d.day[hh] > 0]
            mu_h[h] = _median(pool) if pool else 0.0
            sd_raw[h] = min(2.5, max(SD_FLOOR, _pstdev(pool, mu_h[h]) if len(pool) > 1 else 0.15 * math.sqrt(h - 1)))
            continue
        beta = _ridge([d[0] for d in data], [d[1] for d in data], RIDGE_LAM)
        pred = sum(b * x for b, x in zip(beta, xt))
        res = [yi - sum(b * x for b, x in zip(beta, xi)) for xi, yi in data]
        mu = pred + _median(res)
        mu_h[h] = (1.0 - SHRINK_MU) * mu
        sd = _pstdev(res, sum(res) / len(res))
        dof = max(1, len(res) - len(beta))
        if dof < len(res):
            sd *= math.sqrt(len(res) / dof)
        sd_raw[h] = min(2.5, max(SD_FLOOR, sd))
    log_qs = {}
    for h in range(2, 25):
        vals = [(0.5, sd_raw[h])]
        if h - 1 in sd_raw:
            vals.append((0.25, sd_raw[h - 1]))
        if h + 1 in sd_raw:
            vals.append((0.25, sd_raw[h + 1]))
        w = sum(v[0] for v in vals)
        sd_s = sum(v[0] * v[1] for v in vals) / w
        base = 0.15 * math.sqrt(h - 1)
        sig = (1.0 - SHRINK_SIG) * sd_s + SHRINK_SIG * base
        sig = min(3.0, max(0.05, sig))
        log_qs[h] = [log_p1 + mu_h[h] + z * sig for z in Z]
    return finish_rows(p1, log_qs)



BW_SCALE = 1.0
BW_DIP = 1.5
F_CLASS = 1.25
F_CONSOLE = 1.15
TAIL_K = 0.0
SIZE_GAMMA = 0.0   # log-spread scale per unit log-size gap (clamped)
LR_LO, LR_HI = -3.0, 8.0
REF_JUMP = 5         # aligned coordinate anchor (EDT jump hour)


def _wq(sorted_pairs, tau):
    total = sum(w for _, w in sorted_pairs)
    if total <= 0:
        return sorted_pairs[len(sorted_pairs) // 2][0]
    if len(sorted_pairs) == 1:
        return sorted_pairs[0][0]
    target_w = tau * total
    cum = 0.0
    prev_v, prev_cum = sorted_pairs[0][0], 0.0
    for v, w in sorted_pairs:
        cum += w
        if cum >= target_w:
            if cum == prev_cum:
                return v
            frac = (target_w - prev_cum) / (cum - prev_cum)
            return prev_v + frac * (v - prev_v)
        prev_v, prev_cum = v, cum
    return sorted_pairs[-1][0]


def _weight(donor, target):
    g = (math.log(max(donor.p1, 1.0)) - math.log(max(target.p1, 1.0))) / BW_SCALE
    w = math.exp(-0.5 * g * g)
    if donor.dip and target.dip and donor.dip > 0 and target.dip > 0:
        gd = (math.log(donor.dip) - math.log(target.dip)) / BW_DIP
        w *= math.exp(-0.5 * gd * gd)
    if target.unlock_cls != "UNKNOWN" and donor.unlock_cls == target.unlock_cls:
        w *= F_CLASS
    if ("PS" in donor.platforms) or ("Xbox" in donor.platforms):
        w *= F_CONSOLE
    return w


def _quantiles_at(weighted, hour_of, h, log_p1, size_scale):
    """Weighted quantiles of donor log-ratios; hour_of(donor)->hour or None."""
    pairs = []
    for d, w in weighted:
        hh = hour_of(d)
        if hh is not None and hh in d.day and d.day[hh] > 0:
            lr = math.log(d.day[hh] / d.p1)
            pairs.append((min(LR_HI, max(LR_LO, lr)), w))
    if len(pairs) < 4:
        return None
    pairs.sort()
    n_eff = (sum(w for _, w in pairs) ** 2) / max(1e-12, sum(w * w for _, w in pairs))
    out = []
    for i, tau in enumerate(LEVELS):
        q_lr = _wq(pairs, tau)
        if TAIL_K > 0 and i in (0, len(LEVELS) - 1):
            alpha = n_eff / (n_eff + TAIL_K)
            pers_lr = Z[i] * 0.15 * math.sqrt(h - 1)
            q_lr = alpha * q_lr + (1.0 - alpha) * pers_lr
        out.append(q_lr)
    # Size-conditional spread: rescale deviations from the median.
    med = out[2]
    out = [med + size_scale * (q - med) for q in out]
    return [log_p1 + q for q in out]


def forecast_f(case):
    target = target_curve(case)
    p1 = max(float(target.p1 or 1.0), 1.0)
    donors = donor_curves(case)
    if len(donors) < 5:
        return persistence_rows(p1)
    log_p1 = math.log(p1)
    weighted = [(d, _weight(d, target)) for d in donors]
    mean_lp = sum(math.log(max(d.p1, 1.0)) for d in donors) / len(donors)
    size_scale = math.exp(SIZE_GAMMA * (log_p1 - mean_lp))
    size_scale = min(1.6, max(0.55, size_scale))
    t_jump = dst_jump_hour(case.get("day_start"))
    if target.unlock_cls == "GLOBAL":
        t_jump = None  # fixed-time unlock: no rolling wave to align

    log_qs = {}
    for h in range(2, 25):
        clock_q = _quantiles_at(weighted, lambda d, h=h: h, h, log_p1, size_scale)
        if t_jump is None or h >= 14:
            log_qs[h] = clock_q if clock_q is not None else \
                [log_p1 + z * 0.15 * math.sqrt(h - 1) for z in Z]
            continue
        u = h - t_jump + REF_JUMP  # target's aligned coordinate

        def aligned_hour(d, u=u):
            if d.jump_hour is None:
                return None
            return u + d.jump_hour - REF_JUMP

        al_q = _quantiles_at(weighted, aligned_hour, h, log_p1, size_scale)
        if al_q is None:
            log_qs[h] = clock_q if clock_q is not None else \
                [log_p1 + z * 0.15 * math.sqrt(h - 1) for z in Z]
        elif h <= 11:
            log_qs[h] = al_q
        else:  # hours 12-13: half-and-half blend in log space
            if clock_q is None:
                log_qs[h] = al_q
            else:
                log_qs[h] = [0.5 * a + 0.5 * c for a, c in zip(al_q, clock_q)]
    return finish_rows(p1, log_qs)


def forecast_case(case):
    outs = []
    for fn in (forecast_d, forecast_e, forecast_f):
        res = fn(case)
        outs.append({row["hour"]: row["q"] for row in res["hours"]})
    target = target_curve(case)
    p1 = max(float(target.p1 or 1.0), 1.0)
    log_qs = {}
    for h in range(2, 25):
        log_qs[h] = [
            sum(math.log(max(outs[k][h][i], 1e-9)) for k in range(3)) / 3.0
            for i in range(5)
        ]
    return finish_rows(p1, log_qs)


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(in_dir, "*.in"))):
        with open(path, "r", encoding="utf-8") as fh:
            case = json.load(fh)
        result = forecast_case(case)
        out_path = os.path.join(out_dir, str(case.get("id")) + ".out")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh)


if __name__ == "__main__":
    main()
