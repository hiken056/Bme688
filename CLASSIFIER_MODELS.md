# Classifier on the device

The Pi stores the models and makes the predictions. The interface only uploads models, shows the results
and starts the clean-air recording. Predictions continue when no phone is connected.

| What | Where on the Pi |
|---|---|
| Models | `/var/lib/bme688/models/` |
| Selected model | `/var/lib/bme688/active_model.json` |
| Clean air recorded on this board | `/var/lib/bme688/calibration.json` |
| Which board this is (`bme1` … `bme4`) | `/var/lib/bme688/device.json` |
| Predictions | `predictions.csv` next to the sensor files of each recording |

The code is in `backend/classifier.py`. It uses plain Python only, so nothing extra has to be installed on the Pi.

## Model file

Export it with the notebook (`clasificator/smell_classifier.ipynb`, section 7). It has three parts:

- `metadata`: name, version, classes, timings and `profile_requirements` (below);
- `model`: the feature names, the scaling values, the weights (`coef`, `intercept`) and `unknown_below`;
- `clean_air_reference` (optional): the clean air of each training board, used when no clean air was recorded
  on the board.

Only multinomial logistic regression on curve-shape features (`<profile>_shape_s<step>`) is supported.
The upload is refused, with the reason, when the file is not valid JSON, a section is missing, the numbers don't match
the feature list, the classes differ between `metadata` and `model`, or a feature can't be computed on the device.
The rows of `coef` follow `model.classes` (scikit-learn sorts them alphabetically). `metadata.classes` sets only the
order shown in the interface.

Models can't be added, selected or removed while a measurement is running.

## Profile requirements

A model declares the exact heater profiles and how many sensors run each one:

```json
"profile_requirements": [
  {
    "profile": "hp_354_standard",
    "count": 2,
    "mode": "parallel",
    "duty": 0,
    "sleep": 0,
    "temps": [320, 100, 100, 100, 200, 200, 200, 320, 320, 320],
    "ticks": [5, 2, 10, 30, 5, 5, 5, 5, 5, 5]
  }
]
```

Use the settings from training, not ones copied from a later configuration. Ticks are in the acquisition
program's 140 ms units, and step order matters.

The check compares temperatures, ticks, mode, duty and sleep. Profile names are labels only. Sensor positions are
free: the sensors are matched by settings, in ascending sensor order, by both the interface and the Pi. On a mismatch,
the panel offers **Use model profiles**, which shows the changes and asks for confirmation before saving the configuration.

## Clean-air reference (lenient for now)

Every prediction compares the smell with clean air from the same board. The Pi uses, in this order:

1. clean air **recorded on this board** (Record clean air: 5 minutes, during a measurement);
2. otherwise, the clean air **stored in the model** for this board, if **This board** is set on the Classifier
   page (shown with a warning, because it is older);
3. otherwise, no predictions: the panel asks you to record clean air.

A recording is saved even when something looks wrong, but with a note:
- it was made during warm-up;
- the readings changed between the two halves of the recording (more than 0.15 in ln R; clean air after warm-up
  stays below 0.1);
- a sensor sent too little data.

A recording is used only for sensors whose profile is unchanged since it was made.

## Checks before each prediction

| Check | If it fails |
|---|---|
| Heater profiles match the model | No predictions; "Use model profiles" |
| Sensor data arriving | "No sensor data" |
| Sensors warmed up (`warmup_seconds`, 15 min for v0.2) | Countdown; "Predict now, without waiting" skips it with a warning |
| Clean-air reference available | "Record clean air first" |
| Enough heater cycles in the window (at least half of the expected number) and at most 25% missing values | "Not enough data" |
| Pattern similar to the training data (fewer than 3 values more than 5 standard deviations away; no training window had any beyond 4) | "Unusual pattern", no class |
| Top probability at least `unknown_below` (60%) | "Unknown" with the closest class |

## Tests

```bash
python -m unittest tests.test_classifier_device        # model checks, clean air, prediction states
BME_ROOT=masuratori python -m unittest tests.test_classifier_parity   # same numbers as the notebook (needs .venv-ml and the data)
node tests/test_classifier_profiles.mjs                 # profile matching in the interface
```

The parity test runs the notebook's own functions and the Pi code on recorded sessions and requires every feature to
agree to 9 decimals and every probability to match scikit-learn.
