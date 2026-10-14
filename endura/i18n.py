"""Minimal i18n: tweak texts are {"en": ..., "zh": ...} dicts; tr() picks one."""
import os

_override = None


def set_lang(code):
    """'en' / 'zh' force a language; 'auto' (or anything else) follows the locale."""
    global _override
    _override = code if code in ("en", "zh") else None


def lang():
    if _override:
        return _override
    for v in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        x = os.environ.get(v, "")
        if x:
            return "zh" if x.lower().startswith("zh") else "en"
    return "en"


def tr(text, **fmt):
    if isinstance(text, dict):
        text = text.get(lang()) or text.get("en", "")
    return text.format(**fmt) if fmt else text


def T(en, zh):
    return {"en": en, "zh": zh}
