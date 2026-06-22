from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastmcp.tools import tool
from mcp.types import ToolAnnotations
from prefab_ui.actions import SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    H3,
    Alert,
    AlertDescription,
    AlertTitle,
    Badge,
    Card,
    CardContent,
    CardHeader,
    Column,
    DataTable,
    DataTableColumn,
    Grid,
    If,
    Link,
    Metric,
    Row,
    Small,
    Text,
)
from prefab_ui.components.charts import AreaChart, BarChart, ChartSeries, PieChart
from prefab_ui.rx import STATE, Rx

from src.mcp_components.tools import fetch_search_data, map_search_error


# ---------------------------------------------------------------------------
# MCP Apps (SEP-1865): interactive UI variants of `search`.
#
# These are opt-in: the plain-text `search` tool above is unchanged and stays
# the default. App-aware hosts can call `search_table` / `search_dashboard`
# to get an interactive UI rendered in the conversation; the bulk SERP JSON
# never enters the model context window. Hosts that don't support the Apps
# extension simply ignore these tools.
# ---------------------------------------------------------------------------


def _result_source(result: dict[str, Any]) -> str:
    """Best-effort source label for an organic result (explicit source or host)."""
    source = result.get("source")
    if source:
        return str(source)
    host = urlparse(result.get("link", "") or "").netloc
    return host[4:] if host.startswith("www.") else host


def organic_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten SerpApi organic_results into compact, table-ready rows."""
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(data.get("organic_results") or [], start=1):
        rows.append(
            {
                "position": result.get("position", index),
                "title": result.get("title", ""),
                "link": result.get("link", ""),
                "source": _result_source(result),
                "snippet": result.get("snippet", ""),
            }
        )
    return rows


def source_breakdown(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Count results per source for the dashboard pie chart (top `limit`)."""
    counts = Counter(row["source"] for row in rows if row["source"])
    return [{"source": source, "count": count} for source, count in counts.most_common(limit)]


def dashboard_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Derive the dashboard view-model from a SerpApi response."""
    params = data.get("search_parameters") or {}
    info = data.get("search_information") or {}
    rows = organic_rows(data)
    return {
        "query": params.get("q", ""),
        "engine": params.get("engine", ""),
        "total_results": info.get("total_results"),
        "result_count": len(rows),
        "rows": rows,
        "sources": source_breakdown(rows),
    }


def _error_app(message: str) -> PrefabApp:
    """Render an upstream/search error as an Apps alert instead of raw text."""
    with PrefabApp(title="Search error") as app:
        with Alert(variant="destructive"):
            AlertTitle(content="Search failed")
            AlertDescription(content=message)
    return app


_ORGANIC_COLUMNS = [
    DataTableColumn(key="position", header="#", sortable=True, width="64px"),
    DataTableColumn(key="title", header="Title", sortable=True),
    DataTableColumn(key="source", header="Source", sortable=True),
    DataTableColumn(key="snippet", header="Snippet"),
]


def build_table_app(data: dict[str, Any]) -> PrefabApp:
    """Compose the results-table UI from a SerpApi response."""
    with PrefabApp(title="Search results") as app:
        with Column(gap=4, css_class="p-4"):
            DataTable(
                columns=_ORGANIC_COLUMNS,
                rows=organic_rows(data),
                search=True,
                paginated=True,
                page_size=10,
            )
    return app


def build_dashboard_app(data: dict[str, Any]) -> PrefabApp:
    """Compose the dashboard UI (metrics + chart + table + detail) from a response."""
    summary = dashboard_summary(data)
    total = summary["total_results"]

    with PrefabApp(title="Search dashboard", state={"selected": None}) as app:
        with Column(gap=4, css_class="p-4"):
            with Grid(columns=[1, 1, 1], gap=4):
                Metric(label="Query", value=summary["query"] or "—")
                Metric(label="Engine", value=summary["engine"] or "—")
                Metric(
                    label="Results shown",
                    value=str(summary["result_count"]),
                    description=(f"of ~{total:,} total" if isinstance(total, int) else None),
                )

            with Grid(columns=[1, 2], gap=4):
                if summary["sources"]:
                    PieChart(
                        data=summary["sources"],
                        data_key="count",
                        name_key="source",
                        show_legend=True,
                        height=260,
                    )
                DataTable(
                    columns=_ORGANIC_COLUMNS,
                    rows=summary["rows"],
                    search=True,
                    on_row_click=SetState("selected", Rx("$event")),
                )

            with If(STATE.selected):
                with Card():
                    with CardHeader():
                        H3(Rx("selected.title"))
                        Small(content=Rx("selected.source"))
                    with CardContent():
                        with Column(gap=2):
                            Text(content=Rx("selected.snippet"))
                            Link(
                                content=Rx("selected.link"),
                                href=Rx("selected.link"),
                                target="_blank",
                            )
    return app


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "KRW": "₩",
    "BRL": "R$",
    "AUD": "A$",
    "CAD": "C$",
}


def _currency_symbol(data: dict[str, Any]) -> str:
    """Extract currency symbol from a SerpApi response's search_parameters."""
    code = (data.get("search_parameters") or {}).get("currency", "USD")
    return _CURRENCY_SYMBOLS.get(code, code + " ")


