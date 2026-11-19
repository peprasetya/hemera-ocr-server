"""POST /v1/ocr through the engine's real HTTP handler, with a stand-in reader instead of the
models: the request's checks, and the answer's shape (grapheme clusters, words, polygons, the text
in reading order). These are the checks hemera's OcrApiTest made while hemera shaped the answer.

    venv/bin/python -I tests/test_answer.py
"""
import http.client
import io
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ocr_server  # noqa: E402
from PIL import Image  # noqa: E402

FAILED = []
BOUNDARY = "ocr-test-boundary"


def check(cond, what, detail=""):
    print(("ok    " if cond else "FAIL  ") + what + ("" if cond else "\n      " + str(detail)[:500]))
    if not cond:
        FAILED.append(what)


def spans(n, step=10.0):
    return [[i * step, (i + 1) * step] for i in range(n)]


class StandIn:
    """What the reader returns for any picture: three lines in two blocks, one right to left."""
    seen = []
    mode = "normal"

    class m:
        @staticmethod
        def info():
            return {}

    def read(self, data, options, cancel):
        StandIn.seen.append(options)
        if StandIn.mode == "huge":
            raise ocr_server.BadRequest(413, "the picture has 60000000 pixels; at most 50000000 are read")
        latin = "Café \U0001F44D\U0001F3FD ok"  # 11 code points, 9 grapheme clusters

        def line(text, polygon, direction, script, language, conf):
            n = len(text)
            return {"text": text, "polygon": polygon, "angle": 0.0, "direction": direction, "script": script,
                    "language": language, "confidence": 0.9, "cps": spans(n), "cp_conf": conf(n), "chars_estimated": False}
        return {"image": {"width": 400, "height": 300}, "orientation": 0,
                "timing_ms": {"orientation": 1, "detect": 2, "recognize": 3, "total": 6},
                "blocks": [
                    {"polygon": [[0, 0], [110, 0], [110, 40], [0, 40]], "lines": [
                        line(latin, [[0, 0], [110, 0], [110, 10], [0, 10]], "ltr", "Latn", "en",
                             lambda n: [0.5] * 4 + [1.0] * (n - 4)),
                        line("NIK 3171", [[0, 20], [80, 20], [80, 30], [0, 30]], "ltr", "Latn", "id", lambda n: [0.8] * n)]},
                    {"polygon": [[200, 100], [280, 100], [280, 110], [200, 110]], "lines": [
                        line("مرحبا 15", [[280, 100], [280, 110], [200, 110], [200, 100]], "rtl", "Arab", "ar",
                             lambda n: [0.7] * n)]},
                ]}


def png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "PNG")
    return buf.getvalue()


def multipart(image, options, name="image"):
    out = b""
    if image is not None:
        out += (b"--" + BOUNDARY.encode() + b'\r\nContent-Disposition: form-data; name="' + name.encode()
                + b'"; filename="p.png"\r\nContent-Type: image/png\r\n\r\n' + image + b"\r\n")
    if options is not None:
        out += (b"--" + BOUNDARY.encode() + b'\r\nContent-Disposition: form-data; name="options"\r\n\r\n'
                + options.encode() + b"\r\n")
    return out + b"--" + BOUNDARY.encode() + b"--\r\n"


def post(port, body, ctype="multipart/form-data; boundary=" + BOUNDARY, length=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("POST", "/v1/ocr")
    c.putheader("Content-Type", ctype)
    c.putheader("Content-Length", str(len(body) if length is None else length))
    c.endheaders()
    if length is None:
        c.send(body)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data), r.getheader("Content-Type")


