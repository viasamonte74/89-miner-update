"""Download sealed HF tick windows from the public index (resumable, polite).

Primary training data = the same anchored series validators grade against.
Stores raw `{window_ms}/ticks.jsonl` (+ optional ticks.json / anchor.json) under
RAW_WINDOWS, then optionally converts new windows into per-pair parquet.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from . import paths
from .paths import ensure_dirs, HF_PUBLIC_BASE, RAW_WINDOWS, USER_AGENT, ARCHIVE_STATE


def _req(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_index(base: str = HF_PUBLIC_BASE) -> dict:
    raw = _req(base.rstrip("/") + "/index.json")
    return json.loads(raw.decode("utf-8"))


def _window_dir(w: int) -> str:
    return os.path.join(RAW_WINDOWS, str(w))


def window_complete(w: int) -> bool:
    """A window is done when ticks.jsonl exists and is non-empty."""
    p = os.path.join(_window_dir(w), "ticks.jsonl")
    try:
        return os.path.getsize(p) > 0
    except OSError:
        return False


def download_window(w: int, base: str = HF_PUBLIC_BASE, extras: bool = True) -> str:
    """Fetch one window. Returns 'ok' | 'skip' | 'fail'."""
    if window_complete(w):
        return "skip"
    d = _window_dir(w)
    os.makedirs(d, exist_ok=True)
    url = f"{base.rstrip('/')}/{w}/ticks.jsonl"
    try:
        body = _req(url)
        if not body.strip():
            return "fail"
        tmp = os.path.join(d, "ticks.jsonl.tmp")
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, os.path.join(d, "ticks.jsonl"))
        if extras:
            for name in ("ticks.json", "anchor.json"):
                try:
                    extra = _req(f"{base.rstrip('/')}/{w}/{name}", timeout=30.0)
                    with open(os.path.join(d, name), "wb") as fh:
                        fh.write(extra)
                except Exception:
                    pass
        return "ok"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return "fail"


def _load_state() -> dict:
    try:
        with open(ARCHIVE_STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"ok": 0, "skip": 0, "fail": 0, "last_w": None, "failures": []}


def _save_state(state: dict) -> None:
    ensure_dirs()
    tmp = ARCHIVE_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, ARCHIVE_STATE)


def archive(
    workers: int = 2,
    limit: int | None = None,
    newest_first: bool = True,
    base: str = HF_PUBLIC_BASE,
    sleep_s: float = 0.05,
    convert: bool = False,
) -> dict:
    ensure_dirs()
    idx = fetch_index(base)
    windows: list[int] = sorted(int(w) for w in idx.get("windows", []))
    if newest_first:
        windows = list(reversed(windows))
    if limit is not None:
        windows = windows[:limit]

    state = _load_state()
    state["retain_days"] = idx.get("retain_days")
    state["served"] = idx.get("served")
    state["n_index"] = len(windows)
    state["started_at"] = state.get("started_at") or time.time()
    state["failures"] = []

    print(
        f"archiver: {len(windows)} windows to consider "
        f"(retain_days={idx.get('retain_days')}, workers={workers})",
        flush=True,
    )

    def _one(w: int) -> tuple[int, str]:
        if sleep_s > 0:
            time.sleep(sleep_s)
        return w, download_window(w, base=base)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_one, w): w for w in windows}
        for fut in as_completed(futs):
            w, status = fut.result()
            state[status] = state.get(status, 0) + 1
            state["last_w"] = w
            if status == "fail":
                state["failures"].append(w)
                if len(state["failures"]) > 200:
                    state["failures"] = state["failures"][-200:]
            done += 1
            if done % 50 == 0 or status == "ok":
                _save_state(state)
                print(
                    f"  [{done}/{len(windows)}] w={w} {status} "
                    f"(ok={state.get('ok',0)} skip={state.get('skip',0)} "
                    f"fail={state.get('fail',0)})",
                    flush=True,
                )

    state["finished_pass_at"] = time.time()
    _save_state(state)
    print(f"archiver pass done: {state}", flush=True)

    if convert:
        from .ingest import convert_new_windows

        convert_new_windows(windows_filter=None)
    return state


def archive_loop(
    *,
    interval_s: float = 180.0,
    workers: int = 2,
    base: str = HF_PUBLIC_BASE,
    sleep_s: float = 0.02,
) -> None:
    """Poll the public index forever; download only missing windows (grow beyond 45d retain)."""
    print(
        f"archiver loop: interval={interval_s}s workers={workers} base={base}",
        flush=True,
    )
    while True:
        t0 = time.time()
        try:
            state = archive(
                workers=workers,
                limit=None,
                newest_first=True,
                base=base,
                sleep_s=sleep_s,
                convert=False,
            )
            print(
                f"loop pass: ok={state.get('ok')} skip={state.get('skip')} "
                f"fail={state.get('fail')} ({time.time() - t0:.1f}s)",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            print(f"loop error: {exc!r}; retrying after interval", flush=True)
        elapsed = time.time() - t0
        wait = max(5.0, interval_s - elapsed)
        time.sleep(wait)


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Archive SN89 anchored tick windows")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--limit", type=int, default=None, help="Max windows this run")
    p.add_argument("--oldest-first", action="store_true")
    p.add_argument(
        "--convert",
        action="store_true",
        help="After download, rebuild crypto parquet (slow; usually run separately)",
    )
    p.add_argument("--sleep", type=float, default=0.05)
    p.add_argument("--base", default=HF_PUBLIC_BASE)
    p.add_argument(
        "--loop",
        action="store_true",
        help="Keep polling forever (incremental growth; skips existing)",
    )
    p.add_argument(
        "--interval-s",
        type=float,
        default=180.0,
        help="Seconds between loop passes (default 180 = one HF window)",
    )
    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        if args.loop:
            archive_loop(
                interval_s=args.interval_s,
                workers=args.workers,
                base=args.base,
                sleep_s=args.sleep,
            )
            return 0
        archive(
            workers=args.workers,
            limit=args.limit,
            newest_first=not args.oldest_first,
            base=args.base,
            sleep_s=args.sleep,
            convert=args.convert,
        )
    except KeyboardInterrupt:
        print("interrupted — state saved; re-run to resume", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
