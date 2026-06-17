"""Read embedded image metadata and normal file data.

Important split:
- Embedded metadata dates (EXIF, XMP, IPTC/text date fields, PNG/WebP
  date text/comments) decide whether an image is loaded by default.
- Normal file info (name, size, modified date, dimensions) is displayed only as
  extra information and does not count as metadata.
"""

from __future__ import annotations

import datetime as _dt
import re
import warnings
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import ExifTags, Image
try:
    from PIL.IptcImagePlugin import getiptcinfo
except Exception:  # pragma: no cover - depends on Pillow build/version
    getiptcinfo = None  # type: ignore[assignment]


EXIF_DATE_TAGS = (
    "DateTimeOriginal",
    "DateTimeDigitized",
    "DateTime",
    "OffsetTimeOriginal",
)

XMP_DATE_FIELD_NAMES = (
    "DateTimeOriginal",
    "CreateDate",
    "ModifyDate",
    "MetadataDate",
    "DateCreated",
)

PRIORITY_EXIF_TAGS = [
    "DateTimeOriginal",
    "DateTimeDigitized",
    "DateTime",
    "Make",
    "Model",
    "LensMake",
    "LensModel",
    "Software",
    "Artist",
    "Copyright",
    "ImageDescription",
    "UserComment",
    "Orientation",
    "ExposureTime",
    "FNumber",
    "ISOSpeedRatings",
    "PhotographicSensitivity",
    "ExposureProgram",
    "ExposureMode",
    "ExposureBiasValue",
    "MeteringMode",
    "FocalLength",
    "FocalLengthIn35mmFilm",
    "Flash",
    "WhiteBalance",
    "ColorSpace",
    "GPSInfo",
]

# These Image.info keys are usually technical/container details. They can be
# displayed as extra info, but by themselves they do not make a file qualify as
# "has metadata".
NON_QUALIFYING_INFO_KEYS = {
    "jfif",
    "jfif_version",
    "jfif_unit",
    "jfif_density",
    "dpi",
    "icc_profile",
    "gamma",
    "chromaticity",
    "srgb",
    "transparency",
    "interlace",
    "progressive",
    "progression",
    "duration",
    "loop",
    "background",
    "aspect",
    "version",
    "lossless",
    "quality",
}

# Text/comment keys commonly used by PNG, WebP, JPEG comments, ComfyUI, etc.
QUALIFYING_TEXT_KEYS = {
    "comment",
    "comments",
    "description",
    "title",
    "author",
    "artist",
    "copyright",
    "creation time",
    "creation_time",
    "date:create",
    "date:modify",
    "parameters",
    "prompt",
    "negative_prompt",
    "workflow",
    "comfyui",
    "software",
}

XMP_INFO_KEYS = {
    "xmp",
    "XML:com.adobe.xmp",
    "xml:com.adobe.xmp",
}

EXIF_INFO_KEYS = {"exif"}


@dataclass(frozen=True)
class PictureFileSummary:
    """Small summary used by the resizable picture list columns."""

    path: Path
    name: str
    width: int | None
    height: int | None
    pixel_count: int
    width_height: str
    size_bytes: int
    size_text: str
    metadata_datetime: _dt.datetime | None
    metadata_date_text: str
    file_datetime: _dt.datetime | None
    file_date_text: str
    folder: str


def get_picture_file_summary(path: Path) -> PictureFileSummary:
    """Return the values shown in the picture list/table.

    File info is shown here for convenience, but it still does not decide whether
    an image qualifies for loading. That filtering is based on real embedded
    metadata only.

    The result is cached using the file path, modified time, and size. This
    avoids opening the same image repeatedly when scanning, sorting, and then
    building the visible table rows.
    """
    path_text, mtime_ns, size_bytes = metadata_cache_key(path)
    return _get_picture_file_summary_cached(path_text, mtime_ns, size_bytes)


