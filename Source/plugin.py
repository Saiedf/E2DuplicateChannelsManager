# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import os
import glob
import threading
import datetime
import tarfile
import tempfile
import zipfile

try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen

from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox as _MessageBox
from Screens.ChoiceBox import ChoiceBox as _ChoiceBox
try:
    from Screens.LocationBox import LocationBox
except Exception:
    LocationBox = None
from Components.ActionMap import ActionMap
from Components.Label import Label as _Label
from Components.MenuList import MenuList
try:
    from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
except Exception:
    MultiContentEntryText = None
    MultiContentEntryPixmapAlphaTest = None

try:
    from Tools.LoadPixmap import LoadPixmap
except Exception:
    LoadPixmap = None

try:
    from enigma import eDVBDB, eTimer, getDesktop
except Exception:
    eDVBDB = None
    eTimer = None
    getDesktop = None

try:
    from enigma import eServiceReference
except Exception:
    eServiceReference = None

try:
    from enigma import eListboxPythonMultiContent, gFont
except Exception:
    eListboxPythonMultiContent = None
    gFont = None
try:
    from enigma import RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_VALIGN_CENTER, RT_VALIGN_TOP
except Exception:
    RT_HALIGN_LEFT = RT_HALIGN_CENTER = RT_VALIGN_CENTER = RT_VALIGN_TOP = 0

from . import _
from .core import (
    PLUGIN_VERSION,
    apply_plan,
    build_plan,
    create_backup,
    list_backups,
    list_available_satellites,
    log_message,
    preview_changes,
    recent_log_entries,
    restore_backup,
    run_automatic_cleanup,
    scan_duplicates,
    satellite_label,
    watched_signature,
)

PLUGIN_PATH = os.path.dirname(__file__)
_MONITOR = None
UPDATE_REPOSITORY_URL = "https://github.com/Saiedf/E2DuplicateChannelsManager"
UPDATE_SOURCE_DIRECTORY = "Source"
UPDATE_VERSION_URLS = (
    "https://raw.githubusercontent.com/Saiedf/E2DuplicateChannelsManager/main/ver.txt",
    "https://raw.githubusercontent.com/Saiedf/E2DuplicateChannelsManager/master/ver.txt",
)
UPDATE_ARCHIVE_URLS = (
    "https://github.com/Saiedf/E2DuplicateChannelsManager/archive/refs/heads/main.zip",
    "https://github.com/Saiedf/E2DuplicateChannelsManager/archive/refs/heads/master.zip",
)
UPDATE_CHECK_SETTING_PATH = "/etc/enigma2/e2dcm_check_updates_at_startup"
_UPDATE_CHECK = {"started": False, "done": False, "version": None, "error": None,
                 "notified": False}
_PLUGIN_UPDATE = {"started": False, "done": False, "result": None, "error": None,
                  "reported": False}


try:
    _unicode_type = unicode
except NameError:
    _unicode_type = str


def _menu_text(value):
    """Return the native GUI string type for both DreamOS and Enigma2.

    DreamOS OE2.5 requires UTF-8 byte strings for both listbox rows and
    eLabel text.  Open-source images running Python 3 require normal ``str``.
    """
    if value is None:
        value = ""
    if isinstance(value, _unicode_type):
        if _unicode_type is not str:
            return value.encode("utf-8")
        return value
    try:
        return str(value)
    except Exception:
        return ""


def Label(text=""):
    """Create a stock label with text safe for DreamOS Python 2."""
    return _Label(_menu_text(text))


def _version_numbers(value):
    """Turn ``Ver: 1.2.3`` into a Python-2/3 comparable tuple."""
    if value is None:
        return ()
    if not isinstance(value, _unicode_type):
        try:
            value = _unicode_type(value)
        except Exception:
            return ()
    if ":" in value:
        value = value.rsplit(":", 1)[1]
    numbers = []
    for part in value.strip().split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        try:
            numbers.append(int(digits))
        except (TypeError, ValueError):
            return ()
    return tuple(numbers)


def _backup_display_label(path):
    """Show a compact backup name with a visible creation date and time."""
    name = os.path.basename(path) or path
    try:
        timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        date_text = timestamp.strftime("%d-%m-%Y %H:%M")
    except Exception:
        date_text = "-"
    return "%s  |  %s" % (date_text, name)


def _update_check_enabled():
    """Read the persistent Yes/No update-check option; default is Yes."""
    try:
        with open(UPDATE_CHECK_SETTING_PATH, "rb") as handle:
            return handle.read(16).strip().lower() != b"no"
    except Exception:
        return True


def _set_update_check_enabled(enabled):
    """Persist the menu CheckBox value without relying on image-specific config APIs."""
    directory = os.path.dirname(UPDATE_CHECK_SETTING_PATH)
    temporary = UPDATE_CHECK_SETTING_PATH + ".tmp"
    if not os.path.isdir(directory):
        os.makedirs(directory)
    try:
        with open(temporary, "wb") as handle:
            handle.write(b"yes" if enabled else b"no")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except Exception:
                pass
        if hasattr(os, "replace"):
            os.replace(temporary, UPDATE_CHECK_SETTING_PATH)
        else:
            os.rename(temporary, UPDATE_CHECK_SETTING_PATH)
    except Exception:
        try:
            os.unlink(temporary)
        except Exception:
            pass
        raise


def _remote_plugin_version():
    """Read the packaged version from GitHub without downloading the plugin."""
    last_error = None
    for url in UPDATE_VERSION_URLS:
        try:
            response = urlopen(url, timeout=5)
            try:
                raw = response.read(128)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            if not isinstance(raw, _unicode_type):
                raw = raw.decode("utf-8", "replace")
            value = raw.splitlines()[0].strip() if raw else ""
            if _version_numbers(value):
                return value
        except Exception as error:
            last_error = error
    raise IOError("GitHub version check failed: %s" % (last_error or "invalid version file"))


def _download_update_archive():
    """Download the source archive from GitHub, with main/master fallback."""
    last_error = None
    for url in UPDATE_ARCHIVE_URLS:
        try:
            response = urlopen(url, timeout=20)
            try:
                data = response.read()
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            if data[:2] == b"PK":
                return data
            last_error = IOError("Downloaded file is not a ZIP archive")
        except Exception as error:
            last_error = error
    raise IOError("GitHub update download failed: %s" % (last_error or "unknown error"))


def _safe_archive_relative_path(value):
    """Reject archive paths that could write outside the plugin directory."""
    value = value.replace("\\", "/").strip("/")
    if not value or value.startswith("../") or "/../" in value or value == "..":
        return None
    return value


def _plugin_update_backup_path():
    """Choose a writable, persistent destination for the code backup."""
    candidates = ("/media/hdd/e2_duplicate_channels_backups",
                  "/media/usb/e2_duplicate_channels_backups",
                  "/tmp/e2_duplicate_channels_backups")
    for directory in candidates:
        try:
            if not os.path.isdir(directory):
                os.makedirs(directory)
            test_path = os.path.join(directory, ".e2dcm_update_write_test")
            with open(test_path, "wb") as handle:
                handle.write(b"ok")
            os.unlink(test_path)
            stamp = datetime.datetime.now().strftime("%d_%m_%Y-%H:%M")
            return os.path.join(directory, "e2dcm_plugin_before_update_%s.tar.gz" % stamp)
        except Exception:
            continue
    raise IOError("No writable location for plugin update backup")


