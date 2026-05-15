from pathlib import Path
import urllib.request

FLAGS_DIR = Path("static/flags")

CODES = [
    "ar", "at", "au", "ba", "be", "bf", "br", "ca", "cd", "ch",
    "ci", "co", "cv", "cw", "cz", "de", "dz", "ec", "eg", "es",
    "fr", "gb-eng", "gb-sct", "gh", "hr", "ht", "iq", "ir", "jo", "jp",
    "kr", "ma", "mx", "nl", "no", "nz", "pa", "pt", "py", "qa",
    "ru", "sa", "se", "sn", "tn", "tr", "tt", "us", "uy", "uz", "za"
]

BASE_URL = "https://flagcdn.com/{code}.svg"

SPECIAL = {
    "gb-eng": "gb-eng",
    "gb-sct": "gb-sct",
}

def download_flag(code: str) -> None:
    url_code = SPECIAL.get(code, code)
    url = BASE_URL.format(code=url_code)
    target = FLAGS_DIR / f"{code}.svg"

    print(f"Downloading {code} -> {target}")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = response.read()

        if not data.startswith(b"<svg"):
            print(f"  WARNING: {code} did not return SVG")
            return

        target.write_bytes(data)
        print("  OK")

    except Exception as e:
        print(f"  FAILED: {e}")


def main():
    FLAGS_DIR.mkdir(parents=True, exist_ok=True)

    for code in CODES:
        download_flag(code)


if __name__ == "__main__":
    main()