@lru_cache(maxsize=20_000)
def _get_picture_file_summary_cached(
    path_text: str,
    mtime_ns: int,
    size_bytes_from_key: int,
) -> PictureFileSummary:
    """Cached worker for get_picture_file_summary()."""
    del mtime_ns
    path = Path(path_text)
    size_bytes = size_bytes_from_key
    file_datetime: _dt.datetime | None = None

    try:
        stat = path.stat()
        size_bytes = stat.st_size
        file_datetime = _dt.datetime.fromtimestamp(stat.st_mtime)
    except OSError:
        pass

    width: int | None = None
    height: int | None = None
    width_height = "?"
    try:
        with Image.open(path) as image:
            width = int(image.width)
            height = int(image.height)
            width_height = f"{width} x {height}"
    except Exception:
        pass

    pixel_count = (width or 0) * (height or 0)

    metadata_datetime = get_best_embedded_datetime(path)

    return PictureFileSummary(
        path=path,
        name=path.name,
        width=width,
        height=height,
        pixel_count=pixel_count,
        width_height=width_height,
        size_bytes=size_bytes,
        size_text=human_size(size_bytes) if size_bytes else "?",
        metadata_datetime=metadata_datetime,
        metadata_date_text=format_datetime(metadata_datetime),
        file_datetime=file_datetime,
        file_date_text=format_datetime(file_datetime),
        folder=str(path.parent),
    )


def metadata_cache_key(path: Path) -> tuple[str, int, int]:
    """Return a stable cache key that changes when the file changes."""
    resolved = path.expanduser().resolve()
    try:
        stat = resolved.stat()
        return str(resolved), int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return str(resolved), 0, 0


@dataclass
class MetadataReport:
    """Collected metadata for one image."""

    path: Path
    exif: dict[str, str] = field(default_factory=dict)
    gps: dict[str, str] = field(default_factory=dict)
    xmp: dict[str, str] = field(default_factory=dict)
    iptc: dict[str, str] = field(default_factory=dict)
    text: dict[str, str] = field(default_factory=dict)
    technical: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def has_real_metadata(self) -> bool:
        """True when real embedded metadata was found.

        Normal filesystem data and technical container info do not count.
        """
        return any((self.exif, self.gps, self.xmp, self.iptc, self.text))


def has_supported_metadata(path: Path) -> bool:
    """Return True if image contains EXIF/XMP/IPTC/text/comment metadata."""
    return read_metadata_report(path).has_real_metadata


@dataclass(frozen=True)
class MetadataDateCheckResult:
    """Result from checking one image for an embedded metadata date.

    ``error_count`` is the number of non-fatal metadata-read warnings found
    while opening/parsing the file. The image may still be usable even when this
    is greater than zero.
    """

    metadata_datetime: _dt.datetime | None
    error_count: int = 0
    errors: tuple[str, ...] = ()

    @property
    def has_metadata_date(self) -> bool:
        return self.metadata_datetime is not None


def check_supported_metadata_date(path: Path) -> MetadataDateCheckResult:
    """Return embedded metadata-date information plus read-warning count.

    This uses the same cache key as the metadata-date cache, so repeated scans,
    sorting, and row-building do not reopen the same image again unless the file
    changed.
    """
    path_text, mtime_ns, size_bytes = metadata_cache_key(path)
    return _check_supported_metadata_date_cached(path_text, mtime_ns, size_bytes)


@lru_cache(maxsize=20_000)
def _check_supported_metadata_date_cached(
    path_text: str,
    mtime_ns: int,
    size_bytes: int,
) -> MetadataDateCheckResult:
    """Cached worker for check_supported_metadata_date()."""
    del mtime_ns, size_bytes
    report = read_metadata_report(Path(path_text))
    return MetadataDateCheckResult(
        metadata_datetime=get_best_embedded_datetime_from_report(report),
        error_count=len(report.errors),
        errors=tuple(report.errors),
    )


def has_supported_metadata_date(path: Path) -> bool:
    """Return True if image contains a real embedded metadata date.

    Normal filesystem timestamps do not count here. This is the stricter filter
    used by the loader by default.
    """
    return check_supported_metadata_date(path).has_metadata_date


