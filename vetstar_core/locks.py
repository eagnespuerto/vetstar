"""Process-wide locks shared by the site and the Pi front end."""
from __future__ import annotations

import threading

# matplotlib's pyplot state machine is not thread-safe. Every figure built
# off the main thread (prefetch workers, vet jobs, the Pi GUI's worker
# thread) takes this lock. Re-entrant so a locked helper can call another.
PLOT_LOCK = threading.RLock()
