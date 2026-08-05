# -*- coding: utf-8 -*-
"""Core engine for E2DuplicateChannelsManager.

Python 2.7 and Python 3.x compatible. The plugin removes duplicate channel
references while protecting any service reference that already has a picon.
Picon files are used only as a protection signal; they are never changed.
"""
from __future__ import absolute_import, print_function

import codecs
import datetime
import glob
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import unicodedata

try:
    from . import _
except (ImportError, ValueError):
    def _(text):
        return text

PLUGIN_NAME = "E2DuplicateChannelsManager"


def _plugin_version():
    """Read the packaged version so the interface never needs a hard-coded one."""
    version_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ver.txt")
    try:
        with open(version_path, "rb") as handle:
            value = handle.readline().decode("utf-8", "replace").strip()
        # Both ``1.0.0`` and the user-friendly ``Ver: 1.0.0`` are accepted.
        if ":" in value:
            value = value.rsplit(":", 1)[1].strip()
        return value or "-"
    except (IOError, OSError, UnicodeError):
        return "-"


PLUGIN_VERSION = _plugin_version()
CONFIG_DIR = "/etc/enigma2"
BACKUP_DIR = "/media/hdd/e2_duplicate_channels_backups"
FALLBACK_BACKUP_DIR = "/tmp/e2_duplicate_channels_backups"
LEGACY_BACKUP_DIRS = (
    "/media/hdd/e2_duplicate_picon_backups",
    "/media/usb/e2_duplicate_picon_backups",
    "/data/e2_duplicate_picon_backups",
    "/tmp/e2_duplicate_picon_backups",
)
LOG_PATH = "/tmp/E2DuplicateChannelsManager.log"
LOG_RETENTION_HOURS = 24
_LOG_TIMESTAMP_PATTERN = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

try:
    text_type = unicode
except NameError:
    text_type = str


def _to_text(value):
    if isinstance(value, text_type):
        result = value
    elif isinstance(value, bytes):
        result = value.decode("utf-8", "replace")
    else:
        result = text_type(value)
    # Some lamedb/bouquet files carry a UTF-8 BOM.  It is not part of a
    # service reference or channel name and DreamOS cannot render it.
    return result.lstrip(u"\ufeff")


