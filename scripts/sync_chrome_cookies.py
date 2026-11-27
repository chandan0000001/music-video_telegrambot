"""
Synchronize live YouTube/Google session cookies from local Chrome profile into cookies.txt.
Useful on EC2/Linux servers where Chrome is logged into Google/YouTube so yt-dlp
always receives complete, fresh authentication cookies (including .google.com tokens).
"""
import os
import sys
import http.cookiejar
from pathlib import Path


def sync_chrome_cookies(browser: str = "chrome", output_path: Path | None = None) -> int:
    """Extract cookies from browser SQLite profile and write Netscape cookies.txt (0600)."""
    from yt_dlp.cookies import extract_cookies_from_browser

    target = output_path or (Path(__file__).resolve().parent.parent / "cookies.txt")
    cj = extract_cookies_from_browser(browser)
    netscape_cj = http.cookiejar.MozillaCookieJar(str(target))
    count = 0
    for cookie in cj:
        netscape_cj.set_cookie(cookie)
        count += 1
    netscape_cj.save(ignore_discard=True, ignore_expires=True)
    try:
        os.chmod(target, 0o600)
    except Exception:
        pass
    return count


if __name__ == "__main__":
    browser_name = sys.argv[1] if len(sys.argv) > 1 else "chrome"
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    try:
        total = sync_chrome_cookies(browser=browser_name, output_path=dest)
        print(f"Synced {total} cookies from {browser_name} -> {dest or (Path(__file__).resolve().parent.parent / 'cookies.txt')}")
    except Exception as exc:
        print(f"Cookie sync failed: {exc}", file=sys.stderr)
        sys.exit(1)