def build_info_text(path: Path) -> str:
    """Return file info + embedded metadata as plain text for compare boxes."""
    report = read_metadata_report(path)

    lines: list[str] = []
    lines.extend(read_file_info(path))
    lines.append("")

    if report.has_real_metadata:
        lines.append("EMBEDDED METADATA")
        lines.append("=================")
        append_section(lines, "EXIF", report.exif)
        append_section(lines, "GPS", report.gps)
        append_section(lines, "XMP", report.xmp)
        append_section(lines, "IPTC", report.iptc)
        append_section(lines, "TEXT / COMMENTS", report.text)
    else:
        lines.append("EMBEDDED METADATA")
        lines.append("=================")
        lines.append("No EXIF, XMP, IPTC, or text/comment metadata found.")

    if report.technical:
        lines.append("")
        lines.append("TECHNICAL EMBEDDED INFO")
        lines.append("=======================")
        lines.append("This is shown for comparison, but it does not count when filtering files.")
        for key in sorted(report.technical):
            lines.append(f"{key}: {report.technical[key]}")

    if report.errors:
        lines.append("")
        lines.append("READ WARNINGS")
        lines.append("=============")
        lines.extend(report.errors)

    return "\n".join(lines)


def append_section(lines: list[str], title: str, values: dict[str, str]) -> None:
    """Append a metadata section to a text buffer."""
    if not values:
        return
    lines.append("")
    lines.append(title)
    lines.append("-" * len(title))
    for key in sorted_metadata_keys(values):
        lines.append(f"{key}: {values[key]}")


def sorted_metadata_keys(values: dict[str, str]) -> list[str]:
    """Keep important EXIF fields first, then sort the rest."""
    priority = [key for key in PRIORITY_EXIF_TAGS if key in values]
    remaining = sorted(key for key in values if key not in priority)
    return priority + remaining


def read_file_info(path: Path) -> list[str]:
    """Return normal filesystem and basic image container information."""
    lines: list[str] = ["FILE", "----"]

    try:
        stat = path.stat()
        lines.append(f"Name: {path.name}")
        lines.append(f"Path: {path}")
        lines.append(f"Size: {human_size(stat.st_size)} ({stat.st_size:,} bytes)")
        lines.append(f"Modified: {format_timestamp(stat.st_mtime)}")
        lines.append(f"Created/changed: {format_timestamp(stat.st_ctime)}")
    except OSError as exc:
        lines.append(f"Could not read file info: {exc}")

    try:
        with Image.open(path) as image:
            lines.append(f"Image format: {image.format or 'Unknown'}")
            lines.append(f"Pixel size: {image.width} x {image.height}")
            lines.append(f"Color mode: {image.mode}")
    except Exception as exc:
        lines.append(f"Could not read image info: {exc}")

    return lines


def read_metadata_report(path: Path) -> MetadataReport:
    """Read all supported embedded metadata from an image.

    Pillow sometimes emits non-fatal warnings, for example "Truncated File
    Read". Those used to appear only in the terminal. We capture them here so
    the GUI can count and display them in the Warnings / errors window.
    """
    report = MetadataReport(path=path)

    try:
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            with Image.open(path) as image:
                read_exif_into_report(image, report)
                read_image_info_into_report(image, report)

                if getiptcinfo is not None:
                    try:
                        raw_iptc = getiptcinfo(image)
                        report.iptc.update(decode_iptc_info(raw_iptc or {}))
                    except Exception as exc:
                        # IPTC is optional; some valid files make Pillow throw here.
                        report.errors.append(f"Could not read IPTC metadata: {exc}")
        append_caught_warnings(report, caught_warnings, "Pillow metadata read")
    except Exception as exc:
        report.errors.append(f"Could not open image for metadata: {exc}")

    # Pillow does not expose all XMP packets for every format, so also scan the
    # raw file for an XMP packet. This is especially useful for JPEG/WebP files.
    if not report.xmp:
        try:
            with warnings.catch_warnings(record=True) as caught_warnings:
                warnings.simplefilter("always")
                report.xmp.update(extract_xmp_from_file(path))
            append_caught_warnings(report, caught_warnings, "Raw XMP scan")
        except Exception as exc:
            report.errors.append(f"Could not scan file for XMP metadata: {exc}")

    return report


