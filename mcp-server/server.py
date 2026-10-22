"""Deliberately heavy MCP server for the agentgateway tool-mode demo (synthetic data, invented instruments).

Two kinds of weight, so each tool mode has something to win on:
  * Wide catalog: 36 tools with long descriptions -> big tools/list (Search mode helps)
  * Fat responses: get_positions returns ~500 rows / ~150 KB  (Code mode helps)

All data is synthetic and deterministic (fixed seed), so the measurement
harness can import build_data() and compute the right answer itself.
"""
import os
import random
from datetime import date, timedelta

PRODUCT_DEFS = [
    # symbol, name, asset_class, multiplier, tick, base_price  (all instruments are invented)
    ("LCX", "Large-Cap Equity Index Future", "Equity Index", 50, 0.25, 5850.0),
    ("TCX", "Tech Equity Index Future", "Equity Index", 20, 0.25, 20500.0),
    ("ICX", "Industrial Equity Index Future", "Equity Index", 5, 1.0, 42000.0),
    ("SCX", "Small-Cap Equity Index Future", "Equity Index", 50, 0.1, 2250.0),
    ("MLC", "Micro Large-Cap Index Future", "Equity Index", 5, 0.25, 5850.0),
    ("MTC", "Micro Tech Index Future", "Equity Index", 2, 0.25, 20500.0),
    ("N10", "10-Year Note Future", "Interest Rate", 1000, 0.015625, 111.5),
    ("B30", "30-Year Bond Future", "Interest Rate", 1000, 0.03125, 117.2),
    ("N05", "5-Year Note Future", "Interest Rate", 1000, 0.0078125, 107.9),
    ("N02", "2-Year Note Future", "Interest Rate", 2000, 0.00390625, 103.4),
    ("RT3", "Three-Month Rate Future", "Interest Rate", 2500, 0.005, 96.1),
    ("CRD", "Crude Oil Future", "Energy", 1000, 0.01, 71.4),
    ("NGS", "Natural Gas Future", "Energy", 10000, 0.001, 2.9),
    ("GSL", "Gasoline Future", "Energy", 42000, 0.0001, 2.05),
    ("DSL", "Diesel Future", "Energy", 42000, 0.0001, 2.31),
    ("GLD", "Gold Future", "Metals", 100, 0.1, 2650.0),
    ("SLV", "Silver Future", "Metals", 5000, 0.005, 31.2),
    ("CPR", "Copper Future", "Metals", 25000, 0.0005, 4.35),
    ("PLT", "Platinum Future", "Metals", 50, 0.1, 985.0),
    ("CRN", "Corn Future", "Agriculture", 50, 0.25, 425.0),
    ("SOY", "Soybean Future", "Agriculture", 50, 0.25, 1010.0),
    ("WHT", "Wheat Future", "Agriculture", 50, 0.25, 575.0),
    ("CTL", "Cattle Future", "Agriculture", 400, 0.025, 186.0),
    ("HOG", "Hog Future", "Agriculture", 400, 0.025, 82.0),
    ("EUR", "Euro FX Future", "FX", 125000, 0.00005, 1.085),
    ("YEN", "Yen FX Future", "FX", 12500000, 0.0000005, 0.0067),
    ("GBP", "Pound FX Future", "FX", 62500, 0.0001, 1.27),
    ("AUD", "Aussie Dollar FX Future", "FX", 100000, 0.00005, 0.66),
    ("BTX", "Bitcoin Future", "Crypto", 5, 5.0, 64000.0),
    ("ETX", "Ether Future", "Crypto", 50, 0.5, 3100.0),
]

FIRM_WORDS = ["Granite", "Harbor", "Summit", "Northwind", "Bluewater", "Ironwood", "Lakeshore",
              "Meridian", "Prairie", "Redline", "Silverleaf", "Tidewater", "Westgate", "Keystone"]
FIRM_SUFFIX = ["Capital", "Partners", "Trading", "Asset Management", "Securities", "Fund LP", "Markets"]
STRATS = ["basis", "calendar-spread", "momentum", "mean-reversion", "hedge", "market-making", "macro", "vol-arb"]


