from dataclasses import dataclass
import csv
import io
import subprocess


class OcrError(RuntimeError):
    pass


class OcrTimeoutError(OcrError):
    pass


@dataclass(frozen=True)
class OcrLine:
    text: str
    top: int
    left: int
    width: int
    height: int
    confidence: float | None = None


@dataclass(frozen=True)
class OcrResult:
    raw_text: str
    lines: tuple[OcrLine, ...]


def _parse_tsv(output):
    groups = {}
    reader = csv.DictReader(io.StringIO(output), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = tuple(row.get(name) for name in ("page_num", "block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append(row)

    lines = []
    for words in groups.values():
        text = " ".join(word["text"].strip() for word in words)
        left = min(int(word["left"]) for word in words)
        top = min(int(word["top"]) for word in words)
        right = max(int(word["left"]) + int(word["width"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        confidences = [float(word["conf"]) for word in words if float(word.get("conf", -1)) >= 0]
        lines.append(OcrLine(text, top, left, right - left, bottom - top,
                             sum(confidences) / len(confidences) if confidences else None))
    lines.sort(key=lambda line: (line.top, line.left))
    return tuple(lines)


def extract_text_from_image(image_path, timeout=15):
    command = [
        "tesseract", str(image_path), "stdout", "-l", "rus+eng",
        "--psm", "6", "tsv",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            check=False, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrTimeoutError("Распознавание изображения заняло слишком много времени") from exc
    except OSError as exc:
        raise OcrError("Локальный OCR недоступен") from exc
    if completed.returncode != 0:
        raise OcrError("Не удалось распознать изображение")
    lines = _parse_tsv(completed.stdout)
    return OcrResult("\n".join(line.text for line in lines), lines)
