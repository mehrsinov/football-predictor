"""Unified loader for football-data.co.uk historical data and fixtures.

Extra-league files (new/{CODE}.csv): Country,League,Season,Date,Time,Home,Away,HG,AG,Res,
    PH,PD,PA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA
Main-league files (mmz4281/{season}/{div}.csv): Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,
    ...,B365H,B365D,B365A,...,MaxH,...,AvgH,...,Avg>2.5,Avg<2.5,...
"""
import glob
import io
import os

import numpy as np
import pandas as pd

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")

DIV_NAMES = {
    "E0": "England Premier League", "E1": "England Championship",
    "E2": "England League One", "E3": "England League Two",
    "SC0": "Scotland Premiership", "SC1": "Scotland Championship",
    "D1": "Germany Bundesliga", "D2": "Germany 2. Bundesliga",
    "I1": "Italy Serie A", "I2": "Italy Serie B",
    "SP1": "Spain La Liga", "SP2": "Spain Segunda",
    "F1": "France Ligue 1", "F2": "France Ligue 2",
    "N1": "Netherlands Eredivisie", "B1": "Belgium Pro League",
    "P1": "Portugal Liga", "T1": "Turkey Super Lig", "G1": "Greece Super League",
}


def _parse_dates(s):
    return pd.to_datetime(s, format="mixed", dayfirst=True, errors="coerce")


