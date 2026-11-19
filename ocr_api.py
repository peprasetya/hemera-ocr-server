"""The OCR API as clients see it: POST /v1/ocr.

The request is multipart/form-data, with the picture in a part named `image` (or `file`) and an
optional `options` part of JSON. The answer gives the lines, words and characters, in reading
order. The full reference is hemera's docs/ocr-api.md.

This is the request's checking and the answer's shape. hemera's OcrApi.java did both until hemera
became only a router (hemera d0fbc0e); this is a port of it, with the same checks, messages and
answer:
- **Grapheme clusters:** Unicode's extended grapheme clusters, found with the `regex` module's
  `\\X`, which splits exactly as Java's BreakIterator did on OpenJDK 25. That includes Indic
  conjuncts, Thai, Hangul jamo and emoji sequences.
- **Whitespace:** a "space" is whatever Java's Character.isWhitespace or isSpaceChar called one.
- **Rounding:** numbers are rounded half up, as Java's Math.round does, and a word's confidence
  is summed one value at a time, as Java did.
"""
import json
import math
import re
import unicodedata

import regex

MAX_BYTES = 50 * 1024 * 1024
MAX_PIXELS = 50_000_000
SLACK = 64 * 1024  # multipart framing around a picture of MAX_BYTES


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def too_large():
    return ApiError(413, "the request is larger than %d bytes; send a picture of at most %d MB and %d million pixels"
                    % (MAX_BYTES, MAX_BYTES // 1024 // 1024, MAX_PIXELS // 1_000_000))


# ---- the request --------------------------------------------------------------------------------

def boundary(content_type):
    """The boundary from a Content-Type header, or None when it is not multipart/form-data."""
    if not content_type or not content_type.lower().startswith("multipart/form-data"):
        return None
    for piece in content_type.split(";"):
        p = piece.strip()
        if p[:9].lower() == "boundary=":
            b = p[9:].strip()
            if len(b) > 1 and b.startswith('"') and b.endswith('"'):
                b = b[1:-1]
            return b
    return None


def _header(headers, attr):
    m = re.search(r'(?i)\b' + attr + r'="([^"]*)"', headers)
    return m.group(1) if m else None


def parts(body, bound):
    """The form's parts as (name, data), byte-exact."""
    delim = b"--" + bound.encode()
    out = []
    pos = body.find(delim)
    if pos < 0:
        raise ApiError(400, "malformed multipart body: opening boundary not found")
    pos += len(delim)
    while pos < len(body):
        if body[pos:pos + 2] == b"--":
            break
        while pos < len(body) and body[pos] in (13, 10):
            pos += 1
        head_end = body.find(b"\r\n\r\n", pos)
        if head_end < 0:
            break
        headers = body[pos:head_end].decode("utf-8", "replace")
        start = head_end + 4
        nxt = body.find(delim, start)
        if nxt < 0:
            raise ApiError(400, "malformed multipart body: closing boundary not found")
        end = nxt - 2 if nxt - 2 >= start and body[nxt - 2:nxt] == b"\r\n" else nxt
        out.append((_header(headers, "name"), body[start:end]))
        pos = nxt + len(delim)
    return out


def picture_format(b):
    if len(b) < 12:
        return False
    png = b[0] == 0x89 and b[1:4] == b"PNG"
    jpeg = b[0] == 0xFF and b[1] == 0xD8
    webp = b[0:4] == b"RIFF" and b[8:12] == b"WEBP"
    tiff = b[0:4] in (b"II*\x00", b"MM\x00*")
    return png or jpeg or webp or tiff


def picture_and_options(content_type, body):
    """The picture's bytes and the options' JSON text (None when not sent)."""
    bound = boundary(content_type)
    if bound is None:
        raise ApiError(400, "send multipart/form-data with the picture in a part named 'image'")
    image, opts = None, None
    for name, data in parts(body, bound):
        if name in ("image", "file"):  # "file", as hemera's other uploads name it
            image = data
        elif name == "options":
            opts = data.decode("utf-8", "replace")
    if not image:
        raise ApiError(400, "the picture is missing: send it in a part named 'image'")
    if not picture_format(image):
        raise ApiError(400, "the picture is not PNG, JPEG, WebP or TIFF")
    return image, opts


def options(text):
    m = {}
    if text is not None and text.strip():
        try:
            m = json.loads(text)
        except ValueError as e:
            raise ApiError(400, "options are not a JSON object: %s" % e)
        if not isinstance(m, dict):
            raise ApiError(400, "options are not a JSON object: not an object")
    languages = []
    langs = m.get("languages")
    if langs is not None:
        if not isinstance(langs, list):
            raise ApiError(400, 'languages must be a list of BCP-47 tags, or ["auto"]')
        for x in langs:
            if not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,35}", x):
                raise ApiError(400, 'languages must be a list of BCP-47 tags, or ["auto"]')
            languages.append(x)
    if not languages:
        languages.append("auto")
    granularity = m.get("granularity", "chars")
    if granularity is None:
        granularity = "chars"
    if granularity not in ("lines", "words", "chars"):
        raise ApiError(400, 'granularity must be "lines", "words" or "chars"')
    region = m.get("region")
    if region is not None:
        if not isinstance(region, list) or len(region) != 4 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in region):
            raise ApiError(400, "region must be [x, y, w, h] in the picture's pixels, or null")
        region = [float(v) for v in region]
        if region[2] <= 0 or region[3] <= 0:
            raise ApiError(400, "region's width and height must be positive")
    orientation = m.get("orientation", "auto")
    if orientation is None:
        orientation = "auto"
    if isinstance(orientation, (int, float)) and not isinstance(orientation, bool) \
            and orientation == int(orientation) and int(orientation) in (0, 90, 180, 270):
        orientation = int(orientation)
    elif isinstance(orientation, str) and re.fullmatch(r"0|90|180|270", orientation):
        orientation = int(orientation)
    elif orientation != "auto":
        raise ApiError(400, 'orientation must be "auto", 0, 90, 180 or 270')
    model = m.get("model")
    if model is not None and not isinstance(model, str):
        raise ApiError(400, "model must be a model's name, or null")
    return {"languages": languages, "granularity": granularity, "region": region, "orientation": orientation,
            "model": model}


