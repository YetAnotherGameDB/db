#!/usr/bin/env python3
"""Shared matcher between No-Intro dumps and the superfamicom.org catalogue.

Both sides romanize Japanese titles the same way (Hepburn, ` - ` before the
subtitle), and the catalogue's slug is that romanization slugified, so the
join key is simply the slugified dump title:

    "Zelda no Densetsu 1 - The Hyrule Fantasy"
      -> zelda-no-densetsu-1-the-hyrule-fantasy   (catalogue slug)

Five keys are tried in order, loosest last — exact slug, punctuation-free,
filler-word-free, roman-numerals-as-arabic, then main-title-only — and every
key after the first is only honoured when it resolves to exactly one catalogue
entry. A wrong match still
has to survive the mediaworld serial check downstream, which is the real gate.

Imported by lookup-nintendo-mediaworld.py and add-nintendo-games.py.
"""

import re
import unicodedata

# famicom cartridges and FDS disks are both listed on the site's Famicom side.
SOURCE_FOR = {"famicom": "famicom", "fds": "famicom", "snes": "sfc"}

# No-Intro writes "Addams Family, The"; the catalogue writes "The Addams Family".
TRAILING_ARTICLE = re.compile(r"^(.*?),\s+(The|A|An|Les|La|Le|Der|Die|Das)\b(.*)$", re.I)


def unswap_article(text):
    m = TRAILING_ARTICLE.match(text or "")
    return f"{m.group(2)} {m.group(1)}{m.group(3)}" if m else text