def _write_binary_atomic(path, data):
    """Write one downloaded plugin file safely on Python 2 and Python 3."""
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    fd, temporary = tempfile.mkstemp(prefix=".e2dcm_update_", dir=directory)
    os.close(fd)
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except Exception:
                pass
        try:
            os.chmod(temporary, os.stat(path).st_mode)
        except Exception:
            os.chmod(temporary, 0o644)
        if hasattr(os, "replace"):
            os.replace(temporary, path)
        else:
            os.rename(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except Exception:
            pass
        raise


def _apply_github_update(expected_version):
    """Validate, back up and install a newer source archive from GitHub."""
    archive_data = _download_update_archive()
    archive_file = tempfile.NamedTemporaryFile(prefix="e2dcm_update_", suffix=".zip", delete=False)
    try:
        archive_file.write(archive_data)
        archive_file.close()
        with zipfile.ZipFile(archive_file.name, "r") as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            plugin_suffix = "/%s/plugin.py" % UPDATE_SOURCE_DIRECTORY
            plugin_candidates = [name for name in names if name.endswith(plugin_suffix)]
            source_prefix = None
            for candidate in plugin_candidates:
                prefix = candidate[:-len("/plugin.py")]
                if prefix + "/core.py" in names and prefix + "/ver.txt" in names:
                    source_prefix = prefix
                    break
            if source_prefix is None:
                raise IOError("Update archive does not contain a valid plugin package")

            remote_ver = archive.read(source_prefix + "/ver.txt").decode("utf-8", "replace").strip()
            if _version_numbers(remote_ver) < _version_numbers(expected_version):
                raise IOError("Downloaded archive version does not match the available update")
            if _version_numbers(remote_ver) <= _version_numbers(PLUGIN_VERSION):
                raise IOError("Downloaded archive is not newer than the installed version")

            payload = []
            for name in names:
                if not name.startswith(source_prefix + "/"):
                    continue
                relative = _safe_archive_relative_path(name[len(source_prefix) + 1:])
                if relative is None or relative.startswith(".git/") or "__pycache__/" in relative:
                    continue
                payload.append((relative, archive.read(name)))
            if not any(relative == "plugin.py" for relative, unused in payload):
                raise IOError("Update archive is missing plugin.py")

        backup_path = _plugin_update_backup_path()
        with tarfile.open(backup_path, "w:gz") as backup:
            backup.add(PLUGIN_PATH, arcname=os.path.basename(PLUGIN_PATH), recursive=True)

        original_files = {}
        created_files = []
        try:
            for relative, data in payload:
                destination = os.path.normpath(os.path.join(PLUGIN_PATH, relative))
                plugin_root = os.path.normpath(PLUGIN_PATH)
                if not destination.startswith(plugin_root + os.sep):
                    raise IOError("Unsafe update destination")
                if os.path.exists(destination):
                    with open(destination, "rb") as handle:
                        original_files[destination] = handle.read()
                else:
                    created_files.append(destination)
                _write_binary_atomic(destination, data)
        except Exception:
            for destination, data in original_files.items():
                try:
                    _write_binary_atomic(destination, data)
                except Exception:
                    pass
            for destination in created_files:
                try:
                    if os.path.isfile(destination):
                        os.unlink(destination)
                except Exception:
                    pass
            raise
        return {"version": remote_ver, "backup": backup_path, "files": len(payload)}
    finally:
        try:
            archive_file.close()
        except Exception:
            pass
        try:
            os.unlink(archive_file.name)
        except Exception:
            pass


def _start_update_check():
    """Start one non-blocking update check per Enigma2 session."""
    if _UPDATE_CHECK.get("started"):
        return
    _UPDATE_CHECK["started"] = True

    def worker():
        try:
            remote = _remote_plugin_version()
            _UPDATE_CHECK["version"] = remote
            log_message("GitHub update check completed: remote=%s local=%s" %
                        (remote, PLUGIN_VERSION))
        except Exception as error:
            _UPDATE_CHECK["error"] = str(error)
            log_message("GitHub update check skipped: %s" % error)
        _UPDATE_CHECK["done"] = True

    thread = threading.Thread(target=worker, name="E2DCMUpdateCheck")
    try:
        thread.setDaemon(True)
    except Exception:
        pass
    thread.start()


def _start_plugin_update(remote_version):
    """Install the confirmed update in a background thread."""
    if _PLUGIN_UPDATE.get("started"):
        return
    _PLUGIN_UPDATE["started"] = True

    def worker():
        try:
            _PLUGIN_UPDATE["result"] = _apply_github_update(remote_version)
            log_message("GitHub plugin update completed: %s" %
                        _PLUGIN_UPDATE["result"].get("version"))
        except Exception as error:
            _PLUGIN_UPDATE["error"] = str(error)
            log_message("GitHub plugin update failed: %s" % error)
        _PLUGIN_UPDATE["done"] = True

    thread = threading.Thread(target=worker, name="E2DCMPluginUpdate")
    try:
        thread.setDaemon(True)
    except Exception:
        pass
    thread.start()


class MessageBox(_MessageBox):
    """Convert every popup's text before Enigma2 creates its eLabel."""
    def __init__(self, session, text, *args, **kwargs):
        _MessageBox.__init__(self, session, _menu_text(text), *args, **kwargs)


class ChoiceBox(_ChoiceBox):
    """Keep ChoiceBox headings and visible rows Python-2 GUI safe."""
    def __init__(self, session, *args, **kwargs):
        if "title" in kwargs:
            kwargs["title"] = _menu_text(kwargs["title"])
        if "list" in kwargs:
            converted = []
            for item in kwargs["list"]:
                if isinstance(item, (tuple, list)) and item:
                    converted.append((_menu_text(item[0]),) + tuple(item[1:]))
                else:
                    converted.append(_menu_text(item))
            kwargs["list"] = converted
        _ChoiceBox.__init__(self, session, *args, **kwargs)


def _removable_groups(scan_result):
    """Groups with at least one duplicate reference that has no Picon."""
    return [group for group in scan_result.get("name", [])
            if group.get("removable")]


def _column(value, width):
    """Truncate and pad a table cell for the fixed-width review list."""
    if value is None:
        value = ""
    if not isinstance(value, _unicode_type):
        try:
            value = _unicode_type(value)
        except Exception:
            value = ""
    value = value.replace("\n", " ").strip()
    if len(value) > width:
        value = value[:max(0, width - 3)] + "..."
    return value.ljust(width)


_REVIEW_COLUMNS = (_("Yes/No"), _("Channel name"), _("Keep rule"),
                   _("Total"), _("Remove"), _("Picon"))
_CELL_COLOR = 0xffffff
_CELL_SELECTED_COLOR = 16777215
_CELL_BACKGROUND = 0x00202020
_CELL_SELECTED_BACKGROUND = 0x00606000
_CELL_BORDER = 0xffffff
_TOGGLE_ON_BACKGROUND = 0x006f1f
_TOGGLE_OFF_BACKGROUND = 0x8d1616
_TOGGLE_TEXT = 0xffffff

_ROW_TOGGLE_PIXMAPS = {}


def _plain_menu_list(rows=None, wrap=False):
    """Create a MenuList on both legacy and current Enigma2 images."""
    rows = rows or []
    try:
        return MenuList(rows, enableWrapAround=wrap)
    except TypeError:
        # Older DreamOS/OpenPLi MenuList constructors do not accept the
        # keyword form of enableWrapAround.
        return MenuList(rows)


def _multi_content_menu_list(wrap=False):
    """Return (list, enabled) with a safe fallback for older images."""
    if eListboxPythonMultiContent is not None:
        try:
            return (MenuList([], enableWrapAround=wrap,
                             content=eListboxPythonMultiContent), True)
        except (TypeError, AttributeError):
            pass
    return (_plain_menu_list([], wrap), False)


def _configure_multicontent(menu, font_size, item_height):
    """Use enhanced list styling when the local Enigma2 API provides it."""
    try:
        menu.l.setFont(0, gFont("Regular", font_size))
    except Exception:
        pass
    try:
        menu.l.setItemHeight(item_height)
    except Exception:
        pass


def _row_toggle_pixmap(state):
    """Load the supplied FHD ON/OFF indicator once for list rows."""
    if LoadPixmap is None:
        return None
    key = "on" if state == "Yes" else "off"
    if key not in _ROW_TOGGLE_PIXMAPS:
        path = os.path.join(PLUGIN_PATH, "buttons", "%s_row_fhd.png" % key)
        try:
            _ROW_TOGGLE_PIXMAPS[key] = LoadPixmap(cached=True, path=path)
        except TypeError:
            try:
                _ROW_TOGGLE_PIXMAPS[key] = LoadPixmap(path=path)
            except Exception:
                _ROW_TOGGLE_PIXMAPS[key] = None
        except Exception:
            _ROW_TOGGLE_PIXMAPS[key] = None
    return _ROW_TOGGLE_PIXMAPS.get(key)
def _menu_index(menu):
    """Return the selected row index on DreamOS and open-source Enigma2."""
    try:
        return int(menu.getSelectionIndex())
    except Exception:
        try:
            return int(menu.l.getCurrentSelectionIndex())
        except Exception:
            return 0


def _review_column_layout(list_width):
    """Fit the review table exactly into the available list width.

    The channel-name column is deliberately flexible; all of the compact
    status/count columns retain their readable widths on every resolution.
    """
    select_width = 85
    gap = 6
    trailing_columns = (115, 82, 96, 82)
    trailing_gaps = (7, 8, 8)
    content_width = max(620, int(list_width) - 18)  # leave room for scrollbar
    channel_width = max(210, content_width - select_width - gap -
                        sum(trailing_columns) - sum(trailing_gaps) - gap)
    keep_x = select_width + gap + channel_width + gap
    total_x = keep_x + trailing_columns[0] + trailing_gaps[0]
    remove_x = total_x + trailing_columns[1] + trailing_gaps[1]
    picon_x = remove_x + trailing_columns[2] + trailing_gaps[2]
    return (
        (0, select_width),
        (select_width + gap, channel_width),
        (keep_x, trailing_columns[0]),
        (total_x, trailing_columns[1]),
        (remove_x, trailing_columns[2]),
        (picon_x, trailing_columns[3]),
    )


def _removal_preview_column_layout(list_width):
    """Fit the final review's three columns across the complete table."""
    content_width = max(620, int(list_width) - 18)  # leave room for scrollbar
    action_width = 120
    gap = 7
    name_width = max(240, (content_width - action_width - (2 * gap)) // 2)
    reference_width = content_width - action_width - name_width - (2 * gap)
    return (
        (0, action_width),
        (action_width + gap, name_width),
        (action_width + gap + name_width + gap, reference_width),
    )


def _satellite_columns(label):
    """Split the core label into the name and orbital-position columns."""
    if " - " in label:
        # ``str.rsplit`` returns a list, but percent formatting needs a tuple
        # for the two %s placeholders.  This affected only satellite labels
        # containing a hyphen and crashed the picker on DreamOS.
        return tuple(label.rsplit(" - ", 1))
    return label, ""


def _satellite_column_layout():
    """Size the satellite table to the full width of its list widget.

    The picker uses the same responsive screen width as ``_skin``.  Keeping
    the last edge of every row at that width makes the satellite-name column
    use all of the space up to the scrollbar while retaining a fixed, readable
    orbital-position column on the right.
    """
    screen_width = 940
    if getDesktop is not None:
        try:
            desktop_width = getDesktop(0).size().width()
            desktop_height = getDesktop(0).size().height()
            if desktop_width <= 1280:
                screen_width = 870
            elif desktop_width >= 1920 and desktop_height >= 1080:
                screen_width = 1280
        except Exception:
            pass

    table_width = screen_width - 66  # matches the 8-pixel inset list frame
    yes_no_width = 85
    position_width = max(170, min(230, table_width // 4))
    satellite_width = table_width - yes_no_width - position_width
    return (
        (0, yes_no_width),
        (yes_no_width, satellite_width),
        (yes_no_width + satellite_width, position_width),
    )


class E2DuplicateChannelsManagerSatelliteList(MenuList):
    """XMLupdate-style satellite table with real per-cell borders."""
    def __init__(self):
        self.multi_content = bool(eListboxPythonMultiContent and MultiContentEntryText and gFont)
        if self.multi_content:
            try:
                MenuList.__init__(self, [], enableWrapAround=True,
                                  content=eListboxPythonMultiContent)
            except (TypeError, AttributeError):
                self.multi_content = False
        if self.multi_content:
            # Satellite names are deliberately two points larger for easier
            # reading from the television viewing distance.
            _configure_multicontent(self, 24, 44)
        else:
            try:
                MenuList.__init__(self, [], enableWrapAround=True)
            except TypeError:
                MenuList.__init__(self, [])

    def set_entries(self, satellites):
        if not self.multi_content:
            rows = [_menu_text(_("Yes/No    Satellites                                      Position"))]
            for index, (_position, label) in enumerate(satellites):
                name, orbital = _satellite_columns(label)
                state = _("Yes") if index == getattr(self, "checked_index", 0) else _("No")
                rows.append(_menu_text("%-8s  %-64s  %s" % (state, name, orbital)))
            self.setList(rows)
            return
        result = [self._make_row((_("Yes/No"), _("Satellites"), _("Position")), header=True)]
        for index, (position, label) in enumerate(satellites):
            name, orbital = _satellite_columns(label)
            state = _("Yes") if index == getattr(self, "checked_index", 0) else _("No")
            row = self._make_row((state, name, orbital), header=False)
            row[0] = position
            result.append(row)
        self.l.setList(result)

    def _make_row(self, cells, header=False):
        row = [None]
        for index, (x_pos, width) in enumerate(_satellite_column_layout()):
            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP if index == 1 else RT_HALIGN_CENTER | RT_VALIGN_TOP
            background = 0x606000 if header else 0x202020
            toggle_icon = None
            if not header and index == 0 and cells[index] in (_("Yes"), _("No")):
                toggle_icon = _row_toggle_pixmap("Yes" if cells[index] == _("Yes") else "No")
                if toggle_icon is None:
                    background = 0x006f1f if cells[index] == _("Yes") else 0x8d1616
            row.append(MultiContentEntryText(
                pos=(x_pos, 0), size=(width, 44), font=0, flags=flags,
                text=_menu_text("" if toggle_icon is not None else cells[index]),
                color=0xffffff,
                color_sel=_CELL_SELECTED_COLOR,
                backcolor=background,
                backcolor_sel=0x606000, border_width=1, border_color=0xffffff))
            if toggle_icon is not None and MultiContentEntryPixmapAlphaTest is not None:
                row.append(MultiContentEntryPixmapAlphaTest(
                    pos=(x_pos + 2, 7), size=(81, 30), png=toggle_icon))
        return row


def reload_e2_database(removed_refs=None):
    """Synchronize edited services with Enigma2's in-memory database.

    ``apply_plan`` has already saved lamedb and bouquet files atomically.  If
    the receiver exposes ``removeService``, remove the same references from
    its memory first and then save that verified state.  This prevents a
    shutdown from writing the old in-memory service list back to lamedb.
    """
    actions = []
    errors = []
    removed_refs = list(removed_refs or [])
    if eDVBDB is None:
        log_message("Enigma2 database sync skipped: eDVBDB is unavailable")
        return {"actions": actions, "errors": errors}
    try:
        database = eDVBDB.getInstance()
    except Exception as error:
        log_message("Enigma2 database sync failed to open database: %s" % error)
        return {"actions": actions, "errors": [str(error)]}

    memory_removals = 0
    remove_method = getattr(database, "removeService", None)
    if removed_refs and remove_method is not None and eServiceReference is not None:
        for ref in removed_refs:
            try:
                # DreamOS Python 2's SWIG binding requires a UTF-8 byte
                # string (std::string), while Python 3 requires ``str``.
                remove_method(eServiceReference(_menu_text(ref)))
                memory_removals += 1
            except Exception as error:
                errors.append("remove service %s: %s" % (ref, error))
        if memory_removals:
            actions.append("removed %d services from memory" % memory_removals)
            log_message("Enigma2 memory cleanup removed %d service references" % memory_removals)
    elif removed_refs:
        log_message("Enigma2 memory cleanup API is unavailable; using reload-only sync")

    # Reload the receiver's lists from the files verified by the cleanup
    # engine. This also refreshes bouquet references changed by the plan.
    for method_name, label in (("reloadServicelist", "service list"),
                               ("reloadBouquets", "bouquets")):
        method = getattr(database, method_name, None)
        if method is None:
            continue
        try:
            method()
            actions.append("reloaded %s" % label)
        except Exception as error:
            errors.append("%s: %s" % (label, error))

    # Save only after in-memory removals have succeeded.  This is different
    # from the former unsafe save of an untouched, stale service list.
    if memory_removals and not errors:
        for method_name, label in (("saveServicelist", "service list"),
                                   ("saveBouquets", "bouquets")):
            method = getattr(database, method_name, None)
            if method is None:
                continue
            try:
                method()
                actions.append("saved %s" % label)
            except Exception as error:
                errors.append("%s: %s" % (label, error))

    if errors:
        log_message("Enigma2 database sync warnings: %s" % "; ".join(errors))
    else:
        log_message("Enigma2 database sync completed: %s" % ", ".join(actions or ["no API available"]))
    return {"actions": actions, "errors": errors}


def _review_list_width():
    """Return the review-list width from the same responsive skin geometry.

    This must use the plugin window width rather than the full television
    width.  Otherwise, on a 1280-pixel desktop the trailing channel columns
    are drawn beyond the right edge of the 870-pixel plugin window.
    """
    width = 940
    if getDesktop is not None:
        try:
            screen_width = getDesktop(0).size().width()
            screen_height = getDesktop(0).size().height()
            if screen_width <= 1280:
                width = 870
            elif screen_width >= 1920 and screen_height >= 1080:
                width = 1280
        except Exception:
            pass
    return width - 66


def _skin_border(x_pos, y_pos, width, height, thickness=3):
    """Return a hollow, high-contrast frame for separating screen sections."""
    right = x_pos + width - thickness
    bottom = y_pos + height - thickness
    return """
        <eLabel position="%d,%d" size="%d,%d" backgroundColor="#E00000" />
        <eLabel position="%d,%d" size="%d,%d" backgroundColor="#E00000" />
        <eLabel position="%d,%d" size="%d,%d" backgroundColor="#E00000" />
        <eLabel position="%d,%d" size="%d,%d" backgroundColor="#E00000" />
    """ % (
        x_pos, y_pos, width, thickness,
        x_pos, bottom, width, thickness,
        x_pos, y_pos, thickness, height,
        right, y_pos, thickness, height)


def _build_screen_skin(screen_name, screen_kind, width=940, height=625):
    """Render one of the independently configured plugin screens."""
    if getDesktop is not None:
        try:
            screen_width = getDesktop(0).size().width()
            screen_height = getDesktop(0).size().height()
            if screen_width <= 1280:
                # 720p images have enough vertical room for a substantially
                # taller review list while keeping a small border around the
                # screen.  This adds about three visible channel rows.
                width, height = 870, min(690, screen_height - 30)
            elif screen_width >= 1920 and screen_height >= 1080:
                # Full-HD screens can use a much taller list (about 18 rows)
                # while retaining a 50-pixel television-safe border.
                width, height = 1280, min(980, screen_height - 70)
            else:
                height = min(820, screen_height - 60)
        except Exception:
            pass
    # The list itself remains a stock MenuList so the active image skin keeps
    # control of it.  Only the supplied colour-key button images are used.
    frame_padding = 8
    main_screen = screen_kind == "main"
    info_border = ""
    help_border = ""
    help_widget = ""
    help_x, help_y, help_width, help_height = 0, 0, 1, 1
    button_width = max(150, (width - 95) // 4)
    # Leave a clear margin below the button frame instead of attaching it to
    # the bottom edge of the screen.
    button_y = height - 82
    is_satellite_screen = screen_kind == "satellite"
    is_review_screen = screen_kind in ("review", "preview")
    if is_satellite_screen:
        # The satellite picker combines its two instruction areas into one
        # larger, two-line status box below the list.
        info_border = ""
        info_x, info_y, info_width, info_height = 0, 0, 1, 1
        summary_x, summary_y, summary_width, summary_height = 0, 0, 1, 1
        # Keep the table visually separated from the title bar.
        list_y = 82
        status_height = 80
        status_padding = 12
        status_font = 22
        status_y = button_y - 101
    elif is_review_screen:
        # The channel screen now has just a satellite line and the green
        # removal summary, so its information frame can be comfortably short.
        info_y = 62
        info_height = 78
        info_x, info_width = 33, width - (2 * frame_padding) - 50
        summary_x, summary_y = 33, 101
        summary_width, summary_height = width - 66, 30
        list_y = 156
        status_height = 58
        status_padding = 12
        status_font = 22
        status_y = button_y - 79
        info_border = _skin_border(25, info_y, width - 50, info_height)
    else:
        if main_screen:
            # The technical image/Python summary is not useful in the main
            # workflow. Hide it and give the menu its space instead.
            info_x, info_y, info_width, info_height = 0, 0, 1, 1
            summary_x, summary_y, summary_width, summary_height = 0, 0, 1, 1
            list_y = 62
            status_height = 50
            status_padding = frame_padding
            status_font = 22
            status_y = button_y - 70
            # Reserve a framed two-line help area below the menu. It uses the
            # exact same red three-pixel frame as the other main-screen areas.
            help_height = 68
            help_x, help_width = 33, width - (2 * frame_padding) - 50
            help_y = status_y - help_height - 14
            help_border = _skin_border(25, help_y, width - 50, help_height)
            help_widget = ('<widget name="help" position="%d,%d" size="%d,%d" '
                           'font="Regular;22" foregroundColor="#FFFFFF" '
                           'valign="center" />') % (
                               help_x, help_y + frame_padding, help_width,
                               help_height - (2 * frame_padding))
        else:
            info_y = 62
            info_height = 110
            info_x, info_width = 33, width - (2 * frame_padding) - 50
            summary_x, summary_y, summary_width, summary_height = 0, 0, 1, 1
            list_y = 188
            status_height = 50
            status_padding = frame_padding
            status_font = 20
            status_y = button_y - 70
            info_border = _skin_border(25, info_y, width - 50, info_height)
    if main_screen:
        list_height = max(150, help_y - list_y - 14)
    else:
        list_height = max(180, status_y - list_y - 14)
    button_path = os.path.join(PLUGIN_PATH, "buttons")
    # Bold frames make the informational, action, and button areas distinct
    # from the channel/satellite table on every supported image skin.
    list_border = _skin_border(25, list_y, width - 50, list_height)
    status_border = _skin_border(25, status_y, width - 50, status_height)
    # Nine pixels of empty space above and below the button images keeps
    # them visually separate from the surrounding red frame.
    buttons_border = _skin_border(25, button_y - 9, width - 50, 63)
    if main_screen:
        # Main screen: Options uses the supplied menu button, while blue
        # keeps the channel-file synchronization action.
        green_button_x = 39 + button_width
        yellow_button_x = 44 + (2 * button_width)
        blue_button_x = 49 + (3 * button_width)
        yellow_button_image = "menu.png"
    elif is_satellite_screen or screen_kind in ("preview", "options"):
        # Satellite selection and final review use Cancel (red) and
        # Select/Execute (green) only.
        green_button_x = 39 + button_width
        yellow_button_x = width + 10
        blue_button_x = width + 10
        yellow_button_image = "yellow.png"
    else:
        green_button_x = 39 + button_width
        yellow_button_x = 44 + (2 * button_width)
        blue_button_x = 49 + (3 * button_width)
        yellow_button_image = "yellow.png"
    return """
    <screen name="%s" position="center,center" size="%d,%d" title="" flags="wfNoBorder" backgroundColor="#151515">
        <eLabel position="0,0" size="%d,52" backgroundColor="#383838" />
        <widget name="title" position="25,0" size="%d,52" font="Regular;30" foregroundColor="#FFFFFF" valign="center" transparent="1" zPosition="1" />
        <widget name="version" position="%d,0" size="185,52" font="Regular;25" foregroundColor="#FFFFFF" halign="right" valign="center" transparent="1" zPosition="1" />
        %s
        <widget name="info" position="%d,%d" size="%d,%d" font="Regular;22" />
        <widget name="summary" position="%d,%d" size="%d,%d" font="Regular;23" foregroundColor="#00D000" valign="center" transparent="1" />
        %s
        <widget name="list" position="%d,%d" size="%d,%d" scrollbarMode="showOnDemand" />
        %s
        %s
        %s
        <widget name="status" position="33,%d" size="%d,%d" font="Regular;%d" valign="center" />
        %s
        <ePixmap position="34,%d" size="%d,45" pixmap="%s/red.png" alphatest="on" scale="1" />
        <ePixmap position="%d,%d" size="%d,45" pixmap="%s/green.png" alphatest="on" scale="1" />
        <ePixmap position="%d,%d" size="%d,45" pixmap="%s/%s" alphatest="on" scale="1" />
        <ePixmap position="%d,%d" size="%d,45" pixmap="%s/blue.png" alphatest="on" scale="1" />
        <widget name="key_red" position="34,%d" size="%d,45" font="Regular;24" foregroundColor="#FFFFFF" halign="center" valign="center" transparent="1" zPosition="1" />
        <widget name="key_green" position="%d,%d" size="%d,45" font="Regular;24" foregroundColor="#000000" halign="center" valign="center" transparent="1" zPosition="1" />
        <widget name="key_yellow" position="%d,%d" size="%d,45" font="Regular;24" foregroundColor="#000000" halign="center" valign="center" transparent="1" zPosition="1" />
        <widget name="key_blue" position="%d,%d" size="%d,45" font="Regular;24" foregroundColor="#FFFFFF" halign="center" valign="center" transparent="1" zPosition="1" />
    </screen>
    """ % (
        screen_name, width, height, width, width - 225, width - 210,
        info_border, info_x, info_y + (frame_padding if info_border else 0), info_width,
        info_height - (2 * frame_padding) if info_border else info_height,
        summary_x, summary_y, summary_width, summary_height,
        list_border, 25 + frame_padding, list_y + frame_padding, width - (2 * frame_padding) - 50, list_height - (2 * frame_padding),
        help_border, help_widget,
        status_border, status_y + status_padding, width - (2 * frame_padding) - 50,
        status_height - (2 * status_padding), status_font, buttons_border,
        button_y, button_width, button_path,
        green_button_x, button_y, button_width, button_path,
        yellow_button_x, button_y, button_width, button_path, yellow_button_image,
        blue_button_x, button_y, button_width, button_path,
        button_y, button_width,
        green_button_x, button_y, button_width,
        yellow_button_x, button_y, button_width,
        blue_button_x, button_y, button_width)


def _satellite_skin(screen_name, width=940, height=625):
    """Dedicated layout for the satellite-selection screen."""
    return _build_screen_skin(screen_name, "satellite", width, height)


def _channels_skin(screen_name, width=940, height=625):
    """Dedicated layout for the duplicate-channels review screen."""
    return _build_screen_skin(screen_name, "review", width, height)


def _main_skin(screen_name, width=940, height=625):
    """Dedicated layout for the plugin's main-menu screen."""
    return _build_screen_skin(screen_name, "main", width, height)


def _options_skin(screen_name, width=940, height=625):
    """Dedicated layout for the plugin-options screen."""
    return _build_screen_skin(screen_name, "options", width, height)


def _removal_preview_skin(screen_name, width=940, height=625):
    """Dedicated layout for the final removal-review screen."""
    return _build_screen_skin(screen_name, "preview", width, height)


def _log_skin(screen_name, width=940, height=625):
    """Dedicated layout for the scrollable operation-log screen."""
    return _build_screen_skin(screen_name, "log", width, height)


def _log_wrap_width():
    """Choose a readable log-line width for HD and Full-HD Enigma2 screens."""
    width = 78
    if getDesktop is not None:
        try:
            desktop_width = getDesktop(0).size().width()
            desktop_height = getDesktop(0).size().height()
            if desktop_width <= 1280:
                width = 66
            elif desktop_width >= 1920 and desktop_height >= 1080:
                width = 106
        except Exception:
            pass
    return width


def _wrap_log_lines(text, width=None):
    """Wrap long paths and messages so every log line remains visible."""
    if width is None:
        width = _log_wrap_width()
    width = max(24, int(width))
    if text is None:
        return []
    if not isinstance(text, _unicode_type):
        try:
            text = _unicode_type(text)
        except Exception:
            text = ""
    rows = []
    for raw_line in text.splitlines() if text else []:
        line = raw_line or " "
        while len(line) > width:
            split_at = line.rfind(" ", 0, width + 1)
            if split_at <= 0:
                split_at = width
            rows.append(line[:split_at])
            line = line[split_at:].lstrip()
        rows.append(line)
    return rows


def _important_log_line(line):
    """Return True for successful operation milestones worth highlighting."""
    value = line.lower()
    markers = (
        "backup created",
        "backup restored",
        "cleanup completed",
        "verification passed",
        "sync completed",
        "synchronized successfully",
        "update completed",
        "update-check timer",
    )
    return any(marker in value for marker in markers)


class E2DuplicateChannelsManagerSatellitePicker(Screen):
    """Satellite picker without ChoiceBox numeric-shortcut badges."""
    skinName = "E2DuplicateChannelsManagerSatellitePicker"
    skin = _satellite_skin(skinName)

    def __init__(self, session, satellites, selected=None):
        self.skinName = "E2DuplicateChannelsManagerSatellitePicker"
        Screen.__init__(self, session)
        self.entries = [(None, _("All satellites"))] + list(satellites or [])
        self.selected = selected
        self.checked_index = 0
        for index, (position, _label) in enumerate(self.entries):
            if position == selected:
                self.checked_index = index
                break
        self["title"] = Label(_("Satellite Screen"))
        self["version"] = Label("")
        self["info"] = Label(_("OK changes Yes/No. Green confirms the selected satellite."))
        self["summary"] = Label("")
        self["list"] = E2DuplicateChannelsManagerSatelliteList()
        self["status"] = Label(_("Use arrows and OK to select/unselect.\nPress Green to confirm the satellite."))
        self["key_red"] = Label(_("Cancel"))
        self["key_green"] = Label(_("Select"))
        self["key_yellow"] = Label("")
        self["key_blue"] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "cancel": self.cancel,
            "red": self.cancel,
            "ok": self.toggle_selected,
            "green": self.confirm,
        }, -2)
        self.refresh_list()

    def refresh_list(self):
        previous_index = _menu_index(self["list"])
        self["list"].checked_index = self.checked_index
        self["list"].set_entries(self.entries)
        try:
            # Row 0 contains only the column headings.
            self["list"].moveToIndex(max(1, previous_index))
        except Exception:
            pass

    def confirm(self):
        index = self.checked_index
        if 0 <= index < len(self.entries):
            position, label = self.entries[index]
            self.close((label, position))

    def toggle_selected(self):
        index = _menu_index(self["list"]) - 1
        if 0 <= index < len(self.entries):
            self.checked_index = index
            self.refresh_list()

    def cancel(self):
        self.close(None)


class E2DuplicateChannelsManagerRemovalPreviewScreen(Screen):
    """Final, read-only review of every service affected by a cleanup plan."""
    skinName = "E2DuplicateChannelsManagerRemovalPreviewScreen"
    skin = _removal_preview_skin(skinName)

    def __init__(self, session, plan):
        self.skinName = "E2DuplicateChannelsManagerRemovalPreviewScreen"
        Screen.__init__(self, session)
        self.plan = plan
        groups = list(plan.get("chosen_groups") or [])
        remove_count = len(plan.get("remove_refs") or [])
        kept_count = 0
        rows = [(_("Action"), _("Channel name"), _("Service reference"))]
        for group in groups:
            name = group.get("name") or _("Unnamed service")
            for ref in group.get("removed") or []:
                rows.append((_("REMOVE"), name, ref))
            kept = list(group.get("kept") or [])
            if not kept and group.get("preferred"):
                kept = [group.get("preferred")]
            for ref in kept:
                rows.append((_("KEEP"), name, ref))
            kept_count += len(kept)
        if len(rows) == 1:
            rows.append(("", _("No channels are selected for removal."), ""))

        self["title"] = Label(_("Removal Review"))
        self["version"] = Label("")
        self["info"] = Label(_("Channels to remove: %d") % remove_count)
        self["summary"] = Label(_("Channels kept after removal: %d") % kept_count)
        self.multi_column_list = bool(eListboxPythonMultiContent and MultiContentEntryText and gFont)
        self.preview_column_layout = _removal_preview_column_layout(_review_list_width())
        if self.multi_column_list:
            self["list"], self.multi_column_list = _multi_content_menu_list()
            _configure_multicontent(self["list"], 22, 40)
        if self.multi_column_list:
            self["list"].setList([self._make_table_row(cells, index == 0)
                                  for index, cells in enumerate(rows)])
        else:
            self["list"] = _plain_menu_list([_menu_text("  |  ".join(cells)) for cells in rows])
        self["status"] = Label(_("Review REMOVE and KEEP rows. KEEP rows are protected and cannot be deleted. Green creates a backup, then executes; Red cancels."))
        self["key_red"] = Label(_("Cancel"))
        self["key_green"] = Label(_("Execute"))
        self["key_yellow"] = Label("")
        self["key_blue"] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "cancel": self.cancel,
            "red": self.cancel,
            "green": self.confirm,
        }, -2)

    def _make_table_row(self, cells, header=False):
        """Create a bordered row matching the channel-review table."""
        entries = [cells]
        for index, (x_pos, width) in enumerate(self.preview_column_layout):
            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP
            if index == 0:
                flags = RT_HALIGN_CENTER | RT_VALIGN_TOP
            background = _CELL_SELECTED_BACKGROUND if header else _CELL_BACKGROUND
            if not header and index == 0:
                if cells[index] == _("REMOVE"):
                    background = _TOGGLE_OFF_BACKGROUND
                elif cells[index] == _("KEEP"):
                    background = _TOGGLE_ON_BACKGROUND
            entries.append(MultiContentEntryText(
                pos=(x_pos, 0), size=(width, 40), font=0, flags=flags,
                text=_menu_text(cells[index]), color=_CELL_COLOR,
                color_sel=_CELL_SELECTED_COLOR, backcolor=background,
                backcolor_sel=_CELL_SELECTED_BACKGROUND, border_width=1,
                border_color=_CELL_BORDER))
        return entries

    def confirm(self):
        self.close(True)

    def cancel(self):
        self.close(False)


class E2DuplicateChannelsManagerReviewScreen(Screen):
    # Unique internal skin name prevents installed image skins from overriding this screen.
    skinName = "E2DuplicateChannelsManagerReviewScreen"
    skin = _channels_skin(skinName)

    def __init__(self, session, scan_result):
        self.skinName = "E2DuplicateChannelsManagerReviewScreen"
        Screen.__init__(self, session)
        self.scan_result = scan_result
        self.satellite_filter = scan_result.get("satellite_filter")
        self.visible_groups = _removable_groups(scan_result)
        self.selected_group_keys = set()
        self.selected_group_count = 0
        self.selected_removal_count = 0
        # Building a plan and preview reads every bouquet/database file.  Do
        # that only when the user opens the final review, never on every
        # ON/OFF press.
        self.plan = None
        self.preview = None
        self["title"] = Label(_("Channels Screen"))
        self["version"] = Label("")
        self["info"] = Label("")
        self["summary"] = Label("")
        self.group_rows = []
        self.list_rows = []
        self.pending_row_refresh = False
        # Restore the full XMLupdate-style table shown in the original UI.
        self.multi_column_list = bool(eListboxPythonMultiContent and MultiContentEntryText and gFont)
        self.review_column_layout = _review_column_layout(_review_list_width())
        if self.multi_column_list:
            self["list"], self.multi_column_list = _multi_content_menu_list()
            _configure_multicontent(self["list"], 22, 40)
        if not self.multi_column_list:
            self["list"] = _plain_menu_list()
        self["status"] = Label("")
        self["key_red"] = Label(_("Close"))
        self["key_green"] = Label(_("Review selected"))
        self["key_yellow"] = Label(_("Satellite"))
        self["key_blue"] = Label(_("All / none"))
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.confirm_apply,
                "yellow": self.choose_satellite,
                "blue": self.toggle_all,
                "ok": self.toggle_current,
            }, -2)
        self.refresh_list()

    def _visible_groups(self):
        # The scan result does not change while rows are toggled, so retain
        # this filtered list instead of rebuilding it on every key press.
        return self.visible_groups

    def _invalidate_plan(self):
        """Discard a cached plan after the selected rows have changed."""
        self.plan = None
        self.preview = None

    def _build_plan(self):
        """Build the selected cleanup plan only when it is actually needed."""
        self.plan = build_plan(self.scan_result, clean_exact=False,
                               selected_group_keys=self.selected_group_keys)
        return self.plan

    def _build_preview(self):
        """Read the channel files once, immediately before review/apply."""
        if self.plan is None:
            self._build_plan()
        if self.preview is None:
            self.preview = preview_changes(self.plan)
        return self.preview

    def _update_header(self, visible_groups=None):
        if visible_groups is None:
            visible_groups = self._visible_groups()
        if self.satellite_filter is None:
            satellite = _("All satellites")
        else:
            satellite = satellite_label(self.satellite_filter)
        self["info"].setText(_menu_text(_("Satellite: %s | Selected: %d/%d") % (
            satellite, self.selected_group_count, len(visible_groups))))
        self["summary"].setText(_menu_text(_("Services selected for removal: %d") %
                                            self.selected_removal_count))
        self["status"].setText(_menu_text(_("OK: select/unselect channel | Green: review selected | Yellow: satellite | Blue: select all/none. Picon-linked services are always protected.")))

    def _group_row_cells(self, group):
        """Build one row without redrawing the complete review table."""
        picon_count = len([item for item in group.get("items", []) if item.get("picons")])
        remove_count = len(group.get("removable", []))
        marker = _("Picon") if group.get("preferred_reason") == "picon" else _("First")
        checked = _("Yes") if group.get("key") in self.selected_group_keys else _("No")
        return (checked, group.get("name") or _("Unnamed service"), marker,
                str(len(group.get("items", []))), str(remove_count), str(picon_count))

    def refresh_list(self):
        # The header and rows use identical pixel coordinates, so every
        # heading starts exactly above its data column on every DreamOS skin.
        previous_index = _menu_index(self["list"])
        visible_groups = self._visible_groups()
        rows = [_REVIEW_COLUMNS]
        self.group_rows = [None]
        self.selected_group_count = 0
        self.selected_removal_count = 0
        # Only list names which have at least one unprotected service
        # reference to remove.  A group consisting solely of Picon-linked
        # services is intentionally omitted: it is protected and cannot be
        # cleaned safely.
        for group in visible_groups:
            if group.get("key") in self.selected_group_keys:
                self.selected_group_count += 1
                self.selected_removal_count += len(group.get("removable", []))
            rows.append(self._group_row_cells(group))
            self.group_rows.append(group)
        if len(rows) == 1:
            rows.append(("", _("No removable duplicate channel names were found."), "", "", "", ""))
            self.group_rows.append(None)
        if self.multi_column_list:
            self.list_rows = [self._make_table_row(cells, index == 0)
                              for index, cells in enumerate(rows)]
        else:
            self.list_rows = [_menu_text("  ".join(cells)) for cells in rows]
        self["list"].setList(self.list_rows)
        self.pending_row_refresh = False
        try:
            self["list"].moveToIndex(min(max(1, previous_index), len(rows) - 1))
        except Exception:
            pass
        self._update_header(visible_groups)

    def _refresh_current_row(self, index, group, update_header=True):
        """Refresh one row without rebuilding the review table."""
        if index <= 0 or index >= len(self.list_rows):
            return
        cells = self._group_row_cells(group)
        if self.multi_column_list:
            row = self._make_table_row(cells)
        else:
            row = _menu_text("  ".join(cells))
        try:
            self.list_rows[index] = row
            invalidate = getattr(getattr(self["list"], "l", None), "invalidateEntry", None)
            if invalidate is not None:
                invalidate(index)
            else:
                # Keep the changed state until the next deliberate re-scan on
                # older list implementations that cannot invalidate one row.
                self.pending_row_refresh = True
        except Exception:
            self.pending_row_refresh = True
        if update_header:
            self._update_header()

    def _make_table_row(self, cells, header=False):
        # Match XMLupdatebyiet5's robust multi-content row format. The first
        # element is row data; remaining elements are bordered cell renderers.
        entries = [cells]
        for index, (x_pos, width) in enumerate(self.review_column_layout):
            flags = RT_HALIGN_CENTER | RT_VALIGN_TOP
            if index == 1:
                flags = RT_HALIGN_LEFT | RT_VALIGN_TOP
            color = _CELL_SELECTED_COLOR if header else _CELL_COLOR
            background = _CELL_SELECTED_BACKGROUND if header else _CELL_BACKGROUND
            toggle_icon = None
            if not header and index == 0 and cells[index] in (_("Yes"), _("No")):
                toggle_icon = _row_toggle_pixmap("Yes" if cells[index] == _("Yes") else "No")
                if toggle_icon is None:
                    color = _TOGGLE_TEXT
                    background = (_TOGGLE_ON_BACKGROUND if cells[index] == _("Yes")
                                  else _TOGGLE_OFF_BACKGROUND)
            entries.append(MultiContentEntryText(
                pos=(x_pos, 0), size=(width, 40), font=0,
                flags=flags,
                text=_menu_text("" if toggle_icon is not None else cells[index]),
                color=color,
                color_sel=_CELL_SELECTED_COLOR,
                backcolor=background,
                backcolor_sel=_CELL_SELECTED_BACKGROUND, border_width=1,
                border_color=_CELL_BORDER))
            if toggle_icon is not None and MultiContentEntryPixmapAlphaTest is not None:
                entries.append(MultiContentEntryPixmapAlphaTest(
                    pos=(x_pos + 2, 5), size=(81, 30), png=toggle_icon))
        return entries

    def current_group(self):
        index = _menu_index(self["list"])
        if 0 <= index < len(self.group_rows):
            return self.group_rows[index]
        return None

    def toggle_current(self):
        group = self.current_group()
        if not group:
            return
        index = _menu_index(self["list"])
        key = group.get("key")
        if key in self.selected_group_keys:
            self.selected_group_keys.remove(key)
            self.selected_group_count = max(0, self.selected_group_count - 1)
            self.selected_removal_count = max(0, self.selected_removal_count -
                                              len(group.get("removable", [])))
        else:
            self.selected_group_keys.add(key)
            self.selected_group_count += 1
            self.selected_removal_count += len(group.get("removable", []))
        self._invalidate_plan()
        self._refresh_current_row(index, group)

    def toggle_all(self):
        visible_groups = self._visible_groups()
        visible_keys = set(group.get("key") for group in visible_groups)
        if visible_keys and visible_keys.issubset(self.selected_group_keys):
            self.selected_group_keys.difference_update(visible_keys)
            self.selected_group_count = 0
            self.selected_removal_count = 0
        else:
            self.selected_group_keys.update(visible_keys)
            self.selected_group_count = len(visible_groups)
            self.selected_removal_count = sum(len(group.get("removable", []))
                                              for group in visible_groups)
        self._invalidate_plan()
        for index, group in enumerate(visible_groups, 1):
            self._refresh_current_row(index, group, update_header=False)
        self._update_header(visible_groups)

    def choose_satellite(self):
        self.session.openWithCallback(self._satellite_chosen,
                                      E2DuplicateChannelsManagerSatellitePicker,
                                      self.scan_result.get("satellites", []),
                                      self.satellite_filter)

    def _satellite_chosen(self, answer):
        if answer is None:
            return
        self.satellite_filter = answer[1] if isinstance(answer, (tuple, list)) else answer
        try:
            self.scan_result = scan_duplicates(satellite=self.satellite_filter)
            self.satellite_filter = self.scan_result.get("satellite_filter")
            self.visible_groups = _removable_groups(self.scan_result)
            self.selected_group_keys = set()
            self._invalidate_plan()
            self.refresh_list()
        except Exception as error:
            self.session.open(MessageBox, _menu_text(_("Satellite scan failed:\n%s") % error),
                              MessageBox.TYPE_ERROR, timeout=14)

    def show_group_details(self):
        group = self.current_group()
        if not group:
            return
        lines = [_('Channel: %s') % group.get("name"), ""]
        for item in group.get("items", []):
            state = _("PROTECTED BY PICON") if item.get("picons") else _("No Picon")
            if item.get("ref") == group.get("preferred"):
                state += " / " + _("KEEP")
            elif item.get("ref") in group.get("removable", []):
                state += " / " + _("REMOVE")
            else:
                state += " / " + _("KEEP FOR SAFETY")
            lines.append("%s\n%s" % (state, item.get("ref")))
        # MessageBox uses eLabel on DreamOS OE2.5, which also accepts only
        # UTF-8 byte strings under Python 2.  Without this conversion, opening
        # details for a channel name raises eLabel_setText TypeError.
        self.session.open(MessageBox, _menu_text("\n\n".join(lines)),
                          MessageBox.TYPE_INFO, timeout=20)

    def show_preview(self):
        preview = self._build_preview()
        text = _("Files to modify: %d\nServices removed from lamedb/lamedb5: %d\nBouquet references replaced: %d\nRepeated bouquet entries removed: %d\nProtected Picon conflicts left untouched: %d\n\nA complete backup is created before writing. No Picon file is changed.") % (
            len(preview["changed_files"]), preview["removed_services"],
            preview["replaced"], preview["removed_exact"],
            len(preview.get("protected_conflicts", [])))
        self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=16)

    def confirm_apply(self):
        if not self.selected_group_keys:
            self.session.open(MessageBox, _menu_text(_("Select at least one duplicate channel with OK first.")),
                              MessageBox.TYPE_INFO, timeout=8)
            return
        self._build_plan()
        preview = self._build_preview()
        if not preview["changed_files"]:
            self.session.open(MessageBox, _menu_text(_("Nothing needs to be changed.")),
                              MessageBox.TYPE_INFO, timeout=7)
            return
        self.session.openWithCallback(self._apply_answer,
                                      E2DuplicateChannelsManagerRemovalPreviewScreen,
                                      self.plan)

    def _apply_answer(self, answer):
        if not answer:
            return
        try:
            # ``apply_plan`` repeats the file preview as a final safeguard in
            # case Enigma2 changed the database while the review was visible.
            result = apply_plan(self.plan)
            sync_result = reload_e2_database(self.plan.get("remove_refs", []))
            text = _("Completed successfully.\nModified files: %d\nServices removed: %d\nBouquet duplicates removed: %d\nReferences replaced: %d\nProtected conflicts: %d\nBackup: %s") % (
                len(result["modified"]), result["removed_services"], result["removed_exact"],
                result["replaced"], len(result.get("protected_conflicts", [])), result.get("backup") or _("Not required"))
            if sync_result.get("errors"):
                text += _("\n\nChannel-file sync warnings:\n%s") % "\n".join(sync_result["errors"])
            elif sync_result.get("actions"):
                text += _("\n\nChannel files synchronized successfully:\n%s") % "\n".join(sync_result["actions"])
            else:
                text += _("\n\nChannel-file sync is not available on this image.")
            log_message("Cleanup review completed; returning to main screen")
            # Do not re-scan and redraw the old review list here.  It is both
            # confusing after deletion and unnecessarily expensive.  Closing
            # this screen returns directly to the still-open main screen.
            self.session.openWithCallback(self._finish_apply, MessageBox,
                                          _menu_text(text), MessageBox.TYPE_INFO,
                                          timeout=18)
        except Exception as error:
            self.session.open(MessageBox, _menu_text(_("Operation failed and rollback was attempted:\n%s") % error),
                              MessageBox.TYPE_ERROR, timeout=18)

    def _finish_apply(self, unused_answer):
        self.close()

    def backup_menu(self):
        choices = [(_("Create a manual backup now"), "create")]
        for path in list_backups()[:10]:
            choices.append((_backup_display_label(path), path))
        self.session.openWithCallback(self._backup_choice, ChoiceBox, title=_("Backup and restore"), list=choices)

    def _backup_choice(self, answer):
        if answer is None:
            return
        action = answer[1]
        if action == "create":
            try:
                path = create_backup(reason="manual")
                self.session.open(MessageBox, _("Backup created:\n%s") % path, MessageBox.TYPE_INFO, timeout=10)
            except Exception as error:
                self.session.open(MessageBox, _("Backup failed:\n%s") % error, MessageBox.TYPE_ERROR, timeout=10)
        else:
            self.session.openWithCallback(lambda yes: self._restore_answer(action, yes), MessageBox,
                                          _("Restore this backup? A new safety backup will be created first.\n%s") % _backup_display_label(action),
                                          MessageBox.TYPE_YESNO)

    def _restore_answer(self, path, answer):
        if not answer:
            return
        try:
            safety = restore_backup(path, safety_backup=True)
            reload_e2_database()
            self.session.open(MessageBox, _("Backup restored.\nPre-restore safety backup: %s") % safety,
                              MessageBox.TYPE_INFO, timeout=14)
        except Exception as error:
            self.session.open(MessageBox, _("Restore failed:\n%s") % error, MessageBox.TYPE_ERROR, timeout=14)


