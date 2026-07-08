"""
Neutral word pool for text prints on clothing in generated photo variations.
Grouped into simple style categories used by the "Brand Text" toggle.
"""

STREETWEAR = [
    "URBAN", "STREET", "METRO", "DISTRICT", "UPTOWN",
    "CITY", "BLOCK", "AVENUE", "SQUARE", "GRID",
]

MINIMAL = [
    "STUDIO", "ATELIER", "FORM", "BASIC", "PLAIN",
    "MODE", "LINE", "EDGE", "PURE", "NORTH",
]

SPORT = [
    "ACTIVE", "MOTION", "PACE", "RALLY", "SPRINT",
    "TEAM", "CLUB", "LEAGUE", "SQUAD", "PRO",
]

VINTAGE = [
    "CLASSIC", "HERITAGE", "ORIGINAL", "SINCE '92", "RETRO",
    "ARCHIVE", "LEGACY", "EST.", "VINTAGE", "CO.",
]

NATURE = [
    "SUMMER", "COAST", "HORIZON", "SUNSET", "OCEAN",
    "VALLEY", "RIVER", "MEADOW", "BLOOM", "DRIFT",
]

STATEMENT = [
    "STAY TRUE", "GOOD VIBES", "KEEP GOING", "NO LIMITS",
    "DREAM BIG", "BE KIND", "MAKE IT", "ONE LOVE",
    "GO FAR", "RISE UP",
]

# All words in one list.
ALL_WORDS = STREETWEAR + MINIMAL + SPORT + VINTAGE + NATURE + STATEMENT

# By category (exposed via the /api/words endpoint for the toggles UI).
CATEGORIES = {
    "streetwear": STREETWEAR,
    "minimal": MINIMAL,
    "sport": SPORT,
    "vintage": VINTAGE,
    "nature": NATURE,
    "statement": STATEMENT,
}

if __name__ == "__main__":
    print(f"Total words: {len(ALL_WORDS)}")
    for cat, words in CATEGORIES.items():
        print(f"\n{cat.upper()} ({len(words)}):")
        for w in words:
            print(f"  {w}")
