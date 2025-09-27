import threading
import time
import psutil


class TimingStruct:
    def __init__(self):
        self.start = None          # CPU start (float) or 0.0
        self.end = None            # CPU end (float)
        self.elapsed = None        # Elapsed CPU (float) or wall fallback
        self.wall_start = None     # Wall clock start
        self.wall_end = None       # Wall clock end

class Stopwatch:
    def __init__(self):
        self.timers = {}

    def _get_thread_cpu_times(self, thread_id):
        try:
            for thread_info in psutil.Process().threads():
                if thread_info.id == thread_id:
                    # Some platforms may return None
                    return thread_info.user_time, thread_info.system_time
        except Exception:
            pass
        return None, None

    def _safe_cpu_sum(self, thread_id):
        ut, st = self._get_thread_cpu_times(thread_id)
        vals = [v for v in (ut, st) if v is not None]
        return sum(vals) if vals else None

    def start_timer(self, name) -> None:
        ts = TimingStruct()
        thread_id = threading.get_ident()
        cpu_sum = self._safe_cpu_sum(thread_id)
        ts.start = cpu_sum if cpu_sum is not None else 0.0
        ts.wall_start = time.perf_counter()
        self.timers[name] = ts

    def stop_timer(self, name: str) -> None:
        ts = self.timers.get(name)
        if ts is None:
            return
        thread_id = threading.get_ident()
        cpu_sum = self._safe_cpu_sum(thread_id)
        ts.wall_end = time.perf_counter()
        if cpu_sum is not None:
            ts.end = cpu_sum
            ts.elapsed = ts.end - ts.start
        else:
            # Fallback: wall time
            ts.end = ts.start
            ts.elapsed = ts.wall_end - ts.wall_start

    def merge(self, other: "Stopwatch") -> None:
        for name, ots in other.timers.items():
            if name in self.timers:
                if self.timers[name].elapsed is None:
                    self.timers[name].elapsed = ots.elapsed
                elif ots.elapsed is not None:
                    self.timers[name].elapsed += ots.elapsed
            else:
                self.timers[name] = ots

    def get_time(self, name):
        ts = self.timers.get(name)
        if not ts:
            return 0.0
        return ts.elapsed if ts.elapsed is not None else 0.0

    def get_total_time(self):
        return sum(self.get_time(n) for n in self.timers.keys())