class E2DuplicateChannelsManagerOptionsScreen(Screen):
    """Independent Options screen for persistent plugin preferences."""
    skinName = "E2DuplicateChannelsManagerOptionsScreen"
    skin = _options_skin(skinName)

    def __init__(self, session):
        self.skinName = "E2DuplicateChannelsManagerOptionsScreen"
        Screen.__init__(self, session)
        self.update_check_enabled = _update_check_enabled()
        self["title"] = Label(_("Options"))
        self["version"] = Label("")
        self["info"] = Label(_("Plugin options"))
        self["summary"] = Label("")
        self.multi_column_list = bool(eListboxPythonMultiContent and MultiContentEntryText and gFont)
        if self.multi_column_list:
            self["list"], self.multi_column_list = _multi_content_menu_list()
            _configure_multicontent(self["list"], 26, 45)
        if not self.multi_column_list:
            self["list"] = _plain_menu_list()
        self["status"] = Label(_("OK or Green: switch ON/OFF. Red: back."))
        self["key_red"] = Label(_("Back"))
        self["key_green"] = Label(_("Switch"))
        self["key_yellow"] = Label("")
        self["key_blue"] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "cancel": self.close,
            "red": self.close,
            "ok": self.toggle_update_check,
            "green": self.toggle_update_check,
        }, -2)
        self.refresh_list()

    def refresh_list(self):
        label = _("Check for new version at startup")
        if self.multi_column_list:
            row = [(label, self.update_check_enabled)]
            text_width = _review_list_width() - 110
            entry = [row[0]]
            entry.append(MultiContentEntryText(
                pos=(18, 0), size=(text_width, 45), font=0,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=_menu_text(label),
                color=_CELL_COLOR, color_sel=_CELL_SELECTED_COLOR,
                backcolor=_CELL_BACKGROUND, backcolor_sel=_CELL_SELECTED_BACKGROUND))
            icon = _row_toggle_pixmap("Yes" if self.update_check_enabled else "No")
            if icon is not None and MultiContentEntryPixmapAlphaTest is not None:
                entry.append(MultiContentEntryPixmapAlphaTest(
                    pos=(text_width + 23, 7), size=(81, 30), png=icon))
            self["list"].setList([entry])
        else:
            state = _("ON") if self.update_check_enabled else _("OFF")
            self["list"].setList([_menu_text("%s: %s" % (label, state))])

    def toggle_update_check(self):
        enabled = not self.update_check_enabled
        try:
            _set_update_check_enabled(enabled)
        except Exception as error:
            self.session.open(MessageBox, _menu_text(_("Could not save update-check setting:\n%s") % error),
                              MessageBox.TYPE_ERROR, timeout=10)
            return
        self.update_check_enabled = enabled
        log_message("Update check at startup %s" % ("enabled" if enabled else "disabled"))
        self.refresh_list()


