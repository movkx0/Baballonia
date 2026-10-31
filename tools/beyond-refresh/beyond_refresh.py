#!/usr/bin/env python3
"""Inspect Beyond display modes and launch Monado at a supported refresh rate.

Uses read-only Vulkan queries. It does not flash headset firmware/configuration.
SteamVR switching remains unsupported pending analysis of the vendor utility.
"""

from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys


class RefreshError(Exception):
    pass


@dataclass(frozen=True)
class Mode:
    gpu: int | None
    display: int
    name: str
    index: int
    width: int
    height: int
    millihertz: int

    @property
    def hz(self) -> float:
        return self.millihertz / 1000


class Extent(C.Structure):
    _fields_ = [("width", C.c_uint32), ("height", C.c_uint32)]


class ApplicationInfo(C.Structure):
    _fields_ = [
        ("sType", C.c_uint32), ("pNext", C.c_void_p),
        ("pApplicationName", C.c_char_p), ("applicationVersion", C.c_uint32),
        ("pEngineName", C.c_char_p), ("engineVersion", C.c_uint32),
        ("apiVersion", C.c_uint32),
    ]


class InstanceInfo(C.Structure):
    _fields_ = [
        ("sType", C.c_uint32), ("pNext", C.c_void_p), ("flags", C.c_uint32),
        ("pApplicationInfo", C.POINTER(ApplicationInfo)),
        ("enabledLayerCount", C.c_uint32), ("ppEnabledLayerNames", C.POINTER(C.c_char_p)),
        ("enabledExtensionCount", C.c_uint32), ("ppEnabledExtensionNames", C.POINTER(C.c_char_p)),
    ]


class Extension(C.Structure):
    _fields_ = [("name", C.c_char * 256), ("version", C.c_uint32)]


class DisplayProperties(C.Structure):
    _fields_ = [
        ("display", C.c_uint64), ("name", C.c_char_p),
        ("physicalDimensions", Extent), ("physicalResolution", Extent),
        ("supportedTransforms", C.c_uint32), ("planeReorderPossible", C.c_uint32),
        ("persistentContent", C.c_uint32),
    ]


class ModeParameters(C.Structure):
    _fields_ = [("visibleRegion", Extent), ("refreshRate", C.c_uint32)]


class ModeProperties(C.Structure):
    _fields_ = [("displayMode", C.c_uint64), ("parameters", ModeParameters)]


def _enumerate(function, item_type, *args):
    # Vulkan can return VK_INCOMPLETE if a display is connected during enumeration.
    for _ in range(3):
        count = C.c_uint32()
        result = function(*args, C.byref(count), None)
        if result not in (0, 5):
            raise RefreshError(f"Vulkan enumeration failed (VkResult {result}).")
        if count.value > 4096:
            raise RefreshError("Vulkan returned an unreasonable item count.")
        if not count.value:
            return []
        capacity = count.value
        items = (item_type * capacity)()
        result = function(*args, C.byref(count), items)
        if result == 0 and count.value <= capacity:
            return list(items[:count.value])
        if result != 5:
            raise RefreshError(f"Vulkan enumeration failed (VkResult {result}).")
    raise RefreshError("Display modes changed during enumeration; retry with the headset connected.")


