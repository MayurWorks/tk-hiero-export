# MIT License
#
# Copyright (c) 2026 SlateX
#
# Minimal OpenEXR header reader: just enough to learn a plate's size, pixel
# aspect and (when the file carries it) frame rate without OpenEXR, OIIO or
# Hiero. Reads the first part's header only.

import struct

_MAGIC = b"\x76\x2f\x31\x01"
_MAX_HEADER = 1 << 20  # a header larger than 1 MB is not a header


def read_exr_header(path):
    """
    Returns a dict with ``width``, ``height`` (display window), ``data_window``
    ((xmin, ymin, xmax, ymax)), ``pixel_aspect`` and ``fps`` (None when the
    file has no framesPerSecond attribute), or None if ``path`` is not a
    readable EXR.
    """
    try:
        with open(path, "rb") as handle:
            blob = handle.read(_MAX_HEADER)
    except (IOError, OSError):
        return None
    if blob[:4] != _MAGIC:
        return None

    attrs = {}
    pos = 8  # magic + version/flags
    try:
        while pos < len(blob):
            end = blob.index(b"\x00", pos)
            name = blob[pos:end].decode("ascii", "replace")
            pos = end + 1
            if not name:  # empty name ends the header
                break
            end = blob.index(b"\x00", pos)
            kind = blob[pos:end].decode("ascii", "replace")
            pos = end + 1
            (size,) = struct.unpack_from("<i", blob, pos)
            pos += 4
            data = blob[pos:pos + size]
            if len(data) != size:
                return None
            pos += size
            if name in ("displayWindow", "dataWindow") and kind == "box2i" and size == 16:
                attrs[name] = struct.unpack("<4i", data)
            elif name == "pixelAspectRatio" and kind == "float" and size == 4:
                attrs[name] = struct.unpack("<f", data)[0]
            elif name == "framesPerSecond" and kind == "rational" and size == 8:
                num, den = struct.unpack("<iI", data)
                if den:
                    attrs[name] = num / float(den)
    except (ValueError, struct.error):
        return None

    window = attrs.get("displayWindow") or attrs.get("dataWindow")
    if window is None:
        return None
    return {
        "width": window[2] - window[0] + 1,
        "height": window[3] - window[1] + 1,
        "data_window": attrs.get("dataWindow") or window,
        "pixel_aspect": attrs.get("pixelAspectRatio", 1.0),
        "fps": attrs.get("framesPerSecond"),
    }
