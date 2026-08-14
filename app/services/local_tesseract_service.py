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
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float | None
    page_num: int
    block_num: int
    par_num: int
    line_num: int
    word_num: int

    @property
    def center_y(self):
        return self.top + self.height / 2

    @property
    def line_key(self):
        return self.page_num, self.block_num, self.par_num, self.line_num


@dataclass(frozen=True)
class OcrResult:
    raw_text: str
    lines: tuple[OcrLine, ...]
    words: tuple[OcrWord, ...] = ()
    image_width: int = 0
    image_height: int = 0


def _parse_tsv(output):
    groups = {}
    parsed_words = []
    image_width = 0
    image_height = 0
    reader = csv.DictReader(io.StringIO(output), delimiter="\t")
    for row in reader:
        if row.get("level") == "1":
            image_width = max(image_width, int(row.get("width") or 0))
            image_height = max(image_height, int(row.get("height") or 0))
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = tuple(row.get(name) for name in ("page_num", "block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append(row)
        confidence = float(row.get("conf", -1))
        parsed_words.append(OcrWord(
            text=text,
            left=int(row["left"]), top=int(row["top"]),
            width=int(row["width"]), height=int(row["height"]),
            confidence=confidence if confidence >= 0 else None,
            page_num=int(row.get("page_num") or 0),
            block_num=int(row.get("block_num") or 0),
            par_num=int(row.get("par_num") or 0),
            line_num=int(row.get("line_num") or 0),
            word_num=int(row.get("word_num") or 0),
        ))

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
    parsed_words.sort(key=lambda word: (word.top, word.left))
    return tuple(lines), tuple(parsed_words), image_width, image_height


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
    lines, words, image_width, image_height = _parse_tsv(completed.stdout)
    return OcrResult(
        "\n".join(line.text for line in lines), lines, words,
        image_width=image_width, image_height=image_height,
    )
