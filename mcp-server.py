import os
import re
import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from dotenv import load_dotenv
#from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv()

mcp = FastMCP(
    "Moodys Scraper MCP Server",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_LOGIN_URL = os.getenv("MOODYS_LOGIN_URL", "https://login.moodys.com")
DEFAULT_SESSION_FILE = os.getenv("MOODYS_SESSION_FILE", "moodys_auth_state.json")
DEFAULT_HEADLESS = os.getenv("MOODYS_HEADLESS", "true").lower() == "true"
DEFAULT_USERNAME = os.getenv("MOODYS_USERNAME", "francis.leslie@gmail.com")
DEFAULT_PASSWORD = os.getenv("MOODYS_PASSWORD", "M@tr1xL0ngM00dys")

# ============================================================
# Company -> Moody's Entity ID map
# Add more companies here as needed
# ============================================================

COMPANY_ENTITY_MAP = {
    "amazon": "600042665",
    "amazon.com": "6600042665",
    "amazon inc": "600042665",
    "amazon.com inc": "600042665",

    "netflix": "821694682",
    "netflix inc": "821694682",

    "apple": "197800",
    "apple inc": "197800",

    "microsoft": "698200",
    "microsoft corporation": "698200",

    "alphabet": "824906971",
    "google": "824906971",
    "Alphabet Inc.": "824906971",
    "google llc": "824906971",

    "meta": "823627616",
    "meta platforms": "823627616",
    "facebook": "823627616",
    "meta platforms inc": "823627616",

    "tesla": "823642219",
    "tesla inc": "823642219",
}

BASE_ENTITY_URL = "https://www.moodys.com/entity/{entity_id}/overview"

MOODYS_RATING_ORDER = {
    "Aaa": 1,
    "Aa1": 2, "Aa2": 3, "Aa3": 4,
    "A1": 5, "A2": 6, "A3": 7,
    "Baa1": 8, "Baa2": 9, "Baa3": 10,
    "Ba1": 11, "Ba2": 12, "Ba3": 13,
    "B1": 14, "B2": 15, "B3": 16,
    "Caa1": 17, "Caa2": 18, "Caa3": 19,
    "Ca": 20,
    "C": 21,
}


@dataclass
class ScrapeResult:
    url: str
    title: Optional[str]
    entity_name: Optional[str]
    requested_company: Optional[str]
    resolved_entity_id: Optional[str]
    ratings: List[Dict[str, str]]
    outlooks: List[str]
    watch_items: List[str]
    overview_fields: Dict[str, str]
    extracted_text_sample: str
    decision: Dict[str, Any]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_company_key(name: str) -> str:
    name = (name or "").strip().lower()
    name = re.sub(r"[.,]", "", name)
    name = re.sub(r"\s+", " ", name)
    print(f"Normalized company key: '{name}'")
    return name


def resolve_entity_id(company: Optional[str] = None, entity_id: Optional[str] = None) -> Tuple[str, str]:
    """
    Resolve Moody's entity id from either a direct entity_id or a company name.
    Returns: (resolved_entity_id, constructed_url)
    """

    print(f"Resolving entity ID for company='{company}' entity_id='{entity_id}'")

    if entity_id:
        print(f"Direct entity_id provided: {entity_id}")
        resolved = str(entity_id).strip()
        if not resolved.isdigit():
            raise ValueError(f"Invalid entity_id: {entity_id}")
        return resolved, BASE_ENTITY_URL.format(entity_id=resolved)

    if not company:
        raise ValueError("Either company or entity_id must be provided")

    key = normalize_company_key(company)
    mapped = COMPANY_ENTITY_MAP.get(key)

    print(f'Mapped company key: "{key}" to entity_id: "{mapped}"')

    if not mapped:
        supported = ", ".join(sorted(set(COMPANY_ENTITY_MAP.keys())))
        raise ValueError(
            f"Company '{company}' not found in COMPANY_ENTITY_MAP. "
            f"Add it to the dictionary first. Known values include: {supported}"
        )

    print(f'search url = {BASE_ENTITY_URL.format(entity_id=mapped)}')
    return mapped, BASE_ENTITY_URL.format(entity_id=mapped)


def file_exists_and_nonempty(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def save_html_debug(html: str, filename: str = "moodys_debug.html") -> None:
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)


def try_programmatic_login(context, login_url: str, username: str, password: str) -> bool:
    page = context.new_page()
    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    user_selectors = [
        'input[name="username"]',
        'input[name="email"]',
        'input[type="email"]',
        'input[id*="user"]',
        'input[id*="email"]',
        'input[autocomplete="username"]',
    ]
    pass_selectors = [
        'input[name="password"]',
        'input[type="password"]',
        'input[id*="pass"]',
        'input[autocomplete="current-password"]',
    ]
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
        'button:has-text("Login")',
        'button:has-text("Continue")',
    ]

    username_filled = False
    password_filled = False

    for selector in user_selectors:
        try:
            page.locator(selector).first.fill(username, timeout=3000)
            username_filled = True
            break
        except Exception:
            continue

    for selector in pass_selectors:
        try:
            page.locator(selector).first.fill(password, timeout=3000)
            password_filled = True
            break
        except Exception:
            continue

    if not username_filled or not password_filled:
        page.close()
        return False

    clicked = False
    for selector in submit_selectors:
        try:
            page.locator(selector).first.click(timeout=3000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        try:
            page.keyboard.press("Enter")
            clicked = True
        except Exception:
            pass

    page.wait_for_timeout(5000)

    current_url = page.url.lower()
    html = page.content().lower()

    if "login" in current_url or "signin" in current_url:
        page.close()
        return False

    failure_markers = [
        "invalid password",
        "incorrect password",
        "sign in failed",
        "authentication failed",
        "try again",
    ]
    failed = any(marker in html for marker in failure_markers)

    page.close()
    return not failed


async def build_authenticated_context(
    p,
    auth_mode: str,
    session_file: str,
    login_url: str,
    username: str,
    password: str,
    headless: bool,
):
    logger.info("START build_authenticated_context")
    browser = await p.chromium.launch(headless=headless)

    context_kwargs = dict(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 2200},
        java_script_enabled=True,
    )

    if auth_mode == "session":
        if not file_exists_and_nonempty(session_file):
            await browser.close()
            raise FileNotFoundError(
                f"Session file not found: {session_file}. "
                "Create it first with a separate helper script."
            )
        context = await browser.new_context(storage_state=session_file, **context_kwargs)
        logger.info("END build_authenticated_context")
        return browser, context

    if auth_mode == "login":
        if not username or not password:
            await browser.close()
            raise ValueError("Username/password not provided for auth_mode='login'.")

        context = await browser.new_context(**context_kwargs)
        logged_in = await try_programmatic_login(
            context=context,
            login_url=login_url,
            username=username,
            password=password,
        )
        if not logged_in:
            await context.close()
            await browser.close()
            raise RuntimeError("Automated login failed. Use auth_mode='session' instead.")
        logger.info("END build_authenticated_context")
        return browser, context

    if auth_mode == "none":
        context = await browser.new_context(**context_kwargs)
        logger.info("END build_authenticated_context")
        return browser, context

    await browser.close()
    raise ValueError(f"Unsupported auth_mode: {auth_mode}")

