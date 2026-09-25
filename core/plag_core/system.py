"""Live system vitals and the 'why is my laptop lagging' diagnosis (read-only, L0)."""

import ctypes
import threading
import time
from ctypes import wintypes

import psutil


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):  # SYSTEM_PROCESS_INFORMATION (the head of it), as Task Manager reads it
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG), ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong), ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG), ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong), ("UserTime", ctypes.c_longlong), ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString), ("BasePriority", wintypes.LONG), ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p), ("HandleCount", wintypes.ULONG), ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_size_t), ("PeakVirtualSize", ctypes.c_size_t), ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
    ]


_ntdll = ctypes.WinDLL("ntdll")


def _all_processes() -> list[tuple[int, str, int, int]]:
    """(pid, name, cpu time in 100 ns units, working set bytes) for every process, in one system call (~10 ms)."""
    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG()
        status = _ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(needed)) & 0xFFFFFFFF
        if status == 0xC0000004:  # buffer too small: grow and retry
            size = max(size * 2, needed.value + 65536)
            continue
        if status != 0:
            return []
        break
    out, offset, base = [], 0, ctypes.addressof(buf)
    while True:
        info = _ProcessInfo.from_address(base + offset)
        pid = info.UniqueProcessId or 0
        name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2) if info.ImageName.Buffer else "System Idle"
        out.append((pid, name, info.UserTime + info.KernelTime, info.WorkingSetSize))
        if not info.NextEntryOffset:
            return out
        offset += info.NextEntryOffset


class SystemSampler:
    """Shared by the dashboard vitals loop and the diagnosis tool, so every read takes the lock."""

    def __init__(self) -> None:
        psutil.cpu_percent(None)
        self._lock = threading.Lock()
        self._t = time.monotonic()
        self._disk = psutil.disk_io_counters()
        self._net = psutil.net_io_counters()
        self._cpu_times: dict[int, int] = {}
        self._proc_t = 0.0
        self.latest: dict = {}
        self.top: list[dict] = []
        self.top_at = 0.0

    def sample(self) -> dict:
        with self._lock:
            return self._sample()

    def processes(self, top: int = 6) -> list[dict]:
        with self._lock:
            result = self._processes(top)
            self.top_at = time.monotonic()
            return result

    def _sample(self) -> dict:
        now = time.monotonic()
        dt = max(0.2, now - self._t)
        self._t = now
        disk, net = psutil.disk_io_counters(), psutil.net_io_counters()
        busy = 0.0
        rbps = wbps = 0.0
        if disk and self._disk:
            rbps = (disk.read_bytes - self._disk.read_bytes) / dt
            wbps = (disk.write_bytes - self._disk.write_bytes) / dt
            busy_ms = (disk.read_time - self._disk.read_time) + (disk.write_time - self._disk.write_time)
            busy = min(100.0, max(0.0, busy_ms / (dt * 10)))
        up = (net.bytes_sent - self._net.bytes_sent) / dt if net and self._net else 0.0
        down = (net.bytes_recv - self._net.bytes_recv) / dt if net and self._net else 0.0
        self._disk, self._net = disk, net
        vm = psutil.virtual_memory()
        freq = psutil.cpu_freq()
        bat = psutil.sensors_battery()
        self.latest = {
            "cpu": round(psutil.cpu_percent(None), 1),
            "cpu_freq": {"current": round(freq.current), "max": round(freq.max)} if freq else None,
            "cores": psutil.cpu_count(),
            "mem": {"pct": round(vm.percent, 1), "used_gb": round((vm.total - vm.available) / 2**30, 1),
                    "total_gb": round(vm.total / 2**30, 1)},
            "disk": {"busy_pct": round(busy, 1), "read_bps": int(max(0, rbps)), "write_bps": int(max(0, wbps))},
            "net": {"up_bps": int(max(0, up)), "down_bps": int(max(0, down))},
            "battery": {"pct": round(bat.percent), "plugged": bool(bat.power_plugged)} if bat else None,
        }
        return self.latest

    def _processes(self, top: int) -> list[dict]:
        """Top apps grouped by executable name (Chrome's 20+ processes count as one app)."""
        now = time.monotonic()
        elapsed = max(0.2, now - self._proc_t) if self._proc_t else 0.0
        self._proc_t = now
        ncpu = psutil.cpu_count() or 1
        groups: dict[str, dict] = {}
        times: dict[int, int] = {}
        for pid, name, cpu_time, working_set in _all_processes():
            if pid in (0, 4):
                continue
            times[pid] = cpu_time
            prev = self._cpu_times.get(pid)
            cpu = ((cpu_time - prev) / 1e7 / elapsed / ncpu * 100) if (prev is not None and elapsed) else 0.0
            name = (name or "unknown").removesuffix(".exe")
            g = groups.setdefault(name, {"name": name, "count": 0, "cpu": 0.0, "mem_mb": 0.0})
            g["count"] += 1
            g["cpu"] += max(0.0, cpu)
            g["mem_mb"] += working_set / 2**20
        self._cpu_times = times
        ranked = sorted(groups.values(), key=lambda g: (g["cpu"] * 40 + g["mem_mb"] / 100), reverse=True)
        self.top = [{**g, "cpu": round(g["cpu"], 1), "mem_mb": round(g["mem_mb"])} for g in ranked[:top]]
        return self.top


