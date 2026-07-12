"""Generate external scheduler definitions that invoke the stable OFD CLI."""

from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path


def cron_entry(project: Path, *, hour: int, minute: int) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid cron time")
    command = [
        sys.executable, "-m", "open_financial_data.cli", "update",
        "--project", str(project.resolve()), "--format", "json", "--non-interactive",
    ]
    return f"{minute} {hour} * * 1-5 " + " ".join(shlex.quote(item) for item in command)


def launchd_plist(project: Path, *, hour: int, minute: int) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid launchd time")
    root = project.resolve()
    payload = {
        "Label": f"org.openfinancialdata.update.{root.name}",
        "ProgramArguments": [
            sys.executable, "-m", "open_financial_data.cli", "update",
            "--project", str(root), "--format", "json", "--non-interactive",
        ],
        "StartCalendarInterval": [
            {"Hour": hour, "Minute": minute, "Weekday": weekday}
            for weekday in range(1, 6)
        ],
        "WorkingDirectory": str(root),
        "StandardOutPath": str(root / ".ofd" / "logs" / "scheduler.stdout.log"),
        "StandardErrorPath": str(root / ".ofd" / "logs" / "scheduler.stderr.log"),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode()


def systemd_units(project: Path, *, hour: int, minute: int) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid systemd timer time")
    root = project.resolve()
    command = " ".join(shlex.quote(item) for item in (
        sys.executable, "-m", "open_financial_data.cli", "update",
        "--project", str(root), "--format", "json", "--non-interactive",
    ))
    return f"""# ofd-update.service
[Unit]
Description=OpenFinancialData update for {root.name}

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={command}

# ofd-update.timer
[Unit]
Description=Schedule OpenFinancialData update for {root.name}

[Timer]
OnCalendar=Mon..Fri *-*-* {hour:02d}:{minute:02d}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