def _read_csv_lenient(path):
    """Read a football-data.co.uk CSV tolerating trailing-comma padding.

    The site's fixtures files pad data rows with extra empty fields beyond the
    header (e.g. 98 commas vs 54 columns). pd.read_csv(on_bad_lines="skip")
    silently DROPS every such row -> zero fixtures. Strip trailing commas from
    each line first; rows still longer than the header are skipped as before.
    """
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            lines = [ln.rstrip(",") for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            return pd.DataFrame()
        return pd.read_csv(io.StringIO("\n".join(lines)), on_bad_lines="skip")
    except Exception:
        return pd.DataFrame()


def load_extra_history():
    frames = []
    for path in glob.glob(os.path.join(RAW_DIR, "extra_*.csv")):
        df = _read_csv_lenient(path)
        if df.empty or "Home" not in df.columns:
            continue
        out = pd.DataFrame({
            "league": df["Country"].astype(str).str.strip() + " " + df["League"].astype(str).str.strip(),
            "date": _parse_dates(df["Date"]),
            "home": df["Home"].astype(str).str.strip(),
            "away": df["Away"].astype(str).str.strip(),
            "hg": pd.to_numeric(df["HG"], errors="coerce"),
            "ag": pd.to_numeric(df["AG"], errors="coerce"),
            "odds_h": pd.to_numeric(df.get("AvgH"), errors="coerce"),
            "odds_d": pd.to_numeric(df.get("AvgD"), errors="coerce"),
            "odds_a": pd.to_numeric(df.get("AvgA"), errors="coerce"),
            "odds_over25": np.nan,
            "odds_under25": np.nan,
        })
        frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_main_history():
    frames = []
    for path in glob.glob(os.path.join(RAW_DIR, "main_*.csv")):
        df = _read_csv_lenient(path)
        if df.empty or "HomeTeam" not in df.columns:
            continue
        div = str(df["Div"].iloc[0]).strip()
        out = pd.DataFrame({
            "league": DIV_NAMES.get(div, div),
            "date": _parse_dates(df["Date"]),
            "home": df["HomeTeam"].astype(str).str.strip(),
            "away": df["AwayTeam"].astype(str).str.strip(),
            "hg": pd.to_numeric(df["FTHG"], errors="coerce"),
            "ag": pd.to_numeric(df["FTAG"], errors="coerce"),
            "odds_h": pd.to_numeric(df.get("AvgH", df.get("B365H")), errors="coerce"),
            "odds_d": pd.to_numeric(df.get("AvgD", df.get("B365D")), errors="coerce"),
            "odds_a": pd.to_numeric(df.get("AvgA", df.get("B365A")), errors="coerce"),
            "odds_over25": pd.to_numeric(df.get("Avg>2.5", df.get("B365>2.5")), errors="coerce"),
            "odds_under25": pd.to_numeric(df.get("Avg<2.5", df.get("B365<2.5")), errors="coerce"),
        })
        frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_history():
    """All completed matches, deduplicated, sorted by date."""
    df = pd.concat([load_extra_history(), load_main_history()], ignore_index=True)
    df = df.dropna(subset=["date", "hg", "ag"])
    df = df[(df["hg"] >= 0) & (df["ag"] >= 0)]
    df["hg"] = df["hg"].astype(int)
    df["ag"] = df["ag"].astype(int)
    df = df.drop_duplicates(subset=["league", "date", "home", "away"])
    return df.sort_values("date").reset_index(drop=True)


def load_fixtures():
    """Upcoming fixtures with market odds (both files), normalized schema."""
    frames = []
    p_extra = os.path.join(RAW_DIR, "fixtures_extra.csv")
    if os.path.exists(p_extra):
        df = _read_csv_lenient(p_extra)
        if not df.empty and "Home" in df.columns:
            frames.append(pd.DataFrame({
                "league": df["Country"].astype(str).str.strip() + " " + df["League"].astype(str).str.strip(),
                "date": _parse_dates(df["Date"]),
                "time": df.get("Time"),
                "home": df["Home"].astype(str).str.strip(),
                "away": df["Away"].astype(str).str.strip(),
                "odds_h": pd.to_numeric(df.get("AvgH"), errors="coerce"),
                "odds_d": pd.to_numeric(df.get("AvgD"), errors="coerce"),
                "odds_a": pd.to_numeric(df.get("AvgA"), errors="coerce"),
                "max_h": pd.to_numeric(df.get("MaxH"), errors="coerce"),
                "max_d": pd.to_numeric(df.get("MaxD"), errors="coerce"),
                "max_a": pd.to_numeric(df.get("MaxA"), errors="coerce"),
                "odds_over25": np.nan,
                "odds_under25": np.nan,
            }))
    p_main = os.path.join(RAW_DIR, "fixtures_main.csv")
    if os.path.exists(p_main):
        df = _read_csv_lenient(p_main)
        if not df.empty and "HomeTeam" in df.columns:
            frames.append(pd.DataFrame({
                "league": df["Div"].astype(str).str.strip().map(lambda d: DIV_NAMES.get(d, d)),
                "date": _parse_dates(df["Date"]),
                "time": df.get("Time"),
                "home": df["HomeTeam"].astype(str).str.strip(),
                "away": df["AwayTeam"].astype(str).str.strip(),
                "odds_h": pd.to_numeric(df.get("AvgH", df.get("B365H")), errors="coerce"),
                "odds_d": pd.to_numeric(df.get("AvgD", df.get("B365D")), errors="coerce"),
                "odds_a": pd.to_numeric(df.get("AvgA", df.get("B365A")), errors="coerce"),
                "max_h": pd.to_numeric(df.get("MaxH"), errors="coerce"),
                "max_d": pd.to_numeric(df.get("MaxD"), errors="coerce"),
                "max_a": pd.to_numeric(df.get("MaxA"), errors="coerce"),
                "odds_over25": pd.to_numeric(df.get("Avg>2.5", df.get("B365>2.5")), errors="coerce"),
                "odds_under25": pd.to_numeric(df.get("Avg<2.5", df.get("B365<2.5")), errors="coerce"),
            }))
    if not frames:
        return pd.DataFrame()
    fx = pd.concat(frames, ignore_index=True).dropna(subset=["date"])
    return fx.sort_values(["date", "time"]).reset_index(drop=True)


if __name__ == "__main__":
    h = load_history()
    print("history rows:", len(h), "| leagues:", h["league"].nunique())
    print(h.groupby("league").size().sort_values(ascending=False).head(10))
    print("date range:", h["date"].min(), "->", h["date"].max())
    fx = load_fixtures()
    print("\nfixtures rows:", len(fx))
    print(fx.groupby("league").size())