# ---- the answer ---------------------------------------------------------------------------------

def jround(v, scale=10):
    """Half up, as Java's Math.round: 0.25 -> 0.3, -0.25 -> -0.2."""
    return math.floor(v * scale + 0.5) / scale


def is_space(cluster):
    """Java's Character.isWhitespace(cp) || Character.isSpaceChar(cp), for every code point."""
    return all(unicodedata.category(ch) in ("Zs", "Zl", "Zp") or ord(ch) in (9, 10, 11, 12, 13, 0x1C, 0x1D, 0x1E, 0x1F)
               for ch in cluster)


def _dist(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _lerp(p, q, t):
    return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]


def _pt(p):
    return [jround(p[0]), jround(p[1])]


def segment(poly, direction, frm, to, length):
    """The part of a line between two distances along its reading direction, as a polygon in the
    line's own convention: clockwise, from where the part begins on the line's top side."""
    if len(poly) != 4 or length <= 0:
        return []
    a = max(0.0, min(1.0, frm / length))
    b = max(0.0, min(1.0, to / length))
    p0, p1, p2, p3 = poly
    if direction == "rtl":  # the top side runs p0 -> p3 (right to left), the bottom side p1 -> p2
        return [_pt(_lerp(p0, p3, a)), _pt(_lerp(p1, p2, a)), _pt(_lerp(p1, p2, b)), _pt(_lerp(p0, p3, b))]
    # the top side runs p0 -> p1, the bottom side p3 -> p2
    return [_pt(_lerp(p0, p1, a)), _pt(_lerp(p0, p1, b)), _pt(_lerp(p3, p2, b)), _pt(_lerp(p3, p2, a))]


def _points(polygon):
    out = []
    for p in polygon or []:
        if isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p):
            out.append([float(p[0]), float(p[1])])
    return out


def line(l, granularity):
    text = l.get("text", "") or ""
    direction = l.get("direction", "ltr") or "ltr"
    poly = _points(l.get("polygon"))
    length = _dist(poly[0], poly[3] if direction == "rtl" else poly[1]) if len(poly) == 4 else 0.0

    # grapheme clusters, as [start, end) code point indices
    clusters = []
    for m in regex.finditer(r"\X", text):
        clusters.append((m.start(), m.end()))

    cps = [c for c in (l.get("cps") or []) if isinstance(c, (list, tuple)) and len(c) == 2]
    estimated = bool(l.get("chars_estimated", False)) or len(cps) != len(text)
    cp_conf = [c for c in (l.get("cp_conf") or []) if isinstance(c, (int, float))]
    if len(cp_conf) != len(text):
        cp_conf = None  # words then carry the line's confidence
    chars = []
    for i, (s, e) in enumerate(clusters):
        if not estimated:
            chars.append([min(cps[k][0] for k in range(s, e)), max(cps[k][1] for k in range(s, e))])
        else:  # no positions from the reader: the clusters spread evenly along the line
            w = length / max(1, len(clusters))
            chars.append([i * w, (i + 1) * w])

    out = {"text": text, "polygon": l.get("polygon"), "angle": l.get("angle", 0.0), "direction": direction,
           "script": l.get("script"), "language": l.get("language"), "confidence": l.get("confidence")}
    if granularity != "lines":
        words, start = [], -1
        for i in range(len(clusters) + 1):
            space = i == len(clusters) or is_space(text[clusters[i][0]:clusters[i][1]])
            if not space and start < 0:
                start = i
            if space and start >= 0:
                s = min(chars[k][0] for k in range(start, i))
                e = max(chars[k][1] for k in range(start, i))
                confidence = l.get("confidence")
                if cp_conf is not None:
                    frm, to = clusters[start][0], clusters[i - 1][1]
                    total = 0.0
                    for v in cp_conf[frm:to]:  # one by one, as Java did: sum() compensates, and rounds differently
                        total += v
                    confidence = jround(total / max(1, to - frm), 1000)
                words.append({"text": text[clusters[start][0]:clusters[i - 1][1]],
                              "polygon": segment(poly, direction, s, e, length), "start": start, "end": i,
                              "confidence": confidence})
                start = -1
        out["words"] = words
    if granularity == "chars":
        out["chars"] = [[jround(c[0]), jround(c[1])] for c in chars]
        out["chars_estimated"] = estimated
    return out


def shape(engine, model, granularity, total_ms):
    """The reader's result (lines with each code point's span along them) as the answer."""
    blocks, texts = [], []
    for b in engine.get("blocks") or []:
        lines = [line(l, granularity) for l in b.get("lines") or []]
        if not lines:
            continue
        blocks.append({"polygon": b.get("polygon"), "lines": lines})
        texts.append("\n".join(l.get("text", "") or "" for l in b.get("lines") or []))
    timing = {"total": total_ms}
    timing.update({k: v for k, v in (engine.get("timing_ms") or {}).items() if k != "total"})
    return {"version": 1, "model": model, "image": engine.get("image"), "orientation": engine.get("orientation", 0),
            "timing_ms": timing, "text": "\n\n".join(texts), "blocks": blocks}
