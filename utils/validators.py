"""
Input Validation and Security Filters
Ensures safe handling of URLs, user inputs, and filenames.
"""
import re
import ipaddress
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple

from config import MAX_DURATION_SECONDS, MAX_UPLOAD_SIZE_BYTES

# Strict YouTube Video ID Regex (11 chars: alphanumeric, dash, underscore)
YOUTUBE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# YouTube Hostnames
ALLOWED_YOUTUBE_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be"
}

# Dangerous characters in filenames
FORBIDDEN_FILENAME_CHARS = re.compile(r'[/\\?%*:|"<>^\x00-\x1f]')


def is_safe_url(url: str) -> bool:
    """SSRF Prevention: Ensure URL is public, uses HTTP/HTTPS, and not pointing to private networks."""
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Reject localhost and local names
        if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return False

        # Check if IP address is private/reserved
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return False
        except ValueError:
            pass  # It's a standard domain name, proceed

        return True
    except Exception:
        return False


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube 11-character video ID from allowed YouTube URL variants."""
    if not url or not is_safe_url(url):
        return None

    try:
        parsed = urlparse(url.strip())
        domain = parsed.netloc.lower()

        # Remove port if present
        if ":" in domain:
            domain = domain.split(":")[0]

        if domain not in ALLOWED_YOUTUBE_DOMAINS:
            return None

        # youtu.be/<video_id>
        if domain in ("youtu.be", "www.youtu.be"):
            path_parts = parsed.path.strip("/").split("/")
            if path_parts and YOUTUBE_ID_PATTERN.match(path_parts[0]):
                return path_parts[0]
            return None

        # youtube.com/watch?v=<video_id>
        if parsed.path == "/watch":
            query_params = parse_qs(parsed.query)
            v_param = query_params.get("v")
            if v_param and YOUTUBE_ID_PATTERN.match(v_param[0]):
                return v_param[0]
            return None

        # youtube.com/shorts/<video_id> or /v/<video_id> or /embed/<video_id>
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2 and path_parts[0] in ("shorts", "v", "embed"):
            if YOUTUBE_ID_PATTERN.match(path_parts[1]):
                return path_parts[1]

        return None
    except Exception:
        return None


def is_valid_youtube_url(url: str) -> bool:
    """Return True only if URL is a strictly valid YouTube video or short URL."""
    return extract_youtube_id(url) is not None


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize filename to prevent directory traversal and remove problematic characters."""
    if not name:
        return "unnamed_media"

    # Replace path separators and forbidden characters with underscore
    clean = FORBIDDEN_FILENAME_CHARS.sub("_", name)
    # Prevent traversal
    clean = clean.replace("..", "_").strip(" ._")

    if not clean:
        clean = "media"

    return clean[:max_length]


def validate_media_limits(duration_seconds: Optional[int], filesize_bytes: Optional[int]) -> Tuple[bool, Optional[str]]:
    """Validate media duration and estimated filesize against configured limits."""
    if duration_seconds is not None and duration_seconds > MAX_DURATION_SECONDS:
        max_mins = MAX_DURATION_SECONDS // 60
        curr_mins = duration_seconds // 60
        return False, f"Media is too long ({curr_mins} mins). Maximum allowed duration is {max_mins} minutes."

    if filesize_bytes is not None and filesize_bytes > MAX_UPLOAD_SIZE_BYTES:
        max_mb = MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        curr_mb = filesize_bytes / (1024 * 1024)
        return False, f"Estimated file size ({curr_mb:.1f} MB) exceeds maximum upload limit of {max_mb} MB."

    return True, None