def vulkan_modes() -> list[Mode]:
    library = ctypes.util.find_library("vulkan")
    if not library:
        raise RefreshError("Vulkan loader not found. Install your GPU's Vulkan driver, or use --mode-log.")
    vk = C.CDLL(library)
    enumerate_extensions = vk.vkEnumerateInstanceExtensionProperties
    enumerate_extensions.argtypes = [C.c_char_p, C.POINTER(C.c_uint32), C.POINTER(Extension)]
    enumerate_extensions.restype = C.c_int32
    available = {bytes(e.name) for e in _enumerate(enumerate_extensions, Extension, None)}
    required = [b"VK_KHR_surface", b"VK_KHR_display"]
    if not set(required) <= available:
        raise RefreshError("The Vulkan driver does not expose VK_KHR_display; use a Monado --mode-log instead.")

    app = ApplicationInfo(sType=0, pApplicationName=b"beyond-refresh", apiVersion=1 << 22)
    extensions = (C.c_char_p * len(required))(*required)
    info = InstanceInfo(sType=1, pApplicationInfo=C.pointer(app),
                        enabledExtensionCount=len(required), ppEnabledExtensionNames=extensions)
    instance = C.c_void_p()
    create = vk.vkCreateInstance
    create.argtypes = [C.POINTER(InstanceInfo), C.c_void_p, C.POINTER(C.c_void_p)]
    create.restype = C.c_int32
    result = create(C.byref(info), None, C.byref(instance))
    if result != 0:
        raise RefreshError(f"Cannot initialize Vulkan (VkResult {result}); check GPU driver access.")

    destroy = vk.vkDestroyInstance
    destroy.argtypes = [C.c_void_p, C.c_void_p]
    destroy.restype = None
    try:
        get_proc = vk.vkGetInstanceProcAddr
        get_proc.argtypes = [C.c_void_p, C.c_char_p]
        get_proc.restype = C.c_void_p

        def function(name, *types):
            address = get_proc(instance, name.encode("ascii"))
            if not address:
                raise RefreshError(f"Vulkan function {name} is unavailable; use --mode-log.")
            return C.CFUNCTYPE(C.c_int32, *types)(address)

        devices = function("vkEnumeratePhysicalDevices", C.c_void_p,
                           C.POINTER(C.c_uint32), C.POINTER(C.c_void_p))
        displays = function("vkGetPhysicalDeviceDisplayPropertiesKHR", C.c_void_p,
                            C.POINTER(C.c_uint32), C.POINTER(DisplayProperties))
        modes = function("vkGetDisplayModePropertiesKHR", C.c_void_p, C.c_uint64,
                         C.POINTER(C.c_uint32), C.POINTER(ModeProperties))
        output = []
        for gpu, device in enumerate(_enumerate(devices, C.c_void_p, instance)):
            for display, properties in enumerate(_enumerate(displays, DisplayProperties, device)):
                name = (properties.name or b"Unnamed display").decode("utf-8", errors="replace")
                for index, mode in enumerate(_enumerate(modes, ModeProperties, device, properties.display)):
                    parameters = mode.parameters
                    output.append(Mode(gpu, display, name, index, parameters.visibleRegion.width,
                                       parameters.visibleRegion.height, parameters.refreshRate))
        return output
    finally:
        destroy(instance, None)


DEBUG_ROW = re.compile(r"Available Vk direct mode\s+(\d+):\s+(\d+)x(\d+)@([\d.]+)")
TABLE_ROW = re.compile(r"\|\s*(\d+)\s*\|\s*(\d+)x(\d+)@([\d.]+)")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def log_modes(text: str) -> list[Mode]:
    """Parse the latest Monado mode list, including the newer PRINT_MODES format."""
    rows: dict[int, Mode] = {}
    table = False
    expected_count = None
    for line in ANSI.sub("", text).splitlines():
        if "Available Vk modes for direct mode" in line:
            rows, table, expected_count = {}, True, None
            continue
        match = DEBUG_ROW.search(line)
        is_debug = match is not None
        if match is None and table:
            match = TABLE_ROW.search(line)
        if match:
            index, width, height = map(int, match.groups()[:3])
            rate = int(Decimal(match[4]) * 1000)
            if is_debug and index == 0:
                rows, expected_count, table = {}, None, False
            if index in rows or width <= 0 or height <= 0 or rate <= 0:
                raise RefreshError("Invalid or repeated mode entry in the Monado log.")
            rows[index] = Mode(None, 0, "Monado log", index, width, height, rate)
        elif table and (count := re.search(r"Listed (\d+) modes", line)):
            expected_count = int(count[1])
            table = False
    if not rows:
        raise RefreshError("No modes found. Capture Monado output with XRT_COMPOSITOR_PRINT_MODES=1.")
    if sorted(rows) != list(range(len(rows))) or (expected_count is not None and expected_count != len(rows)):
        raise RefreshError("The latest Monado mode list is incomplete; capture a fresh log.")
    if table:
        raise RefreshError("The latest Monado mode table is unfinished; capture a complete log.")
    return list(rows.values())


def select_mode(modes: list[Mode], hz: int, gpu: int | None = None,
                display: int | None = None, from_log: bool = False) -> Mode:
    if hz not in (75, 90):
        raise RefreshError("Only the stock 75 Hz and 90 Hz modes are supported.")
    if display is not None and gpu is None:
        raise RefreshError("Specify both --gpu and --display to select a display explicitly.")
    if from_log and (gpu is not None or display is not None):
        raise RefreshError("GPU/display selection is unavailable with --mode-log; use a log from the intended GPU.")
    candidates = []
    for mode in modes:
        if gpu is not None and mode.gpu != gpu:
            continue
        if display is not None and mode.display != display:
            continue
        named_beyond = "beyond" in mode.name.lower() or "bigscreen" in mode.name.lower()
        known_size = (mode.width, mode.height) in ((5088, 2544), (3840, 1920))
        if not (named_beyond or display is not None or (from_log and known_size)):
            continue
        candidates.append(mode)
    displays = {(m.gpu, m.display) for m in candidates}
    if not displays:
        raise RefreshError("No Beyond display identified. Run probe; select --gpu and --display explicitly if needed.")
    if len(displays) != 1:
        raise RefreshError("Multiple Beyond displays found. Specify --gpu and --display from probe.")
    matching = [m for m in candidates if abs(m.millihertz - hz * 1000) <= 100]
    if not matching:
        offered = ", ".join(f"{m.width}x{m.height}@{m.hz:g}" for m in candidates)
        raise RefreshError(f"The display does not advertise {hz} Hz (available: {offered}). "
                           "This tool cannot add a missing mode or change headset firmware.")
    return max(matching, key=lambda m: (m.width * m.height, -abs(m.millihertz - hz * 1000), -m.index))