def build_data(seed: int = 42):
    rnd = random.Random(seed)
    products = {}
    for sym, name, cls, mult, tick, px in PRODUCT_DEFS:
        products[sym] = {
            "symbol": sym, "name": name, "asset_class": cls, "exchange": "XDEMO",
            "contract_multiplier": mult, "tick_size": tick, "tick_value_usd": round(mult * tick, 6),
            "currency": "USD", "settlement_type": "cash" if cls in ("Equity Index", "Interest Rate", "Crypto") else "physical",
            "trading_hours": "23x5 electronic session with a daily one-hour maintenance window",
            "listed_months": "H,M,U,Z" if cls in ("Equity Index", "Interest Rate", "FX") else "all",
            "front_month": "Z6", "mark_price": round(px * (1 + rnd.uniform(-0.01, 0.01)), 6),
            "initial_margin_usd": round(mult * px * rnd.uniform(0.04, 0.09), 2),
            "maintenance_margin_usd": round(mult * px * rnd.uniform(0.03, 0.06), 2),
            "position_limit": rnd.choice([10000, 20000, 50000, 100000]),
            "description": f"{name}. Standardized exchange-traded futures contract on {cls.lower()} "
                           f"underlying, quoted in USD with multiplier {mult} and minimum tick {tick}.",
        }

    accounts = {}
    for i in range(1, 401):
        acct = f"ACC-{10000 + i}"
        accounts[acct] = {
            "account_id": acct,
            "firm_name": f"{rnd.choice(FIRM_WORDS)} {rnd.choice(FIRM_WORDS)} {rnd.choice(FIRM_SUFFIX)}",
            "lei": "".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(20)),
            "clearing_member": f"CM-{rnd.randint(100, 180)}",
            "risk_tier": rnd.choice(["T1", "T2", "T3"]),
            "account_type": rnd.choice(["house", "customer-segregated", "customer-omnibus"]),
            "country": rnd.choice(["US", "US", "US", "GB", "SG", "CH", "JP", "DE"]),
            "onboarded": str(date(2015, 1, 1) + timedelta(days=rnd.randint(0, 3600))),
            "status": "active",
        }

    positions = {}
    acct_ids = list(accounts)
    for sym, p in products.items():
        rows = []
        n = 500 if sym == "LCX" else rnd.randint(60, 140)
        for a in rnd.sample(acct_ids, min(n, len(acct_ids))) if n <= len(acct_ids) else acct_ids:
            qty = rnd.choice([-1, 1]) * int(rnd.paretovariate(1.3) * 10)
            if qty == 0:
                qty = 1
            avg = round(p["mark_price"] * (1 + rnd.uniform(-0.03, 0.03)), 6)
            rows.append({
                "account_id": a, "symbol": sym, "contract_month": rnd.choice(["Z6", "H7", "M7"]),
                "net_qty": qty, "long_qty": max(qty, 0) + rnd.randint(0, 5), "short_qty": max(-qty, 0) + rnd.randint(0, 5),
                "avg_entry_price": avg, "mark_price": p["mark_price"],
                "unrealized_pnl_usd": round((p["mark_price"] - avg) * qty * p["contract_multiplier"], 2),
                "initial_margin_usd": round(abs(qty) * p["initial_margin_usd"], 2),
                "trader_id": f"TR-{rnd.randint(1000, 9999)}", "strategy_tag": rnd.choice(STRATS),
                "last_update_utc": f"2026-10-13T{rnd.randint(13, 21):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}Z",
                "position_id": f"POS-{sym}-{rnd.randint(10**7, 10**8 - 1)}",
            })
        positions[sym] = rows
    return products, accounts, positions


def expected_top(n: int = 5, symbol: str = "LCX"):
    """Ground truth for the demo task, used by measure.py."""
    products, accounts, positions = build_data()
    mult = products[symbol]["contract_multiplier"]
    agg = {}
    for r in positions[symbol]:
        agg[r["account_id"]] = agg.get(r["account_id"], 0) + r["net_qty"] * r["mark_price"] * mult
    top = sorted(agg.items(), key=lambda kv: abs(kv[1]), reverse=True)[:n]
    rows = [{"account_id": a, "firm_name": accounts[a]["firm_name"], "notional_usd": round(abs(v))} for a, v in top]
    return rows, sum(r["notional_usd"] for r in rows)


