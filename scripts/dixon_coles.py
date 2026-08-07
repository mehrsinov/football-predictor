"""Time-weighted Dixon-Coles (1997) bivariate Poisson model.

Fits per-league attack/defence ratings by weighted MLE with exponential time
decay, then produces a full scoreline probability matrix per fixture, from
which all betting markets are derived (1X2, O/U, BTTS, double chance, top
scorelines, team totals).
"""
import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

MAX_GOALS = 10


def time_weights(dates, ref_date, half_life_days=180.0):
    days_ago = (ref_date - dates).dt.days.values.astype(float)
    days_ago = np.clip(days_ago, 0, None)
    xi = np.log(2.0) / half_life_days
    return np.exp(-xi * days_ago)


class DixonColes:
    def __init__(self, half_life_days=180.0, max_years=6.0, ridge=3.0, min_team_weight=8.0,
                 xg_weight=0.10):
        self.half_life_days = half_life_days
        self.max_years = max_years
        self.ridge = ridge                      # L2 shrinkage of ratings toward 0
        self.min_team_weight = min_team_weight  # below this effective sample -> prior
        self.xg_weight = xg_weight              # blend share for xG ratings (0 = off)
        self.teams = []
        self.attack = {}
        self.defence = {}
        self.team_weight = {}
        self.home_adv = 0.25
        self.rho = -0.05
        self.avg_goals = 1.3
        self.fitted = False

    # ------------------------------------------------------------------ fit
    def fit(self, df, ref_date, xg_ratings=None):
        """df: columns date, home, away, hg, ag (single league).

        xg_ratings: optional {team: {"xatk","xdef"}} to blend in after fitting.
        """
        cutoff = ref_date - np.timedelta64(int(self.max_years * 365.25), "D")
        d = df[(df["date"] >= cutoff) & (df["date"] < ref_date)].copy()
        if len(d) < 60:
            return False
        w = time_weights(d["date"], ref_date, self.half_life_days)
        keep = w > 0.01
        d, w = d[keep], w[keep]
        if len(d) < 60:
            return False

        self.teams = sorted(set(d["home"]) | set(d["away"]))
        n = len(self.teams)
        idx = {t: i for i, t in enumerate(self.teams)}
        hi = d["home"].map(idx).values
        ai = d["away"].map(idx).values
        hg = d["hg"].values.astype(int)
        ag = d["ag"].values.astype(int)
        self.avg_goals = float((w @ ((hg + ag) / 2.0)) / w.sum())

        def nll(params):
            atk = params[:n] - params[:n].mean()          # identifiability
            dfn = params[n:2 * n]
            ha, rho = params[-2], params[-1]
            lam = np.exp(atk[hi] + dfn[ai] + ha) * self.avg_goals
            mu = np.exp(atk[ai] + dfn[hi]) * self.avg_goals
            lam = np.clip(lam, 1e-6, 12)
            mu = np.clip(mu, 1e-6, 12)
            tau = np.ones_like(lam)
            m = (hg == 0) & (ag == 0); tau[m] = 1 - lam[m] * mu[m] * rho
            m = (hg == 0) & (ag == 1); tau[m] = 1 + lam[m] * rho
            m = (hg == 1) & (ag == 0); tau[m] = 1 + mu[m] * rho
            m = (hg == 1) & (ag == 1); tau[m] = 1 - rho
            tau = np.clip(tau, 1e-10, None)
            ll = w * (np.log(tau) + poisson.logpmf(hg, lam) + poisson.logpmf(ag, mu))
            penalty = self.ridge * (np.sum(atk ** 2) + np.sum(dfn ** 2))
            return (-ll.sum() + penalty) / w.sum()

        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25, -0.05]])
        bounds = [(-1.8, 1.8)] * (2 * n) + [(-0.3, 0.8), (-0.35, 0.35)]
        res = minimize(nll, x0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 250, "ftol": 1e-8})
        p = res.x
        atk = p[:n] - p[:n].mean()
        dfn = p[n:2 * n]
        self.attack = {t: float(atk[idx[t]]) for t in self.teams}
        self.defence = {t: float(dfn[idx[t]]) for t in self.teams}
        tw = np.zeros(n)
        np.add.at(tw, hi, w)
        np.add.at(tw, ai, w)
        self.team_weight = {t: float(tw[idx[t]]) for t in self.teams}
        self.home_adv = float(p[-2])
        self.rho = float(p[-1])
        # optional: blend in xG-based ratings (nudge toward underlying performance)
        if xg_ratings and self.xg_weight > 0:
            self._blend_xg(xg_ratings, self.xg_weight)
        self.fitted = True
        return True

    def _blend_xg(self, xg_ratings, weight=0.20):
        """Shrink goal-based ratings toward xG-based ones for covered teams.

        xg_ratings: {team: {"xatk","xdef"}} for THIS league (as_of the fit date).
        Only teams present in both are adjusted; weight is the xG share.
        """
        self.xg_covered = []
        for t in self.teams:
            xr = xg_ratings.get(t)
            if not xr:
                continue
            self.attack[t] = (1 - weight) * self.attack[t] + weight * xr["xatk"]
            self.defence[t] = (1 - weight) * self.defence[t] + weight * xr["xdef"]
            self.xg_covered.append(t)
        # re-centre attack to keep identifiability (mean 0)
        if self.attack:
            m = float(np.mean(list(self.attack.values())))
            for t in self.attack:
                self.attack[t] -= m

    # -------------------------------------------------------------- predict
    def _rating(self, team):
        """Known team ratings, or a conservative below-average prior.

        Teams with too little (time-weighted) history are treated as unknown:
        their MLE ratings are noise, so they get the prior instead.
        """
        if team in self.attack and self.team_weight.get(team, 0) >= self.min_team_weight:
            return self.attack[team], self.defence[team], True
        atk_prior = float(np.percentile(list(self.attack.values()), 30))
        dfn_prior = float(np.percentile(list(self.defence.values()), 70))
        return atk_prior, dfn_prior, False

    def score_matrix(self, home, away, injuries=None):
        ah, dh, known_h = self._rating(home)
        aa, da, known_a = self._rating(away)
        lam = np.exp(ah + da + self.home_adv) * self.avg_goals
        mu = np.exp(aa + dh) * self.avg_goals
        # injury adjustment: each absentee weakens the team's own attack by ~4%
        # and lets the opponent's attack up by ~2% (weakened defence). Capped
        # at 6 absentees (~ -24% own attack / +12% opp) so a bad injury list
        # can never dominate the rating signal.
        ih = int(min((injuries or {}).get("home", 0) or 0, 6))
        ia = int(min((injuries or {}).get("away", 0) or 0, 6))
        lam *= (1 - 0.04 * ih) * (1 + 0.02 * ia)
        mu *= (1 - 0.04 * ia) * (1 + 0.02 * ih)
        lam = np.clip(lam, 0.05, 8)
        mu = np.clip(mu, 0.05, 8)
        g = np.arange(MAX_GOALS + 1)
        ph = poisson.pmf(g, lam)
        pa = poisson.pmf(g, mu)
        m = np.outer(ph, pa)
        rho = self.rho
        m[0, 0] *= 1 - lam * mu * rho
        m[0, 1] *= 1 + lam * rho
        m[1, 0] *= 1 + mu * rho
        m[1, 1] *= 1 - rho
        m = np.clip(m, 0, None)
        m /= m.sum()
        return m, lam, mu, (known_h and known_a)

    def predict(self, home, away, injuries=None):
        m, lam, mu, known = self.score_matrix(home, away, injuries=injuries)
        i, j = np.indices(m.shape)
        tot = i + j
        p_home = float(m[i > j].sum())
        p_draw = float(np.trace(m))
        p_away = float(m[i < j].sum())
        out = {
            "home": home, "away": away,
            "xg_home": round(float(lam), 2), "xg_away": round(float(mu), 2),
            "known_teams": known,
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
            "p_1x": p_home + p_draw, "p_x2": p_draw + p_away, "p_12": p_home + p_away,
            "p_over15": float(m[tot >= 2].sum()),
            "p_over25": float(m[tot >= 3].sum()),
            "p_over35": float(m[tot >= 4].sum()),
            "p_under25": float(m[tot <= 2].sum()),
            "p_btts_yes": float(m[(i > 0) & (j > 0)].sum()),
            "p_btts_no": float(m[(i == 0) | (j == 0)].sum()),
            "p_home_minus1": float(m[i - 1 > j].sum()),   # home -1 (win by 2+)
            "p_away_plus1": float(m[j + 1 >= i].sum()),    # away +1 covers draw-or-better+1
        }
        flat = [((int(a), int(b)), float(m[a, b])) for a in range(6) for b in range(6)]
        flat.sort(key=lambda kv: kv[1], reverse=True)
        out["top_scores"] = [{"score": f"{a}-{b}", "p": round(p, 3)} for (a, b), p in flat[:3]]
        if injuries:
            out["injuries_applied"] = {k: int(v) for k, v in injuries.items() if v}
        return out

    # ------------------------------------------------------- all markets
    def all_markets(self, home, away, min_prob=0.02, injuries=None):
        """Full betting menu derived from the scoreline matrix.

        Returns a list of {market, pick, label_fa, p, fair_odds} for dozens of
        markets. fair_odds = 1/p is the break-even price; any real bookmaker
        price above it is +EV. Callers can filter by fair_odds >= min_odds.
        """
        m, lam, mu, known = self.score_matrix(home, away, injuries=injuries)
        i, j = np.indices(m.shape)
        tot = i + j
        opts = []

        def add(market, pick, label, p):
            p = float(p)
            if p < min_prob or p > 0.999:
                return
            opts.append({"market": market, "pick": pick, "label_fa": label,
                         "p": round(p, 4), "fair_odds": round(1.0 / p, 2)})

        # 1X2
        ph, pd, pa = float(m[i > j].sum()), float(np.trace(m)), float(m[i < j].sum())
        add("1X2", "1", "برد میزبان", ph)
        add("1X2", "X", "مساوی", pd)
        add("1X2", "2", "برد مهمان", pa)
        # double chance
        add("DC", "1X", "میزبان یا مساوی (۱X)", ph + pd)
        add("DC", "12", "میزبان یا مهمان (۱۲)", ph + pa)
        add("DC", "X2", "مساوی یا مهمان (X۲)", pd + pa)
        # draw no bet
        if ph + pa > 0:
            add("DNB", "1", "برد میزبان بدون تساوی", ph / (ph + pa))
            add("DNB", "2", "برد مهمان بدون تساوی", pa / (ph + pa))
        # totals over/under 0.5 .. 5.5
        for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
            over = float(m[tot > line].sum())
            add(f"OU{str(line).replace('.','')}", "Over", f"بالای {line} گل", over)
            add(f"OU{str(line).replace('.','')}", "Under", f"زیر {line} گل", 1 - over)
        # BTTS
        btts = float(m[(i > 0) & (j > 0)].sum())
        add("BTTS", "Yes", "هر دو تیم گل می‌زنند", btts)
        add("BTTS", "No", "هر دو گل نمی‌زنند", 1 - btts)
        # odd/even total
        odd = float(m[(tot % 2) == 1].sum())
        add("OE", "Odd", "مجموع گل فرد", odd)
        add("OE", "Even", "مجموع گل زوج", 1 - odd)
        # clean sheet / win to nil
        add("CS", "home_yes", "کلین‌شیت میزبان (مهمان گل نمی‌زند)", float(m[j == 0].sum()))
        add("CS", "away_yes", "کلین‌شیت مهمان (میزبان گل نمی‌زند)", float(m[i == 0].sum()))
        add("WTN", "1", "برد میزبان بدون دریافت گل", float(m[(i > j) & (j == 0)].sum()))
        add("WTN", "2", "برد مهمان بدون دریافت گل", float(m[(j > i) & (i == 0)].sum()))
        # result + BTTS combos (popular)
        add("1X2+BTTS", "1+Yes", "برد میزبان و هر دو گل", float(m[(i > j) & (j > 0)].sum()))
        add("1X2+BTTS", "2+Yes", "برد مهمان و هر دو گل", float(m[(j > i) & (i > 0)].sum()))
        # result + over 2.5
        add("1X2+OU25", "1+Over", "برد میزبان و بالای ۲.۵", float(m[(i > j) & (tot > 2.5)].sum()))
        add("1X2+OU25", "2+Over", "برد مهمان و بالای ۲.۵", float(m[(j > i) & (tot > 2.5)].sum()))
        add("1X2+OU25", "1+Under", "برد میزبان و زیر ۲.۵", float(m[(i > j) & (tot < 2.5)].sum()))
        # exact total goals bands
        for k, lab in [(0, "دقیقاً ۰ گل"), (1, "دقیقاً ۱ گل"), (2, "دقیقاً ۲ گل"), (3, "دقیقاً ۳ گل")]:
            add("TotalExact", str(k), lab, float(m[tot == k].sum()))
        add("TotalBand", "0-1", "۰ یا ۱ گل کل", float(m[tot <= 1].sum()))
        add("TotalBand", "2-3", "۲ یا ۳ گل کل", float(m[(tot >= 2) & (tot <= 3)].sum()))
        add("TotalBand", "4+", "۴ گل یا بیشتر", float(m[tot >= 4].sum()))
        # team totals over 0.5/1.5/2.5
        for line in (0.5, 1.5, 2.5):
            add(f"HomeTT{str(line).replace('.','')}", "Over", f"میزبان بالای {line} گل", float(m[i > line].sum()))
            add(f"AwayTT{str(line).replace('.','')}", "Over", f"مهمان بالای {line} گل", float(m[j > line].sum()))
        # asian-ish handicap -1 / +1 (whole-goal, push ignored -> approximate)
        add("AH", "home-1", "هندیکپ میزبان ۱- (برد با ۲+)", float(m[i - j >= 2].sum()))
        add("AH", "away+1", "هندیکپ مهمان ۱+ (نمی‌بازد با ۱ اختلاف)", float(m[j - i >= 0].sum()) + float(m[i - j == 1].sum()))
        # top exact scorelines
        flat = sorted([((a, b), float(m[a, b])) for a in range(6) for b in range(6)],
                      key=lambda kv: -kv[1])
        for (a, b), p in flat[:5]:
            add("CorrectScore", f"{a}-{b}", f"نتیجه دقیق {a}-{b}", p)

        opts.sort(key=lambda o: -o["p"])
        return {"home": home, "away": away, "xg_home": round(float(lam), 2),
                "xg_away": round(float(mu), 2), "known_teams": known, "options": opts}


