"""Shared same-dimension units. Physical relations remain explicit, separate inputs."""
ADDITIONAL_UNITS = {"s": ("time", 1.), "min": ("time", 60.), "h": ("time", 3600.),
    "bar": ("pressure", 1e5), "kPa": ("pressure", 1e3),
    "ms": ("time", 1e-3), "us": ("time", 1e-6),
    "μs": ("time", 1e-6), "µs": ("time", 1e-6), "ns": ("time", 1e-9),
    "ps": ("time", 1e-12), "fs": ("time", 1e-15),
    "V/K": ("thermopower", 1.), "mV/K": ("thermopower", 1e-3),
    "uV/K": ("thermopower", 1e-6), "μV/K": ("thermopower", 1e-6), "µV/K": ("thermopower", 1e-6)}
