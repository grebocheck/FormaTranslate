# Text and photo translation

Open **Text & photos**. This workspace uses the connection and model from Settings; it does not start requests until you click Translate. Document translation and quick translation run separately to respect the configured request budget.

## Text

Type or paste up to 20,000 characters, choose the languages and translate. The model translates and then edits the draft, using your editor model if configured. The application preserves original line breaks, indentation, literal code and protected identifiers/links, and checks numeric values before accepting the result. Copy the final text with the copy button. Changing the source or languages invalidates the previous result.

Text remains in memory for this application session. Navigating to another tab or changing the interface language keeps the draft. Closing the app discards quick text and photo results; use Copy or Save PNG to keep them.

## Photos and clipboard screenshots

Choose a PNG, JPEG, WebP, BMP or TIFF, drag one into the photo area, or use Paste photo / Ctrl+V. Clipboard image data and local image-file URLs are supported; web URLs are not downloaded. Source files are never overwritten by image export.

Select a model that accepts image inputs through your provider's configured Chat Completions or Responses API. Text-only models cannot perform this task. [OpenAI's image-input documentation](https://developers.openai.com/api/docs/guides/images-vision) describes the API formats and vision limitations; other compatible providers can differ.

The model returns recognized text, translations and approximate text rectangles. When local Tesseract is available, Forma also reads word positions on the image. It matches complete recognized source spans to those local positions before drawing, correcting displaced or undersized model rectangles. Local OCR hints accompany the existing image request; they do not replace the model's recognized source or authorize changes to numbers. The full recognized text and translation remain readable beside the image. Use Original/Translation and zoom to inspect the result, Copy photo to put it on the clipboard, or Save PNG to export it.

The complete Windows package already includes Tesseract with English, Ukrainian, Polish and German data. Linux uses an installed `tesseract` command and its language data. Photo anchoring additionally supports French and Spanish when their local data is installed. Auto source language uses available supported data. Missing OCR/language data, a local failure or timeout retains the vision-only route; no language models are downloaded automatically. Local OCR runs in an isolated, cancellable process, has a bounded output and removes its temporary output after parsing.

- Input limits: 30 MB, 24 megapixels and 20,000 pixels on either side. JPEG orientation metadata is applied during loading.
- Uploads: at most 2048 pixels per side and 2 megapixels, with metadata removed. The original full-resolution image remains local; output has the original dimensions.
- At most 120 recognized blocks / 20,000 characters per image. One correction request may be made if translated numbers or identifiers differ from recognized text.
- For locally anchored text, the font size is estimated from the actual source words. The renderer first uses adjacent blank space, then reduces the font only as needed. Other text, icons, table borders and image boundaries stop expansion. Very small unreadable results are left unchanged with a warning. Tight OCR boxes are extended to cover connected antialiased glyph edges.
- Background and text colors are sampled locally. On a flat background the overlay follows its color; busy backgrounds may receive opaque panels. This is not image generation or background reconstruction, and arbitrary curved or rotated text is not re-typeset along its original shape.
- Overlapping or unfit blocks remain unchanged with an explicit warning. Their complete translated text is still available beside the image.

Vision and local OCR can omit or misread small, rotated, blurred or unfamiliar text. Local geometry is used only for sufficiently matching source spans; ambiguous repeated labels or unmatched text retain the model's approximate position. The numeric check compares against recognized text, so it cannot prove that OCR read the original correctly. Inspect the original and recognized text; use a clearer photo or crop if needed. This feature does not promise Google Translate's geometry or coverage on every image.

## Validation

The offline suite covers both API formats, malformed/truncated responses, text structure and numeric protection, clipboard behavior, cancellation, PNG export, image bounds, Unicode text fitting, image metadata and pixels outside the actual paint regions. Photo regressions include displaced/tiny vision boxes, local OCR span matching, repeated labels, collision-aware expansion, source glyph coverage, unchanged neighbouring artwork, bounded local process output and cancellation. All six interface languages remain supported.

The new placement was also exercised with installed local Tesseract and a synthetic dark interface screenshot containing sixteen labels, links and table rows. All sixteen were detected, and deliberately displaced eight-pixel model boxes produced readable overlays without overlap warnings. The translations in this placement check came from an offline provider; it measures geometry and rendering, not model translation quality.

A bounded live smoke test used the configured `gpt-6.1-terra` model on a short synthetic English text and a clean synthetic poster, translating into Ukrainian. Three requests completed: text plus editorial review in about 6.1 seconds; photo translation in about 4.5 seconds. All three poster labels were recognized exactly, numerical values were retained, and overlay rectangles covered all original glyph pixels without modifying pixels outside those rectangles. This small example verifies the workflow, not general OCR or professional translation quality. No confidential documents were used.
