# -*- coding: utf-8 -*-
from __future__ import absolute_import

try:
    from Components.Language import language
    from Tools.Directories import resolveFilename, SCOPE_PLUGINS
    import gettext
    import json
    import os

    PluginLanguageDomain = "E2DuplicateChannelsManager"
    PluginLanguagePath = "Extensions/E2DuplicateChannelsManager/locale"
    _po_catalog = {}

    def _language_codes():
        """Return the active Enigma2 language and its neutral fallback."""
        try:
            code = language.getLanguage() or ""
        except Exception:
            code = ""
        code = code.replace("-", "_").lower()
        result = []
        for value in (code, code.split("_", 1)[0] if code else ""):
            if value and value not in result:
                result.append(value)
        return result

    def _po_value(line):
        """Decode a quoted PO value on both Python 2 and Python 3."""
        try:
            return json.loads(line.strip())
        except Exception:
            return ""

    def _read_source_catalog(locale_dir):
        """Read the bundled dictionary or an optional PO source catalog.

        Enigma2 uses compiled .mo files normally.  The small reader keeps the
        plugin usable from source packages too.  The JSON dictionary covers
        the bundled languages; translators can also add a standard PO file
        for any additional language without changing Python code.
        """
        try:
            with open(os.path.join(locale_dir, "translations.json"), "rb") as handle:
                dictionaries = json.loads(handle.read().decode("utf-8", "replace"))
            supplemental = dictionaries.get("_supplemental", {})
            for code in _language_codes():
                catalog = dictionaries.get(code)
                if isinstance(catalog, dict):
                    result = dict(catalog)
                    extra = supplemental.get(code)
                    if isinstance(extra, dict):
                        result.update(extra)
                    return result
        except Exception:
            pass
        for code in _language_codes():
            path = os.path.join(locale_dir, code, "LC_MESSAGES",
                                PluginLanguageDomain + ".po")
            if not os.path.isfile(path):
                continue
            catalog = {}
            message_id = None
            message_text = None
            field = None
            try:
                with open(path, "rb") as handle:
                    lines = handle.read().decode("utf-8", "replace").splitlines()
                for raw in lines + [""]:
                    line = raw.strip()
                    if line.startswith("msgid "):
                        if message_id is not None and message_text:
                            catalog[message_id] = message_text
                        message_id = _po_value(line[6:])
                        message_text = ""
                        field = "id"
                    elif line.startswith("msgstr "):
                        message_text = _po_value(line[7:])
                        field = "str"
                    elif line.startswith('"'):
                        value = _po_value(line)
                        if field == "id":
                            message_id = (message_id or "") + value
                        elif field == "str":
                            message_text = (message_text or "") + value
                    elif not line and message_id is not None:
                        if message_text:
                            catalog[message_id] = message_text
                        message_id = None
                        message_text = None
                        field = None
                if catalog:
                    return catalog
            except Exception:
                pass
        return {}

    def localeInit():
        global _po_catalog
        locale_dir = resolveFilename(SCOPE_PLUGINS, PluginLanguagePath)
        gettext.bindtextdomain(PluginLanguageDomain, locale_dir)
        _po_catalog = _read_source_catalog(locale_dir)

    def _(txt):
        translated = gettext.dgettext(PluginLanguageDomain, txt)
        if translated == txt:
            translated = _po_catalog.get(txt, gettext.gettext(txt))
        return translated

    localeInit()
    language.addCallback(localeInit)
except Exception:
    def _(txt):
        return txt
