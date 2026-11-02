-- ==========================================================
-- High-Performance Optimized Database Schema
-- Architecture: SQLite 3 with Write-Ahead Logging (WAL) Mode
-- ==========================================================

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_blocked BOOLEAN DEFAULT 0 CHECK (is_blocked IN (0, 1))
);

-- 2. Search History Table
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    search_query TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'youtube',
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- 3. Downloads Table
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    media_title TEXT NOT NULL,
    source TEXT NOT NULL,
    media_type TEXT NOT NULL,
    selected_quality TEXT NOT NULL,
    selected_format TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- 4. Bot Statistics Table (Singleton metrics cache)
CREATE TABLE IF NOT EXISTS bot_statistics (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_users INTEGER DEFAULT 0,
    total_searches INTEGER DEFAULT 0,
    total_successful_downloads INTEGER DEFAULT 0,
    total_failed_downloads INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Orders / Payments Table (Tracks individual orders and 12-digit UTRs)
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    telegram_id INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    amount_inr REAL NOT NULL CHECK (amount_inr >= 0),
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'expired', 'failed', 'cancelled', 'rejected')),
    media_id TEXT,
    media_title TEXT,
    quality TEXT,
    utr_number TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- 6. User Subscriptions Table (Tracks active passes & daily quotas)
CREATE TABLE IF NOT EXISTS user_subscriptions (
    telegram_id INTEGER PRIMARY KEY,
    plan_tier TEXT NOT NULL CHECK (plan_tier IN ('normal', 'go', 'pro', 'ultra', 'max', 'weekly', 'monthly')),
    start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expiry_date TIMESTAMP NOT NULL,
    daily_video_limit INTEGER NOT NULL CHECK (daily_video_limit >= 0),
    today_video_used INTEGER DEFAULT 0 CHECK (today_video_used >= 0),
    last_reset_date TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- 7. Coupons Table (Admin-generated promo/discount/VIP codes)
CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    plan_tier TEXT NOT NULL DEFAULT 'go',
    days INTEGER NOT NULL DEFAULT 7,
    max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    expires_at TIMESTAMP,
    created_by INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Coupon Redemptions Table (Tracks which user redeemed which coupon)
CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coupon_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(coupon_id, telegram_id),
    FOREIGN KEY (coupon_id) REFERENCES coupons(id) ON DELETE CASCADE,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- 9. Referrals Table (Tracks friends invited via personal deep-links)
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_id INTEGER NOT NULL,
    referred_id INTEGER UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (inviter_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
    FOREIGN KEY (referred_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- 10. Referral Rewards Table (Tracks milestone rewards granted to inviters)
CREATE TABLE IF NOT EXISTS referral_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    milestone_count INTEGER NOT NULL,
    reward_days INTEGER NOT NULL DEFAULT 7,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

-- Initialize singleton statistics row if not exists
INSERT OR IGNORE INTO bot_statistics (id, total_users, total_searches, total_successful_downloads, total_failed_downloads)
VALUES (1, 0, 0, 0, 0);

-- ==========================================================
-- Optimal Performance Indexes
-- Eliminates duplicate indices; optimizes sorting & multi-column queries
-- ==========================================================

-- Users
CREATE INDEX IF NOT EXISTS idx_users_joined_at ON users(joined_at DESC);

-- Search History (Composite index for user history & top query aggregations)
CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(telegram_id, searched_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_history_query ON search_history(search_query);

-- Downloads (Composite index for user history & status reporting)
CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(telegram_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status, created_at DESC);

-- Orders (Optimizes admin order lists, user lookups, and anti-fraud UTR verification)
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(telegram_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status_id ON orders(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_utr_status ON orders(utr_number, status);

-- Subscriptions (Optimizes active subscriber lookups)
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_expiry ON user_subscriptions(expiry_date);

-- Coupons (Optimizes promo code lookups and admin active listings)
CREATE INDEX IF NOT EXISTS idx_coupons_code ON coupons(code);
CREATE INDEX IF NOT EXISTS idx_coupons_active ON coupons(is_active, id DESC);

-- Referrals (Optimizes friend count aggregations and reward checks)
CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id);
