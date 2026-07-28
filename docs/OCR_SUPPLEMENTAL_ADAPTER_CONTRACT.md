# OCR Supplemental Adapter Contract

## Purpose

OCR is a structural recovery capability, not a business-semantic authority. It may
recover text and page-local coordinates from rendered pages, but it must not create
business meaning, infer process order, or silently clear unresolved pages.

## Two-phase execution

1. A primary adapter first parses the native source container.
2. The primary adapter exposes fail-visible gaps such as `SCANNED_PAGE_REQUIRES_OCR`.
3. The deferred planner requests `PAGE_RENDERING` and `OCR` for affected pages.
4. `PageRendererRegistry` produces source-preserving `RenderedPage` objects.
5. The OCR adapter returns Document IR blocks and explicit gap resolutions.
6. The central merger applies resolutions page by page and preserves remaining gaps.
7. Merged IR text re-enters Chinese-first fact extraction.
8. Extracted facts are aligned back to exact IR block locators before enterprise model compilation.

OCR must not run over every PDF by default.

## Built-in OCR provider

`TesseractOcrProvider` is optional. It is available only when:

- the `pytesseract` Python package is installed;
- the `tesseract` executable is available on `PATH`;
- requested OCR language data is installed.

Install Python OCR and rendering support with:

```bash
pip install -e '.[ocr,render]'
```

The system-level Tesseract binary and language packs remain deployment responsibilities.
The default language is `chi_sim+eng` and can be changed with `QUALIBUG_OCR_LANG`.

Missing OCR dependencies do not crash document ingestion. The OCR adapter does not match,
and the original scanned-page gap remains formally blocking.

## Supported source paths

### Standalone image source

Image support is not defined by a fixed PNG/JPEG suffix list. The source first enters
`ImageDecoderRegistry`, which selects a decoder by actual bytes and runtime capability.
The default Pillow decoder can consume every format exposed by the installed Pillow build,
including multi-frame containers. Optional registered providers add HEIF/HEIC/AVIF,
SVG/SVGZ and camera RAW families when their dependencies are installed.

Every successful decode is normalized to a rendered page and records the original format,
decoder name/version, frame count and fallback errors. A file with an image-like extension
but undecodable bytes remains blocked.

### Scanned PDF page

The adapter runs only after the PDF primary adapter reports scanned pages. Rendering is
attempted in this order when providers are available:

1. full-page PDFium rasterization;
2. embedded-image extraction through `pypdf` as a lower-fidelity fallback.

Only requested page numbers are rendered. If no renderer produces those pages, the system
reports `PAGE_RENDERER_UNAVAILABLE_OR_FAILED` and keeps them blocked.

### Visual office fallback

Legacy Word, PowerPoint and OpenDocument files may be converted to PDF by a local
LibreOffice installation and then rendered through PDFium. Conversion provenance is
retained. Native adapters still take priority when available.

Spreadsheet families are not silently downgraded to OCR when table, formula and style
structure are required.

## Formal confidence rules

Each OCR line must preserve:

- source id and filename;
- page number;
- rendered-image index;
- page-local bounding box;
- OCR provider and version;
- page renderer and version;
- image decoder and original source format when applicable;
- rendering/decode method and DPI;
- recognition confidence;
- source locator.

The default formal confidence threshold is `0.55`.

- Above threshold: the original scan gap may be resolved for that page, while
  `OCR_PAGE_LAYOUT_PROJECTED` remains a visible P1 limitation.
- Below threshold: `OCR_TEXT_LOW_CONFIDENCE` is P0 and formal understanding remains blocked.
- No text: `OCR_TEXT_NOT_RECOVERED` is P0.
- Provider execution failure: `OCR_PROVIDER_EXECUTION_FAILED` is P0.
- Rendering failure: `PAGE_RENDERER_UNAVAILABLE_OR_FAILED` is P0.
- Image decode failure: `IMAGE_DECODER_UNAVAILABLE_OR_FAILED` is P0.

## Gap-resolution contract

A supplemental adapter may declare:

```json
{
  "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
  "pages": [3, 4],
  "resolution": "OCR_TEXT_RECOVERED"
}
```

The merger may resolve only the listed pages. If the primary gap covers pages `[3, 4, 5]`,
page `5` remains blocked. Adapter output must never clear a whole-document gap merely
because one page succeeded.

## Business-fact authority

OCR text becomes eligible input to Chinese-first fact extraction only after it has entered
merged Document IR. Facts are then aligned back to one unique IR block.

If a fact statement maps to zero or multiple blocks, the system must not select a block
silently. The unresolved alignment remains visible in
`document_ir_fact_evidence_receipt`.

## Known limitations

The current version does not claim:

- semantic reading order for complex multi-column scanned pages;
- handwriting recognition quality;
- table-cell reconstruction;
- flowchart, BPMN, UML or ER-diagram understanding;
- rendering when neither PDFium, LibreOffice nor a usable embedded-image fallback exists;
- decoding of optional image families when their provider is not installed;
- automatic installation of Tesseract or LibreOffice system dependencies;
- automatic installation of OCR language packs.

These limitations must remain visible through structure receipts and enterprise
understanding gates.
