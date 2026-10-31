-- Users Table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_blocked BOOLEAN DEFAULT 0
);

-- Search History Table
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    search_query TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'youtube',
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- Downloads Table
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    media_title TEXT NOT NULL,
    source TEXT NOT NULL,
    media_type TEXT NOT NULL,       -- 'audio' or 'video'
    selected_quality TEXT NOT NULL, -- e.g., '320kbps' or '720p'
    selected_format TEXT NOT NULL,  -- e.g., 'mp3' or 'mp4'
    status TEXT NOT NULL,           -- 'pending', 'completed', 'failed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- Bot Statistics Table (cached totals / counters)
CREATE TABLE IF NOT EXISTS bot_statistics (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_users INTEGER DEFAULT 0,
    total_searches INTEGER DEFAULT 0,
    total_successful_downloads INTEGER DEFAULT 0,
    total_failed_downloads INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Initialize singleton statistics row if not exists
INSERT OR IGNORE INTO bot_statistics (id, total_users, total_searches, total_successful_downloads, total_failed_downloads)
VALUES (1, 0, 0, 0, 0);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_search_history_telegram_id ON search_history(telegram_id);
CREATE INDEX IF NOT EXISTS idx_search_history_searched_at ON search_history(searched_at);
CREATE INDEX IF NOT EXISTS idx_downloads_telegram_id ON downloads(telegram_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_created_at ON downloads(created_at);
