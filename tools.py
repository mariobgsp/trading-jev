#!/usr/bin/env python3
"""The one place that knows how to reach trading-tools.

trading-tools is a plain directory of stdlib scripts, not an installed package, so it is loaded
by explicit path. Every module here that needs its indicators gets them from this file — do not
copy the loading code, one definition of the dependency or none.

Set TRADING_TOOLS_DIR if trading-tools does not live beside this repo.
"""
import importlib.util
import os

import market

TOOLS = os.environ.get("TRADING_TOOLS_DIR") or os.path.expanduser("~/Projects/trading-tools")
_SCREENER = os.path.join(TOOLS, "ihsg-screener", "ihsg_screener.py")

_spec = importlib.util.spec_from_file_location("ihsg_screener", _SCREENER)
if _spec is None or _spec.loader is None:
    raise SystemExit(f"cannot load {_SCREENER} — set TRADING_TOOLS_DIR")
screener = importlib.util.module_from_spec(_spec)
# exec puts trading-tools/ on sys.path, which is what ihsg_screener's own `common.net` import needs
_spec.loader.exec_module(screener)


def bars_for(code, rng="6mo"):
    """Daily bars for a bare IDX code or an already-suffixed Yahoo symbol, via trading-tools'
    cached, politeness-paced Yahoo fetcher.

    This is the one place a price series enters the program, so it is also the one place it
    gets normalised: market.normalise_series tags the bars the vendor adjusted without telling
    us, keeps the raw close where it survives, and never rescales anything - see the KNOWN
    LIMITATION note in market.py for why a rights-issue adjustment cannot be repaired here.
    """
    symbol = code if code.startswith("^") else f"{code}.JK"
    return market.normalise_series(screener.fetch(symbol, rng))
