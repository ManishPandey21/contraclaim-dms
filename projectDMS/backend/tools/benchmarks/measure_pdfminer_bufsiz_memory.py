"""Disposable benchmark: memory cost of OCRmyPDF's global pdfminer BUFSIZ patch.

NOT runtime code. Nothing in ``rbac_backend`` imports this file.

OCRmyPDF 16.10.4 ``ocrmypdf/pdfinfo/layout.py`` sets
``pdfminer.psparser.PSBaseParser.BUFSIZ = 256 * 1024 * 1024`` for the life of
the process. This tool measures what that costs in resident memory (RSS) and in
cgroup memory, for single, sequential, threaded and multi-process extraction.

The patch is process-global, so every arm runs in a FRESH interpreter, and the
host orchestrator (``matrix``) runs every measurement in a FRESH container.

Subcommands
-----------
corpus      build the deterministic synthetic corpus (repo fixture helpers only)
single      one extraction in this process
retention   N sequential extractions in one process, gc + sleep between them
threads     N barrier-synchronised extractions on a ThreadPoolExecutor
processes   N barrier-synchronised extractions, one spawned interpreter each
supervise   PID-1 wrapper: run a child command, then read the cgroup totals
            (memory.peak / memory.events) after it exits - survives a child
            OOM kill, so an OOM case still produces a record
matrix      host-side orchestrator: one ``docker run`` per measurement

Arms (``--arm``)
----------------
control       import pdfplumber only; asserts BUFSIZ != 256 MiB
patched       import ocrmypdf first; asserts BUFSIZ == 256 MiB
patched_reset import ocrmypdf, then restore pdfminer's own BUFSIZ (isolates
              the buffer from the rest of OCRmyPDF's import footprint)
bufsiz_only   no ocrmypdf; set BUFSIZ = 256 MiB by hand (isolates the buffer
              from OCRmyPDF's other patch, the PDFSimpleFont one)

Primary evidence is RSS (psutil + /proc/self/status, sampled every 25 ms) and
cgroup v2 memory.current/memory.peak. tracemalloc is secondary and opt-in
(``--tracemalloc``) because it inflates both time and RSS.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

PATCHED_BUFSIZ = 256 * 1024 * 1024
MIB = 1024 * 1024
CGROUP = Path("/sys/fs/cgroup")
SAMPLE_INTERVAL_S = 0.025

# --------------------------------------------------------------------------
# Measurement primitives
# --------------------------------------------------------------------------


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_kv(path: Path) -> dict[str, int] | None:
    try:
        out: dict[str, int] = {}
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            out[key] = int(value)
        return out
    except (OSError, ValueError):
        return None


def cgroup_snapshot() -> dict[str, Any]:
    stat = _read_kv(CGROUP / "memory.stat") or {}
    return {
        "memory_current": _read_int(CGROUP / "memory.current"),
        "memory_peak": _read_int(CGROUP / "memory.peak"),
        "memory_max": (CGROUP / "memory.max").read_text().strip()
        if (CGROUP / "memory.max").exists()
        else None,
        "memory_events": _read_kv(CGROUP / "memory.events"),
        "anon": stat.get("anon"),
        "file": stat.get("file"),
    }


def _proc_status_kb(field: str) -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def smaps_rollup() -> dict[str, int] | None:
    try:
        out: dict[str, int] = {}
        for line in Path("/proc/self/smaps_rollup").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                out[parts[0].rstrip(":")] = int(parts[1]) * 1024
        return out
    except OSError:
        return None


def mem_now() -> dict[str, Any]:
    import psutil

    info = psutil.Process(os.getpid()).memory_info()
    snap: dict[str, Any] = {
        "t": time.time(),
        "rss": info.rss,
        "vms": info.vms,
        "proc_vmrss": (_proc_status_kb("VmRSS") or 0) * 1024 or None,
        "proc_vmhwm": (_proc_status_kb("VmHWM") or 0) * 1024 or None,
        "cgroup_current": _read_int(CGROUP / "memory.current"),
    }
    try:
        import resource

        ru = resource.getrusage(resource.RUSAGE_SELF)
        snap["minflt"] = ru.ru_minflt
        snap["majflt"] = ru.ru_majflt
        snap["ru_maxrss_kb"] = ru.ru_maxrss
    except ImportError:  # Windows smoke runs only
        pass
    return snap


class Sampler:
    """High-frequency in-process sampler (psutil RSS/VMS + cgroup current)."""

    def __init__(self, interval: float = SAMPLE_INTERVAL_S, extra_pids: Callable[[], list[int]] | None = None):
        self.interval = interval
        self.extra_pids = extra_pids
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.samples = 0
        self.peak_rss = 0
        self.peak_vms = 0
        self.peak_cgroup = 0
        self.peak_tree_rss = 0
        self.timeline: list[tuple[float, int, int | None]] = []

    def _run(self) -> None:
        import psutil

        proc = psutil.Process(os.getpid())
        t0 = time.time()
        while not self._stop.is_set():
            try:
                info = proc.memory_info()
            except Exception:
                break
            cg = _read_int(CGROUP / "memory.current")
            self.samples += 1
            self.peak_rss = max(self.peak_rss, info.rss)
            self.peak_vms = max(self.peak_vms, info.vms)
            if cg is not None:
                self.peak_cgroup = max(self.peak_cgroup, cg)
            if self.extra_pids is not None:
                tree = info.rss
                for pid in self.extra_pids():
                    try:
                        tree += psutil.Process(pid).memory_info().rss
                    except Exception:
                        pass
                self.peak_tree_rss = max(self.peak_tree_rss, tree)
            if self.samples % 4 == 1:  # keep a ~100 ms timeline
                self.timeline.append((round(time.time() - t0, 3), info.rss, cg))
            time.sleep(self.interval)

    def __enter__(self) -> "Sampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict[str, Any]:
        tl = self.timeline
        if len(tl) > 60:  # bounded evidence size
            step = len(tl) / 60
            tl = [tl[int(i * step)] for i in range(60)]
        return {
            "samples": self.samples,
            "interval_ms": self.interval * 1000,
            "peak_rss": self.peak_rss,
            "peak_vms": self.peak_vms,
            "peak_cgroup_current": self.peak_cgroup or None,
            "peak_tree_rss": self.peak_tree_rss or None,
            "timeline_s_rss_cgroup": tl,
        }


# --------------------------------------------------------------------------
# Arms and extraction
# --------------------------------------------------------------------------


def setup_arm(arm: str) -> dict[str, Any]:
    """Apply the arm in THIS (fresh) process and prove the BUFSIZ it produced."""
    original = None
    if arm == "control":
        import pdfplumber  # noqa: F401
    elif arm == "patched":
        import ocrmypdf  # noqa: F401  - the import under test
        import pdfplumber  # noqa: F401
    elif arm == "patched_reset":
        from pdfminer.psparser import PSBaseParser as _P

        original = _P.BUFSIZ
        import ocrmypdf  # noqa: F401
        import pdfplumber  # noqa: F401

        _P.BUFSIZ = original
    elif arm == "bufsiz_only":
        import pdfplumber  # noqa: F401
        from pdfminer.psparser import PSBaseParser as _P

        _P.BUFSIZ = PATCHED_BUFSIZ
    else:
        raise SystemExit(f"unknown arm {arm}")

    from pdfminer.psparser import PSBaseParser

    bufsiz = PSBaseParser.BUFSIZ
    expect_patched = arm in ("patched", "bufsiz_only")
    if expect_patched and bufsiz != PATCHED_BUFSIZ:
        raise SystemExit(f"STOP: arm {arm} expected BUFSIZ 256 MiB, got {bufsiz}")
    if not expect_patched and bufsiz == PATCHED_BUFSIZ:
        raise SystemExit(
            f"STOP: arm {arm} already has BUFSIZ 256 MiB - something imported ocrmypdf "
            f"before the measurement (sys.modules has ocrmypdf: {'ocrmypdf' in sys.modules})"
        )
    return {
        "arm": arm,
        "bufsiz": bufsiz,
        "ocrmypdf_in_sys_modules": "ocrmypdf" in sys.modules,
        "original_bufsiz_restored": original,
    }


def extract(path: Path) -> dict[str, Any]:
    """Production-shaped extraction (services/extraction/engine.py:268-279):
    one pdfplumber.open, extract_text + extract_tables per page, pages kept."""
    import pdfplumber

    digest = hashlib.sha256()
    chars = pages = tables = 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            tables += len(page.extract_tables() or [])
            chars += len(text)
            pages += 1
            digest.update(text.encode("utf-8", "surrogatepass"))
    return {"pages": pages, "chars": chars, "tables": tables, "text_sha256": digest.hexdigest()}


def _emit(record: dict[str, Any]) -> None:
    sys.stdout.write("BENCH_JSON " + json.dumps(record, default=str) + "\n")
    sys.stdout.flush()


def _versions() -> dict[str, Any]:
    from importlib import metadata

    out: dict[str, Any] = {"python": sys.version.split()[0], "platform": platform.platform()}
    for dist in ("ocrmypdf", "pdfminer.six", "pdfplumber", "pikepdf", "psutil"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None
    return out


def _tm_start(enabled: bool) -> None:
    if enabled:
        import tracemalloc

        tracemalloc.start()


def _tm_peak(enabled: bool) -> int | None:
    if not enabled:
        return None
    import tracemalloc

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


# --------------------------------------------------------------------------
# Scenarios (run INSIDE the benchmark container)
# --------------------------------------------------------------------------


def cmd_single(args: argparse.Namespace) -> None:
    startup = mem_now()
    arm = setup_arm(args.arm)
    gc.collect()
    pre = mem_now()
    cg_pre = cgroup_snapshot()
    _tm_start(args.tracemalloc)
    t0 = time.perf_counter()
    with Sampler() as sampler:
        result = extract(Path(args.pdf))
    duration = time.perf_counter() - t0
    tm_peak = _tm_peak(args.tracemalloc)
    post = mem_now()
    gc.collect()
    post_gc = mem_now()
    _emit(
        {
            "scenario": "single",
            **arm,
            "pdf": args.pdf,
            "pdf_bytes": os.path.getsize(args.pdf),
            "duration_s": round(duration, 4),
            "startup": startup,
            "pre": pre,
            "sampler": sampler.summary(),
            "post": post,
            "post_gc": post_gc,
            "smaps_rollup_post_gc": smaps_rollup(),
            "cgroup_pre": cg_pre,
            "cgroup_post": cgroup_snapshot(),
            "tracemalloc_peak": tm_peak,
            "result": result,
            "versions": _versions(),
        }
    )


def cmd_retention(args: argparse.Namespace) -> None:
    startup = mem_now()
    arm = setup_arm(args.arm)
    gc.collect()
    pre = mem_now()
    steps = []
    for index, pdf in enumerate(args.pdfs, start=1):
        t0 = time.perf_counter()
        with Sampler() as sampler:
            result = extract(Path(pdf))
        duration = time.perf_counter() - t0
        post = mem_now()
        gc.collect()
        time.sleep(args.sleep)
        after = mem_now()
        steps.append(
            {
                "doc": index,
                "pdf": pdf,
                "duration_s": round(duration, 4),
                "peak_rss": sampler.peak_rss,
                "peak_vms": sampler.peak_vms,
                "peak_cgroup_current": sampler.peak_cgroup or None,
                "post_rss": post["rss"],
                "post_gc_sleep_rss": after["rss"],
                "post_gc_sleep_vms": after["vms"],
                "post_gc_sleep_cgroup": after["cgroup_current"],
                "minflt": after.get("minflt"),
                "majflt": after.get("majflt"),
                "chars": result["chars"],
                "smaps_rollup": smaps_rollup(),
            }
        )
    _emit(
        {
            "scenario": "retention",
            **arm,
            "startup": startup,
            "pre": pre,
            "steps": steps,
            "cgroup_post": cgroup_snapshot(),
            "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX"),
            "versions": _versions(),
        }
    )


def cmd_threads(args: argparse.Namespace) -> None:
    from concurrent.futures import ThreadPoolExecutor

    n = args.n
    pdfs = [Path(p) for p in args.pdfs][:n]
    if len(pdfs) < n:
        raise SystemExit(f"need {n} distinct PDF copies, got {len(pdfs)}")
    startup = mem_now()
    arm = setup_arm(args.arm)
    gc.collect()
    pre = mem_now()
    barrier = threading.Barrier(n)
    t_base = time.perf_counter()

    def task(i: int) -> dict[str, Any]:
        barrier.wait()
        start = time.perf_counter() - t_base
        try:
            res = extract(pdfs[i])
            ok, err = True, None
        except Exception as exc:  # recorded, never swallowed silently
            res, ok, err = {}, False, repr(exc)[:300]
        return {
            "task": i,
            "start_s": round(start, 4),
            "end_s": round(time.perf_counter() - t_base, 4),
            "ok": ok,
            "error": err,
            "chars": res.get("chars"),
            "text_sha256": res.get("text_sha256"),
        }

    with Sampler() as sampler:
        with ThreadPoolExecutor(max_workers=n) as pool:
            tasks = list(pool.map(task, range(n)))
    duration = time.perf_counter() - t_base
    post = mem_now()
    gc.collect()
    post_gc = mem_now()
    _emit(
        {
            "scenario": "threads",
            **arm,
            "n": n,
            "duration_s": round(duration, 4),
            "startup": startup,
            "pre": pre,
            "sampler": sampler.summary(),
            "post": post,
            "post_gc": post_gc,
            "tasks": tasks,
            "start_spread_s": round(max(t["start_s"] for t in tasks) - min(t["start_s"] for t in tasks), 4),
            "cgroup_post": cgroup_snapshot(),
            "versions": _versions(),
        }
    )


def _proc_child(arm: str, pdf: str, barrier: Any, queue: Any, index: int) -> None:
    pre_arm = mem_now()
    info = setup_arm(arm)
    gc.collect()
    pre = mem_now()
    barrier.wait()
    start = time.time()
    with Sampler() as sampler:
        res = extract(Path(pdf))
    queue.put(
        {
            "proc": index,
            "pid": os.getpid(),
            "bufsiz": info["bufsiz"],
            "startup_rss": pre_arm["rss"],
            "pre_rss": pre["rss"],
            "peak_rss": sampler.peak_rss,
            "peak_vms": sampler.peak_vms,
            "start_ts": start,
            "end_ts": time.time(),
            "chars": res["chars"],
            "text_sha256": res["text_sha256"],
        }
    )


def cmd_processes(args: argparse.Namespace) -> None:
    import multiprocessing as mp

    ctx = mp.get_context("spawn")  # fresh interpreter per process
    n = args.n
    barrier = ctx.Barrier(n)
    queue = ctx.Queue()
    cg_pre = cgroup_snapshot()
    procs = [
        ctx.Process(target=_proc_child, args=(args.arm, args.pdfs[i], barrier, queue, i)) for i in range(n)
    ]
    for proc in procs:
        proc.start()
    t0 = time.time()
    import queue as queue_mod

    results: list[dict[str, Any]] = []
    with Sampler(extra_pids=lambda: [p.pid for p in procs if p.pid and p.is_alive()]) as sampler:
        deadline = time.time() + args.timeout
        # Poll, never block: an OOM-killed child never reports, and the
        # barrier would strand its siblings; both must surface as failures.
        while len(results) < n and time.time() < deadline:
            try:
                results.append(queue.get(timeout=0.5))
            except queue_mod.Empty:
                if any(p.exitcode not in (None, 0) for p in procs):
                    time.sleep(1)
                    break
        for proc in procs:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join()
    ok = len(results) == n and all(p.exitcode == 0 for p in procs)
    starts = [r["start_ts"] for r in results] or [0.0]
    _emit(
        {
            "scenario": "processes",
            "arm": args.arm,
            "n": n,
            "ok": ok,
            "duration_s": round(time.time() - t0, 4),
            "children": sorted(results, key=lambda r: r["proc"]),
            "exitcodes": [p.exitcode for p in procs],
            "start_spread_s": round(max(starts) - min(starts), 4),
            "sampler_parent": sampler.summary(),
            "cgroup_pre": cg_pre,
            "cgroup_post": cgroup_snapshot(),
            "versions": _versions(),
        }
    )
    if not ok:
        sys.exit(4)


def cmd_supervise(args: argparse.Namespace) -> None:
    """PID 1 in the container. Stdlib only, so its own footprint stays tiny."""
    cmd = args.child
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    records = [
        json.loads(line[len("BENCH_JSON ") :]) for line in proc.stdout.splitlines() if line.startswith("BENCH_JSON ")
    ]
    out = {
        "supervisor": {
            "child_cmd": cmd,
            "child_returncode": proc.returncode,
            "child_killed_by_signal": -proc.returncode if proc.returncode < 0 else None,
            "wall_s": round(time.time() - t0, 3),
            "stderr_tail": proc.stderr[-1500:],
            "cgroup_after_child": cgroup_snapshot(),
        },
        "records": records,
    }
    sys.stdout.write("SUPERVISED_JSON " + json.dumps(out, default=str) + "\n")
    sys.stdout.flush()
    sys.exit(0 if proc.returncode == 0 else 3)


# --------------------------------------------------------------------------
# Corpus (repo fixture helpers only - no customer documents)
# --------------------------------------------------------------------------


def _page_lines(page: int, total: int, repeat: int) -> list[str]:
    """Deterministic contract-style page: narrative, identifiers, quantities."""
    lines = [
        f"CONTRACT CC/KNP/2026/{page:05d}  -  Page {page} of {total}",
        f"Letter Ref: GSJV/UPMRC/{1000 + page:05d}/2026   Date: 2026-{(page % 12) + 1:02d}-{(page % 28) + 1:02d}",
    ]
    narrative = [
        "The Contractor hereby notifies the Engineer under Sub-Clause 20.1 of a claim",
        "for additional time and cost arising from the delayed handover of the site",
        "and the resulting idling of plant and labour, as recorded in the joint records.",
        "The particulars below are submitted without prejudice to further entitlement.",
    ]
    for block in range(repeat):
        lines.extend(narrative)
        for row in range(4):
            qty = (page * 7 + block * 3 + row) % 97 + 1
            rate = 1000 + ((page * 13 + row * 17) % 9000)
            lines.append(
                f"{block * 4 + row + 1:>4}  Item BOQ-{page:04d}-{row:02d}   Qty {qty:>4}  Rate {rate:>6}  Amount {qty * rate:>9}"
            )
    return lines


def build_corpus_pdf(
    path: Path, *, pages: int, repeat: int = 1, compress: bool = True, image_side: int = 0
) -> Path:
    import pikepdf

    # Repo fixture helpers: the same content-stream writer and deterministic
    # save as the test fixtures. The font adds /Encoding /WinAnsiEncoding, the
    # standards-correct shape adopted on fix/ocrmypdf-pdfminer-import-order;
    # without it OCRmyPDF's OTHER patch (PDFSimpleFont) turns every glyph into
    # (cid:N) and the control/patched text would differ for a reason unrelated
    # to BUFSIZ.
    from rbac_backend.tests.fixtures.pdf_builders import A4_PORTRAIT, _text_stream

    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    for number in range(1, pages + 1):
        lines = _page_lines(number, pages, repeat)
        # _text_stream starts at y=770 with 14pt leading; heavy pages get a
        # taller MediaBox (still far under the 14400-unit limit) so every line
        # stays on the page at 12pt and extraction sees real text rows.
        height = max(A4_PORTRAIT[3], 72 + 14 * len(lines))
        stream = _text_stream(lines).replace(b"72 770 Td", f"72 {height - 72} Td".encode())
        resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        if image_side:
            # Scanned-hybrid shape: an uncompressed greyscale page image under
            # the text layer. Deterministic bytes; this is what makes real
            # uploads large, and the parser must read past it in the file.
            row = bytes((x * 7 + number) & 0xFF for x in range(image_side))
            image = pdf.make_stream(
                row * image_side,
                Type=pikepdf.Name.XObject,
                Subtype=pikepdf.Name.Image,
                Width=image_side,
                Height=image_side,
                ColorSpace=pikepdf.Name.DeviceGray,
                BitsPerComponent=8,
            )
            resources.XObject = pikepdf.Dictionary(Im1=image)
            stream = f"q {A4_PORTRAIT[2]} 0 0 {height} 0 0 cm /Im1 Do Q\n".encode() + stream
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, A4_PORTRAIT[2], height],
            Resources=resources,
            Contents=pdf.make_stream(stream),
        )
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    pdf.save(
        str(path),
        deterministic_id=True,
        compress_streams=compress,
    )
    pdf.close()
    return path


CORPUS_SPEC = [
    # name, pages, repeat, compress, image_side
    ("p10", 10, 1, True, 0),
    ("p50", 50, 1, True, 0),
    ("p100", 100, 1, True, 0),
    ("p200", 200, 1, True, 0),
    ("p500", 500, 1, True, 0),
    ("p200_x10", 200, 10, True, 0),
    ("p200_x10_raw", 200, 10, False, 0),
    ("p200_x50_raw", 200, 50, False, 0),
    # ~60 MB: 200 x 548x548 raw greyscale images (under the 100 MB upload cap)
    ("p200_img_raw", 200, 1, False, 548),
]


def cmd_corpus(args: argparse.Namespace) -> None:
    out = Path(args.out)
    manifest = []
    for name, pages, repeat, compress, image_side in CORPUS_SPEC:
        path = build_corpus_pdf(
            out / f"{name}.pdf", pages=pages, repeat=repeat, compress=compress, image_side=image_side
        )
        manifest.append(
            {
                "name": name,
                "pages": pages,
                "repeat": repeat,
                "compressed_streams": compress,
                "image_side": image_side,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    # separate copies so every concurrent extraction has its own file + parser
    for i in range(8):
        shutil.copyfile(out / "p200.pdf", out / f"p200_copy{i}.pdf")
    for i in range(4):  # 60 MB each: 4 copies bound the corpus to ~0.6 GB on disk
        shutil.copyfile(out / "p200_img_raw.pdf", out / f"p200_img_raw_copy{i}.pdf")
    if args.extract:
        setup_arm("control")
        for entry in manifest:
            entry.update(extract(out / f"{entry['name']}.pdf"))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


# --------------------------------------------------------------------------
# Host orchestrator
# --------------------------------------------------------------------------


def host_available_mib() -> int | None:
    """Available physical memory on the host running Docker Desktop."""
    if sys.platform == "win32":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullAvailPhys // MIB)
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


def vm_available_mib(image: str) -> int | None:
    """MemAvailable inside the Docker VM (what containers actually draw on)."""
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network=none", "--entrypoint", "sh", image, "-c", "grep MemAvailable /proc/meminfo"],
        capture_output=True,
        text=True,
    )
    try:
        return int(proc.stdout.split()[1]) // 1024
    except (IndexError, ValueError):
        return None


class DockerStatsPoller:
    """Independent outside-the-container mechanism: `docker stats --no-stream`."""

    def __init__(self, name: str):
        self.name = name
        self.readings: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            proc = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", self.name],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                self.readings.append(proc.stdout.strip())

    def __enter__(self) -> "DockerStatsPoller":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    @staticmethod
    def _to_mib(text: str) -> float | None:
        used = text.split("/")[0].strip()
        for unit, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024), ("B", 1 / MIB)):
            if used.endswith(unit):
                try:
                    return float(used[: -len(unit)]) * factor
                except ValueError:
                    return None
        return None

    def peak_mib(self) -> float | None:
        values = [v for v in (self._to_mib(r) for r in self.readings) if v is not None]
        return round(max(values), 1) if values else None


def run_case(
    image: str,
    corpus_host: str,
    child_args: list[str],
    *,
    memory: str | None = None,
    env: dict[str, str] | None = None,
    label: str,
) -> dict[str, Any]:
    name = f"bufsiz-bench-{os.getpid()}-{int(time.time() * 1000)}"
    cmd = ["docker", "run", "--name", name, "--network=none", "-v", f"{corpus_host}:/corpus:ro"]
    if memory:
        cmd += [f"--memory={memory}", f"--memory-swap={memory}"]  # no swap: OOM, not thrash
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd += [
        image,
        "python",
        "/bench/measure_pdfminer_bufsiz_memory.py",
        "supervise",
        "--",
        "python",
        "/bench/measure_pdfminer_bufsiz_memory.py",
        *child_args,
    ]
    t0 = time.time()
    with DockerStatsPoller(name) as poller:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", name], capture_output=True, text=True
    )
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    try:
        state = json.loads(inspect.stdout)
    except json.JSONDecodeError:
        state = {}
    supervised = None
    for line in proc.stdout.splitlines():
        if line.startswith("SUPERVISED_JSON "):
            supervised = json.loads(line[len("SUPERVISED_JSON ") :])
    return {
        "label": label,
        "docker_cmd": cmd,
        "memory_limit": memory,
        "env": env or {},
        "container_exit_code": state.get("ExitCode"),
        "container_oom_killed": state.get("OOMKilled"),
        "docker_stats_peak_mib": poller.peak_mib(),
        "docker_stats_readings": len(poller.readings),
        "wall_s": round(time.time() - t0, 2),
        "supervised": supervised,
        "stderr_tail": proc.stderr[-800:],
    }


def cmd_matrix(args: argparse.Namespace) -> None:
    out_path = Path(args.out)
    results: dict[str, Any] = json.loads(out_path.read_text()) if out_path.exists() and args.resume else {}
    results.setdefault("cases", [])
    done = {c["label"] for c in results["cases"]}
    budget_floor = args.min_available_mib

    def save() -> None:
        out_path.write_text(json.dumps(results, indent=1, default=str))

    def guarded(label: str, projected_mib: float, child: list[str], **kw: Any) -> None:
        if label in done:
            return
        avail = host_available_mib()
        vm_avail = vm_available_mib(args.image) if args.check_vm else None
        limit = 0.7 * min(v for v in (avail, vm_avail) if v is not None) if (avail or vm_avail) else None
        entry: dict[str, Any] = {
            "label": label,
            "projected_mib": projected_mib,
            "host_available_mib_before": avail,
            "vm_available_mib_before": vm_avail,
        }
        if (limit is not None and projected_mib > limit) or (avail is not None and avail < budget_floor):
            entry["result"] = "NOT RUN - LOCAL HOST SAFETY LIMIT"
            print(f"[skip] {label}: projected {projected_mib} MiB > 70% of available ({avail}/{vm_avail})", flush=True)
        else:
            print(f"[run ] {label} (host avail {avail} MiB, vm avail {vm_avail} MiB)", flush=True)
            entry.update(run_case(args.image, args.corpus, child, label=label, **kw))
        results["cases"].append(entry)
        save()

    groups = set(args.groups.split(","))
    reps = range(1, args.reps + 1)
    arms = ["control", "patched"]

    if "sequential" in groups:
        for pages in args.pages.split(","):
            for arm in arms:
                for r in reps:
                    guarded(
                        f"seq/p{pages}/{arm}/r{r}",
                        450,
                        ["single", "--arm", arm, "--pdf", f"/corpus/p{pages}.pdf"],
                    )
    if "isolate" in groups:  # which part of the OCRmyPDF import causes it
        for arm in ("patched_reset", "bufsiz_only"):
            for r in reps:
                guarded(f"isolate/p200/{arm}/r{r}", 450, ["single", "--arm", arm, "--pdf", "/corpus/p200.pdf"])
    if "tracemalloc" in groups:
        for arm in arms:
            guarded(
                f"tracemalloc/p200/{arm}",
                600,
                ["single", "--arm", arm, "--tracemalloc", "--pdf", "/corpus/p200.pdf"],
            )
    if "content" in groups:
        for name in ("p200_x10", "p200_x10_raw", "p200_x50_raw", "p200_img_raw"):
            for arm in arms:
                for r in reps:
                    guarded(f"content/{name}/{arm}/r{r}", 700, ["single", "--arm", arm, "--pdf", f"/corpus/{name}.pdf"])
    retention_docs = ["/corpus/p200.pdf", "/corpus/p100.pdf", "/corpus/p200_x10.pdf", "/corpus/p50.pdf", "/corpus/p200_copy1.pdf", "/corpus/p500.pdf"]
    if "retention" in groups:
        for arm in arms:
            guarded(f"retention/{arm}", 700, ["retention", "--arm", arm, "--sleep", "5", "--pdfs", *retention_docs])
        guarded(
            "retention/patched/MALLOC_ARENA_MAX=2",
            700,
            ["retention", "--arm", "patched", "--sleep", "5", "--pdfs", *retention_docs],
            env={"MALLOC_ARENA_MAX": "2"},
        )
    corpus_name = args.concurrency_pdf
    copies = [f"/corpus/{corpus_name}_copy{i}.pdf" for i in range(8)]
    if "threads" in groups:
        for n in (1, 2, 4, 8):
            for arm in arms:
                for r in range(1, args.conc_reps + 1):
                    guarded(
                        f"threads/{corpus_name}/n{n}/{arm}/r{r}",
                        150 + n * args.per_task_projection_mib,
                        ["threads", "--arm", arm, "--n", str(n), "--pdfs", *copies[:n]],
                    )
    if "processes" in groups:
        for n in (1, 2, 4):
            for arm in arms:
                for r in range(1, args.conc_reps + 1):
                    guarded(
                        f"processes/{corpus_name}/n{n}/{arm}/r{r}",
                        n * (150 + args.per_task_projection_mib),
                        ["processes", "--arm", arm, "--n", str(n), "--pdfs", *copies[:n]],
                    )
    if "limits" in groups:
        for limit in ("512m", "768m", "1g", "1536m", "2g"):
            limit_mib = {"512m": 512, "768m": 768, "1g": 1024, "1536m": 1536, "2g": 2048}[limit]
            for n in (1, 2, 4):
                guarded(
                    f"limits/{limit}/threads-n{n}/patched",
                    limit_mib,  # the cap is the most it can ever draw
                    ["threads", "--arm", "patched", "--n", str(n), "--pdfs", *copies[:n]],
                    memory=limit,
                )
            for n in (1, 2, 4):
                guarded(
                    f"limits/{limit}/processes-n{n}/patched",
                    limit_mib,
                    ["processes", "--arm", "patched", "--n", str(n), "--pdfs", *copies[:n]],
                    memory=limit,
                )
    save()
    print(f"wrote {out_path} ({len(results['cases'])} cases)")


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------


def _mib(value: float | int | None) -> float | None:
    return None if value is None else round(value / MIB, 1)


def _stats(values: list[float]) -> dict[str, float | None]:
    values = [v for v in values if v is not None]
    if not values:
        return {"median": None, "min": None, "max": None}
    return {"median": round(statistics.median(values), 1), "min": round(min(values), 1), "max": round(max(values), 1)}


def cmd_summarize(args: argparse.Namespace) -> None:
    data = json.loads(Path(args.results).read_text())
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in data["cases"]:
        key = case["label"].rsplit("/r", 1)[0] if "/r" in case["label"] else case["label"]
        groups.setdefault(key, []).append(case)
    summary: dict[str, Any] = {}
    for key, cases in groups.items():
        rows = []
        for case in cases:
            sup = case.get("supervised") or {}
            recs = sup.get("records") or []
            rec = recs[0] if recs else {}
            cg = (sup.get("supervisor") or {}).get("cgroup_after_child") or {}
            row: dict[str, Any] = {
                "result": case.get("result", "RUN"),
                "exit": (sup.get("supervisor") or {}).get("child_returncode"),
                "oom_kill_events": (cg.get("memory_events") or {}).get("oom_kill"),
                "container_oom_killed": case.get("container_oom_killed"),
                "cgroup_peak_mib": _mib(cg.get("memory_peak")),
                "docker_stats_peak_mib": case.get("docker_stats_peak_mib"),
            }
            if rec.get("scenario") in ("single", "threads"):
                pre = rec["pre"]["rss"]
                peak = rec["sampler"]["peak_rss"]
                row.update(
                    {
                        "bufsiz": rec.get("bufsiz"),
                        "duration_s": rec["duration_s"],
                        "startup_rss_mib": _mib(rec["startup"]["rss"]),
                        "pre_rss_mib": _mib(pre),
                        "peak_rss_mib": _mib(peak),
                        "delta_rss_mib": _mib(peak - pre),
                        "post_rss_mib": _mib(rec["post"]["rss"]),
                        "post_gc_rss_mib": _mib(rec["post_gc"]["rss"]),
                        "peak_vms_mib": _mib(rec["sampler"]["peak_vms"]),
                        "pre_vms_mib": _mib(rec["pre"]["vms"]),
                        "minflt": (rec["post"].get("minflt") or 0) - (rec["pre"].get("minflt") or 0),
                        "majflt": (rec["post"].get("majflt") or 0) - (rec["pre"].get("majflt") or 0),
                        "tracemalloc_peak_mib": _mib(rec.get("tracemalloc_peak")),
                        "chars": (rec.get("result") or {}).get("chars")
                        if rec["scenario"] == "single"
                        else [t["chars"] for t in rec["tasks"]],
                        "ok": True if rec["scenario"] == "single" else all(t["ok"] for t in rec["tasks"]),
                        "start_spread_s": rec.get("start_spread_s"),
                        "pdf_bytes": rec.get("pdf_bytes"),
                    }
                )
            elif rec.get("scenario") == "processes":
                row.update(
                    {
                        "duration_s": rec["duration_s"],
                        "child_peak_rss_mib": [_mib(c["peak_rss"]) for c in rec["children"]],
                        "child_delta_rss_mib": [_mib(c["peak_rss"] - c["pre_rss"]) for c in rec["children"]],
                        "tree_peak_rss_mib": _mib(rec["sampler_parent"]["peak_tree_rss"]),
                        "chars": [c["chars"] for c in rec["children"]],
                        "start_spread_s": rec.get("start_spread_s"),
                    }
                )
            elif rec.get("scenario") == "retention":
                row.update(
                    {
                        "pre_rss_mib": _mib(rec["pre"]["rss"]),
                        "steps": [
                            {
                                "doc": s["doc"],
                                "pdf": s["pdf"],
                                "peak_rss_mib": _mib(s["peak_rss"]),
                                "post_rss_mib": _mib(s["post_rss"]),
                                "post_gc_sleep_rss_mib": _mib(s["post_gc_sleep_rss"]),
                                "post_gc_sleep_vms_mib": _mib(s["post_gc_sleep_vms"]),
                                "duration_s": s["duration_s"],
                            }
                            for s in rec["steps"]
                        ],
                    }
                )
            rows.append(row)
        agg: dict[str, Any] = {"runs": rows}
        numeric = ["duration_s", "pre_rss_mib", "peak_rss_mib", "delta_rss_mib", "post_gc_rss_mib", "cgroup_peak_mib", "docker_stats_peak_mib", "tree_peak_rss_mib", "peak_vms_mib"]
        for field in numeric:
            vals = [r.get(field) for r in rows if isinstance(r.get(field), (int, float))]
            if vals:
                agg[field] = _stats(vals)
        summary[key] = agg
    Path(args.out).write_text(json.dumps(summary, indent=1))
    print(f"wrote {args.out} ({len(summary)} groups)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("corpus")
    p.add_argument("--out", required=True)
    p.add_argument("--extract", action="store_true", help="also record pages/chars (control arm)")
    p.set_defaults(func=cmd_corpus)

    p = sub.add_parser("single")
    p.add_argument("--arm", required=True)
    p.add_argument("--pdf", required=True)
    p.add_argument("--tracemalloc", action="store_true")
    p.set_defaults(func=cmd_single)

    p = sub.add_parser("retention")
    p.add_argument("--arm", required=True)
    p.add_argument("--sleep", type=float, default=5.0)
    p.add_argument("--pdfs", nargs="+", required=True)
    p.set_defaults(func=cmd_retention)

    for name, func in (("threads", cmd_threads), ("processes", cmd_processes)):
        p = sub.add_parser(name)
        p.add_argument("--arm", required=True)
        p.add_argument("--n", type=int, required=True)
        p.add_argument("--pdfs", nargs="+", required=True)
        p.add_argument("--timeout", type=float, default=900)
        p.set_defaults(func=func)

    p = sub.add_parser("supervise")
    p.add_argument("child", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_supervise)

    p = sub.add_parser("matrix")
    p.add_argument("--image", required=True)
    p.add_argument("--corpus", required=True, help="host path of the corpus directory")
    p.add_argument("--out", required=True)
    p.add_argument("--groups", default="sequential,isolate,tracemalloc,content,retention,threads,processes,limits")
    p.add_argument("--pages", default="10,50,100,200,500")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--conc-reps", type=int, default=1)
    p.add_argument("--concurrency-pdf", default="p200")
    p.add_argument("--per-task-projection-mib", type=float, default=300)
    p.add_argument("--min-available-mib", type=int, default=0)
    p.add_argument("--check-vm", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.set_defaults(func=cmd_matrix)

    p = sub.add_parser("summarize")
    p.add_argument("--results", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_summarize)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
