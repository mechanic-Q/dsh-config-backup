#!/usr/bin/env python3
"""
SearXNG Search CLI Tool

Search using a custom SearXNG instance via HTTP API.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any


def _http_get(url: str, timeout: int = 30) -> "http.client.HTTPResponse":
    req = urllib.request.Request(url, headers={"User-Agent": "searxng-search-skill/1.0"})
    return urllib.request.urlopen(req, timeout=timeout)


def search_searxng(
    base_url: str,
    query: str,
    format_type: str = "json",
    language: str | None = None,
    pageno: int = 1,
    time_range: str | None = None,
    categories: str | None = None,
    engines: str | None = None,
    safesearch: int | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """
    Search using SearXNG API.
    
    Args:
        base_url: SearXNG instance URL (e.g., https://searx.example.org)
        query: Search query string
        format_type: Output format (json, csv, rss)
        language: Language code (e.g., en, zh, de)
        pageno: Page number
        time_range: Time filter (day, month, year)
        categories: Comma-separated category list
        engines: Comma-separated engine list
        safesearch: Safe search level (0, 1, 2)
        timeout: Request timeout in seconds
    
    Returns:
        API response as dictionary (for JSON format)
    """
    # Normalize base URL
    base_url = base_url.rstrip("/")
    
    # Build search URL
    search_url = f"{base_url}/search"
    
    # Build parameters
    params: dict[str, Any] = {
        "q": query,
        "format": format_type,
        "pageno": pageno,
    }
    
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    if safesearch is not None:
        params["safesearch"] = safesearch
    
    # Make request
    search_url = search_url + "?" + urllib.parse.urlencode(params)
    response = _http_get(search_url, timeout=timeout)
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status} - 403 usually means format is disabled (check search.formats in settings.yml)")

    body = response.read().decode("utf-8")
    if format_type == "json":
        return json.loads(body)
    else:
        return {"content": body}


def format_results(results: dict[str, Any], max_results: int | None = None) -> str:
    """Format search results for display."""
    output = []

    # Query info
    query = results.get("query", "N/A")
    num_results = results.get("number_of_results", "Unknown")
    output.append(f"Query: {query}")
    output.append(f"Number of results: {num_results}")
    output.append("-" * 60)

    # Search results
    shown = 0
    for result in results.get("results", []):
        if max_results is not None and shown >= max_results:
            output.append(f"\n(showing first {max_results} results)")
            break
        shown += 1
        title = result.get("title", "No title")
        url = result.get("url", "No URL")
        content = result.get("content", "No description")
        engine = result.get("engine", "Unknown")
        
        output.append(f"\n{shown}. {title}")
        output.append(f"   URL: {url}")
        output.append(f"   Engine: {engine}")
        output.append(f"   {content}")
    
    # Suggestions
    suggestions = results.get("suggestions", [])
    if suggestions:
        output.append(f"\n{'-' * 60}")
        output.append("Suggestions:")
        for suggestion in suggestions:
            output.append(f"  - {suggestion}")
    
    # Answers
    answers = results.get("answers", [])
    if answers:
        output.append(f"\n{'-' * 60}")
        output.append("Answers:")
        for answer in answers:
            output.append(f"  - {answer}")
    
    # Unresponsive engines
    unresponsive = results.get("unresponsive_engines", [])
    if unresponsive:
        output.append(f"\n{'-' * 60}")
        # Handle both string and list items in unresponsive_engines
        formatted = []
        for item in unresponsive:
            if isinstance(item, list):
                formatted.append(', '.join(str(x) for x in item))
            else:
                formatted.append(str(item))
        output.append(f"Unresponsive engines: {', '.join(formatted)}")
    
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Search using SearXNG instance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -u https://searx.example.org -q "python tutorial"
  %(prog)s -u https://searx.example.org -q "news" --lang en --time-range day
  %(prog)s -u https://searx.example.org -q "search" --format csv
        """
    )
    
    parser.add_argument(
        "-u", "--url",
        default=os.environ.get("SEARXNG_URL", "http://localhost:8888"),
        help="SearXNG instance URL (default: SEARXNG_URL env or http://localhost:8888)"
    )
    parser.add_argument(
        "-q", "--query",
        required=True,
        help="Search query"
    )
    parser.add_argument(
        "--format",
        dest="format_type",
        choices=["json", "csv", "rss"],
        default="json",
        help="Output format (default: json)"
    )
    parser.add_argument(
        "--lang", "--language",
        help="Language code (e.g., en, zh, de)"
    )
    parser.add_argument(
        "--page", "--pageno",
        type=int,
        default=1,
        help="Page number (default: 1)"
    )
    parser.add_argument(
        "--time-range",
        choices=["day", "month", "year"],
        help="Time range filter"
    )
    parser.add_argument(
        "--categories",
        help="Comma-separated category list"
    )
    parser.add_argument(
        "--engines",
        help="Comma-separated engine list"
    )
    parser.add_argument(
        "--safesearch",
        type=int,
        choices=[0, 1, 2],
        help="Safe search level (0=off, 1=moderate, 2=strict)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)"
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Show only the first N results"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output raw JSON response"
    )
    
    args = parser.parse_args()
    
    # Get URL from args or environment
    base_url = args.url or os.environ.get("SEARXNG_URL")
    if not base_url:
        print("Error: SearXNG URL not provided. Use -u/--url or set SEARXNG_URL environment variable.", file=sys.stderr)
        sys.exit(1)
    
    try:
        results = search_searxng(
            base_url=base_url,
            query=args.query,
            format_type=args.format_type,
            language=args.lang,
            pageno=args.page,
            time_range=args.time_range,
            categories=args.categories,
            engines=args.engines,
            safesearch=args.safesearch,
            timeout=args.timeout,
        )
        
        if args.raw or args.format_type != "json":
            if isinstance(results, dict) and "content" in results:
                print(results["content"])
            else:
                print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(format_results(results, max_results=args.max_results))

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        print("排障:服务不通时,先启动 Docker Desktop,再 docker compose up -d (compose 文件在 D:\\SynologyDrive\\LMR\\Docker\\searxng)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
