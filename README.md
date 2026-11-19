# ocr-server

Text in pictures, with where each line, word and character is, over HTTP. It is made for Live
Text: a person points at a picture and selects, copies or looks up what it says. It reads with
PaddleOCR's models in ONNX form on the CPU:
- **Lines:** the PP-OCRv6 medium line detector and recogniser (Chinese, Japanese, English and 46
  Latin-script languages).
- **Other scripts:** PP-OCRv5 recognisers for Korean, Arabic, Thai, Devanagari, Cyrillic, Greek,
  Tamil and Telugu.
- **Orientation:** two PP-LCNet classifiers, one for the page and one for each line.

It is an engine for [hemera](https://github.com/peprasetya/hemera), the model router on deepmagpie.
hemera starts it when a picture is first sent, passes `POST /v1/ocr` through unchanged, and stops
it when it has been idle a while. It also runs on its own.

| Call | Purpose |
|---|---|
| `POST /v1/ocr` | multipart: the picture in a part named `image` (or `file`); an optional `options` part, `{"languages", "granularity", "region", "orientation"}`. Answers the lines, words and characters in reading order, with polygons. |
| `GET /health` | 200 once the models are loaded. |
| `GET /info` | The models, the scripts they read, the limits. |
| `POST /ocr` | The reader alone, as hemera called it until 2026-11-19 (the picture as the body, options in an `X-Options` header). Kept so an older hemera still works. |

The API's reference, with every field, is hemera's
[docs/ocr-api.md](https://github.com/peprasetya/hemera/blob/main/docs/ocr-api.md).

**Private by design.**
- **Storage:** pictures and text live in memory for the length of a request.
- **Logging:** the log says how large a picture was, how many lines it had and how long it took,
  never what it said.
- **Network:** nothing is fetched or sent.

## What is in here

| File | What it does |
|---|---|
| `ocr_server.py` | The reader: detection in stitched tiles at full resolution, recognition, scripts, tidying, layout and reading order, and the HTTP server. |
| `ocr_api.py` | `POST /v1/ocr` as clients see it: checking the request, and giving the answer its shape (grapheme clusters, words, polygons, the text in reading order). |
| `requirements.txt` | The Python packages, pinned. |
| `engine.json` | What this engine serves, how to run it, what it downloads, and how its models look on disk. |
| `setup/fetch.sh`, `setup/models.tsv`, `setup/models-small.tsv` | The downloads, pinned and checked (below). |
| `tests/test_answer.py` | The request's checks and the answer's shape, through the real HTTP handler with a stand-in reader (no models needed). |
| `bench/` | The generated test set (`make_testset.py`), the benchmark (`bench.py`) and its results (`RESULTS.md`), and `compare_answers.py`. |

## Setting it up

Everything goes in one directory, here `/media/shared/AIModels/ocr`, which is called `DIR` below.

### 1. The models (221 MB)

```bash
setup/fetch.sh DIR
```

Each file is fetched at a fixed revision, resumably and one after another. Each is checked: the
models by sha256, their small `inference.yml` files by their git blob hash. A file that fails is
set aside as `.bad`; rerun the script to continue.

All twelve come from huggingface.co/PaddlePaddle, under Apache-2.0. Each holds `inference.onnx`
and `inference.yml`.

| Model | Revision | Size |
|---|---|---|
| `PP-OCRv6_medium_det_onnx` (finds the lines) | `61323801` | 62.0 MB |
| `PP-OCRv6_medium_rec_onnx` (reads Chinese, Japanese, English, Latin) | `50c7eaca` | 76.7 MB |
| `korean_`, `arabic_`, `th_`, `devanagari_`, `cyrillic_`, `el_`, `ta_`, `te_` + `PP-OCRv5_mobile_rec_onnx` | see `setup/models.tsv` | 7.8–13.5 MB each |
| `PP-LCNet_x1_0_doc_ori_onnx` (the page's quarter turn) | `7330ab70` | 6.8 MB |
| `PP-LCNet_x1_0_textline_ori_onnx` (a line upside down) | `7fdcf3cf` | 6.8 MB |

`setup/models-small.tsv` adds `PP-OCRv6_small_det_onnx` and `PP-OCRv6_small_rec_onnx` (31 MB),
used with `--det PP-OCRv6_small_det --rec PP-OCRv6_small_rec`. They are twice as fast, but read
Latin worse: 2.7% of characters wrong instead of 0.3%. Fetch them with
`setup/fetch.sh DIR setup/models.tsv setup/models-small.tsv`.

### 2. Python

Python 3.13 and the packages in `requirements.txt`, all of them prebuilt wheels: numpy, ONNX
Runtime, OpenCV (headless), Pillow, PyYAML, and `regex` for grapheme clusters.

```bash
uv venv --python 3.13 DIR/venv
uv pip install --python DIR/venv/bin/python -r requirements.txt
```

### 3. Try it on its own

```bash
cd /tmp && DIR/venv/bin/python -I ocr_server.py --port 8191 --models DIR/models
curl -s localhost:8191/health
curl -s -F image=@screenshot.png -F 'options={"languages":["id","auto"]}' localhost:8191/v1/ocr | jq -r .text
```

### 4. In hemera

```json
"engines": {
  "hemera-ocr": {
    "binaryPath": "DIR/venv/bin/python",
    "argStyle": "custom",
    "healthPath": "/health",
    "gpu": false,
    "defaultArgs": ["-I", "DIR/server/ocr_server.py", "--port", "{port}", "--models", "{file}"]
  }
},
"models": [
  { "name": "pp-ocrv6", "engine": "hemera-ocr", "task": "ocr", "file": "DIR/models",
    "args": ["--threads", "8", "--workers", "2", "--name", "pp-ocrv6"], "keepAliveSeconds": 900,
    "scripts": ["Latn", "Hani", "Jpan", "Hang", "Arab", "Thai", "Deva", "Cyrl", "Grek", "Taml", "Telu"],
    "charBoxes": "exact", "licence": "Apache-2.0 (PaddleOCR models)" }
],
"ocrModel": "pp-ocrv6"
```

- **`"task": "ocr"`:** makes hemera route `/v1/ocr` to it.
- **`--name`:** the model name the answers carry.
- **`--workers`:** how many pictures are read at once; each gets `--threads` threads.
- **`"gpu": false`:** tells hemera it never needs GPU memory.

## Measured on deepmagpie

Measured on 2026-11-19, through hemera, on the 33 generated pictures of `bench/make_testset.py`
(no real documents). `bench/RESULTS.md` has the whole benchmark and why these models won.

| | |
|---|---|
| Characters wrong (whitespace left out) | Latin 0.3%; Arabic 0.7%; Chinese 1.2%; Devanagari 1.4%; Japanese 2.3%; Korean 3.9%; Thai 4.3%; Cyrillic 4.7% |
| Digit strings exact | 261 of 262, and the NIK on both the card and its photo |
| Lines found | 414 of 432 |
| Reading order | right for 391 of 392 consecutive pairs |
| Time (warm) | phone screenshot 1.1 s; 1080p frame 0.9 s; 4K frame 2.8 s; 4000×3000 photo 3.6 s; A4 page at 300 dpi 4.5 s |
| First picture after a start | about 1.3 s more |
| Memory | 0.6 GB idle, up to 3 GB while reading two large pictures, given back after each |

**Weaknesses:**
- motion-blurred signs in video frames are often missed;
- spaces are sometimes dropped (": RINA" becomes ":RINA", "KANAL 7 HD" becomes "KANAL7HD");
- vertical Japanese can lose a 、;
- full-width and half-width punctuation can be confused in CJK lines;
- l can be read as 1 ("Jl." becomes "J1.");
- Thai "โทร" was read as "โns" once.

## How it moved here

Until 2026-11-19 this engine lived in hemera's repository (`engines/ocr/`). hemera's Java code,
OcrApi, checked the requests and gave the answers their shape. Following the rule that hemera
only routes to separate engines, that part moved here as `ocr_api.py`, a line-by-line port with
the same messages. hemera now only picks the model and passes the request through.

Through hemera, the same 33 pictures were read by the old way and the new: with the default
options, and seven of them also for words only, lines only, a region and a given orientation.
All 61 answers were the same, field for field (`bench/compare_answers.py`). The one difference
found on the way: Python's `sum()` adds floats with compensation, which rounded five word
confidences differently at the third decimal. Those are now summed one by one, as Java did.

## Licence

This server's own code is MIT (see `LICENSE`). PaddleOCR's models are Apache-2.0.
