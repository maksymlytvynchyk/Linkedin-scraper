import re

from .models import extract_company_slug, extract_linkedin_id

# selectors (LinkedIn public templates)
CARD_ITEM = "li"                     # each card is an <li>
CARD_LINK = "a.base-card__full-link"
CARD_TITLE = "h3.base-search-card__title"
CARD_COMPANY = "h4.base-search-card__subtitle"
CARD_LOCATION = "span.job-search-card__location"
CARD_TIME = "time"
CARD_COMPANY_LINK = "a.hidden-nested-link, h4.base-search-card__subtitle a"

DETAIL_DESCRIPTION = "div.show-more-less-html__markup"
DETAIL_CRITERIA_ITEM = "li.description__job-criteria-item"
DETAIL_COMPANY_LINK = "a.topcard__org-name-link, a[href*='/company/']"
DETAIL_APPLY = "a.topcard__link, a.apply-button"

COMPANY_TITLE = "h1"
COMPANY_TAGLINE = "p.top-card-layout__headline, h2.top-card-layout__headline"
COMPANY_ABOUT = "section[data-test-id='about-us']"
COMPANY_FOLLOWERS = "span.tblack"

SALARY_RE = re.compile(
    r"(?:[$€£₽]\s?\d[\d,.\s]*[*kK]?\s*(?:-|–|to)\s*[$€£₽]?\s?\d[\d,.\s]*[*kK]?)",
    re.IGNORECASE,
)
APPLICANTS_RE = re.compile(r"(\d[\d,]*)\s+applicant", re.IGNORECASE)
AGO_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
NUMBER_RE = re.compile(r"[\d,]+")

_UNIT_DAYS = {
    "minute": 0,
    "hour": 0,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
_UNIT_SCALE = {"minute": 1, "hour": 1, "day": 1, "week": 7, "month": 30, "year": 365}


def _soup(html: str):
    """Build a soup, preferring lxml and falling back to the stdlib parser."""
    from bs4 import BeautifulSoup

    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, parser)
        except Exception:  # pragma: no cover - parser availability
            continue
    return BeautifulSoup(html, "html.parser")


def _text(node) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _attr(node, name: str) -> str:
    if node is None:
        return ""
    value = node.get(name) or ""
    return value.strip() if isinstance(value, str) else ""


def _abs(url: str, base: str = "https://www.linkedin.com") -> str:
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return base + url
    return url


def to_int(value: str) -> int | None:
    if not value:
        return None
    digits = NUMBER_RE.findall(value)
    if not digits:
        return None
    try:
        return int(digits[0].replace(",", ""))
    except ValueError:
        return None


def posted_age_days(text: str) -> int | None:
    """Turn '3 weeks ago' / 'Reposted 2 days ago' into a day count."""
    if not text:
        return None
    match = AGO_RE.search(text)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    return amount * _UNIT_SCALE.get(unit, 1)


def parse_company_size(text: str) -> tuple[int | None, int | None]:
    """'10,001+ employees' -> (10001, None); '51-200 employees' -> (51, 200)."""
    if not text:
        return None, None
    numbers = [to_int(n) for n in NUMBER_RE.findall(text)]
    numbers = [n for n in numbers if n is not None]
    if not numbers:
        return None, None
    start = numbers[0]
    end = numbers[1] if len(numbers) > 1 else None
    if "+" in text and end is None:
        return start, None
    return start, end


# Stage 1: search result cards
def parse_job_cards(html: str) -> list[dict]:
    """Parse the ``seeMoreJobPostings/search`` fragment into job dicts."""
    soup = _soup(html or "")
    jobs: list[dict] = []

    for card in soup.select(CARD_ITEM):
        link = card.select_one(CARD_LINK)
        title = _text(card.select_one(CARD_TITLE))
        if not link and not title:
            continue

        href = _attr(link, "href")
        raw_id = _attr(card, "data-occludable-job-id") or _attr(card, "data-job-id")
        job_id = extract_linkedin_id(raw_id, href, _attr(card, "data-entity-urn"))
        if not job_id:
            continue
        if not title:
            title = _attr(link, "aria-label").replace("View job: ", "").strip()

        company_link = card.select_one(CARD_COMPANY_LINK)
        company_url = _abs(_attr(company_link, "href")).split("?")[0]
        card_text = _text(card)

        benefits: list[str] = []
        badge = card.select_one("span.job-posting-benefits__text, span[class*='benefits']")
        if badge:
            benefits = [b.strip() for b in _text(badge).split("+") if b.strip()]

        posted_text = _text(card.select_one(CARD_TIME))

        jobs.append(
            {
                "linkedin_id": job_id,
                "job_title": title,
                "company_name": _text(card.select_one(CARD_COMPANY)),
                "company_slug": extract_company_slug(company_url),
                "company_url": company_url,
                "location": _text(card.select_one(CARD_LOCATION)),
                "posted_ago_text": posted_text,
                "posted_ago_days": posted_age_days(posted_text),
                "actively_hiring": "actively hiring" in card_text.lower(),
                "early_applicant": "early applicant" in card_text.lower(),
                "benefits": benefits,
                "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
                "raw_card_html": str(card),
            }
        )

    return jobs


