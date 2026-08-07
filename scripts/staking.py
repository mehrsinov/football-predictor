"""Fractional Kelly staking — the bankroll-management layer.

The football system already produces calibrated probabilities and market odds,
but until now it only *said* "stake 1-2%" without computing anything. This
module turns each pick's edge into a concrete, variance-controlled stake.

Kelly criterion: with true win probability p and decimal odds o, the fraction
of bankroll that maximises long-run log-growth is

    f* = (p*o - 1) / (o - 1).

Full Kelly is far too aggressive in practice (probabilities are estimates, not
truth), so we use FRACTIONAL Kelly (default quarter) and cap the stake. A pick
with no positive expected value gets stake 0 — Kelly never bets a negative edge.
"""

# conservative defaults: quarter-Kelly, capped at 3% of bankroll per pick
DEFAULT_FRACTION = 0.25
DEFAULT_MAX_STAKE = 0.03
DEFAULT_MIN_EV = 0.0


def kelly_fraction(p, odds, fraction=DEFAULT_FRACTION,
                   max_stake=DEFAULT_MAX_STAKE, min_ev=DEFAULT_MIN_EV):
    """Return the recommended stake as a FRACTION of bankroll (0..max_stake).

    p      : our best estimate of the pick hitting (blended probability).
    odds   : decimal odds we can actually get. None/<=1 -> no bet.
    fraction : Kelly multiplier (0.25 = quarter Kelly). Lower = safer.
    max_stake: hard cap on stake fraction regardless of edge.
    min_ev : minimum expected value (p*odds-1) to bet at all.
    """
    if p is None or odds is None:
        return 0.0
    try:
        p = float(p)
        odds = float(odds)
    except (TypeError, ValueError):
        return 0.0
    if p <= 0.0 or p >= 1.0 or odds <= 1.0:
        return 0.0
    b = odds - 1.0
    if b <= 0.0:
        return 0.0
    ev = p * odds - 1.0
    if ev <= min_ev:
        return 0.0
    f_full = (p * odds - 1.0) / b       # full-Kelly fraction
    f = fraction * f_full
    if f <= 0.0:
        return 0.0
    return round(min(f, max_stake), 4)


def stake_units(p, odds, bankroll=100.0, **kw):
    """Stake in currency units for a given bankroll (default 100 units)."""
    f = kelly_fraction(p, odds, **kw)
    return round(f * bankroll, 2)


def parlay_kelly(leg_ps, parlay_odds, **kw):
    """Kelly stake for a multi-leg combo.

    Assumes legs are independent: combined p = product of leg probabilities.
    (Real parlays are usually positively correlated, so this is slightly
    optimistic; the fractional/cap guards keep it safe regardless.)
    """
    if not leg_ps or parlay_odds is None:
        return 0.0
    p = 1.0
    for lp in leg_ps:
        if lp is None or lp <= 0:
            return 0.0
        p *= float(lp)
    return kelly_fraction(p, parlay_odds, **kw)
