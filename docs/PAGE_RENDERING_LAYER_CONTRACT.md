# QualiBug Shared Page Rendering Layer Contract

## Purpose

The page rendering layer converts visual document sources into source-preserving page
images. It is infrastructure shared by OCR, table reconstruction, diagram analysis and
future visual adapters. It must never infer business meaning.

## Mainline

```text
source bytes
  -> content fingerprint / native adapter
  -> image decoder registry or document renderer registry
  -> RenderedPage[]
  -> OCR / TABLE / DIAGRAM supplemental adapter
  -> Document IR merger
  -> Chinese business fact ledger
  -> Enterprise Understanding Model
```

## RenderedPage contract

Every rendered page must include:

- source page or image-frame number;
- image index when one source page produces multiple images;
- immutable normalized image bytes;
- pixel width and height when known;
- renderer name and version;
- render/decode method;
- render DPI when applicable;
- source locator;
- an explicit declaration that no business semantics were added.

Rendered pages are structural evidence only. Page order is not business process order.

## Image decoding is runtime capability, not a fixed suffix list

The product must not claim image support from a hand-written list such as PNG/JPEG/TIFF.
Formal support exists only when a registered decoder successfully opens the source bytes.
The final receipt records:

- selected decoder and version;
- detected original format;
- decoded frame/page count;
- runtime formats exposed by installed decoders;
- optional plug-ins that were active;
- decoder failures and fallback attempts.

A file may have an unknown or incorrect extension and still be processed when its content
is decodable. Conversely, a filename ending in an image-like extension is not considered
understood when no decoder can open it.

## Image decoder providers

The default image decoder registry contains:

1. `pillow-runtime-image-decoder`
   - consumes every format exposed by the installed Pillow runtime;
   - is not limited to a fixed extension list;
   - supports multi-frame containers such as animated images and multi-page TIFF when the
     installed Pillow build supports them;
   - can automatically register HEIF/HEIC/AVIF through `pillow-heif` when installed;
   - normalizes decoded frames to PNG;
   - enforces pixel and frame-count limits.

2. `cairosvg-vector-image-decoder`
   - optional SVG/SVGZ rasterization provider;
   - is active only when CairoSVG is installed;
   - rejects external network/file references before rendering.

3. `rawpy-camera-image-decoder`
   - optional LibRaw-backed provider for camera RAW families;
   - is active only when rawpy is installed;
   - preserves the original RAW container name in the decode receipt.

Additional enterprise or third-party decoders may be registered without modifying OCR,
Document IR or enterprise understanding code.

## Page renderer providers

The default renderer registry contains:

1. `universal-image-page-renderer`
   - delegates image bytes to the image decoder registry;
   - normalizes all successfully decoded frames into the shared RenderedPage protocol;
   - keeps decoder provenance in page-rendering receipts;
   - does not intercept PDF or Office document containers.

2. `pdfium-pdf-page-renderer`
   - preferred full-page PDF renderer;
   - opens PDF content from immutable bytes;
   - preserves target page selection and DPI;
   - uses the optional `pypdfium2` dependency.

3. `pypdf-embedded-image-renderer`
   - lower-fidelity PDF fallback;
   - extracts embedded images only;
   - does not claim that an embedded image equals the complete page.

4. `libreoffice-office-page-renderer`
   - optional office-to-PDF renderer;
   - supports legacy Word, PowerPoint and OpenDocument containers;
   - requires both a local LibreOffice executable and PDFium rendering support;
   - conversion provenance must remain visible.

## Selection rules

- Native document adapters always run before visual supplements when available.
- OCR must not run eagerly beside a successful native PDF or DOCX adapter.
- Scanned pages trigger rendering and OCR only for the affected page numbers.
- A standalone image or unsupported visual office source may use rendered OCR as a
  fail-visible fallback.
- Spreadsheet families are not silently downgraded to OCR when table/formula/style
  structure is the required authority.
- Content signatures and successful decoding outrank filename extensions.
- Document containers must not be mistaken for image files because they contain previews.

## Failure rules

The layer must return a blocked receipt when:

- no decoder or renderer matches the source;
- every matching provider fails;
- a provider returns no requested pages or frames;
- requested page numbers cannot be produced;
- an image exceeds configured pixel/frame safety limits.

Canonical reason codes include:

```text
IMAGE_DECODER_UNAVAILABLE_OR_FAILED
PAGE_RENDERER_UNAVAILABLE_OR_FAILED
PAGE_RENDERING_TARGET_PAGES_INCOMPLETE
```

Failure must never be replaced with an empty successful document.

## Evidence rules

OCR blocks produced from rendered pages must retain:

- OCR provider and confidence;
- rendered-page source locator;
- page renderer name and version;
- image decoder name, source format and decode method through the render receipt;
- rendering method and DPI;
- page-local image coordinates;
- original source identity.

A fact may be promoted only after it is uniquely aligned back to a Document IR block.

## Extension rule

Future table and diagram adapters must consume `RenderedPage` through the registry. They
must not add new PDF, PPT or image rendering code inside their own modules.

Adding a new decoder/renderer is allowed by registration. Adding file-format branches to
the enterprise understanding layer is forbidden.

## Dependency policy

The proprietary product build must not silently introduce reciprocal copyleft rendering
libraries. The default optional PDF renderer is `pypdfium2`. Optional image plug-ins are
not treated as installed merely because the source suffix belongs to their family; their
runtime availability must appear in the capability receipt.

## Deployment

Base Pillow decoding is included in the main project dependencies. Optional providers can
be installed according to deployment requirements:

```text
pillow-heif   -> HEIF / HEIC / AVIF integration with Pillow
cairosvg      -> SVG / SVGZ rasterization
rawpy         -> camera RAW decoding
```

PDF rendering support:

```bash
pip install -e '.[render]'
```

OCR support:

```bash
pip install -e '.[ocr,render]'
```

LibreOffice remains a system dependency and is not installed by Python packaging.
