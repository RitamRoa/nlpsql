"""Multilingual query translation layer for NLPSQL.
Detects non-English user queries and automatically translates them into English
so the schema retrieval, domain knowledge, and Text-to-SQL generation pipeline
operate with maximum accuracy across all languages.
"""

import json
import logging
import re
import urllib.parse
import urllib.request
from functools import lru_cache

logger = logging.getLogger(__name__)

# Common English SQL query words to avoid unnecessary network calls
ENGLISH_INDICATORS = {
    "what", "which", "who", "where", "how", "show", "list", "find", "get",
    "select", "count", "average", "avg", "total", "sum", "max", "min", "top",
    "all", "each", "every", "are", "is", "have", "with", "from", "between",
    "more", "than", "less", "order", "group", "by", "active", "salary", "department"
}


def is_likely_english(text: str) -> bool:
    """Heuristic check: returns True if text is plainly ASCII English."""
    # If text has non-ASCII characters (e.g., Devanagari, Cyrillic, Chinese, accented letters)
    if any(ord(char) > 127 for char in text):
        return False
    words = set(re.findall(r"\b\w+\b", text.lower()))
    matches = words.intersection(ENGLISH_INDICATORS)
    # If at least 2 distinct English query keywords are present and ratio is high
    if len(matches) >= 2 or (len(words) <= 3 and len(matches) >= 1):
        return True
    return False


# Offline fallback phrase and keyword mapping for resilient offline operation
OFFLINE_PATTERNS = [
    # Spanish
    (r"\blista(?:r)?\b", "list"),
    (r"\bmuestra\b", "show"),
    (r"\bmostrar\b", "show"),
    (r"\btodos\b", "all"),
    (r"\btodas\b", "all"),
    (r"\blos\b", ""),
    (r"\blas\b", ""),
    (r"\bempleados\b", "employees"),
    (r"\bempleado\b", "employee"),
    (r"\bdepartamentos?\b", "department"),
    (r"\bsalarios?\b", "salary"),
    (r"\bsueldos?\b", "salary"),
    (r"\bmayor\s+a(?:l)?\b", "greater than"),
    (r"\bmenor\s+a(?:l)?\b", "less than"),
    (r"\bcon\b", "with"),
    (r"\bproyectos?\b", "projects"),
    (r"\bventas\b", "sales"),
    (r"\bclientes\b", "customers"),
    (r"\bpedidos\b", "orders"),
    (r"\bproductos?\b", "products"),
    (r"\bestudiantes?\b", "students"),
    (r"\bpacientes?\b", "patients"),
    (r"\bm[eé]dicos?\b", "doctors"),
    (r"\bdoctores\b", "doctors"),
    # French
    (r"\bmontre(?:r)?\b", "show"),
    (r"\btous\b", "all"),
    (r"\btoutes\b", "all"),
    (r"\bles\b", ""),
    (r"\bemploy[eé]s?\b", "employees"),
    (r"\bd[eé]partements?\b", "department"),
    (r"\bsalaires?\b", "salary"),
    # German
    (r"\bzeige(?:n)?\b", "show"),
    (r"\balle\b", "all"),
    (r"\bmitarbeiter\b", "employees"),
    (r"\babteilung(?:en)?\b", "department"),
    (r"\bgehalt(?:er)?\b", "salary"),
]


def offline_translate(text: str) -> tuple[str, bool]:
    """Applies rule-based keyword translation when network connection is down."""
    result = text
    modified = False
    for pattern, replacement in OFFLINE_PATTERNS:
        new_res, count = re.subn(pattern, replacement, result, flags=re.IGNORECASE)
        if count > 0:
            result = new_res
            modified = True
    # Clean up excess spaces
    cleaned = re.sub(r"\s+", " ", result).strip()
    return cleaned, modified


@lru_cache(maxsize=256)
def translate_query(text: str, timeout_seconds: float = 4.0) -> tuple[str, bool, str]:
    """Translates a natural language query to English if needed.

    Returns:
        tuple of (english_query, was_translated, source_language)
    """
    clean_text = text.strip()
    if not clean_text:
        return text, False, "en"

    # Fast-path for clear English queries
    if is_likely_english(clean_text):
        return clean_text, False, "en"

    try:
        query_params = urllib.parse.urlencode({
            "q": clean_text,
            "langpair": "autodetect|en",
        })
        url = f"https://api.mymemory.translated.net/get?{query_params}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "NLPSQL-Translator/1.0 (Mozilla/5.0)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translated = data.get("responseData", {}).get("translatedText")

            # If translation succeeded and is distinct from original
            if translated and translated.strip().lower() != clean_text.lower():
                return translated.strip(), True, "auto"
    except Exception as e:
        logger.warning("Online translation unavailable (%s). Using offline fallback.", e)
        offline_res, was_off = offline_translate(clean_text)
        if was_off:
            return offline_res, True, "offline_fallback"

    return clean_text, False, "en"
