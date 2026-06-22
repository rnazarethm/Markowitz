#!/usr/bin/env python3
"""
update.py — Atualização mensal automática da base de retornos e fronteira eficiente.
Executado via GitHub Actions no dia 2 de cada mês.

Fontes:
  VT (USD)  : Yahoo Finance  (ticker: VT)
  USD/BRL   : Yahoo Finance  (ticker: BRL=X)
  BOVA11    : Yahoo Finance  (ticker: BOVA11.SA)
  IMAB11    : Yahoo Finance  (ticker: IMAB11.SA)
  CDI       : API SGS/BCB    (série 11 — CDI over anualizado → acumulação mensal)
"""

import json
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, date
from calendar import monthrange

import pandas as pd
import numpy as np
import requests
from scipy.optimize import minimize

try:
    import yfinance as yf
except ImportError:
    print("Instale: pip install yfinance")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent
CSV_PATH   = ROOT / "data" / "returns.csv"
JSON_PATH  = ROOT / "data" / "frontier.json"
ASSETS     = ["VT_BRL", "BOVA11", "IMAB11", "CDI"]

# ── 1. Fetch Yahoo Finance monthly close ───────────────────────────────────────

def fetch_yf_monthly(ticker: str, start: str, end: str) -> pd.Series:
    """Return monthly Adj Close prices indexed by period (YYYY-MM)."""
    log.info(f"  Fetching {ticker} from {start} to {end}")
    df = yf.download(ticker, start=start, end=end, interval="1mo",
                     auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    # Use month-period index
    s = df["Close"].squeeze()
    s.index = s.index.to_period("M")
    return s


def monthly_return(prices: pd.Series) -> pd.Series:
    return prices.pct_change().dropna()


# ── 2. Fetch CDI from SGS/BCB ──────────────────────────────────────────────────

def fetch_cdi_monthly(start: str, end: str) -> pd.Series:
    """
    BCB SGS série 11 = CDI over diário (% a.d.).
    Acumula dia a dia e converte para retorno mensal.
    """
    log.info(f"  Fetching CDI from SGS/BCB ({start} → {end})")
    url = (
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados"
        f"?formato=json&dataInicial={_fmt_bcb(start)}&dataFinal={_fmt_bcb(end)}"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    raw = r.json()

    daily = pd.DataFrame(raw)
    daily["data"]  = pd.to_datetime(daily["data"], dayfirst=True)
    daily["valor"] = daily["valor"].astype(float) / 100  # % → decimal

    # Accumulate within each month
    daily["period"] = daily["data"].dt.to_period("M")
    monthly = daily.groupby("period")["valor"].apply(
        lambda x: (1 + x).prod() - 1
    )
    return monthly


def _fmt_bcb(iso_date: str) -> str:
    """Convert YYYY-MM-DD to DD/MM/YYYY for BCB API."""
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return d.strftime("%d/%m/%Y")


# ── 3. Load existing CSV ───────────────────────────────────────────────────────

def load_existing() -> pd.DataFrame:
    if not CSV_PATH.exists():
        log.warning(f"  {CSV_PATH} not found — starting fresh")
        return pd.DataFrame(columns=["Data"] + ASSETS)
    df = pd.read_csv(CSV_PATH)
    # Ensure period index for merging
    df["_period"] = pd.PeriodIndex(df["Data"], freq="M")
    return df


# ── 4. Determine months to fetch ───────────────────────────────────────────────

def last_period_in_csv(df: pd.DataFrame) -> pd.Period:
    if df.empty or "_period" not in df.columns:
        return pd.Period("2010-07", freq="M")  # one month before our start
    return df["_period"].max()


# ── 5. Build new rows ──────────────────────────────────────────────────────────

def fetch_new_rows(from_period: pd.Period) -> pd.DataFrame:
    """
    Fetch data from the month after `from_period` up to the last complete month.
    """
    today = date.today()
    last_complete = pd.Period(today, freq="M") - 1  # previous month

    if from_period >= last_complete:
        log.info("  CSV already up-to-date.")
        return pd.DataFrame()

    # Fetch window: from_period+1 through last_complete
    start_period = from_period + 1
    start_str = str(start_period.to_timestamp())[:10]           # YYYY-MM-DD
    # End = first day of current month (yfinance end is exclusive)
    end_str = str(last_complete.to_timestamp(how="end"))[:10]
    # Extend end by one day so yfinance includes the last month close
    end_ext = str((last_complete + 1).to_timestamp())[:10]

    log.info(f"  Fetching window: {start_str} → {end_ext}")

    try:
        vt   = fetch_yf_monthly("VT",       start_str, end_ext)
        usd  = fetch_yf_monthly("BRL=X",    start_str, end_ext)
        bova = fetch_yf_monthly("BOVA11.SA", start_str, end_ext)
        imab = fetch_yf_monthly("IMAB11.SA", start_str, end_ext)
        cdi  = fetch_cdi_monthly(start_str, end_ext)
    except Exception as e:
        log.error(f"  Data fetch failed: {e}")
        raise

    # Monthly returns
    vt_ret   = monthly_return(vt)
    usd_ret  = monthly_return(usd)
    bova_ret = monthly_return(bova)
    imab_ret = monthly_return(imab)

    # VT_BRL = (1 + VT_USD) * (1 + USD/BRL) - 1
    vt_brl = (1 + vt_ret) * (1 + usd_ret) - 1

    # Align on common index
    idx = vt_brl.index.intersection(bova_ret.index)\
                       .intersection(imab_ret.index)\
                       .intersection(cdi.index)
    idx = idx[idx >= start_period]
    idx = idx[idx <= last_complete]

    if idx.empty:
        log.info("  No new complete months found.")
        return pd.DataFrame()

    new_df = pd.DataFrame({
        "Data":   [p.strftime("%b/%y").replace("Jan","Jan").replace("Feb","Feb")
                   .replace("Mar","Mar") for p in idx],
        "VT_BRL": vt_brl[idx].values,
        "BOVA11": bova_ret[idx].values,
        "IMAB11": imab_ret[idx].values,
        "CDI":    cdi[idx].values,
        "_period": idx,
    })
    return new_df


# ── 6. Markowitz optimization ──────────────────────────────────────────────────

def run_markowitz(df: pd.DataFrame) -> dict:
    returns = df[ASSETS].values
    n = len(ASSETS)

    mu_m = returns.mean(axis=0)
    mu_a = (1 + mu_m) ** 12 - 1
    cov_m = np.cov(returns.T)
    cov_a = cov_m * 12
    corr = np.corrcoef(returns.T)
    vol_a = np.sqrt(np.diag(cov_a))

    rf = mu_a[3]  # CDI as risk-free

    bounds = [(0, 1)] * n
    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    w0 = np.ones(n) / n

    def port_stats(w):
        ret = (1 + np.dot(w, mu_m)) ** 12 - 1
        vol = np.sqrt(max(w @ cov_a @ w, 0))
        sh  = (ret - rf) / vol if vol > 0 else 0
        return ret, vol, sh

    # Max Sharpe
    def neg_sharpe(w):
        r, v, s = port_stats(w)
        return -s

    res = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds,
                   constraints=constraints, options={"ftol": 1e-12, "maxiter": 1000})
    w_ms = res.x
    ret_ms, vol_ms, sh_ms = port_stats(w_ms)

    # Efficient frontier (60 points)
    target_returns = np.linspace(mu_a.min(), mu_a.max(), 60)
    frontier = []
    for target in target_returns:
        cons = [
            {"type": "eq", "fun": lambda w: w.sum() - 1},
            {"type": "eq", "fun": lambda w, t=target: (1 + np.dot(w, mu_m)) ** 12 - 1 - t},
        ]
        res2 = minimize(lambda w: w @ cov_a @ w, w0, method="SLSQP",
                        bounds=bounds, constraints=cons,
                        options={"ftol": 1e-12, "maxiter": 1000})
        if res2.success:
            ww = res2.x
            rr = (1 + np.dot(ww, mu_m)) ** 12 - 1
            vv = np.sqrt(max(ww @ cov_a @ ww, 0))
            frontier.append({
                "ret": round(float(rr), 6),
                "vol": round(float(vv), 6),
                "weights": {a: round(float(ww[i]), 6) for i, a in enumerate(ASSETS)},
            })

    # Individual assets
    assets_ind = []
    for i, a in enumerate(ASSETS):
        ww = np.zeros(n); ww[i] = 1.0
        rr, vv, ss = port_stats(ww)
        assets_ind.append({"name": a, "ret": round(float(rr),6),
                            "vol": round(float(vv),6), "sharpe": round(float(ss),6)})

    # Date range label
    first = df["Data"].iloc[0] if "Data" in df else "?"
    last  = df["Data"].iloc[-1] if "Data" in df else "?"

    return {
        "meta": {
            "period": f"{first} – {last}",
            "n_months": int(len(df)),
            "assets": ASSETS,
            "rf_annual": round(float(rf), 6),
            "updated_at": datetime.utcnow().isoformat()[:16] + "Z",
        },
        "stats": {
            a: {
                "mu_monthly": round(float(mu_m[i]), 8),
                "mu_annual":  round(float(mu_a[i]), 6),
                "vol_annual": round(float(vol_a[i]), 6),
            }
            for i, a in enumerate(ASSETS)
        },
        "cov_annual": {
            a: {b: round(float(cov_a[i, j]), 10) for j, b in enumerate(ASSETS)}
            for i, a in enumerate(ASSETS)
        },
        "corr": {
            a: {b: round(float(corr[i, j]), 6) for j, b in enumerate(ASSETS)}
            for i, a in enumerate(ASSETS)
        },
        "max_sharpe": {
            "weights": {a: round(float(w_ms[i]), 6) for i, a in enumerate(ASSETS)},
            "ret":     round(float(ret_ms), 6),
            "vol":     round(float(vol_ms), 6),
            "sharpe":  round(float(sh_ms),  6),
        },
        "frontier": frontier,
        "assets_individual": assets_ind,
    }


# ── 7. Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Markowitz updater starting ===")
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load existing
    df = load_existing()
    last_p = last_period_in_csv(df)
    log.info(f"  Last period in CSV: {last_p}")

    # Fetch new rows
    new_rows = fetch_new_rows(last_p)

    if not new_rows.empty:
        log.info(f"  Adding {len(new_rows)} new month(s): {list(new_rows['Data'])}")
        if "_period" in df.columns:
            df = df.drop(columns=["_period"])
        new_rows = new_rows.drop(columns=["_period"])
        df = pd.concat([df, new_rows], ignore_index=True)
        df.to_csv(CSV_PATH, index=False, float_format="%.8f")
        log.info(f"  Saved returns.csv ({len(df)} rows)")
    else:
        log.info("  No new data — skipping CSV update")
        if "_period" in df.columns:
            df = df.drop(columns=["_period"])

    # Re-run optimization
    log.info("  Running Markowitz optimization...")
    result = run_markowitz(df)
    JSON_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    log.info(f"  Saved frontier.json  (frontier: {len(result['frontier'])} pts)")
    log.info(f"  Max Sharpe → ret={result['max_sharpe']['ret']:.4%}  "
             f"vol={result['max_sharpe']['vol']:.4%}  "
             f"sharpe={result['max_sharpe']['sharpe']:.4f}")
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
