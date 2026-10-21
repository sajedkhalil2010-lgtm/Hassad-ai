"""End-to-end check of the exact request the test UI issues.

Generates a synthetic leaf image, POSTs it as multipart `file` to
/api/v1/analyze, and renders the same summary fields the UI would show.
"""

from __future__ import annotations

import io
import json
import sys

from PIL import Image, ImageDraw


def make_leaf() -> bytes:
    """A synthetic green leaf with brown necrotic lesions."""
    img = Image.new("RGB", (420, 420), (48, 124, 48))
    dr = ImageDraw.Draw(img)
    dr.ellipse([40, 40, 380, 380], fill=(52, 132, 52))  # leaf body
    dr.line([210, 40, 210, 380], fill=(36, 100, 36), width=4)  # midrib
    for x, y, r in [(140, 150, 40), (250, 240, 30), (180, 300, 20), (300, 140, 22)]:
        dr.ellipse([x - r, y - r, x + r, y + r], fill=(126, 92, 42))
        dr.ellipse([x - r // 2, y - r // 2, x + r // 2, y + r // 2], fill=(72, 52, 26))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    import httpx

    base = "http://127.0.0.1:8000"

    print(f"\n=== Backend reachable at {base} ===")
    h = httpx.get(f"{base}/health", timeout=10)
    health = h.json()
    print(f"  /health -> {h.status_code} | pipeline={health.get('pipeline')} "
          f"| model={health.get('vision_model')} | configured={health.get('configured')}")

    page = httpx.get(f"{base}/", timeout=10)
    print(f"  GET /   -> {page.status_code} ({len(page.content)} bytes)")

    print("\n=== POST /api/v1/analyze (live Groq call) ===")
    png = make_leaf()
    print(f"  payload: synthetic leaf, {len(png)} bytes")

    r = httpx.post(
        f"{base}/api/v1/analyze",
        files={"file": ("leaf.png", png, "image/png")},
        timeout=240,
    )
    body = r.json()

    print(f"  HTTP {r.status_code} | ok={body.get('ok')} | status={body.get('status')}")

    if body.get("ok") is not True:
        print("\n  PIPELINE DID NOT SUCCEED. Raw response:")
        print(json.dumps(body, indent=2, ensure_ascii=False)[:1500])
        return 1

    dg = body.get("diagnosis") or {}
    ad = body.get("advisory") or {}

    print("\n  --- summary (as the UI would render) ---")
    rows = [
        ("Status", body.get("status")),
        ("Disease", dg.get("disease")),
        ("Disease (AR)", dg.get("disease_ar")),
        ("Crop", dg.get("crop")),
        ("Severity", dg.get("severity")),
        ("Confidence", dg.get("confidence")),
        ("Advisory", ad.get("title")),
        ("Advisory source", (ad.get("meta") or {}).get("source")),
        ("Total time", f"{(body.get('timing') or {}).get('total_ms')} ms"),
    ]
    for k, v in rows:
        print(f"    {k:17} {v}")

    print("\n  --- stage trace ---")
    for s in body.get("stages", []):
        print(f"    {s.get('name'):12} {s.get('status'):6} {s.get('duration_ms')} ms")

    # Assertions mirroring what the UI depends on.
    problems = []
    if dg.get("disease") is None:
        problems.append("diagnosis.disease missing")
    if not isinstance(dg.get("disease_ar"), str):
        problems.append("diagnosis.disease_ar not a string")
    if "meta" not in ad or "title" not in ad:
        problems.append("advisory missing meta/title")
    if len(body.get("stages", [])) != 2:
        problems.append("expected 2 stages")
    if "AQ.Ab8" in r.text:
        problems.append("API KEY LEAKED IN RESPONSE")

    print("\n" + "=" * 52)
    if problems:
        print("FAILED: " + "; ".join(problems))
        return 1
    print("END-TO-END OK: UI contract satisfied, no key leaked.")
    print("=" * 52)
    return 0


if __name__ == "__main__":
    sys.exit(main())