def manual_login_and_save_session(
    login_url: str = DEFAULT_LOGIN_URL,
    session_file: str = DEFAULT_SESSION_FILE,
    headless: bool = False,
) -> None:
    """
    Opens a visible browser so the user can log in manually.
    After successful login, stores the browser auth state to a file.
    Best for SSO / MFA / bot-protected sites.
    """
    with async_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 2000},
        )
        page = context.new_page()

        logger.info(f"Opening login page: {login_url}")
        page.goto(login_url, wait_until="domcontentloaded", timeout=60000)

        logger.info("\nLog in manually in the opened browser.")
        logger.info("Complete username/password")

        input("\nAfter login is complete and you can access Moody's pages, press Enter here to save the session... ")

        context.storage_state(path=session_file)
        logger.info(f"Saved authenticated session to: {session_file}")

        context.close()
        browser.close()

async def fetch_rendered_html_authenticated(
    url: str,
    auth_mode: str = "session",
    session_file: str = DEFAULT_SESSION_FILE,
    login_url: str = DEFAULT_LOGIN_URL,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    headless: bool = DEFAULT_HEADLESS,
) -> str:
    logger.info("START fetch_rendered_html_authenticated")
    logger.info("Fetching URL with authentication: %s (auth_mode=%s)", url, auth_mode)

    try:
        logger.info("About to enter async_playwright()")
        async with async_playwright() as p:
            logger.info("Entered async_playwright() successfully")

            browser, context = await build_authenticated_context(
                p=p,
                auth_mode=auth_mode,
                session_file=session_file,
                login_url=login_url,
                username=username,
                password=password,
                headless=headless,
            )

            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3500)
                await page.mouse.wheel(0, 1500)
                await page.wait_for_timeout(1500)

                html = await page.content()
                logger.info("Fetched HTML content from %s (length: %s)", url, len(html))

                if auth_mode in ("session", "login"):
                    await context.storage_state(path=session_file)

                logger.info("END fetch_rendered_html_authenticated")
                return html
            finally:
                await context.close()
                await browser.close()
    except Exception:
        logger.exception("Error fetching URL with authentication")
        raise


