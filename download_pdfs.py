
import re
import urllib.request
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote

BOOKMARK_FILE = Path("doe_bookmarks.html")
OUTPUT_DIR = Path("pdfs")
TARGET_FOLDER = "DOE PDFs3"


class BookmarkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.folder_stack = []
        self.links = []
        self.pending_folder = None
        self.in_folder_heading = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "h3":
            self.in_folder_heading = True
            self.pending_folder = ""

        elif tag.lower() == "dl":
            if self.pending_folder is not None:
                self.folder_stack.append(
                    self.pending_folder.strip()
                )
                self.pending_folder = None

        elif tag.lower() == "a":
            attrs = dict(attrs)
            href = attrs.get("href", "")

            if (
                href.startswith("http")
                and TARGET_FOLDER in self.folder_stack
            ):
                self.links.append(href)

    def handle_data(self, data):
        if self.in_folder_heading:
            self.pending_folder += data

    def handle_endtag(self, tag):
        if tag.lower() == "h3":
            self.in_folder_heading = False

        elif tag.lower() == "dl":
            if self.folder_stack:
                self.folder_stack.pop()


def get_filename(url):
    path = unquote(
        Path(urlparse(url).path).name
    )

    # Remove invalid Windows filename characters.
    filename = re.sub(
        r'[<>:"/\\|?*]', "_", path
    )

    # Make the downloaded PDF recognizable as a PDF.
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    return filename


def main():
    if not BOOKMARK_FILE.exists():
        raise FileNotFoundError(
            f"Missing {BOOKMARK_FILE}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    parser = BookmarkParser()

    parser.feed(
        BOOKMARK_FILE.read_text(
            encoding="utf-8"
        )
    )

    urls = list(dict.fromkeys(parser.links))

    print(
        f"Found {len(urls)} links "
        f"in '{TARGET_FOLDER}'.\n"
    )

    if not urls:
        print(
            "No links found. Check that the "
            "bookmark folder name matches exactly."
        )
        return

    downloaded = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        filename = get_filename(url)
        output_path = OUTPUT_DIR / filename

        if output_path.exists():
            print(
                f"[{i}/{len(urls)}] SKIPPED: {filename}"
            )
            skipped += 1
            continue

        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urllib.request.urlopen(
                request,
                timeout=60
            ) as response:
                data = response.read()

            # Verify that the response is actually a PDF.
            if not data.startswith(b"%PDF"):
                print(
                    f"[{i}/{len(urls)}] NOT A PDF: "
                    f"{filename}"
                )
                failed += 1
                continue

            output_path.write_bytes(data)

            print(
                f"[{i}/{len(urls)}] DOWNLOADED: "
                f"{filename}"
            )

            downloaded += 1

        except Exception as e:
            print(
                f"[{i}/{len(urls)}] FAILED: "
                f"{filename}"
            )
            print(f"    {e}")
            failed += 1

    print("\n========== SUMMARY ==========")
    print(f"Total links:       {len(urls)}")
    print(f"Downloaded:        {downloaded}")
    print(f"Already existed:   {skipped}")
    print(f"Failed:            {failed}")
    print(f"Output folder:     {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
