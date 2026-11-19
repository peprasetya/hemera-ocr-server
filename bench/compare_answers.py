#!/usr/bin/env python3
"""Read the test set through two hemeras and compare the answers field by field (all but the
times). It was written to check that the answer is the same now that the engine builds it
instead of hemera.

    compare_answers.py TESTSET_DIR OUT_DIR URL_A URL_B

URL_A may also be a directory of answers saved by an earlier run (its OUT_DIR/a or /b).

Each picture is read with the default options; some are also read for words only, lines only, a
region, and a given orientation. The answers go to OUT_DIR/{a,b}/, and every difference is
printed.
"""
import json
import os
import sys
import urllib.error
import urllib.request

EXTRA = [("words", {"granularity": "words"}), ("lines", {"granularity": "lines", "languages": ["id"]}),
         ("region", {"region": [10, 10, 600, 300]}), ("upright", {"orientation": 0, "languages": ["en", "auto"]})]


def post(url, path, options):
    boundary = "compare-boundary"
    data = open(path, "rb").read()
    body = (b"--" + boundary.encode() + b'\r\nContent-Disposition: form-data; name="image"; filename="x"\r\n\r\n'
            + data + b"\r\n--" + boundary.encode() + b'\r\nContent-Disposition: form-data; name="options"\r\n\r\n'
            + json.dumps(options).encode() + b"\r\n--" + boundary.encode() + b"--\r\n")
    req = urllib.request.Request(url + "/v1/ocr", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    for _ in range(60):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 503:
                import time
                time.sleep(2)
                continue
            return e.code, json.loads(e.read())
    return 503, {}


def diff(a, b, where, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k == "timing_ms":
                continue
            if k not in a or k not in b:
                out.append("%s.%s only in %s" % (where, k, "a" if k in a else "b"))
            else:
                diff(a[k], b[k], where + "." + k, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append("%s: %d vs %d items" % (where, len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, "%s[%d]" % (where, i), out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(a - b) > 1e-9:
            out.append("%s: %r vs %r" % (where, a, b))
    elif a != b:
        out.append("%s: %r vs %r" % (where, a, b))


def main():
    testset, out_dir, url_a, url_b = sys.argv[1:5]
    pictures = sorted(f for f in os.listdir(testset) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")))
    jobs = [(p, "default", {}) for p in pictures] + [(p, n, o) for p in pictures[:: max(1, len(pictures) // 6)] for n, o in EXTRA]
    same = differ = 0
    for side in ("a", "b"):
        os.makedirs(os.path.join(out_dir, side), exist_ok=True)
    for pic, name, opts in jobs:
        if os.path.isdir(url_a):
            sa, a = 200, json.load(open(os.path.join(url_a, "%s.%s.json" % (pic, name))))
        else:
            sa, a = post(url_a, os.path.join(testset, pic), opts)
        sb, b = post(url_b, os.path.join(testset, pic), opts)
        for side, ans in (("a", a), ("b", b)):
            with open(os.path.join(out_dir, side, "%s.%s.json" % (pic, name)), "w") as f:
                json.dump(ans, f, ensure_ascii=False)
        found = [] if sa == sb else ["status %d vs %d" % (sa, sb)]
        diff(a, b, "", found)
        if found:
            differ += 1
            print("DIFF  %s (%s): %d differences, first: %s" % (pic, name, len(found), "; ".join(found[:3])), flush=True)
        else:
            same += 1
            print("same  %s (%s), %d lines" % (pic, name, sum(len(bl["lines"]) for bl in a.get("blocks", []))), flush=True)
    print("\n%d answers the same, %d different" % (same, differ))
    sys.exit(1 if differ else 0)


if __name__ == "__main__":
    main()