def main():
    ocr_server.Handler.reader = StandIn()
    ocr_server.Handler.workers = threading.BoundedSemaphore(2)
    ocr_server.Handler.name = "ocr-test"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ocr_server.Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    st, m, ctype = post(port, multipart(png(), '{"languages":["id","en"],"granularity":"chars","region":null,'
                                               '"orientation":"auto","model":null}'))
    check(st == 200 and ctype.startswith("application/json"), "POST /v1/ocr answers 200 with JSON", (st, m))
    check(m["version"] == 1 and m["model"] == "ocr-test", "version 1, model named", m)
    check(m["image"] == {"width": 400, "height": 300} and m["orientation"] == 0, "image size and orientation", m)
    check(isinstance(m["timing_ms"]["total"], int) and "detect" in m["timing_ms"], "timing_ms: the total and the reader's parts",
          m["timing_ms"])
    latin = "Café \U0001F44D\U0001F3FD ok"
    check(m["text"] == latin + "\nNIK 3171\n\nمرحبا 15", "text: lines joined by \\n, blocks by \\n\\n", m["text"])
    blocks = m["blocks"]
    check(len(blocks) == 2 and len(blocks[0]["polygon"]) == 4, "two blocks, each with its polygon", blocks)
    line = blocks[0]["lines"][0]
    check(line["text"] == latin and line["direction"] == "ltr" and line["script"] == "Latn" and line["language"] == "en"
          and line["confidence"] == 0.9 and line["angle"] == 0.0 and len(line["polygon"]) == 4, "line fields", line)
    chars = line["chars"]
    check(len(chars) == 9, "chars: one per grapheme cluster (e + U+0301 is one, a thumb with its skin tone is one)", chars)
    check(chars[3] == [30.0, 50.0] and chars[5] == [60.0, 80.0], "chars: a cluster spans its code points", chars)
    check(line["chars_estimated"] is False, "chars_estimated false", line)
    w = line["words"]
    check(len(w) == 3, "three words", w)
    check(w[0]["text"] == "Café" and (w[0]["start"], w[0]["end"]) == (0, 4) and (w[1]["start"], w[1]["end"]) == (5, 6)
          and w[2]["text"] == "ok" and (w[2]["start"], w[2]["end"]) == (7, 9), "words: text and grapheme indices", w)
    check(abs(w[0]["confidence"] - 0.6) < 1e-9 and abs(w[1]["confidence"] - 1.0) < 1e-9,
          "words: confidence from their own characters", w)
    check(w[0]["polygon"] == [[0.0, 0.0], [50.0, 0.0], [50.0, 10.0], [0.0, 10.0]], "words: polygon along the line", w[0])
    rtl = blocks[1]["lines"][0]
    check(rtl["direction"] == "rtl" and len(rtl["words"]) == 2
          and rtl["words"][1]["polygon"] == [[220.0, 100.0], [220.0, 110.0], [200.0, 110.0], [200.0, 100.0]],
          "right to left: a word's polygon starts at its top-right", rtl["words"])
    check(StandIn.seen[-1] == {"languages": ["id", "en"], "orientation": "auto"},
          "the reader gets the languages and orientation (and no region when none)", StandIn.seen[-1])
    post(port, multipart(png(), '{"region":[1,2,3,4],"orientation":"90"}'))
    check(StandIn.seen[-1] == {"languages": ["auto"], "orientation": 90, "region": [1.0, 2.0, 3.0, 4.0]},
          "the reader gets a region, and \"90\" as 90", StandIn.seen[-1])

    st, m, _ = post(port, multipart(png(), '{"granularity":"lines"}'))
    l0 = m["blocks"][0]["lines"][0]
    check("words" not in l0 and "chars" not in l0, "granularity lines: no words, no chars", l0)
    st, m, _ = post(port, multipart(png(), '{"granularity":"words"}'))
    l0 = m["blocks"][0]["lines"][0]
    check("words" in l0 and "chars" not in l0, "granularity words: words, no chars", l0)
    st, m, _ = post(port, multipart(png(), None, name="file"))
    check(st == 200, "the picture may come as 'file', as for hemera's other uploads", (st, m))

    for body, ctype, what in (
            (b"{}", "application/json", "not multipart"),
            (multipart(None, "{}"), None, "no image part"),
            (multipart(b"hello, this is text", None), None, "not a picture"),
            (multipart(png(), "{languages"), None, "options not JSON"),
            (multipart(png(), '{"languages":"en"}'), None, "languages not a list"),
            (multipart(png(), '{"granularity":"letters"}'), None, "granularity unknown"),
            (multipart(png(), '{"region":[0,0,-5,5]}'), None, "region not positive"),
            (multipart(png(), '{"orientation":45}'), None, "orientation not a quarter turn"),
            (multipart(png(), '{"model":5}'), None, "model not a name")):
        st, m, _ = post(port, body, ctype) if ctype else post(port, body)
        check(st == 400 and isinstance(m.get("error"), str), "400 for %s: %s" % (what, m.get("error")), (st, m))
    st, m, _ = post(port, b"", length=60_000_000)
    check(st == 413 and "50 MB" in m["error"] and "50 million pixels" in m["error"],
          "too large: 413 on the declared length, saying the limits", (st, m))
    StandIn.mode = "huge"
    st, m, _ = post(port, multipart(png(), None))
    StandIn.mode = "normal"
    check(st == 413 and "50000000" in m["error"], "too many pixels: the reader's 413 and its message", (st, m))

    srv.shutdown()
    print("\n%d failed" % len(FAILED) if FAILED else "\nall passed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
