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
    "m.music.youtube.com",
    "youtu.be",
    "www.youtu.be"
}

# YouTube Music Specific Hostnames
YOUTUBE_MUSIC_DOMAINS = {
    "music.youtube.com",
    "m.music.youtube.com"
}

# Twitter / X Hostnames & Pattern
TWITTER_DOMAINS = {
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "x.com",
    "www.x.com"
}
TWITTER_STATUS_PATTERN = re.compile(r"^/([a-zA-Z0-9_]+)/status/([0-9]+)")

# Instagram Hostnames & Patterns
INSTAGRAM_DOMAINS = {
    "instagram.com",
    "www.instagram.com",
    "ddinstagram.com",
    "instagr.am"
}
INSTAGRAM_POST_PATTERN = re.compile(r"^/(p|reel|reels|tv)/([a-zA-Z0-9_-]+)")
INSTAGRAM_HIGHLIGHT_PATTERN = re.compile(r"^/(?:stories/highlights/([0-9]+)|s/([a-zA-Z0-9_-]+))")
INSTAGRAM_STORY_PATTERN = re.compile(r"^/stories/([a-zA-Z0-9_.-]+)(?:/([0-9]+))?")
INSTAGRAM_PROFILE_PATTERN = re.compile(r"^/([a-zA-Z0-9_.-]{1,30})/?$")

INSTAGRAM_RESERVED_ROUTES = {
    "", "p", "reel", "reels", "tv", "stories", "explore", "accounts",
    "direct", "developer", "about", "legal", "terms", "privacy", "help",
    "api", "static", "directory", "settings"
}

# Dangerous characters in filenames
FORBIDDEN_FILENAME_CHARS = re.compile(r'[/\\?%*:|"<>^\x00-\x1f]')


def is_safe_url(url: str) -> bool:
    """SSRF Prevention: Ensure URL is public, uses HTTP/HTTPS, and not pointing to private or AWS internal networks."""
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Reject localhost, local names, and AWS IMDS metadata service
        if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"):
            return False

        # Check if IP address is private/reserved/link-local
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
        if parsed.path in ("/watch", "/watch/"):
            query_params = parse_qs(parsed.query)
            v_param = query_params.get("v")
            if v_param and YOUTUBE_ID_PATTERN.match(v_param[0]):
                return v_param[0]
            return None

        # youtube.com/shorts/<video_id> or /v/<video_id> or /embed/<video_id> or /track/<video_id>
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2 and path_parts[0] in ("shorts", "v", "embed", "track"):
            if YOUTUBE_ID_PATTERN.match(path_parts[1]):
                return path_parts[1]

        return None
    except Exception:
        return None


def is_valid_youtube_url(url: str) -> bool:
    """Return True only if URL is a strictly valid YouTube or YouTube Music video URL."""
    return extract_youtube_id(url) is not None


def is_youtube_music_url(url: str) -> bool:
    """Return True if the URL points specifically to YouTube Music with a valid video ID."""
    if not url or not is_safe_url(url):
        return False
    try:
        parsed = urlparse(url.strip())
        domain = (parsed.netloc or "").lower().split(":")[0]
        return domain in YOUTUBE_MUSIC_DOMAINS and extract_youtube_id(url) is not None
    except Exception:
        return False


def extract_twitter_info(url: str) -> Optional[Tuple[str, str]]:
    """Extract (username, tweet_id) from Twitter / X status URLs."""
    if not url or not is_safe_url(url):
        return None
    try:
        parsed = urlparse(url.strip())
        domain = (parsed.netloc or "").lower().split(":")[0]
        if domain not in TWITTER_DOMAINS:
            return None
        match = TWITTER_STATUS_PATTERN.match(parsed.path)
        if match:
            return match.group(1), match.group(2)
        return None
    except Exception:
        return None


def is_valid_twitter_url(url: str) -> bool:
    """Return True if the URL is a valid Twitter / X status URL."""
    return extract_twitter_info(url) is not None


def extract_instagram_info(url: str) -> Optional[Tuple[str, str]]:
    """
    Extract (category, identifier) from Instagram URLs.
    Supports:
    - Posts & Reels: ('reel'|'p'|'tv', shortcode)
    - Stories: ('story', story_id_or_user)
    - Highlights: ('highlight', highlight_id)
    - Profiles: ('profile', username)
    """
    if not url or not is_safe_url(url):
        return None
    try:
        parsed = urlparse(url.strip())
        domain = (parsed.netloc or "").lower().split(":")[0]
        if domain not in INSTAGRAM_DOMAINS:
            return None

        path = parsed.path.rstrip("/")
        if not path:
            return None

        # 1. Check Posts / Reels / TV
        post_match = INSTAGRAM_POST_PATTERN.match(parsed.path)
        if post_match:
            media_type = post_match.group(1).rstrip("s")
            return media_type, post_match.group(2)

        # 2. Check Highlights
        hl_match = INSTAGRAM_HIGHLIGHT_PATTERN.match(parsed.path)
        if hl_match:
            hl_id = hl_match.group(1) or hl_match.group(2)
            return "highlight", hl_id

        # 3. Check Stories
        story_match = INSTAGRAM_STORY_PATTERN.match(parsed.path)
        if story_match:
            user = story_match.group(1)
            story_id = story_match.group(2) or user
            return "story", story_id

        # 4. Check Profile
        prof_match = INSTAGRAM_PROFILE_PATTERN.match(parsed.path)
        if prof_match:
            username = prof_match.group(1)
            if username.lower() not in INSTAGRAM_RESERVED_ROUTES:
                return "profile", username

        return None
    except Exception:
        return None