# Stage 2a: job details
def parse_job_detail(html: str) -> dict:
    """Parse the ``jobPosting/<id>`` fragment."""
    soup = _soup(html or "")

    description_node = soup.select_one(DETAIL_DESCRIPTION)
    if description_node is not None:
        description = description_node.get_text("\n", strip=True)
    else:  # older/alternate template: whole core section
        fallback = soup.select_one("div.description__text")
        description = fallback.get_text("\n", strip=True) if fallback is not None else ""

    criteria: dict[str, str] = {}
    for item in soup.select(DETAIL_CRITERIA_ITEM):
        header = _text(item.select_one("h3"))
        value = _text(item.select_one("span"))
        if header and value:
            criteria[header.lower()] = value

    page_text = _text(soup)
    applicants = APPLICANTS_RE.search(page_text)
    salary = SALARY_RE.search(description or "")

    apply_link = soup.select_one(DETAIL_APPLY)
    org_link = soup.select_one(DETAIL_COMPANY_LINK)

    similar: list[dict] = []
    for item in soup.select("section.similar-jobs li, li.jobs-similar-card"):
        link = item.select_one("a")
        title = _text(item.select_one("h3")) or _attr(link, "aria-label")
        if title:
            similar.append({"title": title, "url": _abs(_attr(link, "href")).split("?")[0]})

    return {
        "description": description.strip(),
        "seniority_level": criteria.get("seniority level", ""),
        "employment_type": criteria.get("employment type", ""),
        "job_function": criteria.get("job function", ""),
        "industries": criteria.get("industries", ""),
        "applicants_count": to_int(applicants.group(1)) if applicants else None,
        "salary_text": (salary.group(0)[:300] if salary else ""),
        "apply_url": _abs(_attr(apply_link, "href")),
        "company_url": _abs(_attr(org_link, "href")).split("?")[0],
        "similar_jobs": similar[:10],
        "raw_detail_html": str(soup),
    }


# Stage 2b: company page
def _criteria_pairs(soup) -> dict[str, str]:
    """LinkedIn renders company facts as <dl><dt>label</dt><dd>value</dd>."""
    pairs: dict[str, str] = {}
    for dt in soup.select("dl dt"):
        dd = dt.find_next_sibling("dd")
        label = _text(dt).lower().rstrip(":")
        value = _text(dd) if dd is not None else _text(dt.find_next("dd"))
        if label and value:
            pairs.setdefault(label, value)
    return pairs


def parse_company(html: str) -> dict:
    """Parse a public company page into structured data."""
    soup = _soup(html or "")
    page_text = _text(soup)
    pairs = _criteria_pairs(soup)

    about_node = soup.select_one(COMPANY_ABOUT)
    if about_node is not None:
        about = _text(about_node)
    else:
        heading = soup.find(lambda tag: tag.name in {"h2", "h3"} and "about us" in _text(tag).lower())
        about = ""
        if heading is not None:
            for sibling in heading.find_all_next():
                if sibling.name in {"h2", "h3"} and "about us" not in _text(sibling).lower():
                    break
                about = _text(sibling)
                if about:
                    break

    website = ""
    for link in soup.select("a[href*='linkedin.com/redir/redirect'], a[data-test-id='about-website']"):
        href = _attr(link, "href")
        match = re.search(r"[?&]url=([^&]+)", href)
        if match:
            from urllib.parse import unquote

            website = unquote(match.group(1))
            break
    if not website:
        website = pairs.get("website", "")

    # Fallback regexes over the flattened text (robust to class renames).
    def grab(label: str, pattern: str) -> str:
        if label in pairs:
            return pairs[label]
        match = re.search(pattern, page_text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    industry = grab("industry", r"Industry\s+(.+?)\s+(?:Company size|Headquarters|Type|Founded|Specialties|Website)")
    company_size = grab("company size", r"Company size\s+([\d,]+\+?\s*(?:-|–)?\s*[\d,]*\+?\s*employees)")
    headquarters = grab("headquarters", r"Headquarters\s+(.+?)\s+(?:Type|Founded|Specialties|Website|Company size)")
    company_type = grab("type", r"\bType\s+(Public Company|Privately Held|Partnership|Self-Employed|Self-employed|Nonprofit|Educational|Government Agency|Sole Proprietorship|Privately Held)")
    founded = grab("founded", r"Founded\s+(\d{4})")
    specialties = grab("specialties", r"Specialties\s+(.+?)(?:\s+Website|\s+Company size|$)")

    followers = ""
    follower_match = re.search(r"([\d,.]+[KkMm]?)\s+followers", page_text)
    if follower_match:
        followers = follower_match.group(1)

    size_start, size_end = parse_company_size(company_size)

    return {
        "name": _text(soup.select_one(COMPANY_TITLE)),
        "tagline": _text(soup.select_one(COMPANY_TAGLINE)),
        "about": about,
        "website": website,
        "industry": industry,
        "company_size": company_size,
        "employee_range_start": size_start,
        "employee_range_end": size_end,
        "headquarters": headquarters,
        "company_type": company_type,
        "founded": int(founded) if founded.isdigit() else None,
        "specialties": [s.strip() for s in re.split(r",|\band\b", specialties) if s.strip()],
        "follower_count": parse_followers(followers),
        "raw_html": str(soup),
    }


def parse_followers(text: str) -> int | None:
    """'298,424' / '1.2M' / '15K' -> int."""
    if not text:
        return None
    text = text.strip().replace(",", "")
    multiplier = 1
    if text[-1:].lower() == "k":
        multiplier, text = 1_000, text[:-1]
    elif text[-1:].lower() == "m":
        multiplier, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None
