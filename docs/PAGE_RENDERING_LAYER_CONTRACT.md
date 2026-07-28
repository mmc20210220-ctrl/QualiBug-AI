# QualiBug Shared Page Rendering Layer Contract

## Purpose

The page rendering layer converts visual document sources into source-preserving page
images. It is infrastructure shared by OCR, table reconstruction, diagram analysis and
future visual adapters. It must never infer business meaning.

## Mainline

```text
source bytes
  -> content fingerprint / native adapter
  -> concrete visual gap or standalone visual source
  -> PageRendererRegistry
  -> RenderedPage[]
  -> OCR / TABLE / DIAGRAM supplemental adapter
  -> Document IR merger
  -> Chinese business fact ledger
  -> Enterprise Understanding Model
```

## RenderedPage contract

Every rendered page must include:

- source page number;
- image index when one source page produces multiple images;
- immutable image bytes;
- pixel width and height when known;
- renderer name and version;
- render method;
- render DPI when applicable;
- source locator;
- an explicit declaration that no business semantics were added.

Rendered pages are structural evidence only. Page order is not business process order.

## Providers

The default registry contains:

1. `raster-image-page-renderer`
   - normalizes raster images and multi-frame images to PNG;
   - may pass through unreadable raster bytes only as unverified input; the downstream
     provider must still fail visibly if the bytes are invalid.

2. `pymupdf-pdf-page-renderer`
   - preferred full-page PDF renderer;
   - preserves target page selection and DPI;
   - requires the optional `render` dependency.

3. `pypdf-embedded-image-renderer`
   - lower-fidelity PDF fallback;
   - extracts embedded images only;
   - does not claim that an embedded image equals the complete page.

4. `libreoffice-office-page-renderer`
   - optional office-to-PDF renderer;
   - supports legacy Word, PowerPoint, OpenDocument and spreadsheet containers;
   - requires both a local LibreOffice executable and PyMuPDF;
   - conversion provenance must remain visible.

## Selection rules

- Native document adapters always run before visual supplements when available.
- OCR must not run eagerly beside a successful native PDF or DOCX adapter.
- Scanned pages trigger rendering and OCR only for the affected page numbers.
- A standalone image or unsupported visual office source may use rendered OCR as a
  fail-visible fallback.
- Spreadsheet families are not silently downgraded to OCR when table/formula/style
  structure is the required authority.

## Failure rules

The layer must return a blocked receipt when:

- no renderer matches the source;
- every matching renderer fails;
- a renderer returns no requested pages;
- requested page numbers cannot be produced.

The canonical reason code is:

```text
PAGE_RENDERER_UNAVAILABLE_OR_FAILED
```

Rendering failure must never be replaced with an empty successful document.

## Evidence rules

OCR blocks produced from rendered pages must retain:

- OCR provider and confidence;
- rendered-page source locator;
- page renderer name and version;
- rendering method and DPI;
- page-local image coordinates;
- original source identity.

A fact may be promoted only after it is uniquely aligned back to a Document IR block.

## Extension rule

Future table and diagram adapters must consume `RenderedPage` through the registry. They
must not add new PDF, PPT or image rendering code inside their own modules.

Adding a new renderer is allowed by registration. Adding file-format branches to the
enterprise understanding layer is forbidden.

## Deployment

Python rendering support:

```bash
pip install -e '.[render]'
```

OCR support:

```bash
pip install -e '.[ocr,render]'
```

LibreOffice remains a system dependency and is not installed by Python packaging.
