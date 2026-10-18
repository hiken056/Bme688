# Classifier on the device

The Pi stores the models and makes the predictions. The interface only uploads models and shows the results.
Predictions continue when no phone is connected.

| What | Where on the Pi |
|---|---|
| Models | `/var/lib/bme688/models/` |
| Selected model | `/var/lib/bme688/active_model.json` |
| Predictions | `predictions.csv` next to the sensor files of each recording |

The code is in `backend/classifier.py`. It uses plain Python only, so nothing extra has to be installed on the Pi.

## Model file

Export it with the notebook (`clasificator/smell_classifier.ipynb`, section 7). It has three parts:

- `metadata`: name, version, classes, timings and `profile_requirements` (below);
- `model`: the feature names, the scaling values, the weights (`coef`, `intercept`) and `unknown_below`;
- `clean_air_reference`: the clean air of each training board, which the Pi uses as the reference.

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

## Clean-air reference (test version)

Every prediction compares the smell with clean air:
- for the first 10 minutes of a measurement, it uses the clean air the model was trained with. The Pi reads its board
  from the device name (`bme688-01` is bme1, and so on). If the model has no clean air for that board, it uses the
  average of the training boards, with a note;
- from minute 10, it uses minutes 5-10 of the same measurement as clean air (the first 5 minutes are still settling).
  This is saved as `reference.json` next to the recording, and `predictions.csv` shows `measurement` in the Reference column.

So start every measurement in clean air, wait 10 minutes, then bring the sample without stopping.
A new measurement starts with the training clean air again.

## Checks before each prediction

| Check | If it fails |
|---|---|
| Heater profiles match the model | No predictions; "Use model profiles" |
| Sensor data arriving | "No sensor data" |
| Training clean air for these profiles | "No clean-air reference" |
| Enough heater cycles in the window (at least half of the expected number) and at most 25% missing values | "Not enough data" |
| Pattern similar to the training data (fewer than 3 values more than 5 standard deviations away; no training window had any beyond 4) | "Unusual pattern", no class |
| Top probability at least `unknown_below` (60%) | "Unknown" with the closest class |

## Tests

```bash
python -m unittest tests.test_classifier_device        # model checks, prediction states
BME_ROOT=masuratori python -m unittest tests.test_classifier_parity   # same numbers as the notebook (needs .venv-ml and the data)
node tests/test_classifier_profiles.mjs                 # profile matching in the interface
```

The parity test runs the notebook's own functions and the Pi code on recorded sessions and requires every feature to
agree to 9 decimals and every probability to match scikit-learn.
