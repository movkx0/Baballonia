# Beyond refresh-rate tool (work in progress)

Linux prototype for the Bigscreen Beyond 2e. The intended scope is **both SteamVR
and Monado**, but SteamVR switching is not implemented. This tool has not been
validated with a connected headset. It is a checkpoint for continued development.

The script uses Python 3.10 or newer and the standard library. Live probing also
requires a Vulkan loader and a GPU driver exposing `VK_KHR_display`.

## Current commands

Run these commands from the repository root:

```sh
python3 tools/beyond-refresh/beyond_refresh.py probe
python3 tools/beyond-refresh/beyond_refresh.py probe --json
python3 tools/beyond-refresh/beyond_refresh.py monado 75
python3 tools/beyond-refresh/beyond_refresh.py monado 90
```

`probe` reads the modes advertised by Vulkan. `monado` selects an advertised
75 Hz or 90 Hz mode and prints a launch command. It does not change anything by
default. After stopping any existing Monado session, add `--run` to launch
`monado-service` with the selected environment variables. Use `--executable` if
your service executable has another name or path. Envision users can apply the
printed environment variables to their runtime configuration.

If automatic display identification fails, inspect `probe` and pass both
`--gpu INDEX` and `--display INDEX` to the `monado` command.

You can instead capture a complete Monado mode list by starting Monado with
`XRT_COMPOSITOR_PRINT_MODES=1`, then use that log:

```sh
python3 tools/beyond-refresh/beyond_refresh.py probe --mode-log monado.log
python3 tools/beyond-refresh/beyond_refresh.py monado 75 --mode-log monado.log
```

Mode indices belong to the current display and GPU enumeration. Capture a fresh
log after changing hardware, drivers, or GPU selection; a saved log can become
stale. The tool cannot create a mode that the headset does not advertise. It does
not verify the active refresh rate after launch, flash firmware, or modify the
headset's calibration/configuration.

Monado's `XRT_COMPOSITOR_DESIRED_MODE` selects an enumeration index, not a refresh
rate. See the [Monado compositor documentation](https://monado.freedesktop.org/getting-started.html#compositor)
and [direct-display implementation](https://gitlab.freedesktop.org/monado/monado/-/blob/main/src/xrt/compositor/main/comp_window_direct.c).

## Resume SteamVR investigation

Bigscreen Beyond Utility was not installed during development. An anonymous
Steam download was denied because the account lacked access to app `2467050`.
Install the utility through your own Steam account (`steam://install/2467050`),
then locate these files in its installation folder:

- `bin/BeyondHID.exe`
- `bin/steamvr/BeyondSteamVR/bin/win64/driver_BeyondSteamVR.dll`

The read-only inspection command hashes these files and extracts relevant ASCII
and UTF-16 strings without executing them:

```sh
python3 tools/beyond-refresh/beyond_refresh.py inspect-utility /path/to/utility
```

Next steps:

1. Inspect the utility and driver to establish how refresh-rate changes reach
   the headset and SteamVR. Strings alone do not establish the protocol.
2. Determine which operations need a native Linux implementation. Neither
   SteamVR's `preferredRefreshRate` setting nor changing `display_frequency`
   in a Lighthouse configuration has been verified as sufficient for Beyond 2e.
3. If configuration changes are needed, preserve the connected headset's own
   calibration and provide a backup/restore path. Do not copy another headset's
   configuration or send guessed HID reports.
4. Validate both advertised modes and the actual active refresh rate on hardware
   under SteamVR and Monado. Test live Vulkan enumeration, log parsing, and
   failure cases before treating this prototype as a finished tool.

Vendor references for the configuration workflow:
[refresh-rate guidance](https://bigscreen-inc.frontkb.com/en/articles/2700928)
and [related support instructions](https://bigscreen-inc.frontkb.com/en/articles/2740352).
No vendor executable, DLL, firmware, or downloaded research artifact is included
in this repository.
