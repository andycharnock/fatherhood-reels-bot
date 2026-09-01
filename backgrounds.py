"""Background footage.

A plain Python list. No database table, no admin screen. To change the
footage, edit this list and redeploy.

Rules for the URLs:
  - Must be a direct link to an .mp4 file, not a Pexels web page.
    Right-click the download button on Pexels and copy the link address.
  - Prefer clips of 20 seconds or longer.
  - Vertical or square footage crops better to 1080x1920 than wide footage.
"""

import random

BACKGROUND_URLS = [
    # Replace every line below with your own Pexels .mp4 links.
    # The two examples are Shotstack's own test files and will work as-is
    # for a first end-to-end test.
    "https://shotstack-assets.s3-ap-southeast-2.amazonaws.com/footage/beach-overhead.mp4",
    "https://shotstack-assets.s3-ap-southeast-2.amazonaws.com/footage/night-sky.mp4",
]


def pick() -> str:
    """Return one background URL at random."""
    if not BACKGROUND_URLS:
        raise RuntimeError("BACKGROUND_URLS is empty. Add at least one .mp4 URL.")
    return random.choice(BACKGROUND_URLS)
