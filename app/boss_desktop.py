"""Run with python -m app.boss_desktop. Manual login, foreground browser only."""

import argparse
import hashlib
import json
import time
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import models as m
from app.api.resources import get_row, policy_read, profile_read
from app.core.config import Settings
from app.database import make_engine
from app.desktop import Desktop, pixel
from app.vision import Vision, score


def identity(card):
    return hashlib.sha256(
        json.dumps(
            [card.title.strip(), card.company.strip(), card.city.strip(), card.salary.strip()],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(desktop, vision, profile, policy, root, args, before_communicate=None):
    """Bounded loop. One fresh card click at a time; no stale per-page coordinate batch."""
    state_path = root / "history.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    seen = set()
    processed = communications = 0
    skills = profile["skills"]
    # Never send contact details, resume bytes, or arbitrary facts to the vision service.
    evidence = {
        key: profile[key]
        for key in (
            "skills",
            "education",
            "experience_years",
            "preferred_cities",
            "remote_preference",
        )
    }
    for page in range(args.pages):
        while processed < args.jobs:
            image, size = desktop.capture()
            screen = vision.inspect(image, evidence, "list")
            if screen.blocked or screen.uncertain:
                raise RuntimeError("页面验证、弹窗或识别不确定，已暂停")
            card = next((item for item in screen.cards if identity(item) not in seen), None)
            if card is None:
                break
            key = identity(card)
            seen.add(key)
            if card.company in policy.get("blocked_companies", []):
                continue
            history_key = str(profile["id"]) + ":" + key
            previous = state.get(history_key)
            if previous and (
                previous["state"].startswith("communication_")
                or (
                    not args.communicate
                    and previous.get("profile_version") == profile["version_id"]
                    and previous.get("policy_version") == policy["version_id"]
                )
            ):
                continue
            desktop.click(*pixel(card.center, size), expected=image)
            time.sleep(args.settle)
            # New selections normally reset the pane; explicitly return it upward as well.
            for _ in range(args.detail_scrolls):
                desktop.scroll(*pixel(args.right_scroll, size), ticks=10)
            time.sleep(args.settle)
            parts = []
            requirements = {}
            complete = False
            hard = "unknown"
            detail = None
            for part in range(args.detail_scrolls + 1):
                detail_image, size = desktop.capture()
                detail = vision.inspect(detail_image, evidence, "detail")
                if detail.blocked or detail.uncertain:
                    raise RuntimeError("详情识别不确定，停止操作")
                if part == 0 and (detail.selected is None or identity(detail.selected) != key):
                    raise RuntimeError("卡片与右侧职位身份不一致，停止操作")
                parts.append(detail.detail_text)
                for item in detail.requirements:
                    if item.quote not in detail.detail_text:
                        raise RuntimeError("详情引用不属于可见原文，停止操作")
                    previous = requirements.get(item.quote)
                    if previous is not None and previous != item:
                        raise RuntimeError("重叠截图的要求判断不一致，停止操作")
                    requirements[item.quote] = item
                if detail.hard_conditions == "fail":
                    hard = "fail"
                elif detail.hard_conditions == "pass" and hard != "fail":
                    hard = "pass"
                if detail.detail_complete:
                    complete = True
                    break
                if part < args.detail_scrolls:
                    desktop.scroll(*pixel(args.right_scroll, size), ticks=-args.ticks)
                    time.sleep(args.settle)
            combined = "\n".join(parts)
            if complete:
                detail = vision.inspect(
                    detail_image,
                    {
                        **evidence,
                        "complete_detail_text": combined,
                    },
                    "evaluate_all_text",
                )
                if detail.blocked or detail.uncertain:
                    raise RuntimeError("完整详情评估不确定，停止")
                hard = detail.hard_conditions
            detail.detail_text = combined
            if not complete:
                detail.requirements = list(requirements.values())
            detail.detail_complete = complete
            detail.hard_conditions = hard
            value = score(detail, skills)
            processed += 1
            record = {
                "card": card.model_dump(),
                "score": value,
                "hard_conditions": hard,
                "profile_version": profile["version_id"],
                "policy_version": policy["version_id"],
                "state": "analyzed",
                "requirements": [item.model_dump() for item in detail.requirements],
            }
            passes = value is not None and (
                value >= float(policy["threshold"])
                if policy["comparison"] == "gte"
                else value > float(policy["threshold"])
            )
            if args.communicate and passes and hard == "pass":
                if before_communicate is None:
                    raise RuntimeError("缺少投递前版本校验，禁止沟通")
                before_communicate()
                day = datetime.now(ZoneInfo(policy["timezone"])).date().isoformat()
                used = sum(
                    1
                    for name, item in state.items()
                    if name.startswith(str(profile["id"]) + ":")
                    and item.get("communication_day") == day
                    and item["state"].startswith("communication_")
                )
                if used >= policy["daily_limit"]:
                    raise RuntimeError("本次沟通次数达到策略限额，停止")
                # Persist before irreversible input; never retry uncertain communication.
                record["state"] = "communication_pending"
                record["communication_day"] = day
                state[history_key] = record
                save_state(state_path, state)
                fresh_image, fresh_size = desktop.capture()
                fresh = vision.inspect(fresh_image, evidence, "confirm_communication_target")
                if (
                    fresh.blocked
                    or fresh.uncertain
                    or fresh.selected is None
                    or identity(fresh.selected) != key
                    or fresh.communicate is None
                ):
                    raise RuntimeError("沟通前无法确认职位和按钮，保留 pending，不自动重试")
                desktop.click(*pixel(fresh.communicate.center, fresh_size), expected=fresh_image)
                communications += 1
                time.sleep(args.settle)
                result_image, _ = desktop.capture()
                result = vision.inspect(result_image, evidence, "communication_result")
                record["state"] = (
                    "communication_confirmed"
                    if result.communication_confirmed
                    and not result.blocked
                    and not result.uncertain
                    else "communication_unknown"
                )
                state[history_key] = record
                save_state(state_path, state)
                # Chat navigation may replace the job list. Do not click through it blindly.
                if record["state"] != "communication_confirmed" or not result.cards:
                    raise RuntimeError("沟通结果已记录；请核查并回到职位列表后再启动")
                continue
            state[history_key] = record
            save_state(state_path, state)
            (root / f"detail-{uuid4().hex}.png").write_bytes(detail_image)
            # Return the detail pane to its top for the next card.
            for _ in range(args.detail_scrolls):
                desktop.scroll(*pixel(args.right_scroll, size), ticks=args.ticks)
            time.sleep(args.settle)
        if processed >= args.jobs:
            break
        if page + 1 < args.pages:
            desktop.scroll(*pixel(args.left_scroll, size), ticks=-args.ticks)
            time.sleep(args.settle)
    return processed


def main():
    parser = argparse.ArgumentParser(description="BOSS直聘截图/坐标执行器")
    parser.add_argument("action", choices=["screenshot", "click", "scroll", "run"])
    parser.add_argument("--title", default="BOSS直聘")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--ticks", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--profile-id", type=UUID)
    parser.add_argument("--policy-id", type=UUID)
    parser.add_argument("--pages", type=int, choices=range(1, 21), default=3)
    parser.add_argument("--jobs", type=int, choices=range(1, 201), default=20)
    parser.add_argument("--detail-scrolls", type=int, choices=range(1, 21), default=5)
    parser.add_argument("--settle", type=float, default=2)
    parser.add_argument("--left-scroll", nargs=2, type=int, default=[200, 500])
    parser.add_argument("--right-scroll", nargs=2, type=int, default=[750, 800])
    parser.add_argument("--communicate", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    root = settings.artifacts_dir / "boss-desktop"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "desktop.lock"
    try:
        lock = lock_path.open("xb")
    except FileExistsError:
        parser.exit(1, "已有执行器或遗留锁；确认旧进程已停止后再移除 desktop.lock\n")
    engine = None
    try:
        if not 1 <= args.settle <= 30:
            raise ValueError("settle 必须为 1..30 秒")
        desktop = Desktop(args.title)
        print("请在5秒内切换到已手动登录的 BOSS直聘职位列表；Ctrl+C停止")
        time.sleep(5)
        if args.action == "screenshot":
            image, _ = desktop.capture()
            (root / "screen.png").write_bytes(image)
            print("截图已保存到 artifacts/boss-desktop/screen.png")
        elif args.action in {"click", "scroll"}:
            if args.x is None or args.y is None:
                raise ValueError("需要 --x 和 --y 原始屏幕像素坐标")
            if args.action == "click":
                desktop.click(args.x, args.y)
            else:
                desktop.scroll(args.x, args.y, ticks=-args.ticks)
        else:
            if not args.profile_id or not args.policy_id or not settings.database_url:
                raise ValueError("run 需要档案ID、策略ID和数据库连接")
            vision = Vision(settings)
            engine = make_engine(settings.database_url.get_secret_value())
            with Session(engine) as session:
                profile = profile_read(
                    session, get_row(session, m.profiles, args.profile_id)
                ).model_dump(mode="json")
                policy = policy_read(
                    session, get_row(session, m.policies, args.policy_id)
                ).model_dump(mode="json")
            if "boss" not in policy["allowed_platforms"]:
                raise ValueError("策略未允许 BOSS直聘")
            if args.communicate and not policy["auto_apply"]:
                raise ValueError("需先显式开启策略 auto_apply 才可启用沟通")

            def check_versions():
                with Session(engine) as session:
                    current_profile = get_row(session, m.profiles, args.profile_id)
                    current_policy = get_row(session, m.policies, args.policy_id)
                    if (
                        str(current_profile["current_version_id"]) != profile["version_id"]
                        or str(current_policy["current_version_id"]) != policy["version_id"]
                    ):
                        raise RuntimeError("能力档案或策略已经变化，请重新启动")

            print(
                "本次分析数量:", run(desktop, vision, profile, policy, root, args, check_versions)
            )
    except KeyboardInterrupt:
        print("用户已停止")
    except Exception as exc:
        # Avoid leaking database/model credentials in provider or driver exceptions.
        print("执行器已停止:", type(exc).__name__)
        if isinstance(exc, (ValueError, RuntimeError)):
            print(str(exc))
        raise SystemExit(1) from None
    finally:
        if engine:
            engine.dispose()
        lock.close()
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
