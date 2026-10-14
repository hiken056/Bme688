# the device must match the notebook; needs the data and .venv-ml
# BME_ROOT=masuratori python -m unittest tests.test_classifier_parity
import ast
import json
import os
from pathlib import Path
import unittest

PROJECT = Path(__file__).resolve().parent.parent
NOTEBOOK = Path(os.environ.get("BME_NOTEBOOK", PROJECT / "clasificator" / "smell_classifier.py"))
MODEL = Path(os.environ.get("BME_MODEL", PROJECT / "clasificator" / "smell_classifier_v0.2.json"))
ROOT = Path(os.environ.get("BME_ROOT", PROJECT / "masuratori"))
SESSIONS = [("bme1", "Chili"), ("bme1", "Adidas_transpirat"), ("bme2", "Curry"),
            ("bme4", "Busuioc_gradina"), ("bme4", "Ulei_industrial"), ("bme2", "Sosete_mirositoare"), ("bme1", "Ceai_de_menta")]

try:
    import numpy as np
    import pandas as pd
except ImportError:  # the Pi does not need these
    np = pd = None

from backend import classifier


def notebook_functions():
    # notebook functions, without running the training
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    wanted_names = {"CLIP_OHM", "WINDOW_S", "PROFILES", "USE_STEPS"}
    wanted_functions = {"read_cycles", "features"}
    body = [node for node in tree.body
            if (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in wanted_names for t in node.targets))
            or (isinstance(node, ast.FunctionDef) and node.name in wanted_functions)]
    namespace = {"np": np, "pd": pd}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
    return namespace


@unittest.skipIf(np is None, "needs numpy and pandas")
@unittest.skipUnless(NOTEBOOK.exists() and MODEL.exists() and ROOT.exists(), "needs the notebook, the model and the data")
class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nb = notebook_functions()
        cls.keep_ms = classifier.KEEP_MS
        classifier.KEEP_MS = 10 ** 12  # compare whole files
        payload = json.loads(MODEL.read_text(encoding="utf-8"))
        cls.model = classifier.validate_model(payload)
        cls.reference = payload["clean_air_reference"]

    @classmethod
    def tearDownClass(cls):
        classifier.KEEP_MS = cls.keep_ms

    def compare_session(self, board, label):
        runs = sorted(p for p in (ROOT / board / label).iterdir() if p.is_dir()) if (ROOT / board / label).exists() else []
        if not runs:
            self.skipTest(f"{board}/{label} not found")
        model, nb = self.model, self.nb
        stored = self.reference[board]["ln_r0"]
        reference = {(int(key[6]), key.split("_", 1)[1]): [None if v is None else float(v) for v in values]
                     for key, values in stored.items()}

        # notebook windows start at the first cycle
        data, cycles, mapping = {}, {}, {}
        for run in runs:
            for csv_path in sorted(run.glob("sensor*.csv")):
                sensor, profile = int(csv_path.name[6]), csv_path.stem.split("_", 1)[1]
                if profile not in nb["PROFILES"]:
                    continue
                data.setdefault((sensor, profile), []).append(nb["read_cycles"](csv_path))
                reader = classifier.CycleReader(csv_path, model["clip_ohm"])
                reader.update()
                cycles.setdefault(sensor, []).extend(reader.complete_cycles())
                mapping.setdefault(profile, set()).add(sensor)
        mapping = {p: sorted(s) for p, s in mapping.items()}
        for sensor_cycles in cycles.values():
            sensor_cycles.sort(key=lambda c: c[0])
        t_start = min(min(c["t_ms"].min() for c in parts) for parts in data.values())
        frames = {key: pd.concat(parts, ignore_index=True).sort_values("t_ms", ignore_index=True)
                  for key, parts in data.items()}
        frames = {key: c.assign(rel_s=(c["t_ms"] - t_start) / 1000) for key, c in frames.items()}
        ref_np = {key: np.array([np.nan if v is None else v for v in reference[key]]) for key in frames}
        expected = nb["features"]({"data": frames, "ref": ref_np})
        self.assertGreater(len(expected), 3)

        # same windows on the device
        refs = {sensor: reference[(sensor, profile)] for profile, sensors in mapping.items() for sensor in sensors}
        window_ms = model["window_seconds"] * 1000
        self.assertEqual(model["window_seconds"], nb["WINDOW_S"])
        checked = 0
        for window, row in expected.iterrows():
            t_end = t_start + (window + 1) * window_ms - 1
            values, _ = classifier.window_features(model, mapping, cycles, refs, t_end)
            for name in model["features"]:
                want, got = row[name], values[name]
                if pd.isna(want):
                    self.assertIsNone(got, name)
                else:
                    self.assertIsNotNone(got, f"{board}/{label} window {window} {name} expected {want}")
                    self.assertAlmostEqual(got, want, places=9, msg=f"{board}/{label} window {window} {name}")
            checked += 1
        return expected, checked

    def test_features_match_the_notebook(self):
        total = 0
        for board, label in SESSIONS:
            with self.subTest(session=f"{board}/{label}"):
                _, checked = self.compare_session(board, label)
                total += checked
        self.assertGreater(total, 0)

    def test_probabilities_match_scikit_learn(self):
        try:
            from sklearn.impute import SimpleImputer
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            self.skipTest("needs scikit-learn")
        model = self.model
        expected, _ = self.compare_session(*SESSIONS[0])
        x = expected[model["features"]].to_numpy(float)
        x[0, :3] = np.nan  # include missing values
        pipeline = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression())
        pipeline.fit(np.vstack([x, x]), [0, 1, 2] * (2 * len(x) // 3) + [0] * (2 * len(x) % 3))
        imputer, scaler, logreg = pipeline.steps[0][1], pipeline.steps[1][1], pipeline.steps[2][1]
        imputer.statistics_ = np.array(model["impute"])
        scaler.mean_, scaler.scale_ = np.array(model["mean"]), np.array(model["scale"])
        logreg.coef_, logreg.intercept_ = np.array(model["coef"]), np.array(model["intercept"])
        want = pipeline.predict_proba(x)
        for row, probabilities in zip(x, want):
            values = {name: (None if np.isnan(v) else float(v)) for name, v in zip(model["features"], row)}
            got = classifier.classify(model, values)["probabilities"]
            for a, b in zip(got, probabilities):
                self.assertAlmostEqual(a, b, places=9)


if __name__ == "__main__":
    unittest.main()