def _fmt_price(amount: int | float | None, symbol: str) -> str:
    """Format a numeric price with the given currency symbol."""
    if not amount:
        return "—"
    return f"{symbol}{amount:,.0f}"


# ---------------------------------------------------------------------------
# Flights-specific App builder
# ---------------------------------------------------------------------------


def flights_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten best_flights + other_flights into table-ready rows."""
    symbol = _currency_symbol(data)
    rows: list[dict[str, Any]] = []
    for section in ("best_flights", "other_flights"):
        for itinerary in data.get(section) or []:
            segments = itinerary.get("flights") or []
            airlines = sorted({seg.get("airline", "") for seg in segments} - {""})
            departure = segments[0] if segments else {}
            arrival = segments[-1] if segments else {}
            dep_airport = departure.get("departure_airport") or {}
            arr_airport = arrival.get("arrival_airport") or {}
            stops = len(itinerary.get("layovers") or [])
            carbon = itinerary.get("carbon_emissions") or {}
            carbon_pct = carbon.get("difference_percent")
            rows.append(
                {
                    "airline": ", ".join(airlines) or "—",
                    "route": f"{dep_airport.get('id', '?')} → {arr_airport.get('id', '?')}",
                    "departure": dep_airport.get("time", ""),
                    "arrival": arr_airport.get("time", ""),
                    "duration": _format_duration(itinerary.get("total_duration")),
                    "stops": "Direct" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}",
                    "price": itinerary.get("price") or 0,
                    "price_fmt": _fmt_price(itinerary.get("price"), symbol),
                    "carbon_delta": carbon_pct,
                    "carbon_fmt": (
                        f"{carbon_pct:+d}% vs typical" if isinstance(carbon_pct, int) else "—"
                    ),
                    "type": itinerary.get("type", ""),
                }
            )
    return rows


def _format_duration(minutes: int | None) -> str:
    if not minutes:
        return "—"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def price_history_points(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert price_insights.price_history into chart-ready [{date, price}]."""
    insights = data.get("price_insights") or {}
    history = insights.get("price_history") or []
    points: list[dict[str, Any]] = []
    for entry in history:
        if isinstance(entry, list) and len(entry) >= 2:
            ts, price = entry[0], entry[1]
            points.append(
                {
                    "date": datetime.fromtimestamp(ts, tz=UTC).strftime("%b %d"),
                    "price": price,
                }
            )
    return points


def flights_price_insights(data: dict[str, Any]) -> dict[str, Any]:
    """Extract price intelligence metrics from a flights response."""
    insights = data.get("price_insights") or {}
    typical = insights.get("typical_price_range") or []
    return {
        "lowest_price": insights.get("lowest_price"),
        "price_level": insights.get("price_level", "unknown"),
        "typical_low": typical[0] if len(typical) >= 1 else None,
        "typical_high": typical[1] if len(typical) >= 2 else None,
    }


