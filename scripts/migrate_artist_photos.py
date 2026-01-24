#!/usr/bin/env python3
"""
Migration script to populate artist_photo_url for existing smartlinks.

Fetches all smartlinks with Yandex links, parses the artist photo from Yandex,
and updates the records in the index.

Usage:
    python scripts/migrate_artist_photos.py [--dry-run] [--limit N]

Options:
    --dry-run   Don't actually update records, just show what would be done
    --limit N   Only process the first N records (for testing)
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

from helpers import fetch_yandex_artist_photo, normalize_base_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def fetch_smartlinks_needing_migration(session: aiohttp.ClientSession, base_url: str, api_key: str) -> list[dict]:
    """Fetch smartlinks that need artist photo migration."""
    url = f"{base_url}/api/admin/migrate-photos"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error("Failed to fetch migration list: status=%s body=%s", resp.status, body[:200])
                return []
            data = await resp.json()
            if not data.get("ok"):
                logger.error("Migration endpoint returned error: %s", data)
                return []
            return data.get("items", [])
    except Exception as e:
        logger.error("Error fetching smartlinks: %s", e)
        return []


async def update_smartlink(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    smartlink: dict,
    artist_photo_url: str,
    dry_run: bool = False,
) -> bool:
    """Update a smartlink with the artist photo URL."""
    artist_slug = smartlink.get("artist_slug", "")
    slug = smartlink.get("slug", "")
    
    if not artist_slug or not slug:
        logger.warning("Missing slugs for smartlink: %s", smartlink.get("id"))
        return False
    
    if dry_run:
        logger.info("[DRY-RUN] Would update %s/%s with photo: %s", artist_slug, slug, artist_photo_url[:80])
        return True
    
    url = f"{base_url}/api/index/upsert"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Skip-Sync": "1",
    }
    
    # Build minimal payload for update
    payload = {
        "artist_slug": artist_slug,
        "slug": slug,
        "title": smartlink.get("title", ""),
        "artist_name": smartlink.get("artist_name", ""),
        "links": smartlink.get("links", {}),
        "artist_photo_url": artist_photo_url,
    }
    
    try:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status >= 200 and resp.status < 300:
                logger.info("Updated %s/%s with photo", artist_slug, slug)
                return True
            else:
                body = await resp.text()
                logger.error("Failed to update %s/%s: status=%s body=%s", artist_slug, slug, resp.status, body[:200])
                return False
    except Exception as e:
        logger.error("Error updating %s/%s: %s", artist_slug, slug, e)
        return False


async def main():
    parser = argparse.ArgumentParser(description="Migrate artist photos for existing smartlinks")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually update records")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of records to process")
    args = parser.parse_args()
    
    # Get configuration from environment
    base_url = normalize_base_url(os.getenv("SMARTLINK_INDEX_BASE") or os.getenv("GO_INDEX_BASE"), None)
    api_key = (
        os.getenv("SMARTLINK_API_KEY")
        or os.getenv("GO_API_KEY")
        or os.getenv("GO_API_TOKEN")
        or os.getenv("SMARTLINK_INDEX_TOKEN")
    )
    
    if not base_url:
        logger.error("SMARTLINK_INDEX_BASE not configured")
        sys.exit(1)
    
    if not api_key:
        logger.error("SMARTLINK_API_KEY not configured")
        sys.exit(1)
    
    logger.info("Starting migration...")
    logger.info("Base URL: %s", base_url)
    logger.info("Dry run: %s", args.dry_run)
    if args.limit:
        logger.info("Limit: %s", args.limit)
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Fetch smartlinks needing migration (already filtered by server)
        smartlinks = await fetch_smartlinks_needing_migration(session, base_url, api_key)
        logger.info("Found %d smartlinks needing artist photo migration", len(smartlinks))
        
        to_update = []
        for sl in smartlinks:
            links = sl.get("links", {})
            yandex_url = links.get("yandex", "")
            if yandex_url:
                to_update.append((sl, yandex_url))
        
        if args.limit:
            to_update = to_update[:args.limit]
            logger.info("Processing %d (limited)", len(to_update))
        
        # Process each smartlink
        updated = 0
        failed = 0
        skipped = 0
        
        for i, (sl, yandex_url) in enumerate(to_update, 1):
            artist_slug = sl.get("artist_slug", "")
            slug = sl.get("slug", "")
            logger.info("[%d/%d] Processing %s/%s...", i, len(to_update), artist_slug, slug)
            
            # Fetch artist photo
            try:
                photo_url = await fetch_yandex_artist_photo(yandex_url)
            except Exception as e:
                logger.warning("Failed to fetch photo for %s/%s: %s", artist_slug, slug, e)
                photo_url = None
            
            if not photo_url:
                logger.info("No photo found for %s/%s", artist_slug, slug)
                skipped += 1
                continue
            
            # Update the smartlink
            success = await update_smartlink(session, base_url, api_key, sl, photo_url, dry_run=args.dry_run)
            if success:
                updated += 1
            else:
                failed += 1
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
        
        logger.info("=" * 50)
        logger.info("Migration complete!")
        logger.info("Updated: %d", updated)
        logger.info("Skipped (no photo found): %d", skipped)
        logger.info("Failed: %d", failed)


if __name__ == "__main__":
    asyncio.run(main())