def extract_title(soup: BeautifulSoup) -> Optional[str]:
    if soup.title and soup.title.string:
        return normalize_space(soup.title.string)
    return None


def extract_entity_name(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.find("h1")
    if h1:
        text = normalize_space(h1.get_text(" ", strip=True))
        if text:
            return text

    meta = soup.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content"):
        return normalize_space(meta["content"])

    return None


def parse_possible_json_objects(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        raw = raw.strip()
        if not raw:
            continue

        if script.get("type") in {"application/ld+json", "application/json"}:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    results.append(obj)
                elif isinstance(obj, list):
                    results.extend([x for x in obj if isinstance(x, dict)])
            except Exception:
                pass

    return results


def find_ratings_in_text(text: str) -> List[str]:
    pattern = r"\b(?:Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|Caa[123]|Ca|C)\b"
    found = re.findall(pattern, text)
    seen = set()
    result = []
    for item in found:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def extract_outlooks_and_watch(text: str) -> Tuple[List[str], List[str]]:
    outlooks: List[str] = []
    watch_items: List[str] = []

    outlook_patterns = [
        r"\bpositive outlook\b",
        r"\bstable outlook\b",
        r"\bnegative outlook\b",
        r"\bdeveloping outlook\b",
        r"\boutlook:?[\s\-]*(positive|stable|negative|developing)\b",
    ]
    for pat in outlook_patterns:
        for match in re.findall(pat, text, flags=re.IGNORECASE):
            val = normalize_space(match if isinstance(match, str) else " ".join(match))
            if val.lower() in {"positive", "stable", "negative", "developing"}:
                val = f"{val} outlook"
            if val.lower() not in {x.lower() for x in outlooks}:
                outlooks.append(val)

    watch_patterns = [
        r"\breview for upgrade\b",
        r"\breview for downgrade\b",
        r"\bon review\b",
        r"\bwatch(?:list)?\b.{0,30}\b(?:positive|negative|developing)\b",
    ]
    for pat in watch_patterns:
        for match in re.findall(pat, text, flags=re.IGNORECASE):
            val = normalize_space(match)
            if val.lower() not in {x.lower() for x in watch_items}:
                watch_items.append(val)

    return outlooks, watch_items


def extract_key_value_pairs(soup: BeautifulSoup) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key = normalize_space(cells[0].get_text(" ", strip=True))
                value = normalize_space(cells[1].get_text(" ", strip=True))
                if key and value:
                    fields.setdefault(key, value)

    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = normalize_space(dt.get_text(" ", strip=True))
            value = normalize_space(dd.get_text(" ", strip=True))
            if key and value:
                fields.setdefault(key, value)

    return fields


def extract_ratings(soup: BeautifulSoup, json_objects: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    ratings: List[Dict[str, str]] = []

    full_text = normalize_space(soup.get_text(" ", strip=True))
    for rating in find_ratings_in_text(full_text):
        ratings.append({
            "label": "Detected rating",
            "value": rating,
            "source": "page_text",
        })

    def walk(obj: Any, path: str = ""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                next_path = f"{path}.{k}" if path else k
                if isinstance(v, (dict, list)):
                    walk(v, next_path)
                else:
                    sval = normalize_space(str(v))
                    if re.fullmatch(r"(Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|Caa[123]|Ca|C)", sval):
                        ratings.append({
                            "label": next_path,
                            "value": sval,
                            "source": "embedded_json",
                        })
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]")

    for obj in json_objects:
        walk(obj)

    seen = set()
    deduped = []
    for item in ratings:
        key = (item["label"], item["value"], item["source"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped


def choose_primary_rating(ratings: List[Dict[str, str]]) -> Optional[str]:
    preferred_labels = [
        "issuer rating",
        "long-term issuer rating",
        "corporate family rating",
        "senior unsecured rating",
    ]
    for pref in preferred_labels:
        for item in ratings:
            if pref in item["label"].lower():
                return item["value"]
    for item in ratings:
        if item["value"] in MOODYS_RATING_ORDER:
            return item["value"]
    return None


def rate_to_band(rating: Optional[str]) -> str:
    if not rating or rating not in MOODYS_RATING_ORDER:
        return "unknown"
    rank = MOODYS_RATING_ORDER[rating]
    if rank <= 4:
        return "very_strong"
    if rank <= 10:
        return "investment_grade"
    if rank <= 16:
        return "speculative_grade"
    return "high_risk"


def infer_outlook_score(outlooks: List[str], watch_items: List[str]) -> int:
    text = " | ".join([x.lower() for x in outlooks + watch_items])
    score = 0
    if "positive" in text or "upgrade" in text:
        score += 1
    if "negative" in text or "downgrade" in text:
        score -= 1
    return score


def determine_signal(primary_rating: Optional[str], outlooks: List[str], watch_items: List[str]) -> Dict[str, Any]:
    band = rate_to_band(primary_rating)
    outlook_score = infer_outlook_score(outlooks, watch_items)

    reasons = []
    if primary_rating:
        reasons.append(f"Primary detected Moody's rating: {primary_rating}")
    else:
        reasons.append("No clear primary Moody's rating was found")

    if outlooks:
        reasons.append(f"Detected outlook terms: {', '.join(outlooks)}")
    if watch_items:
        reasons.append(f"Detected watch/review terms: {', '.join(watch_items)}")

    if band == "very_strong":
        signal = "BUY" if outlook_score >= 0 else "HOLD"
        confidence = "medium"
    elif band == "investment_grade":
        signal = "BUY" if outlook_score > 0 else "HOLD"
        confidence = "low" if outlook_score > 0 else "medium"
    elif band == "speculative_grade":
        signal = "SELL" if outlook_score < 0 else "HOLD"
        confidence = "medium" if outlook_score < 0 else "low"
    elif band == "high_risk":
        signal = "SELL"
        confidence = "medium"
    else:
        signal = "HOLD"
        confidence = "low"

    return {
        "signal": signal,
        "confidence": confidence,
        "primary_rating": primary_rating,
        "rating_band": band,
        "reasons": reasons,
        "note": "Demo heuristic only. Moody's ratings are credit opinions, not direct equity recommendations.",
    }


async def scrape_entity_internal(
    company: Optional[str] = None,
    entity_id: Optional[str] = None,
    auth_mode: str = "session",
    session_file: str = DEFAULT_SESSION_FILE,
    login_url: str = DEFAULT_LOGIN_URL,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    headless: bool = DEFAULT_HEADLESS,
    save_debug_html: bool = True,
    debug_html_file: str = "moodys_debug.html",
) -> Dict[str, Any]:
    logger.info("START scrape_entity_internal")

    resolved_entity_id, url = resolve_entity_id(company=company, entity_id=entity_id)

    html = await fetch_rendered_html_authenticated(
        url=url,
        auth_mode=auth_mode,
        session_file=session_file,
        login_url=login_url,
        username=username,
        password=password,
        headless=headless,
    )

    if save_debug_html:
        save_html_debug(html, debug_html_file)

    soup = BeautifulSoup(html, "lxml")
    #logger.info(f"Extracted soup: {soup}")

    title = extract_title(soup)
    entity_name = extract_entity_name(soup)
    json_objects = parse_possible_json_objects(soup)
    ratings = extract_ratings(soup, json_objects)

    full_text = normalize_space(soup.get_text(" ", strip=True))
    outlooks, watch_items = extract_outlooks_and_watch(full_text)
    overview_fields = extract_key_value_pairs(soup)

    primary_rating = choose_primary_rating(ratings)
    decision = determine_signal(primary_rating, outlooks, watch_items)

    logger.info(f"Decision: {decision}")

    result = ScrapeResult(
        url=url,
        title=title,
        entity_name=entity_name,
        requested_company=company,
        resolved_entity_id=resolved_entity_id,
        ratings=ratings,
        outlooks=outlooks,
        watch_items=watch_items,
        overview_fields=overview_fields,
        extracted_text_sample=full_text[:1500],
        decision=decision,
    )

    logger.info("END scrape_entity_internal")
    return asdict(result)


@mcp.tool()
def get_local_status() -> dict:
    """Return server status."""
    return {
        "status": "ok",
        "server": "Moodys Scraper MCP Server",
        "message": "Server is running",
        "transport": "streamable-http",
        "stateless_http": True,
        "json_response": True,
        "supported_companies": sorted(set(COMPANY_ENTITY_MAP.keys())),
    }


@mcp.tool()
async def scrape_moodys_entity(
    company: str = "",
    entity_id: str = "",
    auth_mode: str = "session",
    session_file: str = DEFAULT_SESSION_FILE,
    login_url: str = DEFAULT_LOGIN_URL,
    username: str = "",
    password: str = "",
    headless: bool = DEFAULT_HEADLESS,
    save_debug_html: bool = True,
    debug_html_file: str = "moodys_debug.html",
) -> Dict[str, Any]:
    """
    Analyze a company/entity on Moody's and return extracted information and a simple BUY/HOLD/SELL signal based on heuristics.

    Example:
      company="Netflix"
      Symbol="NFLX"
    """
    resolved_username = username or DEFAULT_USERNAME
    resolved_password = password or DEFAULT_PASSWORD

    print('In scrape_moodys_entity tool')

    result = await scrape_entity_internal(
        company=company or None,
        entity_id=None,
        auth_mode=auth_mode,
        session_file=session_file,
        login_url=login_url,
        username=resolved_username,
        password=resolved_password,
        headless=headless,
        save_debug_html=save_debug_html,
        debug_html_file=debug_html_file,
    )

    #logger.info(f"Scraping result for company='{company}': {json.dumps(result, indent=2)}")
    return result


if __name__ == "__main__":

    # manual_login_and_save_session(
    #             login_url=DEFAULT_LOGIN_URL,
    #             session_file=DEFAULT_SESSION_FILE,
    #             headless=False,
    #         )
    mcp.run(transport="streamable-http")
'''
txt = scrape_moodys_entity(
    company="Netflix",
    auth_mode="session",
    session_file=DEFAULT_SESSION_FILE,
    login_url=DEFAULT_LOGIN_URL,
    username=DEFAULT_USERNAME,
    password=DEFAULT_PASSWORD,
    headless=False,
    save_debug_html=True,
    debug_html_file="netflix_moodys.html",
)
print(txt)
'''