def parse_instagram_input(input_str: str) -> Optional[Tuple[str, str]]:
    """Parse either an Instagram URL or an @username/username handle."""
    if not input_str:
        return None
    clean = input_str.strip()
    if clean.startswith("@"):
        uname = clean[1:].strip()
        if re.match(r"^[a-zA-Z0-9_.-]{1,30}$", uname) and uname.lower() not in INSTAGRAM_RESERVED_ROUTES:
            return "profile", uname
        return None

    if "://" in clean or any(d in clean.lower() for d in INSTAGRAM_DOMAINS):
        return extract_instagram_info(clean)

    # Bare username fallback (e.g. 'cristiano')
    if re.match(r"^[a-zA-Z0-9_.-]{1,30}$", clean) and clean.lower() not in INSTAGRAM_RESERVED_ROUTES:
        return "profile", clean

    return None


def is_valid_instagram_url(url: str) -> bool:
    """Return True if the URL is a valid Instagram post, reel, story, highlight, or profile URL."""
    return extract_instagram_info(url) is not None


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
    if MAX_DURATION_SECONDS > 0 and duration_seconds is not None and duration_seconds > MAX_DURATION_SECONDS:
        max_mins = MAX_DURATION_SECONDS // 60
        curr_mins = duration_seconds // 60
        return False, f"Media is too long ({curr_mins} mins). Maximum allowed duration is {max_mins} minutes."

    if filesize_bytes is not None and filesize_bytes > MAX_UPLOAD_SIZE_BYTES:
        max_mb = MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        curr_mb = filesize_bytes / (1024 * 1024)
        return False, f"Estimated file size ({curr_mb:.1f} MB) exceeds maximum upload limit of {max_mb} MB."

    return True, None


def classify_media_restriction(error_text: Optional[str]) -> Optional[Tuple[str, str]]:
    """
    Classify restricted media errors (Members-only, Private, Age-restricted, Live/Premiere)
    across YouTube, Instagram, and X (Twitter) into friendly warning messages.

    Returns:
        Tuple of (category_id, html_formatted_warning) or None if not a recognized restriction.
    """
    if not error_text:
        return None

    err = str(error_text).lower()

    # 1. Members-Only / Subscriber-Only Category
    members_keywords = [
        "member",
        "members-only",
        "members only",
        "members on level",
        "join this channel",
        "channel membership",
        "subscriber-only",
        "subscribers only",
        "exclusive to subscribers",
        "close friends",
        "sponsorship"
    ]
    if any(k in err for k in members_keywords):
        notice = (
            "🔒 <b>Access Restricted: Members-Only Category</b>\n\n"
            "⚠️ This content belongs to a <b>Members-Only</b> or <b>Subscriber-Only</b> category.\n\n"
            "<i>We cannot access or download this media because it requires an active paid channel membership or subscription that cannot be accessed.</i>"
        )
        return "members_only", notice

    # 2. Private Account / Private Video Category
    private_keywords = [
        "private video",
        "this video is private",
        "private account",
        "account is private",
        "this account is private",
        "only available for registered users who follow this account",
        "only available for registered users",
        "tweets are protected",
        "protected tweet",
        "account is protected"
    ]
    if any(k in err for k in private_keywords):
        notice = (
            "🔒 <b>Access Restricted: Private Category</b>\n\n"
            "⚠️ This media belongs to a <b>Private</b> account or video that cannot be accessed.\n\n"
            "<i>Private content is restricted by the owner and cannot be downloaded without authorized personal access.</i>"
        )
        return "private", notice

    # 3. Age-Restricted Content
    age_keywords = [
        "confirm your age",
        "age-restricted",
        "age restricted",
        "inappropriate for some users",
        "age_verification_required",
        "age_check_required"
    ]
    if any(k in err for k in age_keywords):
        notice = (
            "🔞 <b>Access Restricted: Age-Gated Content</b>\n\n"
            "⚠️ This video is <b>Age-Restricted</b> by the platform.\n\n"
            "<i>Anonymous playback is restricted for age-gated videos and cannot be bypassed.</i>"
        )
        return "age_restricted", notice

    # 4. Live Stream / Upcoming Premiere
    live_keywords = [
        "live event will begin",
        "premiere will begin",
        "is currently live",
        "waiting for live stream",
        "offline live"
    ]
    if any(k in err for k in live_keywords):
        notice = (
            "⏳ <b>Access Notice: Live Event / Upcoming Premiere</b>\n\n"
            "⚠️ This media is an ongoing live stream or an upcoming premiere.\n\n"
            "<i>Please wait until the broadcast ends and archives as a regular video before downloading.</i>"
        )
        return "live_premiere", notice

    return None