class E2DuplicateChannelsManagerLogScreen(Screen):
    """Scrollable, wrapped viewer for the plugin operation log."""
    skinName = "E2DuplicateChannelsManagerLogScreen"
    skin = _log_skin(skinName)

    def __init__(self, session):
        self.skinName = "E2DuplicateChannelsManagerLogScreen"
        Screen.__init__(self, session)
        self["title"] = Label(_("Operation Log"))
        self["version"] = Label("")
        self["info"] = Label(_("Latest operation messages"))
        self["summary"] = Label("")
        self.multi_column_list = bool(eListboxPythonMultiContent and
                                      MultiContentEntryText and gFont)
        if self.multi_column_list:
            self["list"], self.multi_column_list = _multi_content_menu_list()
            _configure_multicontent(self["list"], 22, 34)
        if not self.multi_column_list:
            self["list"] = _plain_menu_list([], wrap=False)
        self["status"] = Label(_("Up/Down: scroll | Yellow: top | Blue: bottom | Green: refresh."))
        self["key_red"] = Label(_("Close"))
        self["key_green"] = Label(_("Refresh"))
        self["key_yellow"] = Label(_("Top"))
        self["key_blue"] = Label(_("Bottom"))
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.refresh_log,
                "yellow": self.move_top,
                "blue": self.move_bottom,
                "up": self.move_up,
                "down": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
            }, -2)
        self.log_rows = []
        self.refresh_log()

    def _make_log_row(self, text):
        """Render successful operation milestones in green on supported images."""
        color = 0x00D000 if _important_log_line(text) else _CELL_COLOR
        return [text, MultiContentEntryText(
            pos=(6, 0), size=(max(100, _review_list_width() - 18), 34), font=0,
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=_menu_text(text),
            color=color, color_sel=color, backcolor=_CELL_BACKGROUND,
            backcolor_sel=_CELL_SELECTED_BACKGROUND)]

    def refresh_log(self):
        """Load recent entries without blocking the receiver UI on all logs."""
        text = recent_log_entries(limit=500)
        self.log_rows = _wrap_log_lines(text)
        if not self.log_rows:
            self.log_rows = [_menu_text(_("No operation log is available yet."))]
        else:
            self.log_rows = [_menu_text(row) for row in self.log_rows]
        if self.multi_column_list:
            self["list"].setList([self._make_log_row(row) for row in self.log_rows])
        else:
            self["list"].setList(self.log_rows)
        self["summary"].setText(_menu_text(_("Log lines: %d") % len(self.log_rows)))
        self.move_bottom()

    def _move(self, method_name, fallback):
        method = getattr(self["list"], method_name, None)
        if method is not None:
            try:
                method()
                return
            except Exception:
                pass
        index = _menu_index(self["list"])
        target = max(0, min(len(self.log_rows) - 1, index + fallback))
        try:
            self["list"].moveToIndex(target)
        except Exception:
            pass

    def move_up(self):
        self._move("up", -1)

    def move_down(self):
        self._move("down", 1)

    def page_up(self):
        self._move("pageUp", -12)

    def page_down(self):
        self._move("pageDown", 12)

    def move_top(self):
        try:
            self["list"].moveToIndex(0)
        except Exception:
            pass

    def move_bottom(self):
        try:
            self["list"].moveToIndex(max(0, len(self.log_rows) - 1))
        except Exception:
            pass


