"""Audit explicit image/mask pairs without changing scientific inputs.

Every available calibration is compared; no source silently overrides another.
Agreement of metadata is not proof of correct microscope calibration.
"""

from __future__ import annotations

import csv
import hashlib
import math
import warnings
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_MAX_PIXELS = 25_000_000
RELATIVE_TOLERANCE = 0.01
SUPPORTED_FORMATS = {"PNG", "TIFF"}
GRAY_MODES = {"1", "L", "I", "I;16", "I;16L", "I;16B", "F"}
UNIT_TO_UM = {"µm": 1.0, "μm": 1.0, "um": 1.0, "nm": 0.001,
              "mm": 1000.0, "cm": 10000.0, "m": 1_000_000.0}


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    action: str


@dataclass
class Result:
    image: str
    mask: str
    width: int | None = None
    height: int | None = None
    pixel_size_um: float | None = None
    field_width_um: float | None = None
    field_height_um: float | None = None
    calibrations: dict[str, tuple[float, float]] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.severity == "error" for f in self.findings):
            return "blocked"
        return "review" if self.findings else "no_findings"

    def add(self, code: str, message: str, action: str, severity: str = "error") -> None:
        self.findings.append(Finding(code, severity, message, action))

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def positive_float(value: str | float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Calibration must be a finite positive number.")
    return number


def dataset_path(root: Path, name: str) -> Path:
    """Require explicit relative paths contained within the dataset, including symlinks."""
    candidate = Path(name)
    if not name.strip() or candidate.is_absolute():
        raise ValueError("An input path must be relative to the manifest directory.")
    path = (root / candidate).resolve()
    if not path.is_relative_to(root):
        raise ValueError("An input path escapes the manifest directory.")
    return path


def _ome_scale(description: str, size: tuple[int, int]) -> tuple[float, float] | None:
    """Read one 2D OME Image only, not arbitrary proprietary XML or TIFF DPI."""
    if not description.strip().startswith("<"):
        return None
    if len(description) > 1_000_000 or "<!DOCTYPE" in description.upper() or "<!ENTITY" in description.upper():
        raise ValueError("Unsupported XML declarations or excessive metadata size.")
    root = ET.fromstring(description)
    if root.tag.split("}")[-1] != "OME":
        return None
    if not root.tag.startswith("{http://www.openmicroscopy.org/Schemas/OME/"):
        raise ValueError("Unrecognized OME namespace.")
    images = [e for e in root if e.tag.split("}")[-1] == "Image"]
    if len(images) != 1:
        raise ValueError("Only one OME Image per file is supported.")
    pixels = [e for e in images[0] if e.tag.split("}")[-1] == "Pixels"]
    if len(pixels) != 1:
        raise ValueError("OME Image must contain exactly one Pixels element.")
    attributes = pixels[0].attrib
    if any(int(attributes.get(key, "1")) != 1 for key in ("SizeZ", "SizeC", "SizeT")):
        raise ValueError("OME stacks, channels and time series are outside the 2D scope.")
    if (int(attributes["SizeX"]), int(attributes["SizeY"])) != size:
        raise ValueError("OME dimensions do not match the decoded image header.")
    if "PhysicalSizeX" not in attributes and "PhysicalSizeY" not in attributes:
        return None
    values = []
    for axis in "XY":
        unit = attributes.get(f"PhysicalSize{axis}Unit", "µm")
        if unit not in UNIT_TO_UM:
            raise ValueError(f"Unsupported physical-size unit: {unit}.")
        value = positive_float(attributes[f"PhysicalSize{axis}"]) * UNIT_TO_UM[unit]
        values.append(positive_float(value))
    return values[0], values[1]


def _read_image(path: Path, result: Result, role: str, max_pixels: int) -> tuple[np.ndarray, str] | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in SUPPORTED_FORMATS:
                    raise ValueError("Only PNG and TIFF files are supported.")
                if image.width * image.height > max_pixels:
                    raise ValueError(f"Image exceeds the configured limit of {max_pixels} pixels.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Multi-frame images are outside the 2D scope.")
                if image.mode not in GRAY_MODES:
                    raise ValueError(f"Expected grayscale input, found mode {image.mode}.")
                if role == "image":
                    result.width, result.height = image.size
                    description = getattr(image, "tag_v2", {}).get(270, "")
                    if isinstance(description, bytes):
                        description = description.decode("utf-8")
                    if description:
                        try:
                            scale = _ome_scale(description, image.size)
                            if scale:
                                result.calibrations["ome"] = scale
                        except (ValueError, KeyError, ET.ParseError, OverflowError) as exc:
                            result.add("CALIBRATION_METADATA_INVALID", str(exc),
                                       "Check the source OME metadata and export a supported 2D image.")
                array = np.asarray(image).copy()  # Fully decode to catch truncated files.
                result.hashes[role] = sha256(path)
                return array, image.mode
    except (OSError, ValueError, UnicodeError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        result.add(f"{role.upper()}_UNREADABLE", str(exc),
                   "Check the file, format, dimensionality and configured pixel limit.")
        return None


def _resolve_scale(result: Result) -> None:
    if not result.calibrations:
        result.add("CALIBRATION_MISSING", "No physical pixel size was provided.",
                   "Add pixel_size_um to the manifest, valid OME metadata, or an ADS sidecar.")
        return
    # Compare the entire spread, avoiding reference-dependent tolerance decisions.
    values = np.asarray(list(result.calibrations.values()), dtype=float)
    if np.any((values.max(axis=0) - values.min(axis=0)) > RELATIVE_TOLERANCE * values.min(axis=0)):
        evidence = "; ".join(f"{name}={x:g} x {y:g} um" for name, (x, y) in result.calibrations.items())
        result.add("CALIBRATION_CONFLICT", evidence,
                   "Reconcile these sources against acquisition records; no automatic override is applied.")
        return
    if np.any(np.abs(values[:, 0] - values[:, 1]) > RELATIVE_TOLERANCE * values.min(axis=1)):
        result.add("PIXELS_ANISOTROPIC", "X and Y physical pixel sizes differ by more than 1%.",
                   "ADS uses a scalar pixel size. Review the acquisition and intended resampling method.")
        return
    # Deterministic order, explicitly recorded. This is not a verified ground truth.
    source = next(key for key in ("manifest", "ome", "ads_sidecar") if key in result.calibrations)
    result.pixel_size_um = result.calibrations[source][0]
    if result.width is not None and result.height is not None:
        width = result.width * result.pixel_size_um
        height = result.height * result.pixel_size_um
        if not math.isfinite(width) or not math.isfinite(height):
            result.add("FIELD_OF_VIEW_OVERFLOW", "Physical image extent exceeds numeric limits.",
                       "Check calibration magnitude and units against the acquisition record.")
            return
        result.field_width_um = width
        result.field_height_um = height


def _audit_row(row: dict[str, str], root: Path, max_pixels: int, seen: set[Path]) -> Result:
    result = Result(row.get("image", ""), row.get("mask", ""))
    try:
        image_path = dataset_path(root, result.image)
        if image_path in seen:
            result.add("IMAGE_DUPLICATED", "This image occurs in more than one manifest row.",
                       "Keep one row per image or audit different runs separately.")
        seen.add(image_path)
    except ValueError as exc:
        result.add("IMAGE_PATH_INVALID", str(exc), "Use a relative path inside the dataset.")
        return result

    explicit = row.get("pixel_size_um", "").strip()
    if explicit:
        try:
            value = positive_float(explicit)
            result.calibrations["manifest"] = (value, value)
        except (ValueError, OverflowError):
            result.add("CALIBRATION_MANIFEST_INVALID", "Manifest pixel_size_um is not finite and positive.",
                       "Provide a positive decimal in micrometers per pixel.")
    sidecar = image_path.parent / "pixel_size_in_micrometer.txt"
    if sidecar.exists():
        try:
            if not sidecar.resolve().is_relative_to(root):
                raise ValueError("Sidecar symlink escapes the dataset.")
            if sidecar.stat().st_size > 1024:
                raise ValueError("Sidecar is too large; expected one number.")
            value = positive_float(sidecar.read_text(encoding="utf-8").strip())
            result.calibrations["ads_sidecar"] = (value, value)
            result.hashes["ads_sidecar"] = sha256(sidecar)
        except (OSError, ValueError, UnicodeError, OverflowError) as exc:
            result.add("CALIBRATION_SIDECAR_INVALID", str(exc),
                       "Correct pixel_size_in_micrometer.txt using the acquisition record.")

    original = _read_image(image_path, result, "image", max_pixels)
    if original is not None:
        pixels, _ = original
        if not np.isfinite(pixels).all():
            result.add("IMAGE_NONFINITE", "Image contains NaN or infinite values.",
                       "Inspect the export and acquisition pipeline.")
        elif np.ptp(pixels.astype(np.float64)) == 0:
            result.add("IMAGE_CONSTANT", "All image pixels have the same value.",
                       "Inspect this image before analysis.", severity="warning")

    _resolve_scale(result)
    if not result.mask:
        result.add("MASK_NOT_PROVIDED", "No combined axon/myelin mask was supplied.",
                   "Supply mask in the manifest to check segmentation geometry and labels.", severity="warning")
        return result
    try:
        mask_path = dataset_path(root, result.mask)
        if mask_path == image_path:
            raise ValueError("Image and mask refer to the same file.")
    except ValueError as exc:
        result.add("MASK_PATH_INVALID", str(exc), "Use a separate combined mask inside the dataset.")
        return result
    mask_data = _read_image(mask_path, result, "mask", max_pixels)
    if mask_data is None:
        return result
    mask = mask_data[0]
    if original is not None and mask.shape != original[0].shape:
        result.add("MASK_SHAPE_MISMATCH", f"Image shape {original[0].shape}; mask shape {mask.shape}.",
                   "Use the mask generated on the same pixel grid; do not resize it blindly.")
    valid = np.isin(mask, [0, 127, 255])
    if not bool(valid.all()):
        result.add("MASK_LABELS_INVALID", "Combined mask contains values outside 0, 127 and 255.",
                   "Export an ADS combined mask (background=0, myelin=127, axon=255).")
    elif not bool(np.any(mask)):
        result.add("MASK_EMPTY", "Combined mask contains only background.",
                   "Review whether this is expected for the sample.", severity="warning")
    elif not bool(np.any(mask == 255)):
        result.add("MASK_NO_AXON", "Combined mask contains no axon label (255).",
                   "Review whether the correct combined mask was selected.", severity="warning")
    return result


def audit_manifest(manifest: Path | str, *, max_pixels: int = DEFAULT_MAX_PIXELS) -> list[Result]:
    """Audit a CSV whose paths are relative to its directory.

    Required column: image. Optional: mask, pixel_size_um. Unknown columns are
    rejected to catch misspelled calibration headers. Values are never inferred
    from names or DPI. The caller receives every row even when a file is broken.
    """
    if max_pixels < 1:
        raise ValueError("max_pixels must be positive.")
    manifest = Path(manifest).resolve()
    with manifest.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if "image" not in headers or len(headers) != len(set(headers)):
            raise ValueError("CSV requires a unique image header.")
        if set(headers) - {"image", "mask", "pixel_size_um"}:
            raise ValueError("Supported CSV columns: image, mask, pixel_size_um.")
        rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Manifest must contain complete CSV rows and at least one image.")
    seen: set[Path] = set()
    return [_audit_row(row, manifest.parent, max_pixels, seen) for row in rows]
