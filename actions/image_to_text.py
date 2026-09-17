from pathlib import Path

from actions.logger import log

_OCR_ENGINE = None


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE

    try:
        import onnxruntime  # noqa: F401
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "Image OCR requires rapidocr and onnxruntime. "
            "Install dependencies with: python -m pip install -r requirements.txt. "
            f"Import error: {exc}"
        ) from exc

    _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def image_to_text(
    image_path,
    *,
    min_score=0.0,
    separator="\n",
    strip=True,
):
    """Recognize text in an image and return it as a string.

    Args:
        image_path: Path to a PNG, JPG, BMP, or other supported image.
        min_score: Ignore OCR results below this confidence score (0.0-1.0).
        separator: Text inserted between recognized text regions.
        strip: Remove whitespace around each recognized region.
    """
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"OCR image not found: {path}")
    if not 0.0 <= min_score <= 1.0:
        raise ValueError("min_score must be between 0.0 and 1.0")

    result = _get_ocr_engine()(path)
    texts = result.txts or ()
    scores = result.scores or ()

    recognized = []
    for text, score in zip(texts, scores):
        if float(score) < min_score:
            continue

        value = str(text)
        if strip:
            value = value.strip()
        if value:
            recognized.append(value)

    output = separator.join(recognized)
    log(
        f"image OCR complete path={path} "
        f"regions={len(recognized)} characters={len(output)}"
    )
    return output


def image_to_regions(image_path, *, min_score=0.0, strip=True):
    """Recognize text and return text, score, and bounding box per region."""
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"OCR image not found: {path}")
    if not 0.0 <= min_score <= 1.0:
        raise ValueError("min_score must be between 0.0 and 1.0")

    result = _get_ocr_engine()(path)
    texts = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    boxes = result.boxes if result.boxes is not None else ()

    regions = []
    for text, score, box in zip(texts, scores, boxes):
        if float(score) < min_score:
            continue

        value = str(text)
        if strip:
            value = value.strip()
        if not value:
            continue

        points = [(float(point[0]), float(point[1])) for point in box]
        regions.append(
            {
                "text": value,
                "score": float(score),
                "box": points,
            }
        )

    log(f"image OCR regions path={path} regions={len(regions)}")
    return regions
