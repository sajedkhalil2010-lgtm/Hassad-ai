"""Bilingual (and French-tolerant) response-language routing.

The pipeline previously relied entirely on the model remembering a prose rule
that says "reply in the same language the farmer writes in". That works until
it doesn't: a mixed-language prompt, a system message that is itself English,
or a locale hint from the UI can all tip the model into the wrong language,
and nothing in the response says which language was chosen.

This module makes the choice **explicit and deterministic**:

* detection is done here, on the server, with no model round-trip and no
  third-party dependency — so it is instant, offline-testable, and cannot
  drift with model behaviour;
* the resolved language is injected into the prompt as a direct instruction
  rather than a hope, and echoed back in the response ``meta`` so a caller
  can assert what it actually got.

Arabic is detected by *script*, not by keyword: a character counts as Arabic
only when it sits in an Arabic Unicode block **and** is a letter, which is
what keeps Arabic-Indic digits (``٤٦``) and the Arabic percent sign from
turning a purely numeric English message into an "Arabic" one.

French is detected too, but only so that nothing regresses: the chat system
prompt has always promised to mirror French, and a detector that mapped every
non-Arabic message to English would quietly break that promise. The embedded
knowledge base ships Arabic and English only, so an offline plan for a French
request falls back to English — exactly what it did before this module
existed, so French loses nothing.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

#: Language codes the API accepts. ``en`` is the fallback for anything
#: unknown or absent, which keeps bare ``curl`` calls behaving as they always
#: have and makes the parameter fully optional.
SUPPORTED: tuple[str, ...] = ("ar", "fr", "en")
DEFAULT_LANGUAGE = "en"

#: Code -> the language's own name, used when talking *to* the model.
LANG_NAME: dict[str, str] = {"ar": "Arabic", "fr": "French", "en": "English"}

#: Unicode blocks that can contain Arabic letters. Kept as ranges so the
#: presentation forms produced by some keyboards/encoders are still seen.
_ARABIC_BLOCKS: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

#: Letters that exist in French but not in ordinary English prose. Accented
#: characters carry most of the signal, so they are listed first.
_FRENCH_LETTERS = "éèêëàâçùûîïôœ"
_FRENCH_LETTERS = _FRENCH_LETTERS + _FRENCH_LETTERS.upper()

#: Conservative stop-word set: only words that are unambiguously French when
#: seen as standalone tokens. Deliberately small — a false positive here
#: would answer an English farmer in French.
_FRENCH_WORDS = re.compile(
    r"\b(?:le|la|les|des|une|est|dans|pour|avec|sans|vous|nous|comment"
    r"|pourquoi|combien|traiter|maladie|plante|feuille|arrosage|merci)\b",
    re.IGNORECASE,
)


def _is_arabic_letter(ch: str) -> bool:
    """True only for a letter inside an Arabic Unicode block.

    Filtering on the Unicode general category is what excludes Arabic-Indic
    digits (``Nd``), punctuation (``P``) and the tatweel from counting as
    language evidence on their own.
    """
    if not ch.isalpha():
        return False
    code = ord(ch)
    for start, end in _ARABIC_BLOCKS:
        if start <= code <= end:
            return True
    return False


def has_arabic(text: Any) -> bool:
    """True when ``text`` contains at least one Arabic letter."""
    if not text:
        return False
    return any(_is_arabic_letter(ch) for ch in str(text))


def detect_language(text: Any) -> str:
    """Classify a message into ``"ar"`` / ``"fr"`` / ``"en"``.

    Purely mechanical: no network, no model, no timing. Arabic wins outright
    on the strength of its script, because the product is Arabic-first and
    Arabic characters never appear in an English message by accident.

    Empty or non-text input resolves to :data:`DEFAULT_LANGUAGE`.
    """
    raw = "" if text is None else str(text)
    if not raw.strip():
        return DEFAULT_LANGUAGE

    if has_arabic(raw):
        return "ar"

    if any(ch in _FRENCH_LETTERS for ch in raw) or _FRENCH_WORDS.search(raw):
        return "fr"

    return DEFAULT_LANGUAGE


#: Spellings a client may send for a language. Lower-cased and stripped of
#: the region subtag, so ``ar-DZ`` and ``العربية`` both land somewhere useful.
_ALIASES: dict[str, str] = {
    "ar": "ar",
    "arabic": "ar",
    "العربية": "ar",
    "عربي": "ar",
    "fr": "fr",
    "french": "fr",
    "français": "fr",
    "francais": "fr",
    "en": "en",
    "eng": "en",
    "english": "en",
    "anglais": "en",
}


def normalise_language(value: Any) -> str | None:
    """Coerce a client-supplied hint into a supported code, or ``None``.

    Returning ``None`` (rather than a default) lets the caller distinguish
    "no hint given" — which must fall through to detection — from "an
    unusable hint", which is worth logging but must never be forwarded to the
    model as a made-up language.

    Non-ASCII spellings such as ``العربية`` are handled with an NFC
    normalisation first, so a decomposed form still matches.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # ``ar-DZ`` / ``en_US`` -> ``ar`` / ``en``
    primary = re.split(r"[-_:.]", text, maxsplit=1)[0].strip().lower()
    if primary in SUPPORTED:
        return primary

    folded = unicodedata.normalize("NFC", text).lower()
    if folded in _ALIASES:
        return _ALIASES[folded]
    if primary in _ALIASES:
        return _ALIASES[primary]
    return None


def resolve_language(explicit: Any = None, sample: Any = None) -> str:
    """Pick the response language: an explicit hint beats detection.

    An explicit hint is the application locale, which is authoritative — the
    farmer may well have their UI set to Arabic while typing an English
    cultivar name, and in that case the *interface* language is what they
    expect the answer in. Detection only speaks when no hint was supplied.
    """
    return normalise_language(explicit) or detect_language(sample) or DEFAULT_LANGUAGE


def reply_instruction(language: str) -> str:
    """The sentence injected into a prompt that pins the reply language.

    Wording matters: it names the language outright rather than asking the
    model to detect it itself, and it explicitly protects the ``disease`` /
    ``disease_ar`` pair from being collapsed into one language.
    """
    if language == "ar":
        return (
            "LANGUAGE: answer in clear, professional Arabic (العربية) - modern, "
            "practical wording a farmer uses, not formal literary Arabic. Keep "
            "scientific names of pathogens and active ingredients of products in "
            "Latin script, since that is how they appear on the label."
        )
    if language == "fr":
        return (
            "LANGUAGE: answer in clear, professional French (français). Keep "
            "scientific names of pathogens and active ingredients of products in "
            "Latin script."
        )
    return (
        "LANGUAGE: answer in precise, professional English. Keep scientific "
        "names of pathogens and active ingredients of products in Latin script."
    )


__all__ = [
    "SUPPORTED",
    "DEFAULT_LANGUAGE",
    "LANG_NAME",
    "detect_language",
    "has_arabic",
    "normalise_language",
    "resolve_language",
    "reply_instruction",
]
