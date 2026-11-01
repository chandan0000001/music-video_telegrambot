#!/usr/bin/env bash
# ==============================================================================
# Launch Google Chrome on AWS via X11 for Instagram Login & Cookie Extraction
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "=========================================================="
echo "🌐 1. Launching Google Chrome on AWS..."
echo "👉 Please log into Instagram in the browser window that opens."
echo "👉 Once you are logged in and see your feed, CLOSE Chrome."
echo "=========================================================="

google-chrome --no-sandbox "https://www.instagram.com/accounts/login/" 2>/dev/null || \
google-chrome-stable --no-sandbox "https://www.instagram.com/accounts/login/" 2>/dev/null || \
chromium-browser --no-sandbox "https://www.instagram.com/accounts/login/" 2>/dev/null

echo ""
echo "📦 2. Chrome closed. Extracting AWS-native cookies to instagram_cookies.txt..."

yt-dlp --cookies-from-browser chrome --cookies instagram_cookies.txt --skip-download "https://www.instagram.com" 2>/dev/null || true

if [ ! -s "instagram_cookies.txt" ]; then
    yt-dlp --cookies-from-browser chromium --cookies instagram_cookies.txt --skip-download "https://www.instagram.com" 2>/dev/null || true
fi

if [ -f "instagram_cookies.txt" ] && [ -s "instagram_cookies.txt" ]; then
    echo "✅ Successfully created instagram_cookies.txt!"
    echo "🔄 3. Restarting musicbot.service..."
    sudo systemctl restart musicbot.service || true
    echo "🎉 All set! The bot is now running with authentic AWS Instagram cookies."
else
    echo "⚠️ Notice: Could not auto-export cookies directly."
    echo "💡 You can also copy your 'sessionid' cookie from Chrome DevTools (F12) into .env as:"
    echo "   INSTAGRAM_SESSION_ID=your_session_id"
    echo "   and run: sudo systemctl restart musicbot.service"
fi
