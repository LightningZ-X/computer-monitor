# Project requirements

- The user's priority is lightweight monitoring that does not interfere with work or games.
- Keep tkinter and the existing optional dependencies. Do not add a web view, Electron,
  continuous animations, plotting engines or a second hardware polling loop.
- Defaults: hardware sampling every 2 seconds, logging every 5 seconds; minimum supported
  sampling interval is 0.5 seconds. Never restore the old 5 ms busy-polling mode.
- Hidden/minimized windows must stop table/summary rendering while collection and alerts
  continue. Repaint only changed rows. Keep icon and alert-history caches bounded.
- Use the checked-in VELTRIX artwork consistently for header, tray and Windows shortcuts.
  Rasterize vector artwork in build tools, never on every runtime refresh.
- Validate changes with `python tests/test_hwinfo_parse.py`,
  `python tests/test_lightweight_ui.py` and syntax checks. Use isolated temporary data for
  benchmarks and tests; never overwrite the user's monitoring database or exports.
- Performance claims must identify the measured workload; synthetic UI timing is not a
  guarantee of hardware-driver overhead or zero impact on all systems.
