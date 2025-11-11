from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
from typing import Dict, Any
from serpapi import SerpApiClient as SerpApiSearch
import httpx

load_dotenv()
API_KEY = os.getenv("SERPAPI_API_KEY")

if not API_KEY:
    raise ValueError("SERPAPI_API_KEY not found in environment variables. Please set it in the .env file.")

mcp = FastMCP("SerpApi MCP Server")

@mcp.tool()
async def search(params: Dict[str, Any] = {}) -> str:
    """Perform a search on the specified engine using SerpApi.

    Args:
        params: Dictionary of engine-specific parameters (e.g., {"q": "Coffee", "engine": "google_light", "location": "Austin, TX"}).

    Returns:
        A formatted string of search results or an error message.
    """

    params = {
        "api_key": API_KEY,
        "engine": "google_light", # Fastest engine by default
        **params  # Include any additional parameters
    }

    try:
        search = SerpApiSearch(params)
        data = search.get_dict()

        # Process organic search results if available
        if "organic_results" in data:
            formatted_results = []
            for result in data.get("organic_results", []):
                title = result.get("title", "No title")
                link = result.get("link", "No link")
                snippet = result.get("snippet", "No snippet")
                formatted_results.append(f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n")
            return "\n".join(formatted_results) if formatted_results else "No organic results found"
        else:
            return "No organic results found"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please try again later."
        elif e.response.status_code == 401:
            return "Error: Invalid API key. Please check your SERPAPI_API_KEY."
        else:
            return f"Error: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def get_weather(location: str, unit: str = "fahrenheit", include_daily_forecast: bool = False, include_hourly_forecast: bool = False) -> str:
    """Get weather results for a specific location via SerpApi.

    Args:
        location: The location to get weather results for.
        unit: The unit to get weather results for (fahrenheit or celsius).
        include_daily_forecast: Whether to include daily forecast in the response.
        include_hourly_forecast: Whether to include hourly forecast in the response.

    Returns:
        Weather results in a formatted string or an error message.
    """

    params = {
        "api_key": API_KEY,
        "engine": "google",
        "q": f"Weather in {location} (unit: {unit})",
    }

    try:
        search = SerpApiSearch(params)
        data = search.get_dict()

        answer_box = data.get("answer_box", {})
        if "answer_box" in data and answer_box["type"] == "weather_result":
            result = f"Temperature: {answer_box['temperature']}\nUnit: {answer_box['unit']}\nPrecipitation: {answer_box['precipitation']}\nHumidity: {answer_box['humidity']}\nWind: {answer_box['wind']}\nLocation: {answer_box['location']}\nDate: {answer_box['date']}\nWeather: {answer_box['weather']}"
            
            # Convert hourly forecast to CSV if present
            if include_hourly_forecast and "hourly_forecast" in answer_box:
                hourly_data = answer_box["hourly_forecast"]
                csv_rows = ["Time,Weather,Temperature,Precipitation,Humidity,Wind"]
                for hour in hourly_data:
                    row = [
                        hour.get("time", ""),
                        hour.get("weather", ""),
                        hour.get("temperature", ""),
                        hour.get("precipitation", ""),
                        hour.get("humidity", ""),
                        hour.get("wind", "")
                    ]
                    # Escape any commas in the values
                    row = [f'"{str(val)}"' if "," in str(val) else str(val) for val in row]
                    csv_rows.append(",".join(row))
                result += "\nHourly Forecast:\n" + "\n".join(csv_rows)

            if include_daily_forecast and "forecast" in answer_box:
                daily_data = answer_box["forecast"]
                csv_rows = ["Day,Weather,High Temperature,Low Temperature,Precipitation,Humidity,Wind"]
                for day in daily_data:
                    row = [
                        day.get("day", ""),
                        day.get("weather", ""),
                        day.get("temperature", {}).get("high", ""),
                        day.get("temperature", {}).get("low", ""),
                        day.get("precipitation", ""),
                        day.get("humidity", ""),
                        day.get("wind", "")
                    ]
                    # Escape any commas in the values
                    row = [f'"{str(val)}"' if "," in str(val) else str(val) for val in row]
                    csv_rows.append(",".join(row))
                result += "\nDaily Forecast:\n" + "\n".join(csv_rows)
            
            return result
        else:
            return "No weather results found"

    # Handle HTTP-specific errors
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please try again later."
        elif e.response.status_code == 401:
            return "Error: Invalid API key. Please check your SERPAPI_API_KEY."
        else:
            return f"Error: {e.response.status_code} - {e.response.text}"
    # Handle other exceptions (e.g., network issues)
    except Exception as e:
        print(e)
        return f"Error: {str(e)}"

@mcp.tool()
async def get_stock_market_preview(company_name: str) -> str:
    """Get stock market preview for a specific company via SerpApi.

    Args:
        company_name: The name of the company to get stock market preview for. It could be the company name or the ticker symbol.

    Returns:
        Stock market preview results that include price, currency, previous close, price movement, market cap, pe ratio, and table of key financial metrics in a formatted string or an error message.
    """

    params = {
        "api_key": API_KEY,
        "engine": "google",
        "q": f"{company_name} stock",
    }

    try:
        search = SerpApiSearch(params)
        data = search.get_dict()

        answer_box = data.get("answer_box", {})
        if "answer_box" in data and answer_box["type"] == "finance_results":
            result = [
                f"title: {answer_box['title']}",
                f"Exchange: {answer_box['exchange']}",
                f"Stock: {answer_box['stock']}",
                f"Currency: {answer_box['currency']}",
                f"Price: {answer_box['price']}",
                f"Previous Close: {answer_box['previous_close']}",
            ]
            if "price_movement" in answer_box:
                result.append(f"Price Movement: (price: {answer_box['price_movement']["price"]}) (percentage: {answer_box['price_movement']["percentage"]}) (movement: {answer_box['price_movement']["movement"]})")
            if "market" in answer_box:
                result.append(f"Market Status: (closed: {answer_box['market']["closed"]}) (date: {answer_box['market']["date"]}) (trading: {answer_box['market']["trading"]}) (price: {answer_box['market']["price"]}) (price_movement: (price {answer_box['market']["price_movement"]["price"]}) (percentage: {answer_box['market']["price_movement"]["percentage"]}) (movement: {answer_box['market']["price_movement"]["movement"]}))")
            if "table" in answer_box:
                table_data = answer_box["table"]
                for row in table_data:
                    result.append(f"{row['name']}: {row['value']}")

            return "\n".join(result)
        else:
            return "No weather results found"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please try again later."
        elif e.response.status_code == 401:
            return "Error: Invalid API key. Please check your SERPAPI_API_KEY."
        else:
            return f"Error: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        print(e)
        return f"Error: {str(e)}"

if __name__ == "__main__":
    mcp.run()