def append_caught_warnings(report: MetadataReport, caught_warnings: Iterable[warnings.WarningMessage], context: str) -> None:
    """Append captured Python/Pillow warnings to a metadata report."""
    for warning_message in caught_warnings:
        category = getattr(warning_message.category, "__name__", "Warning")
        message = str(warning_message.message)
        report.errors.append(f"{context}: {category}: {message}")


def read_exif_into_report(image: Image.Image, report: MetadataReport) -> None:
    """Decode EXIF and GPS metadata into report."""
    try:
        exif = image.getexif()
    except Exception as exc:
        report.errors.append(f"Could not read EXIF metadata: {exc}")
        return

    if not exif:
        return

    for tag_id, value in exif.items():
        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
        if tag_name == "GPSInfo":
            gps_values = get_gps_ifd(exif, value)
            report.gps.update(decode_gps_info(gps_values))
            continue
        report.exif[tag_name] = clean_metadata_value(value)


def get_gps_ifd(exif: Image.Exif, value: object) -> dict[Any, Any]:
    """Return decoded GPS IFD data from Pillow's Exif object."""
    gps_tag_id = 34853
    try:
        gps_ifd = exif.get_ifd(gps_tag_id)
        if gps_ifd:
            return dict(gps_ifd)
    except Exception:
        pass

    if isinstance(value, dict):
        return value

    return {}


def read_image_info_into_report(image: Image.Image, report: MetadataReport) -> None:
    """Read metadata exposed through Pillow Image.info."""
    for key, value in image.info.items():
        key_text = str(key)
        key_lower = key_text.lower()

        if key_lower in EXIF_INFO_KEYS:
            # image.getexif() already handles this better.
            continue

        if key_text in XMP_INFO_KEYS or key_lower in XMP_INFO_KEYS:
            report.xmp.update(parse_xmp_packet(clean_metadata_value(value)))
            continue

        cleaned = clean_metadata_value(value)
        if not cleaned:
            continue

        if is_qualifying_text_key(key_lower, value):
            report.text[key_text] = cleaned
        else:
            report.technical[key_text] = cleaned


def is_qualifying_text_key(key_lower: str, value: object) -> bool:
    """Return True when an Image.info key is useful text/comment metadata."""
    if key_lower in NON_QUALIFYING_INFO_KEYS:
        return False
    if key_lower in QUALIFYING_TEXT_KEYS:
        return True
    if isinstance(value, str):
        # Unknown string keys from PNG/WebP/JPEG are often text chunks/comments.
        return True
    if isinstance(value, bytes):
        # A bytes value can still be a useful comment/XMP-ish blob. Count it
        # only if it looks textual, not like an ICC profile.
        return looks_like_text(value)
    return False


def decode_gps_info(gps_info: dict[Any, Any]) -> dict[str, str]:
    """Decode EXIF GPS tag names."""
    decoded: dict[str, str] = {}
    for key, value in gps_info.items():
        name = ExifTags.GPSTAGS.get(key, str(key))
        decoded[name] = clean_metadata_value(value)
    return decoded


def decode_iptc_info(raw_iptc: dict[Any, Any]) -> dict[str, str]:
    """Decode IPTC values exposed by Pillow.

    Pillow gives IPTC keys as numeric tuples. We keep the numeric tag because it
    is reliable across versions, while still making values readable.
    """
    decoded: dict[str, str] = {}
    for key, value in raw_iptc.items():
        decoded[f"IPTC {key}"] = clean_metadata_value(value)
    return decoded