_PRICE_LEVEL_VARIANTS = {
    "low": "success",
    "typical": "secondary",
    "high": "warning",
    "very high": "destructive",
}

_FLIGHTS_COLUMNS = [
    DataTableColumn(key="airline", header="Airline", sortable=True),
    DataTableColumn(key="route", header="Route", sortable=True),
    DataTableColumn(key="departure", header="Departs", sortable=True),
    DataTableColumn(key="arrival", header="Arrives", sortable=True),
    DataTableColumn(key="duration", header="Duration", sortable=True),
    DataTableColumn(key="stops", header="Stops", sortable=True),
    DataTableColumn(key="price", header="Price", sortable=True, format="currency"),
]


def build_flights_app(data: dict[str, Any]) -> PrefabApp:
    """Compose the flights price intelligence dashboard."""
    insights = flights_price_insights(data)
    rows = flights_rows(data)
    history = price_history_points(data)
    symbol = _currency_symbol(data)

    lowest = insights["lowest_price"]
    level = insights["price_level"]
    typical_low = insights["typical_low"]
    typical_high = insights["typical_high"]

    title = "Flights dashboard"
    params = data.get("search_parameters") or {}
    dep = params.get("departure_id", "")
    arr = params.get("arrival_id", "")
    if dep and arr:
        title = f"Flights: {dep} → {arr}"

    with PrefabApp(title=title, state={"selected": None}) as app:
        with Column(gap=4, css_class="p-4"):
            # Metrics row
            with Grid(columns=[1, 1, 1, 1], gap=4):
                Metric(
                    label="Lowest price",
                    value=_fmt_price(lowest, symbol),
                )
                Metric(
                    label="Typical range",
                    value=(
                        f"{_fmt_price(typical_low, symbol)}–{_fmt_price(typical_high, symbol)}"
                        if typical_low and typical_high
                        else "—"
                    ),
                )
                Metric(
                    label="Flights found",
                    value=str(len(rows)),
                )
                with Column(gap=1):
                    Text(content="Price level")
                    Badge(
                        label=level.capitalize(),
                        variant=_PRICE_LEVEL_VARIANTS.get(level, "outline"),
                    )

            # Price history chart
            if history:
                AreaChart(
                    data=history,
                    series=[ChartSeries(data_key="price", label="Price ($)")],
                    x_axis="date",
                    height=280,
                    curve="smooth",
                    show_dots=False,
                )

            # Flights table
            DataTable(
                columns=_FLIGHTS_COLUMNS,
                rows=rows,
                search=True,
                paginated=True,
                page_size=15,
                on_row_click=SetState("selected", Rx("$event")),
            )

            # Detail panel
            with If(STATE.selected):
                with Card():
                    with CardHeader():
                        with Row(gap=2):
                            H3(Rx("selected.airline"))
                            Badge(label=Rx("selected.stops"), variant="secondary")
                            Badge(label=Rx("selected.carbon_fmt"), variant="outline")
                    with CardContent():
                        with Grid(columns=[1, 1, 1, 1], gap=4):
                            with Column(gap=1):
                                Small(content="Route")
                                Text(content=Rx("selected.route"))
                            with Column(gap=1):
                                Small(content="Departure")
                                Text(content=Rx("selected.departure"))
                            with Column(gap=1):
                                Small(content="Arrival")
                                Text(content=Rx("selected.arrival"))
                            with Column(gap=1):
                                Small(content="Duration")
                                Text(content=Rx("selected.duration"))
                        with Grid(columns=[1, 1, 1, 1], gap=4, css_class="mt-2"):
                            with Column(gap=1):
                                Small(content="Price")
                                Text(content=Rx("selected.price_fmt"))
                            with Column(gap=1):
                                Small(content="Carbon emissions")
                                Text(content=Rx("selected.carbon_fmt"))
                            with Column(gap=1):
                                Small(content="Trip type")
                                Text(content=Rx("selected.type"))

    return app


# ---------------------------------------------------------------------------
# Jobs-specific App builder
# ---------------------------------------------------------------------------

