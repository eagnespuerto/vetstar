"""FastAPI routers, one per feature. main.py includes them in this order."""
from __future__ import annotations

from . import habitability, mast, microlensing, multisector, nemesis, observables, transit

ROUTERS = [transit.router, mast.router, habitability.router, multisector.router, observables.router, microlensing.router, nemesis.router]
