"""
PRIME v1.0 Active Trades Portfolio Factor Module (UOA-ENH-002).

Portfolio-level factor awareness: sector concentration, correlation risk,
and position sizing relative to portfolio. Advisory only -- does not execute.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_SECTOR_CONCENTRATION = 0.40

SYMBOL_SECTOR_MAP = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "GOOGL": "Technology", "META": "Technology", "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary", "AVGO": "Technology", "ADBE": "Technology",
    "CRM": "Technology", "ORCL": "Technology", "CSCO": "Technology",
    "INTC": "Technology", "QCOM": "Technology", "TXN": "Technology",
    "IBM": "Technology", "INTU": "Technology", "NOW": "Technology",
    "AMAT": "Technology", "LRCX": "Technology", "MU": "Technology",
    "KLAC": "Technology", "SNPS": "Technology", "CDNS": "Technology",
    "ADSK": "Technology", "MRVL": "Technology", "MCHP": "Technology",
    "ON": "Technology", "PANW": "Technology", "CRWD": "Technology",
    "FTNT": "Technology", "WDAY": "Technology", "TEAM": "Technology",
    "DDOG": "Technology", "NXPI": "Technology",
    "UNH": "Health Care", "JNJ": "Health Care", "MRK": "Health Care",
    "ABBV": "Health Care", "TMO": "Health Care", "ABT": "Health Care",
    "DHR": "Health Care", "LLY": "Health Care", "AMGN": "Health Care",
    "GILD": "Health Care", "VRTX": "Health Care", "REGN": "Health Care",
    "ISRG": "Health Care", "SYK": "Health Care", "DXCM": "Health Care",
    "IDXX": "Health Care",
    "JPM": "Financials", "V": "Financials", "MA": "Financials",
    "AXP": "Financials", "PYPL": "Financials", "COIN": "Financials",
    "BRK.B": "Financials",
    "XOM": "Energy", "CVX": "Energy", "BKR": "Energy",
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "MCD": "Consumer Staples",
    "SBUX": "Consumer Staples", "MDLZ": "Consumer Staples", "MNST": "Consumer Staples",
    "KDP": "Consumer Staples",
    "HD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "BKNG": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary", "MAR": "Consumer Discretionary",
    "ORLY": "Consumer Discretionary", "ROST": "Consumer Discretionary",
    "TJX": "Consumer Discretionary", "DASH": "Consumer Discretionary",
    "CPRT": "Consumer Discretionary",
    "CAT": "Industrials", "HON": "Industrials", "UPS": "Industrials",
    "RTX": "Industrials", "ADP": "Industrials", "CTAS": "Industrials",
    "PAYX": "Industrials", "ODFL": "Industrials", "FAST": "Industrials",
    "GEHC": "Industrials",
    "NEE": "Utilities", "EXC": "Utilities",
    "VZ": "Communication Services", "CHTR": "Communication Services",
    "CSGP": "Communication Services",
    "PM": "Consumer Staples", "BMY": "Health Care",
    "NIO": "Consumer Discretionary",
    "GLD": "Commodities", "SLV": "Commodities", "GDX": "Commodities", "GDXJ": "Commodities",
    "USO": "Energy", "XLE": "Energy",
    "XLF": "Financials",
    "XLK": "Technology",
    "XLV": "Healthcare",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "SPY": "Broad Market", "QQQ": "Technology", "IWM": "Broad Market",
    "DIA": "Broad Market",
    "MELI": "Consumer Discretionary",
}


def sector_map(symbol: str) -> str:
    """Return sector string for symbol. Uses SRS scanner sector data."""
    return SYMBOL_SECTOR_MAP.get(symbol.upper(), "Unknown")


def evaluate_portfolio_risk(
    open_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate portfolio-level risk factors.

    Returns sector concentration score, max single-position weight,
    and correlation flags.
    """
    if not open_positions:
        return {
            "sector_concentration": {},
            "max_sector_weight": 0.0,
            "max_sector": "N/A",
            "concentration_breach": False,
            "max_position_weight": 0.0,
            "max_position_symbol": "N/A",
            "correlation_flags": [],
            "position_count": 0,
            "total_market_value": 0.0,
        }

    total_value = 0.0
    sector_values: Dict[str, float] = {}
    position_values: Dict[str, float] = {}

    for pos in open_positions:
        symbol = pos.get("symbol", "???")
        shares = pos.get("shares", 0)
        price = pos.get("current_price") or pos.get("entry_price") or pos.get("price_at_scan", 0)
        mv = shares * price
        total_value += mv
        position_values[symbol] = mv

        sector = sector_map(symbol)
        sector_values[sector] = sector_values.get(sector, 0.0) + mv

    if total_value <= 0:
        total_value = 1.0

    sector_weights = {s: v / total_value for s, v in sector_values.items()}
    max_sector = max(sector_weights, key=sector_weights.get)
    max_sector_weight = sector_weights[max_sector]

    position_weights = {s: v / total_value for s, v in position_values.items()}
    max_pos_symbol = max(position_weights, key=position_weights.get)
    max_pos_weight = position_weights[max_pos_symbol]

    correlation_flags = []
    if max_sector_weight > MAX_SECTOR_CONCENTRATION:
        correlation_flags.append(
            f"Sector concentration BREACH: {max_sector} at "
            f"{max_sector_weight:.0%} exceeds {MAX_SECTOR_CONCENTRATION:.0%} limit"
        )

    tech_like = {"Technology", "Communication Services"}
    tech_weight = sum(sector_weights.get(s, 0) for s in tech_like)
    if tech_weight > 0.50:
        correlation_flags.append(
            f"Tech+Comm correlation risk: combined {tech_weight:.0%} of portfolio"
        )

    return {
        "sector_concentration": {s: round(w, 4) for s, w in sector_weights.items()},
        "max_sector_weight": round(max_sector_weight, 4),
        "max_sector": max_sector,
        "concentration_breach": max_sector_weight > MAX_SECTOR_CONCENTRATION,
        "max_position_weight": round(max_pos_weight, 4),
        "max_position_symbol": max_pos_symbol,
        "correlation_flags": correlation_flags,
        "position_count": len(open_positions),
        "total_market_value": round(total_value, 2),
    }


