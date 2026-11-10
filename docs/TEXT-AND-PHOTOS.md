# Text and photo translation

Open **Text & photos**. This workspace uses the connection and model from Settings; it does not start requests until you click Translate. Document translation and quick translation run separately to respect the configured request budget.

## Text

Type or paste up to 20,000 characters, choose the languages and translate. The model translates and then edits the draft, using your editor model if configured. The application preserves original line breaks, indentation, literal code and protected identifiers/links, and checks numeric values before accepting the result. Copy the final text with the copy button. Changing the source or languages invalidates the previous result.

Text remains in memory for this application session. Navigating to another tab or changing the interface language keeps the draft. Closing the app discards quick text and photo results; use Copy or Save PNG to keep them.

## Photos and clipboard screenshots

Choose a PNG, JPEG, WebP, BMP or TIFF, drag one into the photo area, or use Paste photo / Ctrl+V. Clipboard image data and local image-file URLs are supported; web URLs are not downloaded. Source files are never overwritten by image export.

Select a model that accepts image inputs through your provider's configured Chat Completions or Responses API. Text-only models cannot perform this task. [OpenAI's image-input documentation](https://developers.openai.com/api/docs/guides/images-vision) describes the API formats and vision limitations; other compatible providers can differ.

The model returns recognized text, translations and approximate text rectangles. Forma checks the response and numeric/identifier consistency, then uses Qt to draw the translated text on an image copy. The full recognized text and translation remain readable beside the image. Use Original/Translation and zoom to inspect the result, Copy photo to put it on the clipboard, or Save PNG to export it.

- Input limits: 30 MB, 24 megapixels and 20,000 pixels on either side. JPEG orientation metadata is applied during loading.
- Uploads: at most 2048 pixels per side and 2 megapixels, with metadata removed. The original full-resolution image remains local; output has the original dimensions.
- At most 120 recognized blocks / 20,000 characters per image. One correction request may be made if translated numbers or identifiers differ from recognized text.
- Text is fitted into the returned rectangles. On a flat background the color is sampled; busy backgrounds may receive opaque panels. This is not image generation or background reconstruction.
- Overlapping or unfit blocks remain unchanged with an explicit warning. Their complete translated text is still available beside the image.

Vision models can omit or misread small, rotated, blurred or unfamiliar text and estimate positions incorrectly. The numeric check compares against recognized text, so it cannot prove that OCR read the original correctly. Inspect the original and recognized text; use a clearer photo or crop if needed. This feature does not promise the same geometry or coverage as a dedicated OCR engine.

## Validation

The offline suite covers both API formats, malformed/truncated responses, text structure and numeric protection, clipboard behavior, cancellation, PNG export, image bounds, Unicode text fitting, image metadata and pixels outside detected text regions. UI checks cover the four interface languages at the minimum 960 × 680 size and 150% scaling.

A bounded live smoke test used the configured `gpt-6.1-terra` model on a short synthetic English text and a clean synthetic poster, translating into Ukrainian. Three requests completed: text plus editorial review in about 6.1 seconds; photo translation in about 4.5 seconds. All three poster labels were recognized exactly, numerical values were retained, and overlay rectangles covered all original glyph pixels without modifying pixels outside those rectangles. This small example verifies the workflow, not general OCR or professional translation quality. No confidential documents were used.