def slugify(text):
    """Lowercase ASCII slug, matching how the catalogue builds its slugs."""
    text = unicodedata.normalize("NFKD", unswap_article(text) or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ").replace("+", " plus ")
    text = re.sub(r"[''`]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def head(text):
    """Key on the main title only, dropping the subtitle after ' - '.

    Romanization of Japanese particles drifts between the two sources
    ("Chinmoku e no Seisen" vs "Chinmoku heno Seisen"), which almost always
    happens in the subtitle. Only used when it resolves to exactly one entry.
    """
    return tight(unswap_article(text).split(" - ")[0])


def tight(text):
    """Punctuation-free key: absorbs hyphen/colon/space disagreements."""
    return re.sub(r"[^a-z0-9]+", "", slugify(text))


# No-Intro numbers sequels in roman ("Bomberman II"), the catalogue in arabic
# ("bomberman-2"). Bare X is excluded because it is far more often a letter
# than a numeral (Rockman X is not Rockman 10); bare V is kept, since allowing
# X recovers no additional titles and every bare-V case is a real numeral
# (Final Fantasy V, Ys V).
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5",
         "vi": "6", "vii": "7", "viii": "8", "ix": "9"}
ROMAN_RE = re.compile(r"\b(ii|iii|iv|v|vi|vii|viii|ix)\b")


def numeral(text):
    """Tight key with roman sequel numbers rewritten as arabic."""
    return tight(ROMAN_RE.sub(lambda m: ROMAN[m.group(1)], slugify(text)))


def loose(text):
    """Tight key minus filler words and trailing volume/edition noise."""
    key = slugify(text)
    key = re.sub(r"-(?:the|a|an|no|wo|ga|to|de|ni)-", "-", key)
    key = re.sub(r"^(?:the|a|an)-", "", key)
    return re.sub(r"[^a-z0-9]+", "", key)


# --- catalogue numbers ----------------------------------------------------
#
# Super Famicom codes are uniform (SHVC-ZL). Famicom and FDS codes are
# publisher-prefixed and much messier: the catalogue's serial slot can hold a
# bare price code ("04"), a JAN ("4907940100431"), or the part number with the
# retail price glued on ("TFC-ANII-5900"), and mediaworld does the same
# ("HFC-NM-45" for HFC-NM). So a serial is first reduced to the part number,
# then compared allowing a trailing numeric price/revision suffix.

SERIAL_HYPHEN = re.compile(r"\b([A-Z]{2,5}-[A-Z0-9]{1,6}(?:-[A-Z0-9]{1,4})?)\b")
SERIAL_SOLID = re.compile(r"\b([A-Z]{2,4}[0-9]{2,5})\b")


def clean_serial(raw):
    """Reduce a raw serial field to a catalogue number, or '' if there is none."""
    text = (raw or "").upper().strip()
    if not text or "?" in text:
        return ""
    # Parenthesised chunks are prices or internal codes, never the part number.
    text = re.sub(r"\([^)]*\)", " ", text)
    match = SERIAL_HYPHEN.search(text) or SERIAL_SOLID.search(text)
    if not match:
        return ""
    serial = match.group(1)
    # A bare number (JAN, price) is not a catalogue number.
    if not re.search(r"[A-Z]", serial):
        return ""
    return serial


def serial_key(serial):
    return re.sub(r"[^A-Z0-9]", "", (serial or "").upper())


def serials_agree(a, b):
    """True when two catalogue numbers denote the same release.

    Equal after normalization, or one extends the other with a purely numeric
    suffix (the printed price, e.g. HFC-NM vs HFC-NM-45). A trailing *letter*
    difference is a different game (HVC-M vs HVC-MA), so it never matches.
    """
    ka, kb = serial_key(a), serial_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    longer, shorter = (ka, kb) if len(ka) > len(kb) else (kb, ka)
    if len(shorter) < 4 or not longer.startswith(shorter):
        return False
    return longer[len(shorter):].isdigit()


# Western releases are tagged by region suffix (SNS-MW-USA) or western prefix.
WESTERN_SUFFIX = re.compile(
    r"-(USA|EUR|NOE|FRA|ESP|ITA|UKV|AUS|SCN|HOL|GPS|BRA|KOR|CHN)$", re.I)
WESTERN_PREFIX = re.compile(r"^(NES|SNS|SNSP|NESP)-", re.I)


def jp_serial_index(entry):
    """(index, catalogue number) of the Japanese release among an entry's serials.

    The /info page prints serials and release dates as parallel ' / '-separated
    lists, one slot per regional release, so the index locates the JP date too.
    Returns (None, '') when the catalogue has no usable Japanese serial.
    """
    for i, raw in enumerate(entry.get("serials", [])):
        raw = (raw or "").strip()
        if not raw or WESTERN_SUFFIX.search(raw) or WESTERN_PREFIX.match(raw):
            continue
        serial = clean_serial(raw)
        if serial and not (WESTERN_SUFFIX.search(serial) or WESTERN_PREFIX.match(serial)):
            return i, serial
    return None, ""


def build_index(catalogue):
    """Map each key form to catalogue entries, per source."""
    index = {"slug": {}, "tight": {}, "loose": {}, "numeral": {}, "head": {}}
    for entry in catalogue:
        source = entry.get("source", "")
        title_en = entry.get("title_en", "")
        forms = {
            "slug": {entry["slug"]},
            "tight": {tight(entry["slug"]), tight(title_en)},
            "loose": {loose(entry["slug"]), loose(title_en)},
            "numeral": {numeral(entry["slug"]), numeral(title_en)},
            "head": {head(title_en)},
        }
        for form, keys in forms.items():
            for key in keys:
                if key:
                    index[form].setdefault((source, key), []).append(entry)
    return index


def match_dump(dump, index):
    """Best catalogue entry for a dump, or None when unmatched/ambiguous."""
    source = SOURCE_FOR[dump["platform"]]
    title = dump["title"]
    for form, key in (("slug", slugify(title)), ("tight", tight(title)),
                      ("loose", loose(title)), ("numeral", numeral(title)),
                      ("head", head(title))):
        hits = index[form].get((source, key))
        if not hits:
            continue
        if len(hits) == 1:
            return hits[0]
        # Ambiguous on a loose key: fall back to an exact slug tie-break.
        exact = [h for h in hits if h["slug"] == slugify(title)]
        if len(exact) == 1:
            return exact[0]
        return None
    return None