def get_rebalance_suggestions(
    open_positions: List[Dict[str, Any]],
    approved_signals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Given open positions and new signals, return sizing suggestions
    that respect concentration limits. Advisory only."""
    risk = evaluate_portfolio_risk(open_positions)
    total_value = risk["total_market_value"]
    sector_conc = risk["sector_concentration"]

    suggestions = []

    for signal in approved_signals:
        symbol = signal.get("symbol", "???")
        sig_sector = sector_map(symbol)
        current_sector_weight = sector_conc.get(sig_sector, 0.0)
        headroom = MAX_SECTOR_CONCENTRATION - current_sector_weight

        if headroom <= 0:
            suggestions.append({
                "symbol": symbol,
                "sector": sig_sector,
                "action": "SKIP",
                "reason": f"{sig_sector} already at {current_sector_weight:.0%} -- no headroom",
                "suggested_pct": 0.0,
            })
            continue

        max_alloc_pct = min(headroom, 0.05)
        max_alloc_value = total_value * max_alloc_pct if total_value > 0 else 0

        price = signal.get("price_at_scan", 0)
        if price > 0:
            suggested_shares = int(max_alloc_value / price)
        else:
            suggested_shares = 0

        suggestions.append({
            "symbol": symbol,
            "sector": sig_sector,
            "action": "SIZE",
            "reason": f"{sig_sector} headroom {headroom:.0%}; max alloc {max_alloc_pct:.0%}",
            "suggested_pct": round(max_alloc_pct, 4),
            "suggested_shares": suggested_shares,
            "suggested_value": round(max_alloc_value, 2),
        })

    return suggestions
