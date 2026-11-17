"""Run the V2 CLI; explicit V1 commands retain the legacy runtime."""

from omni.composition.cli import main

raise SystemExit(main())
