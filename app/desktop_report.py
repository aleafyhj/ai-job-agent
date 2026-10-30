"""Traceable image observations; never expose image payloads or provider credentials."""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageDraw

from app.desktop import pixel


def observe(vision, image, evidence, phase, root):
    folder = (
        root
        / "observations"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    )
    folder.mkdir(parents=True)
    with Image.open(BytesIO(image)) as source:
        size = source.size
        canvas = source.convert("RGB")
    (folder / "screen.png").write_bytes(image)
    report = {
        "phase": phase,
        "created_at": datetime.now(UTC).isoformat(),
        "width": size[0],
        "height": size[1],
        "sha256": hashlib.sha256(image).hexdigest(),
        "status": "recognizing",
    }
    path = folder / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        screen = vision.inspect(image, evidence, phase)
        report.update(status="recognized", result=screen.model_dump(), cards=[])
        draw = ImageDraw.Draw(canvas)
        for index, card in enumerate(screen.cards, 1):
            x, y = pixel(card.center, size)
            report["cards"].append({"index": index, **card.model_dump(), "pixel": [x, y]})
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline="red", width=3)
            draw.line((x - 20, y, x + 20, y), fill="red", width=2)
            draw.line((x, y - 20, x, y + 20), fill="red", width=2)
            draw.text((x + 15, y + 15), f"#{index} ({x}, {y})", fill="red", stroke_width=1)
        canvas.save(folder / "targets.png")
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("本次识别报告:", path.resolve())
    return screen, size, folder


def verify_selection(expected, observed):
    """Identity checks ignore position, which can change after selecting a card."""
    if observed.blocked or observed.uncertain or observed.selected is None:
        raise RuntimeError("详情尚未就绪或无法确认身份，停止后续操作")

    mismatches = [
        field
        for field in ("title", "company", "salary")
        if normalized(getattr(expected, field)) != normalized(getattr(observed.selected, field))
    ]
    left = re.split(r"[-·•/]+", normalized(expected.city))
    right = re.split(r"[-·•/]+", normalized(observed.selected.city))
    if left != right and not (left[0] == right[0] and (len(left) == 1 or len(right) == 1)):
        mismatches.append("city")
    if mismatches:
        raise RuntimeError(
            "点击后职位身份不匹配（"
            + ",".join(mismatches)
            + "）：期望“"
            + expected.title
            + "”，实际“"
            + observed.selected.title
            + "”，停止后续操作"
        )


def normalized(value):
    return (
        "".join(unicodedata.normalize("NFKC", value).split())
        .casefold()
        .replace("–", "-")
        .replace("—", "-")
    )


def ground_card(vision, image, card, cards, folder):
    """Refine coarse full-screen coordinates to the text link using a local crop."""
    with Image.open(BytesIO(image)) as source:
        x, y = pixel(card.center, source.size)
        distances = [
            abs(pixel(other.center, source.size)[1] - y)
            for other in cards
            if other is not card and abs(pixel(other.center, source.size)[1] - y) > 20
        ]
        half_height = (
            max(45, round(min(distances) * 0.55)) if distances else round(source.height * 0.12)
        )
        half_width = round(source.width * 0.16)
        box = (
            max(0, x - half_width),
            max(0, y - half_height),
            min(source.width, x + half_width),
            min(source.height, y + half_height),
        )
        crop = source.crop(box).convert("RGB")
    stream = BytesIO()
    crop.save(stream, format="PNG")
    (folder / "title-crop.png").write_bytes(stream.getvalue())
    target = vision.ground_title(stream.getvalue(), card)
    if target.uncertain or normalized(target.title) != normalized(card.title):
        raise RuntimeError("无法精确定位卡片标题链接，未执行点击")
    local_x, local_y = pixel(target.center, crop.size)
    point = (box[0] + local_x, box[1] + local_y)
    (folder / "title-target.json").write_text(
        json.dumps(
            {
                "crop_box": box,
                "crop_size": crop.size,
                "target": target.model_dump(),
                "pixel": point,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    draw = ImageDraw.Draw(crop)
    draw.ellipse((local_x - 8, local_y - 8, local_x + 8, local_y + 8), outline="red", width=2)
    crop.save(folder / "title-target.png")
    return point
