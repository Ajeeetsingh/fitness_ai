"""
YouTube Links Module for Workout Plans

This module provides functionality to add YouTube tutorial links to exercises
in workout plans. It uses a multi-tier approach:
1. Check cache (file-based, persistent)
2. Try YouTube Data API v3 (if API key provided)
3. Fallback to Google search scraping
4. Graceful degradation (return None if all methods fail)
"""

import os
import json
import re
import time
from typing import Dict, Optional, Any, List
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.log import logger

# Fallback database of common exercise YouTube links
# These are manually curated links that work as a backup when scraping fails
EXERCISE_LINKS_DATABASE = {
    "pushup": "https://www.youtube.com/watch?v=IODxDxX7oi4",
    "push-ups": "https://www.youtube.com/watch?v=IODxDxX7oi4",
    "pushups": "https://www.youtube.com/watch?v=IODxDxX7oi4",
    "squat": "https://www.youtube.com/watch?v=Dy28eq2PjcM",
    "squats": "https://www.youtube.com/watch?v=Dy28eq2PjcM",
    "pull-up": "https://www.youtube.com/watch?v=eGo4IYlbE5g",
    "pull-ups": "https://www.youtube.com/watch?v=eGo4IYlbE5g",
    "pullups": "https://www.youtube.com/watch?v=eGo4IYlbE5g",
    "pullup": "https://www.youtube.com/watch?v=eGo4IYlbE5g",
    "lunges": "https://www.youtube.com/watch?v=QOVaHwm-Q6U",
    "lunge": "https://www.youtube.com/watch?v=QOVaHwm-Q6U",
    "plank": "https://www.youtube.com/watch?v=pSHjTRCQxIw",
    "burpee": "https://www.youtube.com/watch?v=auBLPXO8Fww",
    "burpees": "https://www.youtube.com/watch?v=auBLPXO8Fww",
    "bicep curl": "https://www.youtube.com/watch?v=ykJmrZ5v0Oo",
    "bicepcurls": "https://www.youtube.com/watch?v=ykJmrZ5v0Oo",
    "bicepcurls": "https://www.youtube.com/watch?v=ykJmrZ5v0Oo",
    "rowing": "https://www.youtube.com/watch?v=TEQ7QvXgZqY",
    "row": "https://www.youtube.com/watch?v=TEQ7QvXgZqY",
    "deadlift": "https://www.youtube.com/watch?v=op9kVnSso6Q",
    "deadlifts": "https://www.youtube.com/watch?v=op9kVnSso6Q",
    "bench press": "https://www.youtube.com/watch?v=rT7DgCr-3pg",
    "benchpress": "https://www.youtube.com/watch?v=rT7DgCr-3pg",
    "overhead press": "https://www.youtube.com/watch?v=qEwKCR5JCog",
    "shoulder press": "https://www.youtube.com/watch?v=qEwKCR5JCog",
    "crunches": "https://www.youtube.com/watch?v=MKmrqcoCZ-M",
    "crunch": "https://www.youtube.com/watch?v=MKmrqcoCZ-M",
    "jump rope": "https://www.youtube.com/watch?v=1BZM2Qre9xs",
    "jumping jacks": "https://www.youtube.com/watch?v=UpH7rm0cYbM",
    "arm circles": "https://www.youtube.com/watch?v=U0wpJj3qoI8",
    "stretching": "https://www.youtube.com/watch?v=4pKly2JojMw",
    "static stretching": "https://www.youtube.com/watch?v=4pKly2JojMw",
    "leg stretch": "https://www.youtube.com/watch?v=7YHj5Vb8QqM",
    "shoulder stretch": "https://www.youtube.com/watch?v=KZc3x6b1lqE",
}


# Cache file path
CACHE_PATH = getattr(settings, 'YOUTUBE_CACHE_PATH', os.path.join(settings.STORAGE_DIR, "youtube_links_cache.json"))
CACHE_TTL_DAYS = getattr(settings, 'YOUTUBE_CACHE_TTL_DAYS', 30)

# In-memory cache (loaded from file)
_cache: Optional[Dict[str, Any]] = None


def load_cache() -> Dict[str, Any]:
    """Load cache from JSON file. Returns empty dict if file doesn't exist."""
    global _cache
    if _cache is not None:
        return _cache
    
    _cache = {
        "links": {},
        "timestamp": None
    }
    
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _cache["links"] = data.get("links", {})
                _cache["timestamp"] = data.get("timestamp")
        except Exception as e:
            logger.warning(f"Failed to load YouTube cache: {e}")
            _cache = {"links": {}, "timestamp": None}
    
    return _cache


