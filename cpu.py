"""Keep the app off the cores the game runs on.

Recent Intel CPUs have two kinds of core: performance cores, which games
run on, and efficiency cores. The app's heavy work -- working out each
clip's language on the CPU -- is bursty and can wait a moment; CS2's frames
can't. So on such a CPU the whole app is pinned to the efficiency cores.

Measured on a Core Ultra 9 275HX (8 performance + 16 efficiency cores), the
language pass over 30 clips:
    all cores, 4 threads:              1.46 s a clip, 72% on performance cores
    efficiency cores only, 8 threads:  0.94 s a clip, none on them
OpenVINO's own "efficiency cores only" setting still left about 40% of the
work on performance cores, and Windows' EcoQoS changed nothing (plugged
in), so this uses plain process affinity.
"""
import sys


def efficiency_cores():
    """The logical CPUs of the least powerful core type, or [] when the CPU
    has only one kind (or this isn't Windows)."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        import ctypes.wintypes as W
        k32 = ctypes.windll.kernel32
        need = W.ULONG(0)
        k32.GetSystemCpuSetInformation(None, 0, ctypes.byref(need), None, 0)
        buf = ctypes.create_string_buffer(need.value)
        if not k32.GetSystemCpuSetInformation(buf, need, ctypes.byref(need), None, 0):
            return []
        classes, off = {}, 0
        # SYSTEM_CPU_SET_INFORMATION: Size(4) Type(4) Id(4) Group(2)
        # LogicalProcessorIndex(1) CoreIndex(1) LastLevelCacheIndex(1)
        # NumaNodeIndex(1) EfficiencyClass(1) ...
        while off < need.value:
            size = ctypes.c_ulong.from_buffer(buf, off).value
            if size <= 0:
                break
            group = ctypes.c_ushort.from_buffer(buf, off + 12).value
            if group == 0:                   # affinity below covers group 0
                classes[buf.raw[off + 14]] = buf.raw[off + 18]
            off += size
        if len(set(classes.values())) < 2:
            return []
        lowest = min(classes.values())
        return sorted(cpu for cpu, c in classes.items() if c == lowest)
    except Exception as e:  # noqa: BLE001
        print(f"[cpu] could not read the core types: {e}")
        return []


def detector_threads():
    """Threads for the CPU language pass: 8 on the efficiency cores (fewer
    when there are fewer than 16), else 4 (see stt._get_detector)."""
    cores = efficiency_cores()
    return max(2, min(8, len(cores) // 2)) if cores else 4


def stay_off_performance_cores():
    """Pin this process to the efficiency cores, if the CPU has both kinds
    and enough of them. Returns the cores used, or [] if left alone."""
    cores = efficiency_cores()
    if len(cores) < 4:
        return []
    try:
        import psutil
        psutil.Process().cpu_affinity(cores)
        print(f"[cpu] running on the {len(cores)} efficiency cores, leaving the "
              "performance cores to the game")
        return cores
    except Exception as e:  # noqa: BLE001
        print(f"[cpu] could not move to the efficiency cores: {e}")
        return []