class E2DuplicateChannelsManagerMainScreen(Screen):
    # Unique internal skin name prevents installed image skins from overriding this screen.
    skinName = "E2DuplicateChannelsManagerMainScreen"
    skin = _main_skin(skinName)

    def __init__(self, session):
        self.skinName = "E2DuplicateChannelsManagerMainScreen"
        Screen.__init__(self, session)
        self["title"] = Label("E2DuplicateChannelsManager by iet5")
        self["version"] = Label(_("Ver : %s") % PLUGIN_VERSION)
        # The shared screen skin still defines this hidden label; leave it
        # empty so the main menu is focused solely on actions and their help.
        self["info"] = Label("")
        self["summary"] = Label("")
        self.menu_entries = self._build_menu_entries()
        self["list"] = _plain_menu_list([_menu_text(entry[0]) for entry in self.menu_entries])
        self["help"] = Label("")
        self["status"] = Label(_("Select Scan and review duplicates to choose a satellite. Yellow: options. Blue: sync channel files."))
        self["key_red"] = Label(_("Close"))
        self["key_green"] = Label(_("Select"))
        self["key_yellow"] = Label(_("Menu"))
        self["key_blue"] = Label(_("Sync files"))
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions"], {
            "cancel": self.close,
            "red": self.close,
            "ok": self.select,
            "green": self.select,
            "yellow": self.open_options,
            "menu": self.open_options,
            "blue": self.sync_channel_files,
        }, -2)
        try:
            self["list"].onSelectionChanged.append(self._update_help)
        except Exception:
            pass
        self._update_help()
        self.update_timer = eTimer() if eTimer is not None else None
        self.update_timer_connected = False
        self._connect_update_timer()
        if _update_check_enabled():
            _start_update_check()
            self._poll_update_check()

    def _build_menu_entries(self):
        return [
            (_("Scan and review duplicates"), "scan",
             _("Choose a satellite, review removable duplicate groups, then select the groups you want to clean.")),
            (_("Run automatic cleanup now"), "clean",
             _("Scans all satellites now and removes only unprotected duplicates after creating a backup.")),
            (_("Create backup only"), "backup",
             _("Creates a complete backup of channel databases and bouquet files without changing any channels.")),
            (_("Restore a previous backup"), "restore",
             _("Restores a saved backup after first creating a safety backup of the current channel configuration.")),
            (_("View operation log"), "log",
             _("Shows recent scan, backup, cleanup, synchronization, and error messages.")),
            (_("About and safety rules"), "about",
             _("Explains the protection rules for Picon-linked services, channel files, backups, and automatic rollback.")),
        ]

    def _update_help(self):
        """Show the purpose of the menu row currently under the cursor."""
        index = _menu_index(self["list"])
        if 0 <= index < len(self.menu_entries):
            text = self.menu_entries[index][2]
        else:
            text = ""
        self["help"].setText(_menu_text(text))

    def open_options(self):
        self.session.open(E2DuplicateChannelsManagerOptionsScreen)

    def _connect_update_timer(self):
        if self.update_timer is None or self.update_timer_connected:
            return
        try:
            self.update_timer.callback.append(self._poll_update_check)
            self.update_timer_connected = True
            return
        except Exception:
            pass
        try:
            self.update_timer.timeout.connect(self._poll_update_check)
            self.update_timer_connected = True
        except Exception as error:
            log_message("Update-check timer connection failed: %s" % error)

    def _poll_update_check(self):
        if not _UPDATE_CHECK.get("done"):
            if self.update_timer is not None and self.update_timer_connected:
                try:
                    self.update_timer.start(500, True)
                except Exception:
                    pass
            return
        if _UPDATE_CHECK.get("notified"):
            if _PLUGIN_UPDATE.get("started"):
                self._poll_plugin_update()
            return
        remote = _UPDATE_CHECK.get("version")
        if _version_numbers(remote) > _version_numbers(PLUGIN_VERSION):
            _UPDATE_CHECK["notified"] = True
            text = _("A new version is available: %s\nCurrent version: %s\n\nDo you want to download and install the update now?\nA plugin backup is created first.") % (
                remote, PLUGIN_VERSION)
            self.session.openWithCallback(lambda answer: self._update_answer(remote, answer),
                                          MessageBox, _menu_text(text), MessageBox.TYPE_YESNO)

    def _update_answer(self, remote_version, answer):
        if not answer:
            log_message("GitHub update declined by user")
            return
        _start_plugin_update(remote_version)
        self._poll_plugin_update()

    def _poll_plugin_update(self):
        if not _PLUGIN_UPDATE.get("done"):
            if self.update_timer is not None and self.update_timer_connected:
                try:
                    self.update_timer.start(500, True)
                except Exception:
                    pass
            return
        if _PLUGIN_UPDATE.get("reported"):
            return
        _PLUGIN_UPDATE["reported"] = True
        if _PLUGIN_UPDATE.get("error"):
            text = _("Update failed. The installed plugin was kept unchanged.\n%s") % _PLUGIN_UPDATE["error"]
            self.session.open(MessageBox, _menu_text(text), MessageBox.TYPE_ERROR, timeout=18)
            return
        result = _PLUGIN_UPDATE.get("result") or {}
        text = _("Update completed successfully.\nVersion: %s\nUpdated files: %d\nBackup: %s\n\nRestart the Enigma2 GUI to activate the new version.") % (
            result.get("version", "-"), result.get("files", 0), result.get("backup", "-"))
        self.session.open(MessageBox, _menu_text(text), MessageBox.TYPE_INFO, timeout=25)

    def choose_satellite(self):
        """Open the satellite picker directly from the main screen."""
        try:
            self.session.openWithCallback(self._main_satellite_chosen,
                                          E2DuplicateChannelsManagerSatellitePicker,
                                          list_available_satellites(), None)
        except Exception as error:
            self.session.open(MessageBox, _menu_text(_("Satellite list failed:\n%s") % error),
                              MessageBox.TYPE_ERROR, timeout=14)

    def _main_satellite_chosen(self, answer):
        if answer is None:
            return
        satellite = answer[1] if isinstance(answer, (tuple, list)) else answer
        try:
            self.session.open(E2DuplicateChannelsManagerReviewScreen,
                              scan_duplicates(satellite=satellite))
        except Exception as error:
            self.session.open(MessageBox, _menu_text(_("Satellite scan failed:\n%s") % error),
                              MessageBox.TYPE_ERROR, timeout=14)

    def select(self):
        index = _menu_index(self["list"])
        if index < 0 or index >= len(self.menu_entries):
            return
        action = self.menu_entries[index][1]
        if action == "scan":
            self.choose_satellite()
        elif action == "clean":
            self.session.openWithCallback(self._clean_confirm, MessageBox,
                                          _("Run the protected automatic cleanup now? A backup is created before any change."),
                                          MessageBox.TYPE_YESNO)
        elif action == "backup":
            try:
                path = create_backup(reason="manual")
                self.session.open(MessageBox, _("Backup created:\n%s") % path, MessageBox.TYPE_INFO, timeout=10)
            except Exception as error:
                self.session.open(MessageBox, _("Backup failed:\n%s") % error, MessageBox.TYPE_ERROR, timeout=10)
        elif action == "restore":
            backups = list_backups()
            choices = [(_("Choose a backup from another folder"), "browse")]
            choices.extend((_backup_display_label(path), path) for path in backups[:20])
            self.session.openWithCallback(self.restore_choice, ChoiceBox,
                                          title=_("Choose a backup"), list=choices)
        elif action == "log":
            self.session.open(E2DuplicateChannelsManagerLogScreen)
        else:
            text = _("Safety model:\n- watches changes to lamedb, lamedb5 and bouquet files\n- waits until changed files are stable before cleanup\n- creates a complete backup before every write\n- rolls back automatically after a write failure\n- never removes a service reference that has a Picon\n- never deletes, renames, copies or links Picon files\n- replaces bouquet references before deleting unprotected lamedb duplicates\n- if several duplicates all have Picons, they remain protected and are reported")
            self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=22)

    def sync_channel_files(self):
        """Expose a manual persistence button for users after an update."""
        result = reload_e2_database()
        if result.get("errors"):
            text = _("Channel-file sync completed with warnings:\n%s") % "\n".join(result["errors"])
            box_type = MessageBox.TYPE_ERROR
        elif result.get("actions"):
            text = _("Channel files synchronized successfully:\n%s") % "\n".join(result["actions"])
            box_type = MessageBox.TYPE_INFO
        else:
            text = _("This image does not provide a channel-database sync API.")
            box_type = MessageBox.TYPE_INFO
        self.session.open(MessageBox, _menu_text(text), box_type, timeout=14)

    def _clean_confirm(self, answer):
        if not answer:
            return
        try:
            outcome = run_automatic_cleanup()
            if not outcome["changed"]:
                text = _("No removable duplicates were found. Protected Picon conflicts, if any, were left untouched.")
            else:
                result = outcome["result"]
                reload_e2_database(outcome.get("plan", {}).get("remove_refs", []))
                text = _("Cleanup completed.\nServices removed: %d\nBouquet duplicates removed: %d\nReferences replaced: %d\nProtected conflicts: %d\nBackup: %s") % (
                    result["removed_services"], result["removed_exact"], result["replaced"],
                    len(result.get("protected_conflicts", [])), result["backup"])
            self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=18)
        except Exception as error:
            self.session.open(MessageBox, _("Cleanup failed and rollback was attempted:\n%s") % error,
                              MessageBox.TYPE_ERROR, timeout=18)

    def restore_choice(self, answer):
        if answer is None:
            return
        path = answer[1]
        if path == "browse":
            self.choose_backup_folder()
            return
        self.session.openWithCallback(lambda yes: self.restore_confirm(path, yes), MessageBox,
                                      _("Restore this backup? A safety backup is created first.\n%s") % _backup_display_label(path),
                                      MessageBox.TYPE_YESNO)

    def choose_backup_folder(self):
        """Let the user browse to a folder containing a plugin backup."""
        if LocationBox is None:
            self.session.open(MessageBox, _("Backup folder browser is not available on this image."),
                              MessageBox.TYPE_ERROR, timeout=8)
            return
        start_dir = "/media/hdd" if os.path.isdir("/media/hdd") else "/"
        try:
            self.session.openWithCallback(self._backup_folder_chosen, LocationBox,
                                          text=_("Choose the folder containing the backup"),
                                          currDir=start_dir)
        except TypeError:
            # Legacy OpenPLi/DreamOS LocationBox variants do not always have
            # a ``text`` keyword, but all accept the starting directory.
            try:
                self.session.openWithCallback(self._backup_folder_chosen, LocationBox,
                                              currDir=start_dir)
            except TypeError:
                self.session.openWithCallback(self._backup_folder_chosen, LocationBox)

    def _backup_folder_chosen(self, directory):
        if not directory or not os.path.isdir(directory):
            return
        backups = []
        for pattern in ("e2dcm_*.tar.gz", "e2dpm_*.tar.gz"):
            backups.extend(glob.glob(os.path.join(directory, pattern)))
        backups = sorted(set(backups), key=lambda path: os.path.getmtime(path), reverse=True)
        if not backups:
            self.session.open(MessageBox, _("No E2DuplicateChannelsManager backup was found in this folder."),
                              MessageBox.TYPE_INFO, timeout=8)
            return
        choices = [(_backup_display_label(path), path) for path in backups]
        self.session.openWithCallback(self.restore_choice, ChoiceBox,
                                      title=_("Choose a backup"), list=choices)

    def restore_confirm(self, path, answer):
        if not answer:
            return
        try:
            safety = restore_backup(path, safety_backup=True)
            reload_e2_database()
            self.session.open(MessageBox, _("Restored successfully.\nSafety backup: %s") % safety,
                              MessageBox.TYPE_INFO, timeout=14)
        except Exception as error:
            self.session.open(MessageBox, _("Restore failed:\n%s") % error, MessageBox.TYPE_ERROR, timeout=14)


