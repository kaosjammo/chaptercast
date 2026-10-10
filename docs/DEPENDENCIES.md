# Dependencies and licences

Checked on 10/10/2026 for the locked Linux CPU environment (Python 3.11).
ChapterCast source, original test prose and original app icons use the repository
[MIT licence](../LICENSE). This does not apply to imported chapters, generated
narration of those chapters, third-party code, models or training data. No weights,
voices, EPUBs, audio or dependency binaries are distributed in this repository.

## Sources and runtime components

| Component | Locked version/source | Licence/source notice |
| --- | --- | --- |
| FastAPI | 0.143.0, PyPI | MIT, installed distribution notice |
| Uvicorn | 0.54.0, PyPI | BSD-3-Clause, installed distribution notice |
| Beautiful Soup | 4.15.0, PyPI | MIT, installed distribution notice |
| defusedxml | 0.7.1, PyPI | Python Software Foundation, installed distribution notice |
| SQLite / Python | system Python 3.11 or 3.12 and its SQLite library | [SQLite public domain](https://www.sqlite.org/copyright.html); [Python licence](https://docs.python.org/3/license.html), including bundled components |
| Kokoro / Misaki | 0.9.4 / 0.9.4, PyPI | Apache-2.0; installed notices and upstream [Kokoro](https://github.com/hexgrad/kokoro/blob/main/LICENSE), [Misaki](https://github.com/hexgrad/misaki/blob/main/LICENSE) |
| Kokoro model and British voices | v1.0, `hexgrad/Kokoro-82M`, revision `f3ff3571791e39611d31c381e3a41a3af07b4987` | [Apache-2.0 model card](https://huggingface.co/hexgrad/Kokoro-82M); [pinned source tree](https://huggingface.co/hexgrad/Kokoro-82M/tree/f3ff3571791e39611d31c381e3a41a3af07b4987) |
| PyTorch | 2.6.0+cpu on Linux, official CPU index | BSD-style project licence plus bundled third-party notices in installed `torch` distribution `LICENSE`; [upstream](https://github.com/pytorch/pytorch/blob/v2.6.0/LICENSE) |
| Transformers | 4.51.3, PyPI | Apache-2.0, installed notice |
| spaCy / English model | 3.8.16 / `en_core_web_sm` 3.8.0, PyPI / official GitHub release | MIT; retain the model's `LICENSES_SOURCES` (WordNet notice, OntoNotes licensed by Explosion, reference-only ClearNLP citation) |
| FFmpeg / ffprobe | system tools, not vendored; verification used n9.0.2 | [Build-dependent LGPL/GPL](https://ffmpeg.org/legal.html). The inspected build has `--enable-gpl --enable-version3`, reports GPL-3.0-or-later, and supports `libopus` plus native AAC. Inspect your own `ffmpeg -L` / `-buildconf`. |
| Opus | encoder supplied by system FFmpeg `libopus` | [BSD-style Opus licence and patent notices](https://opus-codec.org/license/); preserve notices if bundling |

The model setup downloads only config, weights and `bf_emma`, `bf_isabella`,
`bm_george`, `bm_lewis`. Private `models/provenance.json` records repository,
revision, package versions, sizes and SHA256 hashes. The model card includes
training-data attribution, including CC BY datasets; consult those upstream
notices when distributing model assets. ChapterCast makes no independent claim
about training-data rights or a licence to narrate someone else's work.

## Copyleft and packaging boundaries

The optional TTS stack includes `phonemizer-fork` 3.3.2 (GPL-3.0-or-later) and
`num2words` 0.5.14 (LGPL-2.1 installed COPYING). `espeakng-loader` 0.2.4 ships a
native espeak-ng library/data. The loader [source licence is MIT](https://github.com/thewh1teagle/espeakng-loader/blob/main/LICENSE),
but [espeak-ng uses GPL v3](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING).
The loader wheel has no declared licence or bundled licence file in its package
metadata; its wrapper licence must not be treated as the native library licence.
These findings prevent describing the entire speech stack as MIT/Apache-only.

This delivery is public source with privately installed dependencies and private
local inference. A distributable executable, container, copied `.venv`, model
bundle or installer containing dependencies is a separate packaging decision.
Before shipping one, account for every included component, retain licences and
attributions, establish required corresponding-source provision for GPL/LGPL
components and evaluate the combined distribution's terms. Resolve the loader's
missing bundled notices/source provenance; do not claim blanket binary licence
clearance. FFmpeg is invoked as an external program; no FFmpeg binary/library is
included here. AAC/codec patent questions are separate from source licences.

## Locked package inventory

The following inventory covers all 99 third-party Python distributions actually
installed by `uv sync --locked --extra dev --extra tts` in the Linux CPU test
`.venv`. Values come from each installed distribution's `License-Expression`,
licence classifiers and shipped notices; links identify the exact upstream
package metadata. Generic BSD classifiers are deliberately not promoted to a
specific variant without checking the notice. Torch's `+cpu` build uses the
explicit [official CPU index](https://download.pytorch.org/whl/cpu), not a CUDA
wheel. The source of `en_core_web_sm` is its
[official 3.8.0 release](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0).
The inventory includes dev tools and build support, rather than claiming every
package is loaded at runtime. Platform-specific packages on other operating
systems require their own installed inventory; `uv.lock` also records those
resolution branches. Installed bundled notices take precedence over this summary.

| Distribution | Installed version | Declared licence / notice | Upstream metadata |
| --- | --- | --- | --- |
| addict | 2.4.0 | MIT | [metadata](https://pypi.org/project/addict/2.4.0/) |
| annotated-doc | 0.0.5 | MIT | [metadata](https://pypi.org/project/annotated-doc/0.0.5/) |
| annotated-types | 0.8.0 | MIT | [metadata](https://pypi.org/project/annotated-types/0.8.0/) |
| anyio | 4.15.1 | MIT | [metadata](https://pypi.org/project/anyio/4.15.1/) |
| attrs | 26.1.0 | MIT | [metadata](https://pypi.org/project/attrs/26.1.0/) |
| babel | 2.18.0 | BSD (see installed notice) | [metadata](https://pypi.org/project/babel/2.18.0/) |
| beautifulsoup4 | 4.15.0 | MIT | [metadata](https://pypi.org/project/beautifulsoup4/4.15.0/) |
| blis | 1.3.3 | BSD (see installed notice) | [metadata](https://pypi.org/project/blis/1.3.3/) |
| catalogue | 2.0.10 | MIT | [metadata](https://pypi.org/project/catalogue/2.0.10/) |
| certifi | 2026.7.22 | MPL-2.0 | [metadata](https://pypi.org/project/certifi/2026.7.22/) |
| charset-normalizer | 3.5.2 | MIT | [metadata](https://pypi.org/project/charset-normalizer/3.5.2/) |
| click | 8.5.0 | BSD-3-Clause | [metadata](https://pypi.org/project/click/8.5.0/) |
| cloudpathlib | 0.26.0 | MIT | [metadata](https://pypi.org/project/cloudpathlib/0.26.0/) |
| cloudpickle | 3.1.2 | BSD (see installed notice) | [metadata](https://pypi.org/project/cloudpickle/3.1.2/) |
| confection | 1.3.3 | MIT | [metadata](https://pypi.org/project/confection/1.3.3/) |
| csvw | 4.1.0 | Apache-2.0 | [metadata](https://pypi.org/project/csvw/4.1.0/) |
| curated-tokenizers | 0.0.10 | MIT | [metadata](https://pypi.org/project/curated-tokenizers/0.0.10/) |
| curated-transformers | 0.1.1 | MIT | [metadata](https://pypi.org/project/curated-transformers/0.1.1/) |
| cymem | 2.0.13 | MIT | [metadata](https://pypi.org/project/cymem/2.0.13/) |
| defusedxml | 0.7.1 | PSF | [metadata](https://pypi.org/project/defusedxml/0.7.1/) |
| dlinfo | 2.0.0 | MIT | [metadata](https://pypi.org/project/dlinfo/2.0.0/) |
| docopt | 0.6.2 | MIT | [metadata](https://pypi.org/project/docopt/0.6.2/) |
| en_core_web_sm | 3.8.0 | MIT | [metadata](https://pypi.org/project/en_core_web_sm/3.8.0/) |
| espeakng-loader | 0.2.4 | Not declared in wheel; see note below | [metadata](https://pypi.org/project/espeakng-loader/0.2.4/) |
| fastapi | 0.143.0 | MIT | [metadata](https://pypi.org/project/fastapi/0.143.0/) |
| filelock | 4.1.0 | MIT | [metadata](https://pypi.org/project/filelock/4.1.0/) |
| fsspec | 2026.9.0 | BSD-3-Clause | [metadata](https://pypi.org/project/fsspec/2026.9.0/) |
| h11 | 0.16.0 | MIT | [metadata](https://pypi.org/project/h11/0.16.0/) |
| hf-xet | 1.7.0 | Apache-2.0 | [metadata](https://pypi.org/project/hf-xet/1.7.0/) |
| httpcore | 1.0.9 | BSD-3-Clause | [metadata](https://pypi.org/project/httpcore/1.0.9/) |
| httpx | 0.28.1 | BSD (see installed notice) | [metadata](https://pypi.org/project/httpx/0.28.1/) |
| huggingface_hub | 0.36.2 | Apache-2.0 | [metadata](https://pypi.org/project/huggingface_hub/0.36.2/) |
| idna | 3.20 | BSD-3-Clause | [metadata](https://pypi.org/project/idna/3.20/) |
| iniconfig | 2.3.1 | MIT | [metadata](https://pypi.org/project/iniconfig/2.3.1/) |
| isodate | 0.7.2 | BSD (see installed notice) | [metadata](https://pypi.org/project/isodate/0.7.2/) |
| Jinja2 | 3.1.6 | BSD (see installed notice) | [metadata](https://pypi.org/project/Jinja2/3.1.6/) |
| joblib | 1.6.0 | BSD-3-Clause | [metadata](https://pypi.org/project/joblib/1.6.0/) |
| jsonschema | 4.26.0 | MIT | [metadata](https://pypi.org/project/jsonschema/4.26.0/) |
| jsonschema-specifications | 2025.9.1 | MIT | [metadata](https://pypi.org/project/jsonschema-specifications/2025.9.1/) |
| kokoro | 0.9.4 | Apache-2.0 | [metadata](https://pypi.org/project/kokoro/0.9.4/) |
| language-tags | 1.3.1 | MIT | [metadata](https://pypi.org/project/language-tags/1.3.1/) |
| loguru | 0.7.3 | MIT | [metadata](https://pypi.org/project/loguru/0.7.3/) |
| markdown-it-py | 4.2.0 | MIT | [metadata](https://pypi.org/project/markdown-it-py/4.2.0/) |
| MarkupSafe | 3.0.4 | BSD-3-Clause | [metadata](https://pypi.org/project/MarkupSafe/3.0.4/) |
| mdurl | 0.1.2 | MIT | [metadata](https://pypi.org/project/mdurl/0.1.2/) |
| misaki | 0.9.4 | Apache-2.0 | [metadata](https://pypi.org/project/misaki/0.9.4/) |
| mpmath | 1.3.0 | BSD (see installed notice) | [metadata](https://pypi.org/project/mpmath/1.3.0/) |
| murmurhash | 1.0.15 | MIT | [metadata](https://pypi.org/project/murmurhash/1.0.15/) |
| networkx | 3.6.1 | BSD-3-Clause | [metadata](https://pypi.org/project/networkx/3.6.1/) |
| num2words | 0.5.14 | LGPL-2.1 (installed COPYING) | [metadata](https://pypi.org/project/num2words/0.5.14/) |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [metadata](https://pypi.org/project/numpy/2.4.6/) |
| opentelemetry-api | 1.45.1 | Apache-2.0 | [metadata](https://pypi.org/project/opentelemetry-api/1.45.1/) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [metadata](https://pypi.org/project/packaging/26.3/) |
| phonemizer-fork | 3.3.2 | GPL-3.0-or-later | [metadata](https://pypi.org/project/phonemizer-fork/3.3.2/) |
| pluggy | 1.6.0 | MIT | [metadata](https://pypi.org/project/pluggy/1.6.0/) |
| preshed | 3.0.13 | MIT | [metadata](https://pypi.org/project/preshed/3.0.13/) |
| pydantic | 2.14.0 | MIT | [metadata](https://pypi.org/project/pydantic/2.14.0/) |
| pydantic_core | 2.50.0 | MIT | [metadata](https://pypi.org/project/pydantic_core/2.50.0/) |
| Pygments | 2.21.0 | BSD-2-Clause | [metadata](https://pypi.org/project/Pygments/2.21.0/) |
| pyparsing | 3.3.3 | MIT | [metadata](https://pypi.org/project/pyparsing/3.3.3/) |
| pytest | 9.1.1 | MIT | [metadata](https://pypi.org/project/pytest/9.1.1/) |
| python-dateutil | 2.9.0.post0 | BSD (see installed notice) / Apache-2.0 | [metadata](https://pypi.org/project/python-dateutil/2.9.0.post0/) |
| PyYAML | 6.0.3 | MIT | [metadata](https://pypi.org/project/PyYAML/6.0.3/) |
| rdflib | 7.6.0 | BSD (see installed notice) | [metadata](https://pypi.org/project/rdflib/7.6.0/) |
| referencing | 0.37.0 | MIT | [metadata](https://pypi.org/project/referencing/0.37.0/) |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | [metadata](https://pypi.org/project/regex/2026.9.29/) |
| requests | 2.34.2 | Apache-2.0 | [metadata](https://pypi.org/project/requests/2.34.2/) |
| rfc3986 | 1.5.0 | Apache-2.0 | [metadata](https://pypi.org/project/rfc3986/1.5.0/) |
| rich | 15.0.0 | MIT | [metadata](https://pypi.org/project/rich/15.0.0/) |
| rpds-py | 2026.9.1 | MIT | [metadata](https://pypi.org/project/rpds-py/2026.9.1/) |
| safetensors | 0.8.0 | Apache-2.0 | [metadata](https://pypi.org/project/safetensors/0.8.0/) |
| segments | 2.4.0 | Apache-2.0 | [metadata](https://pypi.org/project/segments/2.4.0/) |
| setuptools | 84.0.0 | MIT | [metadata](https://pypi.org/project/setuptools/84.0.0/) |
| shellingham | 1.5.4 | ISC | [metadata](https://pypi.org/project/shellingham/1.5.4/) |
| six | 1.17.0 | MIT | [metadata](https://pypi.org/project/six/1.17.0/) |
| smart_open | 8.0.2 | MIT | [metadata](https://pypi.org/project/smart_open/8.0.2/) |
| soupsieve | 2.10 | MIT | [metadata](https://pypi.org/project/soupsieve/2.10/) |
| spacy | 3.8.16 | MIT | [metadata](https://pypi.org/project/spacy/3.8.16/) |
| spacy-curated-transformers | 0.3.1 | MIT | [metadata](https://pypi.org/project/spacy-curated-transformers/0.3.1/) |
| spacy-legacy | 3.0.12 | MIT | [metadata](https://pypi.org/project/spacy-legacy/3.0.12/) |
| spacy-loggers | 1.0.5 | MIT | [metadata](https://pypi.org/project/spacy-loggers/1.0.5/) |
| srsly | 2.5.4 | MIT | [metadata](https://pypi.org/project/srsly/2.5.4/) |
| starlette | 1.7.0 | BSD-3-Clause | [metadata](https://pypi.org/project/starlette/1.7.0/) |
| sympy | 1.13.1 | BSD (see installed notice) | [metadata](https://pypi.org/project/sympy/1.13.1/) |
| termcolor | 3.3.0 | MIT | [metadata](https://pypi.org/project/termcolor/3.3.0/) |
| thinc | 8.3.13 | MIT | [metadata](https://pypi.org/project/thinc/8.3.13/) |
| tokenizers | 0.21.4 | Apache-2.0 | [metadata](https://pypi.org/project/tokenizers/0.21.4/) |
| torch | 2.6.0+cpu | BSD (see installed notice) | [metadata](https://pypi.org/project/torch/2.6.0/) |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | [metadata](https://pypi.org/project/tqdm/4.70.1/) |
| transformers | 4.51.3 | Apache-2.0 | [metadata](https://pypi.org/project/transformers/4.51.3/) |
| typer | 0.27.3 | MIT | [metadata](https://pypi.org/project/typer/0.27.3/) |
| typing-inspection | 0.4.4 | MIT | [metadata](https://pypi.org/project/typing-inspection/0.4.4/) |
| typing_extensions | 4.16.0 | PSF-2.0 | [metadata](https://pypi.org/project/typing_extensions/4.16.0/) |
| uritemplate | 4.2.0 | BSD 3-Clause OR Apache-2.0 | [metadata](https://pypi.org/project/uritemplate/4.2.0/) |
| urllib3 | 2.8.0 | MIT | [metadata](https://pypi.org/project/urllib3/2.8.0/) |
| uvicorn | 0.54.0 | BSD-3-Clause | [metadata](https://pypi.org/project/uvicorn/0.54.0/) |
| wasabi | 1.1.3 | MIT | [metadata](https://pypi.org/project/wasabi/1.1.3/) |
| weasel | 1.0.0 | MIT | [metadata](https://pypi.org/project/weasel/1.0.0/) |
| wrapt | 2.5.0 | BSD-2-Clause | [metadata](https://pypi.org/project/wrapt/2.5.0/) |

Preserve installed licence files when retaining or packaging an environment.
The private qualification evidence includes a machine-readable package inventory
and the inspected system FFmpeg licence/build configuration. The Python lockfile
records exact distributions and integrity hashes; setup source/version/hash
receipts for downloaded weights remain private. Do not copy local installation
paths or host identity into this public document.
