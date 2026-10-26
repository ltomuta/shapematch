"""Spawned, disposable CAD workers with a serializable path/data interface."""
import math
import multiprocessing as mp
import os
import threading
import time


def cad_operation(operation, data):
    import numpy as np
    from shapematch_cache import load_model, save_model, source_hash
    if operation == "extract":
        import random
        import shapematch as sm
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        random.seed(0)
        np.random.seed(0)
        timings = {}
        model = sm.getModelFeatures2(data["path"], sm.Point(0, 0, 0), 1, 0, [0, 0, 1], timings=timings)
        if model is None:
            raise ValueError("No usable surface features")
        if source_hash(data["path"]) != data["hash"]:
            raise ValueError("Source changed during extraction")
        geometry = model.objectHandle
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(geometry.shape, props)
        volume = abs(props.Mass())
        inertia = props.MatrixOfInertia()
        moments = np.linalg.eigvalsh([[inertia.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
        BRepGProp.SurfaceProperties_s(geometry.shape, props)
        area = props.Mass()
        if volume <= 0 or moments[-1] <= 0:
            raise ValueError("Geometry requires positive volume and inertia")
        histogram = []
        for name in ("featureMaxPoints", "featureMinPoints", "featureMaxCentres", "featureMinCentres"):
            points = getattr(model, name)
            radii = np.linalg.norm(np.asarray(points).reshape(-1, 3) - model.centroid, axis=1) / volume ** (1/3)
            histogram.extend(np.histogram(radii, bins=np.linspace(0, 4, 33))[0].tolist())
        histogram.extend(np.histogram(np.asarray(model.featureSphereDisps) / volume ** (1/3),
                                      bins=np.linspace(0, 4, 33))[0].tolist())
        total = sum(histogram)
        histogram = [x / max(total, 1) for x in histogram]
        summary = [area / volume ** (2/3), *list(moments[:2] / moments[-1])]
        save_model(model, data["cache"])
        return {"summary": summary, "histogram": histogram, "volume": volume,
                "area": area, "diagonal": geometry.diagonal, "timings": timings}
    if operation in ("compare", "preview"):
        source = load_model(data["query_cache"], source_path=data["query"])
        target = load_model(data["cache"], source_path=data["path"])
        started = time.perf_counter()
        if operation == "compare":
            from shapematch_workflows import compare
            result = compare(source, target, 0, estimate_scale=data.get("estimate_scale", True))
            result["timings"] = {"registration": time.perf_counter() - started}
        else:
            from shapematch_html import comparison_payload
            result = comparison_payload(source, target, data["comparison"], include_booleans=False)
            result["timings"] = {"preview": time.perf_counter() - started}
        return result
    raise ValueError(f"Unknown CAD operation: {operation}")


def _entry(connection, operation, data):
    try:
        connection.send((True, cad_operation(operation, data)))
    except BaseException as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


class WorkerError(RuntimeError):
    pass


class Supervisor:
    """Each operation gets its own process, so a crash/timeout cannot poison a pool.

    A semaphore bounds live workers, including previews. Poll pipes before join
    because mesh payloads can exceed the OS pipe buffer.
    """
    def __init__(self, workers=2, timeout=300):
        if workers < 1 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Workers and timeout must be positive")
        self.workers = min(workers, os.cpu_count() or 1)
        self.timeout = timeout
        self.condition = threading.Condition()
        self.active = {}
        self.context = mp.get_context("spawn")

    def configure(self, workers):
        if type(workers) is not int or not 1 <= workers <= (os.cpu_count() or 1):
            raise ValueError(f"Workers must be between 1 and {os.cpu_count() or 1}")
        with self.condition:
            self.workers = workers
            self.condition.notify_all()

    def progress(self):
        with self.condition:
            return [{**item, "elapsed": time.monotonic() - item["started"]}
                    for item in self.active.values()]

    def run(self, operation, data, cancel=None):
        cancel = cancel or threading.Event()
        ticket = object()
        with self.condition:
            while len(self.active) >= self.workers:
                if cancel.is_set():
                    raise WorkerError("Cancelled")
                self.condition.wait(.05)
            self.active[ticket] = {"path": data.get("path", ""), "operation": operation,
                                   "started": time.monotonic()}
        parent, child = self.context.Pipe(duplex=False)
        process = self.context.Process(target=_entry, args=(child, operation, data), daemon=True)
        try:
            if cancel.is_set():
                raise WorkerError("Cancelled")
            process.start()
            child.close()
            deadline = time.monotonic() + self.timeout
            while True:
                if cancel.is_set():
                    raise WorkerError("Cancelled")
                if time.monotonic() >= deadline:
                    raise WorkerError(f"Worker timed out after {self.timeout:g}s")
                if parent.poll(.05):
                    try:
                        ok, result = parent.recv()
                    except EOFError as exc:
                        raise WorkerError(f"Worker crashed (exit {process.exitcode})") from exc
                    if not ok:
                        raise WorkerError(result)
                    return result
                if not process.is_alive():
                    raise WorkerError(f"Worker crashed (exit {process.exitcode})")
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join()
            parent.close()
            child.close()
            with self.condition:
                self.active.pop(ticket, None)
                self.condition.notify_all()