def save_cache(cache: Dict[str, Any]) -> None:
    """Save cache to JSON file."""
    global _cache
    _cache = cache
    
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save YouTube cache: {e}")


def get_cached_link(exercise_name: str, cache: Dict[str, Any]) -> Optional[str]:
    """Check if exercise link is cached and still valid."""
    links = cache.get("links", {})
    if exercise_name.lower() in links:
        return links[exercise_name.lower()]
    return None


def validate_youtube_link(url: str) -> bool:
    """
    Validate if a YouTube video exists and is accessible.
    Uses YouTube's oEmbed API (free, no API key needed).
    
    Args:
        url: YouTube URL to validate
        
    Returns:
        True if video exists and is accessible, False otherwise
    """
    try:
        # Extract video ID from URL
        video_id_match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        if not video_id_match:
            return False
        
        video_id = video_id_match.group(1)
        
        # Use YouTube oEmbed API (free, no key needed)
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        
        timeout = httpx.Timeout(5.0)
        with httpx.Client(timeout=timeout) as client:
            response = client.get(oembed_url)
            # 200 = video exists, 404 = video doesn't exist or is private
            if response.status_code == 200:
                return True
            return False
    except httpx.TimeoutException:
        logger.debug(f"Timeout validating YouTube link {url}")
        return False  # Assume invalid if timeout
    except Exception as e:
        logger.debug(f"Error validating YouTube link {url}: {e}")
        return False  # Assume invalid if we can't check


def get_youtube_link_api(exercise_name: str, api_key: str) -> Optional[str]:
    """
    Try YouTube Data API v3 to get tutorial link.
    
    Args:
        exercise_name: Name of the exercise
        api_key: YouTube Data API v3 key
        
    Returns:
        YouTube URL or None if fails
    """
    try:
        query = f"{exercise_name} tutorial proper form how to"
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 1,
            "key": api_key,
            "order": "relevance"
        }
        
        timeout = httpx.Timeout(10.0)  # Sets all timeouts (connect, read, write, pool) to 10 seconds
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            items = data.get("items", [])
            if items:
                video_id = items[0]["id"]["videoId"]
                return f"https://www.youtube.com/watch?v={video_id}"
        
        return None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            logger.warning(f"YouTube API quota exceeded or invalid key for '{exercise_name}'")
        else:
            logger.warning(f"YouTube API error for '{exercise_name}': {e.response.status_code}")
        return None
    except Exception as e:
        logger.warning(f"YouTube API exception for '{exercise_name}': {e}")
        return None


def get_youtube_link_from_template(exercise_name: str) -> Optional[str]:
    """
    Use YouTube search URL template to find videos.
    This is more reliable than scraping Google search.
    
    Args:
        exercise_name: Name of the exercise
        
    Returns:
        YouTube URL or None if fails
    """
    try:
        # YouTube search URL (no API key needed, but may be rate limited)
        query = f"{exercise_name} tutorial proper form"
        # URL encode the query
        import urllib.parse
        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://www.youtube.com/results?search_query={encoded_query}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        timeout = httpx.Timeout(10.0)
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            response = client.get(search_url)
            
            # Handle rate limiting
            if response.status_code == 429:
                logger.warning(f"YouTube search rate limited for '{exercise_name}'")
                return None
            
            if response.status_code == 403:
                logger.warning(f"YouTube search blocked for '{exercise_name}' (403 Forbidden)")
                return None
            
            response.raise_for_status()
            
            # YouTube embeds video IDs in the page HTML in multiple formats
            # Format 1: "videoId":"VIDEO_ID"
            # Format 2: "watch?v=VIDEO_ID"
            # Format 3: /watch?v=VIDEO_ID
            
            page_text = response.text
            
            # Method 1: Look for "videoId":"VIDEO_ID" pattern (most reliable)
            video_id_pattern = r'"videoId":"([a-zA-Z0-9_-]{11})"'
            matches = re.findall(video_id_pattern, page_text)
            
            if matches:
                # Get first result (most relevant)
                video_id = matches[0]
                link = f"https://www.youtube.com/watch?v={video_id}"
                
                # Validate it exists
                if validate_youtube_link(link):
                    logger.debug(f"Found valid YouTube link via template search for '{exercise_name}': {link}")
                    return link
                else:
                    logger.debug(f"Template search found invalid link for '{exercise_name}', trying next match...")
                    # Try next matches if first is invalid
                    for video_id in matches[1:5]:  # Try up to 5 results
                        link = f"https://www.youtube.com/watch?v={video_id}"
                        if validate_youtube_link(link):
                            logger.debug(f"Found valid YouTube link via template search (fallback) for '{exercise_name}': {link}")
                            return link
            
            # Method 2: Look for /watch?v=VIDEO_ID pattern
            watch_pattern = r'/watch\?v=([a-zA-Z0-9_-]{11})'
            matches = re.findall(watch_pattern, page_text)
            if matches:
                video_id = matches[0]
                link = f"https://www.youtube.com/watch?v={video_id}"
                if validate_youtube_link(link):
                    logger.debug(f"Found valid YouTube link via template search (method 2) for '{exercise_name}': {link}")
                    return link
        
        return None
    except httpx.TimeoutException:
        logger.warning(f"YouTube template search timeout for '{exercise_name}'")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(f"YouTube template search HTTP error for '{exercise_name}': {e.response.status_code}")
        return None
    except Exception as e:
        logger.warning(f"YouTube template search exception for '{exercise_name}': {e}")
        return None


