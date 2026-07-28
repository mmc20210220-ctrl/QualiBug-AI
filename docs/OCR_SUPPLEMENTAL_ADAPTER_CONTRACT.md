# OCR Supplemental Adapter Contract

## Purpose

OCR is a structural recovery capability, not a business-semantic authority. It may
recover text and image-local coordinates from raster images or scanned PDF pages, but it
must not create business meaning, infer process order, or silently clear unresolved pages.

## Two-phase execution

1. A primary adapter first parses the native source container.
2. The primary adapter exposes fail-visible gaps such as `SCANNED_PAGE_REQUIRES_OCR`.
3. The deferred parsing planner requests the `OCR` capability only for affected pages.
4. A supplemental OCR adapter returns Document IR blocks and explicit gap resolutions.
5. The central merger applies resolutions page by page and preserves all remaining gaps.
6. Merged IR text re-enters Chinese-first fact extraction.
7. Extracted facts are aligned back to exact IR block locators before enterprise model compilation.

OCR must not run over every PDF by default.

## Built-in provider

`TesseractOcrProvider` is optional. It is available only when:

- the `pytesseract` Python package is installed;
- the `tesseract` executable is available on `PATH`;
- requested OCR language data is installed.

The Python optional dependency is installed with:

```bash
pip install -e '.[ocr]'
```

The system-level Tesseract binary and language packs remain deployment responsibilities.
The default language is `chi_sim+eng` and can be changed with `QUALIBUG_OCR_LANG`.

Missing OCR dependencies do not crash document ingestion. The OCR adapter simply does
not match, and the original scanned-page gap remains formally blocking.

## Supported source paths

### Standalone raster image

The adapter can run standalone for PNG, JPEG, TIFF, BMP, GIF and WebP sources when an OCR
provider is available.

### Scanned PDF page

The adapter runs only after the PDF primary adapter reports scanned pages. The current
Tesseract implementation extracts embedded page images through `pypdf` and applies OCR to
them. If no recoverable embedded image is available, it reports
`OCR_SOURCE_IMAGE_NOT_AVAILABLE` and keeps the page blocked.

A future page renderer may supplement this path without changing enterprise business
understanding.

## Formal confidence rules

Each OCR line must preserve:

- source id and filename;
- page number;
- embedded-image index;
- image-local bounding box;
- OCR provider and version;
- recognition confidence;
- source locator.

The default formal confidence threshold is `0.55`.

- Above threshold: the original scan gap may be resolved for that page, while
  `OCR_PAGE_LAYOUT_PROJECTED` remains a visible P1 limitation.
- Below threshold: `OCR_TEXT_LOW_CONFIDENCE` is P0 and formal understanding remains blocked.
- No text: `OCR_TEXT_NOT_RECOVERED` is P0.
- Provider execution failure: `OCR_PROVIDER_EXECUTION_FAILED` is P0.

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
page `5` remains blocked.

Adapter output must never clear a whole-document gap merely because one page succeeded.

## Business-fact authority

OCR text becomes eligible input to Chinese-first fact extraction only after it has entered
merged Document IR. Facts are then aligned back to one unique IR block.

If a fact statement maps to zero or multiple blocks, the system must not select a block
silently. The unresolved alignment remains visible in
`document_ir_fact_evidence_receipt`.

## Known limitations

The current first version does not claim:

- authoritative page coordinates for OCR lines; coordinates are local to extracted images;
- correct reconstruction of multi-column reading order inside scanned pages;
- handwriting recognition quality;
- table-cell reconstruction;
- flowchart, BPMN, UML or ER-diagram understanding;
- rendering of every PDF page when no embedded image can be extracted;
- automatic installation of Tesseract language packs.

These limitations must remain visible through structure receipts and enterprise
understanding gates.