def diagnose(s: dict, top: list[dict], lang: str) -> str:
    """Rule-based findings, phrased in the user's language. Nothing is killed or changed."""
    findings: list[tuple[str, str, str]] = []  # (english, hindi, hinglish)
    hog_cpu = max(top, key=lambda g: g["cpu"], default=None)
    hog_mem = max(top, key=lambda g: g["mem_mb"], default=None)
    cpu, mem = s.get("cpu", 0), s.get("mem", {})
    if cpu >= 80 and hog_cpu:
        n = hog_cpu["name"]
        findings.append((f"CPU is at {cpu:.0f}%, mostly {n}", f"CPU {cpu:.0f}% पर है, सबसे ज़्यादा {n} ले रहा है",
                         f"CPU {cpu:.0f}% par hai, sabse zyada {n} le raha hai"))
    if mem.get("pct", 0) >= 80 and hog_mem:
        gb = hog_mem["mem_mb"] / 1024
        n = hog_mem["name"]
        findings.append((f"memory is {mem['pct']:.0f}% full and {n} uses {gb:.1f} GB",
                         f"मेमोरी {mem['pct']:.0f}% भरी है और {n} {gb:.1f} GB ले रहा है",
                         f"memory {mem['pct']:.0f}% full hai aur {n} {gb:.1f} GB le raha hai"))
    if s.get("disk", {}).get("busy_pct", 0) >= 85:
        findings.append(("the disk is almost fully busy", "डिस्क लगभग पूरी व्यस्त है", "disk almost poori busy hai"))
    bat = s.get("battery")
    if bat and not bat["plugged"] and bat["pct"] <= 20:
        findings.append((f"battery is at {bat['pct']}%, which can throttle the CPU",
                         f"बैटरी {bat['pct']}% है, इससे CPU धीमा हो सकता है",
                         f"battery {bat['pct']}% hai, isse CPU slow ho sakta hai"))
    idx = {"en": 0, "hi": 1, "mixed": 2}.get(lang, 0)
    if not findings:
        return [
            f"Nothing looks wrong right now: CPU {cpu:.0f}%, memory {mem.get('pct', 0):.0f}%.",
            f"अभी सब ठीक दिख रहा है: CPU {cpu:.0f}%, मेमोरी {mem.get('pct', 0):.0f}%।",
            f"Abhi sab theek lag raha hai: CPU {cpu:.0f}%, memory {mem.get('pct', 0):.0f}%.",
        ][idx]
    body = [" and ", " और ", " aur "][idx].join(f[idx] for f in findings)
    if idx != 1:
        body = body[0].upper() + body[1:]
    end = [".", "।", "."][idx]
    tail = [" Details are on the dashboard.", " डिटेल डैशबोर्ड पर है।", " Details dashboard par hain."][idx]
    return body + end + tail


sampler = SystemSampler()