def make_server():
    from mcp.server.fastmcp import FastMCP

    products, accounts, positions = build_data()
    mcp = FastMCP("trading-desk", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), stateless_http=True)

    @mcp.tool()
    def list_products(asset_class: str = "") -> dict:
        """List every listed futures product with its full contract specification (multiplier,
        tick size, tick value, margins, trading hours, settlement type). Optionally filter by asset_class
        (Equity Index, Interest Rate, Energy, Metals, Agriculture, FX, Crypto)."""
        rows = [p for p in products.values() if not asset_class or p["asset_class"].lower() == asset_class.lower()]
        return {"products": rows}

    @mcp.tool()
    def get_product_spec(symbol: str) -> dict:
        """Get the full contract specification for one futures product by symbol (e.g. LCX, CRD, N10),
        including contract_multiplier, tick size, margins and the current mark_price."""
        return products.get(symbol.upper(), {"error": f"unknown symbol {symbol}"})

    @mcp.tool()
    def get_positions(symbol: str) -> dict:
        """Return ALL open positions in a product across every account. Each row has account_id,
        net_qty (signed contracts), avg_entry_price, mark_price, unrealized P&L, margin, trader, strategy
        and timestamps. Large result: hundreds of rows for liquid products."""
        return {"symbol": symbol.upper(), "positions": positions.get(symbol.upper(), [])}

    @mcp.tool()
    def get_account(account_id: str) -> dict:
        """Get reference data for one account: firm_name, LEI, clearing member, risk tier,
        account type, country, onboarding date and status."""
        return accounts.get(account_id, {"error": f"unknown account {account_id}"})

    @mcp.tool()
    def list_accounts(clearing_member: str = "") -> dict:
        """List all accounts with full reference data, optionally filtered by clearing member (e.g. CM-120)."""
        rows = [a for a in accounts.values() if not clearing_member or a["clearing_member"] == clearing_member]
        return {"accounts": rows}

    # 31 more realistic-looking back-office tools. They inflate tools/list the way a real
    # enterprise MCP server does, but the demo task never needs them.
    filler = [
        ("get_trade_blotter", "Retrieve the executed-trade blotter for an account between two dates, including fills, venues, order IDs, aggressor side, fees and give-up details."),
        ("get_order_history", "Retrieve full order lifecycle history (new, amend, cancel, fill, reject) for an account in a date range with timestamps, order types and time-in-force."),
        ("get_settlement_prices", "Return daily settlement prices for all contract months of every product for a date range, with settlement methodology flags."),
        ("get_variation_margin", "Compute daily variation margin flows for an account across all products for a date range, broken down by product and contract month."),
        ("get_initial_margin_breakdown", "Return the scenario-based initial margin calculation for an account with scenario arrays, inter-commodity spread credits and short-option minimums."),
        ("get_collateral_balances", "List collateral lodged by an account (cash, treasuries, gold, letters of credit) with haircuts, valuations and concentration limits."),
        ("get_large_trader_report", "Produce the large-trader position report for an account and date, with reportable thresholds per product and aggregated ownership."),
        ("get_position_limit_utilization", "Show position-limit utilization per product for an account, including spot-month, single-month and all-months-combined limits."),
        ("get_risk_scenarios", "Run stress scenarios (historical and hypothetical) for an account's portfolio between two dates and return P&L under each scenario."),
        ("get_var_report", "Return 1-day and 10-day historical Value-at-Risk for an account at 95 and 99 percent confidence with component VaR by product."),
        ("get_fee_schedule", "Return the fee schedule applicable to an account: exchange fees, clearing fees, NFA fees, rebates and volume tiers."),
        ("get_give_up_agreements", "List give-up agreements for an account with executing brokers, clearing firms, effective dates and status."),
        ("get_allocation_instructions", "Retrieve post-trade allocation instructions for an account's block trades within a date range."),
        ("get_block_trades", "List privately negotiated block trades reported for an account with reporting delay, price, quantity and counterparty clearing member."),
        ("get_efp_transactions", "List exchange-for-physical (EFP) transactions for an account with related-position evidence and status."),
        ("get_delivery_notices", "Return physical delivery notices, intentions and assignments for an account during a delivery period."),
        ("get_option_greeks", "Return delta, gamma, vega, theta and rho for every option position held by an account, aggregated by underlying."),
        ("get_spread_positions", "Return calendar and inter-commodity spread positions for an account with leg details and spread margin credits."),
        ("get_market_depth", "Return the aggregated order book depth (10 levels) for a product's front month at a given timestamp."),
        ("get_time_and_sales", "Return tick-by-tick time and sales for a product between two timestamps with trade conditions."),
        ("get_volume_statistics", "Return daily volume and open interest statistics for a product over a date range, split by venue and session."),
        ("get_corporate_calendar", "Return the exchange holiday and early-close calendar for a date range with affected products."),
        ("get_surveillance_alerts", "List market-surveillance alerts raised for an account (spoofing, wash trading, marking the close) within a date range."),
        ("get_audit_trail", "Return the regulatory audit trail for an account's orders and messages within a date range, including tag-level FIX detail."),
        ("get_kyc_documents", "List KYC and onboarding documents on file for an account with expiry dates and verification status."),
        ("get_credit_limits", "Return pre-trade credit limits configured for an account with utilization by product group and trading day."),
        ("get_bank_instructions", "Return standing settlement and bank instructions for an account with currency, correspondent and status."),
        ("get_statement", "Generate the daily or monthly customer statement for an account covering trades, positions, cash and margin."),
        ("get_tax_lots", "Return tax lots for an account's closed positions within a date range with cost basis and realized gains."),
        ("get_reg_reporting_status", "Return the status of regulatory report submissions (CFTC, EMIR, MiFIR) for an account within a date range."),
        ("get_incident_log", "Return operational incidents affecting an account (busted trades, late settlements, system outages) within a date range."),
    ]

    def make_filler(tool_name):
        def fn(account_id: str, start_date: str, end_date: str, include_details: bool = False,
               output_format: str = "json", page: int = 1, page_size: int = 100) -> dict:
            return {"tool": tool_name, "account_id": account_id, "start_date": start_date,
                    "end_date": end_date, "rows": [], "note": "no records in range"}
        return fn

    for name, desc in filler:
        long_desc = (desc + " Dates are ISO-8601 (YYYY-MM-DD). Results are paginated with page and "
                     "page_size; set include_details to true to expand nested records. output_format may be "
                     "json or csv. Requires read entitlement on the account.")
        mcp.add_tool(make_filler(name), name=name, description=long_desc)
    return mcp


if __name__ == "__main__":
    make_server().run(transport="streamable-http")
