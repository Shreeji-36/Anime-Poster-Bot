"""Run this ONCE on your own computer (NOT on Render):

    python make_samples.py

It prepares three things that the free Render server cannot afford to build itself:
  1. samples/style_N.jpg      -> the /styles preview pictures
  2. .cache/anilist_tvdb.json -> the small anilist->tvdb map (the server would otherwise download a ~25 MB list)
  3. the font files           -> so the server does not download them on every start

Afterwards commit these folders to GitHub (check that .gitignore does not hide them) and redeploy.
"""
import asyncio
import io

import httpx
from PIL import Image

import config
import dataset


async def main():
    print("1/3 fonts ...")
    missing = await asyncio.to_thread(dataset.ensure_fonts)
    if missing:
        print("   could not download:", ", ".join(missing))

    print("2/3 anilist->tvdb map ...")
    async with httpx.AsyncClient(headers=dataset.HEADERS, follow_redirects=True) as client:
        await dataset._tvdb_id_for(client, 1)            # downloads once and saves .cache/anilist_tvdb.json
    print("   saved:", dataset._MAP_FILE)

    print("3/3 style previews ...")
    anime = await dataset.load_anime(config.SAMPLE_ANIME_ID)
    config.SAMPLES_DIR.mkdir(exist_ok=True)
    for n in dataset.POSTERS:
        try:
            r = await dataset.make_poster(n, anime, {})
        except Exception as e:                            # one bad style must not stop the others
            print(f"   style {n}: FAILED ({e})")
            continue
        im = Image.open(io.BytesIO(r.jpg)).convert("RGB")
        im.thumbnail((1280, 1280))
        out = config.SAMPLES_DIR / f"style_{n}.jpg"
        im.save(out, "JPEG", quality=88)
        print(f"   style {n}: saved {out}")

    print("\nDONE. Now commit these to GitHub:")
    print("  ", config.SAMPLES_DIR)
    print("  ", dataset._MAP_FILE.parent)
    print("  ", config.FONT_DIR)
    print("  ", dataset.TEMPLATES_DIR / "fonts")


if __name__ == "__main__":
    asyncio.run(main())