# Benefits detected from extensions that get badge treatment.
_JOB_BENEFIT_LABELS = {
    "Health insurance",
    "Dental insurance",
    "Paid time off",
    "401(k)",
    "Vision insurance",
    "Life insurance",
    "Disability insurance",
    "Commuter benefits",
    "Tuition reimbursement",
}


def jobs_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten jobs_results into table-ready rows with structured metadata."""
    rows: list[dict[str, Any]] = []
    for job in data.get("jobs_results") or []:
        ext = job.get("detected_extensions") or {}
        extensions = job.get("extensions") or []
        benefits = [e for e in extensions if e in _JOB_BENEFIT_LABELS]
        rows.append(
            {
                "title": job.get("title", ""),
                "company": job.get("company_name", ""),
                "location": job.get("location", ""),
                "salary": ext.get("salary", ""),
                "schedule": ext.get("schedule_type", ""),
                "posted": ext.get("posted_at", ""),
                "qualifications": ext.get("qualifications", ""),
                "work_from_home": ext.get("work_from_home", False),
                "benefits": benefits,
                "benefits_fmt": ", ".join(benefits) if benefits else "—",
                "via": job.get("via", ""),
                "description": (job.get("description") or "")[:300],
                "highlights": job.get("job_highlights") or [],
                "apply_options": job.get("apply_options") or [],
                "source_link": job.get("source_link", ""),
            }
        )
    return rows


def jobs_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Derive summary metrics from a jobs response."""
    rows = jobs_rows(data)
    total = len(rows)
    with_salary = sum(1 for r in rows if r["salary"])
    remote = sum(1 for r in rows if r["work_from_home"])
    return {
        "total": total,
        "with_salary": with_salary,
        "remote": remote,
        "salary_pct": f"{with_salary * 100 // total}%" if total else "—",
        "remote_pct": f"{remote * 100 // total}%" if total else "—",
        "rows": rows,
        "schedule_breakdown": jobs_schedule_breakdown(rows),
    }


def jobs_schedule_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count jobs per schedule type for the pie chart."""
    counts = Counter(r["schedule"] or "Unspecified" for r in rows)
    return [{"schedule": schedule, "count": count} for schedule, count in counts.most_common()]


_JOBS_COLUMNS = [
    DataTableColumn(key="title", header="Title", sortable=True),
    DataTableColumn(key="company", header="Company", sortable=True),
    DataTableColumn(key="location", header="Location", sortable=True),
    DataTableColumn(key="salary", header="Salary", sortable=True),
    DataTableColumn(key="schedule", header="Type", sortable=True),
    DataTableColumn(key="posted", header="Posted", sortable=True),
    DataTableColumn(key="benefits_fmt", header="Benefits"),
]


def build_jobs_app(data: dict[str, Any]) -> PrefabApp:
    """Compose the jobs explorer dashboard."""
    summary = jobs_summary(data)
    rows = summary["rows"]
    params = data.get("search_parameters") or {}
    query = params.get("q", "")

    title = f"Jobs: {query}" if query else "Jobs dashboard"

    with PrefabApp(title=title, state={"selected": None}) as app:
        with Column(gap=4, css_class="p-4"):
            # Metrics row
            with Grid(columns=[1, 1, 1, 1], gap=4):
                Metric(label="Jobs found", value=str(summary["total"]))
                Metric(
                    label="With salary",
                    value=str(summary["with_salary"]),
                    description=summary["salary_pct"],
                )
                Metric(
                    label="Remote",
                    value=str(summary["remote"]),
                    description=summary["remote_pct"],
                )
                Metric(
                    label="Query",
                    value=query or "—",
                )

            # Schedule type breakdown
            if summary["schedule_breakdown"]:
                PieChart(
                    data=summary["schedule_breakdown"],
                    data_key="count",
                    name_key="schedule",
                    show_legend=True,
                    height=220,
                )

            # Jobs table
            DataTable(
                columns=_JOBS_COLUMNS,
                rows=rows,
                search=True,
                paginated=True,
                page_size=10,
                on_row_click=SetState("selected", Rx("$event")),
            )

            # Detail panel
            with If(STATE.selected):
                with Card():
                    with CardHeader():
                        H3(Rx("selected.title"))
                        with Row(gap=2):
                            Small(content=Rx("selected.company"))
                            Text(content="·")
                            Small(content=Rx("selected.location"))
                        with Row(gap=2, css_class="mt-2"):
                            with If(Rx("selected.salary")):
                                Badge(label=Rx("selected.salary"), variant="default")
                            with If(Rx("selected.schedule")):
                                Badge(
                                    label=Rx("selected.schedule"),
                                    variant="secondary",
                                )
                            with If(Rx("selected.work_from_home")):
                                Badge(label="Remote", variant="success")
                    with CardContent():
                        with Column(gap=3):
                            Text(content=Rx("selected.description"))
                            with If(Rx("selected.source_link")):
                                Link(
                                    content="View full listing →",
                                    href=Rx("selected.source_link"),
                                    target="_blank",
                                )

    return app


# ---------------------------------------------------------------------------
# Shopping-specific App builder
# ---------------------------------------------------------------------------


def shopping_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten shopping_results into table-ready rows."""
    rows: list[dict[str, Any]] = []
    for item in data.get("shopping_results") or []:
        price = item.get("extracted_price")
        extensions = item.get("extensions") or []
        discount_tag = next((e for e in extensions if "OFF" in e), "")
        rows.append(
            {
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "price": price or 0,
                "price_fmt": item.get("price", "—"),
                "old_price_fmt": item.get("old_price", ""),
                "discount": discount_tag,
                "rating": item.get("rating") or 0,
                "reviews": item.get("reviews") or 0,
                "snippet": item.get("snippet", ""),
                "product_link": item.get("product_link", ""),
            }
        )
    return rows