def extract_xmp_from_file(path: Path) -> dict[str, str]:
    """Find and parse an XMP packet by scanning the raw file bytes."""
    data = path.read_bytes()
    if not data:
        return {}

    # Decode with replacement because XMP is normally UTF-8, but some files are
    # messy. This keeps the app from crashing on bad metadata.
    text = data.decode("utf-8", errors="ignore")

    start_candidates = [
        index for index in (text.find("<x:xmpmeta"), text.find("<?xpacket")) if index != -1
    ]
    if not start_candidates:
        return {}

    start = min(start_candidates)
    end_markers = ["</x:xmpmeta>", "<?xpacket end="]
    end_positions = [text.find(marker, start) for marker in end_markers]
    end_positions = [pos for pos in end_positions if pos != -1]
    if end_positions:
        end = max(end_positions)
        # Include the closing xmpmeta tag when present.
        closing = "</x:xmpmeta>"
        closing_end = text.find(closing, start)
        if closing_end != -1:
            end = closing_end + len(closing)
        else:
            end += 80
    else:
        end = min(len(text), start + 20_000)

    return parse_xmp_packet(text[start:end])


def parse_xmp_packet(packet: str) -> dict[str, str]:
    """Extract useful readable fields from XMP XML text.

    This is intentionally lightweight: it avoids a strict XML parser because XMP
    found in the wild is often embedded inside binary containers or malformed.
    """
    if not packet:
        return {}

    packet = clean_text(packet)
    decoded: dict[str, str] = {}

    # Attribute style: exif:DateTimeOriginal="..." or dc:title="..."
    for name, value in re.findall(r"([A-Za-z0-9_:-]+)=['\"]([^'\"]{1,1000})['\"]", packet):
        simple_name = name.split(":")[-1]
        decoded[simple_name] = clean_text(value)

    # Element style: <dc:creator>...</dc:creator>
    for name, value in re.findall(
        r"<([A-Za-z0-9_:-]+)[^>]*>(.*?)</\1>", packet, flags=re.DOTALL
    ):
        simple_name = name.split(":")[-1]
        value = strip_xml_tags(value)
        if value:
            decoded.setdefault(simple_name, value)

    # Keep a shortened raw packet too. This helps when the XMP contains fields
    # not caught by the simple parser.
    if decoded:
        decoded.setdefault("Raw XMP preview", shorten(packet, 2000))
    else:
        decoded["Raw XMP preview"] = shorten(packet, 2000)

    return decoded


def strip_xml_tags(value: str) -> str:
    """Remove XML tags from a small value and clean whitespace."""
    value = re.sub(r"<[^>]+>", " ", value)
    return clean_text(value)


def clean_text(value: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", value).strip()


def clean_metadata_value(value: object) -> str:
    """Make metadata values readable and not too long."""
    if isinstance(value, bytes):
        # EXIF UserComment sometimes has an 8-byte character-code prefix.
        for prefix in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"JIS\x00\x00\x00\x00\x00"):
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        try:
            decoded = value.decode("utf-8", errors="replace").strip("\x00")
        except Exception:
            decoded = repr(value)
        return shorten(decoded if decoded else repr(value), 2000)

    if isinstance(value, dict):
        parts = [f"{k}={clean_metadata_value(v)}" for k, v in value.items()]
        return shorten("; ".join(parts), 2000)

    if isinstance(value, (list, tuple)):
        return shorten(", ".join(clean_metadata_value(v) for v in value), 2000)

    text = str(value)
    return shorten(text, 2000)


def looks_like_text(value: bytes) -> bool:
    """Best-effort check for whether bytes are mostly readable text."""
    if not value:
        return False
    sample = value[:512]
    decoded = sample.decode("utf-8", errors="ignore")
    if not decoded:
        return False
    printable = sum(1 for char in decoded if char.isprintable() or char.isspace())
    return printable / max(len(decoded), 1) > 0.85