def get_youtube_link_scraping(exercise_name: str, max_retries: int = 2) -> Optional[str]:
    """
    Fallback: Scrape Google search for YouTube tutorial links.
    Includes retry logic and rate limiting.
    
    Args:
        exercise_name: Name of the exercise
        max_retries: Maximum number of retry attempts
        
    Returns:
        YouTube URL or None if fails
    """
    for attempt in range(max_retries):
        try:
            # Add delay between requests to avoid rate limiting (except first attempt)
            if attempt > 0:
                delay = 1.0 * attempt  # Exponential backoff: 1s, 2s, etc.
                time.sleep(delay)
                logger.debug(f"Retrying Google scraping for '{exercise_name}' (attempt {attempt + 1}/{max_retries})")
            
            # Search Google for YouTube links
            query = f'site:youtube.com "{exercise_name} tutorial"'
            search_url = f"https://www.google.com/search?q={query}&num=5"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            
            timeout = httpx.Timeout(10.0)  # Sets all timeouts (connect, read, write, pool) to 10 seconds
            with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                response = client.get(search_url)
                
                # Handle rate limiting
                if response.status_code == 429:
                    logger.warning(f"Google rate limiting detected for '{exercise_name}'. Waiting before retry...")
                    if attempt < max_retries - 1:
                        time.sleep(2.0 * (attempt + 1))  # Wait longer for rate limits
                        continue
                    return None
                
                # Handle other HTTP errors
                if response.status_code == 403:
                    logger.warning(f"Google blocked request for '{exercise_name}' (403 Forbidden)")
                    return None
                
                response.raise_for_status()
                
                # Parse HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Method 1: Find YouTube links in search result links
                links = soup.find_all('a', href=True)
                
                for link in links:
                    href = link.get('href', '')
                    # Extract YouTube URL from Google redirect
                    if '/url?q=' in href:
                        match = re.search(r'/url\?q=([^&]+)', href)
                        if match:
                            url = match.group(1)
                            # Decode URL
                            import urllib.parse
                            url = urllib.parse.unquote(url)
                            
                            # Check if it's a YouTube link
                            youtube_pattern = r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})'
                            match = re.search(youtube_pattern, url)
                            if match:
                                video_id = match.group(1)
                                found_link = f"https://www.youtube.com/watch?v={video_id}"
                                logger.debug(f"Found YouTube link via scraping for '{exercise_name}': {found_link}")
                                return found_link
                
                # Method 2: Look for direct YouTube links in page text
                youtube_pattern = r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})'
                page_text = response.text
                matches = re.findall(youtube_pattern, page_text)
                if matches:
                    video_id = matches[0]
                    found_link = f"https://www.youtube.com/watch?v={video_id}"
                    logger.debug(f"Found YouTube link in page text for '{exercise_name}': {found_link}")
                    return found_link
                
                # If we get here, no link was found in this attempt
                if attempt < max_retries - 1:
                    continue  # Try again
                else:
                    logger.debug(f"No YouTube link found in Google search results for '{exercise_name}'")
                    return None
        
        except httpx.TimeoutException:
            logger.warning(f"Google scraping timeout for '{exercise_name}' (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                continue
            return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                # Rate limiting - handled above
                continue
            logger.warning(f"Google scraping HTTP error for '{exercise_name}': {e.response.status_code} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                continue
            return None
        except Exception as e:
            logger.warning(f"Google scraping exception for '{exercise_name}': {e} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                continue
            return None
    
    return None


def get_youtube_link_from_database(exercise_name: str) -> Optional[str]:
    """
    Check fallback database for common exercise links.
    
    Args:
        exercise_name: Name of the exercise (normalized)
        
    Returns:
        YouTube URL or None if not found in database
    """
    # Try exact match first
    if exercise_name in EXERCISE_LINKS_DATABASE:
        return EXERCISE_LINKS_DATABASE[exercise_name]
    
    # Try partial matches (e.g., "dumbbell squat" matches "squat")
    for db_name, link in EXERCISE_LINKS_DATABASE.items():
        if db_name in exercise_name or exercise_name in db_name:
            logger.debug(f"Found database match for '{exercise_name}' → '{db_name}'")
            return link
    
    return None


def get_youtube_link(exercise_name: str, api_key: Optional[str] = None, validate: bool = True) -> Optional[str]:
    """
    Main function to get YouTube link for an exercise.
    Tries: cache → database → API → template search → scraping → None.
    Validates links before returning (if validate=True).
    
    Args:
        exercise_name: Name of the exercise
        api_key: Optional YouTube Data API v3 key
        validate: Whether to validate links before returning (default: True)
        
    Returns:
        YouTube URL or None if all methods fail or link is invalid
    """
    if not exercise_name or not exercise_name.strip():
        return None
    
    # Normalize exercise name (lowercase, strip)
    exercise_name_normalized = exercise_name.strip().lower()
    
    # Load cache
    cache = load_cache()
    
    # Helper function to validate and cache a link
    def validate_and_cache_link(link: Optional[str], source: str) -> Optional[str]:
        """Validate a link and cache it if valid."""
        if not link:
            return None
        
        # Validate link if requested
        if validate:
            if not validate_youtube_link(link):
                logger.warning(f"Invalid YouTube link from {source} for '{exercise_name}': {link}")
                return None
        
        # Cache the valid result
        cache["links"][exercise_name_normalized] = link
        cache["timestamp"] = datetime.utcnow().isoformat()
        save_cache(cache)
        logger.debug(f"Found valid {source} link for '{exercise_name}'")
        return link
    
    # 1. Check cache first (fastest)
    cached_link = get_cached_link(exercise_name_normalized, cache)
    if cached_link:
        # Validate cached link (it might have become invalid)
        if validate:
            if validate_youtube_link(cached_link):
                logger.debug(f"Found valid cached link for '{exercise_name}'")
                return cached_link
            else:
                # Remove invalid link from cache
                logger.warning(f"Cached link for '{exercise_name}' is invalid, removing from cache")
                if exercise_name_normalized in cache["links"]:
                    del cache["links"][exercise_name_normalized]
                    save_cache(cache)
        else:
            return cached_link
    
    # 2. Check fallback database (reliable for common exercises)
    db_link = get_youtube_link_from_database(exercise_name_normalized)
    if db_link:
        validated_link = validate_and_cache_link(db_link, "database")
        if validated_link:
            return validated_link
    
    # 3. Try YouTube API (if key provided) - most reliable
    if api_key:
        api_link = get_youtube_link_api(exercise_name_normalized, api_key)
        validated_link = validate_and_cache_link(api_link, "API")
        if validated_link:
            return validated_link
    
    # 4. Try template-based YouTube search (more reliable than Google scraping)
    template_link = get_youtube_link_from_template(exercise_name_normalized)
    validated_link = validate_and_cache_link(template_link, "template search")
    if validated_link:
        return validated_link
    
    # 5. Fallback to Google scraping (least reliable, but works sometimes)
    scraping_link = get_youtube_link_scraping(exercise_name_normalized)
    validated_link = validate_and_cache_link(scraping_link, "scraping")
    if validated_link:
        return validated_link
    
    # All methods failed - return None (graceful degradation)
    logger.debug(f"Could not find valid YouTube link for '{exercise_name}' (all methods failed)")
    return None


def extract_exercises_from_plan(plan_data: Dict[str, Any], plan_type: str) -> List[str]:
    """
    Extract all exercise names from a workout plan.
    
    Args:
        plan_data: The generated plan JSON
        plan_type: "daily", "weekly", "monthly", or "3months"
        
    Returns:
        List of unique exercise names
    """
    exercises = set()
    
    def extract_from_section(section: Dict[str, Any]) -> None:
        """Recursively extract exercise names from a section."""
        if not isinstance(section, dict):
            return
        
        exercises_list = section.get("exercises", [])
        if isinstance(exercises_list, list):
            for ex in exercises_list:
                if isinstance(ex, dict):
                    name = ex.get("name")
                    if name and isinstance(name, str):
                        exercises.add(name.strip())
                elif isinstance(ex, str):
                    exercises.add(ex.strip())
    
    if plan_type == "daily":
        # Daily plan structure: day_1, day_2, etc. or just day_1
        for key in plan_data.keys():
            if key.startswith("day_"):
                day_data = plan_data[key]
                if isinstance(day_data, dict):
                    extract_from_section(day_data.get("warmup", {}))
                    extract_from_section(day_data.get("main_session", {}))
                    extract_from_section(day_data.get("cooldown", {}))
    else:
        # Weekly/monthly plan structure: Check for days, weekly_schedule, or weeks
        # General weekly/monthly uses "days", athlete weekly uses "weekly_schedule"
        if "days" in plan_data:
            days_key = "days"
        elif "weekly_schedule" in plan_data:
            days_key = "weekly_schedule"
        else:
            days_key = "weeks"  # Fallback for monthly/3months if they use weeks
        
        schedule = plan_data.get(days_key, {})
        
        if isinstance(schedule, dict):
            for day_key, day_data in schedule.items():
                if isinstance(day_data, dict):
                    extract_from_section(day_data.get("warmup", {}))
                    extract_from_section(day_data.get("main_session", {}))
                    extract_from_section(day_data.get("cooldown", {}))
    
    return sorted(list(exercises))


def add_youtube_links_to_plan(plan_data: Dict[str, Any], plan_type: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Add YouTube links to all exercises in a workout plan.
    
    Args:
        plan_data: The generated plan JSON
        plan_type: "daily", "weekly", "monthly", or "3months"
        api_key: Optional YouTube Data API v3 key
        
    Returns:
        Modified plan_data with youtube_link fields added
    """
    exercise_count = 0
    
    def add_link_to_exercise(ex: Any) -> None:
        """Add youtube_link to a single exercise object."""
        nonlocal exercise_count
        if isinstance(ex, dict) and "name" in ex:
            exercise_name = ex.get("name", "")
            if exercise_name:
                # Add small delay between exercises to avoid rate limiting (only for scraping)
                # Skip delay for first exercise and if we have API key (API handles rate limiting)
                if exercise_count > 0 and not api_key:
                    time.sleep(0.5)  # 500ms delay between exercises when scraping
                
                # Get YouTube link (with caching)
                youtube_link = get_youtube_link(exercise_name, api_key)
                if youtube_link:
                    ex["youtube_link"] = youtube_link
                else:
                    # Explicitly set to null if not found (for schema validation)
                    ex["youtube_link"] = None
                
                exercise_count += 1
    
    def add_links_to_section(section: Dict[str, Any]) -> None:
        """Add YouTube links to all exercises in a section."""
        if not isinstance(section, dict):
            return
        
        exercises = section.get("exercises", [])
        if isinstance(exercises, list):
            for ex in exercises:
                add_link_to_exercise(ex)
    
    # Process based on plan type
    if plan_type == "daily":
        # Daily plan: day_1, day_2, etc.
        for key in plan_data.keys():
            if key.startswith("day_"):
                day_data = plan_data[key]
                if isinstance(day_data, dict):
                    add_links_to_section(day_data.get("warmup", {}))
                    add_links_to_section(day_data.get("main_session", {}))
                    add_links_to_section(day_data.get("cooldown", {}))
    else:
        # Weekly/monthly: Check for days, weekly_schedule, or weeks
        # General weekly/monthly uses "days", athlete weekly uses "weekly_schedule"
        if "days" in plan_data:
            days_key = "days"
        elif "weekly_schedule" in plan_data:
            days_key = "weekly_schedule"
        else:
            days_key = "weeks"  # Fallback for monthly/3months if they use weeks
        
        schedule = plan_data.get(days_key, {})
        
        if isinstance(schedule, dict):
            for day_key, day_data in schedule.items():
                if isinstance(day_data, dict):
                    add_links_to_section(day_data.get("warmup", {}))
                    add_links_to_section(day_data.get("main_session", {}))
                    add_links_to_section(day_data.get("cooldown", {}))
    
    return plan_data