def monado_environment(mode: Mode, current: dict[str, str]) -> dict[str, str]:
    environment = dict(current)
    environment["XRT_COMPOSITOR_DESIRED_MODE"] = str(mode.index)
    environment["XRT_COMPOSITOR_PRINT_MODES"] = "1"
    if mode.gpu is not None:
        environment["XRT_COMPOSITOR_FORCE_GPU_INDEX"] = str(mode.gpu)
    return environment


def inspect_utility(root: Path) -> dict:
    """Hash and locate useful strings without executing proprietary binaries."""
    if (root / "bin").is_dir():
        root = root / "bin"
    targets = [root / "BeyondHID.exe",
               root / "steamvr/BeyondSteamVR/bin/win64/driver_BeyondSteamVR.dll"]
    interesting = re.compile(r"refresh|frequency|lighthouse|uploadconfig|downloadconfig|"
                             r"display.?mode|preferredRefresh|\.vrsettings", re.IGNORECASE)
    report = []
    for path in targets:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not data.startswith(b"MZ"):
            raise RefreshError(f"Not a Windows PE executable: {path}")
        strings = []
        for pattern, encoding in [(rb"[\x20-\x7e]{6,}", "ascii"),
                                  (rb"(?:[\x20-\x7e]\x00){6,}", "utf-16-le")]:
            for match in re.finditer(pattern, data):
                value = match[0].decode(encoding)
                if interesting.search(value):
                    strings.append({"offset": hex(match.start()), "encoding": encoding, "text": value[:1024]})
        report.append({"file": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                       "size": len(data), "strings": strings})
    if not report:
        raise RefreshError("No utility binaries found. Supply the Bigscreen Beyond Driver folder or its bin folder.")
    return {"files": report, "note": "Strings identify investigation targets; they do not prove a refresh-rate protocol."}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="Read Vulkan display modes without changing them")
    probe.add_argument("--mode-log", type=Path, help="Read a Monado log instead of querying Vulkan")
    probe.add_argument("--json", action="store_true")
    monado = commands.add_parser("monado", help="Select an advertised 75/90 Hz mode for a new Monado session")
    monado.add_argument("hz", type=int, choices=[75, 90])
    monado.add_argument("--mode-log", type=Path)
    monado.add_argument("--gpu", type=int)
    monado.add_argument("--display", type=int)
    monado.add_argument("--run", action="store_true", help="Launch Monado; otherwise only print the command")
    monado.add_argument("--executable", default="monado-service")
    inspect = commands.add_parser("inspect-utility", help="Inspect vendor EXE/DLL strings without running them")
    inspect.add_argument("path", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect-utility":
            print(json.dumps(inspect_utility(args.path), indent=2))
            return 0
        modes = log_modes(args.mode_log.read_text()) if args.mode_log else vulkan_modes()
        if args.command == "probe":
            if args.json:
                print(json.dumps([asdict(m) for m in modes], indent=2))
            else:
                for mode in modes:
                    print(f"GPU {mode.gpu if mode.gpu is not None else '?'} display {mode.display} "
                          f"({mode.name}): mode {mode.index}: {mode.width}x{mode.height}@{mode.hz:g} Hz")
            if not modes:
                raise RefreshError("No Vulkan displays found. Check headset/driver access or use --mode-log.")
            return 0
        mode = select_mode(modes, args.hz, args.gpu, args.display, args.mode_log is not None)
        environment = monado_environment(mode, dict(os.environ))
        settings = [f"{key}={environment[key]}" for key in
                    ("XRT_COMPOSITOR_DESIRED_MODE", "XRT_COMPOSITOR_PRINT_MODES", "XRT_COMPOSITOR_FORCE_GPU_INDEX")
                    if key in environment]
        print(f"Selected advertised mode {mode.index}: {mode.width}x{mode.height}@{mode.hz:g} Hz.", flush=True)
        print(shlex.join(["env", *settings, args.executable]), flush=True)
        if args.run:
            os.execvpe(args.executable, [args.executable], environment)
        else:
            print("Preview only. Stop any existing Monado session, then use --run or apply these settings in Envision.")
        return 0
    except (RefreshError, OSError, ValueError) as error:
        print(f"beyond-refresh: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