def shorten(text: str, limit: int) -> str:
    """Shorten long metadata values for GUI display."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " ..."


def get_best_embedded_datetime(path: Path) -> _dt.datetime | None:
    """Return the best embedded metadata date, or None if no metadata date exists.

    This never uses filesystem timestamps. File modified/created dates are shown
    separately in the GUI, but they do not qualify a file for loading.

    The result is cached using path + file modified time + file size. Scans often
    ask for the same metadata date during filtering, sorting, and row creation,
    so this removes a lot of repeated image reads.
    """
    path_text, mtime_ns, size_bytes = metadata_cache_key(path)
    return _get_best_embedded_datetime_cached(path_text, mtime_ns, size_bytes)


@lru_cache(maxsize=20_000)
def _get_best_embedded_datetime_cached(
    path_text: str,
    mtime_ns: int,
    size_bytes: int,
) -> _dt.datetime | None:
    """Cached worker for get_best_embedded_datetime()."""
    return _check_supported_metadata_date_cached(path_text, mtime_ns, size_bytes).metadata_datetime


def get_best_embedded_datetime_from_report(report: MetadataReport) -> _dt.datetime | None:
    """Return the best embedded metadata date from an already-read report."""

    # Prefer known EXIF camera-date fields.
    for tag_name in EXIF_DATE_TAGS:
        value = report.exif.get(tag_name)
        parsed = parse_metadata_datetime(value) if value else None
        if parsed is not None:
            return parsed

    # Then known XMP date fields.
    for tag_name in XMP_DATE_FIELD_NAMES:
        value = report.xmp.get(tag_name)
        parsed = parse_metadata_datetime(value) if value else None
        if parsed is not None:
            return parsed

    # Some metadata formats use lowercase, namespace-stripped, or text chunk names.
    all_metadata = {**report.exif, **report.xmp, **report.iptc, **report.text}
    lower_lookup = {key.lower(): value for key, value in all_metadata.items()}
    priority_keys = (
        "datetimeoriginal",
        "datetimedigitized",
        "datetime",
        "createdate",
        "modifydate",
        "metadatadate",
        "datecreated",
        "creation time",
        "creation_time",
        "date:create",
        "date:modify",
    )
    for key in priority_keys:
        parsed = parse_metadata_datetime(lower_lookup.get(key))
        if parsed is not None:
            return parsed

    # IPTC from Pillow can have numeric tag names, so also inspect values from
    # keys that look date/time related.
    for key, value in all_metadata.items():
        key_lower = key.lower()
        if any(word in key_lower for word in ("date", "time", "created", "creation")):
            parsed = parse_metadata_datetime(value)
            if parsed is not None:
                return parsed

    # Last chance: find a date-looking value anywhere in embedded metadata. This
    # still does not use filesystem data.
    for value in all_metadata.values():
        parsed = parse_metadata_datetime(value)
        if parsed is not None:
            return parsed

    return None


# Backwards-compatible name used by older sorting code.
def get_best_exif_datetime(path: Path) -> _dt.datetime | None:
    """Return the best embedded date. Kept for compatibility."""
    return get_best_embedded_datetime(path)


def parse_metadata_datetime(value: str | None) -> _dt.datetime | None:
    """Parse common EXIF/XMP datetime formats."""
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    # Common EXIF format: 2026:06:16 14:30:00
    formats = (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d",
    )

    cleaned = value.replace("Z", "+0000")
    if cleaned.endswith("+00:00"):
        cleaned = cleaned[:-6] + "+0000"
    cleaned = re.sub(r"([+-]\d\d):(\d\d)$", r"\1\2", cleaned)

    for fmt in formats:
        try:
            parsed = _dt.datetime.strptime(cleaned, fmt)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            pass

    # Last attempt: look for an EXIF-ish date inside a longer metadata string.
    match = re.search(r"(\d{4})[:/-](\d{2})[:/-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", value)
    if match:
        year, month, day, hour, minute, second = map(int, match.groups())
        try:
            return _dt.datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None

    return None


def human_size(num_bytes: int) -> str:
    """Return a human-readable file size."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def format_datetime(value: _dt.datetime | None) -> str:
    """Format a datetime for display in the GUI."""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp(timestamp: float) -> str:
    """Format a filesystem timestamp."""
    return format_datetime(_dt.datetime.fromtimestamp(timestamp))