class DuplicateChannelsMonitor(object):
    """Wait for stable Enigma2 database files, then clean new duplicates."""

    INITIAL_DELAY_SECONDS = 45
    POLL_SECONDS = 15
    REQUIRED_STABLE_POLLS = 2

    def __init__(self, session):
        self.session = session
        self.timer = eTimer() if eTimer is not None else None
        self.pending_signature = None
        self.cleaned_signature = None
        self.stable_polls = 0
        self.running = False
        self.connected = False
        self._connect()

    def _connect(self):
        if self.timer is None or self.connected:
            return
        try:
            self.timer.callback.append(self._poll)
            self.connected = True
            return
        except Exception:
            pass
        try:
            self.timer.timeout.connect(self._poll)
            self.connected = True
        except Exception as error:
            log_message("Automatic monitor timer connection failed: %s" % error)

    def _schedule(self, seconds):
        if self.timer is None or not self.connected:
            return
        try:
            self.timer.startLongTimer(int(seconds))
        except Exception:
            try:
                self.timer.start(int(seconds) * 1000, True)
            except Exception as error:
                log_message("Automatic monitor timer start failed: %s" % error)

    def start(self):
        log_message("Automatic duplicate monitor started")
        # The monitor is for changes made after Enigma2 starts.  Recording the
        # current state prevents an expensive full scan shortly after every
        # boot when nothing has changed.
        try:
            current = watched_signature()
            self.pending_signature = current
            self.cleaned_signature = current
            self.stable_polls = 0
        except Exception as error:
            log_message("Automatic monitor initial signature failed: %s" % error)
        self._schedule(self.INITIAL_DELAY_SECONDS)

    def _poll(self):
        try:
            signature = watched_signature()
            if self.pending_signature != signature:
                self.pending_signature = signature
                self.stable_polls = 0
            else:
                self.stable_polls += 1

            if (not self.running and self.stable_polls >= self.REQUIRED_STABLE_POLLS and
                    signature != self.cleaned_signature):
                self.running = True
                try:
                    outcome = run_automatic_cleanup()
                    if outcome.get("changed"):
                        reload_e2_database(outcome.get("plan", {}).get("remove_refs", []))
                    self.cleaned_signature = watched_signature()
                    self.pending_signature = self.cleaned_signature
                    self.stable_polls = 0
                except Exception as error:
                    log_message("Automatic cleanup failed: %s" % error)
                    self.cleaned_signature = signature
                finally:
                    self.running = False
        except Exception as error:
            log_message("Automatic monitor poll failed: %s" % error)
        self._schedule(self.POLL_SECONDS)


def session_start(reason, **kwargs):
    global _MONITOR
    if reason != 0:
        return
    session = kwargs.get("session")
    if session is None or eTimer is None:
        return
    if _MONITOR is None:
        _MONITOR = DuplicateChannelsMonitor(session)
        _MONITOR.start()


def main(session, **kwargs):
    session.open(E2DuplicateChannelsManagerMainScreen)


def Plugins(**kwargs):
    icon = os.path.join(PLUGIN_PATH, "plugin.png")
    descriptors = [PluginDescriptor(
        name=_("E2DuplicateChannelsManager"),
        description=_("Automatically remove unprotected duplicate channels and preserve Picon-linked references"),
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon=icon if os.path.exists(icon) else None,
        fnc=main,
        needsRestart=False,
    )]
    session_where = getattr(PluginDescriptor, "WHERE_SESSIONSTART", None)
    if session_where is not None:
        descriptors.append(PluginDescriptor(where=session_where, fnc=session_start, needsRestart=False))
    return descriptors