def devig_1x2(oh, od, oa):
    """Multiplicative de-vig of decimal odds -> fair probabilities."""
    if not oh or not od or not oa or min(oh, od, oa) <= 1.0:
        return None
    inv = np.array([1 / oh, 1 / od, 1 / oa])
    return inv / inv.sum()


def devig_power(oh, od, oa):
    """Power-method de-vig (Jullien, 2017) -> fair probabilities.

    Solves for the exponent k such that sum((1/odds_i)^k) = 1. Unlike plain
    multiplicative de-vig, this does NOT assume the bookmaker's margin is
    spread proportionally across outcomes; empirically it removes the longshot
    bias (over-rating of low-probability outcomes) much better, which matters
    for EV and Kelly stakes on underdogs/draws. Falls back to multiplicative
    de-vig if the solver fails.
    """
    if not oh or not od or not oa or min(oh, od, oa) <= 1.0:
        return None
    inv = np.array([1.0 / oh, 1.0 / od, 1.0 / oa])
    lo, hi = 0.0, 10.0
    # f(k) = sum(inv^k) - 1 is monotonically decreasing in k for inv < 1
    try:
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if float(np.sum(inv ** mid)) > 1.0:
                lo = mid
            else:
                hi = mid
        fair = inv ** ((lo + hi) / 2.0)
        s = float(fair.sum())
        if s <= 0 or not np.isfinite(s):
            return inv / inv.sum()
        return fair / s
    except Exception:
        return inv / inv.sum()