def _extract_currency_prefix(data: dict[str, Any]) -> str:
    """Extract the currency symbol from the first shopping result's price string."""
    for item in data.get("shopping_results") or []:
        price_str = item.get("price", "")
        if price_str:
            prefix = ""
            for ch in price_str:
                if ch.isdigit() or ch in ".,":
                    break
                prefix += ch
            if prefix:
                return prefix
    return "$"


def shopping_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Derive summary metrics and price-by-source chart data."""
    rows = shopping_rows(data)
    prices = [r["price"] for r in rows if r["price"] > 0]
    on_sale = sum(1 for r in rows if r["old_price_fmt"])
    avg_rating = (
        sum(r["rating"] for r in rows if r["rating"]) / max(1, sum(1 for r in rows if r["rating"]))
        if rows
        else 0
    )

    # Price by source (top 10 cheapest for the bar chart)
    priced = sorted([r for r in rows if r["price"] > 0], key=lambda r: r["price"])
    price_chart = [{"source": r["source"], "price": r["price"]} for r in priced[:10]]

    symbol = _extract_currency_prefix(data)

    return {
        "total": len(rows),
        "price_min": min(prices) if prices else 0,
        "price_max": max(prices) if prices else 0,
        "on_sale": on_sale,
        "avg_rating": round(avg_rating, 1),
        "rows": rows,
        "price_chart": price_chart,
        "currency_symbol": symbol,
    }


_SHOPPING_COLUMNS = [
    DataTableColumn(key="title", header="Product", sortable=True),
    DataTableColumn(key="source", header="Seller", sortable=True),
    DataTableColumn(key="price", header="Price", sortable=True, format="currency"),
    DataTableColumn(key="old_price_fmt", header="Was"),
    DataTableColumn(key="discount", header="Discount"),
    DataTableColumn(key="rating", header="Rating", sortable=True),
    DataTableColumn(key="reviews", header="Reviews", sortable=True),
]


def build_shopping_app(data: dict[str, Any]) -> PrefabApp:
    """Compose the shopping price comparison dashboard."""
    summary = shopping_summary(data)
    rows = summary["rows"]
    params = data.get("search_parameters") or {}
    query = params.get("q", "")
    sym = summary["currency_symbol"]

    title = f"Shopping: {query}" if query else "Shopping dashboard"

    with PrefabApp(title=title, state={"selected": None}) as app:
        with Column(gap=4, css_class="p-4"):
            # Metrics row
            with Grid(columns=[1, 1, 1, 1], gap=4):
                Metric(label="Products", value=str(summary["total"]))
                Metric(
                    label="Price range",
                    value=(
                        f"{sym}{summary['price_min']:,.0f}–{sym}{summary['price_max']:,.0f}"
                        if summary["price_min"]
                        else "—"
                    ),
                )
                Metric(label="On sale", value=str(summary["on_sale"]))
                Metric(
                    label="Avg rating",
                    value=str(summary["avg_rating"]) if summary["avg_rating"] else "—",
                )

            # Price comparison bar chart (top 10 cheapest sellers)
            if summary["price_chart"]:
                BarChart(
                    data=summary["price_chart"],
                    series=[ChartSeries(data_key="price", label="Price ($)")],
                    x_axis="source",
                    height=240,
                    horizontal=True,
                )

            # Products table
            DataTable(
                columns=_SHOPPING_COLUMNS,
                rows=rows,
                search=True,
                paginated=True,
                page_size=15,
                on_row_click=SetState("selected", Rx("$event")),
            )

            # Detail panel
            with If(STATE.selected):
                with Card():
                    with CardHeader():
                        H3(Rx("selected.title"))
                        with Row(gap=2):
                            Small(content=Rx("selected.source"))
                            with If(Rx("selected.discount")):
                                Badge(
                                    label=Rx("selected.discount"),
                                    variant="destructive",
                                )
                    with CardContent():
                        with Column(gap=2):
                            with Row(gap=4):
                                Text(content=Rx("selected.price_fmt"))
                                with If(Rx("selected.old_price_fmt")):
                                    Small(content=Rx("selected.old_price_fmt"))
                            with If(Rx("selected.snippet")):
                                Text(content=Rx("selected.snippet"))
                            with If(Rx("selected.product_link")):
                                Link(
                                    content="View on Google Shopping →",
                                    href=Rx("selected.product_link"),
                                    target="_blank",
                                )

    return app


# Engine-specific app dispatch: maps engine names to their dedicated builders.
# Falls back to the generic dashboard for unregistered engines.
ENGINE_APP_BUILDERS: dict[str, Any] = {
    "google_flights": build_flights_app,
    "google_jobs": build_jobs_app,
    "google_shopping": build_shopping_app,
}


@tool(
    meta={"ui": True},
    description=(
        "Interactive UI variant of `search`: returns organic results as a "
        "sortable, searchable table rendered in the conversation. Same params "
        "as `search`. Use when the host supports MCP Apps and the user wants "
        "to browse results visually rather than read JSON."
    ),
    annotations=ToolAnnotations(
        title="SerpApi search (table)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def search_table(params: dict[str, Any] = None) -> PrefabApp:
    try:
        data = fetch_search_data(params)
    except Exception as exc:
        return _error_app(str(exc) if isinstance(exc, RuntimeError) else map_search_error(exc))
    return build_table_app(data)


@tool(
    meta={"ui": True},
    description=(
        "Interactive dashboard variant of `search`: returns summary metrics, a "
        "source breakdown chart, and a results table with a click-to-expand "
        "detail panel, all rendered in the conversation. Same params as "
        "`search`. Use for a richer visual overview of a query's results. "
        "Automatically selects an engine-specific dashboard when available "
        "(e.g. google_flights gets price intelligence charting)."
    ),
    annotations=ToolAnnotations(
        title="SerpApi search (dashboard)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def search_dashboard(params: dict[str, Any] = None) -> PrefabApp:
    try:
        data = fetch_search_data(params)
    except Exception as exc:
        return _error_app(str(exc) if isinstance(exc, RuntimeError) else map_search_error(exc))
    engine = (params or {}).get("engine", "google_light")
    builder = ENGINE_APP_BUILDERS.get(engine, build_dashboard_app)
    return builder(data)