def prune_operation_log(now=None):
    """Keep only operation-log entries from the most recent 24 hours."""
    try:
        if not os.path.isfile(LOG_PATH):
            return 0
        if now is None:
            now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(hours=LOG_RETENTION_HOURS)
        kept = []
        removed = 0
        with codecs.open(LOG_PATH, "r", "utf-8", "replace") as handle:
            lines = handle.readlines()
        for line in lines:
            match = _LOG_TIMESTAMP_PATTERN.match(line)
            if match is None:
                # A log entry is always timestamped. Drop incomplete legacy
                # fragments so they cannot survive beyond the retention window.
                removed += 1
                continue
            try:
                timestamp = datetime.datetime.strptime(match.group(1),
                                                       "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                removed += 1
                continue
            if timestamp < cutoff:
                removed += 1
                continue
            kept.append(line)
        if removed:
            write_text_atomic(LOG_PATH, "".join(kept))
        return removed
    except Exception:
        return 0


def log_message(message):
    try:
        now = datetime.datetime.now()
        prune_operation_log(now)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        entries = _to_text(message).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        with codecs.open(LOG_PATH, "a", "utf-8") as handle:
            for entry in entries:
                handle.write(u"[%s] %s\n" % (stamp, entry))
    except Exception:
        pass


def recent_log_entries(limit=80):
    """Return the newest operation-log lines for the on-screen log viewer."""
    try:
        prune_operation_log()
        lines = read_text(LOG_PATH).splitlines()
        return "\n".join(lines[-max(1, int(limit)):])
    except Exception:
        return ""


def read_text(path):
    with codecs.open(path, "r", "utf-8", "replace") as handle:
        return handle.read()


def write_text_atomic(path, content):
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".e2dcm_", dir=directory)
    os.close(fd)
    try:
        with codecs.open(tmp_path, "w", "utf-8") as handle:
            handle.write(_to_text(content))
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except Exception:
                pass
        try:
            os.chmod(tmp_path, os.stat(path).st_mode)
        except Exception:
            os.chmod(tmp_path, 0o644)
        if hasattr(os, "replace"):
            os.replace(tmp_path, path)
        else:
            if os.path.exists(path):
                os.unlink(path)
            os.rename(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def normalize_name(name):
    """Conservative normalization: only genuinely equal displayed names merge."""
    # Normalizing Unicode first makes visually identical names compare the
    # same across Arabic, Latin, Cyrillic, Asian and accented scripts without
    # removing meaningful letters or diacritics.
    value = unicodedata.normalize("NFKC", _to_text(name)).strip().lower()
    value = value.replace(u"\u0086", u"").replace(u"\u0087", u"")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[\-_\.]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def canonical_service_ref(value):
    value = _to_text(value).strip()
    if value.startswith("#SERVICE"):
        value = value[len("#SERVICE"):].strip()
    if "::" in value:
        value = value.split("::", 1)[0]
    parts = value.split(":")
    if len(parts) < 10:
        return value
    # Different Enigma2 images can write the same DVB fields as ``1`` or
    # ``0001``. Normalize hexadecimal fields so lamedb, lamedb5, bouquets,
    # and Picon checks always compare the same service reference.
    normalized = []
    for field in parts[:10]:
        try:
            normalized.append("%X" % int(field, 16))
        except (TypeError, ValueError):
            normalized.append(field.upper())
    return ":".join(normalized) + ":"


def is_dvb_service_ref(ref):
    parts = canonical_service_ref(ref).split(":")
    if len(parts) < 10:
        return False
    return parts[0] == "1" and parts[1] == "0"


def satellite_position(ref):
    """Return the orbital position in tenths of a degree, or ``None``.

    The top 16 bits of the Enigma2 DVB namespace hold the orbital position.
    West positions use the 3600-based Enigma2 convention (for example,
    Nilesat is 3530 = 7.0 W). Cable, terrestrial and malformed references
    do not produce a valid satellite position.
    """
    parts = canonical_service_ref(ref).rstrip(":").split(":")
    if len(parts) < 7:
        return None
    try:
        position = (int(parts[6], 16) >> 16) & 0xffff
    except (TypeError, ValueError):
        return None
    if 1800 < position <= 3600:
        position -= 3600
    elif position >= 0x8000:
        position -= 0x10000
    if not position or abs(position) > 3600:
        return None
    return position


SATELLITE_NAMES = {
    -80: "8.0W Ku-band Eutelsat 8 West B",
    -70: "7.0W Ku-band Nilesat 201/301 & Eutelsat 7 West A",
    -50: "5.0W Eutelsat 5 West B",
    -40: "4.0W Dror 1",
    -30: "3.0W Ku-band ABS 3A",
    -8: "0.8W Thor 5/6/7 & Intelsat 10-02",
    19: "1.9E BulgariaSat 1",
    30: "3.0E Ku-band Eutelsat 3B & Rascom QAF 1R",
    48: "4.8E Ku-band Astra 4A & SES 5",
    70: "7.0E Eutelsat 7B/7C",
    90: "9.0E Ku-band Eutelsat 9B & Ka-Sat 9A",
    100: "Eutelsat 10B",
    130: "Hot Bird",
    160: "Eutelsat 16A",
    192: "Astra",
    215: "Eutelsat 21B",
    235: "Astra",
    260: "Badr / Arabsat",
    282: "Astra",
    300: "Arabsat",
}


def satellite_label(position):
    """Format an Enigma2 orbital position for a user-facing choice list."""
    if position is None:
        return _("Unknown satellite")
    direction = _("East") if position > 0 else _("West")
    value = abs(int(position))
    location = "%d.%d %s" % (value // 10, value % 10, direction)
    name = SATELLITE_NAMES.get(position)
    if name:
        name = _(name)
    return "%s - %s" % (name, location) if name else location


def available_satellites(refs):
    positions = sorted(set(position for position in
                           (satellite_position(ref) for ref in refs)
                           if position is not None))
    return [(position, satellite_label(position)) for position in positions]


def picon_filename(ref):
    canonical = canonical_service_ref(ref)
    parts = canonical.rstrip(":").split(":")
    if len(parts) < 10:
        return None
    return "_".join(parts[:10]).upper() + ".png"


def picon_filenames(ref):
    """Return common Picon filenames for a normalized service reference.

    Most images use unpadded hexadecimal fields, while some older DreamOS and
    Enigma2 tools retain leading zeroes from lamedb. Both forms identify the
    same service and must protect it from automatic deletion.
    """
    primary = picon_filename(ref)
    if not primary:
        return []
    result = [primary]
    parts = canonical_service_ref(ref).rstrip(":").split(":")
    if len(parts) < 10:
        return result
    try:
        padded_parts = list(parts[:10])
        padded_parts[3] = "%04X" % int(parts[3], 16)
        padded_parts[4] = "%04X" % int(parts[4], 16)
        padded_parts[5] = "%04X" % int(parts[5], 16)
        padded_parts[6] = "%08X" % int(parts[6], 16)
        padded = "_".join(padded_parts).upper() + ".png"
        if padded not in result:
            result.append(padded)
    except (TypeError, ValueError):
        pass
    return result


def detect_image():
    chunks = []
    for path in ("/etc/image-version", "/etc/issue", "/etc/os-release", "/etc/version"):
        try:
            chunks.append(read_text(path))
        except Exception:
            pass
    joined = "\n".join(chunks).lower()
    dream_markers = ("dreamos", "dreambox os", "opendreambox", "newnigma", "dream-elite")
    opensource_markers = ("openatv", "openpli", "openvix", "openhdf", "openbh", "openspa", "egami", "oe-alliance")
    # DreamOS/Dreambox is one compatibility branch.  Do not classify every
    # dpkg-based Enigma2 image as DreamOS: several open-source images can use
    # a Debian package base as well.
    if any(marker in joined for marker in dream_markers):
        family = "DreamOS / Dreambox"
        package_manager = "dpkg"
        compatibility_mode = "dreamos"
    elif any(marker in joined for marker in opensource_markers) or os.path.exists("/usr/bin/opkg"):
        family = "Open Source / OE-Alliance"
        package_manager = "opkg"
        compatibility_mode = "open_source"
    else:
        family = "Generic Enigma2"
        package_manager = "dpkg" if os.path.exists("/usr/bin/dpkg") else "opkg"
        compatibility_mode = "generic"
    return {
        "family": family,
        "package_manager": package_manager,
        "python": "%d.%d.%d" % (sys.version_info[0], sys.version_info[1], sys.version_info[2]),
        "python_major": sys.version_info[0],
        "compatibility_mode": compatibility_mode,
    }


def find_picon_dirs():
    candidates = [
        "/picon",
        "/usr/share/enigma2/picon",
        "/data/picon",
        "/media/hdd/picon",
        "/media/usb/picon",
        "/media/mmc/picon",
    ]
    # Different images and skins use picon, picon_220x132, piconHD, etc.
    # Search the common roots without modifying any discovered Picon file.
    for pattern in (
        "/picon*",
        "/usr/share/enigma2/picon*",
        "/data/picon*",
        "/media/*/picon*",
    ):
        candidates.extend(glob.glob(pattern))
    result = []
    seen = set()
    for path in candidates:
        real = os.path.realpath(path)
        if os.path.isdir(path) and real not in seen:
            seen.add(real)
            result.append(path)
    return result


def locate_picons(ref, picon_dirs=None):
    filenames = picon_filenames(ref)
    if not filenames:
        return []
    if picon_dirs is None:
        picon_dirs = find_picon_dirs()
    found = []
    for directory in picon_dirs:
        for filename in filenames:
            for name in (filename, filename.lower()):
                path = os.path.join(directory, name)
                if os.path.lexists(path):
                    found.append(path)
                    break
            else:
                continue
            break
    return found


def _lamedb_ref(sid, namespace, tsid, onid, stype):
    return canonical_service_ref("1:0:%s:%s:%s:%s:%s:0:0:0:" % (
        stype.upper(), sid.upper(), tsid.upper(), onid.upper(), namespace.upper()))


def _lamedb4_service_ref(line):
    fields = line.strip().split(":")
    if len(fields) < 5:
        return None
    # Service record: sid:namespace:tsid:onid:service_type:flags
    return _lamedb_ref(fields[0], fields[1], fields[2], fields[3], fields[4])


def parse_lamedb4(path, content=None):
    services = {}
    order = []
    if not os.path.exists(path):
        return services, order
    lines = (read_text(path) if content is None else content).splitlines()
    in_services = False
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line == "services":
            in_services = True
            index += 1
            continue
        if in_services and line == "end":
            break
        if in_services and line and not line.startswith("#") and index + 1 < len(lines):
            ref = _lamedb4_service_ref(line)
            if ref:
                name = lines[index + 1].strip()
                services[ref] = name
                order.append(ref)
                index += 3
                continue
        index += 1
    return services, order


def _split_lamedb5_service(line):
    match = re.match(r'^s:([^,]+),"((?:[^"\\]|\\.)*)"', line)
    if not match:
        return None, None
    service_fields = match.group(1).split(":")
    if len(service_fields) < 5:
        return None, None
    sid, namespace, tsid, onid, stype = service_fields[:5]
    name = match.group(2).replace('\\"', '"')
    return _lamedb_ref(sid, namespace, tsid, onid, stype), name


def parse_lamedb5(path, content=None):
    services = {}
    order = []
    if not os.path.exists(path):
        return services, order
    for raw in (read_text(path) if content is None else content).splitlines():
        line = raw.strip()
        if not line.startswith("s:"):
            continue
        ref, name = _split_lamedb5_service(line)
        if ref:
            services[ref] = name
            order.append(ref)
    return services, order


def load_service_names(config_dir=CONFIG_DIR):
    services = {}
    order = []
    for name in ("lamedb", "lamedb5"):
        path = os.path.join(config_dir, name)
        if not os.path.exists(path):
            continue
        try:
            # Read each lamedb file once.  Previously the short format check
            # and the parser each opened and read the same file.
            content = read_text(path)
            first = content[:64]
            if name == "lamedb5" or "/5/" in first:
                parsed, parsed_order = parse_lamedb5(path, content=content)
            else:
                parsed, parsed_order = parse_lamedb4(path, content=content)
            for ref in parsed_order:
                if ref not in services:
                    order.append(ref)
                services[ref] = parsed[ref]
        except Exception as error:
            log_message("Failed reading %s: %s" % (path, error))
    return services, order


def list_available_satellites(config_dir=CONFIG_DIR):
    """Return satellite choices without scanning duplicates or Picon files."""
    service_names, _order = load_service_names(config_dir)
    return available_satellites(service_names.keys())


def bouquet_files(config_dir=CONFIG_DIR):
    patterns = (
        os.path.join(config_dir, "userbouquet.*.tv"),
        os.path.join(config_dir, "userbouquet.*.radio"),
    )
    result = []
    for pattern in patterns:
        result.extend(glob.glob(pattern))
    return sorted(set(result))


def extract_bouquet_entries(path):
    entries = []
    lines = read_text(path).splitlines(True)
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped.startswith("#SERVICE"):
            continue
        ref = canonical_service_ref(stripped)
        if is_dvb_service_ref(ref):
            entries.append({"line": index, "ref": ref, "raw": raw})
    return lines, entries


def _item_sort_key(item):
    return (item.get("order", 999999999), item.get("ref", ""))


def scan_duplicates(config_dir=CONFIG_DIR, picon_dirs=None, satellite=None):
    """Scan duplicates, optionally restricting the scan to one satellite."""
    log_message("Duplicate scan started (satellite=%s)" %
                ("all" if satellite is None else satellite))
    if picon_dirs is None:
        picon_dirs = find_picon_dirs()
    all_service_names, all_service_order = load_service_names(config_dir)
    satellites = available_satellites(all_service_names.keys())
    if satellite is not None:
        try:
            satellite = int(satellite)
        except (TypeError, ValueError):
            satellite = None
    if satellite is None:
        service_names, service_order = all_service_names, all_service_order
    else:
        service_names = dict((ref, name) for ref, name in all_service_names.items()
                             if satellite_position(ref) == satellite)
        service_order = [ref for ref in all_service_order if ref in service_names]
    order_map = dict((ref, index) for index, ref in enumerate(service_order))
    occurrences = {}
    exact_groups = []
    bouquets = bouquet_files(config_dir)

    for path in bouquets:
        lines, entries = extract_bouquet_entries(path)
        by_ref = {}
        for entry in entries:
            if satellite is not None and satellite_position(entry["ref"]) != satellite:
                continue
            by_ref.setdefault(entry["ref"], []).append(entry["line"])
            occurrences.setdefault(entry["ref"], []).append((path, entry["line"]))
        for ref, line_numbers in by_ref.items():
            if len(line_numbers) > 1:
                exact_groups.append({
                    "type": "exact",
                    "name": service_names.get(ref, ref),
                    "ref": ref,
                    "file": path,
                    "lines": line_numbers,
                    "count": len(line_numbers),
                })

    by_name = {}
    for ref, name in service_names.items():
        normalized = normalize_name(name)
        if normalized:
            by_name.setdefault(normalized, []).append(ref)

    name_groups = []
    for normalized, refs in by_name.items():
        unique_refs = sorted(set(refs), key=lambda ref: (order_map.get(ref, 999999999), ref))
        if len(unique_refs) < 2:
            continue
        items = []
        for ref in unique_refs:
            items.append({
                "ref": ref,
                "name": service_names.get(ref, normalized),
                "picons": locate_picons(ref, picon_dirs),
                "occurrences": occurrences.get(ref, []),
                "order": order_map.get(ref, 999999999),
            })
        items.sort(key=_item_sort_key)
        protected = [item for item in items if item["picons"]]
        unprotected = [item for item in items if not item["picons"]]

        if protected:
            preferred = protected[0]["ref"]
            removable = [item["ref"] for item in unprotected]
            protected_conflicts = [item["ref"] for item in protected[1:]]
            reason = "picon"
        else:
            preferred = items[0]["ref"]
            removable = [item["ref"] for item in items[1:]]
            protected_conflicts = []
            reason = "first"

        name_groups.append({
            "type": "name",
            "key": normalized,
            "name": items[0]["name"],
            "items": items,
            "preferred": preferred,
            "preferred_reason": reason,
            "removable": removable,
            "protected_conflicts": protected_conflicts,
        })

    name_groups.sort(key=lambda group: normalize_name(group["name"]))
    exact_groups.sort(key=lambda group: (group["file"], normalize_name(group["name"])))
    result = {
        "exact": exact_groups,
        "name": name_groups,
        "service_count": len(service_names),
        "total_service_count": len(all_service_names),
        "bouquet_count": len(bouquets),
        "picon_dirs": picon_dirs,
        "satellites": satellites,
        "satellite_filter": satellite,
    }
    log_message("Duplicate scan finished: services=%d groups=%d removable_groups=%d bouquets=%d" % (
        result["service_count"], len(name_groups),
        len([group for group in name_groups if group.get("removable")]),
        result["bouquet_count"]))
    return result


def build_plan(scan_result, clean_exact=True, selected_group_keys=None):
    replacements = {}
    remove_refs = set()
    protected_refs = set()
    keep_refs = set()
    chosen_groups = []
    protected_conflicts = []
    picon_dirs_detected = bool(scan_result.get("picon_dirs"))

    selected_group_keys = (set(selected_group_keys)
                           if selected_group_keys is not None else None)
    for group in scan_result.get("name", []):
        if selected_group_keys is not None and group.get("key") not in selected_group_keys:
            continue
        # Picons are a protection signal, not a prerequisite for cleanup.
        # When no Picon directory exists, duplicate cleanup still proceeds and
        # keeps the first service reference. This matches the primary goal:
        # no duplicate channels after a scan.
        preferred = group.get("preferred")
        if not preferred:
            continue
        for item in group.get("items", []):
            if item.get("picons"):
                protected_refs.add(item["ref"])
        group_removed = []
        for ref in group.get("removable", []):
            if ref in protected_refs:
                continue
            replacements[ref] = preferred
            remove_refs.add(ref)
            group_removed.append(ref)
        group_kept = [item.get("ref") for item in group.get("items", [])
                      if item.get("ref") not in group_removed]
        keep_refs.update(ref for ref in group_kept if ref)
        if group.get("protected_conflicts"):
            protected_conflicts.append({
                "name": group.get("name"),
                "refs": list(group.get("protected_conflicts") or []),
                "preferred": preferred,
            })
        chosen_groups.append({
            "name": group.get("name"),
            "preferred": preferred,
            "removed": group_removed,
            "kept": group_kept,
            "reason": group.get("preferred_reason"),
        })

    # Absolute safety: a service reference with a discovered picon is never removed.
    remove_refs.difference_update(protected_refs)
    for ref in list(replacements.keys()):
        if ref in protected_refs:
            del replacements[ref]

    return {
        "clean_exact": bool(clean_exact),
        "replacements": replacements,
        "remove_refs": sorted(remove_refs),
        "protected_refs": sorted(protected_refs),
        # Every service displayed as KEEP in the final review is an explicit
        # safety invariant, not merely a visual label.
        "keep_refs": sorted(keep_refs),
        "chosen_groups": chosen_groups,
        "protected_conflicts": protected_conflicts,
        "picon_dirs_detected": picon_dirs_detected,
        "exact_count": len(scan_result.get("exact", [])) if clean_exact else 0,
    }


def validate_plan_safety(plan):
    """Reject a cleanup plan that could delete a protected or last service.

    This guard deliberately runs immediately before every write.  It protects
    against malformed plans as well as accidental future changes to the scan
    or selection interface.
    """
    remove_refs = set(plan.get("remove_refs") or [])
    protected_refs = set(plan.get("protected_refs") or [])
    keep_refs = set(plan.get("keep_refs") or [])
    errors = []

    protected_removals = remove_refs.intersection(protected_refs)
    if protected_removals:
        errors.append("Picon-linked service selected for removal")

    keep_removals = remove_refs.intersection(keep_refs)
    if keep_removals:
        errors.append("A KEEP service was selected for removal")

    allowed_removals = set()
    for group in plan.get("chosen_groups") or []:
        preferred = group.get("preferred")
        removed = set(group.get("removed") or [])
        kept = set(group.get("kept") or ([preferred] if preferred else []))
        name = group.get("name") or "Unnamed service"
        if not preferred:
            errors.append("No service kept for %s" % name)
            continue
        if preferred in removed or preferred in remove_refs:
            errors.append("Last service would be removed for %s" % name)
        if not kept.difference(remove_refs):
            errors.append("No service would remain for %s" % name)
        allowed_removals.update(removed)

    unexpected = remove_refs.difference(allowed_removals)
    if unexpected:
        errors.append("Removal plan contains an unverified service")

    if errors:
        raise ValueError("Safety check cancelled cleanup: %s" % "; ".join(errors))


def _line_ref(line):
    stripped = line.strip()
    if stripped.startswith("#SERVICE"):
        ref = canonical_service_ref(stripped)
        if is_dvb_service_ref(ref):
            return ref
    return None


def _replace_ref_in_service_line(line, old_ref, new_ref):
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    prefix = "#SERVICE "
    if not body.startswith(prefix):
        return line
    payload = body[len(prefix):]
    if "::" in payload:
        ref_part, suffix = payload.split("::", 1)
        if canonical_service_ref(ref_part) == old_ref:
            return prefix + new_ref + ":" + suffix + newline
    elif canonical_service_ref(payload) == old_ref:
        return prefix + new_ref + newline
    return line


def _process_bouquet_content(content, plan):
    lines = content.splitlines(True)
    seen = set()
    output = []
    changed = False
    removed_exact = 0
    replaced = 0
    skip_description = False
    for line in lines:
        if skip_description and line.strip().startswith("#DESCRIPTION"):
            skip_description = False
            changed = True
            continue
        skip_description = False
        ref = _line_ref(line)
        if ref:
            new_ref = plan.get("replacements", {}).get(ref)
            final_ref = new_ref or ref
            if plan.get("clean_exact") and final_ref in seen:
                removed_exact += 1
                changed = True
                skip_description = True
                continue
            if new_ref:
                new_line = _replace_ref_in_service_line(line, ref, new_ref)
                if new_line != line:
                    line = new_line
                    replaced += 1
                    changed = True
            seen.add(final_ref)
        output.append(line)
    return "".join(output), changed, removed_exact, replaced


def _process_lamedb4_content(content, remove_refs):
    lines = content.splitlines(True)
    output = []
    in_services = False
    index = 0
    removed = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "services":
            in_services = True
            output.append(lines[index])
            index += 1
            continue
        if in_services and stripped == "end":
            in_services = False
            output.append(lines[index])
            index += 1
            continue
        if in_services and stripped and not stripped.startswith("#"):
            ref = _lamedb4_service_ref(stripped)
            if ref:
                block = lines[index:index + 3]
                if ref in remove_refs:
                    removed += 1
                else:
                    output.extend(block)
                index += len(block)
                continue
        output.append(lines[index])
        index += 1
    return "".join(output), removed


def _process_lamedb5_content(content, remove_refs):
    lines = content.splitlines(True)
    output = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("s:"):
            ref, unused_name = _split_lamedb5_service(stripped)
            if ref in remove_refs:
                removed += 1
                continue
        output.append(line)
    return "".join(output), removed


def _process_lamedb_content(path, content, remove_refs):
    first = content[:64]
    if os.path.basename(path) == "lamedb5" or "/5/" in first:
        return _process_lamedb5_content(content, remove_refs)
    return _process_lamedb4_content(content, remove_refs)


def preview_changes(plan, config_dir=CONFIG_DIR):
    changed_files = []
    bouquet_files_changed = []
    database_files_changed = []
    removed_exact = 0
    replaced = 0
    removed_services = 0

    for path in bouquet_files(config_dir):
        new_content, changed, removed_count, replaced_count = _process_bouquet_content(read_text(path), plan)
        if changed:
            changed_files.append(path)
            bouquet_files_changed.append(path)
        removed_exact += removed_count
        replaced += replaced_count

    remove_refs = set(plan.get("remove_refs", []))
    if remove_refs:
        for name in ("lamedb", "lamedb5"):
            path = os.path.join(config_dir, name)
            if not os.path.exists(path):
                continue
            old_content = read_text(path)
            new_content, removed_count = _process_lamedb_content(path, old_content, remove_refs)
            if new_content != old_content:
                changed_files.append(path)
                database_files_changed.append(path)
            removed_services += removed_count

    return {
        "changed_files": sorted(set(changed_files)),
        "bouquet_files": sorted(set(bouquet_files_changed)),
        "database_files": sorted(set(database_files_changed)),
        "removed_exact": removed_exact,
        "replaced": replaced,
        "removed_services": removed_services,
        "protected_conflicts": list(plan.get("protected_conflicts") or []),
    }


def verify_plan_removals(plan, config_dir=CONFIG_DIR):
    """Confirm that every planned removal disappeared from disk files.

    This runs after writes and before a cleanup is reported as successful.
    Returning a failed verification raises an error and activates the existing
    backup rollback instead of leaving the receiver in an uncertain state.
    """
    remove_refs = set(plan.get("remove_refs") or [])
    if not remove_refs:
        return {"services": [], "bouquets": []}

    services, unused_order = load_service_names(config_dir)
    remaining_services = sorted(remove_refs.intersection(set(services.keys())))
    remaining_bouquets = []
    for path in bouquet_files(config_dir):
        unused_lines, entries = extract_bouquet_entries(path)
        for entry in entries:
            if entry.get("ref") in remove_refs:
                remaining_bouquets.append("%s:%d" % (path, entry.get("line", 0) + 1))
    return {"services": remaining_services, "bouquets": remaining_bouquets}


def _choose_backup_dir(preferred=None):
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend((
        BACKUP_DIR,
        "/media/usb/e2_duplicate_channels_backups",
        "/data/e2_duplicate_channels_backups",
        FALLBACK_BACKUP_DIR,
    ))
    for directory in candidates:
        try:
            if not os.path.isdir(directory):
                os.makedirs(directory)
            test_path = os.path.join(directory, ".write_test")
            with open(test_path, "w") as handle:
                handle.write("ok")
            os.unlink(test_path)
            return directory
        except Exception:
            continue
    raise IOError("No writable backup directory found")


def _backup_candidates(config_dir=CONFIG_DIR):
    paths = []
    for name in ("lamedb", "lamedb5", "bouquets.tv", "bouquets.radio", "blacklist", "whitelist"):
        path = os.path.join(config_dir, name)
        if os.path.exists(path):
            paths.append(path)
    paths.extend(bouquet_files(config_dir))
    for path in ("/etc/tuxbox/satellites.xml", "/etc/tuxbox/cables.xml", "/etc/tuxbox/terrestrial.xml", "/etc/tuxbox/atsc.xml"):
        if os.path.exists(path):
            paths.append(path)
    return sorted(set(paths))


def create_backup(config_dir=CONFIG_DIR, backup_dir=None, reason="before_apply"):
    directory = _choose_backup_dir(backup_dir)
    # User-facing backup names use a clear day/month/year timestamp.
    stamp = datetime.datetime.now().strftime("%d_%m_%Y-%H:%M")
    filename = "e2dcm_%s_%s.tar.gz" % (reason, stamp)
    destination = os.path.join(directory, filename)
    counter = 1
    while os.path.exists(destination):
        destination = os.path.join(directory, "e2dcm_%s_%s_%d.tar.gz" % (reason, stamp, counter))
        counter += 1
    files = _backup_candidates(config_dir)
    metadata = {
        "plugin_name": PLUGIN_NAME,
        "plugin_version": PLUGIN_VERSION,
        "created": stamp,
        "reason": reason,
        "image": detect_image(),
        "files": files,
    }
    meta_fd, meta_path = tempfile.mkstemp(prefix="e2dcm_metadata_", suffix=".json")
    os.close(meta_fd)
    try:
        with codecs.open(meta_path, "w", "utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)
        with tarfile.open(destination, "w:gz") as archive:
            for path in files:
                if os.path.lexists(path):
                    archive.add(path, arcname=path.lstrip("/"), recursive=False)
            archive.add(meta_path, arcname="E2DCM_BACKUP_METADATA.json")
    finally:
        try:
            os.unlink(meta_path)
        except Exception:
            pass
    prune_backups(directory, keep=12)
    log_message("Backup created: reason=%s path=%s files=%d" %
                (reason, destination, len(files)))
    return destination


def _all_backup_dirs(backup_dir=None):
    directories = []
    if backup_dir:
        directories.append(backup_dir)
    directories.extend((
        BACKUP_DIR,
        "/media/usb/e2_duplicate_channels_backups",
        "/data/e2_duplicate_channels_backups",
        FALLBACK_BACKUP_DIR,
    ))
    directories.extend(LEGACY_BACKUP_DIRS)
    return directories


def list_backups(backup_dir=None):
    result = []
    seen = set()
    for directory in _all_backup_dirs(backup_dir):
        if directory in seen or not os.path.isdir(directory):
            continue
        seen.add(directory)
        result.extend(glob.glob(os.path.join(directory, "e2dcm_*.tar.gz")))
        result.extend(glob.glob(os.path.join(directory, "e2dpm_*.tar.gz")))
    return sorted(set(result), key=lambda path: os.path.getmtime(path), reverse=True)


def prune_backups(directory, keep=12):
    backups = []
    backups.extend(glob.glob(os.path.join(directory, "e2dcm_*.tar.gz")))
    backups.extend(glob.glob(os.path.join(directory, "e2dpm_*.tar.gz")))
    backups = sorted(set(backups), key=lambda path: os.path.getmtime(path), reverse=True)
    for old in backups[keep:]:
        try:
            os.unlink(old)
        except Exception:
            pass


def _safe_member_path(base, member_name):
    destination = os.path.realpath(os.path.join(base, member_name))
    base_real = os.path.realpath(base)
    prefix = base_real if base_real.endswith(os.sep) else base_real + os.sep
    return destination == base_real or destination.startswith(prefix)


def restore_backup(backup_path, safety_backup=True, config_dir=CONFIG_DIR):
    if not os.path.isfile(backup_path):
        raise IOError("Backup not found: %s" % backup_path)
    pre_restore = None
    if safety_backup:
        pre_restore = create_backup(config_dir=config_dir, reason="before_restore")
    with tarfile.open(backup_path, "r:gz") as archive:
        members = []
        for member in archive.getmembers():
            if member.name in ("E2DCM_BACKUP_METADATA.json", "E2DPM_BACKUP_METADATA.json"):
                continue
            if not _safe_member_path("/", member.name):
                raise ValueError("Unsafe backup member: %s" % member.name)
            members.append(member)
        try:
            # Python 3.14 keeps strict tar extraction filters.  ``data`` is
            # appropriate here because backups contain only regular Enigma2
            # configuration files; older Python versions use the fallback.
            archive.extractall("/", members=members, filter="data")
        except TypeError:
            archive.extractall("/", members=members)
    log_message("Backup restored: path=%s safety_backup=%s" %
                (backup_path, pre_restore or "none"))
    return pre_restore


def apply_plan(plan, config_dir=CONFIG_DIR, backup_dir=None, reason="before_apply"):
    # Final mandatory check: no files or backups are touched when the plan
    # violates Picon protection or would leave a duplicate group with nothing.
    log_message("Cleanup requested: selected_groups=%d removal_refs=%d" % (
        len(plan.get("chosen_groups") or []), len(plan.get("remove_refs") or [])))
    validate_plan_safety(plan)
    log_message("Cleanup safety validation passed")
    preview = preview_changes(plan, config_dir)
    log_message("Cleanup preview: files=%d services=%d replacements=%d bouquet_duplicates=%d" % (
        len(preview["changed_files"]), preview["removed_services"],
        preview["replaced"], preview["removed_exact"]))
    if not preview["changed_files"]:
        log_message("Cleanup stopped: no file changes required")
        return {
            "backup": None,
            "modified": [],
            "removed_exact": 0,
            "replaced": 0,
            "removed_services": 0,
            "protected_conflicts": preview["protected_conflicts"],
        }

    backup_path = create_backup(config_dir=config_dir, backup_dir=backup_dir, reason=reason)
    log_message("Cleanup backup completed; writing channel files")
    modified = []
    removed_exact = 0
    replaced = 0
    removed_services = 0
    try:
        for path in bouquet_files(config_dir):
            old_content = read_text(path)
            new_content, changed, removed_count, replaced_count = _process_bouquet_content(old_content, plan)
            if changed:
                write_text_atomic(path, new_content)
                modified.append(path)
                log_message("Updated bouquet: %s" % path)
            removed_exact += removed_count
            replaced += replaced_count

        remove_refs = set(plan.get("remove_refs", []))
        if remove_refs:
            for name in ("lamedb", "lamedb5"):
                path = os.path.join(config_dir, name)
                if not os.path.exists(path):
                    continue
                old_content = read_text(path)
                new_content, removed_count = _process_lamedb_content(path, old_content, remove_refs)
                if new_content != old_content:
                    write_text_atomic(path, new_content)
                    modified.append(path)
                    log_message("Updated service database: %s" % path)
                removed_services += removed_count

        verification = verify_plan_removals(plan, config_dir)
        if verification["services"] or verification["bouquets"]:
            log_message("Cleanup verification failed: services=%s bouquets=%s" % (
                ",".join(verification["services"]), ",".join(verification["bouquets"])))
            raise IOError("Deleted channel references are still present on disk")
        log_message("Cleanup verification passed: deleted references are absent from channel files")

        result = {
            "backup": backup_path,
            "modified": sorted(set(modified)),
            "removed_exact": removed_exact,
            "replaced": replaced,
            "removed_services": removed_services,
            "protected_conflicts": preview["protected_conflicts"],
            "verification": verification,
        }
        log_message("Cleanup completed: services=%d bouquet_duplicates=%d replacements=%d backup=%s" % (
            removed_services, removed_exact, replaced, backup_path))
        return result
    except Exception:
        log_message("Cleanup failed; rollback started from %s" % backup_path)
        restore_backup(backup_path, safety_backup=False, config_dir=config_dir)
        raise


def run_automatic_cleanup(config_dir=CONFIG_DIR, picon_dirs=None, backup_dir=None):
    scan_result = scan_duplicates(config_dir=config_dir, picon_dirs=picon_dirs)
    plan = build_plan(scan_result, clean_exact=True)
    preview = preview_changes(plan, config_dir=config_dir)
    if not preview["changed_files"]:
        return {
            "changed": False,
            "scan": scan_result,
            "plan": plan,
            "preview": preview,
            "result": None,
        }
    result = apply_plan(plan, config_dir=config_dir, backup_dir=backup_dir, reason="automatic_cleanup")
    return {
        "changed": True,
        "scan": scan_result,
        "plan": plan,
        "preview": preview,
        "result": result,
    }


def watched_signature(config_dir=CONFIG_DIR):
    paths = []
    for name in ("lamedb", "lamedb5", "bouquets.tv", "bouquets.radio"):
        path = os.path.join(config_dir, name)
        if os.path.exists(path):
            paths.append(path)
    paths.extend(bouquet_files(config_dir))
    signature = []
    for path in sorted(set(paths)):
        try:
            stat = os.stat(path)
            signature.append((path, int(stat.st_mtime), int(stat.st_size)))
        except Exception:
            signature.append((path, 0, 0))
    return tuple(signature)
